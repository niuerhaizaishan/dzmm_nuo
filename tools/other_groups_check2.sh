#!/bin/sh
# 只读核查（第二轮）：非认证群是否仍在正常走账号通道
echo "=== A) outbound 全景：每个群的条数 / 成功数 / 首次与最后一次 ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3, time
c = sqlite3.connect("file:/app/data/outbox.db?mode=ro", uri=True)
alias = {
    'bd7fe6a4-0065-4551-ad3d-94116dba8f9c': '认证妹子群(有Bot)',
    '93cc6c7b-ba9e-48f3-a106-3074483aebbd': '裳姐姐和她的骚贱小母狗',
    '38698306-4252-438a-aa56-7dbab89250eb': '被dzmm团本折磨的三人',
    '007bfd75-2efb-491c-8233-20046af69b24': '机电驱动与智能算法研讨群',
    '4a56be71-9ca6-4785-b45f-18725d187f1a': 'Rose（505）玫瑰房',
}
def t(x):
    try:
        return time.strftime('%m-%d %H:%M:%S', time.localtime(float(x)))
    except Exception:
        return str(x)
print("  {:<26s} {:>4s} {:>5s} {:>5s}  {:<14s} {:<14s}".format(
    '群', '总', '已发', '重试', '第一条', '最后一条'))
for cid, n, sent, retry, mn, mx in c.execute("""
        select chatroom_id, count(*),
               sum(case when status='sent' then 1 else 0 end),
               sum(case when attempt_count > 1 then 1 else 0 end),
               min(created_at), max(created_at)
        from outbound group by chatroom_id order by max(created_at)"""):
    print("  {:<26s} {:>4d} {:>5d} {:>5d}  {:<14s} {:<14s}".format(
        alias.get(cid, cid[:8]), n, sent, retry, t(mn), t(mx)))
print()
print("=== B) 最近 14 条（时间 / 群 / 状态 / 重试次数 / 正文开头） ===")
for r in c.execute("""select created_at, chatroom_id, status, attempt_count,
                             coalesce(sent_at,''), substr(replace(coalesce(text,''),
                             char(10),' '),1,24)
                      from outbound order by created_at desc limit 14"""):
    print("  {} {:<24s} {:<7s} try={} sent={} | {}".format(
        t(r[0]), alias.get(r[1], r[1][:8]), r[2], r[3], t(r[4]) if r[4] else '-', r[5]))
print()
print("=== C) 那条 leased（可能卡住没发出去） ===")
for r in c.execute("""select id, created_at, chatroom_id, status, attempt_count,
                             lease_worker_id, lease_expires_at, last_error,
                             substr(replace(coalesce(text,''),char(10),' '),1,40)
                      from outbound where status != 'sent'"""):
    print("  id={} 建于 {} 群={} 状态={} try={}".format(
        r[0], t(r[1]), alias.get(r[2], r[2][:8]), r[3], r[4]))
    print("     租约 worker={} 到期={} 上次错误={}".format(r[5], t(r[6]), r[7]))
    print("     正文: {}".format(r[8]))
    print("     租约是否已过期:", "已过期（等待回收重试）"
          if (r[6] or 0) < time.time() else "未过期（正常在飞）")
print()
print("=== D) 撤回/私聊/图片这类非 send 路径有没有在用（看内容类型） ===")
print("  content_type:", list(c.execute(
    "select content_type, count(*) from outbound group by content_type")))
print("  recall_after_seconds 非空:", c.execute(
    "select count(*) from outbound where coalesce(recall_after_seconds,0) > 0").fetchone()[0])
PY
echo
echo "=== E) 日志里最近的路由事件（account 事件按设计不记，只会看到 bot_*） ==="
docker logs --tail 1500 dzmm-bot 2>&1 | grep -e 'bot路由:' | tail -6
echo
echo "=== F) 账号侧：socket.io 连接与登录态自证 ==="
docker logs --tail 600 dzmm-bot 2>&1 | grep -e '\[sio\]' -e '已登录' -e '登录态' | tail -8
