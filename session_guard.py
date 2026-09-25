"""会话锁的统一入口（P5）。

单独一个模块，是为了让 `renew_token` / `headless_relogin` / `auto_recover`
三处都能一行 import，而不必各自写 try/except。

`runtime` 包不可用（例如部署漏了子目录）时**退化为"永远拿到锁"** ——
即回到改动前的行为，绝不因为缺这个包就让续期/重登停摆。
"""
from contextlib import nullcontext

try:
    from runtime.session import (          # noqa: F401
        guard,
        note_credentials_written,
        version,
    )
except Exception:                          # noqa: BLE001

    def guard(reason: str, timeout: float = 30.0):
        """退化实现：永远视为拿到锁（无锁运行）。"""
        return nullcontext(True)

    def note_credentials_written(reason: str) -> int:
        return 0

    def version() -> int:
        return 0
