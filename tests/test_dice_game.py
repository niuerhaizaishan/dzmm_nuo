"""吹牛骰子判定引擎单测（`dice_game.py` 是纯函数，无外部依赖）。

规则依据：`dice_game.py` 模块 docstring。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dice_game as dg  # noqa: E402


# ---------------------------------------------------------------- 摇骰

def test_roll_dice_shape():
    for n in (1, 5, 10):
        dice = dg.roll_dice(n)
        assert len(dice) == n
        assert all(1 <= d <= 6 for d in dice)


def test_roll_wild_range():
    for _ in range(50):
        assert 1 <= dg.roll_wild() <= 6


def test_dice_str():
    assert dg.dice_str([3, 3, 5, 1, 6]) == "🎲3 🎲3 🎲5 🎲1 🎲6"


# ---------------------------------------------------------------- 数点

def test_count_point_includes_wild():
    """万能点顶任意点数。"""
    all_dice = {"a": [3, 3, 1], "b": [3, 4, 4]}
    # a: 两个 3 + 万能 1 顶 3 = 3；b: 一个 3 = 1 → 共 4
    assert dg.count_point(all_dice, x=3, wild=1, called_wild=False) == 4


def test_count_point_wild_disabled_after_called():
    all_dice = {"a": [3, 3, 1], "b": [3, 4, 4]}
    assert dg.count_point(all_dice, x=3, wild=1, called_wild=True) == 3


def test_count_point_when_x_is_wild_itself():
    """叫的就是万能点数本身时不重复计数。"""
    all_dice = {"a": [3, 3, 1]}
    assert dg.count_point(all_dice, x=1, wild=1, called_wild=False) == 1


# ---------------------------------------------------------------- 加码合法性

def test_first_call_needs_at_least_player_count():
    assert dg.is_legal_raise(None, (3, 2), player_count=3) is True
    assert dg.is_legal_raise(None, (2, 2), player_count=3) is False


def test_raise_by_count_or_face():
    old = (3, 2)
    assert dg.is_legal_raise(old, (4, 1), 3) is True      # 数量变大
    assert dg.is_legal_raise(old, (3, 3), 3) is True      # 同数量、点数变大
    assert dg.is_legal_raise(old, (3, 2), 3) is False     # 原地不动
    assert dg.is_legal_raise(old, (3, 1), 3) is False     # 数量同、点数变小
    assert dg.is_legal_raise(old, (2, 6), 3) is False     # 数量变小


def test_raise_rejects_illegal_face_or_count():
    assert dg.is_legal_raise(None, (0, 3), 3) is False
    assert dg.is_legal_raise(None, (3, 7), 3) is False
    assert dg.is_legal_raise(None, (3, 0), 3) is False


# ---------------------------------------------------------------- 开牌

def test_judge_open_caller_wins_when_enough():
    all_dice = {"a": [3, 3, 1]}
    # 叫 3 个 3，实际 2+1(万能)=3 → 叫的人赢
    assert dg.judge_open(all_dice, (3, 3), wild=1, called_wild=False) is True
    # 叫 4 个 3 → 实际 3 → 开的人赢
    assert dg.judge_open(all_dice, (4, 3), wild=1, called_wild=False) is False


# ---------------------------------------------------------------- 叫数解析

def test_parse_call_valid():
    assert dg.parse_call("3个3") == (3, 3)
    assert dg.parse_call("5个6") == (5, 6)
    assert dg.parse_call(" 2 个 1 ") == (2, 1)


def test_parse_call_invalid():
    for bad in ("三个3", "3个7", "3个", "个3", "3-3", "", "3个0"):
        assert dg.parse_call(bad) is None, bad
