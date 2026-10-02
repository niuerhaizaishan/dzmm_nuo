# -*- coding: utf-8 -*-
"""根目录模块的引用体检：哪些是生产代码、哪些是从没被 import 的一次性脚本。"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SKIP = {".git", ".pylibs", "_tmp", "__pycache__", ".pytest_cache"}


def sources():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        parts = set(Path(rel).parts) if rel != "." else set()
        if parts & SKIP:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def main() -> None:
    files = list(sources())
    texts = {}
    for p in files:
        try:
            texts[p] = p.read_text(encoding="utf-8", errors="replace")
        except Exception:                                      # noqa: BLE001
            texts[p] = ""
    # 谁 import 了谁（粗粒度：正则找 import x / from x import / __import__("x")
    imported = {}
    for p, t in texts.items():
        for m in re.finditer(r"^\s*(?:from|import)\s+([a-zA-Z_][\w.]*)", t, re.M):
            imported.setdefault(m.group(1).split(".")[0], set()).add(
                str(p.relative_to(ROOT)))
    print("=== 根目录 .py（生产 vs 一次性）===")
    alone, used = [], []
    for p in sorted(f for f in files if f.parent == ROOT):
        mod = p.stem
        refs = {x for x in imported.get(mod, set())
                if x != str(p.relative_to(ROOT))}
        # 还要看有没有被当作脚本/字符串提到（工具脚本常这么用）
        mentions = sum(1 for q, t in texts.items()
                       if q != p and mod in t and re.search(
                           r"[\"']" + re.escape(mod) + r"[\"']", t))
        if refs or mentions:
            used.append((mod, sorted(refs)[:3], mentions))
        else:
            alone.append(mod)
    print("  被 import 的（{}）：".format(len(used)))
    for mod, refs, mentions in used:
        print("    {:24s} ← {}（另有 {} 处字符串提及）".format(mod, refs, mentions))
    print("  全仓库没有任何 import / 字符串引用的（{}）：".format(len(alone)))
    print("    " + "、".join(alone))

    print()
    print("=== 本地杂物 ===")
    tmp = ROOT / "tests" / "_tmp"
    if tmp.exists():
        n = sum(1 for _ in tmp.rglob("*") if _.is_dir())
        size = sum(f.stat().st_size for f in tmp.rglob("*") if f.is_file())
        print("  tests/_tmp: {} 个子目录，{:.1f} MB".format(n, size / 1048576))
    print("  根目录 .log 文件：", [f.name for f in ROOT.glob("*.log")])
    print("  __pycache__ 目录数：",
          sum(1 for d in ROOT.rglob("__pycache__")))


if __name__ == "__main__":
    main()
