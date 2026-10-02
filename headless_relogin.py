#!/usr/bin/env python3
"""Headless 登录 DZMM + 写回 token JSON（AppleScript 兜底方案）。

锁屏状态下可用：用 Playwright 无头浏览器登录 dzmm.ai，拿到 sb-rls-auth-token 后
写回 token JSON（token_store，bot 通过它读取）。

与 auto_relogin.py（AppleScript 驱动 Chrome）互补：本脚本不依赖屏幕 / AppleScript / TCC 权限。

用法：
    python headless_relogin.py            # 检测未登录则 headless 登录 + 写回
    python headless_relogin.py --check    # 仅检查登录态（不登录），退出码 0=有效 1=失效

依赖：
    - Playwright（本地 channel="chrome"；云端改 chromium）
    - 7890 代理（dzmm.ai 必须走代理，云端可能直连）
"""

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import env_config

ROOT = Path(__file__).parent
# S1：登录配置路径可用 DZMM_LOGIN_FILE 覆盖 —— Step 2-5 撤老挂载后重登链不能断
CONFIG = Path(os.environ.get("DZMM_LOGIN_FILE") or (ROOT / ".dzmm_login.json"))
PW_PROFILE = "pw_login"  # Playwright 持久化 profile（登录态缓存）

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def load_config():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    return cfg["email"], cfg["password"]


def check_logged_in(min_remain: int = 300) -> bool:
    """读 token JSON 的 sb-rls-auth-token，验证 expires_at 剩余 > min_remain 秒."""
    sys.path.insert(0, str(ROOT))
    import token_store
    try:
        v = token_store.get_cookie_value("sb-rls-auth-token")
        if not v:
            return False
        payload = v[len("base64-"):] if v.startswith("base64-") else v
        payload += "=" * (-len(payload) % 4)
        d = json.loads(base64.b64decode(payload))
        return d.get("expires_at", 0) - time.time() > min_remain
    except Exception:
        return False


