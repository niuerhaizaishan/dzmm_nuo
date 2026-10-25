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


def test_drama_curve_is_monotonic_and_clamped():
    """模型只给紧张度，强度/时长由代码映射 —— 必须单调、不越上限。"""
    rows = [raid.from_drama(score) for score in (0, 25, 50, 75, 100)]
    levels = [row.level for row in rows]
    durations = [row.duration_ms for row in rows]
    assert levels == sorted(levels) and durations == sorted(durations), "必须单调递增"
    assert levels[0] >= 0.2 and abs(levels[-1] - cues.MAX_LEVEL) < 1e-9
    assert raid.from_drama(999).level == raid.from_drama(100).level, "越界要夹住"
    assert raid.from_drama(-5).level == raid.from_drama(0).level
    assert raid.from_drama("abc") is None


def test_beat_tiers_have_rhythm_and_cap():
    """每档都要有节奏；时长不得超过 20 秒（屋主定的上限）。"""
    beats = [raid.from_drama(score) for score in (10, 30, 50, 70, 95)]
    assert [b.rhythm for b in beats] == ["steady", "breathe", "heartbeat", "wave", "stutter"]
    assert all(b.duration_ms <= 20000 for b in beats)
    assert beats[0].duration_ms >= 5000, "平静段落也别短成一下"
    assert len({b.rhythm for b in beats}) == 5, "五档节奏不该重复"


def test_baseline_is_first_device_step():
    """背景层用设备第 1 档（5%）：1% 会取整成 0 = 不转。"""
    assert abs(raid.BASELINE - 0.05) < 1e-9



def test_judge_maps_a_single_number(monkeypatch):
    import brains

    monkeypatch.setattr(brains, "_call_llm_raw",
                        lambda *a, **k: ("72", "stop"))
    got = raid._judge("一段很长的叙述" * 20)
    assert got is not None and abs(got.level - raid.from_drama(72).level) < 1e-9


def test_judge_falls_back_to_plain_pair(monkeypatch):
    import brains

    monkeypatch.setattr(brains, "_call_llm_raw",
                        lambda *a, **k: ("55 1200", "stop"))
    got = raid._judge("一段很长的叙述" * 20)
    assert got is not None and got.duration_ms == 1200


def test_judge_returns_none_when_model_garbage(monkeypatch):
    import brains

    monkeypatch.setattr(brains, "_call_llm_raw", lambda *a, **k: ("不知道", "stop"))
    assert raid._judge("一段很长的叙述" * 20) is None


def test_judge_prompt_carries_previous_level_and_action(monkeypatch):
    """模型必须知道"上一回合多强 + 玩家这回合干了啥"，否则每段都判四五十。"""
    import brains

    seen = {}

    def fake(prompt, content, **kw):
        seen["prompt"] = prompt
        return ("60", "stop")

    monkeypatch.setattr(brains, "_call_llm_raw", fake)
    raid._judge("一段很长的叙述" * 20, {"prev": 0.35, "action": "直接开箱"})
    assert "35%" in seen["prompt"]
    assert "直接开箱" in seen["prompt"]


def test_plan_threads_previous_level_into_judge(_clean):
    """离线仿真也要把上一回合的强度传下去（否则和线上不一致）。"""
    seen = []

    def judge(text, ctx=None):
        seen.append((ctx or {}).get("prev"))
        return cues.Cue("模型", 0.5, 800)

    conf = {"mode": "b", "per_turn": 2, "cooldown_s": 0}
    long_text = "管道检修口的栅格被踹松了，你侧身挤进去。" * 5
    raid.plan([{"at": 0, "text": long_text}, {"at": 120, "text": long_text}],
              conf=conf, judge=judge)
    assert seen == [None, 0.5], "第二次判定必须拿到上一次的强度当锚点"


