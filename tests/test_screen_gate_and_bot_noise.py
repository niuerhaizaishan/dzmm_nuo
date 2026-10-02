"""2026-10-02 两起事故的回归测试。

## 事故一：`screen=False` 的上班群被按认证群规矩筛查并踢人

```
08:43:14 🎉 [f4ec4c21] 新成员加入 1 人，只发欢迎语     ← 入群事件，闸门正确
08:46:14   📇 [f4ec4c21] 成员对账：…开始筛查            ← 成员对账，闸门是假的
08:46:16 🚫 已移出 30932a9a
```

根因**不是**"第二个调用点忘了装闸门"，而是**闸门本身是死的**：

`config_store.feature_map()` 原先只导出 `groups[].features` 子表，而群级字段
`screen` 是**独立字段**（不在子表里）⇒ 覆盖表里从来没有 `"<cid>|screen"`
⇒ `features.on(cid, "screen")` 回落到注册表默认值，而 `Feature("screen", …)`
**没写 `default=`** ⇒ 默认 **True** ⇒ **`_feat_on(cid, "screen")` 对每个群都真**。

于是 `mainloop` 的对账闸门 `if not B._feat_on(_cid, "screen"): continue` 永远不 continue
⇒ 每次容器重启都对所有群做对账 ⇒ 上班群被当认证群筛查并踢人。
入群事件那条路侥幸没出事，只因为它**多查了一个** `cid in SCREEN_CIDS`。

⚠️ 而且已有一条测试 `assert '_feat_on(_cid, "screen")' in seg` 是**源码文本断言** ——
它看着对，但底层开关是死的，所以从来没拦住。**测试断言"代码长这样"不等于"行为对"。**

## 事故二：茶水间把别家 bot 的"记忆考核"当成我们的记忆指令

```
09:21:56 👋 [4fc4a2fd] @消息: 【记忆考核·第 1 级】请记住：mTrOE
09:21:56 💬 🤖 记好啦～小小糯会记住的～♡
```

`memory.MEMORY_TRIGGERS` 里有裸的"记住"，而别家 bot 的开场白写着"**请记住**："。
噪音过滤表（`recap.ignore_kw`）**早就有**，但只保护 recap 的输入，触发层没有这道过滤。
"""
import asyncio
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import features as F                                # noqa: E402
from runtime import joins as J                                   # noqa: E402
from runtime import roster                                       # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
WORK = "f4ec4c21-fb48-4be2-9972-e4128f65923b"


@pytest.fixture(autouse=True)
def _clean_overrides():
    F.set_overrides({})
    yield
    F.set_overrides({})


# ============================================================ 事故一

def _stub_cfg(specs):
    """只带 `feature_map()` 需要的那两个属性的最小 self。"""
    return types.SimpleNamespace(features={}, group_specs=specs)


def test_group_level_screen_field_reaches_the_feature_table():
    """群级 `screen` 必须桥进功能覆盖表 —— 否则 `_feat_on(cid,"screen")` 是死的。

    这是事故一的**根因**：闸门代码在，但它读的开关永远返回 True。
    """
    from runtime import config_store as CS
    cfg = _stub_cfg({
        "cOff": types.SimpleNamespace(screen=False, features={"chat": True}),
        "cOn": types.SimpleNamespace(screen=True, features={}),
    })
    fm = CS.Config.feature_map(cfg)
    assert fm.get("cOff|screen") is False, "群级 screen=False 必须进覆盖表"
    assert fm.get("cOn|screen") is True
    assert fm.get("cOff|chat") is True, "features 子表照旧要桥"

    # 端到端：桥进去之后，features.on 真的跟着变
    F.set_overrides(fm)
    assert F.on("cOff", "screen") is False
    assert F.on("cOn", "screen") is True


def test_screen_default_is_true_so_a_dead_gate_fails_open():
    """钉住"为什么这个 bug 是静默的"：默认值是 True。

    ⇒ 覆盖表缺 key 时闸门**放行**，而不是拦住。缺 key 不报错、不告警，
    只是那个开关悄悄失效 —— 这正是它藏了这么久的原因。
    """
    assert F.get("screen").default is True


