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
MEMORY_PY = ROOT / "runtime" / "memory.py"
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
# P12-G：登记册搬到了 runtime/prompts（谁调 load() 谁被登记），
# 所以"第 40 个"（runtime/memory.py 里的 MEMORY_FILTER_PROMPT）也在里面。
# ⚠️ 必须先 import 这两个模块，登记册才是满的。
import runtime.memory  # noqa: E402,F401
REG = rp.registry()


# --------------------------------------------------------------- 结构与计数

def test_brains_has_prompts():
    assert len(ASSIGNS) == 39, "brains.py 里的 prompt 数量变了：{}".format(len(ASSIGNS))


def test_registry_has_all_forty():
    """39 个在 brains + 1 个在 runtime/memory = 40（第 40 个是 P12-G 补上的）。"""
    assert len(REG) == 40, sorted(REG)
    assert "MEMORY_FILTER_PROMPT" in REG
    assert REG["MEMORY_FILTER_PROMPT"]["module"] == "runtime.memory"


def test_registry_module_is_the_owner_not_a_later_reader():
    """登记册记的必须是**持有常量的模块**（`declare`），读它的人不能改写登记。

    页面/自测/闸门都会调 `load()` 读当前内容；如果读了就登记，
    登记册会变成 `admin.dashboard` —— 热加载把常量写进后台进程，
    机器人那边永远不更新（完全看不出来）。所以：
      - `declare()` 登记（只有常量定义处调）；
      - `load()` **只读不登记**。
    """
    for name in ("CMD_PROMPT", "MEMORY_FILTER_PROMPT"):
        rp.load(name, rp.default_of(name))          # 模拟页面读一遍
    got = rp.registry()
    assert got["CMD_PROMPT"]["module"] == "brains"
    assert got["MEMORY_FILTER_PROMPT"]["module"] == "runtime.memory"
    rp.load("NOBODY_OWNS_THIS_PROMPT", "x")          # 读一个没人声明的名字
    assert "NOBODY_OWNS_THIS_PROMPT" not in rp.registry(), \
        "只有 declare() 才能登记"
    assert rp.load("NOBODY_OWNS_THIS_PROMPT", "x") == "x"


def test_runtime_shim_present():
    """runtime 包缺失时必须还能用代码默认值（本地单文件运行场景）。"""
    src = BRAINS_PY.read_text(encoding="utf-8")
    assert "from runtime.prompts import declare as _p_load" in src
    assert "def _p(name, default):" in src
    mem = MEMORY_PY.read_text(encoding="utf-8")
    assert "from runtime.prompts import declare as _p" in mem, \
        "第 40 个提示词也要走同一个机制（declare = 我持有这个常量）"


def test_every_prompt_file_matches_code_default():
    """带出去的文件必须与代码里的默认值**完全相同**（40 个都要）。

    这条是本次迁移的核心断言：文件与默认值一旦漂移，说明有人只改了
    一边 —— 而线上到底用哪份取决于挂载成不成功，行为会变得不可预测。
    """
    missing = sorted(set(REG) - set(FILES))
    assert missing == [], "缺文件: {}".format(missing)
    diff = []
    for name in REG:
        on_disk = FILES[name].read_text(encoding="utf-8")
        default = rp.default_of(name)
        if on_disk != default:
            diff.append((name, len(default), len(on_disk)))
    assert diff == [], "文件与默认值不一致: {}".format(diff)


def test_no_orphan_prompt_files():
    """`prompts/` 里不该有没人读的文件（改了却毫无效果最容易骗人）。"""
    orphan = sorted(set(FILES) - set(REG))
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


def test_memory_filter_prompt_is_externalised():
    """第 40 个提示词（P12-G 补）：不许再是裸字面量。"""
    tree = ast.parse(MEMORY_PY.read_text(encoding="utf-8"))
    found = False
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "MEMORY_FILTER_PROMPT"):
            found = True
            assert isinstance(node.value, ast.Call), \
                "MEMORY_FILTER_PROMPT 还是裸字面量（改它得改代码）"
            assert node.value.func.id == "_p", node.value.func.id
    assert found, "runtime/memory.py 里找不到 MEMORY_FILTER_PROMPT"


