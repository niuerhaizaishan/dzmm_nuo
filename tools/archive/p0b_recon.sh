#!/bin/sh
# 暴露面与访问来源侦察（只读），给 P0-b 安全组加固用
echo "=== 1) 当前 SSH 连接的来源 IP（要加入白名单的就是它） ==="
ss -tn state established 2>/dev/null | awk 'NR>1 && $4 ~ /:22$/ {print "  对端:", $5}' | sort -u
echo "--- who ---"
who 2>/dev/null | sed 's/^/  /'
echo "--- 最近成功登录的来源（lastlog / auth log 摘要）---"
last -i 2>/dev/null | head -6 | sed 's/^/  /'

echo
echo "=== 2) 对外监听（安全组之外，主机上还有没有别的口开着） ==="
ss -lntp 2>/dev/null | sed 's/^/  /'

echo
echo "=== 3) 主机防火墙 ==="
if command -v iptables >/dev/null 2>&1; then
  iptables -S 2>/dev/null | head -10 | sed 's/^/  /' || echo "  （读不到 iptables 规则）"
else
  echo "  没有 iptables 命令"
fi
echo -n "  firewalld: "; systemctl is-active firewalld 2>/dev/null || echo "未安装/未运行"

echo
echo "=== 4) VNC 是怎么起来的 ==="
systemctl cat dzmm-vnc.service 2>/dev/null | sed 's/^/  /'
echo "  --- start_vnc.sh ---"
sed 's/^/  /' /opt/dzmm/start_vnc.sh 2>/dev/null | head -30

echo
echo "=== 5) VNC 进程与参数（看有没有 -localhost / 密码文件） ==="
ps ax 2>/dev/null | grep -E '[x]11vnc|[w]ebsockify|[X]vfb' | sed 's/^/  /'
echo "  --- 密码文件 ---"
ls -l /root/.vnc/passwd /opt/dzmm/*.passwd 2>/dev/null | sed 's/^/  /' || echo "  （默认位置没找到）"

echo
echo "=== 6) 容器里的验证码告警文案用的地址 ==="
grep -hE '^VNC_URL|^VNC_PASS' /opt/dzmm/.env 2>/dev/null | sed 's/^/  /' || echo "  .env 里没显式设置（用 compose 默认值）"
