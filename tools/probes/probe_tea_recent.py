# -*- coding: utf-8 -*-
"""诊断：茶水间最近的消息 + 本群 `last_id`（看那两条 @ 有没有被处理过）。"""
import sys
import time

sys.path.insert(0, "/app")

import dzmm                                                    # noqa: E402
import state_store                                             # noqa: E402
from runtime import identity as ID                             # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"


def main():
    members = dzmm.get_members(TEA) or {}
    ID._name_cache.update(members)
    last = state_store.group_get(TEA, "last_id", "")
    msgs = dzmm.get_messages(TEA, 40) or []
    print("last_id = {}".format((last or "")[:8]))
    print("最近 {} 条（UTC 时间）：".format(len(msgs)))
    for m in msgs:
        t = (m.get("content") or {}).get("text") or ""
        mid = (m.get("message_id") or "")[:8]
        sb = m.get("sent_by") or ""
        who = "【我】" if ID.is_self(sb) else ID.get_name(sb)[:12]
        print("  {} {} {:<14} {} 字 {} 行 {!r}".format(
            (m.get("sent_at") or "")[11:19], mid, who, len(t),
            t.count("\n") + 1, t[:52].replace("\n", "⏎")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
