"""P12-K 冒烟监看：等玫瑰房有新房间消息 → 回读群里最后几条（只读，不发消息）。

    docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python /tmp/watch_rose_card.py [等待秒数]

等日志里出现新的「房间消息」行（`bot.py` 打的，零平台调用），然后**只做一次**
平台回读，把最后几条消息原文打印出来 —— 用来核对五段卡片在群里落地后的
真实行数/字数/标签。
"""
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

from dzmm import get_messages_since                    # noqa: E402
from runtime.identity import get_name, is_self         # noqa: E402

CID = "4a56be71-9ca6-4785-b45f-18725d187f1a"
TZ = timedelta(hours=8)
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 240
SHOW = int(sys.argv[2]) if len(sys.argv) > 2 else 6
LOG = next((p for p in ("/app/bot.log", "/opt/dzmm/bot.log", "bot.log")
            if os.path.exists(p)), "/app/bot.log")


def room_lines():
    try:
        with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
            return [ln.rstrip() for ln in f if "房间消息" in ln]
    except Exception as e:                                  # noqa: BLE001
        return ["<读日志失败: {}>".format(e)]


base = len(room_lines())
print("日志 = {}，基线「房间消息」行数 = {}".format(LOG, base))
t0 = time.time()
hit = False
while time.time() - t0 < WAIT:
    time.sleep(8)
    cur = room_lines()
    if len(cur) > base:
        print("检测到新的房间消息（{} 行）：".format(len(cur) - base))
        for ln in cur[-2:]:
            print("    " + ln)
        hit = True
        break
if not hit:
    print("等待 {}s 内没有新的房间消息；仍然回读一次群消息。".format(WAIT))

since = (datetime.utcnow() - timedelta(minutes=25)).strftime("%Y-%m-%d %H:%M:%S")
msgs = get_messages_since(CID, since, max_count=150) or []
print("=== 玫瑰房最近消息（平台回读，共 {} 条）===".format(len(msgs)))
for m in msgs[-SHOW:]:
    c = m.get("content") or {}
    txt = str(c.get("text") or "")
    uid = m.get("sent_by") or ""
    who = "机器人" if is_self(uid) else (get_name(uid) or uid[:8])
    ts = str(m.get("sent_at") or "")[:19]
    try:
        ts = (datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") + TZ).strftime("%H:%M:%S")
    except Exception:                                       # noqa: BLE001
        pass
    print("--- [{}] {} | 类型={} | {} 行 {} 字".format(
        ts, who, c.get("type") or "text", len(txt.split("\n")), len(txt)))
    print(txt)
