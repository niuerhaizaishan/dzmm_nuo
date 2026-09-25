#!/bin/sh
# 看 21:23:26 那条 @小小糯-bot 之后的完整处理链路
echo "=== bot.log 里 21:23 前后的上下文 ==="
grep -n -A3 -B1 '21:23:26' /opt/dzmm/bot.log 2>/dev/null | tail -20 | sed 's/^/  /'

echo
echo "=== 直接的顺序片段：21:23:2x ~ 21:23:5x ==="
awk '/21:23:2[0-9]|21:23:3[0-9]|21:23:4[0-9]|21:23:5[0-9]/' /opt/dzmm/bot.log 2>/dev/null | tail -25 | sed 's/^/  /'

echo
echo "=== 出站队列里最近的记录（时间倒序） ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3
from datetime import datetime
c = sqlite3.connect("file:/app/data/outbox.db?mode=ro", uri=True)
try:
    rows = c.execute(
        "select status, attempt_count, substr(text,1,34), "
        "coalesce(last_error,''), created_at, sent_at "
        "from outbound order by rowid desc limit 5").fetchall()
    for st, ac, tx, err, ct, sat in rows:
        print("  {} try={} {} | {!r}".format(
            st, ac, datetime.fromtimestamp(ct).strftime("%H:%M:%S"), tx))
        if err:
            print("      last_error:", err[:70])
finally:
    c.close()
PY

echo
echo "=== 最近 5 分钟进程活动（确认主循环还活着） ==="
tail -6 /opt/dzmm/bot.log | sed 's/^/  /'
