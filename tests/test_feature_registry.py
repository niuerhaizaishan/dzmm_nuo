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

SCAN_FILES = [ROOT / "bot.py", ROOT / "heartbeat.py", ROOT / "check_bot_health.py"] \
    + sorted((ROOT / "runtime").glob("*.py"))

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

def test_registry_has_69_features():
    """65 → 64：`rewrite`（改写档案）经核实**没有真实触发**（`is_rewrite_cmd`
    只服务场景重写，已被 `scene_rewrite` 覆盖），按注册表自己的规矩删掉了。

    64 → 65（P12-AC，2026-09-27）：新增 `chat`（`@` 时的问答回复）——
    它原先是一条**没有开关的隐式行为**，上下文/长度也都写死；现在有闸门、
    长度与上下文条数都按群可调（`reply.max_tokens` / `reply.context_msgs`）。

    65 → 66（P12-AE，2026-09-27）：新增 `quote_reply`（别人右键回复我也接话）——
    与 `chat` 不同，它是**新增的触入口**，加进来就会改变今天的行为，
    所以 default=False（见 `test_only_the_deliberate_exception_may_default_off`）。

    66 → 67（P12-AK，2026-09-29）：新增 `slash_cmds`（接群内斜杠指令）——
    有些群（如顶层茶水间）里跑着**别家 bot 的游戏**，群友的 `/开始摸鱼躲藏`
    会被我们当成指令、走到兜底回一句"这个我不会"。关掉它＝这个群不接 `/` 指令。
    默认开＝今天的行为，不违反下面那条"默认必须等于今天"。

    67 → 68（P12-AL，2026-09-29）：新增 `at_only`（只 @ 才回复）——
    屋主要"只有 @ 小小糯 的时候才触发回复"，是**新增的触发约束**（关掉一切非 @ 触发），
    所以默认关（见 `_DELIBERATE_OFF`）。

    73 → 74（P12-CB，2026-10-13）：新增 `absent`（缺席名册 /寻人）——
    屋主要"查询超过半个月没发言的群友 + 她们的故事和最后一句话"。
    数据面只在认证群聊天归档上跑，是**新增的触发入口**，默认关（见 `_DELIBERATE_OFF`）。
    """
    assert len(F.FEATURES) == 74
    assert len(set(F.keys())) == 74, "key 必须唯一"


def test_every_feature_is_well_formed():
    cats = {c for c, _ in F.CATEGORIES}
    for f in F.FEATURES:
        assert f.category in cats, "{} 的分类 {} 不在 CATEGORIES 里".format(f.key, f.category)
        assert f.stage in F.WIRE_STAGES, "{} 的阶段 {} 非法".format(f.key, f.stage)
        assert f.label and isinstance(f.label, str)
        for skey in f.resets:
            assert isinstance(skey, str) and skey, "{} 的 resets 必须是状态键名".format(f.key)


def test_category_counts_match_the_plan():
    """与 docs/功能与人设配置方案.md §9.1 的分组数量对齐。

    people 少 1 = 删掉 `rewrite`；people 多 1 = P12-AC 新增 `chat`（@ 问答回复）；
    people 再多 1 = P12-AE 新增 `quote_reply`（引用回复也接话）；
    people 再多 1 = P12-AK 新增 `slash_cmds`（接群内斜杠指令的闸门）；
    people 再多 1 = P12-AL 新增 `at_only`（只 @ 才回复）。
    """
    got = {c: len(v) for c, v in F.by_category().items()}
    assert got == {"game": 15, "content": 17, "people": 14, "screen": 7,
                   "schedule": 9, "room": 8, "alert": 4}


