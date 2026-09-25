#!/bin/sh
# 只读核查：非认证群（其余 4 个群）现在是不是还照旧走"账号 小小糯～"收发
ALIAS_bd=认证妹子群
echo "=== 1) 启动自证行：Bot 路由到底覆盖了哪些群 ==="
docker logs --tail 400 dzmm-bot 2>&1 | grep -e 'bot路由' | tail -3
echo
echo "=== 2) 账号登录态 / 通道自证 ==="
docker logs --tail 400 dzmm-bot 2>&1 | grep -e '登录' -e 'cookie' -e 'Cookie' -e 'sio' -e 'token' -e 'P3' -e 'P4' -e 'P6' | tail -12
echo
echo "=== 3) outbound 表：按 群 × 通道 × 状态 统计（delivery_kind 就是走的哪条通道） ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3
c = sqlite3.connect("file:/app/data/outbox.db?mode=ro", uri=True)
alias = {
    'bd7fe6a4-0065-4551-ad3d-94116dba8f9c': '认证妹子群(有Bot)',
    '93cc6c7b-ba9e-48f3-a106-3074483aebbd': '裳姐姐和她的骚贱小母狗',
    '38698306-4252-438a-aa56-7dbab89250eb': '被dzmm团本折磨的三人',
    '007bfd75-2efb-491c-8233-20046af69b24': '机电驱动与智能算法研讨群',
    '4a56be71-9ca6-4785-b45f-18725d187f1a': 'Rose（505）玫瑰房',
}
print("  delivery_kind 全部取值:", [r[0] for r in c.execute(
    "select distinct delivery_kind from outbound")])
rows = list(c.execute("""
    select chatroom_id, delivery_kind, status, count(*)
    from outbound group by chatroom_id, delivery_kind, status
    order by chatroom_id, delivery_kind"""))
print("  {:<26s} {:<10s} {:<8s} {}".format('群', '通道', '状态', '条数'))
for cid, kind, st, n in rows:
    print("  {:<26s} {:<10s} {:<8s} {}".format(
        alias.get(cid, cid[:8]), kind, st, n))
print()
print("=== 4) 最近 10 条出站（看非认证群有没有真的发出去） ===")
for r in c.execute("""
    select created_at, chatroom_id, delivery_kind, status, attempt_count,
           coalesce(sent_at, ''), substr(replace(text, char(10), ' '), 1, 26)
    from outbound order by created_at desc limit 10"""):
    print("  {} {:<24s} {:<9s} {:<7s} try={} {} | {}".format(
        r[0][:19], alias.get(r[1], r[1][:8]), r[2] or '-', r[3] or '-',
        r[4], (r[5] or '')[:19], r[6]))
print()
print("=== 5) 路由计数器（进程内统计，重启会归零） ===")
PY
docker logs --tail 400 dzmm-bot 2>&1 | grep -e 'bot_ok' -e 'account=' -e '路由统计' -e '发送' | tail -8
echo
echo "=== 6) 容器运行时长与心跳 ==="
docker inspect -f '  StartedAt={{.State.StartedAt}} Restarts={{.RestartCount}} Status={{.State.Status}}' dzmm-bot
docker logs --tail 60 dzmm-bot 2>&1 | grep -e 心跳 | tail -3
