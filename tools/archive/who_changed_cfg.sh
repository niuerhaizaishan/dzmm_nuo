#!/bin/sh
# 谁在什么时候改了配置？—— 审计日志 + 后台日志 + 文件时间戳
echo "=== 1) 审计日志（全量） ==="
cat /opt/dzmm/config/config_audit.log
echo
echo "=== 2) 配置文件时间戳 ==="
ls -l --time-style=full-iso /opt/dzmm/config/config.json
echo "  运行中进程启动于: $(docker inspect -f {{.State.StartedAt}} dzmm-bot)"
echo
echo "=== 3) 后台日志：登录 / 保存 / 回滚 / CSRF 拒 ==="
docker logs dzmm-bot 2>&1 | grep -e '\[admin\]' | tail -25
echo
echo "=== 4) 当前文件里每个群的四个开关 ==="
python3 - <<'PY'
import json, time, os
p = '/opt/dzmm/config/config.json'
c = json.load(open(p, encoding='utf-8'))
print("  文件 mtime:", time.strftime('%m-%d %H:%M:%S', time.localtime(os.path.getmtime(p))))
for g in c['groups']:
    print("  {} enabled={:<5} mute={:<5} screen={:<5} push={:<5} alias={}".format(
        g['cid'][:8], str(g['enabled']), str(g['mute']), str(g['screen']),
        str(g['push']), g['alias'][:20]))
PY
echo
echo "=== 5) 与"原始正确配置"的差异（取最早那份备份做基准） ==="
python3 - <<'PY'
import json
base = json.load(open('/opt/dzmm/config/config_backups/config-20260925-140335.json',
                     encoding='utf-8'))
cur = json.load(open('/opt/dzmm/config/config.json', encoding='utf-8'))
b = {g['cid']: g for g in base['groups']}
c = {g['cid']: g for g in cur['groups']}
for cid in b:
    x, y = b[cid], c.get(cid) or {}
    for k in ('enabled', 'mute', 'screen', 'push', 'alias'):
        if x.get(k) != y.get(k):
            print("  {} {}: {!r} -> {!r}".format(cid[:8], k, x.get(k), y.get(k)))
PY
