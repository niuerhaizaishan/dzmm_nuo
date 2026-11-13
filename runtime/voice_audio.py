"""语音房音频 → 玩具档位（C2：服务器端听）。

链路：
    voice:join 拿 serverUrl + participantToken
    → livekit.rtc.Room 连上（听众身份，不发布任何音轨）
    → 订阅房里别人的音频轨 → AudioStream 逐帧
    → AudioMeter：一路单极点低通（≈160Hz）+ 包络跟随 + 鼓点检测
    → on_level(0~1, beat) 回调（由上层推给分享服务）

numpy / livekit 都是**可选依赖**：镜像没装时 `available()` 返回 False，
机器人照常跑，只是不接音乐律动。
"""

import asyncio
import math
import time

DEFAULTS = {
    "cutoff_hz": 160.0,      # 只留低频：鼓点、贝斯
    "gate_db": -50.0,        # 低于这个响度当静音（前奏不嗡）
    "full_db": -16.0,        # 到这个响度算 100%
    "attack": 0.65,          # 起振（越大越跟得上鼓点）
    "release": 0.06,         # 回落（越小越快松手）
    "beat_ratio": 1.55,      # 短窗/长窗比值超过它就判一拍
    "beat_gap_ms": 220,      # 两拍最短间隔
    "cap": 0.6,              # 默认总上限（音乐是长期挂着的）
}


def available():
    """依赖齐不齐。"""
    try:
        import numpy  # noqa: F401
        from livekit import rtc  # noqa: F401
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _db(amplitude):
    return 20.0 * math.log10(max(amplitude, 1e-9))


class AudioMeter:
    """把 int16 采样压成 0~1 档位 + 拍点。纯计算，没有 IO，方便单测。"""

    def __init__(self, **options):
        conf = dict(DEFAULTS)
        conf.update({key: value for key, value in (options or {}).items() if value is not None})
        self.conf = conf
        self._lp = [0.0, 0.0]       # 两级单极点低通级联（-12dB/oct 不够，实测 5kHz 还能漏进来）
        self._alpha = None          # 依赖采样率算一次
        self._sample_rate = 0
        self._level = 0.0
        self._fast = 0.0
        self._slow = 0.0
        self._last_beat = 0.0

    # ---- 内部 ----
    def _prepare(self, sample_rate):
        if sample_rate != self._sample_rate:
            self._sample_rate = sample_rate
            rc = 1.0 / (2 * math.pi * float(self.conf["cutoff_hz"]))
            dt = 1.0 / float(sample_rate or 48000)
            self._alpha = dt / (rc + dt)

    def _normalise(self, amplitude):
        gate = float(self.conf["gate_db"])
        full = float(self.conf["full_db"])
        db = _db(amplitude)
        if db <= gate:
            return 0.0
        return max(0.0, min(1.0, (db - gate) / (full - gate)))

    # ---- 对外 ----
    def push(self, mono, sample_rate=None):
        """喂一段单声道采样（numpy 数组或 int16 序列）。返回 (value, beat)。"""
        import numpy as np

        samples = np.asarray(mono)
        if samples.size == 0:
            return self._level, False
        rate = int(sample_rate or self._sample_rate or 48000)
        self._prepare(rate)

        audio = samples.astype(np.float32)
        if audio.dtype != np.float32:                          # 保险
            audio = audio.astype(np.float32)
        if np.issubdtype(samples.dtype, np.integer):
            audio = audio / 32768.0

        alpha = self._alpha
        low = np.empty_like(audio)
        first, second = self._lp
        for index in range(audio.size):                        # 两级低通，O(n)
            first += alpha * (float(audio[index]) - first)
            second += alpha * (first - second)
            low[index] = second
        self._lp = [first, second]

        rms = float(np.sqrt(np.mean(np.square(low)))) if low.size else 0.0
        target = self._normalise(rms)

        attack = float(self.conf["attack"])
        release = float(self.conf["release"])
        if target >= self._level:
            self._level += (target - self._level) * attack
        else:
            self._level += (target - self._level) * release
        self._level = max(0.0, min(1.0, self._level))

        now = time.monotonic()
        self._fast += (self._level - self._fast) * 0.5
        self._slow += (self._level - self._slow) * 0.02
        beat = False
        gap = float(self.conf["beat_gap_ms"]) / 1000.0
        if (self._fast > 0.12 and self._slow > 0.01
                and self._fast > self._slow * float(self.conf["beat_ratio"])
                and (now - self._last_beat) >= gap):
            beat = True
            self._last_beat = now

        return round(min(1.0, self._level * float(self.conf["cap"])), 3), beat

    def reset(self):
        self._lp = [0.0, 0.0]
        self._level = self._fast = self._slow = 0.0
        self._last_beat = 0.0


