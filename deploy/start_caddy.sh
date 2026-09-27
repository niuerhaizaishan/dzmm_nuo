#!/bin/sh
# 装 Caddy（容器方式，宿主机不装包）+ 起 TLS 入口
set -e
cd /opt/dzmm
echo "=== 拉镜像 ==="
docker pull caddy:2-alpine 2>&1 | tail -2
echo
echo "=== 起 Caddy（--network host，才能反代 127.0.0.1:8091）==="
docker rm -f caddy-nuonuo 2>/dev/null || true
# /opt/dzmm/deploy/img -> /srv/img：给 `sendPhoto` 用的公网图片（P12-L）。
mkdir -p /opt/dzmm/deploy/img
docker run -d --name caddy-nuonuo --restart unless-stopped --network host \
  -v /opt/dzmm/Caddyfile:/etc/caddy/Caddyfile:ro \
  -v /opt/dzmm/deploy/img:/srv/img:ro \
  -v caddy_data:/data -v caddy_config:/config \
  caddy:2-alpine 2>&1 | tail -1
echo
echo "=== 等证书（最多 60 秒）==="
for i in $(seq 1 12); do
  sleep 5
  if docker logs caddy-nuonuo 2>&1 | grep -qiE 'certificate obtained|serving|error'; then
    break
  fi
done
echo "--- Caddy 日志 ---"
docker logs caddy-nuonuo 2>&1 | tail -12
echo
echo "=== 宿主机端口 ==="
ss -lnt 2>/dev/null | grep -E ':(80|443)\b' || echo "（80/443 还没在听）"
