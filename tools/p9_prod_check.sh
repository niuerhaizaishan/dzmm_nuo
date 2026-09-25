#!/bin/sh
# P9 部署后自检（宿主机执行）
echo "=== compose 应用日志尾部 ==="
tail -6 /opt/dzmm/../compose-p9.log 2>/dev/null || true

echo
echo "=== 容器 ==="
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker inspect -f 'restarts={{.RestartCount}} started={{.State.StartedAt}}' dzmm-bot

echo
echo "=== 端口监听（8090 必须只在 127.0.0.1） ==="
ss -lntp 2>/dev/null | grep -E ':(8090|6080|5900)\b' || echo "（没有 8090 在听）"

echo
echo "=== entrypoint 是否起了运维后台 ==="
docker logs dzmm-bot 2>&1 | grep -E 'admin|运维后台' | tail -5
echo "--- dashboard 进程 ---"
docker exec dzmm-bot sh -c "ps ax 2>/dev/null | grep -c '[d]ashboard.py' || echo 0"

echo
echo "=== 宿主机回环探测 ==="
curl -s -m 5 -o /dev/null -w 'healthz http_code=%{http_code}\n' http://127.0.0.1:8090/healthz || echo "回环连不上"
curl -s -m 5 http://127.0.0.1:8090/healthz || true
echo
curl -s -m 8 -o /tmp/p9_summary.json -w 'summary http_code=%{http_code} bytes=%{size_download}\n' \
  http://127.0.0.1:8090/api/summary || echo "summary 连不上"

echo
echo "=== 公网接口探测（应当连不上） ==="
code=$(curl -s -m 4 -o /dev/null -w '%{http_code}' http://47.237.152.187:8090/healthz)
if [ -z "$code" ] || [ "$code" = "000" ]; then
  echo "公网 8090: 连不上 —— 正确（只映射到宿主机回环）"
else
  echo "!! 公网 8090 返回 $code —— 暴露了，需要立刻收回"
fi

echo
echo "=== 启动自证行 ==="
docker logs --tail 40 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:|心跳|sender ready|start  dry'

echo
echo "=== 异常扫描 ==="
docker logs --tail 200 dzmm-bot 2>&1 | grep -iE 'traceback|exception|错误|失败' | head -10
echo "(以上为空 = 无异常)"
