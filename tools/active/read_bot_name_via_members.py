"""用**侧车账号的登录态**从平台读回 Bot 的真实显示名。

Bot API 没有 getMe / 成员接口，但侧车账号（登录态持有者）能读群成员表，
而 Bot 就在群里 —— 所以 `get_members(cid)[BOT_UID]` 就是它的当前显示名。

只读，不发消息、不改任何东西。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")
os.chdir("/app")

from dzmm import get_members, get_messages_all, get_user  # noqa: E402

BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()
NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"
CID = (os.environ.get("DZMM_BOT_CID") or "").strip()

print("bot_id =", BOT_UID)
print("cid    =", CID)
print()

print("=== 1) 群成员表里找 Bot ===")
members = get_members(CID) or {}
print("   成员数:", len(members))
hit = members.get(BOT_UID)
print("   Bot 在成员表里的名字: {!r}".format(hit))
if hit:
    print("   repr（看有没有空格/不可见字符）:", repr(hit), "长度", len(hit))

print()
print("=== 2) 直接查这个 uid 的用户信息 ===")
u = get_user(CID, BOT_UID)
if u:
    for k in ("fullName", "nickName", "name", "gender", "quirk", "bio"):
        if k in u:
            print("   {:10s} = {!r}".format(k, u[k]))
else:
    print("   get_user 返回空")

print()
print("=== 3) 侧车自己（对比） ===")
print("   侧车 in members:", repr(members.get(NUO_UID)))
print("   侧车 get_user :", repr((get_user(CID, NUO_UID) or {}).get("fullName")))

print()
print("=== 4) 最近消息里 bot 自己发的那条，看 sent_by 与显示 ===")
msgs = get_messages_all(CID, max_count=60) or []
for m in msgs:
    if m.get("sent_by") == BOT_UID:
        print("   找到一条 bot 消息:", repr((m.get("content") or {}).get("text", ""))[:60])
        break
else:
    print("   最近 60 条里没有 bot 自己发的消息（不影响上面的结论）")
