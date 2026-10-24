"""触觉指令的调度器：限幅、去重、限流、自动停止、替换与急停都在这一层。

设计原则（这几条是整条链路的安全底线，改动前先读）：
  1. 每条指令都带「最大时长」，到时由服务器主动停止——不存在「设完就不管」。
  2. 新指令替换旧指令：旧指令的自动停止计时器作废，避免两条计时器互相打架。
  3. event_id 去重：同一条剧情事件重放、重连、刷新都不会二次触发。
  4. 强度与时长都在服务器侧夹紧（上限由操作者设定），模型或调用方给多大都不能越界。
  5. 停止是幂等的：断连、关闭、急停都可以重复调用。
"""
import asyncio
import time

from runtime.obs import log
from runtime.haptics.protocol import HapticsUnavailable

MIN_DURATION_MS = 200
DEFAULT_DURATION_MS = 1200


class Scheduler:
    def __init__(self, client, *, max_level=0.6, max_duration_ms=4000,
                 min_interval_ms=150, dedup_ttl=600.0, kind="Vibrate"):
        self.client = client
        self.max_level = max(0.05, min(1.0, float(max_level)))
        self.max_duration_ms = max(MIN_DURATION_MS, int(max_duration_ms))
        self.min_interval_ms = max(0, int(min_interval_ms))
        self.dedup_ttl = max(0.0, float(dedup_ttl))
        self.kind = kind
        self.active = False
        self.active_until = 0.0
        self._token = 0
        self._timer = None
        self._seen = {}
        self._last_send = 0.0
        self.stats = {"accepted": 0, "rejected": 0, "clamped_level": 0,
                      "clamped_duration": 0, "stopped": 0, "failed": 0}
        self.last = {}

    # ---------- 对外接口 ----------

    async def submit(self, event_id, level, duration_ms=DEFAULT_DURATION_MS, *, source="manual"):
        now = time.monotonic()
        reason = self._check(event_id, level, duration_ms, now, source)
        if reason:
            self.stats["rejected"] += 1
            return {"accepted": False, "reason": reason}

        clamped_level = min(float(level), self.max_level)
        clamped_duration = max(MIN_DURATION_MS, min(int(duration_ms), self.max_duration_ms))
        if clamped_level < float(level):
            self.stats["clamped_level"] += 1
        if clamped_duration < int(duration_ms):
            self.stats["clamped_duration"] += 1

        if event_id:
            self._seen[str(event_id)] = now
        self._last_send = now
        try:
            device, value = await self.client.output(clamped_level, kind=self.kind)
        except HapticsUnavailable as exc:
            self.stats["failed"] += 1
            return {"accepted": False, "reason": "unavailable", "detail": str(exc)}

        self._token += 1
        token = self._token
        self.active = True
        self.active_until = time.time() + clamped_duration / 1000.0
        self._arm_stop(token, clamped_duration)
        self.stats["accepted"] += 1
        self.last = {"event_id": str(event_id or ""), "level": clamped_level,
                     "duration_ms": clamped_duration, "source": source,
                     "device": device.display, "value": value, "at": time.time()}
        return {"accepted": True, "level": clamped_level, "value": value,
                "duration_ms": clamped_duration, "device": device.display}

    async def stop(self, reason="manual"):
        self._token += 1
        self._cancel_timer()
        was_active = self.active
        self.active = False
        self.active_until = 0.0
        ok = await self.client.stop_all()
        if was_active or ok:
            self.stats["stopped"] += 1
        self.last = {"stopped": reason, "at": time.time(), "reached": ok}
        if was_active:
            log("haptics: 已停止（" + reason + "）")
        return {"stopped": True, "reason": reason, "reached_device": ok}

    async def shutdown(self):
        await self.stop("shutdown")
        await self.client.close()

    def status(self):
        now = time.monotonic()
        self._prune(now)
        return {"active": self.active,
                "remaining_ms": max(0, int((self.active_until - time.time()) * 1000)),
                "caps": {"max_level": self.max_level, "max_duration_ms": self.max_duration_ms,
                         "min_interval_ms": self.min_interval_ms, "kind": self.kind},
                "stats": dict(self.stats), "last": self.last,
                "dedup_entries": len(self._seen), "device": self.client.status()}

    # ---------- 内部 ----------

    def _check(self, event_id, level, duration_ms, now, source):
        if not self.client.connected:
            return "unavailable"
        if not self.client.devices:
            return "no_device"
        key = str(event_id or "")
        if key and self.dedup_ttl and key in self._seen and now - self._seen[key] < self.dedup_ttl:
            return "duplicate"
        try:
            value = float(level)
        except (TypeError, ValueError):
            return "bad_level"
        if value <= 0:
            return "bad_level"
        try:
            length = int(duration_ms)
        except (TypeError, ValueError):
            return "bad_duration"
        if length <= 0:
            return "bad_duration"
        if self.min_interval_ms and now - self._last_send < self.min_interval_ms / 1000.0:
            return "rate_limited"
        return ""

    def _arm_stop(self, token, duration_ms):
        self._cancel_timer()
        self._timer = asyncio.create_task(self._auto_stop(token, duration_ms))

    def _cancel_timer(self):
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _auto_stop(self, token, duration_ms):
        try:
            await asyncio.sleep(duration_ms / 1000.0)
        except asyncio.CancelledError:
            return
        if token != self._token:
            return
        self.active = False
        self.active_until = 0.0
        try:
            await self.client.stop_all()
        except HapticsUnavailable:
            pass
        log("haptics: 到时自动停止（" + str(duration_ms) + "ms）")

    def _prune(self, now):
        if not self.dedup_ttl:
            return
        expired = [key for key, stamp in self._seen.items() if now - stamp > self.dedup_ttl]
        for key in expired:
            self._seen.pop(key, None)
