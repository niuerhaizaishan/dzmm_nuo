#!/bin/sh
# 验证 Webhook 接收端：日志 / 端口映射 / 本机三连测（无 Secret 必须 401，有 Secret 必须 200）
cd /opt/dzmm
echo "=== 启动日志（P12-J）==="
docker compose logs --since=5m dzmm-bot 2>&1 | grep -E 'P12-J|P12-I: 按群并行|start .*groups=' | tail -6
echo
echo "=== 端口映射 ==="
docker port dzmm-bot 2>&1 | head -4
echo
SECRET=$(grep -E '^DZMM_BOT_SECRET_ROSE=' .env | cut -d= -f2-)
echo "=== 本机直连接收端 :8091 ==="
curl -s -o /dev/null -w "  GET  /bot/webhook            -> HTTP %{http_code}\n" --max-time 6 http://127.0.0.1:8091/bot/webhook
curl -s -o /dev/null -w "  POST 无 Secret               -> HTTP %{http_code}\n" --max-time 6 \
  -X POST -H 'Content-Type: application/json' -d '{"message":{"chat":{"id":"x"},"from":{"id":"y"},"text":"z"}}' \
  http://127.0.0.1:8091/bot/webhook
curl -s -o /dev/null -w "  POST 错 Secret               -> HTTP %{http_code}\n" --max-time 6 \
  -X POST -H "X-Telegram-Bot-Api-Secret-Token: wrong-secret" -H 'Content-Type: application/json' \
  -d '{"message":{"chat":{"id":"x"},"from":{"id":"y"},"text":"z"}}' \
  http://127.0.0.1:8091/bot/webhook
echo "  （下面这条用真 Secret + 玫瑰房 cid + 一句不会触发回复的话：应当 200 且群里没有任何动静）"
curl -s -w "\n  HTTP %{http_code}\n" --max-time 8 \
  -X POST -H "X-Telegram-Bot-Api-Secret-Token: $SECRET" -H 'Content-Type: application/json' \
  -d '{"update_id":9001,"message":{"message_id":"wh-smoke-1","chat":{"id":"4a56be71-9ca6-4785-b45f-18725d187f1a"},"from":{"id":"wh-smoke-user","first_name":"wh"},"date":1758900000,"text":"。webhook自测，无需回复"}}' \
  http://127.0.0.1:8091/bot/webhook
echo
sleep 4
echo "=== 接收端日志（应看到 原始 payload / webhook 收到）==="
tail -12 /opt/dzmm/bot.log | grep -E 'P12-J|webhook|payload' || tail -6 /opt/dzmm/bot.log
