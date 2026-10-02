# -*- coding: utf-8 -*-
"""P12-BH 第三轮：每次"续期失效爆发"是**怎么开始的**（爆发前 12 行的上下文）。

看的是：爆发前一条是什么？"OK/SKIP/浏览器续期成功/撞验证码" —— 不同答案指向不同根因：
  · 前一条是 OK/浏览器成功 ⇒ token 刚拿到就失效（轮换/多端登录冲突）
  · 前一条是 撞验证码        ⇒ 风控，不是会话问题
  · 前一条是 长时间没有日志  ⇒ 进程/容器那段时间不在（重建/睡眠）
"""
import re
import sys
from collections import Counter

DAY = re.compile(r"^(2026-\d\d-\d\d) (\d\d:\d\d:\d\d) ")


def main():
    lines = open("/tmp/rl.log", encoding="utf-8", errors="replace").read().splitlines()
    # 找"爆发"：同一天里 FAIL 连续出现的段落
    bursts = []
    cur = None
    for i, ln in enumerate(lines):
        isfail = "FAIL" in ln or "未授权" in ln
        m = DAY.match(ln)
        if isfail:
            if cur is None:
                cur = {"start": i, "day": m.group(1) if m else "?", "n": 1}
            else:
                cur["n"] += 1
        else:
            if cur and cur["n"] >= 5:        # 连续 ≥5 条失败算一次爆发
                bursts.append(cur)
            cur = None
    if cur and cur["n"] >= 5:
        bursts.append(cur)
    print("共 {} 次爆发（连续 ≥5 条失败）".format(len(bursts)))
    print()
    for b in bursts:
        i = b["start"]
        print("=" * 74)
        print("{}  连续 {} 条失败 | 从第 {} 行开始".format(b["day"], b["n"], i + 1))
        for ln in lines[max(0, i - 12):i]:
            print("   [前] " + ln[:130])
        print("   >>> 爆发第一条：" + lines[i][:130])
    # 爆发前一条的类型统计
    kinds = Counter()
    for b in bursts:
        prev = lines[b["start"] - 1] if b["start"] > 0 else ""
        for k in ("OK:", "SKIP:", "NOTE:", "浏览器续期成功", "撞验证码", "SKIP: token 未变化"):
            if k in prev:
                kinds[k] += 1
                break
        else:
            kinds["（其它/空行）"] += 1
    print()
    print("爆发前一条的类型：", dict(kinds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
