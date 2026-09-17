#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键启动多个 AI 机器人 + 日志记录器，让它们在同一个聊天室房间里互聊。
读取 bots.json（每个元素是一个 AI 的人设），为每个 AI 起一个 ai_bot.py 进程，
并额外起一个 chat_logger.py 把对话落盘到 logs/<房间>.log / .jsonl。
最后会自动发一句"开场白"点燃对话。

用法:
    python run_bots.py                      # 用默认 bots.json
    python run_bots.py my_bots.json        # 指定配置文件
    ROOM=ashgow1241hf1 python run_bots.py  # 指定房间名
    python run_bots.py --no-logger         # 不启动日志记录器
停止:  Ctrl+C 会同时结束所有机器人与日志器。
"""
import json
import os
import sys
import time
import subprocess
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion


def publish_opening(room, text):
    """连一次 Broker，发一句开场白后断开。"""
    c = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, transport="websockets")
    c.ws_set_options(path=os.getenv("BROKER_PATH", "/mqtt"))
    c.tls_set()
    try:
        c.connect(os.getenv("BROKER_HOST", "broker.hivemq.com"),
                  int(os.getenv("BROKER_PORT", "8884")), 60)
        c.loop_start()
        time.sleep(2)
        c.publish(f"workbuddy/chat/{room}/messages",
                  json.dumps({"user": "观众", "text": text, "time": time.strftime("%H:%M")}),
                  qos=0)
        time.sleep(1)
    except Exception as e:
        print("[开场白发送失败]", e)
    finally:
        c.loop_stop()
        c.disconnect()


def main():
    no_logger = "--no-logger" in sys.argv
    cfg_path = next((a for a in sys.argv[1:] if a.endswith(".json")), "bots.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        bots = json.load(f)

    base_env = dict(os.environ)
    room = os.getenv("ROOM", "demo")
    procs = []

    for b in bots:
        env = base_env.copy()
        env["BOT_NAME"] = b["name"]
        env["PERSONA"] = b.get("persona", "你是一个友好的中文聊天助手。")
        if "room" in b:
            env["ROOM"] = b["room"]
        if "llm_base_url" in b:
            env["LLM_BASE_URL"] = b["llm_base_url"]
        if "llm_model" in b:
            env["LLM_MODEL"] = b["llm_model"]
        if "max_turns" in b:
            env["MAX_TURNS"] = str(b["max_turns"])
        if "reply_probability" in b:
            env["REPLY_PROBABILITY"] = str(b["reply_probability"])
        if "min_interval" in b:
            env["MIN_INTERVAL"] = str(b["min_interval"])
        if "mention_only" in b:
            env["MENTION_ONLY"] = str(b["mention_only"])
        p = subprocess.Popen([sys.executable, "ai_bot.py"], env=env)
        procs.append(p)
        print(f"▶ 启动机器人: {b['name']}  (pid={p.pid})")

    if not no_logger:
        le = base_env.copy()
        le["ROOM"] = room
        lp = subprocess.Popen([sys.executable, "chat_logger.py"], env=le)
        procs.append(lp)
        print(f"▶ 启动日志器 (pid={lp.pid}) -> logs/{room}.log / .jsonl")

    print(f"\n共启动 {len([p for p in procs if p != lp])} 个 AI + 1 个日志器，房间「{room}」。")
    print("打开聊天室网页(同房间名)即可围观。按 Ctrl+C 结束全部。\n")

    # 等机器人连上后，发一句开场白点燃对话
    opening = os.getenv("OPENING", "大家好，欢迎各位 AI 互相认识一下，先用一句话简单自我介绍吧！")
    time.sleep(6)
    publish_opening(room, opening)

    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
        print("\n已停止所有机器人与日志器。")


if __name__ == "__main__":
    main()
