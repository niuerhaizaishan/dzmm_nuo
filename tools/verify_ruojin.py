"""只读校验：若瑾那个群的配置、功能开关、人设是否都到位（跑得快，不 import dashboard）。"""
import json
import sys

sys.path.insert(0, "/app")

from runtime import features as F          # noqa: E402
import personas as P                       # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
raw = json.load(open("/app/config/config.json", encoding="utf-8"))

print("=== 群清单 ===")
for g in raw.get("groups", []):
    mark = " ← 若瑾那个群" if g.get("cid") == CID else ""
    print("  {:38s} {:26s} enabled={} persona={}{}".format(
        g.get("cid", ""), g.get("alias", ""), g.get("enabled"),
        g.get("persona") or "（默认）", mark))

print("=== Bot 清单 ===")
for b in raw.get("bots", []):
    print("  key={:10s} name={:8s} cid={:38s} token_env={:20s} enabled={} uid={}".format(
        b.get("key", ""), b.get("name", ""), b.get("cid", ""),
        b.get("token_env", ""), b.get("enabled"), b.get("uid", "")))

print("=== 若瑾那个群的按群开关 ===")
spec = next((g for g in raw.get("groups", []) if g.get("cid") == CID), {})
gf = spec.get("features") or {}
for k, v in sorted(gf.items()):
    print("  {} = {}".format(k, v))
print("  （顶层 features 全局覆盖：{}）".format(
    ", ".join("{}={}".format(k, v) for k, v in sorted((raw.get("features") or {}).items())) or "无"))

# 展平成运行期形状，看实际生效值
ov = {k: bool(v) for k, v in (raw.get("features") or {}).items()}
for k, v in gf.items():
    ov["{}|{}".format(CID, k)] = bool(v)
F.set_overrides(ov)
print("=== 实际生效 ===")
for k in ("scene", "insult", "dice", "vote", "poem", "fortune", "story",
          "diaojiao", "devil_roast", "help", "query", "memory_recall"):
    print("  {:14s} {}".format(k, "开" if F.on(CID, k) else "关"))

print("=== 人设 ===")
P.set_config_personas({CID: spec.get("persona") or ""})
print("  本群自称 =", P.me(CID), "| 全名 =", P.me_full(CID))
print("  自我介绍 =", P.p("intro_text", CID))
print("  帮助文案 =", P.p("help_text", CID).replace("\n", " / "))
