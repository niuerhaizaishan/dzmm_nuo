# -*- coding: utf-8 -*-
"""P12-BR ⑦：结案出站队列里的**假失败**（人工核对后标成 sent）。

为什么需要：进程重启会打断正在发的消息 —— `OutboxSender` 按设计**不自动重发**
（未 ACK 不重发，避免重复打扰），于是那几条永远挂在 `failed` 上。
2026-09-30 16:55 那条（茶水间 265「哥哥，一半就一半…」）事后**已核对确实投递了**，
却一直算在失败数里，既污染 `📤 出站队列 pending/x failed/y` 这一行，
也让"失败条数增长"的巡检口径失去意义。

只做一件事：把某条记录的状态改成 `sent`，并在 `last_error` 里留下**人工结案的理由**
（审计可查）。**不删记录、不改正文、不重发。**

用法（容器内）：
    python tools/resolve_outbox.py                       # 只看 failed 列表
    python tools/resolve_outbox.py --id <前 8 位> --reason "人工核对：实际已投递"
    python tools/resolve_outbox.py --before 2026-09-28 --reason "已过期结案"
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/app")

DB = Path("/app/data/outbox.db")


def _rows(con, where, args=()):
    return con.execute(
        "select id, created_at, chatroom_id, substr(text,1,40), status, "
        "coalesce(last_error,'') from outbound " + where +
        " order by created_at", args).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="记录 id（可只给前 8 位）")
    ap.add_argument("--before", help="把 created_at 早于这个日期(YYYY-MM-DD)的 failed 全部结案")
    ap.add_argument("--reason", default="人工核对：实际已投递（原被误标 failed）")
    ap.add_argument("--expire", action="store_true",
                    help="结案成**过期终态**（不是标 sent）：用于'确认没发出去/不打算补发'的陈旧记录")
    ap.add_argument("--apply", action="store_true", help="不传就只看")
    a = ap.parse_args()

    con = sqlite3.connect(str(DB))
    try:
        print("== 当前 failed ==")
        for r in _rows(con, "where status='failed'"):
            print("  {}  {}  {}  {!r}".format(
                r[0][:8], datetime.fromtimestamp(r[1]).strftime("%m-%d %H:%M"),
                r[2][:8], r[3]))
            print("      原因：{}".format(r[5][:70]))
        rows = []
        if a.id:
            rows = _rows(con, "where status='failed' and id like ?", (a.id + "%",))
        elif a.before:
            cut = time.mktime(datetime.strptime(a.before, "%Y-%m-%d").timetuple())
            rows = _rows(con, "where status='failed' and created_at < ?", (cut,))
        if not rows:
            print("\n（没有要改的行；要改就加 --id 或 --before）")
            return 0
        print("\n== 将结案 {} 条 ==".format(len(rows)))
        for r in rows:
            print("  {}  {!r}".format(r[0][:8], r[3]))
        if not a.apply:
            print("dry-run：没有写库（要真的写加 --apply）")
            return 0
        note = "[人工结案 {}] {}".format(
            datetime.now().strftime("%m-%d %H:%M"), a.reason)
        new_status = "expired" if a.expire else "sent"   # P12-BR ⑦：expired = 终态、不计失败
        n = 0
        for r in rows:
            con.execute("update outbound set status=?, sent_at=?, "
                        "last_error=coalesce(last_error,'') || ' | ' || ? where id=?",
                        (new_status, time.time() if new_status == "sent" else None,
                         note, r[0]))
            n += 1
        con.commit()
        left = con.execute("select count(*) from outbound where status='failed'").fetchone()[0]
        print("\n已结案 {} 条；剩余 failed={}".format(n, left))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
