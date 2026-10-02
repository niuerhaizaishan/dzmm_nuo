# -*- coding: utf-8 -*-
"""审计：哪些**会发到群里**的字符串里还硬编码着「小小糯」（P12-H 收尾）。

两类是**已经被机制覆盖**的，不算问题：
  · `brains.py` 里 `_p("XXX_PROMPT", ...)` 的默认值 —— 系统提示词，
    `apply_persona()` 会把「小小糯～/小小糯」替换成该群人设的名字；
  · `personas.py` 的 `PERSONAS` / `PHRASES` 默认值 —— 那是"代码默认"，
    每群按人设取（Rose 的 19 条话术已经改成薇丝了）。

真正的问题是**硬编码在业务代码里、直接发消息**的那些：它们在 rose 群里会以
「小小糯」自称，而发言者是「薇丝-bot」——名字对不上。

这个脚本用 AST 找字符串常量，打印 文件:行号 + 上下文判断。
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SKIP_DIRS = {"tests", "tools", ".git", "__pycache__", ".pylibs", "docs", "prompts",
             "probe", "data", "novels", "admin"}
# admin/ 里的「小小糯」是页面标题（后台自己看的），不算群内文案

files = [p for p in ROOT.rglob("*.py")
         if not any(part in SKIP_DIRS for part in p.parts)]
rows = []

for path in sorted(files):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception as e:                                       # noqa: BLE001
        print("解析失败", path, e)
        continue
    # 先标出被 _p("NAME", ...) 包住的字面量行号（brains 的 prompt 默认值）
    prompt_lines = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_p" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)):
            prompt_lines.add(getattr(node.args[1], "lineno", 0))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and "小小糯" in node.value:
            ln = getattr(node, "lineno", 0)
            rel = path.relative_to(ROOT)
            src = path.read_text(encoding="utf-8").splitlines()
            line = src[ln - 1].strip() if 0 < ln <= len(src) else ""
            s = " ".join(node.value.split())
            kind = "prompt默认值(已被 apply_persona 覆盖)" if ln in prompt_lines else \
                   ("personas默认值" if rel.name == "personas.py" else "**业务硬编码**")
            rows.append((str(rel), ln, kind, s[:90], line[:70]))

biz = [r for r in rows if r[2] == "**业务硬编码**"]
print("含「小小糯」的字符串常量共 {} 处；其中**业务硬编码** {} 处：\n".format(
    len(rows), len(biz)))
for rel, ln, kind, s, line in biz:
    print("{:<24} {:<5} {}".format(rel, ln, s))
    print("{:<24} {:<5}   src: {}".format("", "", line))
print()
print("被 _p() 覆盖的（不用改）:", sum(1 for r in rows if r[2].startswith("prompt")))
print("personas 默认值（不用改）:", sum(1 for r in rows if r[2] == "personas默认值"))
