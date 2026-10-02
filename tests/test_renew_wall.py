"""P12-BI：把"平台风控墙(418)"和"会话真死"分开 —— 墙在的时候重登是白撞。

## 证据（把几天的续期日志按窗口聚合出来的）

`tools/analyze_renew_windows.py`：87 个失败窗口，最大的几个是

| 窗口 | 失败次数 | 时长 | 之后怎么好的 |
|---|---|---|---|
| 09-27 01:47 → 09:28 | 93 | **461 分钟** | `OK: token 续期成功`（同一份 cookie，中间没登录过） |
| 09-28 19:05 → 09-29 01:21 | 76 | 376 分钟 | 撞验证码 → noVNC 人工验证 |
| 09-28 00:38 → 06:49 | 75 | 371 分钟 | `OK: token 续期成功` |
| 09-29 02:21 → 07:37 | 64 | 316 分钟 | `OK: token 续期成功` |

窗口里 100% 是同一句 `FAIL: refresh_token 失效（未授权），写 emergency 触发重登` +
`emergency 冷却中`，**从没试过浏览器/重登**；而同一时间宿主机
`monitor_recover.log` 记的是**首页=418**（验证码墙；三周累计 6570 次）。

⇒ 那几小时**会话没死，是平台把我们挡在风控墙外**：`未授权` 是墙挡出来的。
旧代码把它读成"refresh_token 失效"，于是写 emergency（只重启 bot.py 进程）、
反复空转 4~8 小时，直到墙自己解除。

## 修法

升级链前面加一道**墙检查**（`_home_status()`）：

    业务探针 unauth
      ├─ 首页 418(captcha) ⇒ probe="captcha"：不重登、不重启、不写 emergency（等墙）
      ├─ 首页探测失败      ⇒ 不下结论（下一轮再判）
      └─ 首页 200          ⇒ 会话真死 ⇒ P12-BG 密码重登（现在才有意义）

`probe="captcha"` 对下游是安全的：巡检与 Socket.IO 都**只认 "unauth" 为死**（P12-AR）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                    # noqa: E402

import renew_token as R                                          # noqa: E402

SRC = (ROOT / "renew_token.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 墙探测

def test_home_status_classifies_200_as_ok(monkeypatch):
    class _R:
        status_code = 200
        text = "<html>ok</html>"
    monkeypatch.setattr(R, "PROXY", {}, raising=False)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _R(), raising=False)
    assert R._home_status() == "ok"


def test_home_status_classifies_418_as_wall(monkeypatch):
    class _R:
        status_code = 418
        text = '{"error":"captcha_required","location":"/__captcha/"}'
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _R(), raising=False)
    assert R._home_status() == "wall"


def test_home_status_treats_200_with_captcha_body_as_wall(monkeypatch):
    """有些路径会 200 但把验证码页当正文返回 —— 那也算墙。"""
    class _R:
        status_code = 200
        text = "<html>.../__captcha/...</html>"
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _R(), raising=False)
    assert R._home_status() == "wall"


def test_home_status_error_path(monkeypatch):
    import requests

    def _boom(*a, **kw):
        raise RuntimeError("conn reset")

    monkeypatch.setattr(requests, "get", _boom, raising=False)
    logged = []
    monkeypatch.setattr(R, "log", lambda m, to_stdout=False: logged.append(m), raising=False)
    assert R._home_status() == "err"
    assert any("首页探测失败" in m for m in logged), logged


# ---------------------------------------------------------------- ② 接线：先看墙再升级

def test_unauth_branch_checks_the_wall_before_relogin():
    # ⚠️ P12-BO 之后 `if probe == "unauth":` 有两处（退避分支里也有一处），
    # 这条要的是**主分支**那处 ⇒ 取最后一处。
    i = SRC.rindex('if probe == "unauth":')
    seg = SRC[i:i + 2600]
    assert "_home_status()" in seg, "unauth 分支必须先探测墙"
    assert seg.index("_home_status()") < seg.index("_try_password_relogin()"), \
        "顺序：先判墙，再考虑重登"
    assert 'wall == "wall"' in seg and '"captcha"' in seg


def test_captcha_wall_does_not_write_emergency():
    """墙在时不许重登/写 emergency（重启过不了墙，只会把在途消息弄丢）。"""
    i = SRC.index('if wall == "wall":')
    seg = SRC[i:SRC.index('if wall == "err":')]
    assert '_write_session_state("captcha")' in seg
    assert "_write_emergency()" not in seg
    assert "_try_password_relogin()" not in seg
    assert "_browser_renew()" not in seg, "墙在时连浏览器续期都不该试（会被 418 挡回来）"


def test_long_unverified_branch_checks_the_wall_too():
    i = SRC.index("if _unverified_min() > RELOGIN_AFTER_MIN:")
    seg = SRC[i:i + 2600]
    assert '_home_status() == "wall"' in seg
    assert seg.index("_home_status()") < seg.index("_browser_renew()"), \
        "墙在的时候连浏览器续期都不该试（它会 418 回来）"


def test_captcha_probe_is_safe_downstream():
    """`probe="captcha"` 不会被下游当成"会话死"（否则 SIO 又把退避顶到 1 小时）。"""
    sio = (ROOT / "sio_subscribe.py").read_text(encoding="utf-8")
    seg = sio.split("def _token_stale")[1][:1600]
    assert '"unauth"' in seg, "SIO 只认 unauth 为死"
    assert "captcha" not in seg, "captcha 不该进 SIO 的判据"
    health = (ROOT / "check_bot_health.py").read_text(encoding="utf-8")
    assert "def check_network" in health and 'return "captcha"' in health, \
        "巡检本来就有验证码检测 —— 续期现在与它同口径"


def test_probe_values_are_documented():
    """`_write_session_state` 的取值表要把 captcha 写进去（否则下一个人又会读错）。"""
    i = SRC.index("def _write_session_state")
    seg = SRC[i:i + 1500]
    assert "captcha" in seg, "心跳取值表里要说明 captcha"
    assert "unauth" in seg


if __name__ == "__main__":                                       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
