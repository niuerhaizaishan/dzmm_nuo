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
import subprocess
import sys
import time
from pathlib import Path
import env_config

ROOT = Path(__file__).parent
CONFIG = ROOT / ".dzmm_login.json"
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


def main():
    if "--check" in sys.argv:
        return 0 if check_logged_in() else 1

    # 后台人工辅助登录模式：由主流程在撞验证码时 Popen 拉起，noVNC 窗口供人工过滑块
    if "--manual-captcha" in sys.argv:
        email, password = load_config()
        cookies = login_manual_captcha(email, password, wait_minutes=10)
        if cookies and write_to_json(cookies):
            print("OK: 人工辅助登录成功")
            return 0
        print("FAIL: 人工辅助登录失败/超时")
        return 1

    if check_logged_in():
        print("OK: 已登录（token 有效）")
        return 0

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


if __name__ == "__main__":
    sys.exit(main())
