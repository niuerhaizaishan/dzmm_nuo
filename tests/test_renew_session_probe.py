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
    # P12-BP：**密码重登的冷却文件也要挪走**。它原先落在仓库的 `data/` 里，
    # 于是"同一次会话里跑第二遍全量测试"时会被上一遍写下的 ts 挡在冷却里，
    # 分支一变这条测试就红了（21:12 写、21:2x 复跑 ⇒ 假失败）。
    # 测试之间不许靠磁盘上的时间戳互相影响。
    monkeypatch.setattr(R, "RELOGIN_STATE_FILE", dbdir / "relogin.json", raising=False)
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


def test_probe_recognizes_the_real_platform_wording(monkeypatch):
    """P12-AS：平台在这条路上回的是 `Invalid authentication credentials`。

    2026-09-29 的事故就是这句：旧分类器只认 未授权/unauthorized/401 →
    判成"探针自己失败" → "不下结论、不重登" → 会话死了 142 分钟
    （15:49~18:11），茶水间彻底没反应，而巡检一路"正常"。
    """
    import dzmm
    monkeypatch.setattr(R, "_probe_cid", lambda: "cid-1")
    for msg in ("tRPC chatroom.getMessages: Invalid authentication credentials",
                "invalid authentication credentials",
                "Invalid credentials",
                "JWT expired",
                "token has expired",
                "invalid_grant",
                "not authenticated"):
        def boom(*a, **k):
            raise RuntimeError(msg)
        monkeypatch.setattr(dzmm, "api", boom)
        assert R._api_probe() == "unauth", msg
    # 真·网络错仍要判 error（别把什么都当未授权去重登）
    def net_err(*a, **k):
        raise RuntimeError("连接超时")
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
    # P12-BP：这两处**必须钉住**，否则这条测试会依赖真实网络与真实磁盘状态：
    #   · `_home_status()` 是 P12-BO 之后才进这条路径的（unauth 先判风控墙）——
    #     不钉住就是"本机能不能连上 dzmm 首页"决定测试红绿；
    #   · `_try_password_relogin()` 不钉住会真的去 spawn `headless_relogin.py`。
    # 墙那一条分支由 `test_renew_wall.py` 专门覆盖，这里只测"真死 → emergency"。
    monkeypatch.setattr(R, "_home_status", lambda: "ok", raising=False)
    monkeypatch.setattr(R, "_try_password_relogin", lambda: False, raising=False)
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
    """探针自己失败（网络抖动）⇒ 一刻钟内不下结论、不重登 —— 下一轮再说。"""
    rc, log, emerg = _run(rt, monkeypatch, "error")
    assert rc == 1 and not emerg
    assert "不下结论" in log


def test_undecided_forever_is_not_allowed(rt, monkeypatch):
    """P12-AS：**"不下结论"必须有期限**。

    事故复盘：探针一直 error（认不出平台的错误串）→ 一直"不下结论、不重登"
    → 会话死了 142 分钟。现在连续 `RELOGIN_AFTER_MIN` 分钟没验证通过就动手：
    先浏览器续期，再不行写 emergency 让 entrypoint 重登。
    """
    # 还没到期（bad_since 是 1 分钟前）→ 保持"不下结论"
    monkeypatch.setattr(R, "read_token_cookie", _about_to_expire)
    monkeypatch.setattr(R, "get_new_token", lambda: {"error": "未授权"})
    monkeypatch.setattr(R, "_api_probe", lambda: "error")
    monkeypatch.setattr(R, "_browser_renew", lambda: 1)   # 别在测试里真开浏览器
    R._write_session_state("error")
    assert R._unverified_min() < 1
    assert R._renew_locked() == 1
    assert not (rt / "token.emergency").exists(), "刚坏一分钟就重登是过度反应"

    # 已经坏了 25 分钟 → 必须动手
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    st["bad_since"] = time.time() - 25 * 60
    (rt / "session_state.json").write_text(json.dumps(st), encoding="utf-8")
    assert R._unverified_min() > R.RELOGIN_AFTER_MIN
    # ⚠️ 先清掉上一轮留下的**浏览器退避**：第 2.5 步的退避闸在最前面，
    # 它会先返回（"撞验证码退避中"），把后面那条"超期就动手"的路挡住。
    # 这是**有意的**：退避说明浏览器这条路刚失败过，而重登也要用浏览器 ——
    # 此时硬重登就是 P12-AJ 之前那种"每小时自杀"。这期间由巡检的
    # `session_unverified` 告警兜（20 分钟没验证通过就喊人）。
    (rt / "backoff.json").unlink(missing_ok=True)
    calls = []
    monkeypatch.setattr(R, "_browser_renew", lambda: (calls.append(1), 1)[1])
    rc = R._renew_locked()
    assert calls, "超过期限必须先试浏览器续期"
    assert rc == 1 and (rt / "token.emergency").exists(), "浏览器也不行就该触发重登"


