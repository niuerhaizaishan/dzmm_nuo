"""线上实测：**三个群里我们自己发的话到底多长**（P12-AG 先量再改）。

屋主 2026-09-27："我觉得妹子群回复的长度还是有点短"。
感觉要有数字支撑，否则改完还是靠感觉争。这个探针只读，不改任何东西：

  · 每个群拉最近 N 条，挑出**我们自己发的**（`self_uids()`：3 个 Bot + 侧车账号）；
  · 去掉开头的 "🤖 " 前缀后统计字数：条数 / 均值 / 中位数 / p90 / 最长；
  · 单独统计**紧跟 @ 之后**的那条（= @ 问答那条路），和其余分开看；
  · 打印最后几条原文，便于人眼判断"短"是短在哪。

用法（容器内）：
    python /app/probe_reply_lengths.py            # 默认每群 120 条
    python /app/probe_reply_lengths.py --limit 200
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import dzmm                                                    # noqa: E402

GROUPS = (
    ("bd7fe6a4-0065-4551-ad3d-94116dba8f9c", "认证妹子群/小小糯"),
    ("f4ec4c21-fb48-4be2-9972-e4128f65923b", "上班群/若瑾"),
    ("4a56be71-9ca6-4785-b45f-18725d187f1a", "玫瑰房/薇丝"),
)


def _my_uids():
    """一次性进程里 `self_uids()` 只认得 env 那两个 —— Bot 的 uid 要**从配置里拿**。

    （2026-09-27 实测：不这么做的话，上班群/玫瑰房的消息会被当成群友的，
    于是"我们自己发了多长"直接统计成 0 条。）
    """
    from runtime.identity import self_uids, NUO_UID
    uids = set(self_uids()) | {NUO_UID}
    try:
        import bot
        for b in bot._CFG.bots:
            if b.uid:
                uids.add(b.uid)
    except Exception as e:                                     # noqa: BLE001
        print("（读配置里的 Bot uid 失败：" + str(e)[:60] + "）")
    return {u for u in uids if u}


def _clean(t: str) -> str:
    t = (t or "").strip()
    return t[2:].strip() if t.startswith("🤖 ") else t


def _stats(vals):
    if not vals:
        return "（没有样本）"
    s = sorted(vals)
    n = len(s)
    return "n={} 均值={:.0f} 中位={} p90={} 最长={} 最短={}".format(
        n, sum(s) / n, s[n // 2], s[min(n - 1, int(n * 0.9))], s[-1], s[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100, help="平台上限就是 100")
    ap.add_argument("--show", type=int, default=4, help="每群打印最后几条原文")
    a = ap.parse_args()
    a.limit = max(1, min(100, int(a.limit)))          # 平台校验：必须 ≤ 100

    me = _my_uids()
    print("我的身份 {} 个：{}".format(len(me), ", ".join(u[:8] for u in sorted(me))))

    for cid, label in GROUPS:
        try:
            msgs = dzmm.get_messages(cid, limit=a.limit) or []
        except Exception as e:                                 # noqa: BLE001
            print("\n【{}】拉取失败：{}".format(label, str(e)[:60]))
            continue
        mine, at_replies, samples = [], [], []
        prev_at = False
        for m in msgs:
            text = ((m.get("content") or {}).get("text") or "").strip()
            uid = m.get("sent_by", "")
            if uid in me:
                body = _clean(text)
                if body:
                    mine.append(len(body))
                    if prev_at:
                        at_replies.append(len(body))
                    samples.append((len(body), body))
            prev_at = ("@" in text) and (uid not in me)

        print()
        print("=" * 70)
        print("【{}】最近 {} 条".format(label, len(msgs)))
        print("=" * 70)
        print("  我们发的全部：", _stats(mine))
        print("  其中紧跟 @ 之后的（= @ 问答那条路）：", _stats(at_replies))
        short = [n for n in mine if n < 120]
        print("  短于 120 字的占比：{}/{} = {:.0%}".format(
            len(short), len(mine), (len(short) / len(mine)) if mine else 0))
        if samples:
            print("  最后 {} 条原文：".format(min(a.show, len(samples))))
            for n, body in samples[-a.show:]:
                print("    [{} 字] {}".format(n, body.replace("\n", "⏎")[:150]))
    print()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
