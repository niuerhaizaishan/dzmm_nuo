"""P8 第 4 步的守卫测试：模块拆分（bot.py -> runtime/*）。

拆分最容易出的事故不是"函数搬错了"，而是**搬完两边都定义**：
bot.py 里留着一个旧定义、模块里有一个新定义 —— 到底跑哪个取决于 import 顺序，
而且测试全绿（两边代码本来就一样）。等哪天只改了一边，问题才会以
"改了没生效"的形式出现，届时极难定位。

所以这里第一组测试盯的是**结构不变量**，第二组才是行为。
"""
import ast
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import runtime.ctx as ctx  # noqa: E402
import runtime.dice as rtdice  # noqa: E402
import runtime.identity as identity  # noqa: E402
import runtime.obs as obs  # noqa: E402

BOT_PY = ROOT / "bot.py"

# 已搬走的函数 -> 它们现在住的模块（每拆一片就加一行）
MOVED = {
    "log": obs,
    "self_uids": identity, "is_self": identity, "self_names": identity,
    "_load_member_names": identity, "get_full_name": identity,
    "get_name": identity, "is_at_me": identity,
    "_dice_state": rtdice, "_dice_save": rtdice, "_dice_current_player": rtdice,
    "_dice_send_private": rtdice, "_dice_board_text": rtdice,
    "_dice_deal_round": rtdice, "_dice_start_cmd": rtdice, "_dice_join_cmd": rtdice,
    "_dice_exit_cmd": rtdice, "_dice_begin_cmd": rtdice,
    "_dice_next_round_cmd": rtdice, "_dice_call_cmd": rtdice,
    "_dice_open_cmd": rtdice, "_dice_quit_cmd": rtdice, "_dice_skip_cmd": rtdice,
    "_dice_status_text": rtdice,
}

# 侦察量出来的回指数：新模块提到 `bot` 多少次。
# 目标始终是 0；涨了就必须在这里显式登记并说明为什么。
BACKREFS = {"obs": 0, "identity": 0, "dice": 0, "ctx": 0}


def _bot_defs():
    tree = ast.parse(BOT_PY.read_text(encoding="utf-8"))
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


# ---------------------------------------------------------------- 结构不变量

def test_moved_functions_are_gone_from_bot_py():
    """bot.py 里不得再有这些函数的定义（只允许 re-export）。"""
    left = sorted(set(MOVED) & _bot_defs())
    assert left == [], "bot.py 里还有旧定义（会被 import 覆盖成哪个说不清）: {}".format(left)


def test_bot_reexports_the_same_objects():
    """bot 上的名字必须与模块里**同一个对象**，而不是又包了一层。"""
    for name, mod in MOVED.items():
        assert getattr(bot, name, None) is getattr(mod, name), name


def test_no_function_is_defined_in_both_bot_and_runtime():
    """全自动的"搬完两边都定义"守卫。

    比逐个列名字强：以后每拆一片都自动生效，不需要记得往这里加东西。
    两边同名的后果是"跑哪个取决于 import 顺序"，而且测试全绿（代码本来就一样）
    —— 直到有人只改了一边，才会以"改了没生效"的形式爆出来。
    """
    bot_defs = _bot_defs()
    clash = {}
    for p in sorted((ROOT / "runtime").glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = {n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        both = sorted(names & bot_defs)
        if both:
            clash[p.name] = both
    assert clash == {}, "两边都定义了同一个函数: {}".format(clash)


def test_no_backreference_to_bot_in_moved_modules():
    """拆出去就应该是单向依赖。回指数涨了 = 只是把文字挪了个位置。"""
    for mod, expected in BACKREFS.items():
        src = (ROOT / "runtime" / (mod + ".py")).read_text(encoding="utf-8")
        n = sum(1 for ln in src.splitlines()
                if ln.strip().startswith(("import bot", "from bot import")))
        assert n == expected, "{} 回指 bot.py {} 次（期望 {}）".format(mod, n, expected)


def test_no_import_bot_in_runtime_package():
    """整包普查：runtime/ 里目前一处都不该 import bot。"""
    hits = []
    for p in (ROOT / "runtime").glob("*.py"):
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
