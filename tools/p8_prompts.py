"""一次性迁移（P8 第 3 步）：把 brains.py 的 `*_PROMPT` 抽到 `prompts/*.md`。

做法是**程序化提取**，不做手工转录 —— 因此不存在"抄错一个字"的可能。

1. 用 `ast` 读出每个 `*_PROMPT` 的**准确值**，原样写入 `prompts/<NAME>.md`
2. 在代码里把赋值包成 `NAME = _p("NAME", <原字面量>)` —— **字面量保留为默认值**
3. 迁移后 `import brains`，**逐个比对**取值是否与迁移前完全相同；不等即报错

任何一步不符即中止（沿用 P4.2 / P8.1 的计数断言做法）。
"""
import ast
import importlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAINS = ROOT / "brains.py"
PROMPT_DIR = ROOT / "prompts"

IMPORT_BLOCK = '''import json, os, random, requests

# P8 第 3 步：prompt 可被 prompts/<NAME>.md 覆盖（见 runtime/prompts.py）。
# runtime 包缺失时退化为"永远用代码里的默认值"，绝不让 prompt 变空。
try:
    from runtime.prompts import load as _p
except Exception:                                          # noqa: BLE001
    def _p(name, default):
        return default
'''


def collect(src: str) -> dict:
    """返回 {NAME: value}（仅模块级、纯字符串字面量、名字以 _PROMPT 结尾）。"""
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        name = node.targets[0].id
        if not name.endswith("_PROMPT"):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out[name] = node.value.value
    return out


def main() -> int:
    if not BRAINS.exists():
        print("找不到 brains.py")
        return 1
    src = BRAINS.read_text(encoding="utf-8")

    if "from runtime.prompts import load as _p" in src:
        print("已经迁移过，中止以免重复应用")
        return 1

    prompts = collect(src)
    if not prompts:
        print("没找到任何 *_PROMPT 常量")
        return 1
    print("发现 {} 个 prompt 常量".format(len(prompts)))

    # 1) 原样写出文件（值 → 文件，字节级一致）
    PROMPT_DIR.mkdir(exist_ok=True)
    written = 0
    for name, value in prompts.items():
        p = PROMPT_DIR / (name + ".md")
        if p.exists():
            print("  跳过已存在: {}".format(p.name))
            continue
        p.write_text(value, encoding="utf-8")
        written += 1
    print("  写出 {} 个文件 -> {}".format(written, PROMPT_DIR))

    # 2) 包一层 _p（保留原字面量为默认值）
    for name in prompts:
        node_value = None
        for n in ast.parse(src).body:
            if (isinstance(n, ast.Assign) and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name)
                    and n.targets[0].id == name):
                node_value = n.value
                break
        seg = ast.get_source_segment(src, node_value)
        old = "{} = {}".format(name, seg)
        if src.count(old) != 1:
            print("中止：{} 的赋值段不唯一".format(name))
            return 1
        src = src.replace(old, '{} = _p("{}", {})'.format(name, name, seg), 1)
    print("  已包裹 {} 处赋值".format(len(prompts)))

    # 3) 加导入（只加一次）
    old_imports = "import json, os, random, requests"
    if src.count(old_imports) != 1:
        print("中止：import 行不唯一")
        return 1
    src = src.replace(old_imports, IMPORT_BLOCK.rstrip("\n"), 1)

    BRAINS.write_text(src, encoding="utf-8")
    print("  brains.py 已更新")

    # 4) 逐个比对：迁移后的取值必须与迁移前完全相同
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import brains
    importlib.reload(brains)
    bad = []
    for name, value in prompts.items():
        got = getattr(brains, name, None)
        if got != value:
            bad.append((name, len(value or ""), len(got or "")))
    print()
    print("比对 {} 个 prompt 取值：{}".format(
        len(prompts), "全部一致" if not bad else "**不一致**"))
    for name, a, b in bad[:5]:
        print("  {} 原 {} 字符 / 现 {} 字符".format(name, a, b))
    if bad:
        return 1

    empty = [n for n in prompts if not (getattr(brains, n) or "").strip()]
    print("空 prompt: {} 个（应为 0）".format(len(empty)))
    if empty:
        print("  " + ", ".join(empty[:5]))
        return 1

    import runtime.prompts as rp
    print("prompt 来源统计:", rp.stats())
    print("OK 迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
