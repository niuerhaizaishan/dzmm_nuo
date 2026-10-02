# -*- coding: utf-8 -*-
"""P12-BM 验收（离线）：拿**群里真实那条"引用她"的消息**走一遍闸门，确认现在会放行。

闸门就是 `bot.py` 里这段（at_only 群）：
    _by_at    = is_at_me(t, cid)
    _by_quote = (not _by_at) and _feat_on(cid, "quote_reply") and _quotes_self(m)
    if not (_by_at or _by_quote): continue

用法（容器内）：`python /app/verify_quote_gate.py [cid]`
"""
import sys
import time

sys.path.insert(0, "/app")

import bot                                                     # noqa: E402
import dzmm                                                    # noqa: E402
from runtime import identity as ID                             # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"


def gate(cid, m):
    t = ((m.get("content") or {}).get("text") or "").strip()
    by_at = bot.is_at_me(t, cid)
    by_quote = (not by_at) and bot._feat_on(cid, "quote_reply") and bot._quotes_self(m)
    return by_at, by_quote, (by_at or by_quote)


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else TEA
    ID._name_cache.update(dzmm.get_members(cid) or {})
    print("群 =", cid[:8], "| quote_reply 生效 =", bot._feat_on(cid, "quote_reply"),
          "| at_only 生效 =", bot._feat_on(cid, "at_only"))
    print()
    msgs = dzmm.get_messages_all(cid, max_count=300) or []
    quoted_self = [m for m in msgs if bot._quotes_self(m)]
    print("最近 {} 条里「引用她」的：{} 条".format(len(msgs), len(quoted_self)))
    ok = 0
    for m in quoted_self[-5:]:
        by_at, by_quote, passed = gate(cid, m)
        ok += 1 if passed else 0
        print("  {} {} 正文={!r} → 被@={} 被引用={} **放行={}**".format(
            (m.get("sent_at") or "")[11:19],
            ID.get_name(m.get("sent_by") or "")[:10],
            ((m.get("content") or {}).get("text") or "")[:26], by_at, by_quote, passed))
    print()
    # 反向：引用**别人**的消息不该被放行
    others = [m for m in msgs if bot._ref_of(m).get("sent_by")
              and not bot._quotes_self(m)]
    print("对照（引用别人的消息，抽查 3 条，应当**不放行**）：")
    for m in others[:3]:
        by_at, by_quote, passed = gate(cid, m)
        print("  {} {} → 放行={}（应为 False）".format(
            (m.get("sent_at") or "")[11:19],
            ID.get_name(m.get("sent_by") or "")[:10], passed))
    print()
    # 合成一条：没 @、引用她
    me = ID.NUO_UID
    synth = {"message_id": "synth-quote", "sent_by": "u-x",
             "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
             "content": {"type": "text", "text": "这条是引用你说的哦",
                         "reference": {"id": "x", "sentBy": me,
                                       "content": {"type": "text", "text": "她之前说的"}}}}
    print("合成用例（没 @、引用她）：放行 =", gate(cid, synth)[2])
    print()
    print("==> {}".format(
        "✅ 闸门已放行「引用她」" if (quoted_self and ok == len(quoted_self[-5:]))
        else "⚠️ 见上面明细"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
