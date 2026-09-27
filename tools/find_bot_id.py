"""找「若瑾」的 botId：试几条只读 tRPC 查询（每个最多一次，失败即过）。"""
import json
import sys

sys.path.insert(0, "/app")

import requests                                    # noqa: E402

import env_config                                  # noqa: E402
from token_store import get_cookies                # noqa: E402

ORIGIN = env_config.get_dzmm_origin().rstrip("/")
CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"       # 上班如上坟，下班如还魂
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"      # 已知有薇丝，用来验证接口形状

ck = get_cookies()
jar = requests.cookies.RequestsCookieJar()
for k, v in (ck or {}).items():
    jar.set(k, v)

print("origin =", ORIGIN)


def q(label, path, payload=None):
    url = "{}/api/trpc/{}".format(ORIGIN, path)
    if payload is not None:
        url += "?input=" + requests.utils.quote(json.dumps(payload))
    try:
        r = requests.get(url, cookies=jar, timeout=15,
                         headers={"accept": "application/json"})
    except Exception as e:                                  # noqa: BLE001
        print("  {:34s} 失败 {}: {}".format(label, type(e).__name__, str(e)[:60]))
        return None
    body = (r.text or "").replace("\n", " ")[:300]
    print("  {:34s} HTTP {} | {}".format(label, r.status_code, body))
    return r


print("--- 1) chatroom.listBots（已知有 Bot 的群，验证形状）---")
q("listBots(rose)", "chatroom.listBots", {"chatroomId": ROSE})
q("listBots(新群)", "chatroom.listBots", {"chatroomId": CID})

print("--- 2) 公开 Bot 目录 / 我的 Bot 列表（猜名字）---")
for p in ("bot.listPublic", "bot.listMine", "bot.listAll", "bot.list",
          "bots.listMine", "chatroom.listPublicBots", "bot.getMine"):
    q(p, p, {} if p != "chatroom.listPublicBots" else {"chatroomId": CID})
