"""P8 第 4 步的守卫测试：模块拆分（bot.py -> runtime/*）。

拆分最容易出的事故不是"函数搬错了"，而是**搬完两边都定义**：
bot.py 里留着一个旧定义、模块里有一个新定义 —— 到底跑哪个取决于 import 顺序，
而且测试全绿（两边代码本来就一样）。等哪天只改了一边，问题才会以
"改了没生效"的形式出现，届时极难定位。

所以这里第一组测试盯的是**结构不变量**，第二组才是行为。

⚠️ 结构检查全部是**自动推导**的，不维护"已搬走函数"清单 ——
清单式守卫迟早会变成"随手往列表里加一行"，那就等于没有守卫。
"""
import ast
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import runtime.ctx as ctx  # noqa: E402
import runtime.dice as rtdice  # noqa: E402

BOT_PY = ROOT / "bot.py"
RUNTIME_DIR = ROOT / "runtime"


def _runtime_modules():
    """runtime/ 下所有模块（跳过 __init__）。"""
    out = {}
    for p in sorted(RUNTIME_DIR.glob("*.py")):
        if p.stem == "__init__":
            continue
        out[p.name] = importlib.import_module("runtime." + p.stem)
    return out


def _module_funcs(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def moved_functions():
    """自动推导"已搬走"的函数：模块里定义了、且 bot 上拿到的是**同一个对象**。

    不需要任何人维护清单：新搬一片，这里自动包含它。
    """
    out = {}
    for fname, mod in _runtime_modules().items():
        for name in _module_funcs(RUNTIME_DIR / fname):
            if getattr(bot, name, None) is getattr(mod, name, None):
                out[name] = mod
    return out


MOVED = moved_functions()
BOT_DEFS = _module_funcs(BOT_PY)


# ---------------------------------------------------------------- 结构不变量

def test_something_was_actually_moved():
    """守卫本身要先能证明"确实有东西搬出来了"，否则下面的断言全是空转。"""
    assert len(MOVED) >= 60, "只推导出 {} 个搬走的函数，守卫可能失效了".format(len(MOVED))


def test_moved_functions_are_gone_from_bot_py():
    """bot.py 里不得再有这些函数的定义（只允许 re-export）。"""
    left = sorted(set(MOVED) & BOT_DEFS)
    assert left == [], "bot.py 里还有旧定义（跑哪个取决于 import 顺序）: {}".format(left)


def test_bot_reexports_the_same_objects():
    """bot 上的名字必须与模块里**同一个对象**，而不是又包了一层。

    45 个发送调用点、几十个业务调用点都还在 `bot.xxx` 上，
    re-export 让它们一处都不用改 —— 这条测试保证 re-export 没被"优化"掉。
    """
    for name, mod in MOVED.items():
        assert getattr(bot, name) is getattr(mod, name), name


def test_no_function_is_defined_in_both_bot_and_runtime():
    """全自动的"搬完两边都定义"守卫。

    比逐个列名字强：以后每拆一片都自动生效，不需要记得往这里加东西。
    两边同名的后果是"跑哪个取决于 import 顺序"，而且测试全绿（代码本来就一样）
    —— 直到有人只改了一边，才会以"改了没生效"的形式爆出来。
    """
    clash = {}
    for fname, _ in _runtime_modules().items():
        both = sorted(_module_funcs(RUNTIME_DIR / fname) & BOT_DEFS)
        if both:
            clash[fname] = both
    assert clash == {}, "两边都定义了同一个函数: {}".format(clash)


def test_no_import_bot_in_runtime_package():
    """整包普查：runtime/ 里一处都不该 import bot。

    出现回指就等于**只是把文字换了个位置**：调用时回指能绕开循环导入，
    所以它"能跑"，但没有任何解耦。要新增回指必须先在这里显式放开并写明理由。
    """
    hits = []
    for p in sorted(RUNTIME_DIR.glob("*.py")):
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if ln.strip().startswith(("import bot", "from bot import")):
                hits.append((p.name, i, ln.strip()))
    assert hits == [], "runtime/ 里出现回指: {}".format(hits)


# ---------------------------------------------------------------- 当前群上下文

def test_active_cid_has_exactly_one_writer():
    """`_ACTIVE_CID =` 只能出现在 `_set_active_cid` 里。

    写点分散就会出现"bot.py 更新了、runtime/ctx 没有"这种隐性错：
    搬出去的模块会安静地读到上一个群。
    """
    tree = ast.parse(BOT_PY.read_text(encoding="utf-8"))
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_ACTIVE_CID"
                    for t in sub.targets):
                if fn.name != "_set_active_cid":
                    bad.append((fn.name, sub.lineno))
    # 模块级初始定义（`_ACTIVE_CID = GROUPS[0]`）是唯一允许的例外
    top = [n.lineno for n in tree.body
           if isinstance(n, ast.Assign) and any(
               isinstance(t, ast.Name) and t.id == "_ACTIVE_CID"
               for t in n.targets)]
    assert bad == [], "这些函数在直接写 _ACTIVE_CID: {}".format(bad)
    assert len(top) <= 1, "模块级重复定义: {}".format(top)


