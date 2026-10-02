"""线上体检（第二份）：出站失败明细 + 提示词 + 数据体积 + 最近告警（只读）。"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from runtime import prompts as PR                               # noqa: E402

print("=== 出站队列：失败明细 ===")
con = sqlite3.connect("/app/data/outbox.db")
con.row_factory = sqlite3.Row
for r in con.execute("select * from outbound where status!='sent'"
                     " order by created_at desc limit 10"):
    d = dict(r)
    print("  {} {} cid={} {}字 attempt={} status={}".format(
        d["id"][:8], time.strftime("%m-%d %H:%M:%S", time.localtime(d["created_at"])),
        str(d["chatroom_id"])[:8], len(d["text"]), d["attempt_count"], d["status"]))
    print("     last_error={}".format(str(d.get("last_error"))[:120]))
    print("     正文首 40 字：{}".format(d["text"][:40].replace("\n", "⏎")))
print("  按状态：", {r["status"]: r["n"] for r in con.execute(
    "select status, count(*) n from outbound group by status")})
print("  按投递类型：", {r["delivery_kind"]: r["n"] for r in con.execute(
    "select delivery_kind, count(*) n from outbound group by delivery_kind")})
slow = list(con.execute(
    "select count(*) n from outbound where sent_at is not null"))
print("  已确认送达 {} 条".format(slow[0]["n"]))

print()
print("=== 提示词 ===")
try:
    st = PR.stats()
    print("  ", json.dumps(st, ensure_ascii=False)[:300])
except Exception as e:                                         # noqa: BLE001
    print("  stats 读不到：", e)
try:
    names = PR.names() if hasattr(PR, "names") else None
    print("  登记数量：{}".format(len(names) if names else "?"))
except Exception as e:                                         # noqa: BLE001
    print("  names 读不到：", e)

print()
print("=== 数据体积 ===")
for p in ("/app/data", "/app/config"):
    tot = 0
    for f in Path(p).rglob("*"):
        if f.is_file():
            tot += f.stat().st_size
    print("  {} 合计 {:.1f} MB".format(p, tot / 1048576))
for f in ("/app/data/store.db", "/app/data/outbox.db", "/app/state.json",
          "/app/profiles.json", "/app/config/config.json"):
    fp = Path(f)
    if fp.exists():
        print("  {:28s} {:8.2f} MB".format(f, fp.stat().st_size / 1048576))

print()
print("=== 配置里那些「看起来没用」的键 ===")
raw = json.load(open("/app/config/config.json", encoding="utf-8"))
print("  顶层键：", sorted(raw.keys()))
print("  features（全局）：", json.dumps(raw.get("features") or {}, ensure_ascii=False))
print("  tuning（全局）：", json.dumps(raw.get("tuning") or {}, ensure_ascii=False))
print("  scopes：", json.dumps(raw.get("scopes") or {}, ensure_ascii=False))
feats_used = set()
for g in raw.get("groups") or []:
    feats_used |= set((g.get("features") or {}).keys())
print("  按群覆盖用到的功能 key {} 个".format(len(feats_used)))
