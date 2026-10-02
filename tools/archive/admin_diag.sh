#!/bin/sh
# 运维后台可达性诊断（服务端侧）
echo "--- 1) 容器与端口 ---"
docker inspect -f 'container={{.State.Status}} restarts={{.RestartCount}}' dzmm-bot
docker port dzmm-bot

echo
echo "--- 2) 服务端本机自测 ---"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/)
echo "  http://127.0.0.1:8090/  ->  HTTP $code"
echo -n "  /healthz 内容: "; curl -s -m 5 http://127.0.0.1:8090/healthz; echo

echo
echo "--- 3) 后台进程 ---"
docker exec dzmm-bot ps ax 2>/dev/null | grep dashboard | grep -v grep

echo
echo "--- 4) sshd 端口转发设置（-L 需要 AllowTcpForwarding yes） ---"
sshd -T 2>/dev/null | grep -i -E 'allowtcpforwarding|gatewayports|permitopen' \
  || echo "  sshd -T 不可用，看配置文件："
grep -i -E 'AllowTcpForwarding|GatewayPorts' /etc/ssh/sshd_config 2>/dev/null \
  || echo "  配置文件里没写 -> 用默认值 AllowTcpForwarding yes（-L 可用）"

echo
echo "--- 5) 注意：8090 只在宿主机回环，公网本来就该连不上 ---"
echo -n "  从公网看（本机自连不算）: "
ss -lntp 2>/dev/null | grep 8090 | sed 's/^/    /'
