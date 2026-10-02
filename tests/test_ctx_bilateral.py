"""P12-BE：双边上下文 —— 她答你的时候，只读"你和她"的记录。

屋主 2026-09-30（追问）：
> "我如果我回复她，她会读取之前很多人的记录，一一批判回复。能设置成那种，
>   我回复她，她只会读取我之前的和她的记录，来进行回复嘛"

实测（`tools/probe_ctx_scope.py`，上班群）喂进去的 16 条里：**5 个不同说话人**、
她自己只占 3 条、另外 13 条是别人和**别的 bot 的 /luck 运势卡刷屏** ⇒
她一句"你是机器人嘛"回过去，会顺手把别人也点评一遍。

五项一起做（缺一条都还会串）：
  1. `reply.ctx_mode`（0 全群 / 1 双边 / 2 仅屋主双边），**默认 0 = 今天的行为**；
  2. `_remember_turn` 多记 **uid**（原先只记昵称 —— 同名/改名就认错人）；
  3. **别的 bot 的消息不进上下文**（`isBot` 为准，昵称带 bot 兜底；自己除外）；
  4. 双边模式下**记忆也按人过滤**（只留提到当前这个人的条目）；
  5. 收尾块补一行"**只跟他说**"（提示词 + 上下文双管）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                        # noqa: E402
import brains                                                     # noqa: E402
from runtime import at_chat                                       # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
ME = "u-me"
OTHER = "u-other"
TOOLBEAR = "u-toolbear"
SRC = (ROOT / "bot.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _turns():
    bot._TURNS.clear()
    bot._TURN_SEEN.clear()
    bot._GROUP_BOTS.clear()
    yield
    bot._TURNS.clear()
    bot._TURN_SEEN.clear()
    bot._GROUP_BOTS.clear()


def _seed():
    bot._TURNS[CID] = [
        {"who": "我", "text": "麻了就去睡", "mid": "m0", "uid": ME},
        {"who": "工具熊 Jr.", "text": "✧ 运势 ✧ 宜摸鱼", "mid": "m1", "uid": TOOLBEAR},
        {"who": "晴天", "text": "这班上得我人都麻了", "mid": "m2", "uid": OTHER},
        {"who": "临屿", "text": "在忙什么", "mid": "m3", "uid": "u-linyu"},
    ]


# ---------------------------------------------------------------- ③ 过滤别的 bot

def test_other_bots_are_kept_out_of_the_context(monkeypatch):
    _seed()
    monkeypatch.setattr(bot, "_other_bot_uids", lambda cid, ttl=600.0: {TOOLBEAR})
    got = [c["text"] for c in bot._recent_turns(CID, 20)]
    assert not any("运势" in t for t in got), "别的 bot 的刷屏不许当群友发言"
    assert len(got) == 3


def test_our_own_messages_are_never_dropped_as_bots(monkeypatch):
    """⚠️ 我们自己也是 `isBot`：别把"我"自己过滤掉（那会丢掉对话连续性）。"""
    _seed()
    monkeypatch.setattr(bot, "_other_bot_uids", lambda cid, ttl=600.0: {TOOLBEAR})
    monkeypatch.setattr(bot, "is_self", lambda uid: uid == ME)
    got = [c["text"] for c in bot._recent_turns(CID, 20)]
    assert "麻了就去睡" in got


# ---------------------------------------------------------------- ② uid

def test_turns_remember_uid():
    bot._remember_turn(CID, "晴天", "在吗", "m9", OTHER)
    assert bot._TURNS[CID][-1]["uid"] == OTHER
    # 不传 uid 也要能记（老调用点/自己发的话）
    bot._remember_turn(CID, "我", "在", "m10")
    assert bot._TURNS[CID][-1]["uid"] == ""


# ---------------------------------------------------------------- ① 双边

def test_bilateral_keeps_only_me_and_him(monkeypatch):
    _seed()
    monkeypatch.setattr(bot, "_other_bot_uids", lambda cid, ttl=600.0: {TOOLBEAR})
    monkeypatch.setattr(bot, "is_self", lambda uid: uid == ME)
    got = [c["text"] for c in bot._recent_turns(CID, 20, only_uid="u-linyu")]
    assert got == ["麻了就去睡", "在忙什么"], got


def test_ctx_mode_defaults_to_whole_group():
    assert bot._ctx_mode(CID) == 0
    assert bot._ctx_only_uid(CID, OTHER) == "", "默认不筛（今天的行为）"


def test_ctx_mode_1_and_2(monkeypatch):
    # P3-3-4 起 `_ctx_only_uid` 住在 runtime/at_chat.py，读的是那边的 `_tune` / `OWNER_UID`
    monkeypatch.setattr(at_chat, "_tune",
                        lambda k, cid: (1 if k == "reply.ctx_mode" else 0))
    assert bot._ctx_only_uid(CID, OTHER) == OTHER, "mode=1：谁都双边"
    monkeypatch.setattr(at_chat, "_tune",
                        lambda k, cid: (2 if k == "reply.ctx_mode" else 0))
    monkeypatch.setattr(at_chat, "OWNER_UID", "u-boss")
    assert bot._ctx_only_uid(CID, "u-boss") == "u-boss", "mode=2：屋主双边"
    assert bot._ctx_only_uid(CID, OTHER) == "", "mode=2：其他人照旧全群"


# ---------------------------------------------------------------- ⑤ 提示词

def test_frame_tells_her_to_talk_to_him_only():
    f = brains.AT_FRAME_PROMPT
    assert "【只跟他说】" in f
    assert "别点评群里其他人" in f


# ---------------------------------------------------------------- 接线

def test_wiring_uses_the_mode_and_filters_memory():
    # P3-3-4 起 @ 闲聊分支在 runtime/at_chat.py
    src = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    i = src.index("P12-BE：`reply.ctx_mode` 决定")
    seg = src[i:i + 800]
    assert "_ctx_only_uid(cid, at_sender)" in seg
    assert "only_uid=_only" in seg
    i2 = src.index("P12-BE d：双边模式下**记忆也按人过滤**")
    seg2 = src[i2:i2 + 900]
    assert "_only and mem" in seg2 and "_keep" in seg2
