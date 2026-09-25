"""投票状态机单测（`bot.py` 的 vote 状态存取与结算）。

状态键为 `state[cid]["vote"]`。用 conftest 的 `state` 夹具隔离。

注：`start_vote()` 会调 LLM 生成选项，不在本测试范围（需要 mock）；
这里覆盖**存取 + 记录 + 结算**三段纯状态逻辑。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402


def test_vote_state_roundtrip(state):
    bot._set_vote_state("c1", {"active": True, "starter": "u1"})
    assert bot._get_vote_state("c1") == {"active": True, "starter": "u1"}


def test_vote_state_defaults_to_empty(state):
    assert bot._get_vote_state("nope") == {}


def test_record_vote_ignored_when_no_active_vote(state):
    bot._record_vote("c1", "u1", "1")
    assert bot._get_vote_state("c1").get("votes") in (None, {})


def test_record_vote_stores_choice(state):
    bot._set_vote_state("c1", {"active": True, "starter": "u1", "votes": {}})
    bot._record_vote("c1", "u2", "1")
    assert bot._get_vote_state("c1")["votes"] == {"u2": "1"}


def test_record_vote_last_one_wins(state):
    bot._set_vote_state("c1", {"active": True, "starter": "u1", "votes": {}})
    bot._record_vote("c1", "u2", "1")
    bot._record_vote("c1", "u2", "3")
    assert bot._get_vote_state("c1")["votes"] == {"u2": "3"}


def test_finish_vote_clears_state_and_awards_heat(state):
    """结算：发起人 +8，其他投票者 +5，票局清空。"""
    bot._set_vote_state("c1", {
        "active": True, "starter": "u-starter", "votes": {"u-a": "1", "u-b": "1"},
        "options": ["甲", "乙"], "title": "测试投票",
    })
    bot._finish_vote("c1")

    heat = state["c1"]["heat_value"]
    assert heat["u-starter"] == 8
    assert heat["u-a"] == 5
    assert heat["u-b"] == 5
    assert bot._get_vote_state("c1") == {}


def test_finish_vote_does_not_double_count_starter(state):
    """发起人也投了票时，只算发起人那份 +8，不叠加 +5。"""
    bot._set_vote_state("c1", {
        "active": True, "starter": "u-starter", "votes": {"u-starter": "1"},
        "options": ["甲"], "title": "测试投票",
    })
    bot._finish_vote("c1")
    assert state["c1"]["heat_value"]["u-starter"] == 8


def test_finish_vote_excludes_bot_account(state):
    bot._set_vote_state("c1", {
        "active": True, "starter": bot.NUO_UID, "votes": {bot.NUO_UID: "1"},
        "options": ["甲"], "title": "测试投票",
    })
    bot._finish_vote("c1")
    assert (state.get("c1") or {}).get("heat_value") in (None, {})
