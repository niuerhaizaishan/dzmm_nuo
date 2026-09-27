#!/bin/sh
# 验证若瑾的 Webhook Secret 被我们的接收端认下来（用不存在的群 id，零群内副作用）
SECRET=550a4f1e-f786-43ff-8917-de980c45959d
URL=https://www.nuonuo.pw/bot/webhook
FAKE=00000000-0000-0000-0000-000000000000

echo "--- 1) 正确 Secret + 假群 id（期望 200，且日志出现"收到 [00000000]"）---"
curl -s -o /tmp/rj1.txt -w 'http=%{http_code}\n' -X POST "$URL" \
  -H 'Content-Type: application/json' \
  -H "X-Telegram-Bot-Api-Secret-Token: $SECRET" \
  -d "{\"update_id\":1,\"message\":{\"message_id\":\"wh-ruojin-smoke-1\",\"chat\":{\"id\":\"$FAKE\",\"title\":\"smoke\"},\"from\":{\"id\":\"wh-smoke\",\"first_name\":\"wh-smoke\"},\"text\":\"ruojin-secret-smoke\"}}"
cat /tmp/rj1.txt; echo

echo "--- 2) 错误 Secret（期望 401 且不入队）---"
curl -s -o /tmp/rj2.txt -w 'http=%{http_code}\n' -X POST "$URL" \
  -H 'Content-Type: application/json' \
  -H 'X-Telegram-Bot-Api-Secret-Token: 00000000-0000-0000-0000-000000000000' \
  -d "{\"update_id\":2,\"message\":{\"message_id\":\"wh-bad-1\",\"chat\":{\"id\":\"$FAKE\"},\"from\":{\"id\":\"x\",\"first_name\":\"x\"},\"text\":\"bad\"}}"
cat /tmp/rj2.txt; echo

echo "--- 3) 最近 6 行 Webhook 日志 ---"
sleep 2
grep -a 'P12-J: webhook 收到' /opt/dzmm/bot.log | tail -6
echo "--- 4) 真实群（f4ec4c21）的 webhook 记录 ---"
grep -a 'webhook 收到 \[f4ec4c21' /opt/dzmm/bot.log | tail -3 || echo "（还没有真实群消息走 webhook）"
