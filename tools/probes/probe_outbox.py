"""看一眼出站队列（只读）：最近几条是谁、什么状态、什么时候。

也可以**取消**一条还没发出去的（`--cancel <id 前缀>`）：运维现场需要"手滑入队的
东西别发出去"。取消 = 把 status 改成 `cancelled`（worker 只取 `pending`，
重试任务只回收 `failed`/`leased`，所以这个状态天然不会被再捡起来），
**保留行**以便留痕。

用法（容器内）：
    python /app/probe_outbox.py [条数]
    python /app/probe_outbox.py --cancel 0b36e613
"""
import sqlite3
import sys
import time
from pathlib import Path

DB = "/app/data/outbox.db"


def _cancel(prefix: str):
    c = sqlite3.connect(DB)
    rows = list(c.execute(
        "SELECT id, status, substr(text,1,60) FROM outbound WHERE id LIKE ?",
        (prefix + "%",)))
    if not rows:
        print("❌ 没有 id 以 {} 开头的消息".format(prefix))
        return 2
    for oid, status, txt in rows:
        if status != "pending":
            print("⚠️ {} 当前状态是 {}（只有 pending 能取消）".format(oid[:8], status))
            continue
        c.execute("UPDATE outbound SET status='cancelled',"
                  " last_error='ops cancel' WHERE id=?", (oid,))
        c.commit()
        print("✅ 已取消 {}（{!r}）".format(oid[:8], txt))
    return 0


def main():
    if "--cancel" in sys.argv:
        i = sys.argv.index("--cancel")
        return _cancel(sys.argv[i + 1] if len(sys.argv) > i + 1 else "")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    p = Path(DB)
    if not p.exists():
        print("❌ 没有 {}（DZMM_OUTBOX 关着？）".format(DB))
        return 2
    c = sqlite3.connect(DB)
    cols = [r[1] for r in c.execute("PRAGMA table_info(outbound)")]
    print("列 = {}".format(cols))
    rows = list(c.execute(
        "SELECT id, status, chatroom_id, substr(text,1,50), created_at, sent_at,"
        " attempt_count, platform_sent_id FROM outbound"
        " ORDER BY created_at DESC LIMIT ?", (n,)))
    print("现在 = {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    for r in rows:
        oid, status, cid, txt, created, sent, att, pid = r
        print("\n{} {}  cid={}  attempt={}".format(
            oid[:8], status, (cid or "")[:8], att))
        print("   建 {} | 发 {}".format(
            time.strftime("%H:%M:%S", time.localtime(created)) if created else "-",
            time.strftime("%H:%M:%S", time.localtime(sent)) if sent else "-"))
        print("   platform_sent_id={}".format((pid or "-")[:16]))
        print("   文 = {}".format((txt or "").replace("\n", " ⏎ ")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
