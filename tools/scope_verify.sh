#!/bin/sh
# 收窄范围之后：确认真的不再碰那 3 个群，且没有异常/积压
echo "=== 1) 重启后的日志里，有没有出现那 3 个被停用的群 ==="
for c in 93cc6c7b 38698306 007bfd75; do
  n=$(docker logs --since 2026-09-25T22:53:38 dzmm-bot 2>&1 | grep -c "$c")
  echo "  $c 出现 $n 次"
done
echo "  （0 次 = 主循环确实没在读/回这几个群）"
echo
echo "=== 2) 玫瑰群（保留）在重启后有没有被处理 ==="
n=$(docker logs --since 2026-09-25T22:53:38 dzmm-bot 2>&1 | grep -c "4a56be71")
echo "  4a56be71 出现 $n 次"
docker logs --since 2026-09-25T22:53:38 dzmm-bot 2>&1 | grep -e 4a56be71 | head -5
echo
echo "=== 3) 重启后有没有新的出站（含玫瑰群） ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3, time
c = sqlite3.connect("file:/app/data/outbox.db?mode=ro", uri=True)
rows = list(c.execute("""select created_at, chatroom_id, status, delivery_kind,
                                substr(replace(coalesce(text,''),char(10),' '),1,28)
                         from outbound where created_at > 1758800000
                         order by created_at desc limit 8"""))
print("  最近 8 条：")
for r in rows:
    print("   {} {} {} try | {}".format(
        time.strftime('%m-%d %H:%M:%S', time.localtime(r[0])), r[1][:8], r[2], r[4]))
print("  未送达的（非 sent）:")
for r in c.execute("select id, created_at, chatroom_id, status, attempt_count,"
                   " coalesce(last_error,'') from outbound where status != 'sent'"):
    print("   {} {} {} try={} err={}".format(
        r[1] and time.strftime('%m-%d %H:%M:%S', time.localtime(r[1])),
        r[2][:8], r[3], r[4], r[5][:40]))
PY
echo
echo "=== 4) 状态数据没被删（停用不等于清档） ==="
docker logs --tail 80 dzmm-bot 2>&1 | grep -e P7:
echo
echo "=== 5) 异常与容器状态 ==="
echo -n "  异常命中: "
docker logs --since 2026-09-25T22:53:38 dzmm-bot 2>&1 | grep -ciE 'traceback|exception'
docker inspect -f '  StartedAt={{.State.StartedAt}} Restarts={{.RestartCount}} Status={{.State.Status}}' dzmm-bot
docker stats --no-stream --format '  MEM={{.MemUsage}} CPU={{.CPUPerc}}' dzmm-bot
