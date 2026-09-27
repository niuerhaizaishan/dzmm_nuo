"""状态机存取管线单测（发骚 / 调教 / 公演 / 剧情）。

这五套 `_get_xxx_state` / `_set_xxx_state` 是几乎相同的 load→get→save 样板
（`优化分析报告.md` P2-7 点名过）。P8 拆分时要合并成通用存取器，
所以先把**当前行为**钉住：往返一致 + 缺省为空 + 互不串台。

不依赖状态键名，因此对后续重构友好。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

MACHINES = [
    ("fasao", "_get_fasao_state", "_set_fasao_state"),
    ("diaojiao", "_get_dj_state", "_set_dj_state"),
    ("perf", "_get_perf_state", "_set_perf_state"),
    ("story", "_get_story_state", "_set_story_state"),
    ("vote", "_get_vote_state", "_set_vote_state"),
]


@pytest.mark.parametrize("name,getter,setter", MACHINES)
def test_roundtrip(state, name, getter, setter):
    get, put = getattr(bot, getter), getattr(bot, setter)
    payload = {"active": True, "marker": name}
    put("c1", payload)
    assert get("c1") == payload


@pytest.mark.parametrize("name,getter,setter", MACHINES)
def test_missing_group_defaults_empty(state, name, getter, setter):
    get = getattr(bot, getter)
    assert get("no-such-group") == {}


@pytest.mark.parametrize("name,getter,setter", MACHINES)
def test_machines_are_per_group(state, name, getter, setter):
    get, put = getattr(bot, getter), getattr(bot, setter)
    put("c1", {"who": "c1"})
    put("c2", {"who": "c2"})
    assert get("c1") == {"who": "c1"}
    assert get("c2") == {"who": "c2"}


def test_machines_do_not_collide_in_same_group(state):
    """五个状态机写同一个 cid 时互不覆盖（各自有独立键）。"""
    for name, getter, setter in MACHINES:
        getattr(bot, setter)("c1", {"marker": name})
    for name, getter, setter in MACHINES:
        assert getattr(bot, getter)("c1") == {"marker": name}, name


def test_clearing_one_machine_keeps_others(state):
    bot._set_fasao_state("c1", {"active": True})
    bot._set_vote_state("c1", {"active": True})
    bot._set_fasao_state("c1", {})
    assert bot._get_fasao_state("c1") == {}
    assert bot._get_vote_state("c1") == {"active": True}


def test_state_machine_checks_run_off_the_loop_thread():
    """状态机的"时间驱动检查"必须走 `to_thread` —— 它们内部会调模型。

    2026-09-14 的事故是同款：主循环里同步等模型 → 把群堵到失联（实堵 37 分钟）。
    按群并行之后堵的只是那个群，但一样是"发一条消息等十几秒"。
    这条用**源码断言**钉住：这些函数不许再被同步调用。
    """
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    for fn in ("_check_diaojiao", "_check_perf_interact", "_check_story",
               "_check_fasao_timeout", "_check_vote", "_dj_end_text"):
        assert "= " + fn + "(" not in src, \
            "{} 在主循环里被同步调用了（要 `await asyncio.to_thread(...)`）".format(fn)
        assert fn in src, "{} 不见了？".format(fn)
