# -*- coding: utf-8 -*-
"""P12-BN 诊断：她叫"姐姐"的对象，我们认成什么性别？（只看，不改）

屋主 2026-09-30 17:3x："**而且她有时候还是叫男生姐姐**，你看看什么问题先，给我方案"

要分清四种可能：
  ① **性别没认出来**（平台没填/查不到 ⇒ "未知"）→ 模型按群里的姐姐习惯默认叫姐姐；
  ② **认错**（档案/平台写的是 female，其实是男的）；
  ③ **叫错人**（她在回答 A，却把 B 的称呼安上去 ⇒ P12-AP 那类"回错人"）；
  ④ **镜像了群里的语气**（群里别人互称"姐姐/顾影姐姐"，她跟着学）。

做法：扫她最近的消息，凡是含"姐姐"的，往前找**最近一条别人的消息**（大概率就是触发它的人），
打印那个人的性别（档案 + 平台两路），以及她那句话里的上下文。

用法（容器内）：`python /app/probe_wrong_sister.py <cid> [n]`
"""
import re
import sys

sys.path.insert(0, "/app")

import bot                                                     # noqa: E402
import dzmm                                                    # noqa: E402
from runtime import identity as ID                             # noqa: E402
from runtime.members import _gender_label, _gender_of           # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
SISTER = ("姐姐", "姐", "姊妹")


def _platform_gender(cid, uid) -> str:
    """平台原始 gender 字段（不经归一化）——看它到底写了什么。"""
    try:
        u = dzmm.get_user(cid, uid) or {}
        return str(u.get("gender"))
    except Exception as e:                                     # noqa: BLE001
        return "查询失败:" + str(e)[:30]


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else TEA
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        ID.register_bots(getattr(cfg, "bots", []) or [])
    except Exception:                                          # noqa: BLE001
        pass
    ID._name_cache.update(dzmm.get_members(cid) or {})
    msgs = dzmm.get_messages_all(cid, max_count=n) or []
    print("群 {} | 最近 {} 条".format(cid[:8], len(msgs)))
    print()
    hits, stat = [], {}
    for i, m in enumerate(msgs):
        t = ((m.get("content") or {}).get("text") or "")
        if not ID.is_self(m.get("sent_by") or "") or not any(k in t for k in SISTER):
            continue
        trig = next((x for x in reversed(msgs[:i])
                     if not ID.is_self(x.get("sent_by") or "")), None)
        uid = (trig or {}).get("sent_by") or ""
        lab = _gender_label(uid) if uid else "?"
        got = _gender_of(uid, cid) if uid else "?"
        raw = _platform_gender(cid, uid) if uid else "?"
        stat[got] = stat.get(got, 0) + 1
        hits.append((m, trig, lab, got, raw, t))
    print("含姐姐的回复共 {} 条；按触发人的识别性别统计：{}".format(len(hits), stat))
    print()
    for m, trig, lab, got, raw, t in hits[-14:]:
        trig_txt = ((trig or {}).get("content") or {}).get("text") or ""
        print("── {} 她: {!r}".format((m.get("sent_at") or "")[11:19], t[:100]))
        print("   触发人: {} | 档案={} 识别={} 平台gender={}".format(
            ID.get_name((trig or {}).get("sent_by") or "")[:12] or "（没找到）",
            lab, got, raw))
        print("   触发原文: {!r}".format(trig_txt[:70]))
    print()
    print("==> 判读：识别=男生/未知 却出现姐姐 ⇒ ①/④；识别=女生但本人是男的 ⇒ ②；")
    print("          句子里姐姐指的不是触发人 ⇒ ③。（详见方案文档 P12-BN）")
    _freq(cid, msgs)

def _trigger_of(msgs, i, cid):
    """她这条回复是**谁**触发的：往前找最近一条 **@她 或 引用她** 的消息。

    ⚠️ 第一版用"上一条别人的消息"当触发人 —— 那条常常不是对她说的话，
    于是把"顾影（男）被叫哥哥"这类**正确**case统计成了错误。判据必须与闸门一致。
    """
    for x in reversed(msgs[:i]):
        if ID.is_self(x.get("sent_by") or ""):
            continue
        t = ((x.get("content") or {}).get("text") or "")
        if bot.is_at_me(t, cid) or bot._quotes_self(x):
            return x
    return None


def _freq(cid, msgs):
    """她所有回复里：带性别称呼的、以及认错的（对着**触发人**的性别看）。"""
    total = wrong = right = has_addr = 0
    bad = []
    for i, m in enumerate(msgs):
        if not ID.is_self(m.get("sent_by") or ""):
            continue
        t = ((m.get("content") or {}).get("text") or "")
        if not t.strip():
            continue
        total += 1
        trig = _trigger_of(msgs, i, cid)
        uid = (trig or {}).get("sent_by") or ""
        if not uid:
            continue
        name = ID.get_name(uid)
        short = (name or "")[:2]
        g = _gender_of(uid, cid)
        said_sis = ("姐姐" in t) or bool(short and (short + "姐" in t))
        said_bro = ("哥哥" in t) or bool(short and (short + "哥" in t))
        if not (said_sis or said_bro):
            continue
        has_addr += 1
        kind = "姐姐" if said_sis else "哥哥"
        if (g == "男生" and said_sis) or (g == "女生" and said_bro):
            wrong += 1
            bad.append(((m.get("sent_at") or "")[11:19], name[:10], g, kind, t[:56]))
        else:
            right += 1
    print()
    print("=== 频率统计（她共 {} 条回复）===".format(total))
    print("  带性别称呼的：{} 条（对 {} / 错 {}）".format(has_addr, right, wrong))
    if has_addr:
        print("  其中认错的比例：{:.0%}".format(wrong / float(has_addr)))
    for row in bad[-10:]:
        print("   ❌ {} 对 {}（识别={}）叫了 {}: {!r}".format(*row))
    return {"total": total, "has_addr": has_addr, "right": right, "wrong": wrong}


if __name__ == "__main__":
    sys.exit(main())
