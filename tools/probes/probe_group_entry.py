# -*- coding: utf-8 -*-
"""打印 config.json 里某个群的**完整条目**（features + tuning + persona），只读。

用法（容器内）：`python /app/probe_group_entry.py <cid>`
"""
import json
import sys

CFG = "/app/config/config.json"
DEFAULT = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    d = json.load(open(CFG, encoding="utf-8"))
    g = next((x for x in (d.get("groups") or []) if (x or {}).get("cid") == cid), None)
    if g is None:
        print("config.json 里没有这个群：", cid)
        return 1
    feats = g.get("features") or {}
    print("群 {}（{}）| 人设={} | 开关数={}".format(
        cid[:8], g.get("alias"), g.get("persona"), len(feats)))
    print("  开着的：", sorted([k for k, v in feats.items() if v]))
    print("  关着的：", sorted([k for k, v in feats.items() if not v]))
    for k in ("at_only", "quote_reply", "chat", "slash_cmds", "fortune"):
        print("  {:14s} -> {}".format(k, feats.get(k, "（没写=用代码默认）")))
    print("  tuning 覆盖：", json.dumps(g.get("tuning") or {}, ensure_ascii=False))
    print("  其它字段：", json.dumps(
        {k: v for k, v in g.items() if k not in ("features", "tuning")},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
