"""P12-BG：会话真死时，续期要**升级到密码重登**（不是只写 emergency 等重启）。

## 现场（屋主 2026-09-30："妹子群怎么不自动踢人了？"）

```
12:23:05  SESSION_DEAD: 业务调用也未授权（会话真失效），写 emergency 触发重登
12:23~13:13  ⚠️ 平台读消息失败：Invalid authentication credentials（每 10 分钟一次）
13:12     用户c9da03 加入了群聊        ← 她根本没看见 ⇒ 没筛查、没踢人
13:23      又一轮失败 → probe: unauth → 再次 emergency 重启
13:27     人工在容器里跑 headless_relogin.py → 会话立刻恢复
```

**缺口**：`renew_token`（每 5 分钟）只做"浏览器刷新"，而浏览器刷新**救不活已死的会话**；
它写的 `token.emergency` 只让 **bot.py 进程**重启，而 entrypoint 里唯一的密码登录
`headless_relogin.py` **只在容器启动时跑** ⇒ 没人再登 ⇒ 瞎了 64 分钟，全靠人工。

## 升级链（现在）

    会话真死（unauth）或连续 RELOGIN_AFTER_MIN 分钟没验证通过
      → 浏览器刷新（现状，能救就救）
      → **自动跑 headless_relogin.py 密码重登**（30 分钟最多一次）
          · 成功 + 业务探针 ok ⇒ 清退避、写会话 ok、**不重启 bot**（保住常驻页面/出站队列）
          · 撞验证码 ⇒ headless_relogin 自己拉 noVNC 并发微信（这条它本来就有）
      → 都不行 ⇒ 落回 emergency 重启（原有兜底）
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                    # noqa: E402

import renew_token as R                                          # noqa: E402

SRC = (ROOT / "renew_token.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 夹具

@pytest.fixture
def env():
    """把三个落盘文件指到一个临时目录（沙箱下 `tmp_path` 会 PermissionError）。"""
    d = ROOT / ".pytest-escalation"
    d.mkdir(exist_ok=True)
    state = d / "relogin_state.json"
    backoff = d / "renew_backoff.json"
    session = d / "session_state.json"
    for f in (state, backoff, session):
        f.unlink(missing_ok=True)
    return {"dir": d, "state": state, "backoff": backoff, "session": session}


def _wire(monkeypatch, env, bad_min=30.0):
    monkeypatch.setattr(R, "RELOGIN_STATE_FILE", env["state"], raising=False)
    monkeypatch.setattr(R, "BROWSER_BACKOFF_FILE", env["backoff"], raising=False)
    monkeypatch.setattr(R, "SESSION_STATE_FILE", env["session"], raising=False)
    monkeypatch.setattr(R, "env_config", type("E", (), {
        "renew_backoff_file": staticmethod(lambda: env["backoff"]),
        "data_dir": staticmethod(lambda: env["dir"]),
    }), raising=False)
    monkeypatch.setattr(R, "_unverified_min", lambda: bad_min, raising=False)
    logged = []
    monkeypatch.setattr(R, "log", lambda m, to_stdout=False: logged.append(m), raising=False)
    return logged


def _fake_run(rc=0, out="OK: headless 登录 + 写回 token JSON 成功", err=""):
    def _run(*a, **kw):
        return subprocess.CompletedProcess(a[0] if a else [], rc, out, err)
    return _run


# ---------------------------------------------------------------- ① 升级链行为

def test_successful_password_relogin_recovers_without_restart(monkeypatch, env):
    logged = _wire(monkeypatch, env)
    monkeypatch.setattr(R.subprocess, "run", _fake_run(0), raising=False)
    monkeypatch.setattr(R, "_api_probe", lambda: "ok", raising=False)
    written = []
    monkeypatch.setattr(R, "_write_session_state", lambda p: written.append(p), raising=False)
    env["backoff"].write_text("{}", encoding="utf-8")      # 先造一个退避文件
    assert R._try_password_relogin() is True
    assert any("ESCALATE" in m for m in logged), logged
    assert written == ["ok"], "恢复后要把会话写成 ok"
    assert not env["backoff"].exists(), "登录成功后必须清退避（否则 SIO 白等）"
    assert env["state"].exists(), "要留时间戳（冷却用）"
    assert json.loads(env["state"].read_text(encoding="utf-8"))["ts"] > 0


def test_relogin_failure_falls_back(monkeypatch, env):
    logged = _wire(monkeypatch, env)
    monkeypatch.setattr(R.subprocess, "run", _fake_run(1, "headless 登录失败（撞验证码）"),
                        raising=False)
    monkeypatch.setattr(R, "_api_probe", lambda: "unauth", raising=False)
    monkeypatch.setattr(R, "_write_session_state", lambda p: None, raising=False)
    assert R._try_password_relogin() is False
    assert any("没成" in m for m in logged), logged


def test_relogin_says_ok_but_probe_still_unauth_is_not_recovery(monkeypatch, env):
    """`headless_relogin` 说成功、业务探针还是 401 ⇒ 不算恢复（别自欺）。"""
    logged = _wire(monkeypatch, env)
    monkeypatch.setattr(R.subprocess, "run", _fake_run(0), raising=False)
    monkeypatch.setattr(R, "_api_probe", lambda: "unauth", raising=False)
    monkeypatch.setattr(R, "_write_session_state", lambda p: None, raising=False)
    assert R._try_password_relogin() is False
    assert any("不算恢复" in m for m in logged), logged


def test_cooldown_blocks_a_second_relogin(monkeypatch, env):
    logged = _wire(monkeypatch, env)
    env["state"].write_text(json.dumps({"ts": time.time() - 60}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(R, "subprocess",
                        type("S", (), {"run": staticmethod(
                            lambda *a, **kw: calls.append(a))}), raising=False)
    assert R._try_password_relogin() is False
    assert calls == [], "冷却期内不许再起浏览器"
    assert any("冷却" in m for m in logged), logged


def test_cooldown_expires(monkeypatch, env):
    _wire(monkeypatch, env)
    env["state"].write_text(json.dumps({"ts": time.time() - R.RELOGIN_COOLDOWN_SEC - 10}),
                            encoding="utf-8")
    assert R._relogin_allowed() is True


def test_fresh_session_is_not_escalated(monkeypatch, env):
    """会话才坏 1 分钟 ⇒ 先观察（一次抖动不该重登）。"""
    logged = _wire(monkeypatch, env, bad_min=1.0)
    calls = []
    monkeypatch.setattr(R, "subprocess",
                        type("S", (), {"run": staticmethod(
                            lambda *a, **kw: calls.append(a))}), raising=False)
    assert R._try_password_relogin() is False
    assert calls == []
    assert any("再自动重登" in m for m in logged), logged


def test_missing_login_script_is_reported(monkeypatch, env):
    logged = _wire(monkeypatch, env)
    monkeypatch.setattr(R, "LOGIN_SCRIPT", env["dir"] / "nope.py", raising=False)
    assert R._try_password_relogin() is False
    assert any("找不到" in m for m in logged), logged


def test_timeout_is_handled(monkeypatch, env):
    logged = _wire(monkeypatch, env)

    def _boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(R.subprocess, "run", _boom, raising=False)
    assert R._try_password_relogin() is False
    assert any("超时" in m for m in logged), logged


def test_clear_backoffs_is_safe_when_files_are_missing(monkeypatch, env):
    _wire(monkeypatch, env)
    R._clear_backoffs()                                       # 不该抛异常
    env["backoff"].write_text("{}", encoding="utf-8")
    R._clear_backoffs()
    assert not env["backoff"].exists()


# ---------------------------------------------------------------- ② 接线

def test_unauth_branch_escalates_before_emergency():
    # P12-BO 之后 `if probe == "unauth":` 有两处（退避分支里也有一处）⇒ 取最后一处
    i = SRC.rindex('if probe == "unauth":')
    seg = SRC[i:i + 2000]              # P12-BI 之后这里多了"先判墙"的一段
    assert "_try_password_relogin()" in seg, "unauth 必须先试密码重登"
    assert seg.index("_try_password_relogin()") < seg.index("_write_emergency()"), \
        "顺序必须是：先升级重登，再落回 emergency"


def test_unverified_timeout_branch_also_escalates():
    i = SRC.index("if _unverified_min() > RELOGIN_AFTER_MIN:")
    seg = SRC[i:i + 2400]              # P12-BI 的墙检查插在前面了
    assert "_browser_renew()" in seg and "_try_password_relogin()" in seg


def test_escalation_uses_the_real_login_script():
    assert R.LOGIN_SCRIPT.name == "headless_relogin.py"
    assert R.LOGIN_SCRIPT.exists(), "脚本必须在仓库里"
    # ⚠️ 2026-10-01 更正：这里原先写着「重登走的是**另一把锁**（headless_relogin），
    # 不会和自己持有的 renew_token 锁打架」—— **这个认知是错的，P0 就是它造成的**。
    # `guard()` 只有一把锁文件（runtime/session.py 的 LOCK_FILE = .session.lock），
    # `reason` 参数只是写进锁文件的一句备注。父进程持锁同步 spawn 子进程 ⇒ 子进程
    # 抢同一把锁 ⇒ 必然等满 headless_relogin 的 timeout=180 后 SKIP。
    login_src = R.LOGIN_SCRIPT.read_text(encoding="utf-8")
    assert 'guard("headless_relogin"' in login_src
    assert 'guard("renew_token"' not in login_src
    # 修复：父进程必须显式声明"锁已由我持有"，且重登脚本必须真的认这个变量。
    assert "DZMM_RELOGIN_UNDER_PARENT_LOCK" in SRC, \
        "renew_token 派生子进程时必须带上 DZMM_RELOGIN_UNDER_PARENT_LOCK"
    assert "DZMM_RELOGIN_UNDER_PARENT_LOCK" in login_src, \
        "headless_relogin 必须识别 DZMM_RELOGIN_UNDER_PARENT_LOCK（父进程已持锁）"
