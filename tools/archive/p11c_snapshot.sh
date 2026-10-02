#!/bin/sh
# P11-C 上线后的生产快照（留档）
echo "=== 容器 ==="
docker inspect -f '  StartedAt={{.State.StartedAt}} Restarts={{.RestartCount}} Mem={{.State.Status}}' dzmm-bot
docker stats --no-stream --format '  MEM={{.MemUsage}} CPU={{.CPUPerc}}' dzmm-bot
echo "=== 启动自证行 ==="
docker logs --tail 60 dzmm-bot 2>&1 | grep -e P5: -e P7: -e P8: -e P11: -e bot路由 -e outbox: | tail -8
echo "=== outbox ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3
c = sqlite3.connect("file:/app/data/outbox.db?mode=ro", uri=True)
tabs = [r[0] for r in c.execute(
    "select name from sqlite_master where type='table'")]
print("  表:", tabs)
for t in tabs:
    cols = [r[1] for r in c.execute("pragma table_info({})".format(t))]
    print("  {}: {} 行, 列={}".format(t, c.execute("select count(*) from " + t).fetchone()[0], cols))
    if 'status' in cols:
        print("     ", list(c.execute(
            "select status, count(*) from {} group by status".format(t))))
PY
echo "=== 配置文件与目录 ==="
ls -la /opt/dzmm/config/ /opt/dzmm/config/config_backups/
echo "=== 备份是否还有坏状态（应当全是 mute=0 screen=3 push=2） ==="
for f in /opt/dzmm/config/config_backups/config-*.json; do
  python3 - "$f" <<'PY'
import json, sys
c = json.load(open(sys.argv[1], encoding='utf-8'))
print("  {:34s} mute={} screen={} push={}".format(
    sys.argv[1].rsplit('/', 1)[1],
    sum(1 for g in c['groups'] if g['mute']),
    sum(1 for g in c['groups'] if g['screen']),
    sum(1 for g in c['groups'] if g['push'])))
PY
done
echo "=== 异常 ==="
docker logs --tail 300 dzmm-bot 2>&1 | grep -icE 'traceback|exception'
