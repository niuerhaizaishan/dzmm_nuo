"""P12-AI：**容器重建不该让登录态和风控退避归零**（屋主报"经常 token 续期失效"的根因守卫）。

2026-09-27 查出的事故链（每次 `deploy` 都会重建容器，当天重建了 9 次）：

1. `pw_login`（`headless_relogin.py` 的 Playwright 持久化 profile）**不在挂载清单里**
   → 重建后是空的 → 没有缓存登录态可用 → 只能**重新走账号密码登录** →
   而那正是最容易被风控/验证码拦下的路径。
2. `renew_browser_backoff.json`（撞验证码后的 1 小时退避）也不在挂载清单里，而且放在
   `/app` 根下 → 重建即清零 → 本该安静 1 小时，结果立刻又去打风控接口，越打越黑。
3. 于是"重建越频繁 → 越容易撞验证码 → 续期失败越多"，与屋主观察到的"现在经常失效"吻合。

这条守卫钉住三件事：**profile 挂载在、退避文件在挂载目录里、启动时清残留锁**。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import env_config                                              # noqa: E402

COMPOSE = ROOT / "docker-compose.prod.yml"


def _yml() -> str:
    return COMPOSE.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1. profile 必须挂载

def test_browser_profiles_are_mounted():
    yml = _yml()
    for d in ("pw_profile", "pw_profile_greet", "pw_login", "pw_captcha"):
        assert "- ./{0}:/app/{0}".format(d) in yml, \
            "{} 必须挂出来：不挂 = 每次重建都冷登录（撞验证码的主因）".format(d)


def test_profiles_are_declared_as_directories_not_left_inside_image():
    """.dockerignore 里也要排除它们（别把上一次构建的 profile 烤进镜像）。"""
    di = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "pw_profile*/" in di or "pw_profile" in di, ".dockerignore 要排除 pw_profile*"


def test_entrypoint_clears_stale_singleton_locks():
    """挂载的 profile 里有上一次容器留下的 SingletonLock → chromium 会拒绝启动。"""
    sh = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert "Singleton*" in sh
    for d in ("pw_profile", "pw_login", "pw_captcha"):
        assert d in sh, "要清理 {} 的残留锁".format(d)
    # 必须在启动 bot / 登录之前清
    assert sh.index("Singleton*") < sh.index("headless_relogin.py")


# ---------------------------------------------------------------- 2. 退避文件在挂载目录里

def test_backoff_file_lives_in_the_mounted_data_dir():
    p = env_config.renew_backoff_file()
    assert p.parent.name == "data", "退避状态必须落在挂载的 data/ 下：{}".format(p)
    yml = _yml()
    assert "- ./data:/app/data" in yml, "前提：data/ 是挂载的"


def test_both_readers_use_the_same_backoff_path():
    """`renew_token.py` 写、`sio_subscribe.py` 读 —— 两边必须同一个路径，
    否则会出现"写了退避但没人看见"或反之。"""
    for name in ("renew_token.py", "sio_subscribe.py"):
        src = (ROOT / name).read_text(encoding="utf-8")
        assert "env_config.renew_backoff_file()" in src, \
            "{} 要走共享的路径函数，别各写各的".format(name)
        assert '"renew_browser_backoff.json"' not in src, \
            "{} 里不该再出现硬编码路径".format(name)


def test_backoff_write_creates_the_parent_dir():
    """`data/` 可能还没建（裸机首次运行）→ 写之前要 mkdir，否则退避静默失败。"""
    src = (ROOT / "renew_token.py").read_text(encoding="utf-8")
    i = src.index("def _browser_backoff()")
    seg = src[i:i + 400]
    assert "mkdir" in seg, "写退避前要确保目录存在"


# ---------------------------------------------------------------- 3. 事故链的对照

def test_compose_keeps_the_other_runtime_state_mounts():
    """这条不是新增，是"顺手别把老的挂载改掉"——它们各自对应过一次事故。"""
    yml = _yml()
    for line in ("- ./state.json:/app/state.json",
                 "- ./data:/app/data",
                 "- ./.dzmm_token.json:/app/.dzmm_token.json",
                 "- ./renew_fail_state.json:/app/renew_fail_state.json",
                 "- ./renew_emergency_state.json:/app/renew_emergency_state.json",
                 "- ./config:/app/config",
                 "- ./prompts:/app/prompts"):
        assert line in yml, line


def test_session_lock_is_deliberately_not_mounted():
    """`.session.lock` 是**进程内互斥**用的：重建后理应重置，不能挂（挂了会留下死锁）。"""
    yml = _yml()
    assert ".session.lock" not in yml
    assert re.search(r"\.session\.lock", yml) is None


# ---------------------------------------------------------------- 4. 内联重登：残留死会话不许短路

import base64                                                     # noqa: E402
import json                                                       # noqa: E402
import time                                                       # noqa: E402

import headless_relogin                                           # noqa: E402


def _session_cookie(exp_at: float) -> dict:
    raw = json.dumps({"expires_at": exp_at}, separators=(",", ":"))
    return {"name": "sb-rls-auth-token",
            "value": "base64-" + base64.b64encode(raw.encode()).decode()}


def test_need_fresh_login_when_no_token():
    assert headless_relogin.need_fresh_login([]) is True
    assert headless_relogin.need_fresh_login([{"name": "_rid", "value": "x"}]) is True


def test_residual_fresh_session_is_reused():
    """健康残留（SPA 已自动刷新）⇒ 直接用，不做多余的密码登录。"""
    assert headless_relogin.need_fresh_login(
        [_session_cookie(time.time() + 1800)]) is False


def test_residual_dead_session_forces_fresh_login():
    """2026-10-03 09:13 事故形状：profile 握着本地已过期/快过期的死会话。"""
    assert headless_relogin.need_fresh_login(
        [_session_cookie(time.time() - 10)]) is True
    assert headless_relogin.need_fresh_login(
        [_session_cookie(time.time() + 60)]) is True


def test_garbage_cookie_value_forces_fresh_login():
    assert headless_relogin.need_fresh_login(
        [{"name": "sb-rls-auth-token", "value": "烂值"}]) is True


def test_clear_singleton_locks(tmp_path):
    (tmp_path / "Cookies").write_text("x", encoding="utf-8")
    for n in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    headless_relogin.clear_singleton_locks(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Cookies"]


def test_headless_login_is_wired_to_clear_locks_and_stale_cookie():
    """钉 headless_login 接线：起浏览器前自清锁；见死残留先清 cookie 再登录；
    旧的"见 cookie 存在就 pass"短路不许复活。"""
    src = (ROOT / "headless_relogin.py").read_text(encoding="utf-8")
    i = src.index("def headless_login(")
    seg = src[i:src.index("def login_manual_captcha")]
    assert "clear_singleton_locks(" in seg, "起浏览器前必须自清 Singleton 锁"
    assert "need_fresh_login(" in seg, "残留会话必须过新鲜度判定"
    assert "ctx.clear_cookies()" in seg, "死残留 cookie 必须先清掉"
    assert 'if any(c["name"] == "sb-rls-auth-token" for c in cookies):' not in seg, \
        "旧的无条件短路已删，不许复活"
