#!/bin/sh
# 布局指令的触发面实测：哪些说法认、哪些不认
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
from runtime import room_layout as RL

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"

cases = [
    "泳池", "薇丝 泳池", "@薇丝 泳池", "泳池风格", "改成泳池", "换成泳池",
    "变成泳池", "来个泳池", "布置成泳池", "我想去泳池", "泳池真好看",
    "审问室", "薇丝 审问室", "烧烤", "户外烧烤", "玫瑰房", "恢复原样",
    "改成赛博朋克",
]
print("=== 屋主说这些话，会不会触发（认布局 + 有改的意图）===")
for t in cases:
    print("  {:14s} 认出的布局={:6s} 屋主触发={:5s} 客人触发={}".format(
        t, RL.layout_of(t) or "认不出",
        str(RL.is_layout_cmd(t, ROSE, VV)), str(RL.is_layout_cmd(t, ROSE, GUEST))))
PY
