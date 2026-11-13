"""DZMM 语音房：只做"听众"的那一层。

设计约束（2026-11-13 屋主定的）：

- 小小糯**只当听众**，不当房主、不开房。所以必须先看到「有人类房主的房间」才 join；
  空房一律不碰 —— 实测空房里 join 会让 bot 变成 host，它一退出房间就跟着散了。
- 事件走现成那条 Socket.IO（`/ws/matching`），调用方把 `sio.call` 传进来即可，
  不另开连接、不自己管 token。
- 心跳：bot 只是听众，不需要靠心跳维持房间；但如果实测被踢，就在 `serve()` 里打开
  `heartbeat=True`。

对外只有三个纯函数用来判定，方便单测：
    human_hosted(state, self_uid) -> bool
    next_action(state, self_uid, inside) -> "join"|"leave"|"stay"|"idle"
"""

import asyncio

ORIGIN = "https://www.dzmm.ai"
SIO_PATH = "/ws/matching"
TIMEOUT = 12.0


class VoiceUnavailable(RuntimeError):
    """语音房接口调不通（断线、超时、被拒）。"""


# ---------------------------------------------------------------- 纯判定

def _participants(state):
    rows = (state or {}).get("participants")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    rows = (state or {}).get("members")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def human_hosted(state, self_uid):
    """这个房间是不是"有人类房主在"——只有这种情况 bot 才允许进去当听众。"""
    if not isinstance(state, dict) or not state:
        return False
    if str(state.get("status") or "") != "active":
        return False
    host = str(state.get("hostId") or "")
    if not host or host == str(self_uid or ""):
        return False
    for row in _participants(state):
        if str(row.get("userId") or "") == host:
            return str(row.get("role") or "") == "host"
    # 参与者列表偶尔不带房主（刚开的房），只要有房主 ID 且不是自己就认
    return True


def next_action(state, self_uid, inside):
    """现在该做什么。inside = bot 是否已经在房里。

    · inside 且房没了/房主不是人类 → leave
    · inside 且房好好开着           → stay
    · 不在房 + 有人类房主的房        → join
    · 其它                          → idle（绝不在空房里自己建房）
    """
    hosted = human_hosted(state, self_uid)
    if inside:
        return "stay" if hosted else "leave"
    return "join" if hosted else "idle"


# ---------------------------------------------------------------- 接口封装

async def get_state(call, chatroom_id):
    """返回 state(dict) 或 None（没有房）。call 形如 sio.call(event, payload, timeout=…)。"""
    try:
        ack = await call("voice:get-state", {"chatroomId": chatroom_id}, timeout=TIMEOUT)
    except Exception as exc:                                   # noqa: BLE001
        raise VoiceUnavailable("语音房查询失败：" + type(exc).__name__) from None
    if not isinstance(ack, dict) or not ack.get("success"):
        raise VoiceUnavailable("语音房查询被拒：" + str((ack or {}).get("error") or "")[:60])
    return ack.get("state")


async def join(call, chatroom_id):
    """进房（或建房）。返回 join ACK：serverUrl / participantToken / sessionId / state。"""
    try:
        ack = await call("voice:join", {"chatroomId": chatroom_id}, timeout=TIMEOUT + 8)
    except Exception as exc:                                   # noqa: BLE001
        raise VoiceUnavailable("进房失败：" + type(exc).__name__) from None
    if not isinstance(ack, dict) or not ack.get("success"):
        raise VoiceUnavailable("进房被拒：" + str((ack or {}).get("error") or "")[:60])
    return ack


async def leave(call, chatroom_id):
    try:
        ack = await call("voice:leave", {"chatroomId": chatroom_id}, timeout=TIMEOUT)
    except Exception as exc:                                   # noqa: BLE001
        raise VoiceUnavailable("退出语音房失败：" + type(exc).__name__) from None
    return bool((ack or {}).get("success"))


