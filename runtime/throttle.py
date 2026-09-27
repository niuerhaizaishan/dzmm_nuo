"""出站限流：令牌桶 + 冷却（P5.2）。

### 为什么要

平台的 tRPC 接口共享配额，短时间打太多会触发限流甚至验证码 —— 这是小小糯
历史上最痛的问题（captcha / 401 风暴）。现在**所有 tRPC 调用都汇聚到
`dzmm.py` 的两个函数**（`api()` 与 `remove_member()`），所以限流只要加在这两点上
（和 P3 的 `Sender.send`、P5.1 的 `token_store.save_cookies` 是同一个套路）。

### 设计

- **令牌桶**：匀速放行（默认 90 次/分钟，突发 15）。目的不是卡死，而是把突发摊平。
- **冷却**：检测到限流/验证码信号 → 全局冷却一段时间（默认 60 秒；验证码 300 秒），
  期间所有请求先等待。撞了风控还继续打只会更糟。
- **优先级**：`priority<=0` 绕过桶（给"必须马上发出去"的路径用）。
- **失败开放**：任何内部异常一律放行；等待超时也放行（记 `forced`）。
  限流坏了不能让 bot 停摆 —— 与 P5.1 的会话锁同一原则。
- `DZMM_THROTTLE=off` 可整体关闭。

### 已知未接

发送路径（`Sender` → 平台）返回的是 bool，看不到"请稍后再试"的具体文案，
所以**发消息撞限流暂时无法自动触发冷却**。读取路径（tRPC）已覆盖。
"""
import os
import threading
import time

DEFAULT_RATE_PER_MIN = 90
DEFAULT_BURST = 15
COOLDOWN_SEC = 60.0
CAPTCHA_COOLDOWN_SEC = 300.0
DEFAULT_WAIT_TIMEOUT = 10.0
# ⚠️ 冷却期的等待上限（P12-J 从 10 秒降到 2 秒）。
# 冷却期里 `acquire` 是**睡**的，而这个值就是每次平台调用最多睡多久 ⇒
# 一条普通回复要调两三次平台 = 20~30 秒，用户看到的是"机器人不回复"。
# 2 秒足够让令牌桶喘口气，又不至于把回复拖到分钟级。
COOLDOWN_WAIT_TIMEOUT = 2.0
# 限流/风控信号（大小写不敏感的子串）
THROTTLE_MARKERS = ("请稍后再试", "请勿发送重复", "过于频繁", "操作频繁",
                    "too many requests", "rate limit", "429")
CAPTCHA_MARKERS = ("captcha", "验证码", "人机验证", "风控")

__all__ = ["TokenBucket", "acquire", "note_response", "note_text",
           "cooldown_remaining", "set_cooldown", "stats", "reset"]


