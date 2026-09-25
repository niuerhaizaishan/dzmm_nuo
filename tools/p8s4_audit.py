"""P8 第 4 步终检：把 runtime/ 里所有搬出来的函数，与**某个 git 版本**的 bot.py 逐字比对。

`tools/p8s4_move.py` 的"搬完比对"是在搬迁当时做的，只覆盖那一次。
它的弱点是：**如果在比对之前就写盘失败/中断，那一次就没有证明**。

所以这里补一个独立的总账：拿 `git show <ref>:bot.py`（默认 HEAD）当基准，
把 runtime/ 里所有能从旧 bot.py 找到同名的函数逐个比对源码文本。
除了 slice 里**声明过**的改写（例如 `_ACTIVE_CID` -> `ctx.get_active_cid()`），
一个字符都不该不同。

用法：
    python tools/p8s4_audit.py            # 与 P8-4 基线比
    python tools/p8s4_audit.py <rev>      # 与指定版本比

基线刻意**不用 HEAD**：搬完提交之后 HEAD 里已经没有这些函数了，
比出来会是 "0/0 一致"，看着像通过、其实什么都没验。
`646e38e` 是开始拆之前的最后一个提交（bot.py 6107 行，业务函数都还在里面）。
"""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import p8s4_move as move  # noqa: E402

DEFAULT_REF = "646e38e"

# 搬完之后**有意**做过的改动：审计要放行，但必须登记 + 写明理由。
# 不登记的话这里会红 —— 那是设计意图：任何"搬完之后又改了"都必须看得见。
INTENTIONAL_AFTER_MOVE = {
    # key 用 (模块文件名, 函数名) —— 与代码里的 (p.name, n) 一致，**不带** runtime/ 前缀
    ("perf.py", "_detect_perf_query"):
        "修 @ 剥离顺序：名字可能互为前缀，必须长名优先（见 runtime/identity.py）",
}


def to_lf(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def norm(s: str) -> str:
    return to_lf(s).strip()


def funcs_of(src: str) -> dict:
    tree = ast.parse(src)
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, n)
            out[n.name] = norm(seg if seg is not None else ast.unparse(n))
    return out


def main(argv) -> int:
    ref = argv[1] if len(argv) > 1 else DEFAULT_REF
    try:
        old = subprocess.run(["git", "show", "{}:bot.py".format(ref)],
                             cwd=str(ROOT), capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        print("拿不到 {}:bot.py -- {}".format(ref, e.stderr.decode()[:200]))
        return 2
    old_src = old.stdout.decode("utf-8")
    old_funcs = funcs_of(old_src)

    # 每个模块对应的声明式改写 + 该片**声明要搬**的常量
    rewrites = {}
    moved_consts = {}
    for sl in move.SLICES.values():
        mod = sl.module[:-3].replace("/", ".") if sl.module.endswith(".py") else sl.module
        rewrites.setdefault(mod, []).extend(sl.rewrites)
        moved_consts.setdefault(mod, set()).update(sl.consts)

    print("基准: {}:bot.py（{} 行 / {} 个模块级函数）".format(
        ref, len(old_src.splitlines()), len(old_funcs)))
    print()
    total = same = 0
    problems = []
    for p in sorted((ROOT / "runtime").glob("*.py")):
        mod = "runtime." + p.stem
        new_src = p.read_text(encoding="utf-8")
        new_funcs = funcs_of(new_src)
        hit = [n for n in new_funcs if n in old_funcs]
        if not hit:
            continue
        rw = rewrites.get(mod, [])
        ok = 0
        for n in sorted(hit):
            total += 1
            want = norm(move.apply_rewrites(old_funcs[n], rw))
            if new_funcs[n] == want:
                ok += 1
                same += 1
            elif (p.name, n) in INTENTIONAL_AFTER_MOVE:
                ok += 1
                same += 1
                print("      (放行有意的搬后改动) {} :: {} —— {}".format(
                    p.name, n, INTENTIONAL_AFTER_MOVE[(p.name, n)]))
            else:
                problems.append((p.name, n))
        print("  {:<22s} {:3d}/{:3d} 与 {} 逐字一致{}".format(
            p.name, ok, len(hit), ref,
            "" if not rw else "（含 {} 条声明改写）".format(len(rw))))

    print()
    print("合计 {}/{} 个搬出来的函数与 {} 逐字一致".format(same, total, ref))
    if problems:
        print()
        print("**不一致**（要么是未声明的改动，要么是搬的时候被改坏了）：")
        for f, n in problems[:20]:
            print("    {} :: {}".format(f, n))
        return 1

    # 顺带核对：搬走的常量，值有没有变
    old_consts = {}
    tree = ast.parse(old_src)
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            old_consts[n.targets[0].id] = norm(ast.get_source_segment(old_src, n)
                                               or ast.unparse(n))
    ctotal = csame = 0
    cbad = []
    for p in sorted((ROOT / "runtime").glob("*.py")):
        mod = "runtime." + p.stem
        want_names = moved_consts.get(mod, set())
        if not want_names:
            continue
        src = p.read_text(encoding="utf-8")
        rw = rewrites.get(mod, [])
        for n in ast.parse(src).body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                    and isinstance(n.targets[0], ast.Name):
                name = n.targets[0].id
                # 只核对"这一片声明要搬"的常量。头部自己定义的（如 obs.ROOT）
                # 同名但不同文，是有意的，不该算差异。
                if name not in old_consts or name not in want_names:
                    continue
                ctotal += 1
                got = norm(ast.get_source_segment(src, n) or ast.unparse(n))
                if got == norm(move.apply_rewrites(old_consts[name], rw)):
                    csame += 1
                else:
                    cbad.append((p.name, name))
    print("合计 {}/{} 个搬出来的常量与 {} 一致".format(csame, ctotal, ref))
    for f, n in cbad[:10]:
        print("    {} :: {}".format(f, n))
    return 1 if cbad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