async def heartbeat(call, chatroom_id):
    try:
        ack = await call("voice:heartbeat", {"chatroomId": chatroom_id}, timeout=TIMEOUT)
    except Exception as exc:                                   # noqa: BLE001
        raise VoiceUnavailable("语音房心跳失败：" + type(exc).__name__) from None
    return bool((ack or {}).get("success"))


async def end(call, chatroom_id):
    """结束整个房间（只有房主能；bot 只当听众时基本用不到，留给运维脚本）。"""
    try:
        ack = await call("voice:end", {"chatroomId": chatroom_id}, timeout=TIMEOUT)
    except Exception as exc:                                   # noqa: BLE001
        raise VoiceUnavailable("结束语音房失败：" + type(exc).__name__) from None
    return bool((ack or {}).get("success"))


# ---------------------------------------------------------------- 值守

class Listener:
    """按 `next_action()` 决定进退，并在房里时把 LiveKit 的音频交给回调。

    on_level(value, beat)：value 0~1，beat 表示这一拍是不是刚踩到鼓点。
    poll_seconds：多久看一次房态（10 秒足够，房态变化不频繁）。
    """

    def __init__(self, call, self_uid, chatroom_id, on_level=None, *,
                 poll_seconds=10.0, heartbeat_seconds=0.0, log=None,
                 on_join=None, on_leave=None):
        self.call = call
        self.self_uid = str(self_uid or "")
        self.cid = chatroom_id
        self.on_level = on_level
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.log = log or (lambda *args: None)
        self.on_join = on_join            # 进房成功后回调（拿 join ACK），用来开听
        self.on_leave = on_leave          # 退房/掉线回调，用来停听
        self.inside = False
        self.session = None          # dict: serverUrl / participantToken / roomName
        self._stop = None            # asyncio.Event
        self._hb_task = None

    async def step(self):
        """走一步：查房态 → 需要就进/退。返回当前动作。"""
        state = await get_state(self.call, self.cid)
        action = next_action(state, self.self_uid, self.inside)
        if action == "join":
            ack = await join(self.call, self.cid)
            self.inside = True
            self.session = {"serverUrl": ack.get("serverUrl", ""),
                            "participantToken": ack.get("participantToken", ""),
                            "sessionId": ack.get("sessionId", ""),
                            "roomName": ((ack.get("state") or {}).get("roomName") or "")}
            self.log("语音房 进房（听众）room=" + str(self.session["roomName"])[:40])
            if self.heartbeat_seconds:
                self._start_heartbeat()
            if self.on_join:
                try:
                    self.on_join(self.session)
                except Exception as exc:                       # noqa: BLE001
                    self.log("语音房 进房回调失败：" + type(exc).__name__)
        elif action == "leave":
            await leave(self.call, self.cid)
            self._finish()
            self.log("语音房 已退出（房间关了或房主换了）")
        return action

    def _start_heartbeat(self):
        if self._hb_task or not self.heartbeat_seconds:
            return

        async def beat():
            while self.inside:
                await asyncio.sleep(self.heartbeat_seconds)
                if not self.inside:
                    return
                try:
                    await heartbeat(self.call, self.cid)
                except VoiceUnavailable as exc:
                    self.log("语音房 心跳失败：" + str(exc)[:60])

        self._hb_task = asyncio.ensure_future(beat())

    def _finish(self):
        was_inside = self.inside
        self.inside = False
        self.session = None
        if self._hb_task:
            self._hb_task.cancel()
            self._hb_task = None
        if was_inside and self.on_leave:
            try:
                self.on_leave()
            except Exception as exc:                           # noqa: BLE001
                self.log("语音房 退房回调失败：" + type(exc).__name__)

    async def serve(self, stop_event=None):
        """常驻：每 poll_seconds 走一步；stop_event 置位就收工。"""
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            try:
                await self.step()
            except VoiceUnavailable as exc:
                self.log("语音房 巡检失败：" + str(exc)[:60])
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                continue
        if self.inside:
            try:
                await leave(self.call, self.cid)
            except VoiceUnavailable:
                pass
            self._finish()
