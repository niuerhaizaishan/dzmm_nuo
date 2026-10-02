"""P12-BO：退避不许把整条升级链一起挡掉（2026-09-30 19:52 事故）+ S2/S6 + 杂项。

## 事故现场（屋主 20:25："检查运行状态"）

```
19:47  最后一次正常（session_state.ok_at）
19:52  会话失效（业务 401）+ 浏览器续期撞验证码 → 写 1 小时退避
19:52~ 每 5 分钟：撞验证码退避中，跳过续期        ← 死循环，谁都走不到
19:50–20:29  bot.log 35 条「平台读消息失败」（她读不到群）
20:26  人工跑密码重登 → 14 秒恢复
```

**根因**：`_renew_locked()` 里那段

```python
if not _browser_backoff_ok():
    log("撞验证码退避中，跳过续期"); _write_session_state("backoff"); return 1
```

挡在**业务探针、墙判定、密码重登之前** ⇒ 浏览器那条路撞一次验证码，整条升级链
（P12-BG 自动重登 + P12-BI 墙判定）**一小时不可达**。而当时 `首页=OK`（没有墙）、
业务 401（会话真死）—— 正是该重登的情况。而且心跳写 `backoff`，
巡检只认 `unauth` 为死 ⇒ **没有任何告警**。

## 修法

退避只挡"浏览器续期"这一次尝试；期间**仍打一次业务探针**：

| 探针 | 行为 |
|---|---|
| `ok` | 会话没死 → 写 ok + record_success，正常返回 |
| `unauth` | 写 **unauth**（不许再被 backoff 盖掉）→ 首页 ok 就**密码重登**；否则保留 unauth 让巡警报 |
| 其它 | 维持原样：写 backoff，跳过 |

顺带：`_home_status()` 把 **403 也算墙**（WAF 对不被接受的 UA 就是回 403）；
管理后台的 `ConnectionResetError` 噪声吞掉。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                    # noqa: E402

import bot                                                       # noqa: E402
import renew_token as R                                          # noqa: E402

SRC_R = (ROOT / "renew_token.py").read_text(encoding="utf-8")
SRC_B = (ROOT / "bot.py").read_text(encoding="utf-8")


def _stale_token():
    """快路径不会命中（已过期），但 cookie 读得出来。"""
    return "cookie-value", {"expires_at": time.time() - 10, "access_token": "old"}


@pytest.fixture
def wire(monkeypatch):
    """把凭据读、心跳写、告警都换成记录器，方便看"最后写了什么状态"。"""
    written = []
    monkeypatch.setattr(R, "read_token_cookie", _stale_token, raising=False)
    monkeypatch.setattr(R, "_write_session_state", lambda p: written.append(p), raising=False)
    monkeypatch.setattr(R, "record_success", lambda: None, raising=False)
    monkeypatch.setattr(R, "record_fail", lambda: False, raising=False)
    monkeypatch.setattr(R, "_browser_backoff_ok", lambda: False, raising=False)
    logged = []
    monkeypatch.setattr(R, "log", lambda m, to_stdout=False: logged.append(m), raising=False)
    return {"written": written, "logged": logged}


# ---------------------------------------------------------------- ① 退避期间仍能恢复

def test_backoff_with_healthy_session_writes_ok(monkeypatch, wire):
    monkeypatch.setattr(R, "_api_probe", lambda: "ok", raising=False)
    assert R._renew_locked() == 0
    assert wire["written"][-1] == "ok", wire["written"]


def test_backoff_with_dead_session_escalates_to_relogin(monkeypatch, wire):
    """**这就是 19:52 事故的形状**：退避 + 业务 401 + 首页正常 ⇒ 必须能走到重登。"""
    monkeypatch.setattr(R, "_api_probe", lambda: "unauth", raising=False)
    monkeypatch.setattr(R, "_home_status", lambda: "ok", raising=False)
    monkeypatch.setattr(R, "_try_password_relogin", lambda: True, raising=False)
    assert R._renew_locked() == 0, "重登成功就该判这轮成功"
    assert "ok" in wire["written"], wire["written"]


def test_backoff_dead_session_and_wall_keeps_unauth(monkeypatch, wire):
    """有墙时重登也过不去 ⇒ 不重登；但状态必须是 **unauth**（让巡警报），不是 backoff。"""
    monkeypatch.setattr(R, "_api_probe", lambda: "unauth", raising=False)
    monkeypatch.setattr(R, "_home_status", lambda: "wall", raising=False)
    called = []
    monkeypatch.setattr(R, "_try_password_relogin",
                        lambda: called.append(1) or True, raising=False)
    assert R._renew_locked() == 1
    assert called == [], "有墙时不该去重登（过不去）"
    assert wire["written"][-1] == "unauth", wire["written"]
    assert wire["written"].count("backoff") == 0, "backoff 不许盖掉 unauth"


def test_backoff_with_error_probe_keeps_backoff(monkeypatch, wire):
    monkeypatch.setattr(R, "_api_probe", lambda: "error", raising=False)
    assert R._renew_locked() == 1
    assert wire["written"][-1] == "backoff", wire["written"]


def test_backoff_branch_probes_before_returning():
    """源码级：退避分支里必须先 `_api_probe()`，且 `unauth` 分支排在 backoff 写入之前。"""
    i = SRC_R.index("if not _browser_backoff_ok():")
    seg = SRC_R[i:i + 2200]
    assert "_api_probe()" in seg
    assert seg.index("_api_probe()") < seg.index('_write_session_state("backoff")'), \
        "先探针，最后才轮到 backoff"
    assert "_try_password_relogin()" in seg
    assert seg.index('_write_session_state("unauth")') < seg.index(
        'log("撞验证码退避中，且业务调用未授权'), \
        "真死时要先把 unauth 写进去"


# ---------------------------------------------------------------- ② 403 也算墙

def test_home_status_counts_403_as_wall(monkeypatch):
    class _R:
        status_code = 403
        text = "<html>WAF</html>"

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _R(), raising=False)
    assert R._home_status() == "wall", "WAF 对不被接受的 UA 回 403，和 418 一样是墙"


def test_home_status_logs_unexpected_status(monkeypatch):
    class _R:
        status_code = 502
        text = "bad gateway"

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _R(), raising=False)
    logged = []
    monkeypatch.setattr(R, "log", lambda m, to_stdout=False: logged.append(m), raising=False)
    assert R._home_status() == "err"
    assert any("502" in m for m in logged), logged


# ---------------------------------------------------------------- ③ 抄回必须持锁（S2）

def test_harvest_takes_the_session_lock():
    # P3-7 起 `_harvest_token` 随 Sender 住 runtime/sender.py
    SRC_S = (ROOT / "runtime" / "sender.py").read_text(encoding="utf-8")
    i = SRC_S.index("async def _harvest_token(")
    seg = SRC_S[i:i + 1800]
    assert "session_guard" in seg and "guard(" in seg, "抄回必须和续期/重登抢同一把锁"
    assert 'timeout=0' in seg, "拿不到锁就跳过这一轮（机会主义行为）"
    assert "_harvest_token_locked()" in seg, "实体要拆出去（锁内调用）"


def test_harvest_skips_when_lock_is_held(monkeypatch):
    import asyncio
    import types

    s = bot.Sender.__new__(bot.Sender)
    s.ctx = types.SimpleNamespace(
        cookies=lambda: (_ for _ in ()).throw(AssertionError("不该读 cookie")))
    s._last_harvest = 0
    called = []

    class _Guard:
        def __enter__(self):
            return False                      # 别人拿着锁

        def __exit__(self, *a):
            return False

    fake = types.SimpleNamespace(guard=lambda reason, timeout=0: _Guard())
    monkeypatch.setitem(sys.modules, "session_guard", fake)
    monkeypatch.setattr(bot.Sender, "_harvest_token_locked",
                        lambda self: called.append(1), raising=False)
    assert asyncio.run(bot.Sender._harvest_token(s)) is False
    assert called == [], "拿不到锁就别动凭据"


# ---------------------------------------------------------------- ④ 杂项

def test_dashboard_swallows_client_reset():
    src = (ROOT / "admin" / "dashboard.py").read_text(encoding="utf-8")
    i = src.index("self.wfile.write(raw)")
    seg = src[max(0, i - 600):i + 200]
    assert "ConnectionResetError" in seg and "BrokenPipeError" in seg, \
        "客户端半路断开不该刷一整段 traceback"


def test_session_probe_reads_token_via_token_store():
    """我那个探针原来调 `env_config.token_file()`（不存在），④ 一直是"解析失败"。"""
    src = (ROOT / "tools" / "probes" / "probe_session_layers.py").read_text(encoding="utf-8")
    assert "token_store.TOKEN_FILE" in src
    assert "env_config.token_file()" not in src
