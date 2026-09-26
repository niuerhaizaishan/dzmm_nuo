# -*- coding: utf-8 -*-
"""回复延迟体检：用**平台时间戳**算「群友发言 → 机器人回复」的真实延迟。

为什么不用 bot 日志：日志只能看到"机器人**开始处理** → 发出"，
中间的**排队等待**（被别的群的长任务堵住的那些秒）在日志里是隐形的 ——
而那正是"感觉慢"的主要来源（P12-I）。

用法（在服务器上跑，一次性容器）：

    cd /opt/dzmm && docker run --rm --entrypoint python \\
      -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
      -v /opt/dzmm:/app:ro -v /tmp/latency_report.py:/r.py:ro -w /tmp \\
      --env-file .env dzmm-bot:latest /r.py [小时数]

参数：小时数默认 12。只读，不往任何群发消息。
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta

HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 12
sys.path.insert(0, "/app")

import bot                                              # noqa: E402
from dzmm import get_messages_since                     # noqa: E402
from runtime.identity import is_self, get_name          # noqa: E402

GROUPS = [("bd7fe6a4-0065-4551-ad3d-94116dba8f9c", "认证群"),
          ("4a56be71-9ca6-4785-b45f-18725d187f1a", "玫瑰房")]
TZ = timedelta(hours=8)          # 平台时间是 UTC，日志/人看的是本地

# 按回复开头认"哪一类"，用来区分"闲聊"和"长任务"
KINDS = [("📮", "回顾"), ("📜", "写诗"), ("🌹 今日星象", "星象"), ("🔮", "算命"),
         ("🎲", "骰子"), ("🔗", "调教"), ("🎬", "公演/剧情"), ("📇", "档案"),
         ("📝", "记忆"), ("🎭", "发骚/场景"), ("😈", "吐槽"), ("排", "排行")]


def kind_of(txt):
    head = txt.lstrip("🤖 ")
    for pre, name in KINDS:
        if head.startswith(pre) or pre in txt[:14]:
            return name
    return "普通回复"


rows = []
for cid, name in GROUPS:
    since = (datetime.utcnow() - timedelta(hours=HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    msgs = get_messages_since(cid, since, max_count=900) or []
    last = None      # (时间, 谁, 说了什么)
    for m in msgs:
        c = m.get("content") or {}
        txt = (c.get("text") or "").strip()
        ts = m.get("sent_at") or ""
        if not txt or len(ts) < 19:
            continue
        try:
            t = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S") + TZ
        except Exception:                                # noqa: BLE001
            continue
        if is_self(m.get("sent_by")):
            if last:
                dt = (t - last[0]).total_seconds()
                if 0 <= dt <= 600:      # 超过 10 分钟不算"在回这句"
                    rows.append((name, dt, kind_of(txt), last[1], last[2][:34], txt[:34]))
            last = None
        elif c.get("type") != "system" and "加入了群聊" not in txt:
            last = (t, get_name(m.get("sent_by") or ""), txt)

print("=== 最近 {} 小时：群友发言 → 机器人回复（平台时间戳）===".format(HOURS))
agg = defaultdict(list)
for name, dt, k, who, q, a in rows:
    agg[(name, k)].append(dt)
for (name, k), xs in sorted(agg.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
    xs.sort()
    med = xs[len(xs) // 2]
    p90 = xs[min(len(xs) - 1, int(len(xs) * 0.9))]
    print("  {:<4} {:<8} n={:<3} 中位 {:>3.0f}s p90 {:>3.0f}s 最大 {:>4.0f}s "
          "| >10s 的 {} 组".format(name, k, len(xs), med, p90, xs[-1],
                                   sum(1 for x in xs if x > 10)))
if rows:
    tot = sorted(r[1] for r in rows)
    print("全部 n={} 中位 {:.0f}s p90 {:.0f}s 最大 {:.0f}s | >10s 占 {:.0f}%".format(
        len(tot), tot[len(tot) // 2], tot[min(len(tot) - 1, int(len(tot) * 0.9))],
        tot[-1], 100.0 * sum(1 for x in tot if x > 10) / len(tot)))
    print()
    print("=== 最慢 8 组 ===")
    for name, dt, k, who, q, a in sorted(rows, key=lambda r: -r[1])[:8]:
        print("[{:>3.0f}s|{}] {:<4} {}: {}  →  {}".format(dt, k, name, who, q, a))
else:
    print("（窗口内没有配得上对的问答）")
