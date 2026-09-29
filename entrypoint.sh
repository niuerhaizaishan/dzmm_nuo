#!/bin/bash
# DZMM bot 容器入口：登录态检查 + 定时任务 + bot 主循环（挂了自动重启）
# 新增：bot.paused 哨兵 —— 撞验证码时 bot 写该文件退出，entrypoint 暂停所有自动任务，
#       直到人工过完验证码、删除该哨兵（resume）后才恢复。
set -e
cd /app

echo "=== DZMM bot 容器启动（$(date '+%Y-%m-%d %H:%M:%S')）==="

# 0. P12-AI：清掉挂载进来的 chromium profile 里残留的 Singleton 锁。
#    这些 profile 现在是挂载卷（重建不丢登录态），但上一次容器被重建时留下的
#    SingletonLock 会让新容器里的 chromium 直接拒绝启动（"profile appears to be in use"）。
#    锁只在进程活着时有意义 —— 新容器里那个进程早没了，删掉是对的。
for d in pw_profile pw_profile_greet pw_login pw_captcha; do
  rm -f "/app/$d"/Singleton* 2>/dev/null || true
done

# 1. 首次启动：确保登录态（token JSON 有效则跳过，失效则 headless 登录）
echo "[entrypoint] 检查登录态..."
python headless_relogin.py || echo "[entrypoint] ⚠️ 首次登录未完成（可能需手动过验证码）"

# 2. 定时任务：续期（每 5 分钟）+ 健康巡检（每 5 分钟），后台循环
#    注意：必须加 `|| true` 容错；且检测 bot.paused 哨兵，暂停期间不撞验证码刷屏
echo "[entrypoint] 启动续期 + 健康巡检后台任务..."
( while true; do [ -f /app/bot.paused ] && { sleep 30; continue; }; python renew_token.py || true; sleep 300; done ) &
( while true; do [ -f /app/bot.paused ] && { sleep 30; continue; }; python check_bot_health.py || true; sleep 300; done ) &

# 2.5 心跳（每 2 小时发一次运行状态到飞书；首次启动立即发一条）
( while true; do [ -f /app/bot.paused ] && { sleep 30; continue; }; python heartbeat.py || true; sleep 7200; done ) &

# 2.6 运维后台（P9）：只读、只绑 127.0.0.1，用 SSH 隧道访问。
#     单独进程 -> 它挂了不影响 bot；DZMM_ADMIN=off 可整体关掉。
#     刻意放在主循环之前、且不加 `set -e` 依赖：起不来也只是没后台，不阻断 bot。
if [ "$(echo "${DZMM_ADMIN:-on}" | tr 'A-Z' 'a-z')" != "off" ]; then
  echo "[entrypoint] 启动运维后台（只读；令牌检查由 dashboard 自己做）..."
  ( while true; do
      [ -f /app/bot.paused ] && { sleep 30; continue; }
      python -u admin/dashboard.py || true
      sleep 30
    done ) &
else
  echo "[entrypoint] DZMM_ADMIN=off，跳过运维后台"
fi

# 3. 档案同步定时（问候/记忆汇报已改由 bot 主循环用常驻页面发送，见 bot._scheduled_pushes）
#    原因：原来「到点另起进程 + 冷启浏览器」在平台抖动时会整页加载超时，问候整晚丢失
#    （2026-09-16 认证群 22:00 晚安丢失事故）
echo "[entrypoint] 启动定时同步后台任务..."
LAST_TASK=""
( while true; do
    [ -f /app/bot.paused ] && { sleep 45; continue; }
    HM=$(date +%H:%M)
    if [ "$HM" != "$LAST_TASK" ]; then
      LAST_TASK="$HM"
      case "$HM" in
        "12:05") python -u bot.py --sync || true ;;
        "23:35") python -u bot.py --sync || true ;;
      esac
    fi
    sleep 45
  done ) &

# 4. 主循环：bot 常驻，异常退出后重启（唯一启动者，renew_token/auto_recover 只负责杀）
#    检测 bot.paused 哨兵：存在则暂停启动（等待人工过验证码后 resume）
echo "[entrypoint] 启动 bot 主循环..."
while true; do
  if [ -f /app/bot.paused ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') 检测到 bot.paused，暂停等待人工验证码处理..."
    sleep 10
    continue
  fi
  echo "$(date '+%Y-%m-%d %H:%M:%S') 启动 bot.py"
  python -u bot.py || true
  echo "$(date '+%Y-%m-%d %H:%M:%S') bot.py 退出，10s 后重启"
  sleep 10
done
