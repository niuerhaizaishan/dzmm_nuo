# -*- coding: utf-8 -*-
"""P12-BH 第二轮：FAIL 的**具体形态**、CAPTCHA/UNAUTH 时间线、体检告警按天统计。"""
import re
import sys
from collections import Counter, defaultdict

DAY = re.compile(r"(2026-\d\d-\d\d)")


def norm(msg: str) -> str:
    m = re.sub(r"\d", "#", msg)
    return m[:70]


def main():
    rl = open("/tmp/rl.log", encoding="utf-8", errors="replace").read().splitlines()
    print("=" * 74)
    print("① FAIL 的形态分布（去掉数字后归并）")
    shapes = Counter()
    shapes_by_day = defaultdict(Counter)
    for ln in rl:
        if "FAIL" not in ln:
            continue
        m = DAY.search(ln)
        d = m.group(1) if m else "?"
        body = ln.split("FAIL", 1)[1].lstrip(": ").strip()
        s = norm(body)
        shapes[s] += 1
        shapes_by_day[d][s] += 1
    for s, n in shapes.most_common(12):
        print("  {:>5}  {}".format(n, s))
    print()
    print("② 三个爆炸日的 FAIL 形态")
    for d in ("2026-09-23", "2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30"):
        top = shapes_by_day.get(d) or {}
        if not top:
            continue
        print("  {}: {}".format(d, " | ".join(
            "{}×{}".format(v, k[:46]) for k, v in Counter(top).most_common(3))))

    print()
    print("=" * 74)
    print("③ CAPTCHA / UNAUTH 时间线（全部）")
    for key in ("验证码", "captcha", "unauth", "Unauthorized"):
        hits = [ln for ln in rl if key in ln]
        if hits:
            print("-- 含 {!r}：{} 条（前 12）".format(key, len(hits)))
            for ln in hits[:12]:
                print("     " + ln[:140])

    print()
    print("=" * 74)
    print("④ 体检（check_health.log）告警按天")
    try:
        ch = open("/tmp/ch.log", encoding="utf-8", errors="replace").read().splitlines()
    except Exception as e:                                     # noqa: BLE001
        print("  读不到：", e)
        return 0
    per = defaultdict(Counter)
    for ln in ch:
        m = DAY.search(ln)
        d = m.group(1) if m else "?"
        low = ln
        kind = None
        for k in ("session_unverified", "renew_loop", "会话未验证", "停摆", "续期循环",
                  "进程", "token", "告警", "ALERT", "正常"):
            if k in low:
                kind = k
                break
        if kind:
            per[d][kind] += 1
    labels = ["session_unverified", "renew_loop", "会话未验证", "停摆", "续期循环",
              "进程", "token", "告警", "ALERT", "正常"]
    print("  {:<12}".format("日期") + "".join("{:>10}".format(x[:8]) for x in labels))
    for d in sorted(per):
        row = "  {:<12}".format(d)
        for lb in labels:
            row += "{:>10}".format(per[d].get(lb, 0))
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
