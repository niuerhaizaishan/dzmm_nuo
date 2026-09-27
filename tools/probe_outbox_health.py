"""一行探针：巡检看到的生产出站队列长什么样（只读）。"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import check_bot_health as h                                    # noqa: E402

ob = h.check_outbox()
if ob is None:
    print("读不到 data/outbox.db")
    raise SystemExit(0)
last = ob.get("last") or {}
print("pending =", ob["pending"], " failed =", ob["failed"])
print("最近失败：群={} 字数={} 状态={}".format(
    str(last.get("chatroom_id"))[:8], len(str(last.get("text") or "")),
    last.get("last_error")))
print("正文开头：", str(last.get("text") or "")[:40].replace("\n", " "))
