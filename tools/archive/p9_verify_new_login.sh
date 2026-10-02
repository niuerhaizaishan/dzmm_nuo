#!/bin/sh
# 校验新账号密码生效、旧账号失效
NEW_U=nuonuo
NEW_P=yx83300049
OLD_U=admin
OLD_P=BKiGBzzWDETTLk
B=http://127.0.0.1:8090
J=/tmp/p9_new.txt
rm -f $J

echo "=== 1) 旧密码应当失效 ==="
echo -n "  旧账密 -> "
curl -s -o /tmp/o1.html -w '%{http_code}' -d "username=$OLD_U&password=$OLD_P" $B/login
grep -q "账号或密码不对" /tmp/o1.html && echo "（401，提示正确）" || echo "（!! 没被拒）"

echo
echo "=== 2) 新密码应当可用 ==="
echo -n "  新账密 -> "
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' -c $J -d "username=$NEW_U&password=$NEW_P" $B/login
echo -n "  会话 cookie: "
grep -o 'dzmm_admin_sid' $J | head -1
echo -n "  带 cookie 取页面 -> "
curl -s -b $J -o /tmp/o2.html -w '%{http_code}' $B/
grep -q "启动自证行" /tmp/o2.html && echo "（拿到面板）" || echo "（!! 没拿到）"
echo -n "  带 cookie 取 JSON -> "
curl -s -b $J -o /tmp/o3.json -w '%{http_code}' $B/api/summary
python3 -c "import json;d=json.load(open('/tmp/o3.json'));print('  ->',len(d['sections']),'个板块, bot_running=',d['sections']['process']['data']['bot_running'])" 2>/dev/null || echo

echo
echo "=== 3) 未登录仍然只给登录页 ==="
curl -s -o /tmp/o4.html $B/
grep -q "请登录" /tmp/o4.html && echo "  OK（登录页）" || echo "  !! 泄漏"
for leak in 启动自证行 出站队列 群状态; do
  grep -q "$leak" /tmp/o4.html && echo "  !! 泄漏[$leak]"
done
echo "  （没有 '!!' 就是干净）"

echo
echo "=== 4) 登出 ==="
curl -s -o /dev/null -b $J -c $J $B/logout
curl -s -b $J -o /tmp/o5.html $B/api/summary
grep -q "请登录" /tmp/o5.html && echo "  登出后已失效" || echo "  !! 仍有效"

echo
echo "=== 5) 容器与自证行 ==="
docker inspect -f '  restarts={{.RestartCount}}' dzmm-bot
docker logs --tail 25 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:' | sed 's/^/  /'
echo -n "  异常扫描命中: "
docker logs --tail 200 dzmm-bot 2>&1 | grep -icE 'traceback|exception'
echo
echo "=== 6) 登录日志（确认旧账号那次是"失败"）==="
docker logs dzmm-bot 2>&1 | grep '\[admin\] 登录' | tail -4 | sed 's/^/  /'
rm -f $J /tmp/o1.html /tmp/o2.html /tmp/o3.json /tmp/o4.html /tmp/o5.html
