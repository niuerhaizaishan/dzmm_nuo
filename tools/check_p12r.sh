#!/bin/sh
# P12-R 上线自检：用今天真实误触发的原文验证新口径
echo "=== 触发口径（玫瑰房）==="
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
from runtime.sign import _is_room_trigger as T

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"
NUONUO = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"

cases = [
    (GUEST, "@橘先生（贱畜桃灼的主人） 小橘！摘个花试试！", False, "今天误触发①：在议论别人"),
    (GUEST, "速配星座：天蝎座", False, "今天误触发②：复读卡片"),
    (GUEST, "幸运颜色：深紫", False, "复读卡片三件套"),
    (GUEST, "想喝一杯酒", False, "A：未点名的点单"),
    (GUEST, "薇丝，给我来杯龙舌兰", True, "点名点单（今天真实）"),
    (GUEST, "薇丝你是谁", True, "今天真实"),
    (GUEST, "摩羯座", True, "保留：星座服务"),
    (GUEST, "摘花", True, "保留：摘花玩法"),
    (GUEST, "我是金牛座", True, "保留：报星座"),
    (GUEST, "今天天气不错", False, "纯闲聊"),
    (VV, "改成泳池", True, "屋主自然说法（不带名字）"),
    (VV, "变成女鬼", True, "屋主命令女鬼"),
    (GUEST, "改成泳池", False, "客人不能换布局"),
]
bad = 0
for uid, text, want, why in cases:
    got = T(ROSE, uid, text)
    ok = "OK " if got == want else "!! "
    if got != want:
        bad += 1
    print("  {}{:30s} 触发={:5s} 期望={:5s}  {}".format(
        ok, text[:30], str(got), str(want), why))
print("不符预期的用例数 =", bad)
PY
echo "=== 启动行 ==="
tail -3 /opt/dzmm/bot.log
