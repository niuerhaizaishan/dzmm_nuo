# -*- coding: utf-8 -*-
"""P12-H 追加：玫瑰房的身份从「小小糯」切到「薇丝」——**只换身份，不换功能**。

背景：玫瑰房过去由侧车账号（昵称「小小糯～」）发言，2026-09-26 起群文本改由
官方 Bot「薇丝-bot」发送，人设 `rose_butler` 也改名成「薇丝」。
但代码里**大量文案是写死「小小糯」的**，机制只覆盖了两处：

  · `brains.py` 的系统提示词 → `apply_persona()` 换名字；
  · `personas.PHRASES` 话术表 → `p(key, cid)` 取本群那份。

其余（房间剧本、欢迎语、撞验证码提示、记忆回执、星座册子、运势…）都是**直接发**
的字符串：玫瑰房的客人会看到「小小糯」，可发言者叫「薇丝-bot」——对不上。

本文件钉住两层：
  1. **机制**：`personas.fill()` / `me()` / `p()` 按群填自称；`identity.mentions_self()`
     按群认名字（客人喊「薇丝」要算在叫我）；
  2. **护栏**：群内可见的模块里，不许再有**写死默认名**的字符串常量
     （AST 扫描；只放行 docstring、人设默认值、以及发给我的微信/飞书告警）。
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import bot              # noqa: E402
import personas as P    # noqa: E402
import runtime.identity as identity  # noqa: E402

ROSE = P.ROSE_CID
NUO_GROUP = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"   # 认证妹子群（默认人设）
OTHER = "test-cid-not-rose"


@pytest.fixture(autouse=True)
def _clean():
    P.set_content_overrides({})
    P.set_config_personas({})
    P.set_cid("")
    yield
    P.set_content_overrides({})
    P.set_config_personas({})
    P.set_cid("")


# ---------------------------------------------------------------- 1. fill / me

def test_me_is_the_persona_name_per_group():
    assert P.me(NUO_GROUP) == P.DEFAULT_NAME_SHORT
    assert P.me_full(NUO_GROUP) == P.DEFAULT_NAME_FULL
    # 玫瑰房的代码默认人设就叫薇丝（配置里也是薇丝）
    assert P.me(ROSE) == "薇丝"
    assert P.me_full(ROSE) == "薇丝"


def test_fill_rewrites_self_reference():
    assert P.fill("我是{me}～", ROSE) == "我是薇丝～"
    assert P.fill("我是{me_full}19岁", ROSE) == "我是薇丝19岁"
    # 旧代码遗留的默认名也要被换掉（不是只认占位符）
    assert P.fill("小小糯记住了", ROSE) == "薇丝记住了"
    assert P.fill("小小糯～记住了", ROSE) == "薇丝记住了"
    # 默认人设：原样（零开销路径）
    assert P.fill("我是小小糯～", NUO_GROUP) == "我是小小糯～"


def test_fill_never_blows_up_on_missing_placeholders():
    """少填一个占位符最多是文案难看，**绝不能抛异常**（群里就发不出去了）。"""
    assert P.fill("欢迎{name}，我是{me}", ROSE) == "欢迎{name}，我是薇丝"
    assert P.fill("欢迎{name}，我是{me}", ROSE, name="阿May") == "欢迎阿May，我是薇丝"
    assert P.fill("有 {who} 和 {unknown}", ROSE, who="他") == "有 他 和 {unknown}"
    assert P.fill("", ROSE) == ""
    assert P.fill("{me}", ROSE) == "薇丝"


def test_p_phrases_are_persona_aware():
    """话术出口统一过 fill()：玫瑰房的话术里不许出现「小小糯」。"""
    for k in P.known_phrase_keys("rose_butler"):
        assert "小小糯" not in P.p(k, ROSE), k
    # 认证群一个字都不能变
    assert "小小糯" in P.p("mem_saved", NUO_GROUP)
    assert P.p("mem_list_head", NUO_GROUP) == "📝 小小糯记住的事（共 {n} 条）："
    # 房间里 mem_list_head 带 {n}，格式化仍然要能用
    assert P.p("mem_list_head", ROSE).format(n=3) == "📝 玫瑰房的册子（共 3 条）："


# ---------------------------------------------------------------- 2. 群内常量

def test_bot_constants_are_persona_aware():
    """bot.py 里那几个"整条发出去"的常量：认证群照旧、玫瑰房换名字。"""
    cases = [(bot.CAPTCHA_NOTICE, "小小糯", "薇丝"),
             (bot.HELP_TEXT, "小小糯", "薇丝"),
             (bot.WELCOME_TEXT, "小小糯", "薇丝"),
             (bot.WELCOME_TEXT_PLAIN, "小小糯", "薇丝"),
             (bot.INTRO_TEXT, "小小糯", "薇丝")]
    for text, old, new in cases:
        for cid, want, other in ((NUO_GROUP, old, new), (ROSE, new, old)):
            got = P.fill(text, cid)
            assert want in got, (cid, want, got[:40])
            assert other not in got, (cid, other, got[:40])


def test_help_and_intro_fall_back_to_persona_text():
    """认证群的话术是 None（用 bot.py 常量）；玫瑰房有自己的 help/intro。"""
    assert P.p("help_text", NUO_GROUP) == ""
    assert "小小糯的指令说明" in P.fill(bot.HELP_TEXT, NUO_GROUP)
    assert "薇丝能做的事" in P.p("help_text", ROSE)
    assert "小小糯" not in P.p("help_text", ROSE)
    assert "小小糯" not in P.p("intro_text", ROSE)


def test_room_greeting_and_ghost_script_use_the_room_name():
    """房间剧本是固定文案、不走 AI —— 名字必须跟着管家改。"""
    # 欢迎语的随机"招待品"那一行本身可能不带名字，所以逐条候选句验（别靠随机）
    assert P.ROOM_GREET_LINES, "前置条件：欢迎语有候选句"
    assert any("薇丝" in P.fill(x, ROSE) for x in P.ROOM_GREET_LINES)
    for x in P.ROOM_GREET_LINES:
        assert "小小糯" not in P.fill(x, ROSE), x
    # 整条欢迎语：名字/招待品/候选句三个阶段都不许漏出旧名
    for _ in range(30):
        greet = bot._room_greet_text("阿May", ROSE)
        assert "阿May" in greet and "小小糯" not in greet, greet
    assert "薇丝" in P.fill(P.ROOM_GREET_TEMPLATE.replace(
        "{name}", "阿May").replace("{item}", "一杯茶").replace(
        "{line}", "{me}请自便～"), ROSE)

    for tpl in (P.ROOM_GHOST_TEXT, P.ROOM_GHOST_WARN):
        got = P.fill(tpl, ROSE)
        assert "薇丝" in got and "小小糯" not in got
    back = P.fill(P.ROOM_GHOST_BACK, ROSE, who="Vv")
    assert "薇丝" in back and "小小糯" not in back and "Vv姐姐" in back
    # 默认群下这些模板还是老样子（它们只在房间里用，纯属回归钉）
    assert "小小糯" in P.fill(P.ROOM_GHOST_WARN, NUO_GROUP)


# ---------------------------------------------------------------- 3. 认名字

def test_mentions_self_accepts_the_room_name():
    """客人喊「薇丝」要算在叫我 —— 否则房间不接话、调戏也不计数。"""
    assert identity.mentions_self("薇丝在吗", ROSE) is True
    assert identity.mentions_self("@薇丝 你好", ROSE) is True
    assert identity.mentions_self("薇丝-bot 你好", ROSE) is True
    assert identity.mentions_self("小小糯在吗", ROSE) is True   # 旧名字也认
    assert identity.mentions_self("今天天气不错", ROSE) is False
    assert identity.mentions_self("@张三 你好", ROSE) is False
    assert identity.mentions_self("", ROSE) is False


def test_is_at_me_and_strip_at_are_group_aware(monkeypatch):
    assert identity.is_at_me("@薇丝 你好", ROSE) is True
    assert identity.is_at_me("薇丝 你好", ROSE) is False        # 没有 @ 就不算 @
    assert bot._strip_at("@薇丝 你好呀", ROSE) == "你好呀"
    # 线上配置里这个 Bot 的 name/short 是「薇丝-bot」/「薇丝」（register_bots 注入），
    # 这里照抄一份：长名优先必须让「@薇丝-bot」整段剥掉，不能留 `-bot` 残余。
    monkeypatch.setattr(identity, "_BOT_NAMES", {"薇丝-bot", "薇丝"})
    names = identity.self_names_longest_first(ROSE)
    assert names.index("薇丝-bot") < names.index("薇丝"), "长名必须排在短名前面"
    assert bot._strip_at("@薇丝-bot 你好呀", ROSE) == "你好呀"
    assert bot._strip_at("@薇丝 你好呀", ROSE) == "你好呀"
    # 剥不掉的原文要原样留着（别把客人名字切掉）
    assert bot._strip_at("@阿May 你好", ROSE) == "@阿May 你好"


def test_room_trigger_gate_accepts_the_new_name():
    """房间分支的总闸门（runtime/sign.py::_is_room_trigger）。"""
    from runtime.sign import _is_room_trigger
    assert _is_room_trigger(ROSE, "some-uid", "薇丝在吗") is True
    assert _is_room_trigger(ROSE, "some-uid", "小小糯在吗") is True
    # 没点名、也不是点单/摘花/星座 → 不该被拦住（避免把闲聊全吞进房间分支）
    assert _is_room_trigger(ROSE, "some-uid", "今晚吃什么好呢") is False


# ---------------------------------------------------------------- 4. 护栏

# 扫描范围 = **会往群里发文字**的模块。
# 不扫 brains.py：那里的「小小糯」全是 `_p("XXX_PROMPT", …)` 的提示词默认值，
# 由 apply_persona() 统一换名（已有 test_personas_* 覆盖）。
SCAN_FILES = ["bot.py", "runtime/sign.py", "runtime/trend.py", "runtime/recap.py",
              "runtime/diaojiao.py", "runtime/dice.py", "runtime/memory.py",
              "runtime/fortune.py", "runtime/perf.py"]

# 允许出现的"告警前缀"：这些字符串发给**我**（微信/飞书/日志），不进群
ALERT_PREFIXES = ("【小小糯", "[小小糯")


def _string_constants(path: Path):
    """(节点, 值, 是不是 docstring) 三元组流。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node, node.value, id(node) in docs


def test_no_group_facing_string_hardcodes_the_default_name():
    """群内可见的模块里，不许再有**写死默认名**的字符串常量。

    放行两类（都不进群）：docstring；以及 `【小小糯bot】…` 这种发给我的告警。
    新增群内文案请用 `personas.fill(text, cid)` / `{me}` 占位符 / `p(key, cid)`。
    """
    bad = []
    for rel in SCAN_FILES:
        path = ROOT / rel
        for node, value, is_doc in _string_constants(path):
            if "小小糯" not in value or is_doc:
                continue
            if any(x in value for x in ALERT_PREFIXES):
                continue
            bad.append("{}:{}: {}".format(rel, node.lineno,
                                          " ".join(value.split())[:70]))
    assert not bad, "群内文案里还写死着默认名：\n" + "\n".join(bad)
