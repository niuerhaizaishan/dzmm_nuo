"""列出账号能看到的所有会话，找「上班如上坟，下班如还魂」的 cid（只读）。"""
import sys

sys.path.insert(0, "/app")

from dzmm import api, get_cookies            # noqa: E402

cookies = get_cookies()
chats = api("chat.listAll", {"json": None, "meta": {"values": ["undefined"], "v": 1}},
            cookies)
if not isinstance(chats, list):
    print("拿到的不是列表：", type(chats), str(chats)[:200])
    raise SystemExit(1)

print("共 {} 个会话：".format(len(chats)))
hit = []
for c in chats:
    d = (c.get("data") or {}) if isinstance(c, dict) else {}
    cid = c.get("id") or ""
    title = d.get("title") or ""
    kind = d.get("chatType") or d.get("mode") or "?"
    print("  {:38s} {:22s} {:16s} {}".format(cid, kind, str(d.get("mode") or ""), title))
    if any(k in title for k in ("上坟", "还魂", "上班")):
        hit.append((cid, title, kind))

print()
if hit:
    for cid, title, kind in hit:
        print("==> 命中：{} | {} | {}".format(cid, title, kind))
else:
    print("==> 没找到名字里含「上坟/还魂/上班」的会话")
