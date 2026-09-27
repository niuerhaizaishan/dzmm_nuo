"""把 JSON 兜底文件（state.json / profiles.json）对账回 SQLite（P12-U 事故善后）。

### 为什么需要

2026-09-27：`Store` 的连接默认 `check_same_thread=True`，而 bot 大量走
`asyncio.to_thread(...)`（发骚 `scene()`、剧情/投票/公演状态机、`sync_profiles()`），
于是**工作线程**的读写一律抛 `ProgrammingError`，被 `state_store` 静默退回到 JSON 文件：
主线程读写走 sqlite、工作线程读写走 JSON —— 同一群的状态被劈成两半。

代码已修（`runtime/store.py`：`check_same_thread=False` + 锁）。
这个脚本负责把**已经被写进 JSON 的那一半**并回 sqlite，否则：
  - 进行中的发骚会话只在 JSON 里 ⇒ `/结束` 仍然读不到 ⇒ 还是静默；
  - 工作线程里的档案更新（性别、XP 等）也只在 profiles.json 里。

### 合并规则（保守，宁可少并不可错并）

`group` 作用域，逐群逐键：
  - `fasao/diaojiao/perf/vote/story`（状态机）：JSON 里 `active=True` 而 sqlite 不是，
    或 JSON 的内嵌时间戳（`last_ts`/`last_interact_ts`/`last_activity`/`ts`/`start_ts`）
    更新 ⇒ 用 JSON；否则保留 sqlite。
  - 其他键：**只在 sqlite 缺失时**补进去；两边都有但不同 ⇒ 只报告，不动
    （这类键由主线程维护，sqlite 才是权威）。

`profile` 作用域，逐人：sqlite 里**非空**的值优先，JSON 补齐缺失/空的字段。

用法：
    python tools/merge_json_into_store.py --dry-run     # 只看计划
    python tools/merge_json_into_store.py --apply       # 落库（先自动备份）
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from runtime.store import SCOPE_GROUP, SCOPE_PROFILE, Store   # noqa: E402

ROOT = Path("/app")
STATE_JSON = ROOT / "state.json"
PROFILES_JSON = ROOT / "profiles.json"
DB = Path(os.environ.get("DZMM_STORE_DB") or (ROOT / "data" / "store.db"))

STATE_KEYS = ("fasao", "diaojiao", "perf", "vote", "story")
TS_FIELDS = ("last_ts", "last_interact_ts", "last_activity", "ts", "start_ts")
EMPTY = (None, "", [], {})


def _active(v) -> bool:
    return bool(isinstance(v, dict) and v.get("active"))


def _ts(v):
    if not isinstance(v, dict):
        return 0.0
    out = 0.0
    for f in TS_FIELDS:
        try:
            out = max(out, float(v.get(f) or 0))
        except Exception:                                     # noqa: BLE001
            pass
    return out


def _prefer_json_state(jv, sv) -> str:
    """状态机键：JSON 赢还是 sqlite 赢？返回 'json' / 'sql'。"""
    if sv is None:
        return "json"
    if _active(jv) and not _active(sv):
        return "json"
    if _ts(jv) > _ts(sv):
        return "json"
    return "sql"


def _short(v, n=70):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s[:n]


def merge(dry: bool) -> None:
    store = Store(DB)
    plan = {"group_new": [], "group_json_wins": [], "group_conflict": [],
            "profile_new": [], "profile_filled": [], "profile_conflict": []}

    # ---------------- 群状态
    if STATE_JSON.exists():
        raw = json.loads(STATE_JSON.read_text(encoding="utf-8"))
        for cid, jg in raw.items():
            if not isinstance(jg, dict):
                continue
            sg = store.get(SCOPE_GROUP, cid) or {}
            merged = dict(sg)
            changed = False
            for k, jv in jg.items():
                sv = sg.get(k)
                if sv is None and k not in sg:
                    merged[k] = jv
                    changed = True
                    plan["group_new"].append((cid, k, _short(jv)))
                elif k in STATE_KEYS:
                    if _prefer_json_state(jv, sv) == "json":
                        merged[k] = jv
                        changed = True
                        plan["group_json_wins"].append((cid, k, _short(jv)))
                elif jv != sv:
                    plan["group_conflict"].append((cid, k, _short(sv), _short(jv)))
            if changed and not dry:
                store.set(SCOPE_GROUP, cid, merged)

    # ---------------- 档案
    if PROFILES_JSON.exists():
        jp = json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
        for uid, jv in jp.items():
            if not isinstance(jv, dict):
                continue
            sv = store.get(SCOPE_PROFILE, uid)
            if sv is None:
                plan["profile_new"].append((uid, len(jv)))
                if not dry:
                    store.set(SCOPE_PROFILE, uid, jv)
                continue
            merged = dict(jv)                              # JSON 打底
            for k, v in sv.items():
                if v not in EMPTY:
                    merged[k] = v                          # sqlite 非空优先
            filled = sorted(k for k in jv if k not in sv or sv[k] in EMPTY)
            if filled:
                plan["profile_filled"].append((uid, filled))
            if merged != sv and not dry:
                store.set(SCOPE_PROFILE, uid, merged)
            for k in set(jv) & set(sv):
                if jv[k] != sv[k] and jv[k] not in EMPTY and sv[k] not in EMPTY:
                    plan["profile_conflict"].append((uid, k))

    print("=== 计划（{}）===".format("dry-run，未落库" if dry else "已落库"))
    print("群：新增键 {} 个 / JSON 覆盖状态机键 {} 个 / 冲突（不动，仅报告）{} 个".format(
        len(plan["group_new"]), len(plan["group_json_wins"]), len(plan["group_conflict"])))
    for cid, k, v in plan["group_new"][:20]:
        print("   + {} {}".format(str(cid)[:8], k))
    for cid, k, v in plan["group_json_wins"]:
        print("   ~ {} {} = {}".format(str(cid)[:8], k, v))
    for cid, k, sv, jv in plan["group_conflict"][:15]:
        print("   ? {} {}  sqlite={}  json={}".format(str(cid)[:8], k, sv, jv))
    print("档案：新增 {} 人 / 补字段 {} 人 / 冲突 {} 处".format(
        len(plan["profile_new"]), len(plan["profile_filled"]),
        len(plan["profile_conflict"])))
    for uid, n in plan["profile_new"][:10]:
        print("   + {}（{} 字段）".format(str(uid)[:8], n))
    for uid, keys in plan["profile_filled"][:10]:
        print("   ~ {} 补 {}".format(str(uid)[:8], keys))
    store.close()


def main() -> None:
    dry = "--apply" not in sys.argv
    if not dry:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bdir = DB.parent / ("backup-state-" + stamp)
        bdir.mkdir(parents=True, exist_ok=True)
        for p in (STATE_JSON, PROFILES_JSON, DB,
                  Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
            if p.exists():
                shutil.copy2(p, bdir / p.name)
        print("已备份到", bdir)
    merge(dry)


if __name__ == "__main__":
    main()
