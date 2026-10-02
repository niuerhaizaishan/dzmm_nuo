"""找出"被截断"的证据：出站队列里的原文 + 发骚历史。

判据：一段文字如果**不以句末标点结尾**（。！？…♡～" 」）），基本就是被 max_tokens 掐断的。
"""
import json
import sqlite3
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import state_store                                            # noqa: E402

END_OK = "。！？…♡～)）\"”』」*_"


def flag(t: str) -> str:
    t = (t or "").rstrip()
    if not t:
        return "（空）"
    return "" if t[-1] in END_OK else "  ← 结尾不像句子（疑似截断）"


out = "/app/data/outbox.db"
con = sqlite3.connect(out)
con.row_factory = sqlite3.Row
rows = list(con.execute("select created_at, chatroom_id, length(text) n, text"
                        " from outbound order by created_at"))
print("=== 出站队列覆盖范围 ===")
print("  条数：", len(rows))
if rows:
    print("  最早：", time.strftime("%m-%d %H:%M:%S", time.localtime(rows[0]["created_at"])))
    print("  最晚：", time.strftime("%m-%d %H:%M:%S", time.localtime(rows[-1]["created_at"])))
    import collections
    per_day = collections.Counter(
        time.strftime("%m-%d", time.localtime(r["created_at"])) for r in rows)
    print("  按天：", dict(sorted(per_day.items())))

print()
print("=== 出站里「结尾不像句子」的长消息（>=150 字）===")
bad = [r for r in rows if (r["n"] or 0) >= 150 and flag(r["text"])]
print("  条数：", len(bad))
for r in bad[:12]:
    print("  {} {}  {} 字 / {} 行".format(
        str(r["chatroom_id"])[:8],
        time.strftime("%m-%d %H:%M:%S", time.localtime(r["created_at"])),
        r["n"], r["text"].count("\n") + 1))
    print("     尾 24 字：…{}".format(r["text"][-24:].replace("\n", "⏎")))

print()
print("=== 状态库里的发骚历史（每段长度 + 结尾）===")
try:
    raw = json.load(open("/app/state.json", encoding="utf-8"))
except Exception as e:                                         # noqa: BLE001
    raw = {}
    print("  state.json 读不到：", e)
for src, data in (("state.json", raw), ("sqlite", state_store.load_state())):
    for cid, g in data.items():
        if not isinstance(g, dict):
            continue
        fs = g.get("fasao") or {}
        hist = fs.get("history") or []
        if not hist:
            continue
        print("  [{}] {}  段数={}  active={}".format(
            src, str(cid)[:8], len(hist), fs.get("active")))
        for i, h in enumerate(hist, 1):
            print("     #{} {} 字{}".format(i, len(h), flag(h)))
            print("        尾：…{}".format(str(h)[-26:].replace("\n", "⏎")))
        la = g.get("last_action") or {}
        if la.get("output"):
            print("     last_action({}) {} 字{}".format(
                la.get("type"), len(la["output"]), flag(la["output"])))
