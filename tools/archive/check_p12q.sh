#!/bin/sh
# P12-Q 上线自检：欢迎语带上自我介绍；问"你是谁"用固定自我介绍
echo "=== 玫瑰房的新客欢迎语（实际会发出去的那条）==="
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
import bot

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
print("--- 玫瑰房 ---")
print(bot._room_greet_text("阿May", ROSE))
print("--- 认证群（没有自我介绍，不能留占位符/空行）---")
print(bot._room_greet_text("阿May", AUTH))
print("--- 问'你是谁'的判定 ---")
import runtime.intro_photo as IP
for t in ("薇丝你是谁", "薇丝是谁", "介绍一下你自己", "今天天气不错"):
    print("  {:12s} 用固定自我介绍={}".format(t, IP.wants_photo(t, ROSE)))
PY
echo "=== 启动行 ==="
tail -3 /opt/dzmm/bot.log