def test_bad_since_is_cleared_once_healthy(rt, monkeypatch):
    """恢复 ok 之后计时器要清空，否则下一次抖动会立刻触发重登。"""
    R._write_session_state("error")
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    st["bad_since"] = time.time() - 60 * 60
    (rt / "session_state.json").write_text(json.dumps(st), encoding="utf-8")
    R._write_session_state("ok")
    st2 = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st2["bad_since"] is None and st2["ok_at"] > time.time() - 5
    assert R._unverified_min() == 0.0


def test_healthy_token_writes_session_state(rt, monkeypatch):
    """token 还很新鲜时也要记一笔"会话活着"，否则巡检 30 分钟后就不敢下结论。"""
    monkeypatch.setattr(R, "read_token_cookie",
                        lambda: ({"value": "x"}, {"access_token": "a",
                                                 "expires_at": time.time() + 3000}))
    called = []
    monkeypatch.setattr(R, "_browser_renew", lambda: called.append(1) or 0)
    assert R._renew_locked() == 0
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st["alive"] is True
    assert not called, "token 还新鲜时**不许**开浏览器（一小时一次就够了）"


# ------------------------------------------- 2.6 P12-AT：窗口内主动浏览器续期

def _cookie_value(d):
    return "base64-" + base64.b64encode(
        json.dumps(d, separators=(",", ":")).encode("utf-8")).decode()


def _same_token_setup(rt, monkeypatch, expires_in=300):
    """让 `_renew_locked` 走到 SKIP 分支：窗口内 + 回显接口原样返回旧 token。"""
    d = {"access_token": "old-at", "expires_at": time.time() + expires_in,
         "expires_in": expires_in, "refresh_token": "r" * 12}
    cookie = {"value": _cookie_value(d)}
    monkeypatch.setattr(R, "read_token_cookie", lambda: (cookie, dict(d)))
    monkeypatch.setattr(R, "get_new_token",
                        lambda: {"access_token": "old-at",
                                 "expires_at": d["expires_at"],
                                 "expires_in": expires_in})
    return d


def test_window_skip_does_not_open_a_browser(rt, monkeypatch):
    """P12-AT 实测结论：**窗口内提前开浏览器没用**，所以不许开。

    证据（2026-09-29 18:35 线上实测）：
        18:35:14 SKIP: 回显接口没给新 token
        18:35:14   → 窗口内，主动走浏览器续期
        18:35:20 SKIP: token 未变化，无需写回     ← 页面开了 6 秒，前端不给没过期的 token 刷新
    平台前端只在 token **临近/已经过期**时才用 refresh_token 换新的。
    真正有用的是下面 `test_401_tries_the_browser_before_relogin` 那条。
    """
    _same_token_setup(rt, monkeypatch)
    called = []
    monkeypatch.setattr(R, "_browser_renew", lambda: called.append(1) or 0)
    assert R._renew_locked() == 0
    assert not called, "token 还没过期就开浏览器 = 白开一次（实测证明拿不到新 token）"
    log = (rt / "renew_token.log").read_text(encoding="utf-8")
    assert "SKIP" in log and "FAIL" not in log


def _expired_401_setup(rt, monkeypatch):
    """401 场景：回显接口说未授权（access_token 过期），但会话本身可能还活着。"""
    d = {"access_token": "old-at", "expires_at": time.time() - 60,
         "expires_in": 3600, "refresh_token": "r" * 12}
    monkeypatch.setattr(R, "read_token_cookie", lambda: ({"value": _cookie_value(d)}, dict(d)))
    monkeypatch.setattr(R, "get_new_token", lambda: {"error": "未授权"})
    return d


def test_401_tries_the_browser_before_relogin(rt, monkeypatch):
    """P12-AT（屋主"要"）：401 的第一反应是**用浏览器刷一次**，刷得动就不重登、不重启。

    2026-09-29 09:46 的 `OK: 浏览器续期成功（expires_at=…）` 就是这条路 ——
    它比重登（写 emergency → bot 主动退出 → entrypoint 冷启动 + headless 登录）
    轻得多：常驻页面、出站队列、SIO 连接全都保住。
    """
    old = _expired_401_setup(rt, monkeypatch)
    new_d = dict(old)
    new_d["access_token"] = "new-at"          # 浏览器刷出来的新 token
    monkeypatch.setattr(R, "_browser_renew", lambda: 0)
    # ⚠️ 必须按"第几次调用"打桩：第一次（进门读 token）给旧的，刷完那次给新的。
    # 一律返回新的会让 `_before` 也变成新 token → 判成"没变"。第一版就是这么假失败的。
    seen = {"n": 0}

    def _rc():
        seen["n"] += 1
        use = new_d if seen["n"] > 1 else old
        return {"value": _cookie_value(use)}, dict(use)

    monkeypatch.setattr(R, "read_token_cookie", _rc)
    touched = []
    monkeypatch.setattr(R, "_api_probe", lambda: touched.append(1) or "ok")
    rc = R._renew_locked()
    assert rc == 0, "刷成功就该当成功返回"
    log = (rt / "renew_token.log").read_text(encoding="utf-8")
    assert "浏览器刷新成功" in log
    assert not touched, "刷成功了就不该再去问业务探针"
    assert not (rt / "token.emergency").exists(), "刷得动就不该重登"
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st["probe"] == "ok"


