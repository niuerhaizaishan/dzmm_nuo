# -*- coding: utf-8 -*-
"""P12-BE 探针：她**一条回复里点了几个人的名**（"一一批判"的量化）。

屋主 2026-09-30：
> "如果我回复她，她会读取之前很多人的记录，**一一批判回复**。
>  能设置成那种，我回复她，她只会读取我之前的和她的记录，来进行回复嘛"

判定：把她的每条回复里出现的**其他成员昵称**数出来（用群成员表 + 记忆模块的
`_extract_names` 同一套名字来源）。真人闲聊极少一条里点名两个人以上。

用法（容器内）：`python /app/probe_mention_spread.py <cid> [n]`
"""
import sys

sys.path.insert(0, "/app")

import dzmm                                                    # noqa: E402
from runtime import identity as ID                             # noqa: E402

DEFAULT_CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"          # 上班群（若瑾）


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CID
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        ID.register_bots(getattr(cfg, "bots", []) or [])
    except Exception:                                          # noqa: BLE001
        pass
    members = dzmm.get_members(cid) or {}
    ID._name_cache.update(members)
    names = [nm for nm in members.values() if nm and len(nm) >= 2]
    msgs = dzmm.get_messages_all(cid, max_count=n) or []
    mine = [((m.get("content") or {}).get("text") or "") for m in msgs
            if ID.is_self(m.get("sent_by") or "")]
    mine = [t for t in mine if t.strip()]
    print("群 {}：成员 {} 人 | 我们最近 {} 条".format(cid[:8], len(members), len(mine)))
    spread = []
    for t in mine:
        hit = [nm for nm in names if nm in t]
        spread.append(len(hit))
        print("  点名 {} 人 {} | {}".format(
            len(hit), [h[:6] for h in hit][:5], t.replace("\n", "⏎")[:70]))
    if spread:
        n2 = len(spread)
        print()
        print("一条回复点到的**其他人数**：均值 {:.1f} / 最多 {} | ≥2 人的占 {:.0%}".format(
            sum(spread) / n2, max(spread), sum(1 for x in spread if x >= 2) / n2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
