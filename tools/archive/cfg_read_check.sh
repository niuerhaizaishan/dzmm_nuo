#!/bin/sh
# 为什么一次性容器进程里 bot.PUSH_CIDS 只有 1 个群，而运行中的进程自证是 推送=2？
echo "=== 1) 环境变量（exec 里看得到哪些） ==="
docker exec -i dzmm-bot sh -c 'env | grep -e DZMM | sort' 2>&1
echo
echo "=== 2) 一次性进程里：配置到底读成了什么 ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, '/app')
from runtime import config_store as cs
print("  config_path() =", cs.config_path(), "存在:", cs.config_path().exists())
try:
    cfg = cs.load({})
    print("  load({}) -> source=%s groups=%d push=%s mute=%s screen=%s" % (
        cfg.source, len(cfg.groups), list(cfg.push), list(cfg.mute), list(cfg.screen)))
except Exception as e:
    print("  load({}) 抛异常:", type(e).__name__, e)
print()
import json
raw = json.load(open('/app/config/config.json', encoding='utf-8'))
for g in raw['groups']:
    print("  文件里: {} enabled={} mute={} screen={} push={}".format(
        g['cid'][:8], g['enabled'], g['mute'], g['screen'], g['push']))
print()
import bot
print("  bot.PUSH_CIDS =", bot.PUSH_CIDS)
print("  bot.SCREEN_CIDS =", sorted(x[:8] for x in bot.SCREEN_CIDS))
print("  bot.MUTE_CIDS =", sorted(x[:8] for x in bot.MUTE_CIDS))
print("  bot.GROUPS =", [x[:8] for x in bot.GROUPS])
PY
echo
echo "=== 3) 运行中进程的自证行（权威） ==="
docker logs --tail 60 dzmm-bot 2>&1 | grep -e P11: | tail -3
