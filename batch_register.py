#!/usr/bin/env python3
"""批量注册妹子认证群没注册的用户。--dry-run 只列名单；--execute 实际注册。"""
import sys, time
from datetime import datetime
sys.path.insert(0, "/app")
import bot
from profiles import load_profiles, upsert_profile
import brains

cid = bot.PRIMARY_CID
bot._ACTIVE_CID = cid
EXECUTE = "--execute" in sys.argv

members = bot.get_members(cid)
profiles = load_profiles()
registered = set(profiles.keys())

# 待注册 = 成员里不在 profiles 且不是 agent 自己
unregistered = [(uid, name) for uid, name in members.items()
                if uid not in registered and uid != bot.NUO_UID]

print(f"群成员 {len(members)} 人 | 已注册 {len(registered)} 人 | 待注册 {len(unregistered)} 人", flush=True)
print("=" * 60, flush=True)

if not EXECUTE:
    for uid, name in unregistered:
        print(f"  [待注册] {name}  ({uid[:8]})", flush=True)
    print("\n(DRY-RUN，未实际注册。加 --execute 才会注册)", flush=True)
    sys.exit(0)

msgs = bot.get_messages_all(cid, max_count=300)

ok = fail = 0
for uid, full in unregistered:
    try:
        u = bot.get_user(cid, uid)
        profile_text = bot._member_profile_text(uid, full, msgs)
        summary = brains.generate_profile_summary(profile_text)
        xp = bot._extract_xp(profile_text)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        profile = {
            "name": full,
            "gender": str(u.get("gender")) if u and u.get("gender") else "",
            "signature": (u.get("quirk") or "") if u else "",
            "bio": (u.get("bio") or "") if u else "",
            "registered_at": now,
            "register_summary": summary,
            "xp": xp,
            "last_seen": now,
            "query_count": 0,
        }
        if upsert_profile(uid, profile):
            print(f"✅ {full} 注册成功", flush=True)
            ok += 1
        else:
            print(f"❌ {full} 注册失败（存档）", flush=True)
            fail += 1
    except Exception as e:
        print(f"❌ {full} 注册异常: {str(e)[:80]}", flush=True)
        fail += 1
    time.sleep(1.5)

print("=" * 60, flush=True)
print(f"完成：成功 {ok}，失败 {fail}", flush=True)
