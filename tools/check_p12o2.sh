#!/bin/sh
# P12-O 补丁自检：命令女鬼 -> 真的进状态
echo "=== 容器内：命令能否进房间分支 + 判定 ==="
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
from runtime.sign import _is_room_trigger
from runtime import ghost_photo as GP

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"
NUONUO = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"
for who, uid in (("屋主", VV), ("糯糯", NUONUO), ("客人", GUEST)):
    for t in ("变成女鬼", "演一下女鬼场景", "女鬼真吓人"):
        print("  {:4s} {:14s} 进房间分支={} 可发图={}".format(
            who, t, _is_room_trigger(ROSE, uid, t), GP.wants_photo(t, ROSE, uid)))
PY
echo "=== bot.py 里的女鬼命令分支 ==="
grep -n '命令女鬼上场' /opt/dzmm/bot.py
grep -c 'rose_ghost' /opt/dzmm/bot.py
echo "=== 启动行 ==="
tail -3 /opt/dzmm/bot.log
echo "=== 女鬼/配图 日志 ==="
grep -a '女鬼\|配图已发' /opt/dzmm/bot.log | tail -5
