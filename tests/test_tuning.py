"""可调参数（P12-B）：默认值必须**逐个等于**它替换掉的那个常量。

这一期是"参数外置"，验收标准与 P12-A 一样是**零行为变化**。
最有力的证明不是"跑几个用例看着一样"，而是：

1. **默认值 == 原常量**（`test_defaults_match_the_code_constants`）：
   48 个参数逐个对照 `bot.DEVIL_*`、`runtime.heat.HEAT_*`、
   `runtime.vote.VOTE_DEFAULT_SECONDS` 等**原来真正生效的那些常量**。
   任何一个数字抄错、或将来有人只改一边，这条就红。
2. **读口唯一**：代码里不许再出现"直接读那个常量"的消费点
   （只允许常量定义行本身 + 文档字符串）。
3. 硬边界、按群覆盖、秒退开关、按群集合的默认值。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import brains  # noqa: E402
from runtime import tuning as T  # noqa: E402

import runtime.fasao as fasao  # noqa: E402
import runtime.heat as heat  # noqa: E402
import runtime.recap as recap  # noqa: E402
import runtime.story as story  # noqa: E402
import runtime.trend as trend  # noqa: E402
import runtime.vote as vote  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("DZMM_TUNING", raising=False)
    T.set_overrides({})
    T.set_scopes({})
    yield
    T.set_overrides({})
    T.set_scopes({})


# ------------------------------------------------- 1. 默认值 == 原常量（核心）

def test_defaults_match_the_code_constants():
    """逐个对照"原来真正生效的常量"。数字抄错、或将来只改一边，这里就红。"""
    pairs = [
        # 发情值：11 个事件分值
        ("heat.scene_pick", heat.HEAT_SCENE_PICK),
        ("heat.scene_continue_target", heat.HEAT_SCENE_CONTINUE_TARGET),
        ("heat.scene_continue_sender", heat.HEAT_SCENE_CONTINUE_SENDER),
        ("heat.insult_target", heat.HEAT_INSULT_TARGET),
        ("heat.insult_sender", heat.HEAT_INSULT_SENDER),
        ("heat.fasao_pick", heat.HEAT_FASAO_PICK),
        ("heat.diaojiao_round", heat.HEAT_DIAOJIAO_ROUND),
        ("heat.vote_starter", heat.HEAT_VOTE_STARTER),
        ("heat.vote_voter", heat.HEAT_VOTE_VOTER),
        ("heat.story_voter", heat.HEAT_STORY_VOTER),
        ("heat.story_protagonist", heat.HEAT_STORY_PROTAGONIST),
        # 超时（原先是散在各处的字面量，这里把值钉住）
        ("scene.timeout", 15 * 60),
        # 发骚正文长度上限（2026-09-27 屋主："有截断""内容太少" → 700 → 1800）
        ("scene.max_tokens", brains.SCENE_MAX_TOKENS),
        # 2026-10-07 屋主：「发骚的过程中，/继续 后输出的字数调整为现在的一半」
        # ⇒ 续写单独一条上限（1800 → 900），首段不变
        ("scene.continue_max_tokens", brains.SCENE_CONTINUE_MAX_TOKENS),
        # P12-AX：按群的正文长度话术（空 = 用提示词默认档 700-1100 字）。
        # 认证群要"一半"→ 配 350-550 字；上班群不配 =一个字不变。
        ("scene.length", ""),
        # P12-AW：茶水间的运势入口（`#运势` 开关 + 当天卡复用冷却）。
        # 默认都是"关/0" ⇒ 其他群一个字不变。
        ("fortune.hash_cmd", 0),
        ("fortune.cooldown", 0),
        ("dream.hash_cmd", 0),
        ("dream.natural", 1),
        ("dream.cooldown", 60),
        ("dream.max_chars", 230),
        ("dream.input_limit", 2000),
        ("dream.timeout", 20),
        ("imagegen.cooldown", 60),
        ("imagegen.input_limit", 2000),
        ("imagegen.timeout", 150),
        ("videogen.cooldown", 60),
        ("videogen.input_limit", 2000),
        ("videogen.timeout", 300),
        ("personal_story.cooldown", 300),
        ("personal_story.timeout", 45),
        ("personal_portrait.cooldown", 300),
        ("personal_portrait.timeout", 45),
        # 2026-10-07：/致某某（代发起者写给群友的一封信）
        # 目标级冷却默认 **0 = 关**（屋主口径「不设冷却」）；真正兜底的是全群单在途
        ("personal_letter.cooldown", 300),
        ("personal_letter.timeout", 45),
        ("personal_letter.target_cooldown", 0),
        ("diaojiao.timeout", 300),
        ("diaojiao.idle_seconds", 30),
        ("dice.timeout", bot.DICE_TIMEOUT_SEC),
        ("vote.default_seconds", vote.VOTE_DEFAULT_SECONDS),
        ("story.max_round", story.STORY_MAX_ROUND),
        ("story.vote_seconds", story.STORY_VOTE_SECONDS),
        ("perf.enrich_seconds", 600),
        ("perf.ending_wait", 20),
        ("perf.novel_chunk", 5),
        ("perf.novel_max_chunks", 12),
        # 恶魔吐槽（原 bot.py 的 DEVIL_* 常量）
        ("devil.base_prob", bot.DEVIL_BASE_PROB),
        ("devil.sensitive_prob", bot.DEVIL_SENSITIVE_PROB),
        ("devil.hot_prob", bot.DEVIL_HOT_PROB),
        ("devil.cooldown", bot.DEVIL_COOLDOWN),
        ("devil.cold_threshold", bot.DEVIL_COLD_THRESHOLD),
        ("devil.cold_prob", bot.DEVIL_COLD_PROB),
        ("devil.cold_cooldown", bot.DEVIL_COLD_COOLDOWN),
        ("devil.cold_quiet_hour", bot.DEVIL_COLD_QUIET_HOUR),
        ("devil.about_person_prob", 0.5),
        # 记忆 / 档案
        ("memory.report_items", 8),
        ("memory.inject", 10),
        ("memory.max", 100),
        ("profile.scan_max_api", bot.PROFILE_SCAN_MAX_API),
        ("profile.notice_max", bot.PROFILE_NOTICE_MAX),
        ("profile.roll_msgs", 30),
        # 发情值：阈值与周期（原先是 bot.py 里的**内联字面量**，没有常量可对照，
        # 所以这里直接把当时的值写死当锚；改代码时两边不一致就会红）
        ("heat.climax_threshold", 100),
        ("heat.climax_reset", 100),
        ("heat.judge_interval", 1800),
        ("heat.judge_trigger", 80),
        ("heat.pending_max", 10),
        # 房间对话的 max_tokens（玫瑰房人设要求"4～8 句"，线上给玫瑰房调高）
        ("room.reply_max_tokens", 400),
        # P12-AC：`@` 问答回复的长度与上下文条数
        # （原来写死 400 token / 末尾 4 条、且**不含机器人自己说过的话**）
        ("reply.max_tokens", 800),
        ("reply.context_msgs", 20),
        # P12-BD：让"@ 闲聊"更像人 —— 装饰概率与排版收口。
        # 前三项的默认值**就是今天的行为**（1.0/1.0/关/空），所以等于没上线：
        # 只有按群给上班群配了值，行为才会变（见 docs/P12-BD）。
        ("chat.decor_prob", 1.0),
        ("chat.bang_prob", 1.0),
        ("chat.humanize", 0),
        ("chat.frame_length", ""),
        # P12-BQ：@ 闲聊的两个人味闸门（默认都是"关"= 今天的行为）
        ("chat.reply_cooldown", 0),
        ("chat.solicit_guard", 0),
        # P12-BE：上下文范围（0=全群 = 今天的行为）
        ("reply.ctx_mode", 0),
        # P12-BR a：排行榜前几名（= `rank_summary(top=10)` 原来的默认）
        ("rank.top_n", 10),
        # P12-BR b：羞辱/调教长度（= 原先 brains.py 里写死的 600）
        ("insult.max_tokens", 600),
        ("diaojiao.max_tokens", 600),
        # 定时
        ("schedule.scan_hour", bot.PROFILE_SCAN_HOUR),
        ("schedule.greet_window_min", 90),
        ("schedule.rank_hours", "12,18"),
        # 回顾
        ("recap.cooldown", recap.RECAP_COOLDOWN),
        ("recap.max_msgs", recap.RECAP_MAX_MSGS),
        ("recap.max_hours", recap.RECAP_MAX_HOURS),
        # P12-AK：默认回退窗口（原来在 recap.py 里写死 6 小时）+ 要忽略的内容关键词
        ("recap.default_hours", 6),
        ("recap.ignore_kw", ""),
        # P12-CB：缺席名册 /寻人（判定口径与刷屏保护，2026-10-13 屋主定档）
        ("absent.days", 15),
        ("absent.min_msgs", 5),
        ("absent.max_show", 8),
        ("absent.max_tokens", 500),
        # P12-CF：茶水间写诗的 `#` 裸指令入口（默认关=零回归）
        ("poem.hash_cmd", 0),
    ]
    wrong = [(k, T.get(k), ref) for k, ref in pairs if T.get(k) != ref]
    assert not wrong, "参数默认值与原常量不一致：{}".format(wrong)
    # 参数表里的每一项都要在对照名单里出现过（防止"加了参数没钉默认值"）
    listed = {k for k, _ in pairs}
    assert set(T.PARAMS) == listed, \
        "没被钉住默认值的参数：{}".format(sorted(set(T.PARAMS) - listed))


def test_no_consumption_site_reads_the_old_constants():
    """读口唯一：消费点不许再直接读旧常量（定义行本身除外）。

    否则会出现"页面上改了、某个调用点还读旧值"——那正是 P12-0 修过的
    "看起来能配、实际不生效"。这里扫源码，把"读旧常量"的地方找出来。
    """
    watch = {
        # 一律正斜杠：Path 在 Windows 上也认 "/"，反斜杠在 Linux 是文件名的一部分
        "bot.py": ["DICE_TIMEOUT_SEC", "PROFILE_SCAN_HOUR", "PROFILE_SCAN_MAX_API",
                   "PROFILE_NOTICE_MAX", "DEVIL_BASE_PROB", "DEVIL_SENSITIVE_PROB",
                   "DEVIL_HOT_PROB", "DEVIL_COOLDOWN", "DEVIL_COLD_THRESHOLD",
                   "DEVIL_COLD_PROB", "DEVIL_COLD_COOLDOWN", "DEVIL_COLD_QUIET_HOUR"],
        "runtime/fasao.py": ["FASAO_TIMEOUT"],
        "runtime/vote.py": ["VOTE_DEFAULT_SECONDS"],
        "runtime/story.py": ["STORY_MAX_ROUND", "STORY_VOTE_SECONDS"],
        "runtime/recap.py": ["RECAP_MAX_MSGS", "RECAP_MAX_HOURS", "RECAP_COOLDOWN"],
        "runtime/trend.py": ["TREND_CIDS"],
        "runtime/recap.py_": ["RECAP_CIDS"],
    }
    offenders = []
    for rel, names in watch.items():
        rel = rel.rstrip("_")
        lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for name in names:
                if name not in line:
                    continue
                # 允许：定义行（`NAME = ...`）、__all__、文档注释（含反引号引用）
                is_def = stripped.startswith(name + " =") or stripped.startswith(name + "=")
                if is_def or '__all__' in line:
                    continue
                if "`" + name + "`" in line:      # 文档里提到这个名字
                    continue
                if stripped.startswith(('"', "'", "只在", "包含")):
                    continue
                offenders.append("{}:{} {}".format(rel, i, stripped[:70]))
    assert not offenders, "还有地方直接读旧常量：\n  " + "\n  ".join(offenders)


# ------------------------------------------------- 2. 三层取值

def test_default_when_no_override():
    assert T.get("devil.base_prob") == 0.03
    assert T.why("devil.base_prob") == "default"


def test_global_override_beats_default():
    T.set_overrides({"devil.base_prob": 0.2})
    assert T.get("devil.base_prob") == pytest.approx(0.2)
    assert T.get("devil.base_prob", "c1") == pytest.approx(0.2)
    assert T.why("devil.base_prob") == "config(全局)"


def test_per_group_override_beats_global():
    T.set_overrides({"devil.base_prob": 0.2, "c1|devil.base_prob": 0.5})
    assert T.get("devil.base_prob", "c1") == pytest.approx(0.5)
    assert T.get("devil.base_prob", "c2") == pytest.approx(0.2)
    assert T.why("devil.base_prob", "c1") == "config(本群)"


def test_env_kill_switch(monkeypatch):
    T.set_overrides({"devil.base_prob": 0.9})
    assert T.get("devil.base_prob") == pytest.approx(0.9)
    monkeypatch.setenv("DZMM_TUNING", "off")
    assert T.get("devil.base_prob") == pytest.approx(0.03), "秒退开关要能忽略所有覆盖"
    assert T.why("devil.base_prob").startswith("env")


# ------------------------------------------------- 3. 硬边界与错误

def test_out_of_range_is_rejected():
    with pytest.raises(T.TuningError):
        T.set_overrides({"devil.base_prob": 2.0})       # 概率上界 1.0
    with pytest.raises(T.TuningError):
        T.set_overrides({"devil.base_prob": -0.1})
    with pytest.raises(T.TuningError):
        T.set_overrides({"heat.climax_threshold": 0})   # 下界 1
    with pytest.raises(T.TuningError):
        T.set_overrides({"perf.novel_max_chunks": 99})  # 上界 24（调大堵主循环）


def test_bad_type_is_rejected():
    with pytest.raises(T.TuningError):
        T.set_overrides({"heat.climax_threshold": "abc"})


def test_unknown_key_is_rejected():
    with pytest.raises(T.TuningError):
        T.set_overrides({"no.such.param": 1})
    with pytest.raises(T.TuningError):
        T.get("no.such.param")


def test_failed_set_leaves_previous_values_intact():
    """一次失败的注入不该把已经生效的覆盖清掉（否则页面上改一半就全丢了）。"""
    T.set_overrides({"devil.base_prob": 0.2})
    with pytest.raises(T.TuningError):
        T.set_overrides({"devil.base_prob": 0.2, "heat.climax_threshold": 0})
    assert T.get("devil.base_prob") == pytest.approx(0.2)


# ------------------------------------------------- 4. 按群集合

def test_scope_defaults_are_todays_hardcoded_sets():
    from runtime.ctx import PRIMARY_CID
    assert T.scope_of("trend") == (PRIMARY_CID,)
    assert T.scope_of("recap") == (PRIMARY_CID,)
    assert set(T.scope_of("trend")) == trend.TREND_CIDS
    assert set(T.scope_of("recap")) == recap.RECAP_CIDS


def test_scope_override_and_membership():
    T.set_scopes({"trend": ["g1", "g2"], "recap": []})
    assert T.in_scope("trend", "g1") is True
    assert T.in_scope("trend", "g3") is False
    assert T.in_scope("recap", "g1") is False, "空集合 = 谁都不给"


def test_scope_rejects_unknown_name():
    with pytest.raises(T.TuningError):
        T.set_scopes({"trendd": ["g1"]})


def test_scope_env_off_restores_defaults(monkeypatch):
    from runtime.ctx import PRIMARY_CID
    T.set_scopes({"trend": ["g1"]})
    assert T.in_scope("trend", "g1") is True
    monkeypatch.setenv("DZMM_TUNING", "off")
    assert T.in_scope("trend", "g1") is False
    assert T.scope_of("trend") == (PRIMARY_CID,)


# ------------------------------------------------- 5. 自证与面板

def test_summary_and_registry_are_json_serialisable():
    import json
    json.dumps(T.summary(), ensure_ascii=False)
    json.dumps(T.registry(), ensure_ascii=False)
    reg = T.registry()
    assert len(reg) == len(T.PARAMS)
    for item in reg:
        assert item["feature"] in {f.key for f in __import__(
            "runtime.features", fromlist=["FEATURES"]).FEATURES}


def test_rank_hour_set_parses_and_falls_back():
    assert bot._rank_hour_set() == {12, 18}
    T.set_overrides({"schedule.rank_hours": "9,21"})
    assert bot._rank_hour_set() == {9, 21}
    T.set_overrides({"schedule.rank_hours": "abc"})
    assert bot._rank_hour_set() == {12, 18}, "配错了要退回默认，不能彻底不发"
    T.set_overrides({"schedule.rank_hours": "25,7"})
    assert bot._rank_hour_set() == {7}, "非法小时要被丢掉"


def test_all_params_are_documented_for_the_page():
    """页面上要显示的每个参数都得有中文名与分档（否则页面只能显示英文 key）。"""
    for key, (p, feat) in T.PARAMS.items():
        assert p.label and p.label != key, key
        assert p.tier in ("必配", "可配", "内部"), key


# ------------------------------------------------- 6. 读口的静态自检

def test_tune_calls_have_cid_in_scope():
    """`_tune("...", cid)` 所在的函数里必须真的有 `cid`（参数或局部变量）。

    这条是**用真实 bug 换来的**：`_climax_hits(group_state)` 里没有 `cid`，
    我批量替换时插了 `_tune(..., cid)`，直到 `tests/test_heat.py` 红了才发现。
    与其等运行时 NameError，不如静态查一遍。
    """
    import ast
    problems = []
    for rel in ["bot.py"] + ["runtime/" + p.name
                             for p in sorted((ROOT / "runtime").glob("*.py"))]:
        if rel.endswith("tuning.py"):
            continue
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            uses_cid = False
            has_cid = any(a.arg == "cid" for a in node.args.args)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                    if name in ("_tune", "_tune_scope") and len(sub.args) >= 2:
                        arg = sub.args[1]
                        if isinstance(arg, ast.Name) and arg.id == "cid":
                            uses_cid = True
                # 函数体内只要出现过对 cid 的赋值/循环变量就算有
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name) and t.id == "cid":
                            has_cid = True
                if isinstance(sub, (ast.For, ast.AsyncFor)):
                    t = sub.target
                    if isinstance(t, ast.Name) and t.id == "cid":
                        has_cid = True
            if uses_cid and not has_cid:
                problems.append("{}:{}()".format(rel, node.name))
    assert not problems, "这些函数用了 `_tune(..., cid)` 但没有 cid：{}".format(problems)
