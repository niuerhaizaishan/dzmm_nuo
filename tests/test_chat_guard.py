"""P12-BQ：@ 闲聊的两个人味闸门 —— 回复冷却（D1）与收尾递话兜底（e）。

屋主 2026-09-30 批准。依据还是那两张量表：

| 特征 | 若瑾 | 真人 |
|---|---|---|
| 递话率（提问+派活收尾） | 67% | 6% |
| 陈述收尾（说完就停） | 13% | 85% |
| 同一个人连问，条条秒回 | 100% | 看心情 |

两个闸门**默认都关**（0 / 0 = 今天的行为），只在配了的群生效。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                        # noqa: E402
import personas as P                                              # noqa: E402
from runtime import at_chat                                       # noqa: E402
from runtime.text import _solicit_tail, _strip_solicit_tail        # noqa: E402

RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
SRC = (ROOT / "bot.py").read_text(encoding="utf-8")
SRC_ML = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")  # P3-19：主循环体
SRC_A = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")  # P3-3-4：@ 闲聊分支住这里


@pytest.fixture(autouse=True)
def _bind_group(monkeypatch):
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {RUOJIN: "ruojin"})


@pytest.fixture(autouse=True)
def _clean_cooldown():
    bot._CHAT_LAST_REPLY.clear()
    yield
    bot._CHAT_LAST_REPLY.clear()


# ---------------------------------------------------------------- e 递话判定

@pytest.mark.parametrize("text,hit", [
    ("行，这次像点样了。", ""),                       # 陈述收尾 = 说完就停
    ("跪下，手放背后。", ""),
    ("你问这个干嘛？", "问号收尾"),                    # 提问收尾 = 递话
    ("要不要我给你调杯酒", "要不要"),
    ("选一个，我等着。", "选一个"),
    ("说清楚再来。", "说清楚"),
    ("三十秒没人我就继续。", "倒计时"),
    ("想继续发 /继续，想结束发 /结束。", "指令菜单"),
])
def test_solicit_tail(text, hit):
    assert _solicit_tail(text) == hit


def test_solicit_tail_ignores_trailing_emoji_and_looks_at_last_line():
    assert _solicit_tail("记住了！！❤️") == ""
    assert _solicit_tail("第一句说清楚了。\n最后一句要不要？") == "问号收尾"


def test_strip_solicit_tail_removes_only_a_pure_tail():
    # 最后一句是纯招揽 → 删掉，留下前面的话
    assert _strip_solicit_tail("今天麻了就去睡。我等着你回话。") == "今天麻了就去睡。"
    assert _strip_solicit_tail("行，这次像点样了！！我等着你解释。") == "行，这次像点样了！！"
    # 只有一句时不许删空（宁可不兜底，也不把话删残）
    assert _strip_solicit_tail("要不要我给你调杯酒") == "要不要我给你调杯酒"
    assert _strip_solicit_tail("") == ""


# ---------------------------------------------------------------- D1 冷却

def test_cooldown_is_off_by_default():
    assert bot._chat_cooling(AUTH, "u-1") == 0, "默认必须每条都答（零回归）"


def test_cooldown_blocks_only_the_same_person(monkeypatch):
    # P3-3-4 起 `_chat_cooling` 住在 runtime/at_chat.py，读的是那边的 `_tune`
    monkeypatch.setattr(at_chat, "_tune",
                        lambda k, cid: 60 if k == "chat.reply_cooldown" else 0)
    assert bot._chat_cooling(RUOJIN, "u-1") == 0
    bot._chat_mark_replied(RUOJIN, "u-1")
    assert bot._chat_cooling(RUOJIN, "u-1") > 0, "同一个人冷却期内不该再答"
    assert bot._chat_cooling(RUOJIN, "u-2") == 0, "别人不受影响"
    assert bot._chat_cooling(AUTH, "u-1") == 0, "别的群不受影响"


def test_cooldown_expires(monkeypatch):
    monkeypatch.setattr(at_chat, "_tune",
                        lambda k, cid: 60 if k == "chat.reply_cooldown" else 0)
    bot._chat_mark_replied(RUOJIN, "u-1")
    bot._CHAT_LAST_REPLY[RUOJIN + "|u-1"] -= 61          # 手动往前拨
    assert bot._chat_cooling(RUOJIN, "u-1") == 0


# ---------------------------------------------------------------- 接线

def test_cooldown_is_wired_into_the_at_branch_only():
    # P3-19 起主循环体在 runtime/mainloop.py（bot 桥 B 前缀，子串兼容）
    i = SRC_ML.index("P12-BQ D1：同一个人连着 @ 她")
    seg = SRC_ML[i:i + 900]
    assert "_chat_cooling(cid" in seg and "at_msg = t" in seg, "冷却要挡在 at_msg 之前"
    assert "这条不接" in seg, "不许静默丢：要留一行日志"
    # P3-3-4 起"只有真发出去了才起冷却"那段随 @ 分支搬进了 runtime/at_chat.py
    i2 = SRC_A.index("P12-BQ D1：只有真发出去了才起冷却")
    assert "_chat_mark_replied(cid, at_sender)" in SRC_A[i2:i2 + 200]


def test_solicit_guard_is_wired_and_skips_cards():
    # P3-3-4 起 @ 闲聊分支在 runtime/at_chat.py
    i = SRC_A.index("P12-BQ e（屋主 2026-09-30 批准）")
    seg = SRC_A[i:i + 2400]
    assert '_tune("chat.solicit_guard", cid) and not _no_decor' in seg, \
        "只作用于 @ 闲聊，卡片（_no_decor）不碰"
    assert "_solicit_tail(reply)" in seg
    assert "_SOLICIT_REDO" in seg, "先重写一次"
    assert "_strip_solicit_tail(reply)" in seg, "重写还不行才删最后那句"
