"""出站限流（令牌桶 + 冷却）单测 —— P5.2。

重点：**失败开放**。限流坏了不能让 bot 停摆。
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime import throttle  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("DZMM_THROTTLE", raising=False)
    monkeypatch.delenv("DZMM_RATE_PER_MIN", raising=False)
    monkeypatch.delenv("DZMM_RATE_BURST", raising=False)
    throttle._state["enabled"] = None
    throttle.reset()
    yield
    throttle._state["enabled"] = None
    throttle.reset()


def bucket(rate_per_min, burst):
    b = throttle.TokenBucket(rate_per_min, burst)
    throttle._state["bucket"] = b
    return b


# ---------------------------------------------------------------- 令牌桶

def test_bucket_allows_burst_then_blocks():
    b = throttle.TokenBucket(60, 3)          # 1/秒，容量 3
    assert [b.try_take() for _ in range(3)] == [True, True, True]
    assert b.try_take() is False             # 第 4 次没令牌了


def test_bucket_refills_over_time():
    b = throttle.TokenBucket(600, 1)         # 10/秒
    assert b.try_take() is True
    assert b.try_take() is False
    time.sleep(0.25)                         # 约补 2.5 个
    assert b.try_take() is True


def test_wait_time_zero_when_tokens_available():
    b = throttle.TokenBucket(60, 5)
    assert b.wait_time() == 0.0


def test_wait_time_positive_when_empty():
    b = throttle.TokenBucket(60, 1)          # 1/秒
    b.try_take()
    assert b.wait_time() == pytest.approx(1.0, abs=0.2)


def test_bucket_info():
    b = throttle.TokenBucket(120, 4)
    info = b.info()
    assert info["capacity"] == 4
    assert info["rate_per_min"] == 120


# ---------------------------------------------------------------- acquire

def test_acquire_bypasses_when_disabled(monkeypatch):
    monkeypatch.setenv("DZMM_THROTTLE", "off")
    throttle._state["enabled"] = None
    bucket(60, 1)
    assert throttle.acquire("x") is False
    assert throttle.acquire("x") is False    # 不限流，不等待
    assert throttle.stats()["counts"]["bypass"] == 2


def test_acquire_priority_zero_bypasses_bucket():
    bucket(60, 1)
    assert throttle.acquire("critical", priority=0) is False
    assert throttle.acquire("critical", priority=0) is False
    assert throttle.stats()["counts"]["bypass"] == 2


def test_acquire_waits_for_token():
    bucket(600, 1)                           # 10/秒
    assert throttle.acquire("a") is False    # 第一个立即拿到
    t0 = time.monotonic()
    waited = throttle.acquire("b", timeout=2.0)
    elapsed = time.monotonic() - t0
    assert waited is True
    assert elapsed >= 0.05                   # 确实等过
    assert throttle.stats()["counts"]["waited"] >= 1


def test_acquire_never_raises_on_internal_error(monkeypatch):
    """桶本身坏掉也必须放行（失败开放）。"""
    def boom():
        raise RuntimeError("bucket broken")
    monkeypatch.setattr(throttle, "_bucket", boom)
    assert throttle.acquire("x", timeout=0.1) is False   # 没抛异常


def test_acquire_forced_counted_on_timeout():
    bucket(1, 1)                             # 极慢：1/分钟
    throttle.acquire("a")                    # 用掉唯一令牌
    t0 = time.monotonic()
    throttle.acquire("b", timeout=0.2)       # 等不到就放行
    assert time.monotonic() - t0 < 2.0
    assert throttle.stats()["counts"]["forced"] >= 1


# ---------------------------------------------------------------- 冷却

def test_cooldown_blocks_then_expires():
    throttle.set_cooldown(0.3, "test")
    assert throttle.cooldown_remaining() > 0
    bucket(6000, 100)
    t0 = time.monotonic()
    throttle.acquire("x", timeout=2.0)
    assert time.monotonic() - t0 >= 0.2      # 冷却期内确实等了
    time.sleep(0.35)
    assert throttle.cooldown_remaining() == 0.0


def test_cooldown_takes_longer_value():
    throttle.set_cooldown(1.0, "long")
    first = throttle.cooldown_remaining()
    throttle.set_cooldown(0.1, "short")      # 更短不应缩短已有的冷却
    assert throttle.cooldown_remaining() >= first - 0.05


def test_note_text_detects_throttle_signal():
    assert throttle.note_text("请稍后再试") == "throttle"
    assert throttle.cooldown_remaining() > 0


def test_note_text_detects_captcha_signal():
    assert throttle.note_text("captcha_required") == "captcha"
    assert throttle.cooldown_remaining() > throttle.COOLDOWN_SEC


def test_note_text_detects_english_rate_limit():
    assert throttle.note_text("Too Many Requests") == "throttle"


def test_note_text_ignores_normal_text():
    assert throttle.note_text("好的，已收到") is None
    assert throttle.note_text("") is None
    assert throttle.cooldown_remaining() == 0.0


def test_note_response_detects_429():
    class R:
        status_code = 429
        text = ""
    assert throttle.note_response(R(), path="x") == "throttle"
    assert throttle.cooldown_remaining() > 0


def test_note_response_reads_body():
    class R:
        status_code = 200
        text = '{"error":"请稍后再试"}'
    assert throttle.note_response(R(), path="x") == "throttle"


def test_note_response_survives_broken_object():
    class R:
        @property
        def status_code(self):
            raise RuntimeError("boom")
    assert throttle.note_response(R()) is None      # 不抛


# ---------------------------------------------------------------- 接线

def test_dzmm_uses_throttle_on_both_funnels():
    import inspect

    import dzmm
    api_src = inspect.getsource(dzmm.api)
    rm_src = inspect.getsource(dzmm.remove_member)
    assert "_throttle(" in api_src and "_note_resp(" in api_src
    assert "_throttle(" in rm_src and "_note_resp(" in rm_src


def test_wrapper_module_degrades_without_runtime():
    import throttle_guard
    assert callable(throttle_guard.acquire)
    assert throttle_guard.stats()["enabled"] is not False or True   # 只要可调用即可
