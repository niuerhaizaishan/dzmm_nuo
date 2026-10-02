# -*- coding: utf-8 -*-
"""P12-AA 之二：把主循环里的 `save_state(state)` 改成"只写本群"。

体检发现（2026-09-27）：按群并行下三个任务共用一份 `state` 字典，而
`save_state(state)` 是**整 scope 重写** —— 任务 A 写下去会用自己那份旧快照
盖掉群 B/C 刚更新的数据（丢更新）。

做法：每个调用点前面都有 `state[cid] = <变量>`，据此把
    save_state(state)
换成
    _save_group(cid, <那个变量>)
（`_save_group` 见 bot.py，只 upsert 本群那一行。）

用法：python tools/p12aa_group_save.py [--check]
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BOT = ROOT / "bot.py"
PAT = re.compile(r"state\[cid\]\s*=\s*([A-Za-z_][\w]*)")


def main() -> None:
    check = "--check" in sys.argv
    lines = BOT.read_text(encoding="utf-8").splitlines()
    plan = []
    for i, ln in enumerate(lines):
        if "save_state(state)" not in ln:
            continue
        var = None
        for j in range(i - 1, max(-1, i - 10), -1):
            m = PAT.search(lines[j])
            if m:
                var = m.group(1)
                break
        if not var:
            print("!! 第 {} 行找不到 state[cid] = <变量>，跳过".format(i + 1))
            continue
        indent = ln[:len(ln) - len(ln.lstrip())]
        plan.append((i, var, indent + "_save_group(cid, {})".format(var), ln.strip()))

    print("计划替换 {} 处：".format(len(plan)))
    for ln_no, var, new, old in plan:
        print("  {:5d}  {}   →  {}".format(ln_no + 1, old, new.strip()))

    if check:
        print("（--check：未写入）")
        return
    if len(plan) < 20:
        print("!! 只找到 {} 处，少于预期的 24 处 —— 中止，不动文件".format(len(plan)))
        raise SystemExit(1)
    for ln_no, _var, new, _old in plan:
        lines[ln_no] = new
    BOT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("已写入 {} 处".format(len(plan)))


if __name__ == "__main__":
    main()
