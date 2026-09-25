#!/bin/sh
# renew_token.log 里 173 条 FAIL 是什么？是全历史的还是最近的？
F=/opt/dzmm/renew_token.log
echo "=== 日志规模 ==="
wc -l $F | sed 's/^/  /'
echo "  首行: $(head -1 $F)"
echo "  末行: $(tail -1 $F)"

echo
echo "=== FAIL 行的日期分布（按天） ==="
grep 'FAIL' $F 2>/dev/null | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort | uniq -c | tail -12 | sed 's/^/  /'

echo
echo "=== 最近 10 条 FAIL（看具体内容） ==="
grep 'FAIL' $F 2>/dev/null | tail -10 | sed 's/^/  /'

echo
echo "=== 最近 30 行整体（含非 FAIL） ==="
tail -30 $F | sed 's/^/  /'

echo
echo "=== 今天（$(date +%Y-%m-%d)）有几条 FAIL ==="
grep -c "^$(date +%Y-%m-%d).*FAIL" $F 2>/dev/null || echo 0
