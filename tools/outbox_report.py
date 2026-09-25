"""Outbox 状态报告（在容器内运行）。

用途：P3/P4 上线后观察出站队列在**真实流量**下的行为 —— 有没有失败堆积、
有没有疑似重复发送、状态机是否正常流转。

用法：
    docker cp tools/outbox_report.py dzmm-bot:/tmp/
    docker exec dzmm-bot python /tmp/outbox_report.py
"""
import datetime
import os
import sqlite3
import sys

db = os.environ.get("DZMM_OUTBOX_DB") or "/app/data/outbox.db"
if not os.path.exists(db):
    print("outbox.db 不存在：{}".format(db))
    raise SystemExit(1)

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
print("db:", db)
print()

print("按状态:")
total = 0
for r in conn.execute("SELECT status, COUNT(*) n FROM outbound"
                      " GROUP BY status ORDER BY n DESC"):
    print("   {:<9} {:>5}".format(r["status"], r["n"]))
    total += r["n"]
print("   {:<9} {:>5}".format("合计", total))
print()

print("最近 10 条:")
for r in conn.execute("SELECT created_at, chatroom_id, status, attempt_count, text"
                      " FROM outbound ORDER BY created_at DESC LIMIT 10"):
    ts = datetime.datetime.fromtimestamp(r["created_at"]).strftime("%m-%d %H:%M:%S")
    txt = (r["text"] or "").replace("\n", " ")[:34]
    print("   {} cid={} {:<8} try={} {}".format(
        ts, (r["chatroom_id"] or "")[:8], r["status"], r["attempt_count"], txt))
print()

print("失败样本（若有）:")
failed = conn.execute("SELECT last_error, COUNT(*) n FROM outbound WHERE status='failed'"
                      " GROUP BY last_error ORDER BY n DESC LIMIT 5").fetchall()
if not failed:
    print("   无")
for r in failed:
    print("   n={} {}".format(r["n"], (r["last_error"] or "")[:70]))
print()

print("疑似重复（同 cid + 完全相同文本 >1 次，启发式，需人工判断）:")
dups = conn.execute("""SELECT chatroom_id, text, COUNT(*) n FROM outbound
                       GROUP BY chatroom_id, text HAVING n > 1
                       ORDER BY n DESC LIMIT 5""").fetchall()
if not dups:
    print("   无")
for r in dups:
    print("   n={} cid={} {}".format(r["n"], (r["chatroom_id"] or "")[:8],
                                     (r["text"] or "").replace("\n", " ")[:34]))
print()

print("租约异常（leased 且已过期）：")
now = datetime.datetime.now().timestamp()
stuck = conn.execute("SELECT COUNT(*) n FROM outbound WHERE status='leased'"
                     " AND lease_expires_at < ?", (now,)).fetchone()
print("   {}".format(stuck["n"]))
