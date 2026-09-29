"""P12-AJ：**`/api/auth/token` 不是续期接口**，它的 401 不能让 bot 自杀重启。

## 2026-09-29 实测（推翻了 09-27 的旧结论）

| 观察 | 结果 |
|---|---|
| 会话新鲜时（剩 3434 秒）调 `/api/auth/token` | 200，返回的 access_token 与本地**逐字节相同** |
| 日志里 `SKIP: token 未变化` | **1220 次**（平台回的永远是手里那个） |
| 日志里 `OK: token 续期成功` | 689 次 —— 只是"抄到了服务端较新的 token"，不是我们续的 |
| 浏览器身份访问（加载页面 7 秒） | cookie 与 refresh_token **零轮换**，`expires_at` 一模一样 |
| access_token 一过期（登录后 1 小时） | 该接口立刻 **401「未授权」** |
| 由此写 `token.emergency` | **80 次** → bot 每小时"主动退出"重启一次 → 又触发一次真登录 |
| 同期业务调用报 unauthorized | **全程只有 6 次** |

也就是说：**"续期失效"很大程度是误判 + 自伤** —— 我们每小时把自己干掉重启，
还顺带每小时做一次账号密码登录（风控/迷宫滑块就是这么攒出来的）。

## 修法（本文件钉住）

1. 401 之后**先用业务调用探一次**：业务正常 ⇒ 只记 `NOTE:`，不写 emergency、不重启；
   业务也 401 ⇒ 记 `SESSION_DEAD:` 才触发重登。
2. 这条路径的日志**不许出现 "FAIL"** —— 巡检的 `check_renew()` 按它告警，写了就是误报源。
3. 巡检与 Socket.IO 的"会话是否失效"改看业务探针结果（`data/session_state.json`），
   **不再看 access_token 的 `expires_at`**。
"""
import base64
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import check_bot_health as H                                  # noqa: E402
import env_config                                             # noqa: E402
import renew_token as R                                       # noqa: E402


@pytest.fixture
def rt(dbdir, monkeypatch):
    """把 renew_token 的所有落盘路径挪到 tmp（别写进仓库）。"""
    monkeypatch.setattr(R, "ROOT", dbdir)
    monkeypatch.setattr(R, "FAIL_STATE_FILE", dbdir / "fail.json")
    monkeypatch.setattr(R, "EMERGENCY_FILE", dbdir / "token.emergency")
    monkeypatch.setattr(R, "EMERGENCY_STATE_FILE", dbdir / "emerg.json")
    monkeypatch.setattr(R, "BROWSER_BACKOFF_FILE", dbdir / "backoff.json")
    monkeypatch.setattr(R, "SESSION_STATE_FILE", dbdir / "session_state.json")
    return dbdir


def _about_to_expire():
    """一个 60 秒后过期的会话（走"该续期了"那条分支）。"""
    return {"value": "base64-xxx"}, {"access_token": "old", "expires_at": time.time() + 60}


# ---------------------------------------------------------------- 1. 探针本身

def test_probe_maps_three_outcomes(monkeypatch):
    import dzmm
    monkeypatch.setattr(R, "_probe_cid", lambda: "cid-1")
    monkeypatch.setattr(dzmm, "api", lambda *a, **k: {"messages": []})
    assert R._api_probe() == "ok"
    for msg in ("tRPC chatroom.getMessages: 未授权", "Unauthorized", "HTTP 401"):
        def boom(*a, **k):
            raise RuntimeError(msg)
        monkeypatch.setattr(dzmm, "api", boom)
        assert R._api_probe() == "unauth", msg

    def net_err(*a, **k):
        raise RuntimeError("网络超时")
    monkeypatch.setattr(dzmm, "api", net_err)
    assert R._api_probe() == "error"


def test_probe_needs_a_real_group_cid(monkeypatch, dbdir):
    monkeypatch.setattr(R, "ROOT", dbdir)
    # 没有 config.json → 用已知认证群兜底
    assert R._probe_cid() == "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
    # 有 config.json → 取第一个启用的群
    (dbdir / "config").mkdir()
    (dbdir / "config" / "config.json").write_text(json.dumps(
        {"groups": [{"cid": "cid-a", "enabled": True}, {"cid": "cid-b"}]}),
        encoding="utf-8")
    monkeypatch.setattr(R, "ROOT", dbdir)
    assert R._probe_cid() == "cid-a"


# ---------------------------------------------------------------- 2. 401 不再自伤

def _run(rt, monkeypatch, probe):
    monkeypatch.setattr(R, "read_token_cookie", _about_to_expire)
    monkeypatch.setattr(R, "get_new_token", lambda: {"error": "未授权"})
    monkeypatch.setattr(R, "_api_probe", lambda: probe)
    rc = R._renew_locked()
    log = (rt / "renew_token.log").read_text(encoding="utf-8") if (rt / "renew_token.log").exists() else ""
    return rc, log, (rt / "token.emergency").exists()


