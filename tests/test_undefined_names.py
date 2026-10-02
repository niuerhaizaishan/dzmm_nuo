"""P12-BB：**未定义的名字**不许留在代码里（NameError 只能等线上才发现）。

## 现场（屋主 2026-09-30："茶水间为什么没回复？"）

```
10:22:43 👋 [4fc4a2fd] @消息: @小小糯～ 给我卜一卦
10:22:43 ⚠️ [4fc4a2fd] 生成异常，回滚 last_id，下轮重试
10:22:43 loop[4fc4a2fd]: name 'is_fortune_ask' is not defined
```

P12-AW 给茶水间加"自然语言算运势"时，`bot.py` 的 @ 分支调了 `is_fortune_ask(q)`，
却**忘了把它加进 `from runtime.fortune import …` 那一行**。后果不只那一句：
茶水间里**任何** @ 都在这一步抛 NameError → 回滚 `last_id` → 永远重试同一条 ⇒ 那是"整条 @ 路静默死掉"。

## 为什么之前没发现

- P12-AW 的验收是屋主发 `#运势` —— 走的是 `#` 闸门（只用 `is_fortune_cmd`），**不碰这一行**；
- 单测查的是"源码里有没有 `is_fortune_ask(q)`"（有），没查"这个名字在 `bot` 模块里存不存在"；
- `python -m py_compile` 也查不出来（语法没问题，是运行时名字解析）。

## 这个测试做什么

用 `ast` 找**每一个模块里被"读"、却既没被绑定也没被导入、又不是内置**的名字
（相当于自带一个最小 pyflakes，不引入新依赖）。真报出来就是"上线必炸"的名字。

⚠️ 有 `import *` 的文件跳过（无法静态判断名字从哪来）。
"""
import ast
import builtins
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 盯住真正跑在线上、而且"炸了没人立刻知道"的入口与热路径。
TARGETS = [
    "bot.py",
    "brains.py",
    "sio_subscribe.py",
    "socketio.py",
    "webhook_server.py",
    "renew_token.py",
    "check_bot_health.py",
    "personas/__init__.py",   # P3-6 起 personas 是包（personas.py 已拆入，见下四行）
    "personas/registry.py",
    "personas/nuo.py",
    "personas/rose_butler.py",
    "personas/ruojin.py",
    "runtime/sender.py",
    "runtime/roast.py",
    "runtime/climax.py",
    "runtime/status.py",
    "runtime/roster.py",
    "runtime/pushes.py",
    "runtime/rank.py",
    "runtime/profile_sync.py",
    "runtime/inflight.py",
    "runtime/turns.py",
    "runtime/sender_chain.py",
    "runtime/config_boot.py",
    "runtime/reply_gate.py",
    "runtime/mainloop.py",
    "profiles.py",
    "state_store.py",
    "main.py",
]


def _bound_names(tree: ast.AST) -> set:
    """文件里"被绑定过"的名字（赋值/def/class/import/参数/except as/with as/推导式/海象…）。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:       # match/case 绑定
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _used_names(tree: ast.AST) -> set:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Load)}


def undefined_names(path: Path) -> list:
    return undefined_from_source(path.read_text(encoding="utf-8"), str(path))


def undefined_from_source(src: str, name: str = "<src>") -> list:
    tree = ast.parse(src, filename=name)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return []
    unknown = _used_names(tree) - _bound_names(tree) - set(dir(builtins))
    # `__file__` / `__name__` 这类模块级 dunder 由框架注入
    unknown = {u for u in unknown if not (u.startswith("__") and u.endswith("__"))}
    return sorted(unknown)


@pytest.mark.parametrize("rel", TARGETS)
def test_no_undefined_names(rel):
    p = ROOT / rel
    if not p.exists():
        pytest.skip(rel + " 不在这个仓库里")
    bad = undefined_names(p)
    assert not bad, "{} 里用了没定义/没导入的名字：{}".format(rel, bad)


def test_the_checker_actually_catches_the_bug():
    """自检：这个检查器必须能抓到 P12-BB 那个真实事故的形状。

    （用字符串而不是 `tmp_path`：这个仓库的 `tmp_path` 在沙箱下会 PermissionError。）
    """
    src = ("import os\n\n\ndef f(q):\n"
           "    return is_fortune_ask(q) and os.path.sep\n")
    assert undefined_from_source(src) == ["is_fortune_ask"]
    # 正常写法（导入了）不能被误报
    ok = ("from runtime.fortune import is_fortune_ask\n"
          "import os\n\n\ndef f(q):\n"
          "    return is_fortune_ask(q) and os.path.sep\n")
    assert undefined_from_source(ok) == []


def test_the_fortune_intent_helper_is_imported_by_bot():
    """事故的直接回归：`bot.py` 用到 `is_fortune_ask`，就必须在 import 行里。"""
    import bot
    assert callable(getattr(bot, "is_fortune_ask", None)), \
        "bot 模块里没有 is_fortune_ask ⇒ 茶水间的 @ 分支会抛 NameError"
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    line = next(ln for ln in src.split("\n")
                if ln.startswith("from runtime.fortune import"))
    for name in ("is_fortune_cmd", "is_fortune_ask", "fortune"):
        assert name in line, name + " 必须出现在 import 行里"


def test_bot_module_has_no_undefined_names():
    """`bot.py` 单文件再查一遍（它是最大的一个，也是这次出事的地方）。"""
    assert undefined_names(ROOT / "bot.py") == []


def test_bot_stays_importable():
    """最后一道：模块真能 import（名字错在 import 期就炸）。"""
    import bot                                                  # noqa: F401
    assert bot is not None


if __name__ == "__main__":                                      # pragma: no cover
    for rel in TARGETS:
        p = ROOT / rel
        if p.exists():
            print("{:<22} {}".format(rel, undefined_names(p) or "ok"))
    sys.exit(0)
