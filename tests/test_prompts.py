"""P8 第 3 步的守卫测试：prompt 外置。

外置的做法是**覆盖**而不是搬家：代码里的字面量仍在，`prompts/<NAME>.md`
存在且非空才覆盖。这批测试盯的正是这件事最容易出岔子的几个点：

1. 带出去的 39 个文件和代码里的默认值**逐字节相同** —— 否则"外置"本身
   就等于偷偷改了 prompt（改了机器人性格，最难发现的那种回归）。
2. 文件缺失 / 为空 / 读失败 → 必须**退回默认值**，绝不能变成空 prompt。
3. `brains.py` 里不能再有裸露的 `*_PROMPT = <三引号字符串>`（漏包一个就静默失效）。
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains  # noqa: E402
import runtime.prompts as rp  # noqa: E402

BRAINS_PY = ROOT / "brains.py"
PROMPT_DIR = ROOT / "prompts"


def _assignments():
    """从 brains.py 里读出所有 `NAME = _p("NAME", <字面量>)`。

    返回 `{NAME: (声明的名字, 默认值字面量)}`。
    """
    tree = ast.parse(BRAINS_PY.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        name = node.targets[0].id
        if not name.endswith("_PROMPT"):
            continue
        call = node.value
        assert isinstance(call, ast.Call), "{} 没被 _p() 包住".format(name)
        assert isinstance(call.func, ast.Name) and call.func.id == "_p", name
        arg0 = call.args[0]
        assert isinstance(arg0, ast.Constant) and arg0.value == name, \
            "{} 的 _p() 名字对不上".format(name)
        assert len(call.args) == 2, "{} 的 _p() 参数不是 2 个".format(name)
        out[name] = ast.literal_eval(call.args[1])
    return out


def _prompt_files():
    return {p.stem: p for p in PROMPT_DIR.glob("*.md")}


ASSIGNS = _assignments()
FILES = _prompt_files()


# --------------------------------------------------------------- 结构与计数

def test_brains_has_prompts():
    assert len(ASSIGNS) == 39, "prompt 数量变了：{}".format(len(ASSIGNS))


def test_runtime_shim_present():
    """runtime 包缺失时必须还能用代码默认值（本地单文件运行场景）。"""
    src = BRAINS_PY.read_text(encoding="utf-8")
    assert "from runtime.prompts import load as _p" in src
    assert "def _p(name, default):" in src


def test_every_prompt_file_matches_code_default():
    """带出去的文件必须与代码里的默认值**完全相同**。

    这条是本次迁移的核心断言：文件与默认值一旦漂移，说明有人只改了
    一边 —— 而线上到底用哪份取决于挂载成不成功，行为会变得不可预测。
    """
    missing = sorted(set(ASSIGNS) - set(FILES))
    assert missing == [], "缺文件: {}".format(missing)
    diff = []
    for name, default in ASSIGNS.items():
        on_disk = FILES[name].read_text(encoding="utf-8")
        if on_disk != default:
            diff.append((name, len(default), len(on_disk)))
    assert diff == [], "文件与默认值不一致: {}".format(diff)


def test_no_orphan_prompt_files():
    """`prompts/` 里不该有没人读的文件（改了却毫无效果最容易骗人）。"""
    orphan = sorted(set(FILES) - set(ASSIGNS))
    assert orphan == [], "无对应常量的文件: {}".format(orphan)


def test_no_bare_prompt_literals_in_brains():
    """守卫迁移完整性：不应再有未被 `_p()` 包裹的 `*_PROMPT = "..."`。"""
    src = BRAINS_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.endswith("_PROMPT")):
            assert isinstance(node.value, ast.Call), node.targets[0].id


# --------------------------------------------------------------- 运行期取值

def test_brains_values_non_empty():
    for name in ASSIGNS:
        assert (getattr(brains, name) or "").strip(), name


def test_brains_values_equal_files():
    """import 之后取到的值应当来自文件（本仓库文件齐全的前提下）。"""
    for name in ASSIGNS:
        assert getattr(brains, name) == FILES[name].read_text(encoding="utf-8"), name


def test_stats_reports_files(monkeypatch):
    monkeypatch.setattr(rp, "_sources", dict(rp.sources()))
    st = rp.stats()
    assert st["enabled"] is True
    assert st["total"] == len(rp.sources())
    assert st["from_file"] + st["from_code"] == st["total"]


# --------------------------------------------------------------- load() 语义

@pytest.fixture
def pdir(monkeypatch, dbdir):
    d = dbdir / "prompts"
    d.mkdir()
    monkeypatch.setenv("DZMM_PROMPT_DIR", str(d))
    monkeypatch.delenv("DZMM_PROMPTS", raising=False)
    monkeypatch.setattr(rp, "_sources", {})
    return d


def test_load_returns_file_when_present(pdir):
    (pdir / "X_PROMPT.md").write_text("来自文件", encoding="utf-8")
    assert rp.load("X_PROMPT", "默认值") == "来自文件"


def test_load_falls_back_when_missing(pdir):
    assert rp.load("NOPE_PROMPT", "默认值") == "默认值"
    assert rp.sources()["NOPE_PROMPT"] == "code"


def test_load_treats_empty_file_as_no_override(pdir):
    """空文件绝不能让 prompt 变空 —— 这是外置最危险的失败模式。"""
    (pdir / "X_PROMPT.md").write_text("", encoding="utf-8")
    assert rp.load("X_PROMPT", "默认值") == "默认值"
    (pdir / "X_PROMPT.md").write_text("   \n\n", encoding="utf-8")
    assert rp.load("X_PROMPT", "默认值") == "默认值"


def test_load_keeps_raw_whitespace(pdir):
    """首尾换行可能是有意义的，返回时不做 strip。"""
    raw = "\n  <b>你好</b>\n\n"
    (pdir / "X_PROMPT.md").write_text(raw, encoding="utf-8")
    assert rp.load("X_PROMPT", "默认值") == raw


def test_load_survives_unreadable_dir(monkeypatch, dbdir):
    """目录不存在 / 不是目录 → 退回默认值，不抛异常。"""
    monkeypatch.setenv("DZMM_PROMPT_DIR", str(dbdir / "not-a-dir"))
    monkeypatch.setattr(rp, "_sources", {})
    assert rp.load("X_PROMPT", "默认值") == "默认值"


def test_switch_off_uses_code_default(monkeypatch, pdir):
    (pdir / "X_PROMPT.md").write_text("来自文件", encoding="utf-8")
    monkeypatch.setenv("DZMM_PROMPTS", "off")
    assert rp.load("X_PROMPT", "默认值") == "默认值"
    assert rp.sources()["X_PROMPT"] == "code"


def test_load_uses_env_dir(monkeypatch, dbdir):
    d = dbdir / "other"
    d.mkdir()
    (d / "X_PROMPT.md").write_text("别处", encoding="utf-8")
    monkeypatch.setenv("DZMM_PROMPT_DIR", str(d))
    monkeypatch.setattr(rp, "_sources", {})
    assert rp.prompt_dir() == d
    assert rp.load("X_PROMPT", "默认值") == "别处"


def test_default_dir_is_repo_prompts():
    assert rp.DEFAULT_DIR == ROOT / "prompts"
