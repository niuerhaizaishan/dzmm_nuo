"""语音房音乐律动：听众判定 + 能量映射 + 分享服务推送。

不联网、不起 LiveKit，只测纯逻辑：
- `voice.human_hosted / next_action`：什么情况下 bot 才允许进房
- `voice_audio.AudioMeter`：静音、持续低频、鼓点脉冲三种输入的输出
- `share._relay_music`：档位夹取与房主不在线的处理
"""

import asyncio
import math
import sys

import pytest

from runtime import voice, voice_audio
from runtime.haptics import share as share_mod

SELF = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"
HUMAN = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"


def room(host=HUMAN, status="active", participants=None):
    return {"chatroomId": "cid", "status": status, "hostId": host,
            "participants": participants if participants is not None else [
                {"userId": host, "role": "host"}]}


# ---------------------------------------------------------------- 听众判定

def test_human_hosted_accepts_human_room():
    assert voice.human_hosted(room(), SELF) is True


def test_human_hosted_rejects_empty_and_self_hosted():
    assert voice.human_hosted(None, SELF) is False
    assert voice.human_hosted({}, SELF) is False
    assert voice.human_hosted(room(host=SELF), SELF) is False          # 自己就是房主
    assert voice.human_hosted(room(status="ended"), SELF) is False     # 房已经关了


def test_human_hosted_tolerates_missing_participant_list():
    """刚开房时参与者列表可能还没带房主，只要 hostId 是人类就认。"""
    assert voice.human_hosted(room(participants=[]), SELF) is True


@pytest.mark.parametrize("state,inside,expected", [
    (None, False, "idle"),                     # 没房 → 绝不自建
    (room(), False, "join"),                   # 有人类房主 → 进去当听众
    (room(host=SELF), False, "idle"),          # 空房被自己占了 → 不动
    (room(), True, "stay"),
    (None, True, "leave"),
    (room(status="closed"), True, "leave"),
])
def test_next_action(state, inside, expected):
    assert voice.next_action(state, SELF, inside) == expected


def test_listener_joins_only_when_room_is_hosted():
    """Listener.step 走一遍：空房不 join，进/退房分别触发回调。"""
    calls = []

    async def call(event, payload=None, timeout=None):
        calls.append(event)
        if event == "voice:get-state":
            return {"success": True, "state": None}
        return {"success": True}

    listener = voice.Listener(call, SELF, "cid")
    assert asyncio.run(listener.step()) == "idle"
    assert "voice:join" not in calls          # 空房里绝不进

    async def call2(event, payload=None, timeout=None):
        calls.append(event)
        if event == "voice:get-state":
            return {"success": True, "state": room()}
        if event == "voice:join":
            return {"success": True, "serverUrl": "wss://x", "participantToken": "t",
                    "state": {"roomName": "r"}}
        return {"success": True}

    listener2 = voice.Listener(call2, SELF, "cid")
    assert asyncio.run(listener2.step()) == "join"
    assert listener2.inside and listener2.session["participantToken"] == "t"


# ---------------------------------------------------------------- 能量映射

def _tone(frames, hz=60, amplitude=12000, rate=48000):
    import numpy as np
    t = np.arange(frames) / rate
    return (np.sin(2 * math.pi * hz * t) * amplitude).astype("int16")


def test_meter_silence_is_zero():
    meter = voice_audio.AudioMeter()
    import numpy as np
    for _ in range(5):
        value, beat = meter.push(np.zeros(480, dtype="int16"), 48000)
    assert value == 0.0 and beat is False


def test_meter_loud_low_frequency_rises_and_caps():
    meter = voice_audio.AudioMeter()
    values = []
    for _ in range(8):
        value, _beat = meter.push(_tone(480, amplitude=20000), 48000)
        values.append(value)
    assert values[-1] > 0.3, values
    assert values[-1] <= meter.conf["cap"] + 1e-9, "不能超过总上限"