def test_readonly_401_with_healthy_business_is_not_a_failure(rt, monkeypatch):
    """核心：只读接口 401 + 业务正常 ⇒ 不写 emergency、不重启、**日志里不许有 FAIL**。"""
    rc, log, emerg = _run(rt, monkeypatch, "ok")
    assert rc == 0, "会话没死就该当成功返回，让巡检别再告警"
    assert not emerg, "绝不能因为只读接口 401 就写 emergency（那会让 bot 每小时自杀）"
    assert "FAIL" not in log, "这行日志会被 check_renew() 当告警，写了就是误报源"
    assert "NOTE" in log and "会话没死" in log
    assert json.loads((rt / "session_state.json").read_text(encoding="utf-8"))["alive"] is True


def test_readonly_401_with_dead_business_triggers_relogin(rt, monkeypatch):
    rc, log, emerg = _run(rt, monkeypatch, "unauth")
    assert rc == 1
    assert emerg, "业务调用也 401 才是真的会话失效，这时才该重登"
    assert "SESSION_DEAD" in log
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st["alive"] is False and st["probe"] == "unauth"


def test_probe_error_does_not_decide(rt, monkeypatch):
    """探针自己失败（网络抖动）⇒ 不下结论、不重登 —— 下一轮再说。"""
    rc, log, emerg = _run(rt, monkeypatch, "error")
    assert rc == 1 and not emerg
    assert "不下结论" in log


def test_healthy_token_writes_session_state(rt, monkeypatch):
    """token 还很新鲜时也要记一笔"会话活着"，否则巡检 30 分钟后就不敢下结论。"""
    monkeypatch.setattr(R, "read_token_cookie",
                        lambda: ({"value": "x"}, {"access_token": "a",
                                                 "expires_at": time.time() + 3000}))
    assert R._renew_locked() == 0
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st["alive"] is True


# ---------------------------------------------------------------- 3. 巡检改判

def _state(tmp, alive, ago_min=1.0):
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "session_state.json").write_text(json.dumps(
        {"at": time.time() - ago_min * 60, "alive": alive, "probe": "ok" if alive else "unauth"}),
        encoding="utf-8")


def test_health_uses_the_probe_not_the_access_expiry(dbdir, monkeypatch):
    monkeypatch.setattr(env_config, "data_dir", lambda: dbdir)
    _state(dbdir, True)
    assert H.check_session_state()["dead"] is False
    _state(dbdir, False)
    d = H.check_session_state()
    assert d["dead"] is True and d["probe"] == "unauth"


def test_health_ignores_a_stale_probe_file(dbdir, monkeypatch):
    """文件太旧（>30 分钟）就不下结论 —— 免得重启初期拿旧结论误报。"""
    monkeypatch.setattr(env_config, "data_dir", lambda: dbdir)
    _state(dbdir, False, ago_min=45)
    assert H.check_session_state()["dead"] is False


def test_health_renew_check_ignores_note_lines(dbdir, monkeypatch):
    """只读接口 401 现在记 `NOTE:` —— 巡检不能再把它当"续期脚本失败"。"""
    monkeypatch.setattr(H, "ROOT", dbdir)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log = dbdir / "renew_token.log"
    log.write_text(stamp + " NOTE: 只读接口未授权（未授权），但业务调用正常 → 会话没死，不重登\n",
                   encoding="utf-8")
    assert H.check_renew() is False
    log.write_text(stamp + " SESSION_DEAD: 业务调用也未授权（会话真失效）\n", encoding="utf-8")
    assert H.check_renew() is True
    log.write_text(stamp + " FAIL: /api/auth/token 请求异常: boom\n", encoding="utf-8")
    assert H.check_renew() is True


# ---------------------------------------------------------------- 4. SIO 退避

def test_sio_backoff_uses_the_probe_not_the_access_expiry(dbdir, monkeypatch):
    """access_token 过期不再等于"会话死" —— 否则 Socket.IO 会把重连退避顶到 1 小时。"""
    monkeypatch.setattr(env_config, "data_dir", lambda: dbdir)
    import sio_subscribe
    sub = sio_subscribe.SioSubscriber(["cid-1"])
    assert sub._token_stale() is False, "没有探针结论时不该判 stale"
    _state(dbdir, True)
    assert sub._token_stale() is False
    _state(dbdir, False)
    assert sub._token_stale() is True
    _state(dbdir, False, ago_min=45)
    assert sub._token_stale() is False, "结论太旧就该重新探测"


def test_sio_no_longer_reads_the_jwt_expiry():
    """源码级：`_token_stale` 不许再解 JWT 的 expires_at（那会把重连退避顶到 1 小时）。"""
    src = (ROOT / "sio_subscribe.py").read_text(encoding="utf-8")
    i = src.index("def _token_stale")
    seg = src[i:i + 1800]
    assert "session_state.json" in seg
    assert "b64decode" not in seg, "旧实现是解 JWT 里的 expires_at，必须彻底删掉"
    assert 'get_cookie("sb-rls-auth-token")' not in seg


def test_renew_token_log_vocabulary_stays_alert_safe():
    """源码级守卫：未授权那一段的日志不许用 FAIL/失败 这种会被巡检抓的词。"""
    src = (ROOT / "renew_token.py").read_text(encoding="utf-8")
    i = src.index("别急着判死")
    seg = src[i:i + 1400]
    for bad in ('log("FAIL', "log('FAIL"):
        assert bad not in seg, "这段里出现 FAIL 就会让巡检每小时误报"
    assert 'log("NOTE' in seg and 'log("SESSION_DEAD' in seg