class MusicBridge:
    """把 AudioMeter 的输出推给分享服务，顺带做速率限制与总上限。

    分享服务那条通道是按"控制指令"设计的，音乐每秒最多推 ~8 次就够跟手感，
    再密只会白白占带宽。
    """

    def __init__(self, share, *, cap=None, min_interval_ms=120, log=None):
        self.share = share                    # runtime.haptics.share_client.Client
        self.cap = float(cap if cap is not None else DEFAULTS["cap"])
        self.min_interval = min_interval_ms / 1000.0
        self.log = log or (lambda *args: None)
        self._last = 0.0
        self._last_value = -1.0
        self._errors = 0

    async def push(self, value, beat=False):
        now = time.monotonic()
        value = max(0.0, min(1.0, float(value) * self.cap))
        if not beat and abs(value - self._last_value) < 0.02 and \
                now - self._last < 0.5:
            return
        if now - self._last < self.min_interval and not beat:
            return
        self._last = now
        self._last_value = value
        try:
            result = await self.share.music(value, beat=beat)
        except Exception as exc:                               # noqa: BLE001
            result = {"ok": False, "detail": type(exc).__name__}
        if not result.get("ok"):
            self._errors += 1
            if self._errors in (1, 10, 100):                    # 别刷屏
                self.log("语音房 音乐推送失败：" + str(result.get("detail"))[:60])


async def run(call, self_uid, chatroom_id, *, share=None, meter=None, cap=None,
              poll_seconds=10.0, stop_event=None, log=None):
    """完整的一条链：巡检房态 → 听众进房 → LiveKit 听 → 推档位 → 退房收工。"""
    from runtime import voice

    log = log or (lambda *args: None)
    stop_event = stop_event or asyncio.Event()
    bridge = MusicBridge(share, cap=cap, log=log) if share is not None else None
    tasks = {}

    async def on_level(value, beat):
        if bridge is not None:
            await bridge.push(value, beat)

    def join(session):
        token = session.get("participantToken") or ""
        url = session.get("serverUrl") or ""
        if not token or not url:
            log("语音房 进房没有拿到 LiveKit 凭据，跳过听音")
            return
        if not available():
            log("语音房 缺 numpy/livekit，只进房不接音乐律动")
            return
        tasks["listen"] = asyncio.ensure_future(
            listen(url, token, on_level, meter=meter, stop_event=stop_event, log=log))

    def leave_():
        task = tasks.pop("listen", None)
        if task:
            task.cancel()

    listener = voice.Listener(call, self_uid, chatroom_id, on_level=on_level,
                              poll_seconds=poll_seconds, log=log,
                              on_join=join, on_leave=leave_)
    try:
        await listener.serve(stop_event)
    finally:
        leave_()
        if bridge is not None:
            try:
                await share.music(0.0)                          # 收工归零
            except Exception as exc:                            # noqa: BLE001
                log("语音房 收工归零失败：" + type(exc).__name__)


async def listen(server_url, participant_token, on_level, *, meter=None,
                 stop_event=None, max_seconds=0, log=None):
    """以听众身份连上 LiveKit，持续订阅音频并回调 on_level(value, beat)。"""
    log = log or (lambda *args: None)
    if not available():
        raise RuntimeError("依赖缺失：需要 numpy 与 livekit（服务器未安装）")
    from livekit import rtc

    meter = meter or AudioMeter()
    room = rtc.Room()
    started = time.monotonic()
    drained = {"tracks": 0}

    async def pump(track):
        import numpy as np
        stream = rtc.AudioStream(track)
        drained["tracks"] += 1
        async for event in stream:
            try:
                frame = event.frame
                data = np.frombuffer(bytes(frame.data), dtype=np.int16)
                if frame.num_channels > 1:
                    data = data.reshape(-1, frame.num_channels).mean(axis=1)
                value, beat = meter.push(data, frame.sample_rate)
                await on_level(value, beat)
            except Exception as exc:                           # noqa: BLE001
                log("语音房 音频帧处理失败：" + type(exc).__name__)
                break

    @room.on("track_subscribed")
    def on_track(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            log("语音房 订阅到音频：" + str(participant.identity)[:12])
            asyncio.ensure_future(pump(track))

    @room.on("participant_disconnected")
    def on_left(participant):
        log("语音房 参与者离开：" + str(participant.identity)[:12])

    await room.connect(server_url, participant_token)
    log("语音房 LiveKit 已连接：" + str(room.name)[:40])
    try:
        stop_event = stop_event or asyncio.Event()

        async def timer():
            limit = float(max_seconds or 0)
            if limit > 0:
                await asyncio.sleep(limit)
                stop_event.set()

        asyncio.ensure_future(timer())
        await stop_event.wait()
    finally:
        await room.disconnect()
        meter.reset()
        log("语音房 LiveKit 已断开（{:.0f}s，{} 条轨道）".format(
            time.monotonic() - started, drained["tracks"]))
