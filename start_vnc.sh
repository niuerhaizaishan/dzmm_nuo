#!/bin/bash
# DZMM noVNC 远程桌面三件套启动脚本（Xvfb 虚拟屏 + x11vnc VNC 服务 + websockify 网页桥）
# 由 systemd 单元 dzmm-vnc.service 管理，开机自启 + 崩溃自动重启

# 清理旧进程，避免重复启动
pkill -f "Xvfb :99" 2>/dev/null
pkill -f x11vnc 2>/dev/null
pkill -f websockify 2>/dev/null
sleep 1

# 1. Xvfb 虚拟屏幕 :99（1280x800 24 位色）
nohup Xvfb :99 -screen 0 1280x800x24 > /tmp/xvfb.log 2>&1 &
sleep 2

# 2. x11vnc 把 :99 暴露为 VNC（端口 5900，密码从 .env 的 VNC_PASS 读，默认 dzmm2026）
[ -f /opt/dzmm/.env ] && . /opt/dzmm/.env
nohup x11vnc -display :99 -forever -shared -passwd "${VNC_PASS:-dzmm2026}" -rfbport 5900 > /tmp/x11vnc.log 2>&1 &
sleep 2

# 3. websockify 把 VNC 转成 WebSocket（网页 6080 → localhost:5900），前端用 noVNC
nohup websockify --web /opt/noVNC 6080 localhost:5900 > /tmp/websockify.log 2>&1 &
sleep 2

echo "noVNC 三件套已启动：Xvfb :99 / x11vnc :5900 / websockify :6080"
