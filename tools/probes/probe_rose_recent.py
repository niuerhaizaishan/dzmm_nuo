# -*- coding: utf-8 -*-
"""诊断：玫瑰房最近的消息 + 本群 `last_id` / 配置（看谁在说话、她有没有回）。"""
import sys

sys.path.insert(0, "/app")

import dzmm                                                    # noqa: E402
import state_store                                             # noqa: E402
from runtime import identity as ID                             # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"


def show(label, cid, n=20):
    members = dzmm.get_members(cid) or {}
    ID._name_cache.update(members)
    last = state_store.group_get(cid, "last_id", "")
    msgs = dzmm.get_messages(cid, n) or []
    print("=== {} ({}) 成员 {} 人 | last_id={} ===".format(
        label, cid[:8], len(members), (last or "")[:8]))
    for m in msgs:
        t = (m.get("content") or {}).get("text") or ""
        sb = m.get("sent_by") or ""
        who = "【我】" if ID.is_self(sb) else ID.get_name(sb)[:12]
        print("  {} {:<10} {:<14} {} 字 {} 行 {!r}".format(
            (m.get("sent_at") or "")[11:19], (m.get("message_id") or "")[:8], who,
            len(t), t.count("\n") + 1, t[:56].replace("\n", "⏎")))
    print()


def main():
    show("玫瑰房", ROSE)
    try:
        from runtime import config_store
        from admin import dashboard
        cfg = config_store.reload(dashboard._config_defaults())
        g = cfg.group(ROSE) if hasattr(cfg, "group") else None
        print("玫瑰房配置：", getattr(g, "features", None))
        print("bots:", [b.key for b in (cfg.bots or [])])
    except Exception as e:                                     # noqa: BLE001
        print("读配置失败：", str(e)[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
