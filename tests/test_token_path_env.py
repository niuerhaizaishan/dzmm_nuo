"""S1 Step 2-1：凭据路径环境变量覆盖（DZMM_TOKEN_FILE / DZMM_LOGIN_FILE）。

钉两件事：
1. 不设变量时，路径与老版本**逐字一致**（零回归的钉子）；
2. 设了变量时，token/login 读口都指向新路径（双写期面板与续期写同一份文件）。

注意：用 `importlib.reload` 后必须在用例内恢复默认（delenv + reload），
否则会泄漏到其它 import 了这些模块的测试。
"""
import importlib
from pathlib import Path

import token_store


def _default_token_path(ts) -> Path:
    return Path(ts.__file__).parent / ".dzmm_token.json"


def test_token_file_default_unchanged(monkeypatch):
    """不设 DZMM_TOKEN_FILE 时，路径必须与老版本逐字一致（零回归）。"""
    monkeypatch.delenv("DZMM_TOKEN_FILE", raising=False)
    ts = importlib.reload(token_store)
    assert ts.TOKEN_FILE == _default_token_path(ts)


def test_token_file_env_override(monkeypatch, dbdir):
    """设了 DZMM_TOKEN_FILE 时，唯一凭据写入点指向新路径。"""
    fake = Path(dbdir) / "token.json"
    monkeypatch.setenv("DZMM_TOKEN_FILE", str(fake))
    ts = importlib.reload(token_store)
    assert ts.TOKEN_FILE == fake
    # 恢复默认，避免泄漏
    monkeypatch.delenv("DZMM_TOKEN_FILE", raising=False)
    assert importlib.reload(token_store).TOKEN_FILE == _default_token_path(ts)


def test_login_file_env_override(monkeypatch, dbdir):
    """headless_relogin（容器内重登链）的 CONFIG 支持 DZMM_LOGIN_FILE。"""
    import headless_relogin

    fake = Path(dbdir) / "login.json"
    monkeypatch.setenv("DZMM_LOGIN_FILE", str(fake))
    hl = importlib.reload(headless_relogin)
    assert hl.CONFIG == fake
    monkeypatch.delenv("DZMM_LOGIN_FILE", raising=False)
    assert importlib.reload(hl).CONFIG == Path(hl.__file__).parent / ".dzmm_login.json"


def test_monitor_recover_prefers_env_then_falls_back(monkeypatch):
    """宿主守护脚本：env 优先；无 env 且 secrets/ 不存在（本机）→ 老路径。"""
    import monitor_recover

    monkeypatch.setenv("DZMM_TOKEN_FILE", "X:/tok.json")
    mr = importlib.reload(monitor_recover)
    assert mr.TOKEN_FILE == "X:/tok.json"

    monkeypatch.delenv("DZMM_TOKEN_FILE", raising=False)
    mr = importlib.reload(monitor_recover)
    # 本机（Windows）没有 /opt/dzmm，应落到老路径
    assert mr.TOKEN_FILE == "/opt/dzmm/.dzmm_token.json"
