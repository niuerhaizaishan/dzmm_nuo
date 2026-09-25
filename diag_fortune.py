#!/usr/bin/env python3
"""诊断 /运势 耗时：分别测算分、AI 调用、总耗时。"""
import json
import sys
import time

sys.path.insert(0, "/app")
import bot
import brains

bot._set_active_cid("bd7fe6a4-0065-4551-ad3d-94116dba8f9c")
prof = json.load(open("/app/profiles.json"))
name2uid = {v.get("name"): uid for uid, v in prof.items()
            if isinstance(v, dict) and v.get("name")}
uid = name2uid.get("御褚")
full = bot.get_full_name(uid)
today = bot.datetime.now().strftime("%Y-%m-%d")

t0 = time.time()
info = bot._fortune_calc(uid, full, today, "test bio")
t1 = time.time()
print(f"[算分 _fortune_calc] {t1 - t0:.3f}s")

hint = "用户主名：御褚\n命盘：本命金（受生）、名格土（比和）；四维：健康80"
t2 = time.time()
txt = brains.generate_fortune("御褚", info["score"], today, hint)
t3 = time.time()
print(f"[AI generate_fortune] {t3 - t2:.3f}s  → {str(txt)[:60]}")

t4 = time.time()
out = bot.fortune(uid)
t5 = time.time()
print(f"[fortune 全文] {t5 - t4:.3f}s  行数={len(out.split(chr(10)))}")

# 再测一次 AI（看是否第二次更快/更慢）
t6 = time.time()
brains.generate_fortune("御褚", 85, today, hint)
t7 = time.time()
print(f"[AI 第二次] {t7 - t6:.3f}s")

# 测读文件耗时
t8 = time.time()
for _ in range(5):
    bot.get_profile(uid)
    bot.load_state()
t9 = time.time()
print(f"[读 profile+state ×5] {t9 - t8:.3f}s")
