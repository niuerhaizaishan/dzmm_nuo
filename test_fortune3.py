#!/usr/bin/env python3
"""验证：昵称带括号后缀的人，AI 是否用主名称呼（不再误认括号里的名字）。"""
import json
import sys

sys.path.insert(0, "/app")
import bot

bot._ACTIVE_CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
prof = json.load(open("/app/profiles.json"))
name2uid = {v.get("name"): uid for uid, v in prof.items()
            if isinstance(v, dict) and v.get("name")}

for nm in ["糯糯～（裳姐姐的骚贱小母狗❤）", "过期少女（小号）", "VvNa（被百人斩6/100）"]:
    uid = name2uid.get(nm)
    if not uid:
        print("找不到:", nm)
        continue
    out = bot.fortune(uid)
    lines = out.split("\n")
    print("=" * 50)
    print(out)
    print(f"--- 行数: {len(lines)} {'OK' if len(lines) <= 10 else 'OVER!'}")
    print()
