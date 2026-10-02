# -*- coding: utf-8 -*-
"""项目结构体检：文件/行数/函数规模/风险模式计数（只读）。

用法：python tools/audit_project.py
"""
import ast
import collections
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SKIP_DIRS = {".git", ".pylibs", "_tmp", "__pycache__", ".pytest_cache",
             "node_modules", "pw_profile", "pw_profile_greet", "data", "logs",
             "deploy", "probe"}


def py_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        parts = set(Path(rel).parts) if rel != "." else set()
        if parts & SKIP_DIRS:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(Path(dirpath) / fn)
    return sorted(out)


def loc(p: Path):
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:                                          # noqa: BLE001
        return 0, 0, 0
    code = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    return len(lines), len(code), sum(1 for ln in lines if len(ln) > 120)


def main() -> None:
    files = py_files()
    total = code_total = long_lines = 0
    per_dir = collections.Counter()
    per_dir_code = collections.Counter()
    rows = []
    for p in files:
        n, c, ll = loc(p)
        total += n
        code_total += c
        long_lines += ll
        rel = p.relative_to(ROOT)
        top = rel.parts[0] if len(rel.parts) > 1 else "(根)"
        per_dir[top] += n
        per_dir_code[top] += c
        rows.append((c, n, ll, str(rel)))

    print("=== 规模 ===")
    print("  .py 文件 {} 个；总行 {}；代码行（非空非注释）{}；>120 列的行 {}".format(
        len(files), total, code_total, long_lines))
    print("  按目录（代码行）：")
    for d, c in per_dir_code.most_common():
        print("    {:12s} {:6d} 行（共 {} 行）".format(d, c, per_dir[d]))

    print()
    print("=== 最大的 15 个文件（代码行）===")
    for c, n, ll, rel in sorted(rows, reverse=True)[:15]:
        print("  {:6d}  {:6d}总  {:3d}长行  {}".format(c, n, ll, rel))

    print()
    print("=== 函数规模（AST，最大的 15 个）===")
    funcs = []
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:                                      # noqa: BLE001
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size = (node.end_lineno or node.lineno) - node.lineno + 1
                funcs.append((size, str(p.relative_to(ROOT)), node.name, node.lineno))
    for size, rel, name, ln in sorted(funcs, reverse=True)[:15]:
        print("  {:4d} 行  {}:{}  {}".format(size, rel, ln, name))
    print("  函数/方法总数：{}".format(len(funcs)))

    print()
    print("=== 风险模式计数 ===")
    pats = {
        "静默吞异常（except 后 pass/return）": re.compile(
            r"except[^\n]*:\s*\n\s+(pass|return\b|continue\b)"),
        "裸 except": re.compile(r"except\s*:"),
        "TODO/FIXME/XXX/HACK": re.compile(r"\b(TODO|FIXME|XXX|HACK)\b"),
        "requests. 直连调用": re.compile(r"\brequests\.(post|get)\("),
        "asyncio.to_thread 包裹": re.compile(r"asyncio\.to_thread\("),
        "在线程池外直接调用生成函数": re.compile(r"=\s*generate_[a-z_]+\("),
        "写文件（write_text/open(...,'w')）": re.compile(
            r"write_text\(|open\([^)]*['\"]w"),
        "裸 print（应走 log）": re.compile(r"^\s*print\(", re.M),
        "硬编码 http(s) 地址": re.compile(r"https?://[a-zA-Z0-9./_-]+"),
        "环境变量读取": re.compile(r"os\.environ(\.get)?\("),
    }
    counts = collections.Counter()
    hits = collections.defaultdict(list)
    for p in files:
        txt = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(ROOT))
        for name, rx in pats.items():
            for m in rx.finditer(txt):
                counts[name] += 1
                if len(hits[name]) < 400:
                    line = txt[:m.start()].count("\n") + 1
                    hits[name].append("{}:{}".format(rel, line))
    for name, c in counts.most_common():
        top = collections.Counter(h.split(":")[0] for h in hits[name]).most_common(4)
        print("  {:32s} {:5d}   最集中：{}".format(name, c, top))

    print()
    print("=== 文件类型分布 ===")
    ext = collections.Counter()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        parts = set(Path(rel).parts) if rel != "." else set()
        if parts & SKIP_DIRS:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext[Path(fn).suffix or "(无)"] += 1
    for e, c in ext.most_common(14):
        print("  {:8s} {}".format(e, c))


if __name__ == "__main__":
    sys.exit(main())
