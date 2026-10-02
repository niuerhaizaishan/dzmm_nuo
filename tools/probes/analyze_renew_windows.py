# -*- coding: utf-8 -*-
"""P12-BH 第四轮：把续期失败聚成**窗口**（相邻失败间隔 <15 分钟算同一次），
打印每个窗口的时长/次数/前因/后果。

这样才看得出"这几天经常续期失效"到底是：
  · 一次坏了很久（前因是会话死、后果是人工/容器重启才好）——时长问题
  · 还是一天里零零碎碎坏很多次（前因各异）——触发问题
"""
import re
import sys
from datetime import datetime

LINE = re.compile(r"^(2026-\d\d-\d\d) (\d\d:\d\d:\d\d) (.*)$")
GAP_MIN = 15


def main():
    rows = []
    for ln in open("/tmp/rl.log", encoding="utf-8", errors="replace").read().splitlines():
        m = LINE.match(ln)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3)))
    fails, others = [], []
    for i, (d, t, body) in enumerate(rows):
        (fails if ("FAIL" in body or "未授权" in body or "SESSION_DEAD" in body) else others).append(i)
    failset = set(fails)
    windows, cur = [], None
    for i in fails:
        d, t, _ = rows[i]
        ts = datetime.strptime(d + " " + t, "%Y-%m-%d %H:%M:%S")
        if cur and (ts - cur["end"]).total_seconds() <= GAP_MIN * 60:
            cur["end"] = ts
            cur["n"] += 1
            cur["idx"].append(i)
        else:
            if cur:
                windows.append(cur)
            cur = {"start": ts, "end": ts, "n": 1, "idx": [i]}
    if cur:
        windows.append(cur)

    print("共 {} 个失败窗口（相邻失败 ≤{} 分钟算同一个）".format(len(windows), GAP_MIN))
    print()
    for w in sorted(windows, key=lambda x: -x["n"])[:14]:
        i0, i1 = w["idx"][0], w["idx"][-1]
        dur = (w["end"] - w["start"]).total_seconds() / 60
        print("=" * 76)
        print("{} → {} | 失败 {} 次 | 跨度 {:.0f} 分钟".format(
            w["start"].strftime("%m-%d %H:%M"), w["end"].strftime("%m-%d %H:%M"),
            w["n"], dur))
        print("  ── 之前 4 条 ──")
        for j in range(max(0, i0 - 4), i0):
            print("   " + "{} {}".format(rows[j][1], rows[j][2])[:130])
        print("  ── 之后 4 条 ──")
        for j in range(i1 + 1, min(len(rows), i1 + 5)):
            print("   " + "{} {}".format(rows[j][1], rows[j][2])[:130])
    return 0


if __name__ == "__main__":
    sys.exit(main())