def test_401_falls_back_to_the_probe_when_browser_cannot_refresh(rt, monkeypatch):
    """浏览器刷不动 → 落回业务探针：业务正常就只记 NOTE，不重登。"""
    _expired_401_setup(rt, monkeypatch)
    monkeypatch.setattr(R, "_browser_renew", lambda: 1)     # 刷失败
    monkeypatch.setattr(R, "_api_probe", lambda: "ok")
    assert R._renew_locked() == 0
    log = (rt / "renew_token.log").read_text(encoding="utf-8")
    assert "浏览器刷新没成" in log and "NOTE" in log and "会话没死" in log
    assert not (rt / "token.emergency").exists()
    assert (rt / "backoff.json").exists(), "失败要退避，别每 5 分钟开一次浏览器"


# ------------------------------------------- 2.5 P12-AR：心跳必须每轮都刷新

def _main_with(rt, monkeypatch, rc, locked=None):
    """跑一遍 `R.main()`，把会话锁与真正的续期逻辑换掉。"""
    class _Guard:
        def __enter__(self_):
            return True

        def __exit__(self_, *a):
            return False

    import session_guard
    monkeypatch.setattr(session_guard, "guard", lambda *a, **k: _Guard())
    monkeypatch.setattr(R, "_renew_locked", locked or (lambda: rc))
    return R.main()


def test_successful_renewal_refreshes_the_heartbeat(rt, monkeypatch):
    """P12-AR 的核心：`SKIP`/`OK` 这两条真续期路径原先**不写心跳** ——
    于是每次续期窗口（token 剩余 ≤10 分钟那 10 分钟）心跳必然变旧，
    实测出现过"心跳 15.9 分钟"这种**健康但看着像停摆**的状态。"""
    (rt / "session_state.json").unlink(missing_ok=True)
    assert _main_with(rt, monkeypatch, 0) == 0
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st["alive"] is True and st["probe"] == "ok"
    assert (time.time() - st["at"]) < 10, "心跳必须是刚刚写的"


def test_dead_session_is_not_overwritten_by_the_heartbeat(rt, monkeypatch):
    """rc != 0（含 SESSION_DEAD）时**不许**把心跳刷成"活着"。"""
    def _dead():
        R._write_session_state("unauth")
        return 1
    assert _main_with(rt, monkeypatch, 1, locked=_dead) == 1
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st["probe"] == "unauth", "失败的一轮不能把结论覆盖成 ok"


def test_captcha_backoff_still_writes_a_heartbeat(rt, monkeypatch):
    """撞验证码退避期间：循环是活的，得留心跳，否则停摆告警会误报。

    ⚠️ P12-BO 之后退避期间还会打一次**业务探针**（不许把整条升级链一起挡掉）：
      · 探针"没结论/失败" → 写 `backoff`（本测试）
      · 探针说会话活着     → 写 `ok`（更好，见 `test_renew_backoff_escalation.py`）
    """
    monkeypatch.setattr(R, "read_token_cookie", _about_to_expire)
    monkeypatch.setattr(R, "_browser_backoff_ok", lambda: False)
    monkeypatch.setattr(R, "_api_probe", lambda: "error")     # 探针本身失败 ⇒ 不下结论
    assert R._renew_locked() == 1
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st["probe"] == "backoff" and st["alive"] is False


def test_lock_skip_still_writes_a_heartbeat(rt, monkeypatch):
    """抢不到会话锁（别人在重登）＝ 循环跑到了，也要留心跳，
    否则人工过验证码那段时间会被"停摆"告警误报。"""
    class _NoLock:
        def __enter__(self_):
            return False

        def __exit__(self_, *a):
            return False

    import session_guard
    monkeypatch.setattr(session_guard, "guard", lambda *a, **k: _NoLock())
    assert R.main() == 0
    st = json.loads((rt / "session_state.json").read_text(encoding="utf-8"))
    assert st["probe"] == "locked" and st["alive"] is False


