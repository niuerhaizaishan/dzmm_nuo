"""P12-X：工作群里"没在局里就不理" + 发骚/羞辱正文撒表情。

屋主 2026-09-27 的两点：
  1. "工作群针对 /talk、/ask 等指令，没在局里就不理" —— 这些是**别的 bot 的指令语法**，
     原来会被当成调教命令，回一句「这个我不会…」；现在没在局里就静默。
     （`/命令 xxx` 是我们自己的参与语法，仍旧给"没有进行中的调教"提示。）
  2. "工作群和妹子群，发骚、羞辱的内容中增加一些 💗 之类的表情" ——
     做成话术 `spice_emoji`（空格分隔，空 = 不撒），代码只负责"在句末标点后均匀撒几个"。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import personas as P                                          # noqa: E402
from runtime.text import _spice_emoji                          # noqa: E402

RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"


@pytest.fixture(autouse=True)
def _bind_groups(monkeypatch):
    """线上是 config.json 的 groups[].persona 在绑人设；这里等价地绑一下。"""
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {RUOJIN: "ruojin"})


# ---------------------------------------------------------------- 1. 表情

def test_spice_inserts_after_sentence_end_only():
    t = "第一句。第二句！第三句？第四句～第五句。第六句。第七句。第八句。"
    out = _spice_emoji(t, "💗 💦 🥵")
    assert out != t and "💗" in out
    # 每个表情都必须紧跟在句末标点后面
    for i, ch in enumerate(out):
        if ch in "💗💦🥵":
            assert out[i - 1] in "。！？～…", out
    # 不能把句子中间切开：去掉表情应与原文一致
    assert out.replace("💗", "").replace("💦", "").replace("🥵", "") == t


def test_spice_is_spread_out_and_capped():
    t = "。".join("第{}句".format(i) for i in range(40)) + "。"
    out = _spice_emoji(t, "💗 💦 🥵", every=2, max_n=6)
    n = sum(out.count(e) for e in ("💗", "💦", "🥵"))
    assert n == 6, "cap = max_n"
    # 均匀取样：第一个出现在前 1/6，最后一个出现在后 1/6
    first = min(out.index(e) for e in "💗💦🥵" if e in out)
    last = max(out.rindex(e) for e in "💗💦🥵" if e in out)
    assert first < len(out) * 0.25 and last > len(out) * 0.6, (first, last, len(out))


def test_spice_prefers_line_ends():
    """优先插在行尾（段末）—— 段中「？💗下一句」很怪。"""
    t = "第一句。第二句。\n第三句。第四句。\n第五句。第六句。\n第七句。第八句。"
    out = _spice_emoji(t, "💗", every=2, max_n=3)
    for i, ch in enumerate(out):
        if ch == "💗":
            assert i + 1 >= len(out) or out[i + 1] == "\n", "表情必须在行尾"
    assert out.count("💗") == 3


def test_spice_falls_back_to_mid_line_when_no_breaks():
    """没有换行时退回普通句末标点（不能因为"没有行尾"就一个都不撒）。"""
    out = _spice_emoji("甲。乙。丙。丁。", "💗", every=1, max_n=2)
    assert out.count("💗") == 2


def test_spice_is_off_by_default_and_without_punctuation():
    assert _spice_emoji("第一句。第二句。", "") == "第一句。第二句。"
    assert _spice_emoji("没有句末标点的长句", "💗") == "没有句末标点的长句"
    assert _spice_emoji("", "💗") == ""


def test_spice_set_is_per_persona_phrase():
    """表情清单是话术（页面能改、热加载）：妹子群/工作群开着，玫瑰房关着。"""
    assert "💗" in P.p("spice_emoji", AUTH)
    assert "💗" in P.p("spice_emoji", RUOJIN)
    assert P.p("spice_emoji", ROSE) == "", "玫瑰房保持管家口吻，不撒表情"
    assert "spice_emoji" in P.PHRASE_LABELS


def test_scene_and_insult_paths_apply_the_spice():
    """两条链路都要真的调用它（源码级，避免只加话术没接线）。

    P3-3-2 起这些调用点随分派链/续写家族在 runtime/dispatch.py。
    """
    src = (ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")
    assert src.count("_spice_emoji(") >= 4, "羞辱 + 两种续写 + 重写 都要撒"
    assert "_spice_emoji," in src, "得先 import"
    fasao = (ROOT / "runtime" / "fasao.py").read_text(encoding="utf-8")
    assert "_spice_emoji(text" in fasao, "发骚正文（scene）也要撒"
    # 标题行（🎲 combo）不受影响：撒的是正文 text，拼接在后
    assert 'return "🎲 " + combo + "\\n" + text' in fasao


# ---------------------------------------------------------------- 2. 没在局里就不理

def test_ruojin_silences_unknown_slash_commands_when_idle():
    """若瑾那份把 `dj_idle_unknown` 写成空串 = 明确要求静默。"""
    assert P.phrase_is_blank("dj_idle_unknown", RUOJIN) is True
    assert P.p("dj_idle_unknown", RUOJIN) == ""


def test_auth_group_keeps_the_old_unknown_cmd_reply():
    """认证群没写过这个 key ⇒ 不静默，仍旧回 unknown_cmd（行为一个字不变）。"""
    assert P.phrase_is_blank("dj_idle_unknown", AUTH) is False
    assert P.p("unknown_cmd", AUTH) != ""


def test_blank_phrase_helper_distinguishes_missing_from_blank():
    """空串与"没写过"是两回事 —— 这是这件事的关键。

    注意 `None` 也算"明确不写"（默认人设的 `insult_prefix_who: None` 就是这层意思），
    所以判断的是"这一层有没有**明确表态**"，而不是"值是不是字符串"。
    """
    assert P.phrase_is_blank("不存在的话术key", AUTH) is False, "没写过 → 回落默认"
    assert P.phrase_is_blank("insult_prefix_who", AUTH) is True, "None = 明确不启用"
    P.set_content_overrides({"nuo": {"phrases": {"dj_idle_unknown": "   "}}})
    try:
        assert P.phrase_is_blank("dj_idle_unknown", AUTH) is True, "空白也算空"
    finally:
        P.set_content_overrides({})


def test_bot_wires_the_idle_branch():
    """P3-3-2 起调教 idle 分支随分派链在 runtime/dispatch.py。"""
    src = (ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")
    i = src.index('elif (dj_cmd := is_dj_command_cmd(cmd))')
    seg = src[i:]
    seg = seg[:seg.index("elif is_diaojiao_cmd(cmd)")]
    assert 'phrase_is_blank("dj_idle_unknown", cid)' in seg
    assert 'reply = None' in seg, "空话术 ⇒ 静默（reply=None 才是不发消息）"
    assert 'cmd.startswith("/命令")' in seg, "/命令 是我们自己的语法，仍旧给提示"