def test_meter_high_frequency_is_filtered_out():
    """5kHz 的响度不该把玩具顶起来（只认低频）。"""
    meter = voice_audio.AudioMeter()
    for _ in range(8):
        value, _ = meter.push(_tone(480, hz=5000, amplitude=20000), 48000)
    assert value < 0.15, value


def test_meter_detects_beats_on_pulses():
    import numpy as np
    meter = voice_audio.AudioMeter(beat_gap_ms=100)
    beats = 0
    for index in range(24):
        chunk = _tone(480, amplitude=20000) if index % 6 == 0 else np.zeros(480, dtype="int16")
        _value, beat = meter.push(chunk, 48000)
        beats += 1 if beat else 0
    assert beats >= 1, "连续的鼓点脉冲至少要判出一拍"


def test_meter_reset_clears_state():
    meter = voice_audio.AudioMeter()
    meter.push(_tone(480, amplitude=20000), 48000)
    meter.reset()
    assert meter._level == 0.0 and meter._lp == [0.0, 0.0]


# ---------------------------------------------------------------- 分享服务侧

class FakeSession:
    def __init__(self, host=True):
        self.music = 0.0
        self._host = object() if host else None
        self.sent = []

    @property
    def host(self):
        return self._host


def test_relay_music_clamps_and_relays(monkeypatch):
    session = FakeSession()

    async def to_host(session_, payload):
        session_.sent.append(payload)
        return True

    monkeypatch.setattr(share_mod, "_to_host", to_host)
    value, why = asyncio.run(share_mod._relay_music(session, {"value": 3.5}))
    assert why is None and value <= 1.0
    assert session.music == value
    assert session.sent[-1] == {"t": "music", "value": value}


def test_relay_music_rejects_bad_value_and_missing_host(monkeypatch):
    async def to_host(session_, payload):
        return True

    monkeypatch.setattr(share_mod, "_to_host", to_host)
    assert asyncio.run(share_mod._relay_music(FakeSession(), {"value": "abc"}))[1] == "强度不对"
    assert asyncio.run(share_mod._relay_music(FakeSession(host=False), {"value": 0.4}))[1] == "房主不在线"


def test_session_state_exposes_music():
    session = share_mod.Session("sid", "123456", "http://host")
    assert "music" in session.state() and session.state()["music"] == 0.0


def test_listener_fires_join_and_leave_callbacks():
    joined, leaved = [], []

    async def call(event, payload=None, timeout=None):
        if event == "voice:get-state":
            return {"success": True, "state": None}
        return {"success": True}

    listener = voice.Listener(call, SELF, "cid", on_join=joined.append,
                              on_leave=lambda: leaved.append(True))
    asyncio.run(listener.step())                 # 空房：什么都不该发生
    assert joined == [] and leaved == []

    listener.inside = True                       # 假装已经在房里，房没了 → 退房
    asyncio.run(listener.step())
    assert leaved == [True] and listener.inside is False


class FakeShare:
    def __init__(self):
        self.calls = []

    async def music(self, value, beat=False):
        self.calls.append((round(value, 3), beat))
        return {"ok": True}


def test_music_bridge_caps_and_rate_limits():
    share = FakeShare()
    bridge = voice_audio.MusicBridge(share, cap=0.5, min_interval_ms=120)

    async def run():
        await bridge.push(1.0, beat=False)      # 第一条必发
        await bridge.push(1.0, beat=False)      # 太快 + 值没变 → 丢
        await bridge.push(0.9, beat=True)       # 拍点优先，允许穿过

    asyncio.run(run())
    assert [value for value, _ in share.calls][0] <= 0.5
    assert len(share.calls) == 2
    assert share.calls[-1][1] is True


def test_music_bridge_logs_first_failure_only_once():
    logs = []

    class Broken:
        async def music(self, value, beat=False):
            raise RuntimeError("boom")

    bridge = voice_audio.MusicBridge(Broken(), log=logs.append)

    async def run():
        for _ in range(3):
            await bridge.push(0.3, beat=True)

    asyncio.run(run())
    assert len(logs) == 1, logs
