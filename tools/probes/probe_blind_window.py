# -*- coding: utf-8 -*-
"""P12-BO 诊断：**某个时间窗内 @她 / 引用她 的消息，有没有被漏答**（只看）。

用法（容器内）：`python /app/probe_blind_window.py <起始UTC HH:MM> [条数]`
例：`python /app/probe_blind_window.py 11:45 120`（= 北京 19:45 之后）
"""
import sys

sys.path.insert(0, "/app")

import bot                                                     # noqa: E402
import dzmm                                                    # noqa: E402
from runtime import identity as ID                             # noqa: E402

GROUPS = [
    ("认证妹子群", "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"),
    ("上班群", "f4ec4c21-fb48-4be2-9972-e4128f65923b"),
    ("玫瑰房", "4a56be71-9ca6-4785-b45f-18725d187f1a"),
    ("顶层茶水间", "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"),
]


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "11:45"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        ID.register_bots(getattr(cfg, "bots", []) or [])
    except Exception:                                          # noqa: BLE001
        pass
    total_asks = 0
    for label, cid in GROUPS:
        ID._name_cache.update(dzmm.get_members(cid) or {})
        msgs = dzmm.get_messages_all(cid, max_count=n) or []
        win = [m for m in msgs if (m.get("sent_at") or "")[11:16] >= since]
        asks = []
        for i, m in enumerate(win):
            if ID.is_self(m.get("sent_by") or ""):
                continue
            t = ((m.get("content") or {}).get("text") or "")
            if bot.is_at_me(t, cid) or bot._quotes_self(m):
                # 后面有没有我们发的消息（同窗口内、且在这条之后）
                gi = msgs.index(m)
                replied = any(ID.is_self(x.get("sent_by") or "")
                              for x in msgs[gi + 1:gi + 4])
                asks.append(((m.get("sent_at") or "")[11:19],
                             ID.get_name(m.get("sent_by") or "")[:10],
                             t[:36], replied))
        total_asks += len(asks)
        if asks:
            print("=== {} （窗口内 @她/引用她 {} 条）===".format(label, len(asks)))
            for at, who, txt, replied in asks:
                print("   {} {} {!r} -> {}".format(
                    at, who, txt, "✅ 回了" if replied else "❌ **没回**"))
        else:
            print("=== {} ：窗口内没人 @她 ===".format(label))
    print()
    print("窗口（UTC >= {}）内 @她/引用她 共 {} 条".format(since, total_asks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
