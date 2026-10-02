# -*- coding: utf-8 -*-
"""直接从 `config.json` 看某个群的生效覆盖 + 全局功能默认（只读，最可靠）。

用法（容器内）：`python /app/probe_cfg_raw.py <cid>`
"""
import json
import sys

sys.path.insert(0, "/app")

CFG = "/app/config/config.json"
CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else CID
    d = json.load(open(CFG, encoding="utf-8"))
    print("顶层 key：", sorted(d.keys()))
    groups = d.get("groups")
    print("groups 类型：", type(groups).__name__)
    g = None
    if isinstance(groups, dict):
        g = groups.get(cid)
    elif isinstance(groups, list):
        g = next((x for x in groups if (x or {}).get("cid") == cid), None)
    print("本群配置 =", json.dumps(g, ensure_ascii=False, indent=1)[:1200])
    print()
    print("personas 映射 =", json.dumps(d.get("personas"), ensure_ascii=False)[:300])
    print()
    from runtime import features as F
    on = (g or {}).get("features") or {}
    print("与'聊天/主动/长度'有关的功能（默认 → 本群）：")
    for feat in F.FEATURES:
        if any(w in feat.key for w in ("chat", "devil", "heat", "greet", "cold",
                                       "at_only", "quote", "memory", "insult", "fasao",
                                       "diaojiao", "scene", "room", "recap", "poem")):
            ov = on.get(feat.key)
            print("  {:18s} 默认={:<5} 本群={}  {}".format(
                feat.key, str(feat.default), "—" if ov is None else ov, feat.label))
    return 0


if __name__ == "__main__":
    sys.exit(main())
