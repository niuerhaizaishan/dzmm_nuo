"""P12-K：玫瑰房星象卡改「分数 + 宜忌 + 五段正文 + 幸运三件套」的守卫测试。

这张卡的风险有两处，测试就盯这两处：

1. **模型不按五段写** —— 必须退回旧口径（分数 + 一段点评），绝不开天窗、也绝不
   把半张卡发出去（解析不够三段就当没认出来）。
2. **解析器太贪** —— 正文里出现"综合来看""财运：…"这类词不能被当成段标签。

另外钉死：四维口径一个字没变（星象榜的名次不受影响），新增的幸运数字/速配星座
是确定性的（同人同日同座必相同）。
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from personas import ROSE_SIGN_EMOJI, ROSE_SIGN_MATCH   # noqa: E402
from runtime import sign as S                            # noqa: E402

FIVE = """【综合运势】今天适合把节奏放慢一点，别急着替所有人收尾，先把自己的事做完。
【爱情运势】单身的人别急着回应示好，先看清对方的态度；有伴的记得把话说完。
【事业学业】上午先做最需要专注的那件，下午再回消息，别让别人的急事挤掉你的正事。
【财富运势】该花的钱别省，不该垫的情分也别垫，记账比心情可靠。
【健康运势】久坐记得起身走动，晚上少喝一杯，睡前把手机放远一点。"""


def _fix(monkeypatch, text):
    """把 LLM 调用换掉：`_sign_recent` 不读记录，`generate_horoscope` 返回给定文本."""
    monkeypatch.setattr(S, "_sign_recent", lambda uid, cid: "")
    monkeypatch.setattr(S, "generate_horoscope", lambda *a, **kw: text)


# ------------------------------------------------------------ 五段解析

def test_split_five_sections():
    got = S._split_sections(FIVE)
    assert list(got) == ["综合", "爱情", "事业学业", "财富", "健康"]
    assert got["爱情"].startswith("单身的人")
    assert "【" not in got["健康"] and "\n" not in got["健康"]


def test_split_tolerates_aliases_numbering_and_spacing():
    txt = ("1. 【爱情】今天适合把话说清楚。\n"
           "2、【事业】先把最要紧的做完。\n\n"
           "3. 财运：别替别人垫钱，记账更可靠。\n"
           "4. 健康运势 ：早点睡，别熬到眼睛发干。\n"
           "综合运势：今天整体偏稳，慢一点反而顺。")
    got = S._split_sections(txt)
    assert set(got) == {"综合", "爱情", "事业学业", "财富", "健康"}
    assert got["财富"].startswith("别替别人垫钱")
    assert got["综合"].startswith("今天整体偏稳")


def test_split_survives_single_line_output():
    """模型把五段挤在一行（只靠标签分隔）也要能拆开。"""
    one = " ".join(FIVE.split("\n"))
    got = S._split_sections(one)
    assert len(got) == 5, got


def test_split_rejects_what_is_not_five_sections():
    assert S._split_sections("") == {}
    assert S._split_sections("今天整体偏稳，慢一点反而顺。") == {}
    # 只认出两段 → 当作没认出来（宁可退回单段，也不要半张卡）
    assert S._split_sections("【爱情运势】甲段。\n【财富运势】乙段。") == {}
    # 正文里的"综合来看"不能被当成标签
    got = S._split_sections(FIVE.replace("今天适合", "综合来看，今天适合"))
    assert list(got) == ["综合", "爱情", "事业学业", "财富", "健康"]


def test_prompt_asks_for_the_five_labels():
    """代码默认 prompt 必须点明五个标签 —— 解析器按它写的。"""
    import brains
    for lb in ("【综合运势】", "【爱情运势】", "【事业学业】", "【财富运势】", "【健康运势】"):
        assert lb in brains.HOROSCOPE_PROMPT, lb


# ------------------------------------------------------------ 卡片结构

def _card(monkeypatch, text, sign="金牛", month=4, day=25):
    _fix(monkeypatch, text)
    return asyncio.run(S._sign_card("cid-1", "uid-1", sign, month, day, "客人"))


def test_card_keeps_scores_and_yiji_at_the_top(monkeypatch):
    lines = _card(monkeypatch, FIVE).split("\n")
    assert lines[0] == "🌹 今日星象 · ♉ 金牛座"
    assert any(ln.startswith("💗爱情") and "🌙心情" in ln and "💰财运" in ln for ln in lines), lines
    assert any(ln.startswith("📅 宜") and "｜" in ln for ln in lines), lines
    assert any(ln.startswith("🪐 土象 · 守护星金星") for ln in lines), lines
    assert any(ln.startswith("🍀 幸运：") for ln in lines), lines


def test_card_body_is_five_labeled_sections_in_order(monkeypatch):
    lines = _card(monkeypatch, FIVE).split("\n")
    body = [ln.split("】")[0] + "】" for ln in lines if ln.startswith("【")]
    assert body == ["【综合运势】", "【爱情运势】", "【事业学业】",
                    "【财富运势】", "【健康运势】"]
    assert len(lines) >= 15, lines          # 旧卡片 ≤10 行，五段式必然更长


def test_card_hides_unknown_birthday(monkeypatch):
    """只报座名（月日都是 0）时不能印出「00-00生」—— 看着像坏了。"""
    _fix(monkeypatch, FIVE)
    named = asyncio.run(S._sign_card("cid-1", "uid-1", "天蝎", 0, 0, "客人"))
    assert "00-00" not in named
    assert "月相：" in named and "生" not in named.split("月相：")[1].split("\n")[0]
    dated = asyncio.run(S._sign_card("cid-1", "uid-1", "天蝎", 11, 5, "客人"))
    assert "11-05生" in dated


def test_card_lucky_triplet_matches_calc(monkeypatch):
    card = _card(monkeypatch, FIVE)
    r = S._sign_calc("uid-1", "金牛", __import__("datetime").datetime.now().strftime("%Y-%m-%d"))
    assert "♉ 幸运颜色：" + r["luck"]["color"] in card
    assert "♉ 幸运数字：" + str(r["luck"]["num"]) in card
    assert "♉ 速配星座：" + r["luck"]["match"] + "座" in card
    assert 1 <= r["luck"]["num"] <= 9
    assert r["luck"]["match"] in ROSE_SIGN_MATCH["金牛"]


def test_every_sign_has_emoji_and_match_pool():
    for sign, el, ruler, _a, _b, _kw in __import__("personas").ROSE_SIGNS:
        assert ROSE_SIGN_EMOJI.get(sign), sign
        assert len(ROSE_SIGN_MATCH.get(sign) or ()) == 3, sign


# ------------------------------------------------------------ 退回旧口径

def test_card_falls_back_when_model_ignores_format(monkeypatch):
    card = _card(monkeypatch, "今天整体偏稳，\n慢一点反而顺。", sign="双鱼")
    lines = card.split("\n")
    assert "【综合运势】" not in card
    assert len(lines) == 11, lines                    # 7 行头 + 1 段正文 + 3 行尾巴
    assert "慢一点反而顺" in lines[7]
    assert "♓ 速配星座：" in card                     # 尾巴照旧要发
    assert "【" not in lines[7]


def test_card_falls_back_when_llm_is_empty(monkeypatch):
    card = _card(monkeypatch, "")
    assert "【" not in card
    assert "♉ 速配星座：" in card
    assert "宜" in card and "忌" in card


def test_card_falls_back_when_llm_raises(monkeypatch):
    monkeypatch.setattr(S, "_sign_recent", lambda uid, cid: "")

    def boom(*a, **kw):
        raise RuntimeError("网络错误")

    monkeypatch.setattr(S, "generate_horoscope", boom)
    card = asyncio.run(S._sign_card("cid-1", "uid-1", "金牛", 4, 25, "客人"))
    assert "【" not in card and "♉ 幸运数字：" in card


def test_unknown_sign_returns_empty(monkeypatch):
    assert S._sign_card is not None
    assert asyncio.run(S._sign_card("cid-1", "uid-1", "蛇夫", 0, 0, "客人")) == ""


# ------------------------------------------------------------ 确定性

def test_calc_deterministic_and_four_dims_untouched():
    a = S._sign_calc("uid-1", "金牛", "2026-10-05")
    b = S._sign_calc("uid-1", "金牛", "2026-10-05")
    assert a == b
    assert set(a["dims"]) == {"爱情", "事业", "财运", "心情"}      # 口径没变
    assert a["total"] == sum(a["dims"].values()) // 4             # 星象榜名次不受影响
    assert all(52 <= v < 96 for v in a["dims"].values())
    assert 52 <= a["health"] < 96
    c = S._sign_calc("uid-2", "金牛", "2026-10-05")
    assert (c["dims"], c["luck"]["num"]) != (a["dims"], a["luck"]["num"])


def test_horoscope_gets_health_and_total(monkeypatch):
    seen = {}

    def fake(sign, el, ruler, moon, dims, yi, ji, date_str,
             who="客人", hint="", health=0, total=0):
        seen.update({"health": health, "total": total, "sign": sign, "who": who})
        return FIVE

    monkeypatch.setattr(S, "_sign_recent", lambda uid, cid: "")
    monkeypatch.setattr(S, "generate_horoscope", fake)
    asyncio.run(S._sign_card("cid-1", "uid-1", "金牛", 4, 25, "客人"))
    assert seen["sign"] == "金牛" and seen["who"] == "客人"
    assert 52 <= seen["health"] < 96 and 52 <= seen["total"] < 96