# --------------------------------------------------------------- 运行期取值

def test_brains_values_non_empty():
    for name in ASSIGNS:
        assert (getattr(brains, name) or "").strip(), name
    import runtime.memory as _mem
    assert (_mem.MEMORY_FILTER_PROMPT or "").strip()


def test_brains_values_equal_files():
    """import 之后取到的值应当来自文件（本仓库文件齐全的前提下）。"""
    for name in ASSIGNS:
        assert getattr(brains, name) == FILES[name].read_text(encoding="utf-8"), name
    import runtime.memory as _mem
    assert _mem.MEMORY_FILTER_PROMPT == FILES[
        "MEMORY_FILTER_PROMPT"].read_text(encoding="utf-8")


def test_stats_reports_files(monkeypatch):
    monkeypatch.setattr(rp, "_sources", dict(rp.sources()))
    st = rp.stats()
    assert st["enabled"] is True
    # total = **登记册**里的提示词数（40）；
    # `sources()` 里可能还有测试自己读过的临时名字，所以不能拿它当分母。
    assert st["total"] == len(rp.registry()) == 40
    assert st["from_file"] + st["from_code"] == st["total"]
    assert st["files_on_disk"] >= st["from_file"]


# --------------------------------------------------------------- load() 语义

@pytest.fixture
def pdir(monkeypatch, dbdir):
    d = dbdir / "prompts"
    d.mkdir()
    monkeypatch.setenv("DZMM_PROMPT_DIR", str(d))
    monkeypatch.delenv("DZMM_PROMPTS", raising=False)
    monkeypatch.setattr(rp, "_sources", {})
    rp.clear_cache()          # P12-F 的 mtime 缓存：每个用例都从干净状态开始
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


# --------------------------------------------------------------- mtime 缓存（P12-F）

def test_cache_avoids_rereading_and_notices_edits(pdir):
    """缓存按 (路径, mtime, 大小) 判定：没改就不重读，改了立刻看到新内容。"""
    rp.clear_cache()
    (pdir / "X_PROMPT.md").write_text("第一版", encoding="utf-8")
    assert rp.load("X_PROMPT", "默认值") == "第一版"
    c1 = rp.cache_stats()
    assert rp.load("X_PROMPT", "默认值") == "第一版"
    c2 = rp.cache_stats()
    assert c2["hits"] == c1["hits"] + 1, "第二次应当命中缓存"
    assert c2["misses"] == c1["misses"]

    # 改写文件：mtime 变了 ⇒ 必须重读（显式推一下 mtime，别指望文件系统粒度）
    import os
    import time
    (pdir / "X_PROMPT.md").write_text("第二版", encoding="utf-8")
    t = time.time() + 2
    os.utime(str(pdir / "X_PROMPT.md"), (t, t))
    assert rp.load("X_PROMPT", "默认值") == "第二版"
    assert rp.cache_stats()["misses"] == c2["misses"] + 1


def test_cache_notices_creation_and_deletion(pdir):
    """文件从无到有、从有到无，都要被发现（"不存在"也要进缓存）。"""
    rp.clear_cache()
    assert rp.load("X_PROMPT", "默认值") == "默认值"
    (pdir / "X_PROMPT.md").write_text("后来加的", encoding="utf-8")
    assert rp.load("X_PROMPT", "默认值") == "后来加的"
    (pdir / "X_PROMPT.md").unlink()
    assert rp.load("X_PROMPT", "默认值") == "默认值"
    assert rp.sources()["X_PROMPT"] == "code"


def test_empty_file_edit_still_falls_back(pdir):
    """把文件改成空的：缓存要跟着变，语义仍然是"退回默认值"。"""
    rp.clear_cache()
    (pdir / "X_PROMPT.md").write_text("有内容", encoding="utf-8")
    assert rp.load("X_PROMPT", "默认值") == "有内容"
    (pdir / "X_PROMPT.md").write_text("", encoding="utf-8")
    assert rp.load("X_PROMPT", "默认值") == "默认值"
