# -*- coding: utf-8 -*-
"""P12-BH：把几天的续期/体检日志按天+按事件分类，找"经常续期失效"的规律。

输入：/tmp/rl.log（renew_token.log）、/tmp/ch.log（check_health.log）、
      /tmp/mr.log（monitor_recover.log，可选）。
"""
import os
import re
import sys
from collections import Counter, defaultdict

FILES = [("renew", "/tmp/rl.log"), ("health", "/tmp/ch.log"), ("recover", "/tmp/mr.log")]

# 事件分类（按关键字，先匹配先归类）
EVENTS = [
    ("EMERGENCY", ("EMERGENCY:",)),
    ("SESSION_DEAD", ("SESSION_DEAD:",)),
    ("ESCALATE", ("ESCALATE:",)),
    ("OK", ("OK:",)),
    ("SKIP", ("SKIP:",)),
    ("NOTE", ("NOTE:",)),
    ("FAIL", ("FAIL:",)),
    ("UNAUTH", ("unauth", "Unauthorized", "未授权")),
    ("CAPTCHA", ("验证码", "captcha")),
    ("RELOGIN", ("headless 登录", "relogin", "密码重登")),
    ("TIMEOUT", ("Read timed out", "timeout", "超时")),
    ("PROXY", ("ProxyError", "proxy", "代理")),
]
DAY = re.compile(r"(2026-\d\d-\d\d)")


def main():
    for name, path in FILES:
        if not os.path.exists(path):
            print("== {} == （没有 {}）".format(name, path))
            continue
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        print("=" * 70)
        print("== {} == {} 行 | 首 {} | 末 {}".format(
            name, len(lines), lines[0][:60] if lines else "-",
            lines[-1][:60] if lines else "-"))
        per_day = defaultdict(Counter)
        days = Counter()
        for ln in lines:
            m = DAY.search(ln)
            d = m.group(1) if m else "无日期"
            days[d] += 1
            for label, keys in EVENTS:
                if any(k in ln for k in keys):
                    per_day[d][label] += 1
                    break
        print("按天的行数：{}".format(dict(sorted(days.items()))))
        labels = [e[0] for e in EVENTS]
        head = "  {:<14}".format("日期") + "".join("{:>12}".format(x) for x in labels)
        print(head)
        for d in sorted(per_day):
            row = "  {:<14}".format(d)
            for lb in labels:
                row += "{:>12}".format(per_day[d].get(lb, 0))
            print(row)
        # 关键事件的时间线（每个事件只列前 40 条）
        for want in ("SESSION_DEAD", "EMERGENCY", "ESCALATE", "UNAUTH", "CAPTCHA"):
            hits = [ln for ln in lines if any(k in ln for k in dict(EVENTS)[want])]
            if hits:
                print("-- {} 共 {} 条，前 25 条：".format(want, len(hits)))
                for ln in hits[:25]:
                    print("     " + ln[:150])
    return 0


if __name__ == "__main__":
    sys.exit(main())
