"""看上班群里"调教"相关的配置与现状（只读），给开启做依据。"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from runtime import features as F                             # noqa: E402
from runtime import tuning as T                               # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
KEYS = ("diaojiao", "diaojiao_idle", "scene", "scene_end", "insult", "heat",
        "dice", "vote", "story", "perf", "devil_roast", "help")

raw = json.load(open("/app/config/config.json", encoding="utf-8"))
g = [x for x in (raw.get("groups") or []) if str(x.get("cid", "")).startswith("f4ec4c21")]
g = g[0] if g else {}
print("群条目键：", sorted(g.keys()))
print("features：", json.dumps(g.get("features") or {}, ensure_ascii=False))
print("tuning（本群）：", json.dumps(g.get("tuning") or {}, ensure_ascii=False))
print("tuning（全局）：", json.dumps(raw.get("tuning") or {}, ensure_ascii=False))
print("personas 覆盖：", sorted((raw.get("personas") or {}).keys()))
print("version：", raw.get("version"))

print()
print("=== 这个群里各功能现在的状态 ===")
for k in KEYS:
    try:
        print("  {:16s} {}".format(k, "开" if F.on(CID, k) else "关"))
    except Exception as e:                                     # noqa: BLE001
        print("  {:16s} ? {}".format(k, e))

print()
print("=== 调教相关可调参数 ===")
for p in ("diaojiao.timeout", "diaojiao.idle_seconds", "heat.diaojiao_round"):
    try:
        print("  {:24s} = {}（本群生效值）".format(p, T.get(p, CID)))
    except Exception as e:                                     # noqa: BLE001
        print("  {:24s} ? {}".format(p, e))

print()
print("=== 开了调教之后，斜杠指令的归属会变 ===")
import bot                                                     # noqa: E402
for cmd in ("/调教 糯糯 学狗叫", "/命令 跪好", "/talk妈妈", "/跪好"):
    print("  {!r:24s} is_diaojiao_cmd={!r:8s} is_dj_command_cmd={!r}".format(
        cmd, bot.is_diaojiao_cmd(cmd), bot.is_dj_command_cmd(cmd)))
