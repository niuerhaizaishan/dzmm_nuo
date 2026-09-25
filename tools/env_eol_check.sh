#!/bin/sh
# .env 行尾体检：统计 CRLF / LF 各多少行，并列出混用的行
F=/opt/dzmm/.env
total=$(wc -l < $F)
crlf=$(grep -c $'\r$' $F 2>/dev/null || true)
echo "总行数 : $total"
echo "以 CRLF 结尾的行: $crlf"
echo "以 LF   结尾的行: $((total - crlf))"
echo
echo "=== 非 CRLF 结尾的行（行号 + 内容，值已截断） ==="
awk '{ if (substr($0, length($0), 1) != "\r") printf "  %4d: %s\n", NR, substr($0, 1, 60) }' $F | head -20
echo
echo "=== 空的/只有 CR 的行 ==="
awk 'length($0)==1 && substr($0,1,1)=="\r" { print "  "NR }' $F | head -10
echo "(以上为空 = 没有这种行)"
