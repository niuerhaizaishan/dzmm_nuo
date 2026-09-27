"""定位「发骚会话写到哪个 cid」——把 sqlite 里的原始行全列出来（只读）。"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import state_store                                            # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
MARK = "糯糯大嘴巴抽晴天"

print("=== 后端 ===")
print(" ", state_store.stats())
db = "/app/data/store.db"
print("  db mtime:", time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(db))))
sj = "/app/state.json"
print("  state.json:", os.path.exists(sj),
      time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(sj))) if os.path.exists(sj) else "")

con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
print("  表：", tables)
rows = []
for t in tables:
    try:
        rows += [dict(r) for r in con.execute("select * from " + t)]
    except Exception as e:                                    # noqa: BLE001
        print("  读 {} 失败：{}".format(t, e))

print()
print("=== 所有含 '{}' 的行 ===".format(MARK))
for d in rows:
    v = str(d)
    if MARK in v:
        k = d.get("k") or d.get("key") or d.get("cid") or "?"
        print("  key={} scope={} 长度={} 含标记".format(
            str(k)[:40], d.get("scope") or d.get("ns"), len(v)))

print()
print("=== 每个 group 行的 fasao 摘要 ===")
keys = []
for d in rows:
    sc = str(d.get("scope") or d.get("ns") or "")
    k = d.get("k") or d.get("key") or d.get("cid")
    if not k:
        continue
    if sc and sc != "group":
        continue
    keys.append(str(k))
    v = d.get("v") or d.get("value") or ""
    try:
        import json as _j
        obj = _j.loads(v) if isinstance(v, str) else (v or {})
    except Exception:                                         # noqa: BLE001
        obj = {}
    if isinstance(obj, dict) and obj.get("fasao"):
        fs = obj["fasao"]
        print("  {}  full={!r} history={} active={}".format(
            str(k)[:8], fs.get("full"), len(fs.get("history") or []), fs.get("active")))
    if isinstance(obj, dict) and MARK in _j.dumps(obj, ensure_ascii=False):
        print("      ^^ 这个 cid 里有 15:39 那场发骚：last_action.full={!r}".format(
            (obj.get("last_action") or {}).get("full")))

print()
print("  本群 last_msg_time =", time.strftime(
    "%H:%M:%S", time.localtime(state_store.group_get(CID, "last_msg_time") or 0)))
print("  本群 fasao =", str(state_store.group_get(CID, "fasao"))[:200])
print("  group 行 keys =", [k[:8] for k in keys])
