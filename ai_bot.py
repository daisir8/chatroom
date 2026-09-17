#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 聊天室机器人 —— 把本地大模型接入 MQTT 聊天室。
开多个进程、每个用不同的人设(BOT_NAME/PERSONA) = 多个 AI 在同一个房间里互聊。

策略(均可用环境变量覆盖):
  REPLY_PROBABILITY  普通消息被回复的概率(默认0.75)——控制"发言节奏"
  MIN_INTERVAL       本机器人两次发言的最小间隔(秒,默认6)——避免机器枪式刷屏
  MENTION_ONLY       设为 1/true 时，只回复 @提到自己 的消息
  IDLE_TRIGGER       冷场多少秒后可能自发接话(默认18)
  IDLE_PROB          冷场时自发接话的概率(默认0.6)

安装依赖: pip install paho-mqtt requests
单开:       python ai_bot.py
一键多开:   python run_bots.py   (读取 bots.json)
退出:       Ctrl+C
"""
import os
import json
import time
import random
import threading
import requests
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

# ============ 配置（环境变量可覆盖，便于一份脚本多开） ============
BROKER_HOST = os.getenv("BROKER_HOST", "broker.hivemq.com")
BROKER_PORT = int(os.getenv("BROKER_PORT", "8884"))
BROKER_PATH = os.getenv("BROKER_PATH", "/mqtt")
ROOM = os.getenv("ROOM", "demo")                             # ★ 必须和聊天室网页里的房间名一致

BOT_NAME = os.getenv("BOT_NAME", "AI_助手")                  # ★ 每个进程要不同
PERSONA = os.getenv("PERSONA",                               # ★ 人设
    "你是一个友好的中文聊天伙伴，正在群里和多位朋友闲聊，用简洁中文回复，不超过3句话。")

# 本地大模型（OpenAI 兼容；你的 MiniCPM Lab 实测可用）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "minicpm5:2b")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")
# 备选：Ollama 直连 -> LLM_BASE_URL=http://localhost:11434/v1

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "12"))
REPLY_DELAY = tuple(float(x) for x in os.getenv("REPLY_DELAY", "1.0,3.0").split(","))
MAX_TURNS = int(os.getenv("MAX_TURNS", "0"))                 # 0=无限；设数字可限制总回复数

# ---- 发言策略 ----
REPLY_PROBABILITY = float(os.getenv("REPLY_PROBABILITY", "0.75"))  # 普通消息被回复的概率(节奏)
MIN_INTERVAL = float(os.getenv("MIN_INTERVAL", "6.0"))             # 本机器人两次发言最小间隔(秒)
MENTION_ONLY = os.getenv("MENTION_ONLY", "false").lower() in ("1", "true", "yes")  # 仅回应@提到自己
IDLE_TRIGGER = float(os.getenv("IDLE_TRIGGER", "18.0"))            # 冷场多少秒后可能自发发言
IDLE_PROB = float(os.getenv("IDLE_PROB", "0.6"))                   # 冷场时自发发言概率
# ====================================================================

def msg_topic():
    return f"workbuddy/chat/{ROOM}/messages"

def pres_topic():
    return f"workbuddy/chat/{ROOM}/presence"

history = []             # 滚动上下文
last_reply_ts = 0.0      # 本机器人上次发言时间
last_msg_ts = time.time()  # 房间上次有消息的时间(用于冷场检测)
turns = 0


def mentioned(text):
    return ("@" + BOT_NAME) in text


def call_llm(user_text):
    sys_msg = {"role": "system", "content": PERSONA}
    messages = [sys_msg] + history[-MAX_HISTORY:] + [{"role": "user", "content": user_text}]
    try:
        r = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "messages": messages, "temperature": 0.8, "stream": False},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("[LLM 错误]", e)
        return None


def publish_reply(client, text):
    global last_reply_ts, turns
    history.append({"role": "assistant", "content": text})
    if len(history) > MAX_HISTORY * 2:
        history[:] = history[-MAX_HISTORY * 2:]
    payload = json.dumps(
        {"user": BOT_NAME, "text": text, "time": time.strftime("%H:%M")},
        ensure_ascii=False,
    )
    client.publish(msg_topic(), payload, qos=0)
    last_reply_ts = time.time()
    turns += 1
    print(f"[{BOT_NAME}] -> {text}\n")


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[{BOT_NAME}] 已连接 Broker，加入房间「{ROOM}」")
    client.subscribe([(msg_topic(), 0), (pres_topic(), 0)])
    client.publish(pres_topic(), json.dumps({"type": "join", "user": BOT_NAME}), qos=0)


def on_message(client, userdata, msg):
    global last_msg_ts
    if msg.topic == pres_topic():
        return
    try:
        data = json.loads(msg.payload.decode())
    except Exception:
        return
    user = data.get("user")
    text = data.get("text", "")
    if not text or user == BOT_NAME:
        return  # 不回复自己

    last_msg_ts = time.time()
    history.append({"role": "user", "content": f"{user}说：{text}"})
    if len(history) > MAX_HISTORY * 2:
        history[:] = history[-MAX_HISTORY * 2:]

    if MAX_TURNS and turns >= MAX_TURNS:
        return

    is_men = mentioned(text)
    now = time.time()
    since = now - last_reply_ts

    if MENTION_ONLY:
        # 仅回应 @提到自己
        if not is_men:
            return
        if since < MIN_INTERVAL * 0.5:
            return
    else:
        # 自由聊天：@提到自己 -> 必回；普通消息 -> 按概率+最小间隔控制节奏
        if not is_men:
            if since < MIN_INTERVAL:
                return
            if random.random() > REPLY_PROBABILITY:
                return

    time.sleep(random.uniform(*REPLY_DELAY))
    reply = call_llm(text)
    if reply:
        publish_reply(client, reply)


def idle_loop(client):
    """冷场检测：长时间无人说话时，以一定概率自发接话，保持多 AI 对话不断。"""
    if MENTION_ONLY:
        return
    while True:
        time.sleep(5)
        now = time.time()
        if now - last_msg_ts > IDLE_TRIGGER and now - last_reply_ts > MIN_INTERVAL:
            if random.random() < IDLE_PROB:
                prompt = "（群聊有点冷场）主动说一句有趣或能引发讨论的话，继续聊天，不超过2句中文。"
                reply = call_llm(prompt)
                if reply:
                    publish_reply(client, reply)


def heartbeat(client):
    while True:
        try:
            client.publish(pres_topic(), json.dumps({"type": "heartbeat", "user": BOT_NAME}), qos=0)
        except Exception:
            pass
        time.sleep(4)


def main():
    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, transport="websockets")
    client.ws_set_options(path=BROKER_PATH)
    client.tls_set()  # WSS 加密
    client.will_set(pres_topic(), json.dumps({"type": "leave", "user": BOT_NAME}), qos=0)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    print(f"[{BOT_NAME}] 启动中… (Ctrl+C 退出)")
    threading.Thread(target=heartbeat, args=(client,), daemon=True).start()
    threading.Thread(target=idle_loop, args=(client,), daemon=True).start()
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print(f"[{BOT_NAME}] 已退出")


if __name__ == "__main__":
    main()
