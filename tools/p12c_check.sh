#!/bin/sh
# P12-C 上线验收：v1 → v2 自动迁移（备份 + 落盘）+ 行为不变
echo "=== 0) 迁移前快照 ==="
cp /opt/dzmm/config/config.json /tmp/pre_v2_snapshot.json
python3 - <<'PY'
import json
d = json.load(open('/tmp/pre_v2_snapshot.json', encoding='utf-8'))
print("  version =", d.get("version"), "| 群 =", len(d["groups"]), "| 键 =", sorted(d.keys()))
PY
echo -n "  迁移前备份数: "; ls -1 /opt/dzmm/config/config_backups/config-*.json | wc -l

echo
echo "=== 1) 重启（启动时自动升级） ==="
cd /opt/dzmm && docker compose up -d --force-recreate dzmm-bot >/dev/null 2>&1
sleep 16
docker logs --tail 200 dzmm-bot 2>&1 | grep -e "P11: 配置已从" -e "P11: 配置=" -e "P11: 人设" -e "P12:" | head -8

echo
echo "=== 2) 磁盘上的文件已经是 v2 吗 ==="
python3 - <<'PY'
import json
d = json.load(open('/opt/dzmm/config/config.json', encoding='utf-8'))
print("  version =", d.get("version"), "| 群 =", len(d["groups"]), "| 键 =", sorted(d.keys()))
print("  空节没被写进去（features/tuning/scopes 不该凭空出现）:",
      not any(k in d for k in ("features", "tuning", "scopes")))
PY

echo
echo "=== 3) 升级前的原文件进备份了吗 ==="
echo -n "  迁移后备份数: "; ls -1 /opt/dzmm/config/config_backups/config-*.json | wc -l
NEW=$(ls -1t /opt/dzmm/config/config_backups/config-*.json | head -1)
echo "  最新备份: $(basename $NEW)"
python3 - "$NEW" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print("  备份里的 version =", d.get("version"), "（应当是 1，即升级前那一版）")
PY

echo
echo "=== 4) 行为没变（与迁移前逐字段比对） ==="
python3 - <<'PY'
import json
a = json.load(open('/tmp/pre_v2_snapshot.json', encoding='utf-8'))
b = json.load(open('/opt/dzmm/config/config.json', encoding='utf-8'))
same_groups = [dict(g) for g in a["groups"]] == [dict(g) for g in b["groups"]]
same_bots = [dict(x) for x in a.get("bots") or []] == [dict(x) for x in b.get("bots") or []]
print("  群一字未动:", same_groups, "| Bot 一字未动:", same_bots)
print("  只多了 version 的变化:", same_groups and same_bots and
      a.get("version") == 1 and b.get("version") == 2)
PY

echo
echo "=== 5) 面板读到的 schema ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/admin")
import dashboard as d
c = d.section_config()
print("  schema_version =", c.get("schema_version"), "| migrated_from =", c.get("migrated_from"),
      "| source =", c.get("source"), "| 群(配置) =", c.get("configured_count"))
PY

echo
echo "=== 6) 异常 ==="
docker logs --tail 300 dzmm-bot 2>&1 | grep -ciE 'traceback|exception'