def test_backoff_probe_is_not_treated_as_dead(dbdir, monkeypatch):
    """`backoff`/`error` 只是"这轮没验证"，绝不能被当成会话失效。

    这条钉住三处读者：巡检告警、SIO 退避。写宽了，一次验证码退避就会：
    巡检报"会话已失效" + SIO 把重连退避顶到 1 小时。
    """
    monkeypatch.setattr(env_config, "data_dir", lambda: dbdir)
    import sio_subscribe
    sub = sio_subscribe.SioSubscriber(["cid-1"])
    for probe in ("backoff", "error", "ok", "locked"):
        dbdir.mkdir(parents=True, exist_ok=True)
        (dbdir / "session_state.json").write_text(json.dumps(
            {"at": time.time(), "alive": probe == "ok", "probe": probe}),
            encoding="utf-8")
        assert H.check_session_state()["dead"] is False, probe
        assert sub._token_stale() is False, probe
    # 只有 unauth 才算死
    (dbdir / "session_state.json").write_text(json.dumps(
        {"at": time.time(), "alive": False, "probe": "unauth"}), encoding="utf-8")
    assert H.check_session_state()["dead"] is True
    assert sub._token_stale() is True


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


def test_health_flags_a_long_unverified_session(dbdir, monkeypatch):
    """P12-AS：**"连续没验证通过"必须告警** —— 今天那次宕机全程静默。

    探针一直 error（不是 unauth）⇒ `dead` 一直 False ⇒ 巡检一路"正常"，
    而用户那边已经两个多小时没回话。判据得从"死没死"扩成"多久没证明它活着"。
    """
    monkeypatch.setattr(env_config, "data_dir", lambda: dbdir)
    dbdir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    # 刚坏 3 分钟：只是"这轮没验证"，不报警
    (dbdir / "session_state.json").write_text(json.dumps(
        {"at": now, "alive": False, "probe": "error",
         "bad_since": now - 3 * 60}), encoding="utf-8")
    d = H.check_session_state()
    assert d["unverified"] is False and d["dead"] is False
    # 坏了 25 分钟：必须报警
    (dbdir / "session_state.json").write_text(json.dumps(
        {"at": now, "alive": False, "probe": "error",
         "bad_since": now - 25 * 60}), encoding="utf-8")
    d = H.check_session_state()
    assert d["unverified"] is True and d["unverified_min"] > 20
    # 恢复后要能自愈（bad_since 清空）
    (dbdir / "session_state.json").write_text(json.dumps(
        {"at": now, "alive": True, "probe": "ok", "bad_since": None}),
        encoding="utf-8")
    assert H.check_session_state()["unverified"] is False


def test_health_flags_a_stalled_renew_loop(dbdir, monkeypatch):
    """P12-AR：心跳过旧 = 续期循环停摆 —— 这条以前是**无告警**的。

    正常每 5 分钟刷新一次（`renew_token.main()` 每轮都写），
    所以 20 分钟没动静 = 连丢 4 轮，必须喊人；否则只能等 token 到期后
    "群里突然不回话"才发现。
    """
    monkeypatch.setattr(env_config, "data_dir", lambda: dbdir)
    _state(dbdir, True, ago_min=1)
    d = H.check_session_state()
    assert d["stale"] is False and d["dead"] is False
    _state(dbdir, True, ago_min=25)
    assert H.check_session_state()["stale"] is True
    # 文件不存在（首次部署/换机）不算停摆：那种情况由 check_renew 的 FAIL 兜
    (dbdir / "session_state.json").unlink()
    assert H.check_session_state()["stale"] is False


def test_stale_threshold_is_bigger_than_the_healthy_worst_case():
    """阈值必须比"健康时的最坏间隔"大 —— 这个数是实测出来的（15.9 分钟），
    根因（续期路径不写心跳）修掉后正常间隔 ≈5~6 分钟。"""
    assert H.RENEW_LOOP_STALE_MIN >= 15, "比实测的健康最坏值还小就会误报"


def test_health_renew_check_ignores_note_lines(dbdir, monkeypatch):
    """只读接口 401 现在记 `NOTE:` —— 巡检不能再把它当"续期脚本失败"。"""
    from datetime import datetime
    monkeypatch.setattr(H, "ROOT", dbdir)
    # check_renew 把日志时间戳按**东八区**解析（服务端永远在 CST 打日志）；
    # 用本机时区写时间的话，UTC 的 CI 上会差 8 小时、永远落不进 15 分钟窗口。
    stamp = datetime.now(H.TZ8).strftime("%Y-%m-%d %H:%M:%S")
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
    seg = src[i:i + 2600]   # P12-BI/BJ 之后这段变长了（多了墙检查与窗口告警）
    for bad in ('log("FAIL', "log('FAIL"):
        assert bad not in seg, "这段里出现 FAIL 就会让巡检每小时误报"
    assert 'log("NOTE' in seg and 'log("SESSION_DEAD' in seg
