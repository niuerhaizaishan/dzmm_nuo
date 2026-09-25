#!/usr/bin/env python3
"""验证新格式（命盘合并一行 + 破折号分隔线）。"""
import json
import sys

sys.path.insert(0, "/app")
import bot

bot._ACTIVE_CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
prof = json.load(open("/app/profiles.json"))
name2uid = {v.get("name"): uid for uid, v in prof.items()
            if isinstance(v, dict) and v.get("name")}

for nm in ["糯糯～（裳姐姐的骚贱小母狗❤）", "御褚"]:
    uid = name2uid.get(nm)
    if not uid:
        print("找不到:", nm)
        continue
    out = bot.fortune(uid)
    print("=" * 46)
    print(out)
    print(f"--- 行数: {len(out.split(chr(10)))}")
    print()
