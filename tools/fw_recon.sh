#!/bin/sh
# 主机侧防火墙全貌：确认到底是谁在挡 5900，以及 8090 为什么能通
echo "=== iptables INPUT 链（全部规则） ==="
iptables -S INPUT

echo
echo "=== INPUT 链计数（有命中说明规则在起作用） ==="
iptables -L INPUT -n -v --line-numbers 2>/dev/null | head -25

echo
echo "=== DOCKER-USER 链 ==="
iptables -S DOCKER-USER

echo
echo "=== nftables（Aliyun 新实例可能用 nft 而不是 iptables） ==="
if command -v nft >/dev/null 2>&1; then
  nft list ruleset 2>/dev/null | head -40
  echo "(以上为空 = nft 没有规则)"
else
  echo "没有 nft 命令"
fi

echo
echo "=== 本机看到的对外监听 ==="
ss -lntp 2>/dev/null | sed 's/^/  /'
