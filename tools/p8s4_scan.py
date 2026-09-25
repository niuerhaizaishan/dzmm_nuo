"""P8 第 4 步侦察：把 bot.py 的结构量出来，不靠"看起来像一组"来拆。

输出四张表：
  1. 模块级全局（谁被别的函数读写）
  2. 模块级函数（行号范围 / 是否 async）
  3. 每个函数引用的模块级名字 + 它自己有没有调别的模块级函数
  4. 按状态机候选前缀分组的函数清单，附"该组引用到的外部全局"

用法：python tools/p8s4_scan.py > tools/p8s4_report.txt
"""
import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot.py"

# 5 个状态机的候选关键词（名字里带这些词就算候选，先看聚类效果再定边界）
MACHINES = {
    "fasao(发骚/场景)": ("fasao", "scene"),
    "diaojiao(调教)": ("diajiao", "_dj_", "dj_"),
    "perf(公演)": ("perf",),
    "vote(投票)": ("vote",),
    "story(剧情)": ("story",),
    "heat(发情值)": ("heat",),
}


def main() -> int:
    src = BOT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()

    # ---- 模块级全局 ----
    globals_assigned = {}          # name -> lineno
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    globals_assigned.setdefault(n.id, node.lineno)

    # ---- 模块级函数 / 类 ----
    funcs = {}
    classes = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = node.lineno

    print("=" * 78)
    print("bot.py 总量")
    print("=" * 78)
    print("总行数          : {}".format(len(lines)))
    print("模块级函数      : {}".format(len(funcs)))
    print("模块级 async    : {}".format(
        sum(1 for f in funcs.values() if isinstance(f, ast.AsyncFunctionDef))))
    print("模块级类        : {}".format(len(classes)))
    print("模块级全局(赋值): {}".format(len(globals_assigned)))
    print("模块级 import 名: {}".format(
        sum(1 for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))))

    # ---- 每个函数引用的模块级名字 ----
    # 只认"模块级作用域"的名字：函数自身参数/局部赋值不算
    per_func_globals = {}
    per_func_calls = {}
    for name, node in funcs.items():
        local = set()
        for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            local.add(a.arg)
        if node.args.vararg:
            local.add(node.args.vararg.arg)
        if node.args.kwarg:
            local.add(node.args.kwarg.arg)
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                local.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)) and n is not node:
                local.add(n.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                local.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for al in n.names:
                    local.add((al.asname or al.name).split(".")[0])
            elif isinstance(n, ast.comprehension) and isinstance(n.target, ast.Name):
                local.add(n.target.id)

        g = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in local:
                g.add(n.id)
        per_func_globals[name] = g

        c = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                if n.func.id in funcs and n.func.id != name:
                    c.add(n.func.id)
        per_func_calls[name] = c

    # ---- 反向：每个全局被哪些函数引用 ----
    global_users = defaultdict(set)
    for fname, gs in per_func_globals.items():
        for g in gs:
            if g in globals_assigned:
                global_users[g].add(fname)

    print()
    print("=" * 78)
    print("模块级全局：被引用的广度（只列被 ≥5 个函数引用的，这些是拆分的真障碍）")
    print("=" * 78)
    hot = sorted(global_users.items(), key=lambda kv: -len(kv[1]))
    for g, users in hot[:45]:
        print("  {:32s} 被 {:3d} 个函数引用".format(g, len(users)))
    print("  ... 共 {} 个全局至少被 1 个函数引用".format(len(hot)))

    # ---- 状态机聚类 ----
    print()
    print("=" * 78)
    print("状态机候选聚类（按名字关键词）")
    print("=" * 78)
    claimed = set()
    for label, keys in MACHINES.items():
        hit = [n for n in funcs
               if any(k in n.lower() for k in keys) and n not in claimed]
        if not hit:
            continue
        claimed.update(hit)
        print()
        print("--- {} : {} 个函数 ---".format(label, len(hit)))
        for n in sorted(hit, key=lambda x: funcs[x].lineno):
            f = funcs[n]
            span = (f.end_lineno or f.lineno) - f.lineno + 1
            kind = "async" if isinstance(f, ast.AsyncFunctionDef) else "def  "
            print("    {:6d}-{:6d} ({:4d} 行) {} {}".format(
                f.lineno, f.end_lineno or f.lineno, span, kind, n))

        # 这组函数引用了哪些"组外"全局 —— 这就是拆出去要处理的依赖面
        gs = set()
        for n in hit:
            gs |= per_func_globals[n]
        outside = sorted(g for g in gs
                         if g in globals_assigned
                         and not any(k in g.lower() for k in keys))
        inside = sorted(g for g in gs
                        if g in globals_assigned
                        and any(k in g.lower() for k in keys))
        print("      组内全局 {} 个: {}".format(len(inside), ", ".join(inside[:12])))
        print("      组外全局 {} 个: {}".format(len(outside), ", ".join(outside[:20])))

        # 这组函数调用了哪些组外函数
        callees = set()
        for n in hit:
            callees |= per_func_calls[n]
        ext_calls = sorted(c for c in callees if c not in hit)
        print("      调用的组外函数 {} 个: {}".format(
            len(ext_calls), ", ".join(ext_calls[:20])))

    print()
    print("=" * 78)
    print("未被任何状态机关键词命中的模块级函数（按行号）")
    print("=" * 78)
    rest = [(n, funcs[n].lineno) for n in funcs if n not in claimed]
    rest.sort(key=lambda x: x[1])
    for n, ln in rest:
        span = (funcs[n].end_lineno or ln) - ln + 1
        print("  {:6d} ({:4d} 行) {}".format(ln, span, n))
    print("  共 {} 个".format(len(rest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
