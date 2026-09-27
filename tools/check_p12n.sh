#!/bin/sh
# P12-N 上线自检（容器内判定用 heredoc 直接喂 python，避免文件传输的编码坑）
echo "=== 容器内布局判定 ==="
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
from runtime import room_layout as RL

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"
for t in ("薇丝改变房间布局（审问室风格）", "换成泳池", "把露台弄成户外烧烤",
          "恢复原样", "改成赛博朋克风格", "玫瑰房真好看"):
    print("  {:26s} 布局={:6s} 屋主={} 客人={}".format(
        t, RL.layout_of(t) or "认不出",
        RL.wants_photo(t, ROSE, VV), RL.wants_photo(t, ROSE, GUEST)))
for name in ("审问室", "玫瑰泳池", "户外烧烤", "玫瑰房"):
    print("  {} -> {}".format(name, RL.photo_url(name)))
PY
echo "=== 最近房间消息 ==="
grep -a '房间消息' /opt/dzmm/bot.log | tail -3
echo "=== 配图日志 ==="
grep -a '配图已发' /opt/dzmm/bot.log | tail -3
