"""投票状态机单测（`bot.py` 的 vote 状态存取与结算）。

状态键为 `state[cid]["vote"]`。用 conftest 的 `state` 夹具隔离。

注：`start_vote()` 会调 LLM 生成选项，不在本测试范围（需要 mock）；
这里覆盖**存取 + 记录 + 结算**三段纯状态逻辑。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from runtime import dispatch, vote


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


def test_tea_vote_from_topic_through_dispatch_and_end(state, monkeypatch):
    cid = vote.TEA_CID
    sent = []
    monkeypatch.setattr(dispatch, "_feat_on", lambda group, key: group == cid and key == "vote")
    monkeypatch.setattr(vote, "_vote_members_text", lambda *a: "")
    monkeypatch.setattr(vote, "_extract_mentioned_members", lambda *a: [])
    monkeypatch.setattr(vote, "generate_vote_options", lambda title, members: ["火锅", "烧烤"])
    monkeypatch.setattr(vote, "get_name", lambda uid: uid)
    bot._set_vote_state("c1", {"active": True, "votes": {"u-other": "1"}})

    class Sender:
        async def send(self, text, group):
            assert group == cid
            sent.append(text)
            return True

    async def issue(text, uid):
        msg = {"message_id": text, "content": {"text": text}}
        await dispatch.handle_slash(Sender(), cid, text, uid, False, {}, [msg], msg, {},
            _save_group=lambda *a: None, _mark_inflight=lambda *a: None,
            _remember_turn=lambda *a: None, rank_summary=lambda: "", sync_profiles=lambda: "")

    async def run():
        await issue("＃投票 晚上吃什么", "u-starter")
        await issue("#选 1", "u-a")
        await issue("#2", "u-a")
        await issue("#选 9", "u-b")
        await issue("#投票结束", "u-starter")
        await issue("#投票结束", "u-starter")

    asyncio.run(run())
    assert len(sent) == 4
    assert "#选 X 或 #X" in sent[0] and "【2】烧烤" in sent[0]
    assert "之间的选项编号" in sent[1]
    assert "火锅　0票" in sent[2] and "烧烤　1票(100%)　u-a" in sent[2]
    assert "没有进行中的投票" in sent[3]
    assert bot._get_vote_state(cid) == {}
    assert bot._get_vote_state("c1")["votes"] == {"u-other": "1"}


def test_tea_vote_timer_reminds_with_hash_then_finishes(state, monkeypatch):
    cid = vote.TEA_CID
    monkeypatch.setattr(vote.time, "time", lambda: 100)
    intro = vote.start_vote(cid, "#投票 晚饭 | 火锅 | 烧烤", "u-starter")
    assert "2分钟倒计时" in intro
    monkeypatch.setattr(vote.time, "time", lambda: 131)
    assert "#选 X 或 #X" in vote._check_vote(cid)
    monkeypatch.setattr(vote.time, "time", lambda: 220)
    assert "投票结果" in vote._check_vote(cid)
    assert bot._get_vote_state(cid) == {}
