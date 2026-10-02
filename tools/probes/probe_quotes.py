# -*- coding: utf-8 -*-
"""P12-BM 诊断：茶水间的"引用回复她"能不能被识别（只看，不改任何东西）。

屋主 2026-09-30 17:2x："**茶水间的小小糯回复她发的消息不能触发她啊？你先别改，你看一下给我方案。**"

要看的三件事：
  ① 本群 `at_only` / `quote_reply` 两个开关的实际状态（配置里有没有写、默认值是什么）；
  ② 平台到底有没有把"被引用的那条"塞进入站消息（`content.reference`）——
     账号通道（茶水间没有 Bot）是不是也带；
  ③ 引用的是不是**我们**发的（`sent_by` 是否 `is_self`）。

用法（容器内）：`python /app/probe_quotes.py <cid> [n]`
"""
import json
import sys

sys.path.insert(0, "/app")

import bot                                                     # noqa: E402
import dzmm                                                    # noqa: E402
from runtime import identity as ID                             # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
WORK = "f4ec4c21-fb48-4be2-9972-e4128f65923b"      # 对照组：那边 quote_reply=true


def _features(cid: str) -> dict:
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        for g in (getattr(cfg, "groups", []) or []):
            if getattr(g, "cid", "") == cid:
                return {"features": dict(getattr(g, "features", {}) or {}),
                        "persona": getattr(g, "persona", "")}
    except Exception as e:                                     # noqa: BLE001
        return {"error": str(e)[:80]}
    return {}


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else TEA
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        ID.register_bots(getattr(cfg, "bots", []) or [])
    except Exception:                                          # noqa: BLE001
        cfg = None
    feats = _features(cid)
    print("群 = {} ({})".format(cid[:8], "茶水间" if cid == TEA else
                              ("上班群" if cid == WORK else "其它")))
    print("人设 = {} | features 里写的 = {}".format(
        feats.get("persona", "?"), json.dumps(feats.get("features", {}), ensure_ascii=False)))
    # 生效值（含默认）
    from runtime import features as F
    for key in ("at_only", "quote_reply", "chat", "slash_cmds"):
        f = F.get(key)
        ov = (feats.get("features") or {}).get(key, None)
        print("   {:14s} 默认={:<6} 覆盖={:<6} 生效={}".format(
            key, str(f.default), str(ov), str(F.on(cid, key))))
    print()
    ID._name_cache.update(dzmm.get_members(cid) or {})
    msgs = dzmm.get_messages_all(cid, max_count=n) or []
    quoted = [m for m in msgs if bot._ref_of(m) .get("sent_by")]
    print("最近 {} 条消息里，带「引用」的：{} 条".format(len(msgs), len(quoted)))
    for m in quoted[-12:]:
        ref = bot._ref_of(m)
        who = ID.get_name(m.get("sent_by") or "")[:12]
        rwho = "【我】" if ID.is_self(ref.get("sent_by") or "") else \
            ID.get_name(ref.get("sent_by") or "")[:12]
        print("  {} {} 引用了 {} | 她发的吗={} | 引用原文={!r} | 正文={!r}".format(
            (m.get("sent_at") or "")[11:19], who, rwho,
            bot._quotes_self(m), ref.get("text", "")[:24],
            ((m.get("content") or {}).get("text") or "")[:30]))
    if not quoted:
        print("  （这段时间里没人引用过任何消息 —— 无法判断账号通道带不带 reference）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
