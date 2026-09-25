"""唯一登录者：把「凭据写入」收敛到单一受锁路径（P5）。

### 解决什么

现在有**四个**地方可能同时动凭据：

1. `entrypoint.sh` 的 `renew_token.py` 循环（每 5 分钟）
2. `check_bot_health.py` 主动派生 `headless_relogin.py`（refresh_token 将到期时）
3. `auto_recover.py` 的登录阶梯（bot 连续 unauthorized 时调起）
4. 容器重启时 `entrypoint.sh` 直接跑 `headless_relogin.py`

而平台的 **refresh_token 是轮换制**（`renew_token.py` 里就有
「已同步新 refresh_token（rotation）」），**两处同时续期会互相作废** ——
表现就是随机 401 / 被踢下线，正是 DDZM 文档强调的那条硬约束。

### 设计原则：失败开放（fail-open）

锁只用来**减少并发**，绝不能因为它自己坏掉就让续期停摆 ——
那会把"偶发 401"升级成"必然掉线"。所以：

- 锁机制本身出任何异常 → 视为**拿到锁**，照常继续
- 拿不到锁（别人正在做）→ 由调用方决定：**跳过本次**（不是失败）
- `DZMM_SESSION_LOCK=off` 可整体关闭

### 后端

Linux 用 `fcntl.flock`（进程崩溃自动释放）；其它平台退回
`O_CREAT|O_EXCL` + 陈旧超时（进程被 kill 后最多阻塞 `stale_after`）。
"""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

try:                                    # Linux（生产）
    import fcntl as _fcntl
except ImportError:                     # Windows（本地开发/测试）
    _fcntl = None

_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = _ROOT / ".session.lock"
VERSION_FILE = _ROOT / ".session_version.json"

# 测试可强制后端（"flock" / "excl"）；None = 自动
FORCED_BACKEND = None
# O_EXCL 后端下，判定陈旧锁的阈值（秒）
STALE_AFTER = 300.0
_POLL = 0.5

__all__ = ["guard", "note_credentials_written", "version", "LOCK_FILE", "VERSION_FILE"]


def _lock_enabled() -> bool:
    v = (os.environ.get("DZMM_SESSION_LOCK") or "on").strip().lower()
    return v not in ("off", "0", "no", "false")


def _backend() -> str | None:
    if FORCED_BACKEND:
        return FORCED_BACKEND
    return "flock" if _fcntl is not None else "excl"


def _write_note(fd: int, reason: str) -> None:
    try:
        os.ftruncate(fd, 0)
        os.write(fd, "{} pid={} at={:.0f}\n".format(
            reason, os.getpid(), time.time()).encode("utf-8"))
    except Exception:
        pass


def _acquire(timeout: float):
    """返回 (fd, got)。fd 为 None 表示没拿到；异常表示机制故障（调用方 fail-open）。"""
    backend = _backend()
    deadline = time.time() + max(0.0, timeout)
    while True:
        if backend == "flock":
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return fd, True
            except OSError:
                os.close(fd)
                if time.time() >= deadline:
                    return None, False
                time.sleep(_POLL)
        else:
            try:
                fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                return fd, True
            except FileExistsError:
                try:
                    if time.time() - LOCK_FILE.stat().st_mtime > STALE_AFTER:
                        LOCK_FILE.unlink(missing_ok=True)
                        continue
                except Exception:
                    pass
                if time.time() >= deadline:
                    return None, False
                time.sleep(_POLL)


def _release(fd) -> None:
    if fd is None:
        return
    backend = _backend()
    if backend == "flock":
        try:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        except Exception:
            pass
    try:
        os.close(fd)
    except Exception:
        pass
    # ⚠️ 必须**先关文件再删**：Windows 不允许删除仍处于打开状态的文件，
    # 顺序反了会静默删不掉，锁文件残留 → 后续永远取不到锁。
    if backend != "flock":
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


@contextmanager
def guard(reason: str, timeout: float = 30.0):
    """取得"登录/续期"独占权。

    yields True  → 拿到了，可以动凭据
    yields False → 别人正在动，**本次跳过**（不要重复续期/重登）

    机制故障一律 yield True（fail-open）。
    """
    if not _lock_enabled():
        yield True
        return

    fd = None
    got = True
    try:
        fd, got = _acquire(timeout)
    except Exception:
        # 锁坏了不能让续期停摆
        yield True
        return

    if got:
        _write_note(fd, reason)
    try:
        yield got
    finally:
        if got:
            _release(fd)


def note_credentials_written(reason: str) -> int:
    """凭据已写入 → 递增 session 版本（供其它模块判断缓存是否过期）。

    永不抛异常：这是审计/通知，不是关键路径。
    """
    n = version() + 1
    try:
        VERSION_FILE.write_text(json.dumps(
            {"version": n, "at": time.time(), "reason": reason},
            ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return n


def version() -> int:
    try:
        return int(json.loads(VERSION_FILE.read_text(encoding="utf-8")).get("version", 0))
    except Exception:
        return 0
