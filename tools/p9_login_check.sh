#!/bin/sh
# P9 登录版自检：登录页 / 未登录拿不到数据 / 登录成功 / 锁定 / 只读
#
# ⚠️ 读 .env 的每一处都要 `tr -d '\r'`：
#   `docker compose` 读 .env 时会自动去掉值尾部的 CR，shell 的 cut **不会**。
#   不剥掉的话脚本送去的是 "admin\r"，而容器里是 "admin" ——
#   表现是"密码明明一样却 401"（这个坑真踩过，见 docs/小小糯总方案.md）。
PW=$(grep -E '^DZMM_ADMIN_PASSWORD=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
USER=$(grep -E '^DZMM_ADMIN_USER=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
USER=${USER:-admin}
B=http://127.0.0.1:8090
J=/tmp/p9_cookies.txt
rm -f $J

echo "=== 1) 端口与启动日志 ==="
docker port dzmm-bot | sed 's/^/  /'
docker logs --tail 30 dzmm-bot 2>&1 | grep -E '\[admin\]' | sed 's/^/  /'

echo
echo "=== 2) 未登录：应该只看到登录页，拿不到任何数据 ==="
echo -n "  GET /            -> "
curl -s -o /tmp/p9_home.html -w '%{http_code}' $B/
grep -q "请登录" /tmp/p9_home.html && echo -n "（是登录页）" || echo -n "（!! 不是登录页）"
for leak in 启动自证行 出站队列 群状态; do
  if grep -q "$leak" /tmp/p9_home.html; then echo -n " 泄漏[$leak]"; fi
done
echo
echo -n "  GET /api/summary -> "
curl -s -o /tmp/p9_api.html -w '%{http_code}' $B/api/summary
grep -q "sections" /tmp/p9_api.html && echo "（!! 泄漏了 JSON）" || echo "（没有 JSON）"
echo -n "  GET /healthz     -> "
curl -s -o /tmp/p9_hz.html -w '%{http_code}' $B/healthz
grep -q "请登录" /tmp/p9_hz.html && echo "（登录页）" || cat /tmp/p9_hz.html

echo
echo "=== 3) 错误密码 ==="
echo -n "  错误密码 -> "
curl -s -o /tmp/p9_bad.html -w '%{http_code}' -c $J -d "username=$USER&password=definitely-wrong" $B/login
grep -q "账号或密码不对" /tmp/p9_bad.html && echo "（提示正确）" || echo "（!! 提示不对）"

echo
echo "=== 4) 正确密码登录 ==="
echo -n "  登录 -> "
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}' -c $J -d "username=$USER&password=$PW" $B/login
echo
echo -n "  cookie: "
grep -o 'dzmm_admin_sid.*' $J | cut -c1-40
echo -n "  带 cookie 取页面 -> "
curl -s -b $J -o /tmp/p9_ok.html -w '%{http_code}' $B/
grep -q "启动自证行" /tmp/p9_ok.html && echo "（拿到真数据了）" || echo "（!! 没拿到数据）"
echo -n "  带 cookie 取 JSON -> "
curl -s -b $J -o /tmp/p9_ok.json -w '%{http_code}\n' $B/api/summary
python3 -c "import json;d=json.load(open('/tmp/p9_ok.json'));print('    板块数', len(d['sections']), '| bot_running', d['sections']['process']['data']['bot_running'])" 2>/dev/null

echo
echo "=== 5) 登出后立即失效 ==="
curl -s -o /dev/null -b $J -c $J $B/logout
echo -n "  登出后再取 -> "
curl -s -b $J -o /tmp/p9_out.html -w '%{http_code}' $B/api/summary
grep -q "请登录" /tmp/p9_out.html && echo "（已失效）" || echo "（!! 仍然有效）"

echo
echo "=== 6) 只读性 ==="
echo -n "  POST /api/summary -> "
curl -s -o /dev/null -w '%{http_code}\n' -X POST $B/api/summary

echo
echo "=== 7) 容器与自证行 ==="
docker inspect -f '  restarts={{.RestartCount}}' dzmm-bot
docker logs --tail 30 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:' | sed 's/^/  /'
echo -n "  异常扫描命中: "
docker logs --tail 200 dzmm-bot 2>&1 | grep -icE 'traceback|exception'
rm -f $J /tmp/p9_home.html /tmp/p9_api.html /tmp/p9_hz.html /tmp/p9_bad.html /tmp/p9_ok.html /tmp/p9_ok.json /tmp/p9_out.html
