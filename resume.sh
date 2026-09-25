#!/bin/bash
# 人工过完验证码后恢复 bot：删除 bot.paused 哨兵，entrypoint 主循环会自动拉起 bot
set -e
docker exec dzmm-bot rm -f /app/bot.paused
echo "OK: bot.paused 已删除，entrypoint 将在约 10 秒内自动拉起 bot"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep dzmm-bot || true