def test_dm_uids_filter_ignores_players_long_paragraphs(_clean):
    """真实团本里玩家也写大段 IC —— 只认 DM，否则一次会话冒出几十条触觉。"""
    dm = "3adb1285-febb-4ad5-b7da-6ebd76034d66"
    player = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
    conf = {"mode": "b", "per_turn": 2, "cooldown_s": 0, "dm_uids": [dm]}
    long_text = "白光一闪，废墟、直升机、指挥官的怒吼全部消失。" * 4
    judge = lambda text, ctx=None: cues.Cue("DM", 0.5, 800)      # noqa: E731

    rows = raid.plan([{"at": 0, "text": long_text, "uid": player}], conf=conf, judge=judge)
    assert rows[0]["cues"] == [], "玩家的长段落不该触发"
    rows = raid.plan([{"at": 0, "text": long_text, "uid": dm}], conf=conf, judge=judge)
    assert rows[0]["cues"], "DM 的叙述要触发"


def test_without_dm_uids_every_long_text_is_judged(_clean):
    """没配 dm_uids 时退回旧行为（只看长度），保证老群不受影响。"""
    conf = {"mode": "b", "per_turn": 2, "cooldown_s": 0}
    long_text = "白光一闪，废墟、直升机、指挥官的怒吼全部消失。" * 4
    rows = raid.plan([{"at": 0, "text": long_text, "uid": "someone"}], conf=conf,
                     judge=lambda text, ctx=None: cues.Cue("模型", 0.5, 800))
    assert rows[0]["cues"]


def test_collect_still_parses_a_lone_cue_line(_clean):
    """短消息里单独一行的【震动】也要认（a 路线）。"""
    conf = {"mode": "b", "per_turn": 2, "dm_uids": ["dm"]}
    got = raid.collect([{"text": "【震动】警官 30% 800ms", "uid": "dm"}], conf)
    assert got and got[0].duration_ms == 800




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
    monkeypatch.setattr(raid, "_judge", lambda text, ctx=None: None)
    conf = {"mode": "b", "per_turn": 2}
    got = raid.collect(["【震动】警官 30% 800ms"], conf)
    assert got and got[0].role == "警官", "模型没有结论时必须退回提示词档位"


def test_collect_prefers_model_answer_in_mode_b(monkeypatch):
    monkeypatch.setattr(raid, "_judge", lambda text, ctx=None: cues.Cue("模型", 0.6, 1500))
    conf = {"mode": "b", "per_turn": 2}
    long_text = "管道检修口的栅格被军靴第二下就踹松了。" * 6 + "【震动】警官 25% 600ms"
    got = raid.collect([long_text], conf)
    assert got and got[0].role == "模型" and got[0].duration_ms == 1500


def test_mode_b_skips_short_texts_to_save_model_calls(monkeypatch):
    """状态栏/短回复不该调模型，也不该震；短文本只走 a 的兜底解析。"""
    calls = []
    monkeypatch.setattr(raid, "_judge", lambda text, ctx=None: calls.append(text) or None)
    conf = {"mode": "b", "per_turn": 2}
    assert raid.collect(["【进度】密码 ①— ②— ③—｜钥匙 有/无"], conf) == []
    assert calls == [], "太短，不该调模型"
    assert len("【进度】密码 ①— ②— ③—｜钥匙 有/无") < raid.MIN_NARRATION



def test_watch_ignores_groups_not_registered(_clean, monkeypatch):
    _write_config(_clean)
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    called = []

    async def _fake_baseline(value):
        called.append(("base", round(value, 3)))
        return {"ok": True}

    async def _fake_beat(level, duration_ms, rhythm="steady"):
        called.append((round(level, 2), duration_ms, rhythm))
        return {"ok": True}

    monkeypatch.setattr(raid._share, "client", lambda: type(
        "C", (), {"baseline": staticmethod(_fake_baseline),
                  "beat": staticmethod(_fake_beat)})())

    async def scenario():
        got = await raid.watch("other-group", [{"content": {"text": "【震动】警官 30% 800ms"}}])
        assert got == 0
        assert called == []
    asyncio.run(scenario())


