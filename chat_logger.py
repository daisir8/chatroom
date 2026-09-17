#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聊天室对话记录器 —— 订阅指定房间，把房间里所有人的消息(含各 AI 与真人)
实时写入日志文件：
    logs/<房间名>.log    人类可读：[时间] 昵称: 内容
    logs/<房间名>.jsonl  结构化：每行一条 JSON，方便程序分析

用法:
    python chat_logger.py                 # 读取环境变量 ROOM(默认 demo)
    ROOM=ashgow1241hf1 python chat_logger.py
退出:
    Ctrl+C
"""
import os
import json
import time
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

ROOM = os.getenv("ROOM", "demo")
BROKER_HOST = os.getenv("BROKER_HOST", "broker.hivemq.com")
BROKER_PORT = int(os.getenv("BROKER_PORT", "8884"))
BROKER_PATH = os.getenv("BROKER_PATH", "/mqtt")
LOG_DIR = os.getenv("LOG_DIR", "logs")

os.makedirs(LOG_DIR, exist_ok=True)
log_path = os.path.join(LOG_DIR, f"{ROOM}.log")
jsonl_path = os.path.join(LOG_DIR, f"{ROOM}.jsonl")
logf = open(log_path, "a", encoding="utf-8")
jsonlf = open(jsonl_path, "a", encoding="utf-8")
print(f"[logger] 房间「{ROOM}」日志 -> {log_path}")


def msg_topic():
    return f"workbuddy/chat/{ROOM}/messages"


def on_connect(c, u, f, rc, p=None):
    c.subscribe(msg_topic(), 0)


def on_message(c, u, msg):
    try:
        data = json.loads(msg.payload.decode())
    except Exception:
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    user = data.get("user", "?")
    text = data.get("text", "")
    line = f"[{ts}] {user}: {text}\n"
    logf.write(line)
    logf.flush()
    jsonlf.write(json.dumps({"ts": ts, "user": user, "text": text}, ensure_ascii=False) + "\n")
    jsonlf.flush()


client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, transport="websockets")
client.ws_set_options(path=BROKER_PATH)
client.tls_set()
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
try:
    client.loop_forever()
except KeyboardInterrupt:
    logf.close()
    jsonlf.close()