# ------------------------------------------------ 2026-10-02 补：screen 双真源归一
#
# 事故一的修复让 feature_map() 把群级 screen 桥进覆盖表，但 bridges 是
# 「群级字段**后写覆盖**」—— 而功能矩阵（features 页）的三态行写的恰恰是
# **子表** `groups[].features.screen`。两个页面都能表达同一个开关，
# features 页写的那个会**静默失效**（探针实测：子表 False + 群级 True
# → `cid|screen: True`）。归一规则：读入时子表的 screen 吸收进群级字段
# （子表赢 —— 矩阵里的显式覆盖操作），并从子表删除；
# as_dict() 只序列化群级字段，配置文件里的 features.screen 在下一次保存后消失。

def _parse_groups(groups):
    from runtime import config_store as CS
    cfg = CS._parse({"version": CS.SCHEMA_VERSION, "groups": groups,
                     "bots": []}, {})
    return cfg.group_specs


def test_feature_subtable_screen_wins_and_gets_absorbed():
    """子表 screen 与群级字段同时存在 ⇒ 子表赢，且被吸收（单真源）。"""
    specs = _parse_groups([
        {"cid": "cA", "screen": True, "features": {"screen": False}},
        {"cid": "cB", "screen": False, "features": {"screen": True}},
    ])
    assert specs["cA"].screen is False, "矩阵里的显式覆盖（关）要赢"
    assert specs["cB"].screen is True, "矩阵里的显式覆盖（开）也要赢"
    assert "screen" not in specs["cA"].features, "吸收后子表里不许残留"
    assert "screen" not in specs["cB"].features
    # feature_map 读到的就是归一后的值
    from runtime import config_store as CS
    from runtime.config_store import Config
    fm = CS.Config.feature_map(
        types.SimpleNamespace(features={}, group_specs=specs))
    assert fm["cA|screen"] is False and fm["cB|screen"] is True


def test_group_level_screen_without_subtable_is_untouched():
    """只写群级字段（/edit 页的常态）⇒ 原样，不凭空造子表覆盖。"""
    specs = _parse_groups([{"cid": "cA", "screen": True}])
    assert specs["cA"].screen is True
    assert specs["cA"].features == {}
    specs2 = _parse_groups([{"cid": "cA", "features": {"chat": True}}])
    assert specs2["cA"].screen is False, "没写 screen 时默认 False（群级字段语义不变）"
    assert specs2["cA"].features == {"chat": True}, "别的功能覆盖照旧"


def test_roundtrip_drops_feature_subtable_screen():
    """归一 → 序列化：features.screen 不再写回文件（双真源逐步收敛）。"""
    import json
    from runtime import config_store as CS
    raw = {"version": CS.SCHEMA_VERSION,
           "groups": [{"cid": "cA", "alias": "A", "enabled": True,
                       "screen": True, "features": {"screen": False,
                                                    "chat": True}}],
           "bots": []}
    cfg = CS._parse(raw, {})
    out = json.loads(json.dumps(cfg.as_dict()))
    g = out["groups"][0]
    assert g["screen"] is False, "归一后的值落到群级字段"
    assert g["features"] == {"chat": True}, "子表里不再有 screen，其余保留"


def test_screen_members_refuses_to_touch_members_when_screen_is_off():
    """筛查链**自己**也要把关（闸门下沉），第三个调用点就不可能再漏。

    返回"全部放行"而不是空列表：语义是"这些人没问题，交给调用方去欢迎"。
    """
    F.set_overrides({"{}|screen".format(WORK): False})
    passed = asyncio.run(J.screen_members(
        None, WORK, ["u1", "u2"], False,
        welcome_of=lambda cid, plain=False: "", captcha_active=lambda: False))
    assert passed == ["u1", "u2"], "未开筛查的群：不筛查、不动成员、全部放行"


