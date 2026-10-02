# -*- coding: utf-8 -*-
"""P12-BH 第五轮：失败窗口**内部**到底试过哪些手段（归一化后按出现次数）。

关心三件事：
  1. 窗口里有没有"浏览器续期"（P12-AT 之后才有的手段）？
  2. 恢复那一刻是**哪条路**把它救回来的（OK/SKIP/浏览器成功/人工）？
  3. 窗口期间 bot 还有没有在别处成功读过消息（区分"整个账号死"vs"只是续期接口不认"）。
"""
import re
import sys
from collections import Counter
from datetime import datetime

LINE = re.compile(r"^(2026-\d\d-\d\d) (\d\d:\d\d:\d\d) (.*)$")
GAP_MIN = 15
WANT = ("2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30")


def norm(s: str) -> str:
    return re.sub(r"\d", "#", s)[:64]


def main():
    rows = []
    for ln in open("/tmp/rl.log", encoding="utf-8", errors="replace").read().splitlines():
        m = LINE.match(ln)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3)))
    fails = [i for i, (d, t, b) in enumerate(rows)
             if ("FAIL" in b or "未授权" in b or "SESSION_DEAD" in b)]
    windows, cur = [], None
    for i in fails:
        d, t, _ = rows[i]
        ts = datetime.strptime(d + " " + t, "%Y-%m-%d %H:%M:%S")
        if cur and (ts - cur["end"]).total_seconds() <= GAP_MIN * 60:
            cur["end"], cur["n"] = ts, cur["n"] + 1
            cur["idx"].append(i)
        else:
            if cur:
                windows.append(cur)
            cur = {"start": ts, "end": ts, "n": 1, "idx": [i]}
    if cur:
        windows.append(cur)

    for w in sorted(windows, key=lambda x: -x["n"])[:6]:
        i0, i1 = w["idx"][0], w["idx"][-1]
        inner = [rows[j][2] for j in range(i0, i1 + 1)]
        print("=" * 76)
        print("{} → {} | {} 次 | {:.0f} 分钟".format(
            w["start"].strftime("%m-%d %H:%M"), w["end"].strftime("%m-%d %H:%M"),
            w["n"], (w["end"] - w["start"]).total_seconds() / 60))
        print("  窗口内的消息形态：")
        for shape, n in Counter(norm(x) for x in inner).most_common(8):
            print("    {:>4}× {}".format(n, shape))
        print("  窗口内出现过的'手段'关键字：")
        for k in ("浏览器续期", "browser", "重登", "headless", "noVNC", "验证码", "退避"):
            c = sum(1 for x in inner if k in x)
            if c:
                print("    {:>4}× {}".format(c, k))
        print("  恢复前后 6 条：")
        for j in range(max(0, i1 - 2), min(len(rows), i1 + 7)):
            mark = "  >>>" if j == i1 else "     "
            print("{} {} {}".format(mark, rows[j][1], rows[j][2])[:140])
    return 0


if __name__ == "__main__":
    sys.exit(main())
