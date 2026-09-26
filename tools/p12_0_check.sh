#!/bin/sh
# P12-0 三项修复在生产上的验收
PW=$(grep -E '^DZMM_ADMIN_PASSWORD=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
USER=$(grep -E '^DZMM_ADMIN_USER=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
J=/tmp/p120_cookies.txt
rm -f $J
curl -s -o /dev/null -c $J -d "username=$USER" -d "password=$PW" http://127.0.0.1:8090/login
curl -s -b $J -o /tmp/p120.html http://127.0.0.1:8090/edit

echo "=== ① 人设字段已接通（页面上能看出"留空会用哪个"） ==="
grep -o "人设（填人设名）" /tmp/p120.html | head -1
echo -n "  可用人设名提示: "; grep -o "可用的人设名：<span class='mono'>[^<]*" /tmp/p120.html | sed 's/.*>//'
echo -n "  认证群留空时用: "; grep -o "name='groups\[0\]\[persona\]'[^>]*placeholder='[^']*'" /tmp/p120.html | sed "s/.*placeholder='//;s/'//"
echo -n "  Rose 群留空时用: "; grep -o "name='groups\[4\]\[persona\]'[^>]*placeholder='[^']*'" /tmp/p120.html | sed "s/.*placeholder='//;s/'//"
echo -n "  Bot 表还有人设列吗（应当没有）: "
grep -c "bots\[0\]\[persona\]" /tmp/p120.html

echo
echo "=== ② 备份：列全 + 能点进去看差异 ==="
echo -n "  磁盘备份数: "; ls -1 /opt/dzmm/config/config_backups/config-*.json | wc -l
echo -n "  页面列出的回滚按钮数（应当与磁盘一致）: "
grep -c "action='/edit/rollback'" /tmp/p120.html
echo -n "  页面上的「看差异」链接数: "; grep -c "看差异" /tmp/p120.html
BK=$(ls -1t /opt/dzmm/config/config_backups/config-*.json | head -1 | xargs basename)
echo "  挑最新一版看详情: $BK"
curl -s -b $J -o /tmp/p120_bk.html -w "  /edit/backup -> %{http_code}\n" "http://127.0.0.1:8090/edit/backup?name=$BK"
grep -q "回滚到这一版" /tmp/p120_bk.html && echo "  详情页有回滚按钮 ✓" || echo "  !! 详情页异常"
echo -n "  详情页差异区块: "; grep -o "这个备份与当前配置的差异" /tmp/p120_bk.html | head -1
echo -n "  路径穿越防护: "
curl -s -b $J -o /tmp/p120_trav.html -w "%{http_code}" "http://127.0.0.1:8090/edit/backup?name=../config_audit.log"
grep -q "打不开备份" /tmp/p120_trav.html && echo "（被拒 ✓）" || echo "（!! 没被拒）"

echo
echo "=== ③ 人设自证行（配置指定的会逐条打印） ==="
docker logs --tail 120 dzmm-bot 2>&1 | grep -e "P11: 人设" | tail -2

echo
echo "=== 异常 ==="
docker logs --tail 300 dzmm-bot 2>&1 | grep -ciE 'traceback|exception'
rm -f $J
