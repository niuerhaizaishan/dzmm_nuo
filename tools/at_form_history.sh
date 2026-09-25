#!/bin/sh
# 从历史日志里找"@ 机器人"的实证：@ 消息里写的名字到底是哪一个
echo "=== 1) 日志里所有「@消息」的形式统计（含新名字） ==="
grep -o '@消息: @[^ ]*' /opt/dzmm/bot.log 2>/dev/null | sort | uniq -c | sort -rn | head -15

echo
echo "=== 2) 有没有人 @ 过新名字 小小糯-bot ==="
n=$(grep -c '@小小糯-bot' /opt/dzmm/bot.log 2>/dev/null || true)
echo "  命中 $n 条"
grep '@小小糯-bot' /opt/dzmm/bot.log 2>/dev/null | tail -5 | sed 's/^/  /'

echo
echo "=== 3) 最近 20 条 @消息（看文本形式） ==="
grep '@消息:' /opt/dzmm/bot.log 2>/dev/null | tail -20 | sed 's/^/  /'

echo
echo "=== 4) 出站队列现状 ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3
c = sqlite3.connect("file:/app/data/outbox.db?mode=ro", uri=True)
try:
    print("  status:", dict(c.execute(
        "select status, count(*) from outbound group by status").fetchall()))
    print("  attempt_count>1:", c.execute(
        "select count(*) from outbound where attempt_count > 1").fetchone()[0])
finally:
    c.close()
PY
