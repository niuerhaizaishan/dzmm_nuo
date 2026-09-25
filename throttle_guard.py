"""限流入口（P5.2）。

单独一个模块，让 `dzmm.py` 一行 import 即可；`runtime` 包不可用时
**退化为完全不限流**（回到改动前行为）。
"""
try:
    from runtime.throttle import (         # noqa: F401
        acquire,
        cooldown_remaining,
        note_response,
        note_text,
        set_cooldown,
        stats,
    )
except Exception:                          # noqa: BLE001

    def acquire(*_a, **_kw) -> bool:
        return False

    def note_response(*_a, **_kw):
        return None

    def note_text(*_a, **_kw):
        return None

    def set_cooldown(*_a, **_kw) -> None:
        return None

    def cooldown_remaining() -> float:
        return 0.0

    def stats() -> dict:
        return {"enabled": False, "counts": {}, "cooldown_remaining": 0.0,
                "cooldown_reason": "", "bucket": None}
