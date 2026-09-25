#!/bin/sh
# P9 公网发布后的自检：端口绑定 / 令牌闸门 / 拒绝启动闸门
echo "=== 1) 端口绑定（现在应是 0.0.0.0，不再限制在回环） ==="
docker port dzmm-bot
ss -lntp 2>/dev/null | grep 8090 | sed 's/^/  /'

echo
echo "=== 2) entrypoint / dashboard 启动日志 ==="
docker logs --tail 30 dzmm-bot 2>&1 | grep -E 'admin|运维后台|对外' | sed 's/^/  /'

echo
echo "=== 3) 令牌闸门（服务端本机测） ==="
echo -n "  不带令牌      -> "; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/api/summary
echo -n "  错误令牌      -> "; curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8090/api/summary?token=wrong'
echo -n "  healthz 不带  -> "; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/healthz

TOKEN=$(grep -E '^DZMM_ADMIN_TOKEN=' /opt/dzmm/.env | cut -d= -f2-)
if [ -z "$TOKEN" ]; then
  echo "  !! .env 里没有 DZMM_ADMIN_TOKEN"
else
  echo -n "  正确令牌      -> "; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8090/api/summary?token=$TOKEN"
  echo -n "  正确令牌(页面)-> "; curl -s -o /dev/null -w '%{http_code} %{size_download} bytes\n' "http://127.0.0.1:8090/?token=$TOKEN"
  echo -n "  是否种 cookie -> "; curl -s -D - -o /dev/null "http://127.0.0.1:8090/api/summary?token=$TOKEN" | grep -i '^set-cookie' | sed 's/dzmm_admin_token=[^;]*/dzmm_admin_token=<已隐藏>/' || echo "（没有 Set-Cookie）"
  echo -n "  cookie 单独用 -> "; curl -s -o /dev/null -w '%{http_code}\n' -H "Cookie: dzmm_admin_token=$TOKEN" http://127.0.0.1:8090/api/summary
  echo -n "  令牌是否正确  -> "; [ "$(curl -s "http://127.0.0.1:8090/healthz?token=$TOKEN")" = "ok" ] && echo "是" || echo "否"
fi

echo
echo "=== 4) 只读性仍然成立 ==="
echo -n "  POST -> "; curl -s -o /dev/null -w '%{http_code}\n' -X POST "http://127.0.0.1:8090/api/summary?token=$TOKEN"

echo
echo "=== 5) 容器与自证行 ==="
docker inspect -f 'restarts={{.RestartCount}}' dzmm-bot
docker logs --tail 30 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:' | sed 's/^/  /'
echo -n "  异常扫描: "
docker logs --tail 200 dzmm-bot 2>&1 | grep -icE 'traceback|exception' | sed 's/^/命中 /'