def test_screen_members_still_screens_when_screen_is_on():
    """反面：开了筛查的群必须照常筛（别把闸门做成了"一律不筛"）。"""
    F.set_overrides({"{}|screen".format(TEA): True,
                     "{}|screen_tourist".format(TEA): True,
                     "{}|screen_welcome".format(TEA): True})
    # 游客昵称（TOURIST_KEYWORDS = ["游客"]）→ 应该被踢，不放行
    kicked = []

    async def _kick(sender, cid, uid, full, warn, done, dry):
        kicked.append(uid)

    orig = J.kick_and_notify
    J.kick_and_notify = _kick
    try:
        passed = asyncio.run(J.screen_members(
            None, TEA, ["u_游客1"], False,
            welcome_of=lambda cid, plain=False: "", captcha_active=lambda: False))
    finally:
        J.kick_and_notify = orig
    assert kicked == ["u_游客1"], "游客要被踢"
    assert passed == [], "被踢的不该再放行去欢迎"
    assert _kick is not orig


def test_join_path_remembers_members_in_the_snapshot(monkeypatch):
    """入群事件也要写快照 —— 否则下次对账把刚欢迎过的人再处理一遍。"""
    saved = {"known_members": ["u1"]}
    monkeypatch.setattr(roster.state_store, "group_get",
                        lambda cid, k, d=None: saved.get(k, d))
    monkeypatch.setattr(roster.state_store, "group_set",
                        lambda cid, k, v: saved.__setitem__(k, v))
    roster.remember_members("c1", ["u2", "u1", ""])
    assert saved["known_members"] == ["u1", "u2"], "只增不减、去重、丢弃空值"
    roster.remember_members("c1", [])
    assert saved["known_members"] == ["u1", "u2"], "空输入不写盘"


def test_mainloop_join_path_calls_remember_members():
    """接线：mainloop 处理完入群事件后要写快照。"""
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    i = src.index("handle_join_event(")
    seg = src[i:i + 900]
    assert "remember_members(cid, joins)" in seg, "入群事件后要写快照"


# ============================================================ 事故二

def test_other_bot_quiz_text_is_noise_in_the_tea_room():
    """别家 bot 的游戏播报必须被判为噪音（复用 recap 那张表）。"""
    import bot
    from runtime import tuning as T
    T.set_overrides({"{}|recap.ignore_kw".format(TEA):
                     "打卡,摸鱼,躲藏,躲猫猫,巡查,记忆考核,摸鱼币,机选号码"})
    try:
        # 现场逐字
        assert bot._is_other_bot_noise(TEA, "【记忆考核·第 1 级】请记住：mTrOE") is True
        assert bot._is_other_bot_noise(TEA, "恭喜 晓 完成第 5 级记忆考核") is True
        assert bot._is_other_bot_noise(TEA, "第 3 级通过。现在收手可获得 3 摸鱼币") is True
        # 正常聊天不许被吃
        assert bot._is_other_bot_noise(TEA, "今天中午吃什么") is False
        assert bot._is_other_bot_noise(TEA, "记住我明天要开会") is False, \
            "真正的记忆请求（没命中 ignore_kw）不能被误伤"
    finally:
        T.set_overrides({})


def test_other_bot_noise_check_never_raises():
    """判定失败一律"不是噪音"：宁可多回一句，也不能把正常消息吃掉。"""
    import bot
    assert bot._is_other_bot_noise("no-such-cid", "随便") in (True, False)


def test_trigger_layer_filters_noise_before_taking_the_message():
    """触发层必须在"接话"之前过滤 —— 这才是治本的位置。

    ⚠️ 锚点用代码语句；`at_msg = t` 必须在过滤**之后**。
    """
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    i = src.index("_noisy = B._is_other_bot_noise(cid, t)")
    j = src.index("at_msg = t", i)
    assert i < j, "噪音过滤要在接话之前"
    # 刻意不用 continue（后面还有记账/上下文，跳过整个循环体是另一类隐性变化）
    seg = src[i:j]
    assert "continue" not in seg, "只该『不接话』，不该跳过整条消息的处理"


def test_memory_extraction_is_also_guarded():
    """双保险：记忆库被污染是不可逆的，提取前再挡一次。"""
    src = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    i = src.index("_extract_remember(q)")
    seg = src[max(0, i - 900):i]
    assert "_recap_is_noise" in seg, "记忆提取前要再挡一次噪音"
    assert "not _noisy" in src[i:i + 300], "命中噪音时不许提取"
