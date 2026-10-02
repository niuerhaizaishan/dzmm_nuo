#!/bin/sh
# P12-O 上线自检：女鬼图公网可读 + 容器内判定正确
echo "=== 女鬼图 ==="
curl -s -o /tmp/g.jpg -w 'http=%{http_code} bytes=%{size_download}\n' \
  https://www.nuonuo.pw/img/room/ghost.jpg
md5sum /tmp/g.jpg
echo "=== 容器内判定 ==="
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
from runtime import ghost_photo as GP

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"
NUONUO = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"
for t in ("薇丝变成女鬼", "演一下女鬼场景", "进入女鬼模式", "来个女鬼场景",
          "女鬼真吓人", "薇丝你说这个女鬼到底是什么来头啊我有点好奇能不能讲讲"):
    print("  {:24s} 屋主={} 糯糯={} 客人={}".format(
        t[:24], GP.wants_photo(t, ROSE, VV), GP.wants_photo(t, ROSE, NUONUO),
        GP.wants_photo(t, ROSE, GUEST)))
print("  URL =", GP.URL)
PY
echo "=== 配图日志 ==="
grep -a '配图已发' /opt/dzmm/bot.log | tail -4
echo "=== 启动行 ==="
tail -3 /opt/dzmm/bot.log
