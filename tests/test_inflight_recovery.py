"""P12-BC：被容器重建杀掉的那条消息，重启后要能补回来。

## 现场（屋主 2026-09-30 10:33："**怎么 ROSE 房又没反应了**"）

```
玫瑰房最后一条   02:07:43(UTC) 亦书: 摩羯座      ← 没人回答
薇丝最后一条     01:05:48(UTC) 🌹 今日星象 · ♏ 天蝎座
玫瑰房 last_id   d4360903 = 上面那条"摩羯座"
```

`02:07:07(UTC)` 我正在执行部署（`docker compose up -d` 重建容器）——
消息就是在这个窗口里到的。**`last_id` 等于那条没被回答的消息**，说明
`last_id` 被推进了、回复却从未发出。

## 根因

三个分支都是"**先推进 `last_id`，再生成回复**"（`bot.py` 里的三处），
异常时靠 `except` 回滚（`_rollback_last_id`）。可容器重建是 **SIGKILL**：
`except` 来不及跑 ⇒ `last_id` 停在最新那条 ⇒ 重启后它"看起来已经处理过了"
⇒ **永远不会被再读** —— 一条回复就这么静默消失（"又没反应了"）。

## 对策

1. 推进 `last_id` 的同时落一个 `inflight` 标记（`{mid, at}`，存 group state）；
2. 每轮正常跑完（`last_id` 推到最新那一刻）清掉标记；
3. 启动时 `_recover_inflight()`：标记还在（且 < 1 小时）就**回群里读**：
   - 那条之后有我们发的话 ⇒ 回过了，只清标记；
   - 没有 ⇒ 把 `last_id` 退回它前面一条，交回主循环重放；
   - 已经滚出最近 50 条窗口 ⇒ 补不回来，**留一行警告**（不许静默）。

判据仍是老规矩（P12-AN）：**"发出去了"只能由群里读回证明**。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                       # noqa: E402
import state_store                                               # noqa: E402

CID = "c-rose"
# P3-19 起主循环体在 runtime/mainloop.py（bot 桥 B 前缀，源码钉点改查这里）
BOT_PY = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 标记写在哪

def test_all_three_dispatch_sites_mark_inflight():
    """cmd / @ / 房间消息 三条路都要落标记 —— 玫瑰房出事的是"房间消息"那条。

    标记要指**这一轮真正在处理的那条**（`_cmd_msg` / `_at_msg_obj` / `_room_obj`），
    而不是批次尾巴 `msgs[-1]` —— 后者可能比被处理的那条更新，回放时就会多回一条。
    P3-3-2 起 cmd 那条路（`_cmd_msg`）在 runtime/dispatch.py；P3-3-3 起房间那条路
    （`_room_obj`）在 runtime/room.py；P3-3-4 起 @ 那条路（`_at_msg_obj`）在
    runtime/at_chat.py —— 三个 runtime 文件各恰一处、合计三处。
    """
    src_d = (ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")
    src_r = (ROOT / "runtime" / "room.py").read_text(encoding="utf-8")
    src_a = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    assert '_mark_inflight(cid, (_cmd_msg or msgs[-1]).get("message_id")' in src_d, "_cmd_msg"
    assert '_mark_inflight(cid, (_at_msg_obj or msgs[-1]).get("message_id")' in src_a, "_at_msg_obj"
    assert '_mark_inflight(cid, (_room_obj or msgs[-1]).get("message_id")' in src_r, "_room_obj"
    assert BOT_PY.count("_mark_inflight(cid, (") == 0
    assert src_d.count("_mark_inflight(cid, (") == 1
    assert src_a.count("_mark_inflight(cid, (") == 1
    assert src_r.count("_mark_inflight(cid, (") == 1
    i = src_a.index("_mark_inflight(cid, (")
    seg = src_a[max(0, i - 300):i]
    assert "group_state[\"last_id\"] = msgs[-1][\"message_id\"]" in seg, \
        "标记必须紧跟在先推进 last_id 的那两行旁边"


def test_round_end_clears_the_marker():
    i = BOT_PY.index("_clear_inflight(cid)          # P12-BC")
    seg = BOT_PY[max(0, i - 260):i]
    assert 'group_state["last_id"] = msgs[-1]["message_id"]' in seg, \
        "必须在每轮跑完、推进 last_id 的那一刻清标记"


def test_startup_calls_recovery():
    i = BOT_PY.index("_recover_inflight(state, B.GROUPS)")
    seg = BOT_PY[max(0, i - 400):i]
    assert "state = B.load_state()" in seg, "要在主循环起来之前、拿到 state 之后跑"
    assert "_recover_inflight" in BOT_PY


# ---------------------------------------------------------------- ② 恢复逻辑

def _patch_state(monkeypatch, state):
    monkeypatch.setattr(bot, "load_state", lambda: state)
    monkeypatch.setattr(bot, "_save_group", lambda cid, gs: None)
    monkeypatch.setattr(bot.state_store, "group_get",
                        lambda cid, key, default=None: (state.get(cid) or {}).get(key, default))
    monkeypatch.setattr(bot.state_store, "group_set",
                        lambda cid, key, val: (state.setdefault(cid, {}).__setitem__(key, val)))


def _msg(mid, self_msg=False, text="摩羯座"):
    return {"message_id": mid, "sent_by": "u-bot" if self_msg else "u-yishu",
            "sent_at": "2026-09-30T02:07:43.000Z",
            "content": {"type": "text", "text": text}}


def _patch_msgs(monkeypatch, msgs):
    monkeypatch.setattr(bot, "get_messages", lambda cid, n=20: msgs)
    monkeypatch.setattr(bot, "is_self", lambda uid: uid == "u-bot")


def test_recovery_replays_when_nobody_answered(monkeypatch):
    """玫瑰房那个现场：标记在、那条之后没有我们的话 ⇒ 退回一条让它重放。"""
    state = {CID: {"last_id": "m-3",
                   bot.INFLIGHT_KEY: {"mid": "m-2", "at": time.time()}}}
    _patch_state(monkeypatch, state)
    _patch_msgs(monkeypatch, [_msg("m-1"), _msg("m-2"), _msg("m-3")])
    assert bot._recover_inflight(state, [CID]) == 1
    assert state[CID]["last_id"] == "m-1", "要退到出事那条的前一条"
    assert bot.INFLIGHT_KEY not in state[CID], "标记要清掉"


def test_recovery_does_not_duplicate_when_reply_already_went_out(monkeypatch):
    """我们其实回过了（那条之后有我们的话）⇒ 只清标记，绝不重放。"""
    state = {CID: {"last_id": "m-3",
                   bot.INFLIGHT_KEY: {"mid": "m-2", "at": time.time()}}}
    _patch_state(monkeypatch, state)
    _patch_msgs(monkeypatch, [_msg("m-2"), _msg("m-self", self_msg=True)])
    assert bot._recover_inflight(state, [CID]) == 0
    assert state[CID]["last_id"] == "m-3", "last_id 不许动"
    assert bot.INFLIGHT_KEY not in state[CID]


def test_recovery_warns_when_the_message_slid_out_of_window(monkeypatch):
    """已经滚出最近 50 条 ⇒ 补不回来，但**必须留痕**（不许静默）。"""
    state = {CID: {"last_id": "m-9",
                   bot.INFLIGHT_KEY: {"mid": "m-gone", "at": time.time()}}}
    _patch_state(monkeypatch, state)
    _patch_msgs(monkeypatch, [_msg("m-8"), _msg("m-9")])
    logged = []
    monkeypatch.setattr(bot, "log", lambda m: logged.append(m))
    assert bot._recover_inflight(state, [CID]) == 0
    assert any("滚出" in m for m in logged), logged
    assert bot.INFLIGHT_KEY not in state[CID]


def test_recovery_ignores_a_stale_marker(monkeypatch):
    """1 小时前的标记不重放（重启不该翻旧账），但也要留痕 + 清掉。"""
    state = {CID: {"last_id": "m-3",
                   bot.INFLIGHT_KEY: {"mid": "m-2", "at": time.time() - 7200}}}
    _patch_state(monkeypatch, state)
    _patch_msgs(monkeypatch, [_msg("m-1"), _msg("m-2"), _msg("m-3")])
    logged = []
    monkeypatch.setattr(bot, "log", lambda m: logged.append(m))
    assert bot._recover_inflight(state, [CID]) == 0
    assert any("太旧" in m for m in logged), logged
    assert state[CID]["last_id"] == "m-3"


def test_recovery_is_a_noop_without_a_marker(monkeypatch):
    state = {CID: {"last_id": "m-3"}}
    _patch_state(monkeypatch, state)
    _patch_msgs(monkeypatch, [_msg("m-1"), _msg("m-2"), _msg("m-3")])
    assert bot._recover_inflight(state, [CID]) == 0
    assert state[CID]["last_id"] == "m-3"


def test_recovery_survives_a_broken_room_read(monkeypatch):
    """读群失败不能让启动挂掉（但要有日志）。"""
    state = {CID: {"last_id": "m-3",
                   bot.INFLIGHT_KEY: {"mid": "m-2", "at": time.time()}}}
    _patch_state(monkeypatch, state)

    def boom(cid, n=20):
        raise RuntimeError("http 502")

    monkeypatch.setattr(bot, "get_messages", boom)
    logged = []
    monkeypatch.setattr(bot, "log", lambda m: logged.append(m))
    assert bot._recover_inflight(state, [CID]) == 0
    assert any("恢复失败" in m for m in logged), logged


def test_mark_inflight_stores_mid_and_time(monkeypatch):
    state = {}
    _patch_state(monkeypatch, state)
    bot._mark_inflight(CID, "m-7")
    mark = state[CID][bot.INFLIGHT_KEY]
    assert mark["mid"] == "m-7" and mark["at"] > 0
    # 空 mid 不写（别拿 None 去回滚）
    bot._mark_inflight(CID, "")
    assert state[CID][bot.INFLIGHT_KEY]["mid"] == "m-7"
