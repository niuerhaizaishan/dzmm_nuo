"""P8 第 4 步：四个状态机（投票/剧情/公演/发骚/调教）的**分派重叠**与纯逻辑。

`docs/保全清单.md` 把几条"开始判定与结束判定同时命中"的现状行为登记为
**重构高危项**：顺序变了行为会静默改变。这批测试把那些重叠逐条钉住 ——
以后谁重排分派、谁改谓词，这里会说话。

⚠️ 这里钉的是**现状行为**，不是"应该怎样"。
例如 `/投票结束` 同时命中开始与结束，看起来像 bug，但线上就是这个行为。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import runtime.diaojiao as dj  # noqa: E402
import runtime.fasao as fasao  # noqa: E402
import runtime.heat as heat  # noqa: E402
import runtime.story as story  # noqa: E402
import runtime.vote as vote  # noqa: E402


# ---------------------------------------------------------------- 分派重叠

def test_vote_end_word_hits_both_start_and_end():
    """`/投票结束` 同时命中开始与结束。

    没有进行中的投票时，结束判定被 `active` 挡掉 → 落到开始判定 →
    **开出一个标题叫「结束」的投票**。这是分派顺序在兜着的行为。
    """
    assert vote.is_vote_start_cmd("/投票结束") is True
    assert vote.is_vote_end_cmd("/投票结束") is True


def test_plain_vote_hits_start_only():
    assert vote.is_vote_start_cmd("/投票") is True
    assert vote.is_vote_end_cmd("/投票") is False


def test_fasao_end_word_hits_three_predicates():
    """`/发骚结束` 同时命中发骚、场景、发骚结束三个判定。

    没有进行中的发骚时会落到场景判定 → **开一场目标叫「结束」的场景**。
    """
    assert fasao.is_fasao_cmd("/发骚结束") is True
    assert fasao.is_scene_cmd("/发骚结束") is True
    assert fasao.is_fasao_end_cmd("/发骚结束") is True


def test_plain_fasao_hits_two_predicates():
    """`/发骚` 被发骚与场景两个功能抢，靠分派顺序区分。"""
    assert fasao.is_fasao_cmd("/发骚") is True
    assert fasao.is_scene_cmd("/发骚") is True
    assert fasao.is_fasao_end_cmd("/发骚") is False


def test_story_end_word_does_not_overlap_start():
    """实测澄清：剧情**没有**投票那种重叠（文档曾写错，这里钉住事实）。"""
    assert story.is_story_start_cmd("/剧情结束") is False
    assert story.is_story_end_cmd("/剧情结束") is True
    assert story.is_story_start_cmd("/剧情") is True
    assert story.is_story_end_cmd("/剧情") is False


def test_diaojiao_end_word_hits_start_predicate():
    """`/调教结束`：开始判定把「结束」当成**目标名**返回，结束判定也命中。

    这正是保全清单 #3 记的那条：没有进行中的调教时，会开一场目标叫「结束」的调教。
    """
    assert dj.is_diaojiao_cmd("/调教结束") == "结束"
    assert dj.is_dj_end_cmd("/调教结束") is True


def test_diaojiao_needs_a_target_to_start():
    """`/调教` 不带目标不算开始（返回空串 = "不是开始命令"）。"""
    assert dj.is_diaojiao_cmd("/调教") == ""
    assert dj.is_diaojiao_cmd("/调教 依依") == "依依"
    assert dj.is_dj_end_cmd("/调教 依依") is False


# ---------------------------------------------------------------- 发情值

@pytest.fixture
def heat_state(state, monkeypatch):
    monkeypatch.setattr(heat, "is_self", lambda uid: uid in ("nuo", "bot"))
    return heat


def test_add_heat_accumulates(heat_state, state):
    heat_state._add_heat("cid-1", "u1", heat.HEAT_VOTE_VOTER)
    heat_state._add_heat("cid-1", "u1", heat.HEAT_VOTE_VOTER)
    assert state["cid-1"]["heat_value"] == {"u1": 10}


def test_add_heat_ignores_self(heat_state, state):
    heat_state._add_heat("cid-1", "nuo", 10)
    heat_state._add_heat("cid-1", "bot", 10)
    assert state.get("cid-1", {}).get("heat_value") in (None, {})


def test_add_heat_ignores_empty_uid_and_non_positive(heat_state, state):
    heat_state._add_heat("cid-1", "", 10)
    heat_state._add_heat("cid-1", "u1", 0)
    heat_state._add_heat("cid-1", "u1", -5)
    assert state.get("cid-1", {}).get("heat_value") in (None, {})


def test_heat_constants_unchanged():
    """调平衡只改常量；改错了这条会说话（与 test_p8_refactor 双保险）。"""
    assert heat.HEAT_SCENE_PICK == 10
    assert heat.HEAT_INSULT_TARGET == 15
    assert heat.HEAT_FASAO_PICK == 15
    assert heat.HEAT_DIAOJIAO_ROUND == 20
    assert heat.HEAT_VOTE_STARTER == 8
    assert heat.HEAT_VOTE_VOTER == 5
    assert heat.HEAT_STORY_VOTER == 8
    assert heat.HEAT_STORY_PROTAGONIST == 15


# ---------------------------------------------------------------- 场景骰子

def test_pick_combo_by_xp_uses_bias_lists():
    """带 XP 时只能从该 XP 的偏好里挑（否则"按 XP 定制"是假的）。"""
    for _ in range(30):
        scene, pos, extra = fasao._pick_combo_by_xp(["露出"])
        assert scene in fasao.XP_BIAS["露出"]["scene"]
        assert extra in fasao.XP_BIAS["露出"]["extra"]
        assert pos in fasao.POSITIONS          # 露出没有 pos 偏好 → 全量随机


def test_pick_combo_by_xp_without_xp_uses_full_lists():
    for _ in range(20):
        scene, pos, extra = fasao._pick_combo_by_xp(None)
        assert scene in fasao.SCENES
        assert pos in fasao.POSITIONS
        assert extra in fasao.EXTRAS


def test_pick_combo_by_xp_unknown_tag_falls_back():
    scene, pos, extra = fasao._pick_combo_by_xp(["不存在的XP"])
    assert scene in fasao.SCENES and pos in fasao.POSITIONS and extra in fasao.EXTRAS


# ---------------------------------------------------------------- 常量没被抄漏

def test_diaojiao_participate_hint_is_complete():
    """`DJ_PARTICIPATE_HINT` 曾被我在 slice header 里抄漏后半句。

    搬过来的原文才是权威；这条测试防的是"手抄常量"这类事故再发生。
    """
    assert "想参与的姐妹也来玩呀" in dj.DJ_PARTICIPATE_HINT
    assert "30秒没人我就自己继续啦" in dj.DJ_PARTICIPATE_HINT
    assert dj.DJ_PARTICIPATE_HINT.endswith("～")


def test_vote_default_seconds():
    assert vote.VOTE_DEFAULT_SECONDS == 2 * 60


def test_story_limits():
    assert story.STORY_MAX_ROUND == 6
    assert story.STORY_VOTE_SECONDS == 60


def test_fasao_timeout_is_15_minutes():
    assert fasao.FASAO_TIMEOUT == 15 * 60
