#!/bin/sh
# 一次性排查：配置页的"备份可回滚"列表到底渲染成了什么
cp /opt/dzmm/config/config_backups/config-20260925-222259.json /opt/dzmm/config/config.json
cd /opt/dzmm && docker compose restart dzmm-bot >/dev/null 2>&1
sleep 12
docker logs --tail 12 dzmm-bot 2>&1 | grep -e P11:
PW=$(grep -E '^DZMM_ADMIN_PASSWORD=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
USER=$(grep -E '^DZMM_ADMIN_USER=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
J=/tmp/probe_cookies.txt
rm -f $J
curl -s -o /dev/null -c $J -d "username=$USER" -d "password=$PW" http://127.0.0.1:8090/login
curl -s -b $J -o /tmp/probe.html http://127.0.0.1:8090/edit
echo "--- 页面大小 ---"
wc -c < /tmp/probe.html
echo "--- 含 rollback 的片段 ---"
grep -o "action='/edit/rollback'[^<]*<input[^>]*><input[^>]*>" /tmp/probe.html | head -3
echo "--- 页面里出现的所有 config-*.json 名字 ---"
grep -o "config-20[0-9-]*\.json" /tmp/probe.html | head -5
echo "--- name= 类隐藏域 ---"
grep -o "name='name'[^>]*" /tmp/probe.html | head -5
echo "--- 容器内 list_backups 直接调用 ---"
docker exec -i dzmm-bot python -c "
import sys
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/admin')
from runtime import config_store as cs
from admin import config_ui as ui
print('backup_dir =', cs.backup_dir())
print('list       =', ui.list_backups(cs.backup_dir()))
print('audit_path =', cs.audit_path())
print('config_path=', cs.config_path())
"
