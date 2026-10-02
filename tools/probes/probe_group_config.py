# -*- coding: utf-8 -*-
"""看某个群**生效的配置**：人设、功能开关、参数覆盖（只读）。

用法（容器内）：`python /app/probe_group_config.py <cid>`
"""
import sys

sys.path.insert(0, "/app")

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"          # 上班群（若瑾）


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else CID
    from admin import dashboard
    import personas as P
    from runtime import config_store, features as F, tuning as T

    cfg = config_store.reload(dashboard._config_defaults())
    g = None
    for x in (getattr(cfg, "groups", []) or []):
        if getattr(x, "cid", "") == cid:
            g = x
            break
    print("群 =", cid)
    print("人设 =", P.persona_key_for(cid) if hasattr(P, "persona_key_for") else "?")
    print("自称 =", P.me(cid))
    print()
    print("功能覆盖（只列写过的）：")
    feats = getattr(g, "features", {}) if g else {}
    for k, v in sorted((feats or {}).items()):
        print("  {:22s} {}".format(k, v))
    print()
    print("参数覆盖：")
    tune = getattr(g, "tuning", {}) if g else {}
    for k, v in sorted((tune or {}).items()):
        print("  {:28s} {}".format(k, v))
    print()
    print("全群默认（未覆盖时生效）里和'聊天/长度/主动'有关的：")
    keys = {}
    for feat in F.FEATURES:
        for p in feat.params:
            keys[p.key] = (p.default, p.label)
    for k in sorted(keys):
        if any(w in k for w in ("chat", "reply", "room", "cold", "devil", "heat",
                                "greet", "insult", "fasao")):
            print("  {:28s} 默认={!r}  {}{}".format(
                k, keys[k][0], keys[k][1],
                "  ← 本群覆盖=" + repr(tune[k]) if k in (tune or {}) else ""))
    print()
    print("话术表里和'招呼/欢迎'有关的 key：")
    for k in sorted(P.PHRASES_LABELS if hasattr(P, "PHRASES_LABELS") else {}):
        if any(w in k for w in ("welcome", "greet", "intro", "join")):
            print("  {:22s} {}".format(k, P.PHRASES_LABELS.get(k)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
