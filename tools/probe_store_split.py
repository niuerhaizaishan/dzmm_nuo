"""对比 sqlite 与 state.json（JSON 兜底文件）——确认"双写分叉"（只读）。"""
import json
import os
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import state_store                                            # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
MARK = "糯糯大嘴巴抽晴天"

sj = "/app/state.json"
raw = json.loads(open(sj, encoding="utf-8").read())
print("state.json 大小={}B mtime={}".format(
    os.path.getsize(sj), time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(sj)))))
print("  顶层键：", sorted(raw.keys()))
print("  含 15:39 那场发骚：", MARK in json.dumps(raw, ensure_ascii=False))
g = raw.get(CID) or {}
print("  本群键：", sorted(g.keys()))
fs = g.get("fasao") or {}
print("  本群 fasao：active={} full={!r} history={} last_ts={}".format(
    fs.get("active"), fs.get("full"), len(fs.get("history") or []), fs.get("last_ts")))
print("  本群 last_action.full =", (g.get("last_action") or {}).get("full"))
print("  本群 heat_value =", str(g.get("heat_value"))[:80])

print()
print("sqlite 里的本群：")
sq = state_store.group_get(CID, "fasao")
print("  fasao =", str(sq)[:120])
print("  heat_value =", str(state_store.group_get(CID, "heat_value"))[:80])
print("  last_action.full =", (state_store.group_get(CID, "last_action") or {}).get("full"))

print()
print("两个后端分叉的群（键集合差异）：")
sq_all = state_store.load_state()
for cid in sorted(set(raw.keys()) | set(sq_all.keys())):
    a = set((raw.get(cid) or {}).keys()) if isinstance(raw.get(cid), dict) else set()
    b = set((sq_all.get(cid) or {}).keys()) if isinstance(sq_all.get(cid), dict) else set()
    only_json = sorted(a - b)
    only_sql = sorted(b - a)
    if only_json or only_sql:
        print("  {}  只在JSON={}  只在sqlite={}".format(str(cid)[:8], only_json, only_sql))
