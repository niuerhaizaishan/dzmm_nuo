"""P12-AW：茶水间接入"真运势"（`#运势` 裸指令 + `@我算运势` 自然语言）。

屋主 2026-09-29/30：
  1. "把妹子群小小糯的运势功能复制到茶水间，但是要用 **#运势** 这个命令"
  2. "**@小小糯 算运势（包括之类的话）也可以触发**，@ 的时候不用加 `#`"
  3. 限频：冷却内**原样重发整卡**（复用缓存、不调模型）；§7 选 (a)（认证群一起变真卡）
  4. "开工"

方案文档：`docs/P12-AW-茶水间运势.md`。

三个必须守住的点：
  · `#` 入口要在 `at_only` 闸门**之前**（那段会把没 @ 的消息直接 continue 掉）；
  · 白名单只放**运势族**（其他 `#xxx` 一个字都不改，别吞别家 bot 的语法）；
  · 运势卡**不经过 `_ensure_decor`**（它的 `bang` 半段会把最后一个"。"换成"！！"，改卡）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                     # noqa: E402
from runtime import ctx                                        # noqa: E402
from runtime import fortune as FT                              # noqa: E402
from runtime import tuning as T                                # noqa: E402

BOT_PY = (ROOT / "bot.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1. 触发词

def test_hash_variants_are_commands():
    for c in ("#运势", "#算命", "#占卜", "#今日运势", "#今日运气", "#运气"):
        assert FT.is_fortune_cmd(c) is True, c
    assert FT.is_fortune_cmd("＃运势") is True
    for c in ("#写诗", "#帮助", "运势", "#"):
        assert FT.is_fortune_cmd(c) is False, c
    # `/` 那套一个字没变
    for c in ("/运势", "/算命", "/占卜", "/今日运势", "/运气"):
        assert FT.is_fortune_cmd(c) is True, c
    # ⚠️ `startswith` 语义 ⇒ `#运势趋势` 也算运势指令。
    # 认证群里 `/运势趋势` 会被 **trend 分支先接走**（`bot.py` 里它排在 fortune 之前）；
    # 茶水间没开 trend，所以 `#运势趋势` 会给一张**个人卡**（方案文档已记这个边角）。
    assert FT.is_fortune_cmd("#运势趋势") is True


def test_natural_language_intent():
    yes = ("算运势", "帮我算运势", "今日运势怎么样", "算一卦", "给我占卜一下",
           "看看我今天运势", "帮我算算运气", "来个占卜", "今天命盘怎么样",
           # P12-BB：屋主 2026-09-30 原话就是「**给我卜一卦**」—— 上面这些词一个都不中
           "给我卜一卦", "卜一卦", "帮我卜卦", "起一卦", "摇一卦", "帮我解卦",
           "求个签", "看看我的八字", "紫微排盘")
    for t in yes:
        assert FT.is_fortune_ask(t) is True, t
    no = ("我今天运气好差", "在干嘛呢", "", "   ", "运势榜", "群里运势怎么样",
          "运势趋势", "大家今天运势如何", "帮我看看这个 bug",
          # 只提到"卦"不算请求（别把闲聊/感叹吃进来）
          "这卦真准", "卦象不错", "刚抽的签好灵")
    for t in no:
        assert FT.is_fortune_ask(t) is False, t


# ---------------------------------------------------------------- 2. 接线（顺序很关键）

def test_hash_gate_runs_before_the_at_only_gate():
    """裸 `#运势` 没有 @ —— 如果 `#` 入口排在 `at_only` 之后，它会被直接丢掉。"""
    # P3-19 起主循环体在 runtime/mainloop.py（bot 桥 B 前缀）
    ml = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    i_hash = ml.index('if t.startswith("#") and B._feat_on(cid, "fortune")')
    i_at_only = ml.index('if B._feat_on(cid, "at_only"):')
    assert i_hash < i_at_only, "# 入口必须在 at_only 闸门之前"


def test_hash_gate_is_whitelisted_and_switchable():
    """只放行运势族，而且要有按群开关（`fortune.hash_cmd`，默认关）。"""
    ml = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    i = ml.index('if t.startswith("#") and B._feat_on(cid, "fortune")')
    seg = ml[i:i + 400]
    assert "B.is_fortune_cmd(t)" in seg, "白名单：只认运势族"
    assert 'B._tune("fortune.hash_cmd", cid)' in seg, "要能按群开关"


def test_at_branch_uses_the_fortune_entry():
    """@ 分支走 `fortune_reply`（带当天卡缓存），并声明"别补装饰"。

    P3-3-4 起 @ 分支在 runtime/at_chat.py。
    """
    src_a = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    i = src_a.index('elif is_fortune_ask(q) and _feat_on(cid, "fortune"):')
    seg = src_a[i:i + 700]
    assert "fortune_reply" in seg, "要走进口（带冷却/缓存），别直接调 fortune"
    assert "_no_decor = True" in seg


def test_trend_family_never_gives_a_personal_card():
    """P12-BR e：`#运势趋势` 这一族在**没开 `trend`** 的群里不许给个人运势卡。

    为什么会答错：`is_fortune_cmd` 是 `startswith` 语义 ⇒ `#运势趋势` 也命中运势；
    茶水间 `trend` 关着 ⇒ trend 分支被闸门跳过 ⇒ 落到 fortune ⇒ **给他一张个人卡**
    （指令说的是"趋势"，给的是"个人"，属于答错）。现在趋势族先挑出来：能给群榜给群榜，
    给不了就明说没开（话术 `trend_off`）。
    """
    # P3-3-2 起运势分支随分派链在 runtime/dispatch.py（@ 分支的锚点仍在 BOT_PY）
    src_d = (ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")
    i = src_d.index('elif is_fortune_cmd(cmd) and _feat_on(cid, "fortune"):')
    seg = src_d[i:i + 1500]
    assert "is_trend_cmd(cmd) is not None or is_trend_board_cmd(cmd)" in seg, \
        "趋势族要先被认出来"
    assert "week_trend_board(cid)" in seg and 'p("trend_off", cid)' in seg
    assert seg.index("week_trend_board(cid)") < seg.index("asyncio.to_thread(fortune, cmd_sender, cid)"), \
        "先判趋势、再给个人卡（顺序反了就还是答错）"
    assert "算命" in seg, "兜底文案要能落地（话术没写就用代码里的那句）"


def test_decor_is_skipped_for_the_card():
    """P12-BD 起守卫里先做排版收口、再按概率补装饰 —— 装饰调用仍在**守卫之内**
    （运势卡的 `_no_decor = True` 依旧命中不了它），而且**只有这一处**。

    P3-3-4 起 @ 分支（含装饰守卫）在 runtime/at_chat.py。
    """
    src_a = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    assert "if not _no_decor:" in src_a
    i = src_a.index("if not _no_decor:")
    assert "_ensure_decor(reply" in src_a[i:i + 900], "装饰调用必须留在守卫里面"
    assert src_a.count("_ensure_decor(reply") == 1, "@ 分支只该有一处装饰调用"


# ---------------------------------------------------------------- 3. 冷却 / 缓存

def _stub_fortune(monkeypatch, calls):
    def _fake(uid, cid=None):
        calls.append(uid)
        return "卡-" + str(len(calls))
    monkeypatch.setattr(FT, "fortune", _fake)


def test_cooldown_zero_regenerates_every_time(state, monkeypatch):
    """`fortune.cooldown=0`（默认、认证群）⇒ 行为与以前完全一致：每次都重新生成。"""
    calls = []
    _stub_fortune(monkeypatch, calls)
    T.set_overrides({})
    ctx.set_active_cid("c1")
    try:
        a = FT.fortune_reply("u1", "c1")
        b = FT.fortune_reply("u1", "c1")
    finally:
        ctx.reset_active_cid()
    assert a == "卡-1" and b == "卡-2"
    assert len(calls) == 2


def test_cooldown_replays_the_same_card_without_calling_the_model(state, monkeypatch):
    """冷却内再问 ⇒ **原样重发整卡**，模型只被调 1 次（屋主定的行为）。"""
    calls = []
    _stub_fortune(monkeypatch, calls)
    T.set_overrides({"c1|fortune.cooldown": 3600})
    ctx.set_active_cid("c1")
    try:
        a = FT.fortune_reply("u1", "c1")
        b = FT.fortune_reply("u1", "c1")
        c = FT.fortune_reply("u2", "c1")          # 换个人 ⇒ 各自一张
    finally:
        ctx.reset_active_cid()
        T.set_overrides({})
    assert a == b, "同一天同一人必须原样重发"
    assert a == "卡-1" and c == "卡-2"
    assert len(calls) == 2, "只应调两次模型（u1 一次、u2 一次）"


def test_cache_prunes_other_days(state):
    """缓存只留当天：写今天的卡会把隔天的旧条目清掉。

    （`state` 夹具里 `save_state` 是 no-op，所以**直接往那个内存 dict 里塞**旧条目。）
    """
    today = FT.datetime.now().strftime("%Y-%m-%d")
    state["c1"] = {FT.FORTUNE_CARD_KEY: {"old": {"date": "2000-01-01",
                                                 "text": "隔天的旧卡"}}}
    assert FT._card_get("c1", "old", "2000-01-01") == "隔天的旧卡"

    FT._card_put("c1", "u1", today, "今天的卡")
    got = FT._card_cache("c1")
    assert "u1" in got and got["u1"]["text"] == "今天的卡"
    assert "old" not in got, "隔天的条目要被清掉（缓存只留当天）"
    assert FT._card_get("c1", "old", "2000-01-01") == ""


# ---------------------------------------------------------------- 4. 参数登记

def test_params_are_registered_with_safe_defaults():
    from runtime import features as F
    keys = {}
    for feat in F.FEATURES:
        for p in feat.params:
            keys[p.key] = p
    assert keys["fortune.hash_cmd"].default == 0, "默认关 ⇒ 其他群零变化"
    assert keys["fortune.cooldown"].default == 0, "默认 0 ⇒ 认证群行为不变"
    assert keys["fortune.cooldown"].hi == 86400