def headless_login(email: str, password: str):
    """Playwright 无头登录，返回 cookie 列表 [{name,value,domain,path,expires,secure,httpOnly}] 或 None."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        proxy_url = env_config.get_proxy_url()
        ctx = p.chromium.launch_persistent_context(
            PW_PROFILE, channel=env_config.get_channel(), headless=True,
            viewport={"width": 1280, "height": 800},
            proxy={"server": proxy_url} if proxy_url else None, user_agent=UA,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
        page = ctx.new_page()
        try:
            page.goto(env_config.get_dzmm_origin() + "/", timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # 已登录（pw_login 残留登录态）则直接取 token
            cookies = ctx.cookies()
            if any(c["name"] == "sb-rls-auth-token" for c in cookies):
                pass
            else:
                # 点登录 → 填账号密码 → 提交
                page.locator("text=登录").first.click(timeout=8000)
                page.wait_for_timeout(2000)
                page.fill("input[type=email]", email)
                page.fill("input[type=password]", password)
                page.locator("button", has_text="登录").last.click(timeout=8000)
                page.wait_for_timeout(6000)
        except Exception as e:
            # 撞验证码（找不到登录按钮）/页面结构变化 → 返回 None，让上层转人工辅助登录（noVNC）
            print("headless_login 异常（撞验证码/页面变化）:", str(e)[:120])
            return None

        cookies = ctx.cookies()
        token = next((c for c in cookies if c["name"] == "sb-rls-auth-token"), None)
        if not token:
            return None
        # 只回写关键 cookie：sb-rls-auth-token + _rid（用户标识）
        keep = [c for c in cookies if c["name"] in ("sb-rls-auth-token", "_rid")]
        return [{"name": c["name"], "value": c["value"], "domain": c["domain"],
                 "path": c["path"], "expires": c.get("expires"),
                 "secure": c.get("secure"), "httpOnly": c.get("httpOnly")} for c in keep]


PW_PROFILE_CAPTCHA = "pw_captcha"  # 人工过验证码专用 profile（独立，避免与自动登录冲突）


def login_manual_captcha(email, password, wait_minutes=10):
    """撞验证码时的人工辅助登录：headed 模式打开登录页（显示到宿主机 Xvfb :99），
    发微信通知用户去 noVNC 网页滑动验证码，轮询等待登录成功（cookie 出现）。
    返回 cookie 列表或 None（超时）。"""
    import os
    from playwright.sync_api import sync_playwright
    vnc_url = os.environ.get("VNC_URL", "http://47.237.152.187:6080/vnc.html")
    vnc_pass = os.environ.get("VNC_PASS", "dzmm2026")
    # 清理可能的残留 Singleton 锁
    for lock in ROOT.glob(f"{PW_PROFILE_CAPTCHA}/Singleton*"):
        try:
            lock.unlink()
        except Exception:
            pass
    try:
        sys.path.insert(0, str(ROOT))
        from wx_alert import send_weixin
        send_weixin(
            "⚠️【小小糯bot】撞验证码了，需要你过一下\n"
            f"电脑浏览器打开：{vnc_url}\n"
            f"密码：{vnc_pass}\n"
            "10 分钟内有效，看到登录页后帮我拖动滑块过验证码，过完会自动继续"
        )
    except Exception:
        pass
    with sync_playwright() as p:
        proxy_url = env_config.get_proxy_url()
        ctx = p.chromium.launch_persistent_context(
            PW_PROFILE_CAPTCHA, channel=env_config.get_channel(), headless=False,
            viewport={"width": 1280, "height": 800},
            proxy={"server": proxy_url} if proxy_url else None, user_agent=UA,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
        page = ctx.new_page()
        try:
            page.goto(env_config.get_dzmm_origin() + "/", timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            cookies = ctx.cookies()
            if not any(c["name"] == "sb-rls-auth-token" for c in cookies):
                page.locator("text=登录").first.click(timeout=8000)
                page.wait_for_timeout(2000)
                page.fill("input[type=email]", email)
                page.fill("input[type=password]", password)
                page.locator("button", has_text="登录").last.click(timeout=8000)
                page.wait_for_timeout(2000)
        except Exception:
            # 自动填表失败（撞验证码/页面异常）→ 不崩溃，进入轮询等用户手动在 noVNC 操作
            pass
        deadline = time.time() + wait_minutes * 60
        while time.time() < deadline:
            cookies = ctx.cookies()
            token = next((c for c in cookies if c["name"] == "sb-rls-auth-token"), None)
            if token:
                keep = [c for c in cookies if c["name"] in ("sb-rls-auth-token", "_rid")]
                return [{"name": c["name"], "value": c["value"], "domain": c["domain"],
                         "path": c["path"], "expires": c.get("expires"),
                         "secure": c.get("secure"), "httpOnly": c.get("httpOnly")} for c in keep]
            page.wait_for_timeout(3000)
        return None


def write_to_json(cookies: list) -> bool:
    """写 cookie 到 token JSON（token_store）。"""
    sys.path.insert(0, str(ROOT))
    import token_store
    return token_store.set_cookies(cookies)


def _do_login() -> int:
    """真正的登录流程；由 `main()` 在**持有会话锁**的前提下调用。"""
    email, password = load_config()
    print("token 失效，开始 headless 登录...")
    cookies = headless_login(email, password)
    if not cookies:
        # 撞验证码：headless 登录失败。后台拉起 noVNC 人工辅助（不阻塞主流程），
        # 主流程快速返回，让 entrypoint 尽快启动 bot 降级运行（收发继续），
        # 风控解除后由监测脚本 monitor_recover.py 重启容器触发重登。
        print("headless 登录失败（撞验证码），后台拉起 noVNC 人工辅助，主流程快速返回")
        try:
            subprocess.Popen(
                [sys.executable, __file__, "--manual-captcha"],
                cwd=str(ROOT), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            pass
        return 1

    print("headless 登录成功，写回 token JSON...")
    if not write_to_json(cookies):
        print("FAIL: 写回 token JSON 失败")
        return 1

    if check_logged_in():
        print("OK: headless 登录 + 写回 token JSON 成功")
        return 0
    print("WARN: 写回后验证未通过")
    return 1


def main():
    from contextlib import nullcontext

    from session_guard import guard

    if "--check" in sys.argv:
        return 0 if check_logged_in() else 1

    # ⚠️ P0（2026-10-01 生产实锤）：`guard()` 只有**一把锁**
    # （runtime/session.py 的 LOCK_FILE = .session.lock），`reason` 参数只是写进锁
    # 文件里的一句备注，**不是两把锁**。而 renew_token.py 是**在持有该锁的情况下**
    # 用 subprocess.run 同步 spawn 本脚本的：父进程握着锁不放，子进程在这里再抢
    # 同一把锁 ⇒ 永远拿不到 ⇒ 等满 timeout=180 后打印 "SKIP: 另一个进程正在
    # 续期/重登" 并 rc=1 ⇒ 自动恢复整条链 100% 失效。
    # 代价：2026-09-30 掉线 8.8 小时、2026-10-01 21:29 再次掉线，两次都只能人工救。
    # 父进程用这个环境变量显式声明"锁已由我持有"，此时子进程直接视为持锁。
    # ⚠️ 只认**显式声明**，绝不能默认放开 —— 真并发的两个登录会互相作废 refresh_token。
    _under_parent = (os.environ.get("DZMM_RELOGIN_UNDER_PARENT_LOCK") or "").strip() == "1"

    def _guard(reason: str, timeout: float = 30.0):
        if _under_parent:
            return nullcontext(True)
        return guard(reason, timeout)

    # 后台人工辅助登录模式：由主流程在撞验证码时 Popen 拉起，noVNC 窗口供人工过滑块。
    # 这条路径可能持续 10 分钟（等人），但仍必须持锁 —— 否则会和续期/重登并发。
    if "--manual-captcha" in sys.argv:
        with _guard("headless_relogin.manual_captcha", timeout=600) as got:
            if not got:
                print("SKIP: 另一个进程正在续期/重登，放弃本次人工辅助登录")
                return 1
            email, password = load_config()
            cookies = login_manual_captcha(email, password, wait_minutes=10)
            if cookies and write_to_json(cookies):
                print("OK: 人工辅助登录成功")
                return 0
            print("FAIL: 人工辅助登录失败/超时")
            return 1

    with _guard("headless_relogin", timeout=180) as got:
        if not got:
            print("SKIP: 另一个进程正在续期/重登，放弃本次登录（避免并发登录互相作废）")
            return 1
        # 拿到锁后**再确认一次**：等待锁的期间别人可能已经登好了（双重检查）
        if check_logged_in():
            print("OK: 已登录（token 有效，锁内复查）")
            return 0
        return _do_login()


if __name__ == "__main__":
    sys.exit(main())
