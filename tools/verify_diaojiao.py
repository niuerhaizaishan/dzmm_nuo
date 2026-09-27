"""调教上线自检（容器内跑，只读）：开关是否生效 + 群里实际会用哪套文案。"""
import hashlib
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import personas as P                                          # noqa: E402
from admin import dashboard                                   # noqa: E402
from runtime import config_store                              # noqa: E402
from runtime import features as F                             # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
KEYS = ("dj_format", "dj_no_target", "dj_start", "dj_timeout",
        "dj_replied", "dj_idle", "dj_hint")

for f in ("/app/personas.py", "/app/runtime/diaojiao.py"):
    print("  md5 {}  {}".format(hashlib.md5(open(f, "rb").read()).hexdigest()[:12], f))

defaults = dashboard._config_defaults()
cfg = config_store.reload(defaults)
F.set_overrides(cfg.feature_map())
P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values()
                       if s.persona})

print()
print("=== 开关（按群）===")
for k in ("diaojiao", "diaojiao_idle", "scene", "insult"):
    print("  {:16s} {}  ({})".format(k, "开" if F.on(CID, k) else "关",
                                     F.why_off(CID, k)))

print()
print("=== 这个群实际会说的调教文案（若瑾）===")
for k in KEYS:
    print("  {:14s} {}".format(k, P.p(k, CID, who="糯糯")))

print()
print("=== 对照：认证群/玫瑰房没被改动 ===")
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
for label, cid in (("认证群", AUTH), ("玫瑰房", ROSE)):
    print("  {:6s} start={!r} hint={!r}".format(
        label, P.p("dj_start", cid, who="某某"), P.p("dj_hint", cid)[:24]))

print()
src = open("/app/runtime/diaojiao.py", encoding="utf-8").read()
# 唯一允许留下的字面量 = fallback 常量本身（老测试钉着它；话术取不到时才用它）
code = "\n".join(ln for ln in src.splitlines()
                 if not ln.startswith("DJ_PARTICIPATE_HINT"))
bad = [s for s in ("想参与的姐妹也来玩呀", "没找到调教目标哦～", "调教开始！目标：")
       if s in code]
print("代码里残留的硬编码调教文案：", bad or "无（都走话术表了，只剩 fallback 常量）")
