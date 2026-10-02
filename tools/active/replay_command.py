# -*- coding: utf-8 -*-
"""补发/重放一条"群友消息"给正在跑的机器人（P12-J）。

用途：某条命令因为 bug 被吞掉时（例如 2026-09-27 紫苑的 `/运势` 和一条
`/写诗` 同批到达，第二条被静默丢弃），把**它重新喂进机器人的队列**，
让机器人按正常路径生成并发出回复。

⚠️ 群里只会多出**机器人的那条回复** —— 平台侧不会出现假的用户消息
（我们只是往本机 Webhook 接收端 POST 一条 payload，没有向平台发任何东西）。

用法（在服务器上、接收端所在容器之外）：

    cd /opt/dzmm && docker run --rm --entrypoint python --network host \\
      -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
      -v /opt/dzmm:/app:ro -v /tmp/replay_command.py:/r.py:ro -w /tmp \\
      --env-file .env dzmm-bot:latest /r.py \\
      --cid bd7fe6a4-0065-4551-ad3d-94116dba8f9c \\
      --uid 79c910d0-e0e8-40c8-9d84-ad4c6a45bd49 \\
      --name 紫苑 --text /运势

参数：
    --cid    群 id（必填）
    --uid    以谁的身份发（必填；用群成员的 uid，机器人会按他处理）
    --name   显示名（仅用于日志/占位，默认"补发"）
    --text   要重放的文本（必填，通常是 /命令）
    --dry-run  只打印 payload 与将要 POST 的地址，不真发
    --port   接收端端口（默认取 .env 的 DZMM_WEBHOOK_PORT，否则 8091）
"""
import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, "/app")
import requests                                     # noqa: E402

ENV_FILE = "/app/.env"


def _env(key: str, default: str = "") -> str:
    try:
        for line in open(ENV_FILE, encoding="utf-8"):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except Exception:                                          # noqa: BLE001
        pass
    return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", required=True)
    ap.add_argument("--uid", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--name", default="补发")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    port = a.port or int(_env("DZMM_WEBHOOK_PORT", "0") or 8091)
    secret = _env("DZMM_BOT_SECRET") or _env("DZMM_BOT_SECRET_ROSE")
    if not secret:
        print("❌ .env 里没有 DZMM_BOT_SECRET / DZMM_BOT_SECRET_ROSE，接收端不会认")
        return 2
    payload = {"update_id": int(time.time()),
               "message": {"message_id": "replay-" + uuid.uuid4().hex,
                           "chat": {"id": a.cid},
                           "from": {"id": a.uid, "first_name": a.name,
                                    "username": a.name, "is_bot": False},
                           "date": int(time.time()),
                           "text": a.text}}
    url = "http://127.0.0.1:{}/bot/webhook".format(port)
    print("POST", url)
    print("payload:", json.dumps(payload, ensure_ascii=False))
    if a.dry_run:
        print("（--dry-run：没有发送）")
        return 0
    try:
        r = requests.post(url,
                          headers={"X-Telegram-Bot-Api-Secret-Token": secret,
                                   "Content-Type": "application/json"},
                          data=json.dumps(payload, ensure_ascii=False), timeout=10)
        print("HTTP {}  {}".format(r.status_code, r.text[:120]))
        if r.status_code == 401:
            print("❌ 401：Secret 不在白名单里（顺带确认端口通、反代链路对）")
        return 0 if r.status_code == 200 else 1
    except Exception as e:                                     # noqa: BLE001
        print("❌ POST 失败：{}".format(str(e)[:100]))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