# 唯一允许 default=False 的功能，且必须写明理由（别让它悄悄变长）。
_DELIBERATE_OFF = {
    # P12-AE：这是一条**新增的触入口**（别人右键回复我也接话）。今天的行为是
    # "只有 @ 才答"，所以它默认必须是关的 —— 默认打开等于在所有群悄悄改了行为，
    # 玫瑰房那套"谁能触发管家"的房间策略也会被绕过。要用就在对应群按群打开。
    "quote_reply",
    # P12-AL：同样是**新增的触发约束**（只 @ 才回）。默认打开会把所有群的非 @ 触发
    # 全掐掉（认证群的骰子/发骚/排行/问候都靠非 @ 消息），那是灾难。
    "at_only",
    "dream",
    "personal_story",
    "personal_portrait",
    # 2026-10-07：同样是一条**新增的触发入口**（`/致 某某`、`@小小糯 给某某写封信`）。
    # 默认打开等于在所有群多出一整类回答时机，而且它是**代发起者写给别人的公开信**，
    # 更该按群显式打开。
    "personal_letter",
    "rose_moderation",
    # P12-CB（2026-10-13）：缺席名册 /寻人。数据面是认证群聊天归档（只有妹子群在跑），
    # 也是**新增的触发入口**；默认打开等于在所有群多一类指令，其中多数群连归档都没有。
    "absent",
}


