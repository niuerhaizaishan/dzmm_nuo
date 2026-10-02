# -*- coding: utf-8 -*-
"""P12-BJ 决策工具：**要不要自动换域名？** —— 读攒下来的墙窗口记录来判断。

背景：`renew_token` 在每个坏窗口的第一轮会探一遍 12 个镜像域名，并把结果落成
`data/wall_probes.jsonl`（一行一个窗口）。攒够几个窗口之后用这个工具看：

    · 一共有几次墙窗口、各自持续多久（从 `renew_token.log` 的坏窗口算）；
    · **墙期间当前节点什么样、镜像域名里有没有能用的**（`home=200 且 token=token-ok`）；
    · 结论建议：如果"本节点不可用 + 至少一个镜像可用"反复出现 ⇒ 自动换域名值得做；
      如果墙期间**所有**域名都一样不可用 ⇒ 换了也没用，别做（省一次重启风险）。

用法：
    容器内  `python /app/analyze_wall_windows.py`
    本地    `python tools/analyze_wall_windows.py <wall_probes.jsonl> [renew_token.log]`
"""
import json
import os
import sys
from collections import Counter

DEFAULT_JSONL = "/app/data/wall_probes.jsonl"
DEFAULT_LOG = "/app/renew_token.log"


def load_probes(path: str) -> list:
    rows = []
    try:
        for ln in open(path, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:                                  # noqa: BLE001
                continue
    except FileNotFoundError:
        pass
    return rows


def summarize(rows: list) -> dict:
    """纯函数：给一组墙窗口记录，算"镜像可用性"统计（便于单测）。"""
    n = len(rows)
    cur_bad = 0            # 本节点在墙里的次数
    any_alt_ok = 0         # 有镜像可用（至少一个 token-ok）的次数
    all_alt_bad = 0        # 所有镜像也不可用的次数
    per_host = Counter()   # 每个域名"可用"的次数
    for r in rows:
        home = str(r.get("origin_home") or "")
        if home not in ("ok", "200"):
            cur_bad += 1
        nodes = r.get("nodes") or []
        ok = [x["host"] for x in nodes
              if str(x.get("home")) == "200" and x.get("token") == "token-ok"]
        for h in ok:
            per_host[h] += 1
        if ok:
            any_alt_ok += 1
        elif nodes:
            all_alt_bad += 1
    return {"windows": n, "origin_bad": cur_bad, "alt_ok": any_alt_ok,
            "all_alt_bad": all_alt_bad, "per_host": per_host}


def main() -> int:
    jsonl = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_JSONL
    logp = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LOG
    rows = load_probes(jsonl)
    print("墙窗口记录：{} 条（{}）".format(len(rows), jsonl))
    if not rows:
        print("（还没有墙窗口记录 —— 探针只在坏窗口的第一轮跑，攒到之后再看）")
        return 0
    s = summarize(rows)
    print()
    print("  墙里本节点不可用的次数 : {}".format(s["origin_bad"]))
    print("  **有镜像可用**的次数  : {}".format(s["alt_ok"]))
    print("  所有镜像也不可用的次数 : {}".format(s["all_alt_bad"]))
    if s["per_host"]:
        print("  各镜像可用次数：")
        for h, c in s["per_host"].most_common():
            print("    {:<24} {}".format(h, c))
    print()
    for r in rows[-6:]:
        nodes = r.get("nodes") or []
        ok = [x["host"] for x in nodes if x.get("token") == "token-ok"]
        print("  {} | 本节点首页={} | 耗时 {:.0f}s | 可用镜像 {} 个 {}".format(
            r.get("at"), r.get("origin_home"), float(r.get("elapsed") or 0),
            len(ok), (ok[:3] if ok else "（无）")))
    print()
    if s["alt_ok"] and s["origin_bad"]:
        print("==> 建议：**值得做自动换域名**（墙期间有镜像能用）。")
    elif s["all_alt_bad"] and not s["alt_ok"]:
        print("==> 建议：**别做**（墙期间所有域名一样不可用，换了还得重启一次容器）。")
    else:
        print("==> 样本还不够（本节点在墙里可用 / 没抓到墙），继续攒。")
    # 顺带把 renew 日志里的坏窗口数报一下（如果给了日志路径）
    if os.path.exists(logp):
        try:
            fails = sum(1 for ln in open(logp, encoding="utf-8", errors="replace")
                        if "FAIL" in ln or "未授权" in ln or "SESSION_DEAD" in ln
                        or "WALL:" in ln)
            print("   （同目录日志里的失败/墙行数：{}）".format(fails))
        except Exception:                                      # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
