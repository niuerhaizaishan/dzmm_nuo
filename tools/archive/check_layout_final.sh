#!/bin/sh
# 布局指令最终口径：必须点名（屋主 2026-09-27）
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
from runtime import room_layout as RL
from runtime.sign import _is_room_trigger as T

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"
NUONUO = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"

print("=== 屋主说这些话：会不会换房间 + 发图 ===")
for t in ("泳池", "改成泳池", "泳池风格", "我想去泳池",
          "薇丝 泳池", "@薇丝 泳池", "薇丝 改成泳池", "薇丝 泳池风格",
          "薇丝 审问室", "薇丝 恢复原样", "薇丝 我想去泳池",
          "薇丝把房间改成赛博朋克风格"):
    print("  {:22s} 换房间+发图={:5s} 管家应声={}".format(
        t, str(RL.is_layout_cmd(t, ROSE, VV)), str(T(ROSE, VV, t))))

print("=== 客人说同样的话（应声后婉拒，绝不换房间）===")
for t in ("薇丝 泳池", "薇丝 改成泳池", "泳池"):
    print("  {:22s} 换房间+发图={:5s} 管家应声={}".format(
        t, str(RL.is_layout_cmd(t, ROSE, GUEST)), str(T(ROSE, GUEST, t))))

print("=== 女鬼命令（强信号，照旧不要求名字）===")
for t in ("变成女鬼", "演一下女鬼场景"):
    print("  {:22s} 应声={}".format(t, str(T(ROSE, NUONUO, t))))
PY
echo "=== 启动行 ==="
tail -2 /opt/dzmm/bot.log
