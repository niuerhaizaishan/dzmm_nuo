#!/bin/sh
# 核查：开关被写错但**真生效**的那 78 秒（22:30:23 ~ 22:31:41）里，有没有真的发出去东西
echo "=== 窗口内与"发送/推送/筛查/入群"相关的日志行数 ==="
docker logs --since 2026-09-25T22:30:20 --until 2026-09-25T22:31:45 dzmm-bot 2>&1 \
  | grep -c -i -e 发送 -e 推送 -e 筛查 -e 入群 -e 淘汰 -e sent
echo "=== 命中内容（前 10 行） ==="
docker logs --since 2026-09-25T22:30:20 --until 2026-09-25T22:31:45 dzmm-bot 2>&1 \
  | grep -i -e 发送 -e 推送 -e 筛查 -e 入群 -e sent | head -10
echo "=== 窗口内全部日志（看有没有别的动作） ==="
docker logs --since 2026-09-25T22:30:20 --until 2026-09-25T22:31:45 dzmm-bot 2>&1 | wc -l
docker logs --since 2026-09-25T22:30:20 --until 2026-09-25T22:31:45 dzmm-bot 2>&1 | tail -12
echo "=== outbox 各状态条数 ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3
c = sqlite3.connect("file:/app/data/outbox.db?mode=ro", uri=True)
print(list(c.execute("select status, count(*) from outbox group by status")))
print("最后 3 条:", list(c.execute(
    "select id, cid, status, substr(body, 1, 20) from outbox order by rowid desc limit 3")))
PY
