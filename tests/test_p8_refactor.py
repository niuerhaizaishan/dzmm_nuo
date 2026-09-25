"""P8 第 1、2 步的守卫测试：状态存取器 + 发情值常量。

第 1 步：8 组"读全部群 → 改 → 写全部群"的样板 → `state_store.group_get/group_set`
第 2 步：散落的 5/8/10/15/20 → `HEAT_*` 常量
"""
import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
import state_store  # noqa: E402

BOT_PY = Path(__file__).resolve().parent.parent / "bot.py"
CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"


# ---------------------------------------------------------------- 第 2 步：常量

# 取值必须与 P8 之前的字面量**逐个相同**（改规则时要显式改这里）
EXPECTED_HEAT = {
    "HEAT_SCENE_PICK": 10,
    "HEAT_SCENE_CONTINUE_TARGET": 10,
    "HEAT_SCENE_CONTINUE_SENDER": 10,
    "HEAT_INSULT_TARGET": 15,
    "HEAT_INSULT_SENDER": 10,
    "HEAT_FASAO_PICK": 15,
    "HEAT_DIAOJIAO_ROUND": 20,
    "HEAT_VOTE_STARTER": 8,
    "HEAT_VOTE_VOTER": 5,
    "HEAT_STORY_VOTER": 8,
    "HEAT_STORY_PROTAGONIST": 15,
}


@pytest.mark.parametrize("name,value", sorted(EXPECTED_HEAT.items()))
def test_heat_constant_values(name, value):
    assert getattr(bot, name) == value


def test_all_declared_heat_constants_are_expected():
    declared = {n for n in dir(bot) if n.startswith("HEAT_")}
    assert declared == set(EXPECTED_HEAT), declared ^ set(EXPECTED_HEAT)


def test_no_bare_numeric_add_heat_calls():
    """迁移守卫：`_add_heat` 的调用不应再出现裸数字。"""
    src = BOT_PY.read_text(encoding="utf-8")
    left = re.findall(r"_add_heat\([^)]*,\s*\d+\)", src)
    assert left == [], left


def test_no_old_state_boilerplate_in_accessors():
    """8 组存取器不该再自己 load/save 整个 state。"""
    for fn in ("_get_fasao_state", "_set_fasao_state", "_get_dj_state", "_set_dj_state",
               "_get_perf_state", "_set_perf_state", "_get_vote_state", "_set_vote_state",
               "_get_story_state", "_set_story_state", "_dice_state", "_dice_save",
               "_get_memory", "_set_last_action", "_get_last_action", "_add_heat"):
        src = inspect.getsource(getattr(bot, fn))
        assert "state_store" in src, fn
        assert "save_state(" not in src, fn


# ---------------------------------------------------------------- 第 1 步：行为

def test_group_set_writes_only_that_group(isolated):
    """单键写入：改 A 群不该动到 B 群。"""
    state_store.save_state({CID_A: {"a": 1}, CID_B: {"b": 2}})
    state_store.group_set(CID_A, "fasao", {"active": True})
    st = state_store.load_state()
    assert st[CID_A]["fasao"] == {"active": True}
    assert st[CID_A]["a"] == 1                 # 同群其它键保留
    assert st[CID_B] == {"b": 2}               # 别的群完全没动


def test_group_set_creates_group_if_absent(isolated):
    state_store.group_set(CID_A, "vote", {"active": True})
    assert state_store.load_state()[CID_A] == {"vote": {"active": True}}


def test_group_get_defaults(isolated):
    assert state_store.group_get(CID_A, "nope") is None
    assert state_store.group_get(CID_A, "nope", {}) == {}


def test_top_set_get_roundtrip(isolated):
    state_store.top_set("dice_game", {"active": True, "order": ["u1"]})
    assert state_store.top_get("dice_game") == {"active": True, "order": ["u1"]}
    assert state_store.top_get("nope") is None


def test_five_machines_do_not_interfere_via_store(isolated):
    """五个状态机写同一个群时互不覆盖（单键读写的关键性质）。"""
    pairs = [("fasao", bot._set_fasao_state, bot._get_fasao_state),
             ("diaojiao", bot._set_dj_state, bot._get_dj_state),
             ("perf", bot._set_perf_state, bot._get_perf_state),
             ("vote", bot._set_vote_state, bot._get_vote_state),
             ("story", bot._set_story_state, bot._get_story_state)]
    for name, setter, _ in pairs:
        setter(CID_A, {"marker": name})
    for name, _, getter in pairs:
        assert getter(CID_A) == {"marker": name}, name


def test_memory_roundtrip_via_store(isolated):
    bot._remember(CID_A, "我喜欢芒果", "糯糯")
    mem = bot._get_memory(CID_A)
    assert len(mem) == 1
    assert mem[0]["text"] == "我喜欢芒果"
    assert mem[0]["from"] == "糯糯"


def test_memory_window_is_capped(isolated):
    for i in range(bot.MEMORY_MAX + 5):
        bot._remember(CID_A, "第{}条".format(i), "糯糯")
    mem = bot._get_memory(CID_A)
    assert len(mem) == bot.MEMORY_MAX


def test_add_heat_via_store(isolated):
    bot._add_heat(CID_A, "u1", bot.HEAT_VOTE_STARTER)
    assert bot._add_heat and state_store.group_get(CID_A, "heat_value") == {"u1": 8}
