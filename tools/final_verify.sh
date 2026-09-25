#!/bin/sh
# 收尾核查：中断记录是否如实转成 failed、队列是否干净、有没有异常
echo "=== 1) outbound 各状态 ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3, time
c = sqlite3.connect("file:/app/data/outbox.db?mode=ro", uri=True)
print("  状态分布:", list(c.execute(
    "select status, count(*) from outbound group by status")))
print("  非终态（应当为 0）:", c.execute(
    "select count(*) from outbound where status in ('pending','leased')").fetchone()[0])
print("  曾经中断的那条：")
for r in c.execute("""select created_at, chatroom_id, status, attempt_count, last_error
                      from outbound where last_error is not null
                      order by created_at desc limit 3"""):
    print("   {} {} {} try={}".format(
        time.strftime('%m-%d %H:%M:%S', time.localtime(r[0])), r[1][:8], r[2], r[3]))
    print("     原因: {}".format(r[4]))
PY
echo
echo "=== 2) 启动自证行 ==="
docker logs --tail 60 dzmm-bot 2>&1 | grep -e P5: -e P7: -e P8: -e P11: -e bot路由 -e outbox: | tail -7
echo
echo "=== 3) 停用的 3 群是否还被碰（重启后） ==="
for c in 93cc6c7b 38698306 007bfd75; do
  printf "  %s 出现 %s 次\n" "$c" "$(docker logs --since 2026-09-25T22:58:15 dzmm-bot 2>&1 | grep -c "$c")"
done
echo
echo "=== 4) 异常 / 容器 / 心跳 ==="
echo -n "  异常命中: "
docker logs --since 2026-09-25T22:58:15 dzmm-bot 2>&1 | grep -ciE 'traceback|exception'
docker inspect -f '  StartedAt={{.State.StartedAt}} Restarts={{.RestartCount}} Status={{.State.Status}}' dzmm-bot
docker stats --no-stream --format '  MEM={{.MemUsage}} CPU={{.CPUPerc}}' dzmm-bot
docker logs --tail 40 dzmm-bot 2>&1 | grep -e 心跳 | tail -2
echo
echo "=== 5) 配置目录 ==="
ls -1 /opt/dzmm/config/config_backups/ | tail -3
echo -n "  备份总数: "; ls -1 /opt/dzmm/config/config_backups/config-*.json | wc -l
echo -n "  审计行数: "; wc -l < /opt/dzmm/config/config_audit.log
