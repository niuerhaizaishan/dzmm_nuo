#!/bin/sh
# 收尾核查：把每一份备份的"开关状态"列出来，删掉那份**全 true** 的坏备份
# （它是我那个拼接表单的脚本 bug 写出来的状态，留着会被误回滚）
CFG=/opt/dzmm/config/config.json
BKD=/opt/dzmm/config/config_backups
BAK=/opt/dzmm/config/config_backups/config-20260925-222259.json

echo "=== 各备份的开关状态（认证群 alias / 5 个群的 mute+screen+push 合计） ==="
for f in $BKD/config-*.json; do
  python3 - "$f" <<'PY'
import json, sys
p = sys.argv[1]
c = json.load(open(p, encoding='utf-8'))
flags = {k: sum(1 for g in c['groups'] if g.get(k)) for k in ('mute', 'screen', 'push')}
alias = next((g['alias'] for g in c['groups'] if g['cid'].startswith('bd7fe6a4')), '?')
print("  {:34s} alias={:16s} mute={} screen={} push={}".format(
    p.rsplit('/', 1)[1], alias[:16], flags['mute'], flags['screen'], flags['push']))
PY
done

echo
echo "=== 备份与当前配置是否逐字段一致 ==="
python3 - "$BAK" "$CFG" <<'PY'
import json, sys
GD = {"alias": "", "enabled": True, "mute": False, "screen": False, "push": False, "persona": ""}
BD = {"name": "", "short": "", "cid": "", "token_env": "", "persona": "", "enabled": True}
def norm(x, d):
    x = dict(x or {})
    for k, v in d.items():
        x.setdefault(k, v)
    return x
def load(p):
    c = json.load(open(p, encoding='utf-8'))
    return ([norm(g, GD) for g in c['groups']], [norm(b, BD) for b in c.get('bots') or []])
a, b = load(sys.argv[1]), load(sys.argv[2])
print("  ✓ 与 P11-A 期那份原始备份完全一致" if a == b else "  !! 不一致")
PY

echo
echo "=== 删掉全 true 的那份坏备份 ==="
BAD=$BKD/config-20260925-223012.json
if [ -f "$BAD" ]; then
  rm -f "$BAD" && echo "  已删 $BAD"
else
  echo "  （没有，跳过）"
fi
ls -1 $BKD/

echo
echo "=== 审计日志全量 ==="
cat /opt/dzmm/config/config_audit.log

echo
echo "=== 当前生效状态 ==="
docker logs --tail 30 dzmm-bot 2>&1 | grep -e P11: | tail -2
docker inspect -f '  StartedAt={{.State.StartedAt}} Restarts={{.RestartCount}}' dzmm-bot
