"""从平台读回 Bot 的真实显示名（避免手抄不一致）。

试几条路，谁通用谁：
  1. Telegram 兼容的 getMe            /api/bot/bot<token>/getMe
  2. 官方 /api/bot/me 或 /api/bot/info
  3. tRPC chatroom.listMyBots（只读）

全部只读，不发消息。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path("/app")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

import requests  # noqa: E402

import env_config  # noqa: E402

TOKEN = (os.environ.get("DZMM_BOT_TOKEN") or "").strip()
BOT_ID = (os.environ.get("DZMM_BOT_ID") or "").strip()
ORIGIN = env_config.get_dzmm_origin()
PROXY = os.environ.get("DZMM_PROXY") or None
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

print("origin =", ORIGIN)
print("bot_id =", BOT_ID or "(未设)")
print("token  =", (TOKEN[:8] + "…") if TOKEN else "(未设)")
print()

HEAD = {"X-Bot-Token": TOKEN, "Content-Type": "application/json"}


def show(label, r):
    body = (r.text or "")[:400].replace("\n", " ")
    print("--- {} ---".format(label))
    print("   HTTP", r.status_code, "|", body)
    return r


def try_get(label, url, headers=None):
    try:
        r = requests.get(url, headers=headers or HEAD, proxies=PROXIES, timeout=15)
        show(label, r)
        return r
    except Exception as e:                                      # noqa: BLE001
        print("--- {} ---\n   失败: {}: {}".format(label, type(e).__name__, e))
        return None


def try_post(label, url, payload, headers=None):
    try:
        r = requests.post(url, headers=headers or HEAD, json=payload,
                          proxies=PROXIES, timeout=15)
        show(label, r)
        return r
    except Exception as e:                                      # noqa: BLE001
        print("--- {} ---\n   失败: {}: {}".format(label, type(e).__name__, e))
        return None


# 1) Telegram 兼容 getMe
if TOKEN:
    try_get("GET /api/bot/bot<token>/getMe",
            "{}/api/bot/bot{}/getMe".format(ORIGIN, TOKEN))
    # 有的实现把 token 放在头里
    try_get("GET /api/bot/getMe", "{}/api/bot/getMe".format(ORIGIN))

# 2) 官方 me / info
if TOKEN:
    try_get("GET /api/bot/me", "{}/api/bot/me".format(ORIGIN))
    try_get("GET /api/bot/info", "{}/api/bot/info".format(ORIGIN))

# 3) tRPC 列出 Bot（只读）
CID = (os.environ.get("DZMM_BOT_CID") or "").strip()
if CID:
    try_get("GET trpc chatroom.listBots (只读)",
            "{}/api/trpc/chatroom.listBots?input={}".format(
                ORIGIN, requests.utils.quote(json.dumps({"chatroomId": CID}))))
    try_get("GET trpc chatroom.getBots (只读)",
            "{}/api/trpc/chatroom.getBots?input={}".format(
                ORIGIN, requests.utils.quote(json.dumps({"chatroomId": CID}))))
