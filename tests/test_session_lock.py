"""会话锁（唯一登录者）单测 —— P5。

覆盖：互斥、超时拒绝、释放后可再取、开关关闭、机制故障时 fail-open、
版本号递增，以及两个后端（fcntl flock / O_EXCL 回退）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime import session  # noqa: E402


@pytest.fixture
def lockenv(dbdir, monkeypatch):
    """把锁文件与版本文件指到临时目录，避免污染仓库。"""
    monkeypatch.setattr(session, "LOCK_FILE", dbdir / ".session.lock")
    monkeypatch.setattr(session, "VERSION_FILE", dbdir / ".session_version.json")
    monkeypatch.delenv("DZMM_SESSION_LOCK", raising=False)
    monkeypatch.setattr(session, "FORCED_BACKEND", None)
    yield monkeypatch


# ---------------------------------------------------------------- 基本互斥

@pytest.mark.parametrize("backend", ["excl", "flock"])
def test_guard_acquires_when_free(lockenv, backend):
    if backend == "flock" and session._fcntl is None:
        pytest.skip("本平台无 fcntl")
    lockenv.setattr(session, "FORCED_BACKEND", backend)
    with session.guard("t1", timeout=0.5) as got:
        assert got is True


@pytest.mark.parametrize("backend", ["excl", "flock"])
def test_second_guard_is_refused_while_held(lockenv, backend):
    """互斥的核心：别人持锁时**不许重复登录/续期**。"""
    if backend == "flock" and session._fcntl is None:
        pytest.skip("本平台无 fcntl")
    lockenv.setattr(session, "FORCED_BACKEND", backend)
    with session.guard("first", timeout=0.5) as got1:
        assert got1 is True
        with session.guard("second", timeout=0.2) as got2:
            assert got2 is False


@pytest.mark.parametrize("backend", ["excl", "flock"])
def test_guard_releases_after_exit(lockenv, backend):
    if backend == "flock" and session._fcntl is None:
        pytest.skip("本平台无 fcntl")
    lockenv.setattr(session, "FORCED_BACKEND", backend)
    with session.guard("a", timeout=0.5) as got:
        assert got is True
    with session.guard("b", timeout=0.5) as got:
        assert got is True


def test_guard_releases_even_on_exception(lockenv):
    lockenv.setattr(session, "FORCED_BACKEND", "excl")
    with pytest.raises(RuntimeError):
        with session.guard("boom", timeout=0.5):
            raise RuntimeError("x")
    with session.guard("after", timeout=0.5) as got:
        assert got is True


# ---------------------------------------------------------------- 开关与 fail-open

def test_disabled_switch_always_grants(lockenv):
    lockenv.setenv("DZMM_SESSION_LOCK", "off")
    with session.guard("a", timeout=0.2) as got1:
        assert got1 is True
        with session.guard("b", timeout=0.2) as got2:
            assert got2 is True          # 关闭后完全不互斥


@pytest.mark.parametrize("val", ["off", "0", "no", "false", "OFF"])
def test_disabled_switch_values(lockenv, val):
    lockenv.setenv("DZMM_SESSION_LOCK", val)
    with session.guard("a", timeout=0.1) as got:
        assert got is True


def test_mechanism_failure_is_fail_open(lockenv):
    """锁机制坏掉时**必须放行** —— 否则会把"偶发 401"升级成"必然掉线"。"""
    lockenv.setattr(session, "LOCK_FILE", Path("Z:/definitely/not/here/x.lock"))
    with session.guard("broken", timeout=0.2) as got:
        assert got is True


# ---------------------------------------------------------------- 版本号

def test_note_credentials_written_increments(lockenv):
    assert session.version() == 0
    assert session.note_credentials_written("test") == 1
    assert session.version() == 1
    assert session.note_credentials_written("test2") == 2
    assert session.version() == 2


def test_version_reads_zero_when_missing(lockenv):
    assert session.version() == 0


def test_note_never_raises_even_if_path_broken(lockenv):
    lockenv.setattr(session, "VERSION_FILE", Path("Z:/nope/x.json"))
    assert session.note_credentials_written("x") == 1     # 不抛异常


# ---------------------------------------------------------------- 接线守卫

def test_wrapper_module_exposes_api():
    import session_guard
    assert callable(session_guard.guard)
    assert callable(session_guard.note_credentials_written)
    assert callable(session_guard.version)


def test_token_store_is_the_only_credential_writer():
    """`save_cookies` 必须触发版本递增（它是唯一的凭据写入点）。"""
    import inspect

    import token_store
    src = inspect.getsource(token_store.save_cookies)
    assert "_note_written" in src


def test_login_scripts_were_split_so_lock_can_wrap_them():
    """三个登录脚本必须把真正的动作拆成 `_xxx_locked`，锁才有地方套。"""
    import auto_recover
    import headless_relogin
    import renew_token
    assert callable(renew_token._renew_locked)
    assert callable(headless_relogin._do_login)
    assert callable(auto_recover._recover_locked)


def test_login_mains_use_guard():
    import inspect

    import auto_recover
    import headless_relogin
    import renew_token
    for mod in (renew_token, headless_relogin, auto_recover):
        src = inspect.getsource(mod.main)
        assert "guard(" in src, mod.__name__
