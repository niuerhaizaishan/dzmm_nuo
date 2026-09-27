"""P12-T：异性恋人设（若瑾）的**代码级过滤**守卫。

起因（2026-09-27 屋主："羞辱的话，还是女生人设啊"）：羞辱/发骚这两条链路用的是**专用
提示词**，里面写死"自称妹妹、叫目标姐姐""露骨描述她身体的淫荡反应"。人设里写了覆盖声明，
实测**压不住**：对男性目标已经变成若瑾的 S 口吻，对女性目标却照样输出了性羞辱。

所以取向这件事必须在代码里过滤，不指望模型自觉：

- 羞辱：`brains.generate_insult(..., gender, straight)` —— 女性目标 + 异性恋 →
  换一套"只毒舌"风格库、不掺露骨词库、并注入硬约束；
- 发骚/场景：`runtime.fasao` —— 女性目标 + 异性恋 → 直接回绝（话术 `wrong_gender`），
  而且**不加发情值**。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402

RUOJIN_CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


@pytest.fixture(autouse=True)
def _bind(monkeypatch):
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {RUOJIN_CID: "ruojin"})


@pytest.fixture
def ruojin_ctx():
    """把"当前群"切到若瑾那个群（`scene()/fasao()` 用 ctx 读 cid）."""
    from runtime import ctx
    ctx.set_active_cid(RUOJIN_CID)
    yield
    ctx.reset_active_cid()


def _capture(monkeypatch):
    """把 `_call_llm` 换成只记录 user content 的桩，返回 seen dict."""
    seen = {}

    def fake(prompt, content, api_key=None, max_tokens=0, **kw):
        seen["prompt"] = prompt
        seen["content"] = content
        return "（桩输出）"

    monkeypatch.setattr(brains, "_call_llm", fake)
    return seen


# ---------------------------------------------------------------- 取向标记

def test_orientation_flag_only_on_ruojin():
    assert P.orientation(RUOJIN_CID) == "straight"
    assert P.is_straight(RUOJIN_CID) is True
    assert P.is_straight(AUTH) is False, "默认人设不能带上这个限制"
    assert P.orientation(AUTH) == ""


# ---------------------------------------------------------------- 羞辱过滤

def test_female_target_also_gets_the_hard_rule(monkeypatch):
    """屋主 2026-09-27："对女生也调整为露骨、淫贱" → 默认男女一视同仁（都拉满）."""
    monkeypatch.setattr(brains, "RANDOM_INSULT_PROB", 1.0)
    # ⚠️ "拉满"那条走的是 HARD_INSULT_PROB（0.75），不是 RANDOM_INSULT_PROB ——
    #    只压 RANDOM 的话这条断言会 25% 概率随机挂掉（曾经就是这么飘的）。
    monkeypatch.setattr(brains, "HARD_INSULT_PROB", 1.0)
    seen = _capture(monkeypatch)
    brains.generate_insult("昵称：阿雅\n性别：女", gender="女生", straight=True)
    got = seen["content"]
    assert "拉到最满" in got, "女性目标也要注入'拉满'约束"
    assert "只毒舌" not in got
    assert "随机羞辱词库" in got, "女性目标同样掺露骨词库"


def test_plain_for_female_switch_still_works(monkeypatch):
    """反悔开关：把 INSULT_PLAIN_FOR_FEMALE 改回 True，女性目标就只会被毒舌."""
    monkeypatch.setattr(brains, "INSULT_PLAIN_FOR_FEMALE", True)
    monkeypatch.setattr(brains, "RANDOM_INSULT_PROB", 1.0)
    seen = _capture(monkeypatch)
    brains.generate_insult("昵称：阿雅\n性别：女", gender="女生", straight=True)
    got = seen["content"]
    assert "只毒舌，不涉情欲" in got
    assert any(s in got for s in brains.INSULT_STYLES_PLAIN)
    assert not any(s in got for s in brains.INSULT_STYLES)
    assert "肉便器" not in got, "露骨随机词库要被跳过"


def test_male_target_keeps_the_old_flavour(monkeypatch):
    monkeypatch.setattr(brains, "RANDOM_INSULT_PROB", 1.0)
    monkeypatch.setattr(brains, "HARD_INSULT_PROB", 1.0)   # 同上：拉满那条用的概率
    seen = _capture(monkeypatch)
    brains.generate_insult("昵称：小张\n性别：男", gender="男生", straight=True)
    got = seen["content"]
    assert "只毒舌" not in got
    assert "露骨淫荡" in got
    # 词库块用**固定表头**判断（抽到的具体词是随机的，不能拿"母狗"这种词断言）
    assert "随机羞辱词库" in got, "男性目标照旧掺露骨词库"


def test_default_persona_is_untouched(monkeypatch):
    """不带 straight 时行为与以前一样（认证群那套一个字不变）."""
    monkeypatch.setattr(brains, "RANDOM_INSULT_PROB", 0.0)
    seen = _capture(monkeypatch)
    brains.generate_insult("昵称：阿雅\n性别：女")            # 老调用方式
    got = seen["content"]
    assert "只毒舌" not in got and "露骨淫荡" in got
    assert any(s in got for s in brains.INSULT_STYLES), "用的是原来那套风格库"


def test_unknown_gender_does_not_downgrade_by_default(monkeypatch):
    """默认政策下性别未知也走"拉满"（不再降级成只毒舌）—— 与男女一视同仁一致."""
    seen = _capture(monkeypatch)
    brains.generate_insult("昵称：某人\n性别：未知", gender="未知", straight=True)
    assert "拉到最满" in seen["content"]


def test_unknown_gender_takes_the_safe_side_when_switch_on(monkeypatch):
    """开着"对女性收敛"时，未知性别走**保守**那条（宁可骂轻，不押错取向）."""
    monkeypatch.setattr(brains, "INSULT_PLAIN_FOR_FEMALE", True)
    seen = _capture(monkeypatch)
    brains.generate_insult("昵称：某人\n性别：未知", gender="未知", straight=True)
    assert "只毒舌" in seen["content"]


def test_male_gender_gets_the_hard_rule(monkeypatch):
    """男性目标 + S 人设 → 注入"拉满"约束，且掺词库概率更高（屋主：再淫贱露骨一些）."""
    seen = _capture(monkeypatch)
    brains.generate_insult("昵称：小张\n性别：男", gender="男生", straight=True)
    assert "拉到最满" in seen["content"]
    assert brains.HARD_INSULT_PROB > brains.RANDOM_INSULT_PROB


# ---------------------------------------------------------------- 发骚/场景过滤

def test_scene_allows_female_target_by_default(monkeypatch, ruojin_ctx):
    """默认（INSULT_PLAIN_FOR_FEMALE=False）：女性目标照样开局（男女一视同仁）."""
    from runtime import fasao
    monkeypatch.setattr(fasao, "_parse_scene_target", lambda t: ("u-f", "阿雅", []))
    monkeypatch.setattr(fasao, "_gender_of", lambda uid, cid=None, **kw: "女生")
    monkeypatch.setattr(fasao, "_pick_combo_by_xp", lambda xp: ("场景", "体位", "附加"))
    monkeypatch.setattr(fasao, "generate_scene",
                        lambda s, **kw: "（桩场景）")
    monkeypatch.setattr(fasao, "_set_last_action", lambda *a, **kw: None)
    monkeypatch.setattr(fasao, "_set_fasao_state", lambda *a, **kw: None)
    monkeypatch.setattr(fasao, "_add_heat", lambda *a, **kw: None)
    monkeypatch.setattr(fasao, "get_profile", lambda uid: {})
    assert "桩场景" in fasao.scene("/发骚 阿雅")


def test_scene_refuses_female_target_when_switch_on(monkeypatch, ruojin_ctx):
    """反悔开关：开着"对女性收敛"时，女性目标直接回绝且**不涨发情值**."""
    import brains
    from runtime import fasao
    monkeypatch.setattr(brains, "INSULT_PLAIN_FOR_FEMALE", True)
    monkeypatch.setattr(fasao, "_parse_scene_target", lambda t: ("u-f", "阿雅", []))
    monkeypatch.setattr(fasao, "_gender_of", lambda uid, cid=None, **kw: "女生")
    monkeypatch.setattr(fasao, "_phrase", lambda key, cid=None, **kw: "我只跟男的玩。")
    heats = []
    monkeypatch.setattr(fasao, "_add_heat", lambda *a, **kw: heats.append(a))
    assert fasao.scene("/发骚 阿雅") == "我只跟男的玩。"
    assert heats == [], "被回绝的目标不该涨发情值"


def test_scene_allows_male_target(monkeypatch, ruojin_ctx):
    from runtime import fasao
    monkeypatch.setattr(fasao, "_parse_scene_target", lambda t: ("u-m", "小张", []))
    monkeypatch.setattr(fasao, "_gender_of", lambda uid, cid=None, **kw: "男生")
    monkeypatch.setattr(fasao, "_pick_combo_by_xp", lambda xp: ("场景", "体位", "附加"))
    monkeypatch.setattr(fasao, "generate_scene",
                        lambda s, **kw: "（桩场景）")
    monkeypatch.setattr(fasao, "_set_last_action", lambda *a, **kw: None)
    monkeypatch.setattr(fasao, "_set_fasao_state", lambda *a, **kw: None)
    monkeypatch.setattr(fasao, "_add_heat", lambda *a, **kw: None)
    monkeypatch.setattr(fasao, "get_profile", lambda uid: {})
    out = fasao.scene("/发骚 小张")
    assert "桩场景" in out and "小张" in out


def test_fasao_allows_female_target_by_default(monkeypatch, ruojin_ctx):
    from runtime import fasao
    monkeypatch.setattr(fasao, "_find_in_profiles", lambda t: ("u-f", {}))
    monkeypatch.setattr(fasao, "get_full_name", lambda uid: "阿雅")
    monkeypatch.setattr(fasao, "_gender_of", lambda uid, cid=None, **kw: "女生")
    monkeypatch.setattr(fasao, "generate_heat_fasao", lambda full: "（桩发骚）")
    monkeypatch.setattr(fasao, "_add_heat", lambda *a, **kw: None)
    assert "桩发骚" in fasao.fasao("/发骚 阿雅")


def test_fasao_refuses_female_target_when_switch_on(monkeypatch, ruojin_ctx):
    import brains
    from runtime import fasao
    monkeypatch.setattr(brains, "INSULT_PLAIN_FOR_FEMALE", True)
    monkeypatch.setattr(fasao, "_find_in_profiles", lambda t: ("u-f", {}))
    monkeypatch.setattr(fasao, "get_full_name", lambda uid: "阿雅")
    monkeypatch.setattr(fasao, "_gender_of", lambda uid, cid=None, **kw: "女生")
    monkeypatch.setattr(fasao, "_phrase", lambda key, cid=None, **kw: "我只跟男的玩。")
    assert fasao.fasao("/发骚 阿雅") == "我只跟男的玩。"
