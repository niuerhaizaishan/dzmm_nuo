#!/bin/sh
# 排查：.env 里的密码 vs 容器里读到的密码 vs shell 读出来的密码
echo "=== .env 里的行（cat -A 看隐藏字符） ==="
grep -E '^DZMM_ADMIN_(USER|PASSWORD)=' /opt/dzmm/.env | cat -A

echo
echo "=== shell 读出来的（含长度与尾部字节） ==="
PW=$(grep -E '^DZMM_ADMIN_PASSWORD=' /opt/dzmm/.env | cut -d= -f2-)
printf '  长度=%s\n' "${#PW}"
printf '  尾部三字节: '; printf '%s' "$PW" | tail -c 3 | od -c | head -1

echo
echo "=== 容器里读到的 ==="
docker exec dzmm-bot sh -c 'printf "  USER=[%s]\n" "$DZMM_ADMIN_USER"; printf "  密码长度=%s\n" "${#DZMM_ADMIN_PASSWORD}"; printf "  尾部: "; printf "%s" "$DZMM_ADMIN_PASSWORD" | tail -c 3 | od -c | head -1'

echo
echo "=== .env 的行尾风格 ==="
file /opt/dzmm/.env
