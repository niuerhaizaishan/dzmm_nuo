"""P12-M：`出站/入站"放回队列"`的守卫测试（2026-09-27 玫瑰房刷屏事故）。

事故现场（bot.log）：

    11:38:39 👋 [4a56be71] @消息: @薇丝-bot说话触发比较好吧！…
    11:38:33 ♻️ [4a56be71] 同批还有 2 条没轮到，已放回队列
    …（52 次放回，机器人每 1~2 秒回一条，靠重启清空内存队列才停）

原因：这条 @ 消息**同时**命中 @ 分支与房间分支（`_is_room_trigger` 把"被点名"也算
房间触发）。@ 分支赢了之后，代码把房间那条**也**放回队列 —— 而它就是刚刚这条，
于是下一轮捞回来，如此往复。

修法两条，都在 `bot._pick_leftover` 里：
1. **被处理的那条绝不放回**（按对象身份判断，不是按内容）；
2. 兜底：同一条最多放回 `REQUEUE_MAX` 次。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                    # noqa: E402


def _m(text, mid):
    """一条最小形状的消息（真实形状里就这几个字段被用到）."""
    return {"message_id": mid, "sent_by": "u1", "content": {"text": text, "type": "text"}}


def test_same_message_hit_by_both_branches_is_not_requeued():
    """核心回归：@ 与房间是**同一条**消息 → 一条都不放回（否则无限循环）."""
    m = _m("@薇丝 说话触发比较好", "mid-1")
    out = bot._pick_leftover("cid", "", None, "@薇丝 说话触发比较好", m, m)
    assert out == []


def test_consumed_command_is_not_requeued():
    m = _m("/写诗", "mid-1")
    assert bot._pick_leftover("cid", "/写诗", m, "", None, None) == []


def test_other_batch_messages_are_requeued_once():
    cmd = _m("/写诗", "mid-1")
    at = _m("@薇丝 你好", "mid-2")
    room = _m("想喝一杯", "mid-3")
    out = bot._pick_leftover("cid", "/写诗", cmd, "@薇丝 你好", at, room)
    assert [x["message_id"] for x in out] == ["mid-2", "mid-3"]
    assert all(x["_requeue_n"] == 1 for x in out)


def test_at_message_wins_room_message_waits():
    at = _m("@薇丝 你是谁", "mid-1")
    room = _m("薇丝你是谁", "mid-2")          # 另一条、也是房间触发
    out = bot._pick_leftover("cid", "", None, "@薇丝 你是谁", at, room)
    assert [x["message_id"] for x in out] == ["mid-2"]


def test_second_command_in_batch_keeps_waiting():
    """同批第二、三条命令在扫描时就被收进 `already`，不能被重复追加."""
    first, second = _m("/写诗", "mid-1"), _m("/运势", "mid-2")
    out = bot._pick_leftover("cid", "/写诗", first, "", None, None, already=[second])
    assert [x["message_id"] for x in out] == ["mid-2"]


def test_requeue_cap_stops_any_future_loop():
    """兜底：放回到上限的那条直接丢掉（宁可漏一条，也不刷屏）."""
    m = _m("反复被放回的一条", "mid-1")
    m["_requeue_n"] = bot.REQUEUE_MAX
    other = _m("另一条", "mid-2")
    out = bot._pick_leftover("cid", "", None, "", None, m, already=[other])
    assert [x["message_id"] for x in out] == ["mid-2"], out


def test_requeue_counter_increments():
    at = _m("@薇丝 你好", "mid-0")            # @ 那条被处理，房间那条每轮多等一次
    m = _m("第二条", "mid-1")
    out = bot._pick_leftover("cid", "", None, "@薇丝 你好", at, m)
    assert out[0]["_requeue_n"] == 1
    out2 = bot._pick_leftover("cid", "", None, "@薇丝 你好", at, out[0])
    assert out2[0]["_requeue_n"] == 2


def test_empty_batch_is_fine():
    assert bot._pick_leftover("cid", "", None, "", None, None) == []
