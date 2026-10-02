#!/bin/sh
# 手工重现登录，并把服务端日志打出来
USER=$(grep -E '^DZMM_ADMIN_USER=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
PW=$(grep -E '^DZMM_ADMIN_PASSWORD=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
B=http://127.0.0.1:8090
J=/tmp/p9c.txt
rm -f $J

echo "读到账号=[$USER]  密码长度=${#PW}"
echo

echo "--- 直接用 curl 登录 ---"
curl -s -i -c $J -d "username=$USER" -d "password=$PW" $B/login | head -12
echo

echo "--- cookie 文件 ---"
cat $J 2>/dev/null | sed 's/^/  /'
echo

echo "--- 服务端日志（最近 12 行 admin 相关）---"
docker logs dzmm-bot 2>&1 | grep '\[admin\]' | tail -12 | sed 's/^/  /'
rm -f $J
