"""顶层茶水间「噪音内容」取证（只读）：打卡 / 摸鱼躲藏 / 记忆考核 长什么样。

屋主 2026-09-29 改口径："别忽略其他 bot 了，忽略**打卡、摸鱼躲藏、记忆考核**内容。"

要过滤就得先看清它们：
  · 谁发的（是不是固定几个账号/机器人）？
  · 文本有什么稳定特征（前缀？emoji？关键词？）？
  · 会不会 @ 到小小糯（会的话还得管住 @ 触发那条路）？
  · 占窗口里多大比例（recap 一条窗口里有多少条是这种噪音）？

用法（容器内）：
    python /app/probe_tea_noise.py
    python /app/probe_tea_noise.py --limit 100
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import dzmm                                                    # noqa: E402
from runtime.identity import get_name, _load_member_names, is_self   # noqa: E402

CID = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
KW = ("打卡", "摸鱼", "躲藏", "记忆", "考核")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    a = ap.parse_args()
    a.limit = max(1, min(100, a.limit))

    try:
        _load_member_names(CID)
    except Exception as e:                                     # noqa: BLE001
        print("（成员名预载失败：{}）".format(str(e)[:60]))

    msgs = dzmm.get_messages(CID, limit=a.limit) or []
    print("拉到 {} 条".format(len(msgs)))
    print()
    print("=== 全部消息（[谁] 正文前 70 字）===")
    hit, senders = [], {}
    for m in msgs:
        uid = str(m.get("sent_by") or "")
        txt = ((m.get("content") or {}).get("text") or "").strip()
        ctype = (m.get("content") or {}).get("type")
        who = "（我们）" if is_self(uid) else get_name(uid)
        senders[who] = senders.get(who, 0) + 1
        mark = ""
        if any(k in txt for k in KW):
            mark = "   <== 命中噪音关键词"
            hit.append((who, txt))
        print("  [{}|{}|{}] {}{}".format(who[:12], uid[:8], ctype, txt[:70].replace("\n", " "), mark))

    print()
    print("=== 命中 {} 条 / 共 {} 条（占 {:.0%}）===".format(
        len(hit), len(msgs), (len(hit) / len(msgs)) if msgs else 0))
    for who, txt in hit[:12]:
        print("  [{}] {}".format(who, txt[:100].replace("\n", " ⏎ ")))
    print()
    print("=== 发言者分布 ===")
    for who, n in sorted(senders.items(), key=lambda kv: -kv[1]):
        print("  {:<16s} {}".format(who, n))
    print()
    # 这些噪音里有没有 @ 我们的
    me_hits = [t for _w, t in hit if "@" in t]
    print("命中的噪音里带 @ 的：{} 条".format(len(me_hits)))
    for t in me_hits[:5]:
        print("   ", t[:100].replace("\n", " "))
    print()
    print("done")


if __name__ == "__main__":
    sys.exit(main())
