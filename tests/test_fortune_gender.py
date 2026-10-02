"""P12-BA：运势的称呼**按性别**（不能逮谁叫谁姐姐）。

屋主 2026-09-30："**还有，运势你也不能逮谁叫谁姐姐呀，要看性别的呀**"

## 根因（和 P12-AL 同一类毛病）

`FORTUNE_PROMPT` 里其实早写了"女生叫 X 姐姐、男生叫 X 哥哥"，但：

1. **输入里从来没给性别** —— `fortune()` 拼的 `hint` 只有「用户主名 / 命盘 / 四维 / 最近发言」，
   模型手里没有事实可用；
2. 提示词第一句是"给**姐姐**的今日运势"，示例**只有女生那一条** ⇒ 语气和示例把模型
   训成了一律叫姐姐（实测茶水间把男生（苏白、G_百戏…）也叫"姐姐"）。

## 修法

- `fortune()` 调 `_gender_label_of(uid, cid)`（档案优先、缺了实时查平台、查不到=未知），
  把 `性别：X` 当**输入字段**给模型；
- `FORTUNE_PROMPT` 改成"先看性别再决定怎么叫"，并把**男生、未知**两条示例补上
  （光靠规则不管用，P12-AL 的教训）；
- 同一个毛病在「过去 7 天运势趋势」也有（`TREND_PROMPT` 写死"叫对方姐姐"）⇒ 一起改：
  `week_trend` 把性别传进 `generate_trend`。
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                   # noqa: E402
from runtime import fortune as FT                               # noqa: E402

FORTUNE_MD = ROOT / "prompts" / "FORTUNE_PROMPT.md"
TREND_MD = ROOT / "prompts" / "TREND_PROMPT.md"


# ---------------------------------------------------------------- ① 提示词

def test_fortune_prompt_is_gender_first():
    t = FORTUNE_MD.read_text(encoding="utf-8")
    assert "给群友的今日运势" in t, "第一句不能是'给姐姐的'（会把它当默认称呼）"
    assert "给姐姐的" not in t
    assert "先看输入里的「性别」再决定怎么叫" in t
    assert "绝不许把女生以外的人叫" in t


def test_fortune_prompt_has_male_and_unknown_examples():
    """规则不够 —— 示例里必须有男生的，否则模型照旧一律"姐姐"（P12-AL 的教训）。"""
    t = FORTUNE_MD.read_text(encoding="utf-8")
    ex = t.split("示例：")[-1]
    assert "性别：男生" in ex, "要有男生的示例"
    assert "性别：未知" in ex, "未知性别也要有示例（只叫名字）"
    male = [ln for ln in ex.split("\n") if ln.startswith("你：") and "哥" in ln]
    assert male, "男生那条回答要用「X哥」"
    assert "姐姐" not in male[0], "男生示例里不能出现「姐姐」"
    unknown = [ln for ln in ex.split("\n")
               if ln.startswith("你：") and "苏白" in ln]
    assert unknown and "姐姐" not in unknown[0] and "哥" not in unknown[0], \
        "未知性别只叫名字"


def test_trend_prompt_is_gender_first_too():
    t = TREND_MD.read_text(encoding="utf-8")
    assert '叫对方"姐姐"' not in t, "趋势点评也不能写死姐姐"
    assert "称呼按输入里的「性别」" in t
    assert "未知**只叫名字**" in t or "只叫名字" in t


def test_prompt_files_match_code_defaults():
    assert FORTUNE_MD.read_text(encoding="utf-8") == brains.FORTUNE_PROMPT
    assert TREND_MD.read_text(encoding="utf-8") == brains.TREND_PROMPT


# ---------------------------------------------------------------- ② 输入里要带性别

def _stub(monkeypatch, calls):
    def _fake(main_name, score, date_str, hint=""):
        calls.append({"name": main_name, "hint": hint})
        return ""
    monkeypatch.setattr(FT, "generate_fortune", _fake)
    monkeypatch.setattr(FT, "get_full_name", lambda uid: "苏白")
    monkeypatch.setattr(FT, "get_profile", lambda uid: {})
    monkeypatch.setattr(FT, "_record_fortune", lambda *a, **kw: None)
    monkeypatch.setattr(FT, "_pp", lambda *a, **kw: "")


def test_fortune_hint_carries_the_gender(monkeypatch):
    calls = []
    _stub(monkeypatch, calls)
    monkeypatch.setattr(FT, "_gender_label_of", lambda uid, cid=None: "男生")
    FT.fortune("u-1", "c-1")
    assert calls, "模型必须被调到"
    assert "性别：男生" in calls[0]["hint"]
    assert "用户主名：苏白" in calls[0]["hint"]


def test_unknown_gender_is_passed_as_unknown_not_guessed(monkeypatch):
    calls = []
    _stub(monkeypatch, calls)
    monkeypatch.setattr(FT, "_gender_label_of", lambda uid, cid=None: "未知")
    FT.fortune("u-2", "c-1")
    assert "性别：未知" in calls[0]["hint"]


def test_gender_lookup_failure_does_not_break_the_card(monkeypatch):
    """查性别要让平台发请求，失败（网络/风控）也必须能出卡 —— 当成"未知"。"""
    def boom(uid, cid=None):
        raise RuntimeError("http 502")

    monkeypatch.setattr(FT, "_gender_of", boom)
    monkeypatch.setattr(FT, "_CARD_WARNED", set())
    logged = []
    monkeypatch.setattr("runtime.obs.log", lambda m: logged.append(m), raising=False)
    assert FT._gender_label_of("u-3") == "未知"
    assert any("识别性别" in m for m in logged), logged


def test_gender_label_of_never_returns_empty(monkeypatch):
    """平台上 `gender` 可能是空串/None ⇒ 必须归一成"未知"。"""
    monkeypatch.setattr(FT, "_gender_of", lambda uid, cid=None: "")
    assert FT._gender_label_of("u-4") == "未知"
    monkeypatch.setattr(FT, "_gender_of", lambda uid, cid=None: "女生")
    assert FT._gender_label_of("u-4") == "女生"


# ---------------------------------------------------------------- ③ 周趋势也要带

def test_trend_passes_gender_into_the_model(monkeypatch):
    from runtime import trend as TR
    seen = {}

    def fake_gen(data_txt, who, gender="未知", api_key=None):
        seen["gender"] = gender
        seen["who"] = who
        return "一段回顾"

    monkeypatch.setattr(TR, "generate_trend", fake_gen)
    monkeypatch.setattr(TR, "_gender_label_of", lambda uid, cid=None: "男生",
                        raising=False)
    monkeypatch.setattr(TR, "get_name", lambda uid: "临屿")
    monkeypatch.setattr(TR, "_tune_scope", lambda key, cid: True)
    monkeypatch.setattr(TR, "_trend_days", lambda cid, uid, n: [
        ("2026-09-29", 51), ("2026-09-30", 61)])
    monkeypatch.setattr(TR, "_trend_stats", lambda pairs: {
        "n": 2, "total": 7, "avg": 56.0, "hi": 61, "lo": 51,
        "hi_d": "2026-09-30", "lo_d": "2026-09-29"})
    monkeypatch.setattr(TR, "_trend_chong", lambda uid, dates: [])
    monkeypatch.setattr(TR, "_pp", lambda *a, **kw: "")
    monkeypatch.setattr(TR, "log", lambda m: None)
    out = asyncio.run(TR.week_trend("c-1", "u-9", 2))
    assert seen.get("gender") == "男生", "趋势点评必须拿到性别"
    assert "临屿" in out


def test_brains_generate_trend_puts_gender_in_the_prompt(monkeypatch):
    captured = {}

    def fake_call(prompt, content, api_key=None, **kw):
        captured["content"] = content
        return "ok"

    monkeypatch.setattr(brains, "_call_llm", fake_call)
    brains.generate_trend("数据", "临屿", gender="男生")
    assert "性别：男生" in captured["content"]


def test_brains_generate_trend_defaults_to_unknown(monkeypatch):
    captured = {}
    monkeypatch.setattr(brains, "_call_llm",
                        lambda p, c, api_key=None, **kw: captured.setdefault("c", c))
    brains.generate_trend("数据", "某人")
    assert "性别：未知" in captured["c"]
