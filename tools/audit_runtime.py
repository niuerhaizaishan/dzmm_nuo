"""线上运行体检（容器内跑，只读）：配置 / 存储 / 出站 / 热加载 / 人设 / 提示词。"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import personas as P                                           # noqa: E402
import state_store                                             # noqa: E402
from admin import dashboard                                    # noqa: E402
from runtime import config_store                               # noqa: E402
from runtime import features as F                              # noqa: E402
from runtime import prompts as PR                              # noqa: E402
from runtime import tuning as T                                # noqa: E402


def human(sec):
    sec = int(sec)
    return "{}h{}m".format(sec // 3600, (sec % 3600) // 60)


print("=== 进程/环境 ===")
print("  pid={} uptime={} python={}".format(
    os.getpid(), human(time.time() - float(os.environ.get("BOT_START_TS") or 0))
    if os.environ.get("BOT_START_TS") else "?", sys.version.split()[0]))
for k in ("DZMM_HOST", "DZMM_STORE", "DZMM_STORE_DB", "DZMM_WEBHOOK_PORT",
          "DZMM_PARALLEL_GROUPS", "DZMM_FEATURES"):
    print("  {}={!r}".format(k, os.environ.get(k)))

print()
print("=== 配置 ===")
defaults = dashboard._config_defaults()
cfg = config_store.reload(defaults)
print("  schema_version={} source={} path={}".format(
    cfg.version if hasattr(cfg, "version") else config_store.SCHEMA_VERSION,
    cfg.source, cfg.path))
print("  群 {} 个（启用 {}）  Bot {} 个  mute={} screen={} push={}".format(
    len(cfg.group_specs), sum(1 for s in cfg.group_specs.values() if s.enabled),
    len(cfg.bots), len(cfg.mute or []), len(cfg.screen or []), len(cfg.push or [])))
for s in cfg.group_specs.values():
    print("    {} {!r} enabled={} persona={!r} features覆盖={}".format(
        str(s.cid)[:8], s.alias, s.enabled, s.persona or "(默认)",
        len(getattr(s, "features", {}) or {})))

print()
print("=== 功能注册表 ===")
F.set_overrides(cfg.feature_map())
T.set_overrides(cfg.tuning_map())
s = F.summary()
print("  共 {} 项：已接线 {} / 待接线 {}；默认关 {}；当前关 {}".format(
    s["total"], s["wired"], s["planned"], s["off_by_default"], len(s["off_now"])))
print("  参数共 {} 项，被配置覆盖 {} 项".format(len(T.PARAMS), len(T.overrides())))
print("  集合(scopes)：{}".format({k: len(v) for k, v in (T.scopes() or {}).items()}) if
      hasattr(T, "scopes") else "")
for cid in [x.cid for x in cfg.group_specs.values() if x.enabled]:
    on = [k for k in F.keys() if F.on(cid, k)]
    print("    {} 开 {} 项".format(str(cid)[:8], len(on)))

print()
print("=== 人设与话术 ===")
P.set_content_overrides(cfg.persona_texts())
P.set_config_personas({x.cid: x.persona for x in cfg.group_specs.values() if x.persona})
for key in P.PERSONAS:
    h = P.health(key)
    print("  {:14s} 文本 {} 字  话术 {} 条  体检 {}".format(
        key, len(P.effective_persona(key).get("extra") or ""),
        len(P.phrases_for(key)), "通过" if not h else "问题 " + str(h[:2])))
print("  配置覆盖的人设：{}".format(sorted((cfg.persona_texts() or {}).keys())))

print()
print("=== 提示词 ===")
try:
    st = PR.stats()
    print("  {}（目录 {}）".format(st, PR.default_dir()))
except Exception as e:                                         # noqa: BLE001
    print("  stats 读不到：", e)

print()
print("=== 存储 ===")
print("  ", state_store.stats())
st = state_store.load_state()
groups = [k for k, v in st.items() if isinstance(v, dict) and len(k) == 36]
print("  状态里群 {} 个 / 顶层键 {}".format(
    len(groups), [k for k in st if len(k) != 36]))
print("  档案 {} 份".format(len(state_store.load_profiles())))
hot = [k for k in st if len(k) == 36 and (st[k].get("heat_value") or {})]
print("  有发情值记录的群：{}".format([k[:8] for k in hot]))

print()
print("=== 出站队列 ===")
try:
    db = "/app/data/outbox.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = list(con.execute("select status, count(*) n from outbound group by status"))
    print("  ", {r["status"]: r["n"] for r in rows})
    tot = con.execute("select count(*) from outbound").fetchone()[0]
    first = con.execute("select min(created_at) from outbound").fetchone()[0]
    print("  累计 {} 条，最早 {}".format(
        tot, time.strftime("%m-%d %H:%M", time.localtime(first)) if first else "-"))
    pend = list(con.execute("select id, created_at, chatroom_id, length(text) n,"
                            " status, error from outbound where status!='sent'"
                            " order by created_at desc limit 5"))
    for r in pend:
        print("    ! {} {} {}字 status={} err={}".format(
            r["id"][:8], time.strftime("%m-%d %H:%M", time.localtime(r["created_at"])),
            r["n"], r["status"], str(r["error"])[:40]))
    if not pend:
        print("    没有未发出的消息")
except Exception as e:                                         # noqa: BLE001
    print("  outbox 读不到：", e)

print()
print("=== 热加载状态 ===")
try:
    p = config_store.config_path().parent / "hotload_status.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    print("  at={} source={} ok={} 改动={} 错误={}".format(
        time.strftime("%m-%d %H:%M:%S", time.localtime(d.get("at") or 0)),
        d.get("source"), d.get("ok"), d.get("n_changes"), d.get("error") or "无"))
    print("  counts={}".format(d.get("counts")))
except Exception as e:                                         # noqa: BLE001
    print("  读不到：", e)