class TokenBucket:
    """线程安全令牌桶（同步版 —— dzmm.py 用的是 requests）。"""

    def __init__(self, rate_per_min: float, burst: float):
        self.rate = max(0.0, rate_per_min) / 60.0      # 每秒补充
        self.capacity = max(1.0, burst)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        dt = now - self.updated
        if dt > 0:
            self.tokens = min(self.capacity, self.tokens + dt * self.rate)
            self.updated = now

    def try_take(self, cost: float = 1.0, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._refill(now)
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False

    def wait_time(self, cost: float = 1.0, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._refill(now)
            if self.tokens >= cost:
                return 0.0
            if self.rate <= 0:
                return float("inf")
            return (cost - self.tokens) / self.rate

    def info(self) -> dict:
        with self._lock:
            self._refill(time.monotonic())
            return {"tokens": round(self.tokens, 2),
                    "capacity": self.capacity,
                    "rate_per_min": round(self.rate * 60, 1)}


# ---------------------------------------------------------------- 模块状态

_state = {
    "enabled": None,          # None = 读环境变量
    "bucket": None,
    "cooldown_until": 0.0,
    "cooldown_reason": "",
    "counts": {"ok": 0, "waited": 0, "bypass": 0, "forced": 0, "cooldown_waits": 0},
}
_lock = threading.Lock()


def _enabled() -> bool:
    if _state["enabled"] is not None:
        return bool(_state["enabled"])
    v = (os.environ.get("DZMM_THROTTLE") or "on").strip().lower()
    return v not in ("off", "0", "no", "false")


def _bucket() -> TokenBucket:
    b = _state["bucket"]
    if b is None:
        rate = float(os.environ.get("DZMM_RATE_PER_MIN") or DEFAULT_RATE_PER_MIN)
        burst = float(os.environ.get("DZMM_RATE_BURST") or DEFAULT_BURST)
        b = _state["bucket"] = TokenBucket(rate, burst)
    return b


def _bump(key: str, n: int = 1) -> None:
    _state["counts"][key] = _state["counts"].get(key, 0) + n


# ---------------------------------------------------------------- 对外接口

def set_cooldown(seconds: float, reason: str = "") -> None:
    """进入全局冷却：期间所有请求先等待。"""
    until = time.monotonic() + max(0.0, seconds)
    if until > _state["cooldown_until"]:
        _state["cooldown_until"] = until
        _state["cooldown_reason"] = reason


def cooldown_remaining() -> float:
    return max(0.0, _state["cooldown_until"] - time.monotonic())


def acquire(reason: str = "", cost: float = 1.0, priority: int = 1,
            timeout: float = DEFAULT_WAIT_TIMEOUT) -> bool:
    """取一个出站配额。**永远返回 True**（失败开放），返回值仅表示是否等待过。"""
    if not _enabled():
        _bump("bypass")
        return False
    if priority <= 0:                      # 关键路径绕过桶
        _bump("bypass")
        return False

    waited = False
    try:
        deadline = time.monotonic() + max(0.0, timeout)

        # 1) 冷却期先等（**用更短的上限**：冷却里睡 10 秒会把回复拖成分钟级）
        cd = cooldown_remaining()
        if cd > 0:
            _bump("cooldown_waits")
            waited = True
            time.sleep(min(cd, COOLDOWN_WAIT_TIMEOUT,
                           max(0.0, deadline - time.monotonic())))

        # 2) 令牌桶
        b = _bucket()
        while True:
            if b.try_take(cost):
                break
            if time.monotonic() >= deadline:
                _bump("forced")            # 超时也放行（失败开放）
                break
            waited = True
            time.sleep(min(b.wait_time(cost), 0.5,
                           max(0.0, deadline - time.monotonic()) + 0.001))
        _bump("ok")
        if waited:
            _bump("waited")
        return waited
    except Exception:
        _bump("forced")
        return False


def note_text(text: str) -> str | None:
    """从任意响应文本里识别限流/验证码信号，并据此设置冷却。返回命中的类别。"""
    if not text:
        return None
    low = str(text).lower()
    try:
        for m in CAPTCHA_MARKERS:
            if m in low:
                set_cooldown(CAPTCHA_COOLDOWN_SEC, "captcha: " + m)
                return "captcha"
        for m in THROTTLE_MARKERS:
            if m in low:
                set_cooldown(COOLDOWN_SEC, "throttle: " + m)
                return "throttle"
    except Exception:
        return None
    return None


def note_response(resp=None, body: str | None = None, path: str = "") -> str | None:
    """检查一次响应，必要时进入冷却。

    识别：HTTP 429 / 响应体里的限流与验证码信号。
    """
    try:
        if resp is not None and getattr(resp, "status_code", None) == 429:
            set_cooldown(COOLDOWN_SEC, "http 429 " + path)
            return "throttle"
        if body is None and resp is not None:
            try:
                body = resp.text
            except Exception:
                body = None
        return note_text(body or "")
    except Exception:
        return None


def stats() -> dict:
    # 懒建桶，这样启动时也能报到真实配置（否则 bucket 为 None）
    b = _state["bucket"]
    if b is None and _enabled():
        try:
            b = _bucket()
        except Exception:
            b = None
    return {
        "enabled": _enabled(),
        "counts": dict(_state["counts"]),
        "cooldown_remaining": round(cooldown_remaining(), 2),
        "cooldown_reason": _state["cooldown_reason"],
        "bucket": b.info() if b else None,
    }


def reset() -> None:
    """测试用：清空状态。"""
    with _lock:
        _state["bucket"] = None
        _state["cooldown_until"] = 0.0
        _state["cooldown_reason"] = ""
        _state["counts"] = {"ok": 0, "waited": 0, "bypass": 0,
                            "forced": 0, "cooldown_waits": 0}
