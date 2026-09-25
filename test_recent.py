#!/usr/bin/env python3
"""验证：AI 点评是否结合了用户最近发言。"""
import json
import sys

sys.path.insert(0, "/app")
import bot

bot._set_active_cid("bd7fe6a4-0065-4551-ad3d-94116dba8f9c")
prof = json.load(open("/app/profiles.json"))
name2uid = {v.get("name"): uid for uid, v in prof.items()
            if isinstance(v, dict) and v.get("name")}

for nm in ["御褚", "糯糯～（裳姐姐的骚贱小母狗❤）"]:
    uid = name2uid.get(nm)
    if not uid:
        print("找不到:", nm)
        continue
    p = bot.get_profile(uid) or {}
    recent = [str(m.get("text", ""))[:35] for m in (p.get("messages") or [])[-6:]]
    print("=" * 46)
    print(f"【{nm}】最近发言: {recent}")
    out = bot.fortune(uid)
    print(out.split(chr(10))[-1])  # 只打印最后一句
    print(f"--- 总行数: {len(out.split(chr(10)))}")
    print()
