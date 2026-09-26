"""功能注册表（P12-A）：注册表自身的规矩 + **接线进度不许漂**。

这一期是"零行为变化"的接线期，所以测试要钉住三件事：

1. **注册表本身自洽**：key 唯一、分类/阶段合法、参数边界合理、
   默认值与今天的行为一致（只有恶魔吐槽/冷场吐槽默认关）。
2. **接线进度不许悄悄漂**：`stage="wired"` 的条目必须在代码里真有
   `_feat_on(cid, "key")` 闸门；反过来，代码里出现的每个 key 都必须在注册表里
   （拼错 key → 这里红，而不是线上静默放行）。这条是整期最重要的一条：
   "以为配了、其实没接线"正是 P12-0 刚修过的那类 bug。
3. **零行为变化**：没有任何覆盖时 `on()` 返回默认值；未知 key 必须抛。
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import features as F  # noqa: E402

SCAN_FILES = [ROOT / "bot.py"] + sorted((ROOT / "runtime").glob("*.py"))

# 扫描 `_feat_on(<任意表达式>, "key")`；允许中间有空格/换行
_FEAT_ON_RE = re.compile(r"_feat_on\(\s*[^,()]+,\s*\"([a-z_]+)\"\s*\)")


def _scanned_keys():
    """代码里"被闸门引用到的"功能 key。

    除了直接写字面量的调用点，还包括 `bot._GREET_FEATURES` 这种**显式登记表**——
    因为 `period` 是拼出来的（`"greet_" + period`），静态扫描看不见。
    这种"拼出来的 key"必须集中登记，否则 `_feat_on` 的"未知 key 只记日志+放行"
    会让它静默失效（第一次跑这条测试时就是这样：注册表写 wired、扫描找不到）。
    """
    found = {}
    for p in SCAN_FILES:
        if p.name == "features.py":       # 注册表自己不算"接线"
            continue
        txt = p.read_text(encoding="utf-8")
        for m in _FEAT_ON_RE.finditer(txt):
            line = txt[:m.start()].count("\n") + 1
            found.setdefault(m.group(1), []).append("{}:{}".format(p.name, line))
    # 显式登记表里的 key 也算"接线"（值必须是字面量，才能被看见）
    for key, src in _declared_tables():
        found.setdefault(key, []).append(src)
    return found


def _declared_tables():
    """`_XXX_FEATURES = {"...": "feature_key"}` 形式的显式登记表。"""
    out = []
    for p in SCAN_FILES:
        if p.name == "features.py":
            continue
        txt = p.read_text(encoding="utf-8")
        for m in re.finditer(r"_FEATURES\s*=\s*\{(.*?)\}", txt, re.S):
            for km in re.finditer(r":\s*\"([a-z_]+)\"", m.group(1)):
                out.append((km.group(1), "{}:table".format(p.name)))
    return out


# ---------------------------------------------------------------- 1. 注册表自洽

def test_registry_has_65_features():
    assert len(F.FEATURES) == 65
    assert len(set(F.keys())) == 65, "key 必须唯一"


def test_every_feature_is_well_formed():
    cats = {c for c, _ in F.CATEGORIES}
    for f in F.FEATURES:
        assert f.category in cats, "{} 的分类 {} 不在 CATEGORIES 里".format(f.key, f.category)
        assert f.stage in F.WIRE_STAGES, "{} 的阶段 {} 非法".format(f.key, f.stage)
        assert f.label and isinstance(f.label, str)
        for skey in f.resets:
            assert isinstance(skey, str) and skey, "{} 的 resets 必须是状态键名".format(f.key)


def test_category_counts_match_the_plan():
    """与 docs/功能与人设配置方案.md §9.1 的分组数量对齐。"""
    got = {c: len(v) for c, v in F.by_category().items()}
    assert got == {"game": 15, "content": 12, "people": 11, "screen": 7,
                   "schedule": 9, "room": 7, "alert": 4}


def test_only_devil_features_default_off():
    """默认值必须等于"今天的行为"：只有恶魔吐槽与冷场吐槽今天是关的。"""
    off = sorted(f.key for f in F.FEATURES if not f.default)
    assert off == ["devil_cold", "devil_roast"]


def test_params_have_sane_bounds():
    for f in F.FEATURES:
        for p in f.params:
            assert p.tier in ("必配", "可配", "内部"), p.key
            if isinstance(p.default, (int, float)) and not isinstance(p.default, bool):
                if p.lo is not None:
                    assert p.lo <= p.default, "{} 的默认值小于下界".format(p.key)
                if p.hi is not None:
                    assert p.default <= p.hi, "{} 的默认值大于上界".format(p.key)
                if p.lo is not None and p.hi is not None:
                    assert p.lo < p.hi, p.key


def test_reset_keys_are_known_state_keys():
    """`resets` 里写的必须是真实存在的群状态键（防拼错）。"""
    known = {"fasao", "diaojiao", "perf", "vote", "story", "memory", "last_action",
             "rose_ghost", "rose_strikes", "pending_screen"}
    for f in F.FEATURES:
        for skey in f.resets:
            assert skey in known or skey in F.TOP_LEVEL_KEYS, \
                "{} 的 resets 里有未知状态键 {}".format(f.key, skey)


# ---------------------------------------------------------------- 2. 接线进度

def test_wired_stage_matches_actual_gates():
    """**最重要的一条**：`stage="wired"` ⟺ 代码里真有闸门。两边都不许单方面变。"""
    scanned = set(_scanned_keys())
    declared = {f.key for f in F.FEATURES if f.stage == "wired"}
    assert scanned == declared, (
        "接线进度漂了：\n"
        "  代码里有闸门但注册表还写 planned: {}\n"
        "  注册表写 wired 但代码里找不到闸门: {}".format(
            sorted(scanned - declared), sorted(declared - scanned)))


def test_every_gate_uses_a_registered_key():
    """拼错 key 会让开关静默失效（`_feat_on` 只能记日志 + 放行）——这里拦住。"""
    bad = {k: v for k, v in _scanned_keys().items() if k not in set(F.keys())}
    assert not bad, "代码里出现了未注册的功能 key：{}".format(bad)


def test_wiring_progress_does_not_regress():
    """已接线的数量只许多不许少（防止后续改动把闸门删掉）。"""
    wired = sum(1 for f in F.FEATURES if f.stage == "wired")
    assert wired >= 9, "P12-A 第一批至少 9 项已接线，现在是 {}".format(wired)


# ---------------------------------------------------------------- 3. 判定与复位

@pytest.fixture(autouse=True)
def _clean_features(monkeypatch):
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    F.set_overrides({})
    yield
    F.set_overrides({})


def test_default_behaviour_is_todays(monkeypatch):
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    assert F.on("any-cid", "dice") is True
    assert F.on("any-cid", "perf") is True
    assert F.on("any-cid", "devil_roast") is False, "今天默认关的必须还是关"
    assert F.why_off("any-cid", "dice") == "default"


def test_unknown_key_raises():
    with pytest.raises(F.FeatureError):
        F.on("cid", "not_a_feature")
    with pytest.raises(F.FeatureError):
        F.get("nope")


def test_overrides_are_per_group_and_global():
    F.set_overrides({"cidA|dice": False, "*|insult": False})
    assert F.on("cidA", "dice") is False
    assert F.on("cidB", "dice") is True, "本群覆盖不该影响别的群"
    assert F.on("cidA", "insult") is False and F.on("cidB", "insult") is False
    assert F.why_off("cidA", "dice") == "config(本群)"
    assert F.why_off("cidB", "insult") == "config(全局)"


def test_env_kill_switch_forces_everything_on(monkeypatch):
    """`DZMM_FEATURES=off` = 忽略所有开关（全开）：页面配错了能秒退。"""
    F.set_overrides({"cidA|dice": False, "*|devil_roast": False})
    assert F.on("cidA", "dice") is False
    monkeypatch.setenv("DZMM_FEATURES", "off")
    assert F.on("cidA", "dice") is True
    assert F.on("cidA", "devil_roast") is True
    assert F.why_off("cidA", "dice").startswith("env")


def test_reset_feature_clears_inflight_state(state):
    import state_store
    state_store.group_set("c1", "fasao", {"active": True, "scene": "x"})
    assert F.active_states("c1"), "应当能看出这个群有进行中的状态"
    done = F.reset_feature("c1", "scene")
    assert done and any("fasao" in d for d in done)
    assert not (state_store.group_get("c1", "fasao", {}) or {}).get("active")
    assert F.active_states("c1") == []


def test_reset_feature_does_not_touch_data(state):
    """复位只清"进行中"，**不删数据**（档案/记忆/发情值一律不碰）。"""
    import state_store
    state_store.group_set("c1", "memory", [{"text": "记得的事"}])
    state_store.group_set("c1", "heat_value", {"u1": 42})
    F.reset_all("c1")
    assert state_store.group_get("c1", "memory", [])
    assert state_store.group_get("c1", "heat_value", {}) == {"u1": 42}


def test_reset_dice_clears_top_level_key(state):
    """骰子局是**顶层键**：关掉它必须能清，否则所有群都开不了新局。"""
    import state_store
    state_store.top_set("dice_game", {"active": True, "cid": "c1"})
    done = F.reset_feature("c1", "dice")
    assert done and not (state_store.top_get("dice_game") or {})


def test_features_without_state_need_no_reset():
    assert F.reset_feature("c1", "help") == []


def test_summary_is_json_serialisable():
    import json
    json.dumps(F.summary(), ensure_ascii=False)
    json.dumps(F.registry(), ensure_ascii=False)


# ---------------------------------------------------------------- 4. 公演超时

def test_perf_timeout_unlocks_group(state):
    """公演原先**没有超时**：人散了会让该群永久哑掉。现在必须能自动解锁。"""
    import time
    import state_store
    from runtime import perf
    state_store.group_set("c1", "perf", {
        "active": True, "start_id": "s1", "last_interact_ts": time.time() - 10,
        "review_status": "none"})
    assert perf._check_perf_timeout("c1") == "", "还没到点，不该解锁"
    assert (state_store.group_get("c1", "perf", {}) or {}).get("active") is True
    perf._check_perf_timeout("c1", timeout_sec=1)
    after = state_store.group_get("c1", "perf", {}) or {}
    assert after.get("active") is False, "超时后必须解锁"
    assert "timeout_ts" in after


def test_perf_timeout_keeps_pending_review(state):
    """解锁**不能丢掉待审的小说**（`review_status` 要保留）。"""
    import time
    import state_store
    from runtime import perf
    state_store.group_set("c1", "perf", {
        "active": True, "last_interact_ts": time.time() - 99999,
        "review_status": "pending"})
    perf._check_perf_timeout("c1", timeout_sec=1)
    assert (state_store.group_get("c1", "perf", {}) or {}).get("review_status") == "pending"


def test_perf_timeout_noop_when_inactive(state):
    import state_store
    from runtime import perf
    assert perf._check_perf_timeout("c1", timeout_sec=1) == ""
    assert (state_store.group_get("c1", "perf", {}) or {}) == {}


# ------------------------------------------- 5. 行为验证：闸门真的被咨询到了

def _frozen_datetime(fixed):
    """把 `bot.datetime.now()` 冻在某个时刻（其余用法保持可用）。"""
    from datetime import datetime as _dt

    class Frozen(_dt):
        @classmethod
        def now(cls, tz=None):
            return fixed
    return Frozen


class _FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, text, cid, allow_fallback=True):
        self.sent.append((cid, text))
        return True


def test_greeting_gate_is_actually_consulted(monkeypatch, state):
    """真跑一遍 `_scheduled_pushes`，看它到底咨询了哪个功能 key。

    为什么不能只靠静态扫描：问候的 key 是 `_GREET_FEATURES[period]` 查出来的
    （`period` 在循环里拼），正则扫不到字符串字面量。第一次写这条测试时，
    注册表写着 wired、静态扫描却找不到 —— 说明"看起来接了"和"真接了"是两回事。
    所以这里驱动真代码，记录 `_feat_on` 被问了哪些 key。
    """
    import asyncio
    from datetime import datetime as _dt

    import bot

    seen = []
    real = bot._feat_on

    def recorder(cid, key):
        seen.append(key)
        return real(cid, key)

    monkeypatch.setattr(bot, "_feat_on", recorder)
    monkeypatch.setattr(bot, "PUSH_CIDS", ["c1"])
    monkeypatch.setattr(bot, "generate_greeting", lambda period, today: "测试问候")
    fake = _FakeSender()

    # 08:30 → 只该问 greet_morning
    monkeypatch.setattr(bot, "datetime",
                        _frozen_datetime(_dt(2026, 9, 26, 8, 30)))
    asyncio.run(bot._scheduled_pushes(fake, False))
    assert "greet_morning" in seen, seen
    assert "greet_noon" not in seen and "greet_evening" not in seen
    assert fake.sent, "问候应当发出去了"

    # 12:30 → 午安 + 记忆汇报
    seen.clear()
    monkeypatch.setattr(bot, "datetime",
                        _frozen_datetime(_dt(2026, 9, 26, 12, 30)))
    monkeypatch.setattr(bot, "_publish_memory_report",
                        _async_noop)
    asyncio.run(bot._scheduled_pushes(fake, False))
    assert "greet_noon" in seen and "memory_report" in seen, seen


async def _async_noop(*a, **kw):
    return None


def test_rank_push_gate_is_wired_in_source():
    """排行推送的闸门在长条件里，静态确认它挂在正确的位置（与 last_daily_rank_date 同处）。"""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert '_feat_on(cid, "rank_push")' in src
    idx = src.index('_feat_on(cid, "rank_push")')
    window = src[idx - 400:idx + 100]
    assert "last_daily_rank_date" in window, "闸门必须与「今天发过没」的判定在一起"
    assert "PUSH_CIDS" in window, "定时推送仍然只发生在 PUSH_CIDS 里的群"
