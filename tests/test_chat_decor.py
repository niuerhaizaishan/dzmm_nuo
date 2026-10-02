"""P12-AH：`_ensure_decor` —— @ 对话的装饰补足（屋主："@ 若瑾说话时多加 ❤️ 和！！！"）。

为什么这条要用代码兜，而不是继续改提示词：实测（v5，6 条 @ 对话）
  · 提示词里写清"每条都带 ❤️、每条至少一处！"之后 → ❤️ **6/6 条**，
  · 但 **"！"只有 3/6 条** —— 她的人设是"低、稳、慢半拍"，模型收尾就是爱用句号。
所以补足做成确定性的（与 `_spice_emoji` 同一套路），**只补不叠**。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import personas as P                                           # noqa: E402
from runtime.text import _ensure_decor                         # noqa: E402

RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


@pytest.fixture(autouse=True)
def _bind_group(monkeypatch):
    """按群的人设映射平时由 bot 从 config 注入；测试里自己摆上（否则回落默认人设）。"""
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {RUOJIN: "ruojin"})


# ---------------------------------------------------------------- 补足规则

def test_appends_one_heart_when_missing():
    assert _ensure_decor("在。说吧。", "❤️") == "在。说吧！！❤️"


def test_does_not_stack_when_heart_already_there():
    src = "行，这次像点样了！！❤️❤️"
    assert _ensure_decor(src, "❤️") == src


def test_turns_the_last_full_stop_into_two_bangs():
    assert _ensure_decor("跪下。手放到背后。", "") == "跪下。手放到背后！！"


def test_does_not_touch_a_question_ending():
    """问号结尾是真的在问 —— 硬加感叹号会变成阴阳怪气。"""
    out = _ensure_decor("你到底想说什么？", "❤️")
    assert out.endswith("？❤️"), out
    assert "！！" not in out


def test_leaves_text_with_a_bang_alone():
    src = "别让我说第二遍！"
    assert _ensure_decor(src, "❤️") == src + "❤️"
    assert "！！" not in _ensure_decor(src, "")


def test_heart_goes_after_the_final_punctuation_not_after_a_newline():
    assert _ensure_decor("好。\n\n去吧。\n", "❤️") == "好。\n\n去吧！！❤️"


def test_empty_or_disabled_is_a_noop():
    assert _ensure_decor("", "❤️") == ""
    assert _ensure_decor("在。", "", bang=False) == "在。"
    # 没有句号也没有感叹号（只有问号）时不许硬塞
    assert _ensure_decor("在吗？", "") == "在吗？"


def test_long_reply_keeps_its_paragraphs():
    src = "第一段。\n\n第二段说完。"
    out = _ensure_decor(src, "❤️")
    assert out == "第一段。\n\n第二段说完！！❤️"


# ---------------------------------------------------------------- 只在若瑾那个群开

def test_only_ruojin_has_the_decor_switch_on():
    assert P.p("chat_decor", RUOJIN) == "❤️"
    assert P.p("chat_decor", AUTH) == "", "认证群（小小糯）不该被补符号"
    assert "chat_decor" in P.PHRASE_LABELS, "话术表要有中文标签"
    for mark in ("～", "呀", "嘛", "啦", "♡"):
        assert mark not in P.p("chat_decor", RUOJIN)


def test_bot_wires_it_into_the_at_branch_only():
    """只接在 @ 问答那条路（屋主点名的就是那条），且开关为空时等于没加。

    P12-AW 起外面多了一层 `if not _no_decor:` —— 运势卡这类**卡片回复**要跳过它
    （`bang` 半段会把卡上最后一个"。"换成"！！"）。

    P12-BD 起这一层里面还多了两件事（都只在这条路上）：
      · `_humanize_chat`：收空行 / 限行数（排版才是"一眼机器人"的主因）；
      · 概率：`chat.decor_prob` / `chat.bang_prob` —— 默认 1.0 = 今天的行为，
        上班群把它调低，"每条都带 ❤️ 和 ！！"就变成偶尔。
    `p("chat_decor", cid)` 仍旧是那条开关（空 = 不补）。
    """
    # P3-3-4 起 @ 闲聊分支（含装饰段）在 runtime/at_chat.py
    src = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    i_anchor = src.index("P12-AH：@ 对话补足装饰")
    i_guard = src.index("if not _no_decor:")
    assert i_anchor < i_guard, "装饰那段要留在 @ 分支里"
    seg = src[i_guard:i_guard + 900]
    assert "_ensure_decor(reply, _heart, bang=_bang)" in seg, \
        "跳过开关里面就是装饰调用（P12-BD 起改成按概率取的两个实参）"
    assert '_heart = p("chat_decor", cid)' in seg, "开关还是那条话术"
    assert "chat.decor_prob" in seg and "chat.bang_prob" in seg, \
        "概率必须从可调参数读（默认 1.0 = 零回归）"
    assert "_humanize_chat(reply)" in seg and "chat.humanize" in seg, \
        "排版收口也要接在这条路上"
    # 玩法正文那条路的装饰走 spice_emoji，不受影响
    # （P3-3-2 起分派链在 runtime/dispatch.py，去那边数）
    assert "_spice_emoji(reply, p(\"spice_emoji\", cid))" in (
        ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")
