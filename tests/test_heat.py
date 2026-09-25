"""发情值 / 高潮结算单测（`bot._add_heat` / `_climax_hits` / `_parse_judge`）。

用 conftest 的 `state` 夹具隔离状态，绝不触碰真实 state.json。
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- _add_heat

def test_add_heat_accumulates(state):
    bot._add_heat("c1", "u1", 10)
    bot._add_heat("c1", "u1", 15)
    assert state["c1"]["heat_value"]["u1"] == 25


def test_add_heat_is_per_group(state):
    bot._add_heat("c1", "u1", 10)
    bot._add_heat("c2", "u1", 7)
    assert state["c1"]["heat_value"]["u1"] == 10
    assert state["c2"]["heat_value"]["u1"] == 7


def test_add_heat_excludes_bot_account(state):
    """小小糯自己不计入发情值。"""
    bot._add_heat("c1", bot.NUO_UID, 10)
    assert "c1" not in state


def test_add_heat_ignores_nonpositive_and_empty(state):
    bot._add_heat("c1", "u1", 0)
    bot._add_heat("c1", "u1", -5)
    bot._add_heat("c1", "", 10)
    assert "c1" not in state


# ---------------------------------------------------------------- _climax_hits

def test_climax_triggers_at_100_and_keeps_remainder():
    gs = {"heat_value": {"u1": 105, "u2": 99}, "climax_date": TODAY}
    hits, changed = bot._climax_hits(gs)
    assert hits == [("u1", 105)]
    assert gs["heat_value"]["u1"] == 5          # 溢出计入下一轮
    assert gs["heat_value"]["u2"] == 99         # 未满不动
    assert gs["heat_climax"]["u1"] == 1
    assert gs["heat_climax_today"]["u1"] == 1
    assert changed is True


def test_climax_no_op_below_threshold():
    gs = {"heat_value": {"u1": 50}, "climax_date": TODAY}
    hits, changed = bot._climax_hits(gs)
    assert hits == []
    assert changed is False


def test_climax_multiple_and_double_overflow():
    """一次可触发多人；同一人 200+ 也只结算一次（规则如此）。"""
    gs = {"heat_value": {"u1": 210, "u2": 100}, "climax_date": TODAY}
    hits, _ = bot._climax_hits(gs)
    assert ("u1", 210) in hits and ("u2", 100) in hits
    assert gs["heat_value"]["u1"] == 110        # 只减 100 一次
    assert gs["heat_climax"]["u1"] == 1


def test_climax_resets_today_counter_on_new_day():
    gs = {"heat_value": {}, "climax_date": "1970-01-01",
          "heat_climax_today": {"u1": 3}}
    _, changed = bot._climax_hits(gs)
    assert gs["heat_climax_today"] == {}
    assert gs["climax_date"] == TODAY
    assert changed is True


def test_climax_first_ever_call_marks_changed():
    """climax_date 缺失时视为跨天，需写回磁盘。"""
    gs = {"heat_value": {}}
    hits, changed = bot._climax_hits(gs)
    assert hits == []
    assert changed is True


# ---------------------------------------------------------------- _parse_judge

def test_parse_judge_plain_json():
    assert bot._parse_judge('[{"name":"甲","delta":10}]') == {"甲": 10}


def test_parse_judge_with_code_fence():
    raw = '```json\n[{"name":"甲","delta":10}]\n```'
    assert bot._parse_judge(raw) == {"甲": 10}


def test_parse_judge_embedded_in_text():
    raw = '好的，评定如下：[{"name":"甲","delta":7}] 完毕'
    assert bot._parse_judge(raw) == {"甲": 7}


def test_parse_judge_string_delta_coerced():
    assert bot._parse_judge('[{"name":"甲","delta":"7"}]') == {"甲": 7}


def test_parse_judge_filters_nonpositive_and_blank():
    raw = '[{"name":"甲","delta":0},{"name":"","delta":5},{"name":"乙","delta":-3}]'
    assert bot._parse_judge(raw) == {}


def test_parse_judge_garbage_returns_empty():
    for bad in ("", "不是 JSON", "{}", "[]", '{"name":"甲","delta":5}'):
        assert bot._parse_judge(bad) == {}, bad


def test_parse_judge_ignores_non_dict_items():
    assert bot._parse_judge('["甲", {"name":"乙","delta":4}]') == {"乙": 4}