def test_only_the_deliberate_exception_may_default_off():
    """默认值必须等于"今天的行为"。

    ⚠️ 这里的"默认"是**允不允许这个能力**，不是"今天它是不是开着"。
    一开始我把恶魔吐槽/冷场吐槽写成 default=False（因为它们今天确实是关的），
    结果 `/吐槽开` 这条**群内控制命令**被自己功能关掉而静默 —— 直接改掉了
    今天的行为。正确的模型是：配置层默认"允许"，实际是否吐槽由群状态
    `devil_enabled`（`/吐槽开|关`）决定，两层各管各的。
    所以“默认关”只在一种情况下是对的：**这个功能本身是一条新增的触发入口**
    （不做就等于今天），而不是给“今天已经在跑的行为”加闸门。
    """
    off = sorted(f.key for f in F.FEATURES if not f.default)
    assert off == sorted(_DELIBERATE_OFF), \
        "默认关闭的功能只能是写明了理由的那几个；要加新的先来说清为什么"
    for k in _DELIBERATE_OFF:      # 关/开分别是什么行为，必须写进 note
        assert "关" in F.get(k).note and "开" in F.get(k).note, k
    # 那两层门的关系要写在注册表里，别只留在脑子里
    assert "devil_enabled" in F.get("devil_roast").note
    assert "devil_roast" in F.get("devil_cold").note, \
        "冷场吐槽要说清它和恶魔吐槽共用同一个群内开关"


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
    """P12-A 完成条件：**64 项全部接线**（少一项就说明有功能配了不生效）。"""
    wired = sum(1 for f in F.FEATURES if f.stage == "wired")
    assert wired == len(F.FEATURES), \
        "还有 {} 项没接线：{}".format(
            len(F.FEATURES) - wired,
            [f.key for f in F.FEATURES if f.stage != "wired"])


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
    # 恶魔吐槽的"默认关"是**群状态**那一层（devil_enabled，靠 /吐槽开），
    # 不是配置这一层：配置默认是"允许"，所以 /吐槽开 这条群内控制命令仍然有效。
    assert F.on("any-cid", "devil_roast") is True
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
    P3-11 起真身在 runtime/pushes.py：`_feat_on`/`PUSH_CIDS` 经注入壳仍是
    bot 接缝；generate_greeting/datetime/_publish_memory_report 是叶子工具，
    pushes 直接 import，patch 相应改到 pushes 模块上。
    """
    import asyncio
    from datetime import datetime as _dt

    import bot
    from runtime import pushes

    seen = []
    real = bot._feat_on

    def recorder(cid, key):
        seen.append(key)
        return real(cid, key)

    monkeypatch.setattr(bot, "_feat_on", recorder)
    monkeypatch.setattr(bot, "PUSH_CIDS", ["c1"])
    monkeypatch.setattr(pushes, "generate_greeting", lambda period, today: "测试问候")
    fake = _FakeSender()

    # 08:30 → 只该问 greet_morning
    monkeypatch.setattr(pushes, "datetime",
                        _frozen_datetime(_dt(2026, 9, 26, 8, 30)))
    asyncio.run(bot._scheduled_pushes(fake, False))
    assert "greet_morning" in seen, seen
    assert "greet_noon" not in seen and "greet_evening" not in seen
    assert fake.sent, "问候应当发出去了"

    # 12:30 → 午安 + 记忆汇报
    seen.clear()
    monkeypatch.setattr(pushes, "datetime",
                        _frozen_datetime(_dt(2026, 9, 26, 12, 30)))
    monkeypatch.setattr(pushes, "_publish_memory_report",
                        _async_noop)
    asyncio.run(bot._scheduled_pushes(fake, False))
    assert "greet_noon" in seen and "memory_report" in seen, seen


async def _async_noop(*a, **kw):
    return None


def test_rank_push_gate_is_wired_in_source():
    """排行推送的闸门在长条件里，静态确认它挂在正确的位置（与 last_daily_rank_date 同处）。"""
    # P3-19 起主循环体在 runtime/mainloop.py（bot 桥 B 前缀，子串兼容）
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    assert '_feat_on(cid, "rank_push")' in src
    idx = src.index('_feat_on(cid, "rank_push")')
    window = src[idx - 400:idx + 100]
    assert "last_daily_rank_date" in window, "闸门必须与「今天发过没」的判定在一起"
    assert "PUSH_CIDS" in window, "定时推送仍然只发生在 PUSH_CIDS 里的群"


# ------------------------------------- 6. 命令分派链：归属表与兜底静默

def test_cmd_attribution_table_covers_every_cmd_gate():
    """分派链里每个**决定分支走不走**的闸门都要在归属表里有一份。

    ⚠️ 例外：**分支体内**的闸门（例如 `/查` 里"对方没注册时用什么话术"的
    `profile_card`）不决定命令归谁，只是同一命令的两种表现 —— 它们登记在下面
    这份白名单里，必须写清理由；新增这样的闸门时也要一并登记。
    """
    import bot
    inner_ok = {
        # `/查 X` 归 query 管；profile_card 只决定"没注册"那一路是 AI 介绍还是
        # 一句"没找到"。关掉它 `/查` 照样能用，所以它不参与命令归属。
        "profile_card",
    }
    table_keys = {k for entry in bot._CMD_FEATURE_ORDER for k in entry[0]}
    # P3-3-2 起分派链在 runtime/dispatch.py（handle_slash 到文件尾）
    src_d = (ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")
    chain = src_d[src_d.index("# 吹牛骰子命令（优先）"):]
    gate_keys = set(re.findall(r'_feat_on\(cid, "([a-z_]+)"\)', chain))
    missing = gate_keys - table_keys - inner_ok
    assert not missing, "分派链里有闸门但归属表没登记：{}".format(sorted(missing))


def test_cmd_attribution_order_follows_the_chain():
    """归属表的顺序必须与代码里闸门出现的顺序一致（"顺序即行为"）。

    顺序错了会出现：`/发骚结束` 这类"落空转发"的命令被算到错误的功能上，
    于是关掉 A 功能却把 B 功能的命令静默掉了。

    ⚠️ 只比"谓词分支"上的闸门：**互斥分支**（`perf_active and _feat_on(...)`、
    `_get_dj_state(cid).get("active") and ...`）不是功能归属，它们带的是状态判断，
    所以在归属表里没有对应项，也不该参与顺序比较。
    """
    import bot
    # P3-3-2 起分派链在 runtime/dispatch.py（handle_slash 到文件尾）
    src_d = (ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")
    chain = src_d[src_d.index("# 吹牛骰子命令（优先）"):]
    src_order = []
    for line in chain.splitlines():
        m = re.search(r'_feat_on\(cid, "([a-z_]+)"\)', line)
        if not m:
            continue
        if not ("is_" in line or "cmd ==" in line or "cmd.startswith" in line):
            continue                       # 互斥分支，跳过
        if m.group(1) not in src_order:
            src_order.append(m.group(1))
    table_order = []
    for keys, _, _gate in bot._CMD_FEATURE_ORDER:
        # 只比**主键**（元组第一个）：共享/落空转发的次要键（如 `/选` 的 story、
        # `/发骚结束` 的 scene）本来就要跟主键排在一起，不能参与顺序比较。
        if keys[0] not in table_order:
            table_order.append(keys[0])
    common = [k for k in src_order if k in table_order]
    assert common == [k for k in table_order if k in src_order], \
        "归属表顺序与分派链不一致：代码 {} vs 表 {}".format(common, table_order)


def test_generic_stop_word_gets_a_hint_instead_of_silence():
    """没进行中的玩法时，`/结束` 要回一句提示，不能静默（屋主 2026-09-27 定的口径）。

    实测"/结束 没反应"有一半是"本来就没有在进行的玩法"：命令落空 → 静默 →
    用户以为机器人坏了。所以分派链里加了一条**兜底提示**分支（在真正的收尾分支之后）。

    ⚠️ 只认**通用**停止词：`/剧情结束`、`/调教结束` 这类点名的归各自功能管 ——
    功能关掉时该静默还静默（"这个群没开这个玩法"不能被当成闲聊或提示答）。
    """
    import bot
    # P3-3-2 起分派链在 runtime/dispatch.py（handle_slash 到文件尾；链体缩进 24→4）
    src_d = (ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")
    chain = src_d[src_d.index("# 吹牛骰子命令（优先）"):]
    i_hint = chain.index("elif _is_stop_word(cmd):")
    i_else = chain.index("\n    else:\n")
    assert i_hint < i_else, "兜底提示必须在真正的收尾分支之后、兜底聊天之前"
    assert 'p("no_play", cid)' in chain[i_hint:i_else], "提示文案走话术 no_play"

    assert bot._is_stop_word("/结束") and bot._is_stop_word("/停止")
    assert not bot._is_stop_word("/剧情结束"), "点名的结束词归剧情管"
    assert not bot._is_stop_word("/调教结束"), "点名的结束词归调教管"


def test_disabled_feature_makes_its_command_silent(monkeypatch):
    """关掉一个功能后，它的命令必须**静默**（不能被兜底聊天接走）。"""
    import bot
    F.set_overrides({})                       # 默认全开
    for cmd in ("/吹牛骰子", "/投票 主题|A|B", "/剧情开始", "/发骚", "/写诗",
                "/算命", "/帮助", "/注册", "/查 某人", "/回忆", "/羞辱 某人"):
        assert bot._cmd_owned_by_disabled("c1", cmd) is False, cmd

    F.set_overrides({"c1|vote": False})
    assert bot._cmd_owned_by_disabled("c1", "/投票 主题|A|B") is True
    # `/选 N` 是共享分支：投票关了但剧情还开着 ⇒ **不能静默**（剧情要用它）
    assert bot._cmd_owned_by_disabled("c1", "/选 1") is False
    assert bot._cmd_owned_by_disabled("c1", "/算命") is False, "别的功能不受影响"
    # 两个都关 ⇒ 才静默
    F.set_overrides({"c1|vote": False, "c1|story": False})
    assert bot._cmd_owned_by_disabled("c1", "/选 1") is True


def test_shared_stop_word_survives_a_disabled_feature(monkeypatch):
    """`/结束` 是骰子/发骚/调教**共用**的收尾词：关掉骰子不能把发骚的收尾一起吞掉。

    2026-09-27 实测事故：若瑾那个群只开 `scene`+`insult` → 用户发 `/结束` 想收尾发骚，
    却因为"第一个匹配是骰子"且骰子被关掉 → 静默。
    """
    import bot
    bot._set_fasao_state("c1", {})                # 没有进行中的发骚
    F.set_overrides({"c1|dice": False})           # 只关骰子
    assert bot._cmd_owned_by_disabled("c1", "/结束") is False, \
        "调教还开着（它会接住这个停止词）⇒ 不能静默"

    # 真的有一场发骚在进行 ⇒ 即使骰子关着，也必须放行（这就是那起事故）
    bot._set_fasao_state("c1", {"active": True})
    assert bot._cmd_owned_by_disabled("c1", "/结束") is False, \
        "发骚进行中、scene_end 开着 ⇒ 必须放行给 end_fasao"

    # 发骚关了（其余收尾也没开）⇒ 才静默
    F.set_overrides({"c1|dice": False, "c1|scene": False, "c1|scene_end": False,
                     "c1|diaojiao": False})
    assert bot._cmd_owned_by_disabled("c1", "/结束") is True, \
        "所有认它的功能都关了 ⇒ 才静默"
    bot._set_fasao_state("c1", {})
    F.set_overrides({})
    assert bot._cmd_owned_by_disabled("c1", "/结束") is False


def test_fallthrough_commands_are_attributed_correctly(monkeypatch):
    """**落空转发**的命令：归属挂在多个功能上，任何一个开着就不静默。

    `/发骚结束` 在没有进行中的场景时，实际会被"发起场景"那个分支处理
    （代码注释里登记过这个行为 —— 归属表里它是**独立的一项**，不是靠元组第二个键兜的）：

      - 没有进行中的发骚 ⇒ `scene_end` 那一支压根不会接（闸门关），
        所以此时"scene_end 关着"**不是**它没人接的原因；接下来看 `scene` 那一支：
          - `scene` 开 ⇒ 不静默（场景会把它变成一次发起）；
          - `scene` 关 ⇒ 静默。
      - 有进行中的发骚 ⇒ `scene_end` 那一支会接，关着就静默。
    """
    import bot
    bot._set_fasao_state("c1", {})                # 没有进行中的发骚
    F.set_overrides({"c1|scene_end": False})
    assert bot._cmd_owned_by_disabled("c1", "/发骚结束") is False, \
        "发起场景开着 ⇒ 它会接住这条命令"
    F.set_overrides({"c1|scene": False})
    assert bot._cmd_owned_by_disabled("c1", "/发骚结束") is True, \
        "两支都关着且没有进行中的场景 ⇒ 静默"
    F.set_overrides({"c1|scene": False, "c1|scene_end": False})
    assert bot._cmd_owned_by_disabled("c1", "/发骚结束") is True

    # 发骚进行中：收尾那一支真的会接 ⇒ 关掉它就静默
    bot._set_fasao_state("c1", {"active": True})
    F.set_overrides({"c1|scene_end": False})
    assert bot._cmd_owned_by_disabled("c1", "/发骚结束") is True, \
        "发骚进行中，收尾被关 ⇒ 静默（别再把它变成一次发起）"
    F.set_overrides({})
    assert bot._cmd_owned_by_disabled("c1", "/发骚结束") is False
    bot._set_fasao_state("c1", {})


def test_continue_is_attributed_by_last_action(state):
    """共享分支：`/继续` 归谁管取决于上一步是羞辱还是场景。"""
    import bot
    import state_store
    state_store.group_set("c1", "last_action", {"type": "insult", "output": "x"})
    monkeypatch_ok = bot._continue_allowed("c1")
    assert monkeypatch_ok is True
    F.set_overrides({"c1|insult": False})
    assert bot._continue_allowed("c1") is False, "上一步是羞辱 ⇒ 归羞辱管"
    F.set_overrides({"c1|insult": False, "c1|scene_continue": True})
    state_store.group_set("c1", "last_action", {"type": "scene", "output": "y"})
    assert bot._continue_allowed("c1") is True, "上一步是场景 ⇒ 归场景续写管"


def test_devil_switch_cannot_be_turned_on_when_feature_is_off(monkeypatch):
    """配置里关了主动吐槽，群内 `/吐槽开` 就不该还能打开它（否则页面与群里自相矛盾）。"""
    import bot
    F.set_overrides({"c1|devil_roast": False})
    assert bot._cmd_owned_by_disabled("c1", "/吐槽开") is True
    F.set_overrides({})
    assert bot._cmd_owned_by_disabled("c1", "/吐槽开") is False


# ------------------------------------- 7. 第三批：房间 / 发情值 / 入群筛查

def test_room_order_gate_decides_whether_butler_answers(state):
    """客人点单要不要管家回应，由 `room_order` 决定（闸门在 `_is_room_trigger` 里）。

    ⚠️ P12-R（2026-09-27 屋主确认"别误触发"）改了口径：**未点名的关键词点单不再触发**
    （"想喝一杯酒"这种闲聊里随时会出现），改成"点名的点单才算"。本用例因此跟着改成
    "点名的点单"来验证同一个开关 —— 它守的是"开关管用"，不是"关键词算不算"。
    """
    from personas import ROSE_CID
    from runtime import sign
    F.set_overrides({})
    assert sign._is_room_trigger(ROSE_CID, "guest-uid-1", "薇丝 想喝一杯酒") is True
    assert sign._is_room_trigger(ROSE_CID, "guest-uid-1", "想喝一杯酒") is False, \
        "P12-R：没点名就不接（关键词自动触发是误触发大户）"
    F.set_overrides({"{}|room_order".format(ROSE_CID): False})
    assert sign._is_room_trigger(ROSE_CID, "guest-uid-1", "薇丝 想喝一杯酒") is False, \
        "关掉点单后，客人点单不再触发管家"


def test_heat_gate_blocks_further_scoring(state):
    """关掉 `heat` 就不再加分，但**已有的分数不删**。"""
    import state_store
    from runtime.heat import _add_heat
    _add_heat("c1", "u1", 10)
    assert (state_store.group_get("c1", "heat_value", {}) or {}).get("u1") == 10
    F.set_overrides({"c1|heat": False})
    _add_heat("c1", "u1", 10)
    assert (state_store.group_get("c1", "heat_value", {}) or {}).get("u1") == 10, \
        "关了发情值就不该再加分（已有分数保留）"


def test_screen_off_still_welcomes_in_source():
    """`screen` 只该包住"筛查踢人"那一支，不能包住整个入群块 ——
    否则关掉筛查会把欢迎语、房间迎客一起关掉（那两件事另有开关）。

    P3-3-1 起分派逻辑在 runtime/joins.py（`handle_join_event`）；
    bot.py 只留"剧情进行中不迎新"的故事态闸门。
    """
    src_b = (ROOT / "bot.py").read_text(encoding="utf-8")
    src_j = (ROOT / "runtime" / "joins.py").read_text(encoding="utf-8")
    region = src_j[src_j.index("async def handle_join_event"):src_j.index("for jid in passed")]
    assert 'cid in screen_cids and _feat_on(cid, "screen")' in region
    assert "elif cid in screen_cids:" in region, "关掉筛查要退化成「只欢迎不踢人」"
    # P12-BD：那份中性欢迎语现在先问话术表（注入的 `welcome_of`），没写 key 的群仍旧
    # 用常量兜底 —— 语义没变，只是取法可覆盖。
    # P3-20：常量真源与兜底表达式搬 runtime/joins.py（bot 壳传值注入）。
    src_j = (ROOT / "runtime" / "joins.py").read_text(encoding="utf-8")
    assert "welcome_of(cid, plain=True)" in region
    assert "welcome_text_plain if plain else welcome_text" in src_j, "常量还是兜底"
    # 故事态闸门留在调用点（P3-19 起随主循环体在 runtime/mainloop.py，B 前缀）
    src_ml = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    assert 'if not B._get_story_state(cid).get("active"):' in src_ml


def test_defaults_are_logically_equivalent_to_before(state):
    """零行为变化的**逻辑保证**：没有覆盖时每个闸门都返回 True。

    于是分派链里：
      `elif <原条件> and _feat_on(...)`  ≡  `elif <原条件>`
      `elif ... and (_feat_on(a) or _feat_on(b))`  ≡  `elif ...`
      兜底那条 `elif _cmd_owned_by_disabled(...)` ≡ 永不成立（直接落到 generate_reply）
    这不是"跑几个用例看着一样"，而是条件化简 —— 也是这一期敢上线的底气。
    """
    import bot
    # 唯一例外见 _DELIBERATE_OFF：它默认关 = 今天的行为（只有 @ 才答）
    assert all(F.on("any-cid", k) is True for k in F.keys()
               if k not in _DELIBERATE_OFF), \
        "默认必须有闸门全开（否则等于改了今天的行为）"
    assert F.on("any-cid", "quote_reply") is False, \
        "引用触发默认关，否则所有群都会多出一种回答时机"
    for cmd in ("/吹牛骰子", "/投票 x|a|b", "/选 1", "/发骚结束", "/吐槽开", "/结束",
                "/剧情开始", "/公演开始", "/同意", "/命令 过来", "/调教 x",
                "/写诗", "/算命", "/周运", "/我不在", "/总结", "/帮助", "/介绍",
                "/同步", "/注册", "/查 x", "/羞辱 x", "/继续", "/重写"):
        assert bot._cmd_owned_by_disabled("any-cid", cmd) is False, cmd
    # 互斥分支同理：`X and _feat_on(...)` ≡ `X`
    assert bot._continue_allowed("any-cid") is True
    import state_store
    state_store.group_set("any-cid", "last_action", {"type": "insult"})
    assert bot._continue_allowed("any-cid") is True
