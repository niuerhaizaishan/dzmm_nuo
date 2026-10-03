"""会话失效恢复链的回归测试（P0-2 / 冷却 / 进程级去重）—— 2026-10-01。

## 现场

21:29 掉线 15 分钟。会话真死时**第一个**症状是"读不到消息"，而那条路径原先只打
一行日志就 `msgs = []`，既不判 unauthorized、也不拉起 `auto_recover.py`。
证据（bot.log，含掉线窗口）：`平台读消息失败`=16 而 `UNAUTHORIZED`=0、
`触发自动登录恢复`=0；对照正常时期 bot.log.1 是 865 / 86 / 81。

## 本文件钉四件事

1. `_is_unauth_error` 认线上真实原文，且**不误判**普通网络错误；
2. 读消息失败路径**必须**接到统一的 `_on_unauthorized`（源码级接线）；
3. `_spawn_auto_recover` 有**进程级**去重 —— 按群并行下 4 个实例会同时读失败，
   不去重就是"连续重启 bot 4 次"（`auto_recover._recover_locked()` 末尾无条件
   `restart_bot()`）；
4. `_try_password_relogin` 撞 SKIP（子进程根本没开始登录）时**不消耗** 30 分钟冷却。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                    # noqa: E402

import renew_token as R                                          # noqa: E402
from runtime import mainloop as M                                # noqa: E402

MAINLOOP_SRC = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1. 判定口径

def test_is_unauth_error_matches_real_production_strings():
    """必须认线上真实原文 —— 第一条是 2026-10-01 21:30 日志里逐字抄的。"""
    assert M._is_unauth_error(
        Exception("tRPC chatroom.getMessages: Invalid authentication credentials"))
    assert M._is_unauth_error("unauthorized")
    assert M._is_unauth_error("HTTP 401 未授权")


def test_is_unauth_error_ignores_ordinary_network_errors():
    """别把普通抖动当会话失效 —— 那等于拿"重启+重登"去治一次网络超时。"""
    assert not M._is_unauth_error(Exception("HTTPSConnectionPool: Read timed out"))
    assert not M._is_unauth_error(Exception("tRPC chatroom.getMessages: 500"))
    assert not M._is_unauth_error(Exception("proxy connect ECONNREFUSED"))


# ---------------------------------------------------------------- 2. 接线

def test_read_failure_path_handles_unauthorized():
    """源码级：`平台读消息失败` 的 except 里必须判 unauthorized 并交给统一处理。

    ⚠️ 锚点必须用**那行日志语句**而不是裸的"平台读消息失败" —— 后者在文件顶部的
    P0-2 注释里也出现过，`index()` 会命中注释（本测试第一次跑就是这么红的）。
    """
    i = MAINLOOP_SRC.index('log("⚠️ [{}] 平台读消息失败：{}"')
    seg = MAINLOOP_SRC[i:i + 1500]
    assert "_is_unauth_error(_re)" in seg, "读消息失败必须判 unauthorized（P0-2）"
    assert "_on_unauthorized(" in seg, "读消息失败必须走统一的 unauthorized 处理"
    # 必须设冷却：否则本轮末尾的"恢复正常"分支会把 `_recovering` 当轮清零 ⇒ 重复拉起
    assert "captcha_cooldown_until" in seg, "不设冷却 ⇒ _recovering 当轮被重置"


def test_both_paths_share_one_unauth_handler():
    """两处口径必须只有一份实现（别再漂移）。"""
    assert MAINLOOP_SRC.count("async def _on_unauthorized") == 1
    assert MAINLOOP_SRC.count("await _on_unauthorized(") == 2, "读路径 + 生成路径各一处"


# ---------------------------------------------------------------- 3. 进程级去重

class _FakeBot:
    HERMES_PY = "python"
    ROOT = "."


def _patch_popen(monkeypatch):
    calls = []
    monkeypatch.setattr(M.subprocess, "Popen", lambda *a, **k: calls.append(a))
    return calls


def test_spawn_auto_recover_is_debounced(monkeypatch):
    """4 个并行实例同时读失败时，同一进程只许拉起一个 auto_recover。"""
    monkeypatch.setattr(M, "_auto_recover_last_spawn", 0.0)
    monkeypatch.setattr(M, "AUTO_RECOVER_MIN_GAP", 300.0)
    calls = _patch_popen(monkeypatch)
    assert M._spawn_auto_recover(_FakeBot, "第一次") is True
    assert M._spawn_auto_recover(_FakeBot, "第二次") is False
    assert M._spawn_auto_recover(_FakeBot, "第三次") is False
    assert len(calls) == 1, "同一进程内只许拉起一个 auto_recover"


def test_spawn_auto_recover_allows_retry_after_gap(monkeypatch):
    """间隔够了要能再拉一次 —— 去重不能变成"只救一次"。"""
    monkeypatch.setattr(M, "_auto_recover_last_spawn", 0.0)
    monkeypatch.setattr(M, "AUTO_RECOVER_MIN_GAP", 0.0)
    calls = _patch_popen(monkeypatch)
    assert M._spawn_auto_recover(_FakeBot, "a") is True
    assert M._spawn_auto_recover(_FakeBot, "b") is True
    assert len(calls) == 2


# ---------------------------------------------------------------- 4. 冷却只算真尝试

@pytest.fixture
def rt(dbdir, monkeypatch):
    """把 renew_token 的落盘路径挪到 tmp（别写进仓库）。"""
    monkeypatch.setattr(R, "ROOT", dbdir)
    monkeypatch.setattr(R, "RELOGIN_STATE_FILE", dbdir / "relogin.json", raising=False)
    monkeypatch.setattr(R, "FAIL_STATE_FILE", dbdir / "fail.json")
    monkeypatch.setattr(R, "EMERGENCY_STATE_FILE", dbdir / "emerg.json")
    # ⚠️ 必须钉住 `log`：它带 `to_stdout=True` 时会**真的去发微信**。
    monkeypatch.setattr(R, "log", lambda *a, **k: None)
    return dbdir


class _P:
    def __init__(self, stdout="", returncode=1):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _prep(monkeypatch, stdout, returncode=1):
    """把重登的前置条件全部置为"可以试"，并记录 `_stamp_relogin` 是否被调用。"""
    monkeypatch.setattr(R, "_unverified_min", lambda: 999.0)
    monkeypatch.setattr(R, "_relogin_allowed", lambda: True)
    stamped = []
    monkeypatch.setattr(R, "_stamp_relogin", lambda: stamped.append(1))
    monkeypatch.setattr(R.subprocess, "run",
                        lambda *a, **k: _P(stdout=stdout, returncode=returncode))
    return stamped


def test_skip_does_not_consume_relogin_cooldown(rt, monkeypatch):
    """子进程被锁挡在门外（打印 SKIP）⇒ 没真登录 ⇒ 不许吃掉 30 分钟冷却。

    这正是 2026-10-01 21:34 的形态：SKIP 把冷却打到 22:04，
    21:42 的第二次尝试直接被"冷却期内"挡掉。
    """
    stamped = _prep(monkeypatch, "SKIP: 另一个进程正在续期/重登，放弃本次登录\n")
    assert R._try_password_relogin() == "skip"
    assert stamped == [], "零工作的尝试不该消耗恢复窗口"


def test_real_attempt_consumes_relogin_cooldown(rt, monkeypatch):
    """真试过（子进程开始登录了，只是没成）⇒ 必须计冷却，别把风控刷起来。"""
    stamped = _prep(monkeypatch, "token 失效，开始 headless 登录...\n", returncode=1)
    assert R._try_password_relogin() == "fail"
    assert stamped == [1], "真尝试过就要计冷却"


def test_child_gets_parent_lock_env(rt, monkeypatch):
    """P0：派生子进程时必须带上"父进程已持锁"的声明（否则必然 180s 后 SKIP）。"""
    seen = {}

    def _fake_run(cmd, **kw):
        seen.update(kw.get("env") or {})
        return _P(stdout="SKIP: x\n")

    monkeypatch.setattr(R, "_unverified_min", lambda: 999.0)
    monkeypatch.setattr(R, "_relogin_allowed", lambda: True)
    monkeypatch.setattr(R, "_stamp_relogin", lambda: None)
    monkeypatch.setattr(R.subprocess, "run", _fake_run)
    R._try_password_relogin()
    assert seen.get("DZMM_RELOGIN_UNDER_PARENT_LOCK") == "1"


def test_backoff_log_messages_use_the_constant():
    """退避时长只有**一个**真源：`BROWSER_BACKOFF_SEC`。

    原先两处文案手写"30 分钟"而常量是 3600 秒（1 小时）—— 21:29 的排障就被它
    带偏过（我当时据此以为只退避 30 分钟）。手写数字会再次漂移，所以钉住。
    """
    src = (ROOT / "renew_token.py").read_text(encoding="utf-8")
    assert "退避 30 分钟" not in src, "不许再手写 30 分钟（常量是 3600 秒）"
    assert "30 分钟内已失败过" not in src, "同上"
    assert src.count("BROWSER_BACKOFF_SEC // 60") >= 2, "文案应插值常量"
