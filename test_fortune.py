#!/usr/bin/env python3
"""测试五行运势算分（不调 AI，只验证算分和组装）。"""
import sys

sys.path.insert(0, "/app")
import bot

POSTS = [
    ("cf1b1360-7f3f-4ef8-9188-07d718fe9173", "御褚"),
    ("a56cbd67-3a1e-439a-8beb-d1557dbeff79", "糯糯～"),
    ("625e5f9f-0000-0000-0000-000000000000", "林九鹓"),
    ("cf541812-0000-0000-0000-000000000000", "清歌"),
]

today = bot.datetime.now().strftime("%Y-%m-%d")
print("今日:", today, "| 干支:", bot._ganzhi(bot.datetime.now()))
print()

# 用真实 profile 找人（按名字匹配 uid）
import json
prof = json.load(open("/app/profiles.json"))
name2uid = {}
for uid, v in prof.items():
    if isinstance(v, dict) and v.get("name"):
        name2uid[v["name"]] = uid

for uid, label in POSTS:
    # 若 uid 不存在则按名字找
    if uid not in prof:
        uid = name2uid.get(label) or uid
    full = bot.get_full_name(uid) if uid in prof else label
    bio = str((bot.get_profile(uid) or {}).get("bio") or "")
    info = bot._fortune_calc(uid, full, today, bio)
    lk = info["luck"]
    dw = info["day_wx"]
    yi = "、".join(bot._WX_YI.get(dw, ()))
    ji = "、".join(bot._WX_JI.get(dw, ()))
    chong = bot._SHENGXIAO.get(bot._CHONG.get(info["day_zhi"], ""), "")
    d = info["dims"]
    print("=" * 46)
    print(f"🔮 {full} 今日运势 {info['score']}分（{info['level']}）")
    print(f"📜 命盘：本命{info['ben_ganzhi']}{info['ben_wx']}·名格{info['name_wx']}·今日{info['day_ganzhi']}{info['day_wx']}")
    print(f"   → 本命{bot._REL_TEXT.get(info['rel_ben'],'')}、名格{bot._REL_TEXT.get(info['rel_name'],'')}")
    print(f"📅 宜{yi} ｜ 忌{ji} ｜ 冲{chong}")
    print(f"🧮 命基{bot._WX_BASE.get(info['ben_wx'],50)} ± 名格{bot._REL_SCORE.get(info['rel_name'],8)} ± 本命{bot._REL_SCORE.get(info['rel_ben'],8)} + 人和{info['human']} = {info['score']}")
    print(f"📈 💰财运{d['财运']} · 💼事业{d['事业']} · 💗感情{d['感情']} · 🩺健康{d['健康']}")
    print(f"🍀 幸运：{lk[0]} · {lk[1]} · {lk[2]}")
