"""P12-T：把「若瑾」接进来 —— 服务器侧一次做完（.env + config.json）。

顺序（调用方保证）：先部署带 `ruojin` 人设的代码，再跑这个脚本，最后 `-Action compose`
（compose 是逐键透传，新 Bot 的 token/secret 必须在 compose 里也加一行才进得了容器）。

做的事：
  1. `.env` 追加 `DZMM_BOT_TOKEN_RUOJIN` / `DZMM_BOT_SECRET_RUOJIN`（已有就覆盖成给定值，
     改前备份 `.env.bak-<时间>`）；
  2. `config.json` 追加群「上班如上坟，下班如还魂」与 Bot「若瑾」，人设 `ruojin`，
     uid 直接写死（9938211c…），走 `config_store.save()`（**先校验、再备份、原子写**）；
  3. 打印结果供核对。幂等：已存在就只更新，不会写重复项。
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

ENV_PATH = Path("/opt/dzmm/.env")
if not ENV_PATH.is_file():
    ENV_PATH = Path("/app/.env")

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
ALIAS = "上班如上坟，下班如还魂"
KEY = "ruojin"
NAME = "若瑾"
SHORT = "若瑾"
TOKEN_ENV = "DZMM_BOT_TOKEN_RUOJIN"
TOKEN = "b623bc24-f82a-4c34-923a-fd970f599fb4"
SECRET = "550a4f1e-f786-43ff-8917-de980c45959d"
UID = "9938211c-b245-4cad-a594-cd9a1eb4966a"
PERSONA = "ruojin"

# ---------------------------------------------------------------- 1) .env
print("=== .env ===")
if not ENV_PATH.is_file():
    print("  跳过：容器里看不到 {}（.env 在宿主机，用短命令单独追加即可）".format(ENV_PATH))
else:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = ENV_PATH.with_name(ENV_PATH.name + ".bak-" + stamp)
    shutil.copy2(str(ENV_PATH), str(bak))
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    want = {"DZMM_BOT_TOKEN_RUOJIN": TOKEN, "DZMM_BOT_SECRET_RUOJIN": SECRET}
    seen = set()
    out = []
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in want:
            out.append("{}={}".format(k, want[k]))
            seen.add(k)
        else:
            out.append(ln)
    for k, v in want.items():
        if k not in seen:
            out.append("{}={}".format(k, v))
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("  备份:", bak)
    for k in want:
        print("  {} = {}…".format(k, want[k][:8]))

# ---------------------------------------------------------------- 2) config
print("=== config.json ===")
import dashboard                                    # noqa: E402
from runtime import config_store as cs              # noqa: E402

defaults = dashboard._config_defaults()
if defaults.get("error"):
    raise SystemExit("读默认值失败：{}".format(defaults["error"]))
cfg = cs.reload(defaults)
raw = json.loads(json.dumps(cfg.as_dict(), ensure_ascii=False))

g = next((x for x in raw.get("groups", []) if x.get("cid") == CID), None)
if g is None:
    g = {"cid": CID, "alias": ALIAS, "enabled": True, "persona": PERSONA}
    raw.setdefault("groups", []).append(g)
    print("  新增群:", CID)
else:
    g.update({"alias": ALIAS, "enabled": True, "persona": PERSONA})
    print("  已存在群，更新:", CID)

b = next((x for x in raw.get("bots", []) if x.get("key") == KEY), None)
if b is None:
    b = {"key": KEY, "name": NAME, "short": SHORT, "cid": CID,
         "token_env": TOKEN_ENV, "enabled": True, "uid": UID}
    raw.setdefault("bots", []).append(b)
    print("  新增 Bot:", KEY)
else:
    b.update({"name": NAME, "short": SHORT, "cid": CID,
              "token_env": TOKEN_ENV, "enabled": True, "uid": UID})
    print("  已存在 Bot，更新:", KEY)

# ---------------------------------------------------------------- 3) 功能开关
# 屋主 2026-09-27："功能的话先开放发骚和羞辱。"
# 所以本群**显式打开**这两项，其余"玩法"类逐项关掉（按群覆盖，不动别的群）；
# 基础功能（帮助/介绍/记忆/查人/注册/同步/心跳/告警）保留，不然 `/帮助` 都会说没开。
ENABLE = ("scene", "insult")
DISABLE = (
    "dice", "dice_timeout", "diaojiao", "diaojiao_idle", "vote", "story",
    "devil_roast", "devil_cold", "heat_climax", "perf", "perf_enrich",
    "perf_review", "perf_novel", "poem", "fortune", "horoscope", "trend",
    "recap", "summary", "rank_heat", "rank_push", "greet_morning", "greet_noon",
    "greet_evening", "profile_scan", "notice_rename",
)
print("=== 功能开关（本群）===")
# ⚠️ 按群的覆盖写在**群条目自己的 features 里**（`Config.feature_map()` 会把
# `group_specs[cid].features` 展平成 `"cid|key"`）—— 顶层 features 只收**全局裸 key**，
# 写成 `"cid|key"` 会被校验拒绝（第一次就是这么被挡下的）。
gfeat = g.setdefault("features", {})
for k in ENABLE:
    gfeat[k] = True
for k in DISABLE:
    gfeat[k] = False
print("  显式开：", ", ".join(ENABLE))
print("  显式关（{} 项）：{}".format(len(DISABLE), ", ".join(DISABLE)))
print("  保留（基础）：help/intro/memory_*/query/register/archive_build/"
      "profile_card/person_summary/sync/heartbeat/health_check/alert_*/heat/"
      "scene_continue/scene_rewrite/scene_end")

cs.save(raw, defaults)          # 校验不过一个字都不写
cfg2 = cs.reload(defaults)
print("  群清单:")
for x in cfg2.groups:
    print("    {:38s} {:26s} enabled={} persona={}".format(
        x.cid, x.alias, x.enabled, getattr(x, "persona", "")))
print("  Bot 清单:")
for x in cfg2.bots:
    print("    key={:10s} name={:8s} cid={:38s} token_env={:24s} uid={}".format(
        x.key, x.name, x.cid, x.token_env, x.uid))

print("=== 本群生效的玩法 ===")
from runtime import features as F                            # noqa: E402
plays = ("scene", "insult", "dice", "vote", "story", "diaojiao", "poem",
         "fortune", "horoscope", "trend", "recap", "summary", "rank_heat",
         "devil_roast", "heat_climax", "help", "memory_recall", "query")
on = [k for k in plays if F.on(CID, k)]
off = [k for k in plays if not F.on(CID, k)]
print("  开:", ", ".join(on))
print("  关:", ", ".join(off))