def test_set_active_cid_keeps_ctx_and_mirror_in_sync():
    bot._set_active_cid("cid-A")
    assert ctx.get_active_cid() == "cid-A"
    assert bot._ACTIVE_CID == "cid-A"
    bot._set_active_cid("cid-B")
    assert ctx.get_active_cid() == "cid-B"
    assert bot._ACTIVE_CID == "cid-B"


def test_set_active_cid_tolerates_none():
    bot._set_active_cid(None)
    assert ctx.get_active_cid() == ""
    bot._set_active_cid("back")


# ---------------------------------------------------------------- 行为：骰子局

@pytest.fixture
def dice(state, monkeypatch):
    """隔离的骰子局：内存状态 + 不打网络的名字解析。"""
    monkeypatch.setattr(rtdice, "get_name", lambda uid: "名-" + uid[:4])
    bot._set_active_cid("cid-test")
    return rtdice


def test_dice_start_creates_waiting_game(dice):
    text = asyncio.run(dice._dice_start_cmd("cid-test", "u1"))
    assert "发起" in text
    g = dice._dice_state()
    assert g["active"] is True
    assert g["phase"] == "waiting"
    assert g["host"] == "u1"
    assert g["players"] == ["u1"]


def test_dice_start_rejected_when_already_active(dice):
    asyncio.run(dice._dice_start_cmd("cid-test", "u1"))
    assert "已经有一局" in asyncio.run(dice._dice_start_cmd("cid-test", "u2"))


def test_dice_join_then_duplicate(dice):
    asyncio.run(dice._dice_start_cmd("cid-test", "u1"))
    assert "加入" in asyncio.run(dice._dice_join_cmd("u2"))
    assert dice._dice_state()["players"] == ["u1", "u2"]
    assert "已经在局里" in asyncio.run(dice._dice_join_cmd("u2"))


def test_dice_join_without_game(dice):
    assert "没有骰子局" in asyncio.run(dice._dice_join_cmd("u1"))


def test_dice_exit_ends_game_below_two_players(dice):
    asyncio.run(dice._dice_start_cmd("cid-test", "u1"))
    asyncio.run(dice._dice_join_cmd("u2"))
    text = dice._dice_exit_cmd("u2")
    assert "剩余不足 2 人" in text
    g = dice._dice_state()
    assert g["active"] is False
    assert g["phase"] == "done"


def test_dice_exit_keeps_game_with_three_players(dice):
    asyncio.run(dice._dice_start_cmd("cid-test", "u1"))
    asyncio.run(dice._dice_join_cmd("u2"))
    asyncio.run(dice._dice_join_cmd("u3"))
    assert "剩余 2 人" in dice._dice_exit_cmd("u3")
    assert dice._dice_state()["active"] is True


def test_dice_exit_unknown_player_is_silent(dice):
    asyncio.run(dice._dice_start_cmd("cid-test", "u1"))
    assert dice._dice_exit_cmd("nobody") == ""


def test_dice_current_player_wraps_around():
    g = {"order": ["a", "b", "c"], "current_idx": 4}
    assert rtdice._dice_current_player(g) == "b"      # 4 % 3 == 1
    assert rtdice._dice_current_player({"order": []}) == ""


def test_dice_board_text_lists_records(dice):
    g = {"players": ["u1", "u2"], "stats": {"u1": {"win": 2, "lose": 1},
                                            "u2": {"win": 0, "lose": 3}},
         "round": 3}
    text = dice._dice_board_text(g, final=True)
    assert "共 3 轮" in text
    assert "2胜1负" in text
    assert "0胜3负" in text


def test_dice_state_is_top_level_key(isolated):
    """骰子局是**顶层键**、不按 cid 存（P7 差点踩中的那个坑）。"""
    rtdice._dice_save({"active": True, "players": ["x"]})
    import state_store
    raw = state_store.load_state()
    assert raw.get("dice_game") == {"active": True, "players": ["x"]}
    assert rtdice._dice_state()["players"] == ["x"]
