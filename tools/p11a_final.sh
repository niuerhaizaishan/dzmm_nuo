#!/bin/sh
# P11 A 期上线后的最终健康检查
echo "=== 容器 ==="
docker ps --format '  {{.Names}}  {{.Status}}  {{.Ports}}'
docker inspect -f '  restarts={{.RestartCount}}' dzmm-bot
docker stats --no-stream dzmm-bot --format '  CPU={{.CPUPerc}} MEM={{.MemUsage}}'

echo
echo "=== config.json 挂载 ==="
docker exec dzmm-bot sh -c 'ls -l /app/config.json; echo "--- 容器内读到的 source ---"; cd /app && python -c "
import sys; sys.path.insert(0,\"/app\")
from runtime import config_store as cs
p = cs.config_path()
print(\"  path      =\", p)
print(\"  是文件吗   =\", p.is_file())
print(\"  是目录吗   =\", p.is_dir())
"'

echo
echo "=== 异常扫描（最近 200 行） ==="
n=$(docker logs --tail 200 dzmm-bot 2>&1 | grep -icE 'traceback|exception' || true)
echo "  命中 $n 条（0 = 干净）"

echo
echo "=== 机器人最近活动 ==="
tail -8 /opt/dzmm/bot.log | sed 's/^/  /'

echo
echo "=== 出站队列 ==="
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
