#!/bin/sh
# P8 第 3 步部署后生产自检（在宿主机上跑，只读）
echo "=== container ==="
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker inspect -f 'restarts={{.RestartCount}} started={{.State.StartedAt}}' dzmm-bot
docker stats --no-stream dzmm-bot --format 'CPU={{.CPUPerc}} MEM={{.MemUsage}} ({{.MemPerc}})'

echo
echo "=== 启动自证行（P3/P4/P5/P7/P8） ==="
docker logs --tail 60 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:|心跳|sender ready|start  dry'

echo
echo "=== 异常扫描（最近 300 行） ==="
docker logs --tail 300 dzmm-bot 2>&1 | grep -iE 'traceback|exception|错误|失败' | head -20
echo "(以上为空 = 无异常)"

echo
echo "=== prompts 挂载 ==="
ls -1 /opt/dzmm/prompts | wc -l
docker exec dzmm-bot sh -c 'ls -1 /app/prompts | wc -l'

echo
echo "=== 出站队列 ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3

c = sqlite3.connect("/app/data/outbox.db")
try:
    rows = c.execute("select status, count(*) from outbound group by status").fetchall()
    print("status:", dict(rows))
    print("attempt_count>1:", c.execute(
        "select count(*) from outbound where attempt_count > 1").fetchone()[0])
    print("最近 3 条:", c.execute(
        "select status, attempt_count, substr(text,1,24) from outbound "
        "order by rowid desc limit 3").fetchall())
finally:
    c.close()
PY
