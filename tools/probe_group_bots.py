"""上班群里有没有"另一个机器人"？顺带确认新代码在跑（只读）。"""
import hashlib
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import state_store                                            # noqa: E402

for f in ("/app/runtime/store.py", "/app/state_store.py", "/app/bot.py"):
    h = hashlib.md5(open(f, "rb").read()).hexdigest()
    print("  md5 {}  {}".format(h[:12], f))

print()
print("=== 档案里像机器人的成员（名字含 熊/bot/机器人/助手/AI）===")
profs = state_store.load_profiles()
for uid, p in profs.items():
    name = str(p.get("full_name") or p.get("name") or "")
    if any(k in name for k in ("熊", "bot", "Bot", "机器人", "助手", "AI")):
        print("  {}  {!r}  性别={}  字段={}".format(
            uid[:8], name, p.get("gender"), sorted(p.keys())[:8]))

print()
print("=== 工具熊 Jr. 的档案 ===")
gjx = state_store.get_profile("84065dcd-d87f-40a9-8fa5-8bdcf5d40618")
print("  ", str(gjx)[:400] if gjx else "（没有档案）")
