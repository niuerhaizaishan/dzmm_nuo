"""P12-T：第三个 Bot「若瑾」的人设守卫。

屋主口径："人设基本参考小小糯，名字改为若瑾，同时**纯 S 属性、异性恋**。"
基础提示词（`CMD_PROMPT` 那套）写的是小小糯的软糯/百合/被引导，所以若瑾必须靠
`RUOJIN_EXTRA` 的**覆盖声明**压住 —— 这几条钉住"压得住"这件事不会悄悄失效。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import personas as P                                          # noqa: E402

RUOJIN_CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"


@pytest.fixture(autouse=True)
def _bind_group(monkeypatch):
    """把「若瑾那个群」绑到 ruojin 人设上（线上是 config.json 的 groups[].persona 在做）.

    不绑的话 `persona_key_for(未知群)` 会回落到默认人设（小小糯）—— 这正是设计：
    哪个群用哪套人设由配置决定，代码只提供"可用的人设"。
    """
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {RUOJIN_CID: "ruojin"})


def test_persona_registered_with_own_name():
    rec = P.PERSONAS.get("ruojin")
    assert rec, "若瑾的人设 key 必须在代码里登记（配置只能覆盖已存在的 key）"
    assert rec["name_full"] == "若瑾" and rec["name_short"] == "若瑾"
    assert P.me(RUOJIN_CID) == "若瑾"
    assert P.me_full(RUOJIN_CID) == "若瑾"


def test_extra_declares_the_override_first():
    """覆盖声明必须在最前面 —— 它是唯一的"压住基础人设"的机制。"""
    extra = P.RUOJIN_EXTRA
    assert "覆盖声明" in extra[:80]
    assert "作废" in extra[:120]
    for word in ("纯 S", "异性恋"):
        assert word in extra, word
    for bad in ("叫姐姐", "软糯撒娇", "百合", "自称妹妹"):
        assert bad in extra, "要明确点名作废的说法：{}".format(bad)


def test_phrases_speak_in_her_voice():
    """固定话术不能漏出默认人设：既没有"小小糯"，也没有撒娇尾音。"""
    for key in P.known_phrase_keys("ruojin"):
        got = P.p(key, RUOJIN_CID)
        assert "小小糯" not in got, key
        assert "～" not in got, "若瑾不用波浪号讨好人：{}".format(key)
    assert P.p("intro_text", RUOJIN_CID).startswith("我是若瑾")
    assert "母狗" not in P.p("help_text", RUOJIN_CID), "帮助文案别一上来就露骨"


def test_health_is_clean():
    assert P.health("ruojin") == [], P.health("ruojin")


def test_default_groups_are_untouched():
    """认证群还是小小糯（别把若瑾的覆盖招呼到别人头上）."""
    from personas import ROOMS
    auth = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
    assert P.me(auth) == "小小糯"
    assert auth not in ROOMS, "认证群不由 ROOMS 派生（它走默认人设）"


# ---------------------------------------------------------------- 羞辱前缀

def test_insult_prefix_only_hits_the_configured_target():
    """屋主指定：羞辱「工具熊」时消息最前面加 `/talk`（做成话术，可改可热加载）."""
    import bot
    assert bot._insult_prefix(RUOJIN_CID, "工具熊 Jr.") == "/talk"
    assert bot._insult_prefix(RUOJIN_CID, "工具熊") == "/talk"
    assert bot._insult_prefix(RUOJIN_CID, "小张") == ""
    assert bot._insult_prefix(RUOJIN_CID, "") == ""
    # 别的群没配 → 不加（认证群一个字不变）
    assert bot._insult_prefix("bd7fe6a4-0065-4551-ad3d-94116dba8f9c", "工具熊 Jr.") == ""


def test_insult_prefix_is_data_not_code():
    """关键词与内容都从话术表来（页面能改；改完热加载 1 秒生效）."""
    assert P.p("insult_prefix_who", RUOJIN_CID) == "工具熊"
    assert P.p("insult_prefix_text", RUOJIN_CID) == "/talk"
    assert not P.p("insult_prefix_who", "bd7fe6a4-0065-4551-ad3d-94116dba8f9c")


def test_bot_wires_the_prefix_and_drops_the_robot_mark():
    """加了前缀的消息不能再有"🤖 "在前面 —— 否则 /talk 就不在"最最前面"了."""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "_insult_prefix(cid, get_full_name(target_uid))" in src
    assert 'reply if _no_prefix else ("🤖 " + reply)' in src
    assert "_no_prefix = False" in src


# ---------------------------------------------------------------- 调教话术（P12-V）

DJ_KEYS = ("dj_format", "dj_no_target", "dj_start", "dj_timeout",
           "dj_replied", "dj_idle", "dj_hint")
CUTE_MARKS = ("哦～", "呀", "姐妹", "啦～", "～\n", "～。")


def test_diaojiao_phrases_default_to_the_old_literals():
    """没配覆盖的群（认证群/玫瑰房）一个字都不能变 —— 默认值 = 原来的硬编码字面量."""
    auth = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
    assert P.p("dj_format", auth) == "格式：/调教 目标 命令（例如 /调教 糯糯 学狗叫）"
    assert P.p("dj_no_target", auth) == "没找到调教目标哦～换个人试试？"
    assert P.p("dj_start", auth, who="糯糯") == "🔗 调教开始！目标：糯糯～"
    assert P.p("dj_timeout", auth) == "⏱️ 调教结束啦（5分钟没动静）～"
    assert "30秒没人发我就自己来" in P.p("dj_replied", auth)
    assert P.p("dj_idle", auth) == "30秒没人发命令呀～那我继续啦～"
    from runtime.diaojiao import DJ_PARTICIPATE_HINT
    assert P.p("dj_hint", auth) == DJ_PARTICIPATE_HINT


def test_ruojin_diaojiao_phrases_are_s_voiced():
    """若瑾是纯 S：调教这套文案不能漏出「哦～/呀/姐妹」的软糯口吻."""
    for key in DJ_KEYS:
        txt = P.p(key, RUOJIN_CID, who="糯糯")
        assert txt, "{} 必须有若瑾版本".format(key)
        for mark in CUTE_MARKS:
            assert mark not in txt, "{} 里还有软萌口吻 {!r}：{}".format(key, mark, txt)
    assert "{who}" not in P.p("dj_start", RUOJIN_CID, who="糯糯")


def test_diaojiao_flow_uses_the_phrases(monkeypatch):
    """`start_diaojiao` 真的走话术表（不是又写死一份）—— 用若瑾的音色验一次."""
    from runtime import ctx
    from runtime import diaojiao as dj

    monkeypatch.setattr(dj, "_parse_scene_target", lambda rest: ("u-1", "糯糯", ["学狗叫"]))
    monkeypatch.setattr(dj, "_dj_target_text", lambda uid: "目标资料")
    monkeypatch.setattr(dj, "generate_diaojiao", lambda *a, **kw: "跪下。")
    monkeypatch.setattr(dj, "_set_dj_state", lambda *a, **kw: None)
    monkeypatch.setattr(dj, "get_profile", lambda uid: {"xp": []})
    ctx.set_active_cid(RUOJIN_CID)
    try:
        out = dj.start_diaojiao(RUOJIN_CID, "/调教 糯糯 学狗叫", "u-2")
    finally:
        ctx.reset_active_cid()
    assert out.startswith("🔗 调教开始。目标：糯糯。"), out
    assert "跪下。" in out
    assert out.rstrip().endswith(P.p("dj_hint", RUOJIN_CID)), out
    assert "想参与的姐妹" not in out
