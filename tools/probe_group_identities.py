"""P12-AE 辅助：上班群里"谁是谁" —— 若要改由账号发，群里会看到哪个名字。

屋主的第二个要求（回复挂在对方那条消息上）官方 Bot API 做不到
（`tools/probe_reply_capability.py` 出站部分：15 个候选字段全无校验）。
剩下唯一能做引用的通道是**账号**（Socket.IO / 浏览器），但那样消息的
发送者就从"若瑾-bot"变成侧车账号 —— 所以先把这个差别摆清楚：

    账号 uid/名字 vs 若瑾 Bot 的 uid/名字，在这个群里分别显示成什么。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import dzmm                                                    # noqa: E402
from runtime.identity import NUO_UID                           # noqa: E402

RUOJIN_CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
RUOJIN_BOT = "9938211c-b245-4cad-a594-cd9a1eb4966a"

members = dzmm.get_members(RUOJIN_CID) or {}
print("上班群成员 {} 人".format(len(members)))
for uid in (NUO_UID, RUOJIN_BOT):
    print("  {} -> {!r}".format(uid[:8], members.get(uid, "（不在成员表里）")))

print()
print("--- 侧车账号在这个群的资料（群里显示的署名）---")
d = dzmm.get_user(RUOJIN_CID, NUO_UID) or {}
for k in ("fullName", "nickname", "name", "avatar", "isBot", "gender", "signature"):
    if k in d:
        print("  {:<10s} {}".format(k, str(d.get(k))[:70]))

print()
print("--- 若瑾 Bot 在这个群的资料 ---")
d2 = dzmm.get_user(RUOJIN_CID, RUOJIN_BOT) or {}
for k in ("fullName", "nickname", "name", "avatar", "isBot", "gender", "signature"):
    if k in d2:
        print("  {:<10s} {}".format(k, str(d2.get(k))[:70]))

print()
print("--- 最近 40 条各发送者出现次数 ---")
msgs = dzmm.get_messages(RUOJIN_CID, limit=40) or []
cnt = {}
for m in msgs:
    u = m.get("sent_by", "")
    cnt[u] = cnt.get(u, 0) + 1
for u, n in sorted(cnt.items(), key=lambda kv: -kv[1]):
    label = members.get(u, "")
    tag = " <- 侧车账号" if u == NUO_UID else (" <- 若瑾Bot" if u == RUOJIN_BOT else "")
    print("  {:<10s} {:<3} {}{}".format(u[:8], n, label, tag))
print()
print("done")
