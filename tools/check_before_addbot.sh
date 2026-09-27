#!/bin/sh
# 加 Bot 前的现状盘点：群清单 / Bot 清单 / compose 透传 / webhook Secret 命名规则
echo "=== 群清单（cid + alias + enabled）==="
docker exec dzmm-bot python -c "
import json
c = json.load(open('/app/config/config.json', encoding='utf-8'))
for g in c.get('groups', []):
    print('  {:40s} {:28s} enabled={}'.format(g.get('cid',''), g.get('alias',''), g.get('enabled')))
"
echo "=== Bot 清单 ==="
docker exec dzmm-bot python -c "
import json
c = json.load(open('/app/config/config.json', encoding='utf-8'))
for b in c.get('bots', []):
    print('  key={:12s} name={:12s} short={:8s} cid={:38s} token_env={:24s} enabled={} uid={}'.format(
        b.get('key',''), b.get('name',''), b.get('short',''), b.get('cid',''),
        b.get('token_env',''), b.get('enabled'), b.get('uid','')))
"
echo "=== compose 的 env 透传（含 env_file？）==="
grep -n 'DZMM_BOT\|env_file' /opt/dzmm/docker-compose.yml
echo "=== .env 里的 BOT 变量名 ==="
grep -a -o 'DZMM_BOT[A-Z_]*' /opt/dzmm/.env | sort -u
echo "=== webhook Secret 的命名规则 ==="
grep -n -A14 'def secret_env_for' /app/webhook_server.py 2>/dev/null || \
  docker exec dzmm-bot sh -c "grep -n -A14 'def secret_env_for' /app/webhook_server.py"
