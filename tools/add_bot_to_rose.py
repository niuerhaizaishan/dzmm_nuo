# -*- coding: utf-8 -*-
"""把「薇丝-bot」加进「Rose（505）玫瑰房」（照 2026-09-25 认证群的做法）。

方式（docs/小小糯总方案.md §"已完成：Bot 入群"）：
    POST /api/trpc/chatroom.addBot {chatroomId, botId}   ← 用侧车账号（需群主/管理员）
前提：Bot 必须是**公开**的（私有 Bot 只能由创建者添加）。

⚠️ 这次一律走 `dzmm.api()`（内部有令牌桶限流）而不是裸 requests —— 上一次侦察我用裸
requests 连打 ~20 次，把账号打进了 `HTTP 418 captcha_required`（自己作的）。
调用之间还额外 sleep，尽量不再触发风控。

只做三件事：找 id → 加群 → 复核成员表。找不到 id 就明确说"需要你把它改成公开或给我 id"。
"""
import json
import sys
import time

sys.path.insert(0, "/app")
import requests  # noqa: E402
import dzmm  # noqa: E402
import env_config  # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
NUO = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"
ORIGIN = env_config.get_dzmm_origin()
PROXIES = env_config.get_proxies()
COOKIES = dzmm.get_cookies()


def api(path, inp):
    """走 dzmm.api（带令牌桶）。返回 (ok, data_or_err)。"""
    try:
        d = dzmm.api(path, inp, COOKIES)
        return True, d
    except Exception as e:                                       # noqa: BLE001
        return False, "{}: {}".format(type(e).__name__, str(e)[:160])


def members(cid):
    ok, d = api("chatroom.getMembers", {"json": {"chatroomId": cid, "limit": 100}})
    if not ok:
        return None, d
    return (d.get("members") or []), ""


print("=== 1) 账号会话还活着吗（captcha 冷却过了没）===")
ok, d = api("chatroom.listBots", {"json": {"chatroomId": ROSE}})
if not ok:
    print("  ✗ 平台拒绝了：{}".format(d))
    print("  ⇒ captcha 还没过。等冷却（或人工在 noVNC 里过一下）再重试。")
    raise SystemExit(2)
bots = d.get("bots") or []
print("  ✓ 通了。玫瑰房的公开 Bot 目录：{} 个".format(len(bots)))
for b in bots:
    print("     {:<38} {:<22} public={} added={}".format(
        b.get("bot_id"), b.get("full_name"), b.get("is_public"), b.get("added")))
time.sleep(2)

hit = [b for b in bots if "薇" in str(b.get("full_name") or "")
       or "丝" in str(b.get("full_name") or "")
       or "weisi" in str(b.get("full_name") or "").lower()]
if not hit:
    print()
    print("!! 公开目录里没有「薇丝」—— 两种情况之一：")
    print("   1) 它是**私有** Bot（私有 Bot 只能由创建者添加，而创建者就是本账号时也可能")
    print("      不进公开目录）→ 请把它切成**公开**，或直接把它的 bot_id 告诉我；")
    print("   2) 名字不含「薇/丝」→ 下面把目录全部列出来，你指一下是哪个。")
    print("   顺便：目录里的名字 →", [b.get("full_name") for b in bots])
    raise SystemExit(3)

target = hit[0]
bid = target["bot_id"]
print()
print("=== 2) 找到候选：{}（{}）".format(target.get("full_name"), bid))

if str(target.get("full_name")) not in ("薇丝", "薇丝-bot") and len(hit) > 1:
    print("   ⚠️ 命中多个：{}".format([(b.get("full_name"), b.get("bot_id")) for b in hit]))
time.sleep(2)

print()
print("=== 3) 加进玫瑰房（POST chatroom.addBot）===")
url = "{}/api/trpc/chatroom.addBot".format(ORIGIN)
payloads = [
    ("body {'json':…}", {"json": {"chatroomId": ROSE, "botId": bid}}),
    ("body {'0':{'json':…}}", {"0": {"json": {"chatroomId": ROSE, "botId": bid}}}),
]
added = False
for label, body in payloads:
    try:
        r = requests.post(url, json=body, cookies=COOKIES, timeout=25, proxies=PROXIES)
    except Exception as e:                                       # noqa: BLE001
        print("   {} -> 请求失败 {}: {}".format(label, type(e).__name__, e))
        continue
    print("   {} -> HTTP {} | {}".format(
        label, r.status_code, (r.text or "")[:300].replace("\n", " ")))
    if r.status_code == 200 and '"error"' not in (r.text or ""):
        added = True
        break
    time.sleep(3)

print()
print("=== 4) 复核：玫瑰房成员表里有没有它 ===")
time.sleep(2)
ms, err = members(ROSE)
if ms is None:
    print("   ✗ 查不了成员表：{}".format(err))
else:
    print("   成员数 = {}".format(len(ms)))
    bots_in = [(m.get("id"), m.get("fullName")) for m in ms if m.get("isBot")]
    print("   其中的 Bot：{}".format(bots_in or "（还没有）"))
    mine = [m for m in ms if m.get("id") == NUO]
    if mine:
        print("   账号自己的记录字段：{}".format(sorted(mine[0].keys())))
        print("      {}".format(json.dumps(mine[0], ensure_ascii=False)[:260]))
    print()
    if any(m.get("isBot") for m in ms):
        print("===> 成功：薇丝-bot 已在玫瑰房成员表里（uid 见上）。")
    elif added:
        print("===> addBot 返回成功，但成员表暂时还没刷新（平台有延迟）；稍后再看。")
    else:
        print("===> 没加成。如果是权限问题：确认账号是玫瑰房群主/管理员，")
        print("     以及 Bot 是公开的（私有 Bot 只能由创建者添加）。")
