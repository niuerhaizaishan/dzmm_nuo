"""看一眼功能开关现状：全局覆盖 + 新群里各功能是否生效（只读）。"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from runtime import features as F          # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"

print("=== config.json 里的 features 覆盖（全局 + 按群）===")
try:
    raw = json.load(open("/app/config/config.json", encoding="utf-8"))
    feats = raw.get("features") or {}
    if not feats:
        print("  （没有 features 节 → 全部走代码默认值）")
    for k, v in sorted(feats.items()):
        print("  {} = {}".format(k, v))
except Exception as e:                                      # noqa: BLE001
    print("  读取失败：", e)

print()
print("=== 新群（若瑾那个）里各功能是否生效 ===")
keys = ("scene", "scene_continue", "scene_rewrite", "scene_end", "insult",
        "diaojiao", "heat", "devil_roast", "devil_cold", "dice", "vote", "story",
        "poem", "fortune", "horoscope", "trend", "recap", "summary", "rank_heat",
        "register", "query", "archive_build", "memory_remember", "memory_recall",
        "memory_report", "person_summary", "sync", "help", "intro", "profile_card",
        "greet_morning", "greet_noon", "greet_evening", "rank_push", "profile_scan",
        "heartbeat", "health_check", "screen", "screen_welcome", "alert_send_fail")
on, off = [], []
for k in keys:
    try:
        (on if F.on(CID, k) else off).append(k)
    except Exception as e:                                  # noqa: BLE001
        off.append("{}（未知：{}）".format(k, e))
print("  开着({}): {}".format(len(on), ", ".join(on)))
print("  关着({}): {}".format(len(off), ", ".join(off)))
print()
print("  全部功能数：", len(F.keys()))
