"""剧情触觉：`【震动】` 行解析、夹紧、时间线、`watch()` 的限流与兜底。"""
import asyncio
import json
import os
import uuid

import pytest

from runtime.haptics import cues, raid

# 沙箱里 pytest 的 tmp_path 会被拒、新建子目录也写不进去 —— 直接在本目录下开临时文件
_HERE = os.path.dirname(os.path.abspath(__file__))


def _mkpath():
    return os.path.join(_HERE, "haptics-raid-{}.json".format(uuid.uuid4().hex[:8]))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每个用例一份干净的配置文件和模块状态。"""
    path = _mkpath()
    monkeypatch.setenv("DZMM_HAPTICS_RAID_FILE", path)
    raid.reset()
    yield path
    raid.reset()
    try:
        os.remove(path)
    except OSError:
        pass


def _write_config(path, **extra):
    data = {"enabled": True, "cids": ["c1"], "mode": "a", "per_turn": 2,
            "cooldown_s": 10}
    data.update(extra)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    raid.load(force=True)
    return path


def test_parses_cue_line():
    got = cues.parse_cues("【震动】警官 55% 1200ms")
    assert len(got) == 1
    assert got[0].role == "警官"
    assert abs(got[0].level - 0.55) < 1e-9
    assert got[0].duration_ms == 1200


def test_parses_without_percent_or_unit():
    got = cues.parse_cues("【震动】侠盗 45 900")
    assert got and abs(got[0].level - 0.45) < 1e-9 and got[0].duration_ms == 900


def test_percent_sign_wins_over_magnitude():
    """带 % 就按百分数读：0.5% 是 0.5%，不是 50%。"""
    got = cues.parse_cues("【震动】警官 0.5% 900ms")
    assert abs(got[0].level - 0.005) < 1e-9
    # 不带 % 的小数就按比例读
    got = cues.parse_cues("【震动】警官 0.5 900ms")
    assert abs(got[0].level - 0.5) < 1e-9


def test_clamps_to_safe_range():
    got = cues.parse_cues("【震动】警官 150% 90000ms")
    assert abs(got[0].level - cues.MAX_LEVEL) < 1e-9
    assert got[0].duration_ms == cues.MAX_MS
    got = cues.parse_cues("【震动】警官 20% 10ms")
    assert got[0].duration_ms == cues.MIN_MS, "过短也要抬到下限，别震成一点"


def test_ignores_text_without_marker():
    assert cues.parse_cues("今晚风很大，巡警在街角点了根烟。") == []
    assert cues.parse_cues("") == []


def test_parse_plain_accepts_model_answer():
    got = cues.parse_plain("55 1200")
    assert got and abs(got.level - 0.55) < 1e-9 and got.duration_ms == 1200
    assert cues.parse_plain("不知道") is None
    assert cues.parse_plain("30") is None


def test_per_turn_cap_and_timeline():
    text = "【震动】警官 25% 600ms 又【震动】侠盗 45% 900ms"
    assert len(cues.parse_cues(text)) == 2
    assert len(cues.per_turn(cues.parse_cues(text), 1)) == 1
    row = cues.timeline([{"at": 12.5, "text": text}])
    assert [r["role"] for r in row] == ["警官", "侠盗"]
    assert row[0]["at"] == 12.5


def test_config_defaults_to_disabled_when_file_missing():
    conf = raid.load(force=True)
    assert conf["enabled"] is False and conf["cids"] == []
    assert raid.is_raid("c1") is False


def test_config_bad_json_is_disabled_not_crash(_clean):
    with open(_clean, "w", encoding="utf-8") as handle:
        handle.write("{不是 json")
    assert raid.load(force=True)["enabled"] is False


def test_is_raid_only_for_listed_cids(_clean):
    _write_config(_clean)
    assert raid.is_raid("c1") is True
    assert raid.is_raid("c2") is False


def test_collect_uses_prompt_tiers_by_default():
    conf = {"mode": "a", "per_turn": 2}
    got = raid.collect(["【震动】警官 25% 600ms", "【震动】侠盗 45% 900ms"], conf)
    assert [c.role for c in got] == ["警官", "侠盗"]
    assert raid.collect(["没有触觉的一段叙述"], conf) == []


def test_collect_falls_back_to_prompt_tier_when_model_fails(monkeypatch):
    monkeypatch.setattr(raid, "_judge", lambda text: None)
    conf = {"mode": "b", "per_turn": 2}
    got = raid.collect(["【震动】警官 30% 800ms"], conf)
    assert got and got[0].role == "警官", "模型没有结论时必须退回提示词档位"


def test_collect_prefers_model_answer_in_mode_b(monkeypatch):
    monkeypatch.setattr(raid, "_judge", lambda text: cues.Cue("模型", 0.6, 1500))
    conf = {"mode": "b", "per_turn": 2}
    got = raid.collect(["【震动】警官 25% 600ms"], conf)
    assert got and got[0].role == "模型" and got[0].duration_ms == 1500


def test_watch_ignores_groups_not_registered(_clean, monkeypatch):
    _write_config(_clean)
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    called = []

    async def _fake_command(level, duration_ms):
        called.append((level, duration_ms))
        return {"ok": True}

    monkeypatch.setattr(raid._share, "client", lambda: type(
        "C", (), {"command": staticmethod(_fake_command)})())

    async def scenario():
        got = await raid.watch("other-group", [{"content": {"text": "【震动】警官 30% 800ms"}}])
        assert got == 0
        assert called == []
    asyncio.run(scenario())


def test_watch_queues_and_sends_cue(_clean, monkeypatch):
    _write_config(_clean)
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    called = []

    async def _fake_command(level, duration_ms):
        called.append((round(level, 2), duration_ms))
        return {"ok": True}

    monkeypatch.setattr(raid._share, "client", lambda: type(
        "C", (), {"command": staticmethod(_fake_command)})())

    async def scenario():
        got = await raid.watch("c1", [{"content": {"text": "【震动】警官 30% 800ms"}}])
        assert got == 1
        if raid._QUEUE is not None:
            await asyncio.wait_for(raid._QUEUE.join(), timeout=5)
        assert called == [(0.3, 800)]
        # 同角色立刻再来一次要被冷却挡住
        got2 = await raid.watch("c1", [{"content": {"text": "【震动】警官 30% 800ms"}}])
        assert got2 == 0
    asyncio.run(scenario())


def test_watch_skips_self_messages(_clean, monkeypatch):
    _write_config(_clean)
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")

    async def scenario():
        got = await raid.watch("c1", [{"sent_by": "me", "content": {"text": "【震动】警官 30% 800ms"}}],
                               is_self=lambda uid: uid == "me")
        assert got == 0
    asyncio.run(scenario())


def test_watch_without_program_token_does_nothing(_clean, monkeypatch):
    _write_config(_clean)
    monkeypatch.delenv("DZMM_SHARE_PROGRAM_TOKEN", raising=False)

    async def scenario():
        got = await raid.watch("c1", [{"content": {"text": "【震动】警官 30% 800ms"}}])
        assert got == 0
    asyncio.run(scenario())
