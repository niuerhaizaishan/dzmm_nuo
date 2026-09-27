#!/bin/sh
# P12-P 上线自检：玫瑰房里 /查 对所有人开放，其余指令仍婉拒
echo "=== 容器内闸门判定 ==="
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
import bot
import personas
from runtime import features

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"
NUONUO = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"
print("  query 功能开关 =", features.on(ROSE, "query"))
print("  查档案话术     =", personas.p("query_working", ROSE))
for cmd in ("/查 橘先生", "/whois 橘先生", "/资料 橘先生", "/写诗 月亮", "/算命", "/注册"):
    print("  {:14s} 客人被婉拒={}  主人被婉拒={}".format(
        cmd, bot._room_cmd_refused(ROSE, cmd, GUEST), bot._room_cmd_refused(ROSE, cmd, VV)))
print("  is_profile_cmd('/查 橘先生') =", repr(bot.is_profile_cmd("/查 橘先生")))
PY
echo "=== 最近房间消息 ==="
grep -a '房间消息' /opt/dzmm/bot.log | tail -3
echo "=== 启动行 ==="
tail -3 /opt/dzmm/bot.log