def test_watch_queues_and_sends_cue(_clean, monkeypatch):
    _write_config(_clean)
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    called = []

    async def _fake_baseline(value):
        called.append(("base", round(value, 3)))
        return {"ok": True}

    async def _fake_beat(level, duration_ms, rhythm="steady"):
        called.append((round(level, 2), duration_ms, rhythm))
        return {"ok": True}

    monkeypatch.setattr(raid._share, "client", lambda: type(
        "C", (), {"baseline": staticmethod(_fake_baseline),
                  "beat": staticmethod(_fake_beat)})())

    async def scenario():
        got = await raid.watch("c1", [{"content": {"text": "【震动】警官 30% 800ms"}}])
        assert got == 1
        if raid._QUEUE is not None:
            await asyncio.wait_for(raid._QUEUE.join(), timeout=5)
        assert called == [("base", 0.05), (0.3, 800, "steady")], \
            "先开背景层，再下发段落"
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


def test_same_turn_two_cues_both_go_out(_clean, monkeypatch):
    """同一回合解析出两条时，是排着队发两条 —— 不能被 3 秒间隔按条误伤。"""
    _write_config(_clean)
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    called = []

    async def _fake_baseline(value):
        called.append(("base", round(value, 3)))
        return {"ok": True}

    async def _fake_beat(level, duration_ms, rhythm="steady"):
        called.append((round(level, 2), duration_ms, rhythm))
        return {"ok": True}

    monkeypatch.setattr(raid._share, "client", lambda: type(
        "C", (), {"baseline": staticmethod(_fake_baseline),
                  "beat": staticmethod(_fake_beat)})())

    async def scenario():
        got = await raid.watch("c1", [{"content": {"text":
            "【震动】警官 25% 600ms 又【震动】侠盗 45% 900ms 再【震动】警官 70% 3000ms"}}])
        assert got == 2, "每回合上限 2 条，两条都要发出去"
        if raid._QUEUE is not None:
            await asyncio.wait_for(raid._QUEUE.join(), timeout=8)
        beats = [row for row in called if row[0] != "base"]
        assert beats == [(0.25, 600, "steady"), (0.45, 900, "steady")]
    asyncio.run(scenario())


def test_third_cue_of_same_turn_is_dropped(_clean, monkeypatch):
    _write_config(_clean, per_turn=2)
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")

    async def scenario():
        got = await raid.watch("c1", [{"content": {"text":
            "【震动】警官 25% 600ms【震动】侠盗 45% 900ms【震动】警官 70% 3000ms"}}])
        assert got == 2
    asyncio.run(scenario())


def test_plan_replays_history_with_cooldowns(_clean):
    """离线仿真：同一套冷却规则。隔 30 秒的两次都触发，隔 1 秒的被挡。"""
    _write_config(_clean, mode="a", per_turn=2, cooldown_s=10)
    items = [
        {"at": 0, "text": "开场，没有触觉。"},
        {"at": 30, "text": "【震动】警官 30% 800ms"},
        {"at": 31, "text": "【震动】警官 55% 1200ms"},     # 同角色 1 秒内 → 挡
        {"at": 75, "text": "【震动】警官 55% 1200ms"},     # 隔 45 秒 → 放
    ]
    rows = raid.plan(items)
    fired = [(r["at"], [round(c["level"], 2) for c in r["cues"]]) for r in rows]
    assert fired == [(0, []), (30, [0.3]), (31, []), (75, [0.55])]
    assert rows[2]["dropped"], "被挡掉的那条要能在仿真里看到原因"


def test_plan_uses_injected_judge_in_mode_b(_clean):
    _write_config(_clean, mode="b", per_turn=2, cooldown_s=0)
    long_text = "管道检修口的栅格被踹松了，你侧身挤进去。" * 5
    rows = raid.plan([{"at": 0, "text": long_text}],
                     conf={"mode": "b", "per_turn": 2, "cooldown_s": 0},
                     judge=lambda text, ctx=None: cues.Cue("模型", 0.6, 1500))
    assert rows[0]["cues"] and rows[0]["cues"][0]["duration_ms"] == 1500


def test_plan_skips_status_lines_in_mode_b(_clean):
    conf = {"mode": "b", "per_turn": 2, "cooldown_s": 0}
    rows = raid.plan([{"at": 0, "text": "【进度】密码 ①— ②— ③—｜钥匙 有/无"}],
                     conf=conf, judge=lambda text, ctx=None: cues.Cue("模型", 0.9, 3000))
    assert rows[0]["cues"] == []

