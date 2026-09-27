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
