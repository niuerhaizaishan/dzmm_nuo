#!/usr/bin/env python3
"""浏览器续期：Playwright 打开 dzmm.ai，页面里 fetch /api/auth/token 拿新 access_token 写回。

背景（2026-09-07 实测确认）：
    撞验证码（IP 风控/脚本检测）拦的是 requests 裸 HTTP（续期/读简介/HTTP 兜底），
    但不拦浏览器身份请求——Playwright 页面里 fetch /api/auth/token 返回 200。
    因此 requests 续期撞验证码时，切到本脚本用浏览器身份续期，绕过脚本检测。

由 renew_token.py 在 requests 续期返回 ['error','location']（撞验证码）时调用。

用法：
    python renew_token_browser.py    # 退出码 0=续期成功，1=失败
"""
import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path("/app")
sys.path.insert(0, str(ROOT))

import env_config
import token_store
import renew_token  # 复用 read_token_cookie / write_token_cookie / log

ORIGIN = env_config.get_dzmm_origin()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def jwt_exp(token: str) -> int:
    """从 JWT payload 解出 exp（秒），失败返回 0。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.b64decode(payload)).get("exp", 0))
    except Exception:
        return 0


def clear_singleton_lock() -> None:
    """清 pw_renew 目录残留的 Singleton 锁，避免上次异常退出导致下次启动失败。"""
    for lock in ROOT.glob("pw_renew/Singleton*"):
        try:
            lock.unlink()
        except Exception:
            pass


def browser_fetch_token() -> dict:
    """Playwright 打开 dzmm.ai + 注入 cookie + 页面里 fetch /api/auth/token。
    返回 {status, body}；任何异常返回 {}。"""
    from playwright.sync_api import sync_playwright
    clear_singleton_lock()
    with sync_playwright() as p:
        proxy_url = env_config.get_proxy_url()
        ctx = p.chromium.launch_persistent_context(
            str(ROOT / "pw_renew"), channel=env_config.get_channel(), headless=True,
            viewport={"width": 1280, "height": 800},
            proxy={"server": proxy_url} if proxy_url else None, user_agent=UA,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
        page = ctx.new_page()
        try:
            page.goto(ORIGIN, wait_until="domcontentloaded", timeout=30000)
            ctx.add_cookies(token_store.get_cookies_for_playwright())
            page.goto(ORIGIN, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            result = page.evaluate(
                "async () => { const r = await fetch('/api/auth/token', {credentials:'include'});"
                " return {status: r.status, body: await r.text()}; }")
            return result
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def main() -> int:
    cookie, d = renew_token.read_token_cookie()
    if not cookie or not d:
        renew_token.log("浏览器续期 FAIL: 未读到 sb-rls-auth-token cookie")
        return 1

    old_exp = d.get("expires_at", 0)
    old_at = d.get("access_token", "")

    try:
        result = browser_fetch_token()
    except Exception as e:
        renew_token.log("浏览器续期 FAIL: Playwright 异常: " + str(e)[:100])
        return 1

    status = result.get("status")
    if status != 200:
        renew_token.log("浏览器续期 FAIL: fetch status=" + str(status)
                        + " body=" + str(result.get("body", ""))[:120])
        return 1

    try:
        new = json.loads(result.get("body", "{}"))
    except Exception:
        renew_token.log("浏览器续期 FAIL: 响应非 JSON")
        return 1

    new_at = new.get("access_token", "")
    if not new_at:
        renew_token.log("浏览器续期 FAIL: 无 access_token: " + str(list(new.keys())))
        return 1

    # 更新 access_token + expires_at（body 有 expires_at 用之；否则从 JWT exp 解）
    new_exp = new.get("expires_at", 0)
    if not new_exp:
        new_exp = jwt_exp(new_at)
    if not new_exp:
        new_exp = old_exp + 3600  # 兜底：默认 1 小时

    d["access_token"] = new_at
    d["expires_at"] = new_exp
    d["expires_in"] = new.get("expires_in", d.get("expires_in", 3600))

    # 关键修复：若服务端返回了新 refresh_token（refresh token rotation），必须同步更新，
    # 否则 cookie 里的旧 refresh_token 已被作废，下次续期会 ['error'] 失效。
    new_refresh = new.get("refresh_token", "")
    if new_refresh and new_refresh != d.get("refresh_token", ""):
        d["refresh_token"] = new_refresh
        renew_token.log("  已同步新 refresh_token（rotation）")

    new_value = "base64-" + base64.b64encode(
        json.dumps(d, separators=(",", ":")).encode("utf-8")).decode()

    if new_value == cookie.get("value", ""):
        renew_token.log("SKIP: token 未变化，无需写回")
        return 0

    if not renew_token.write_token_cookie(new_value):
        renew_token.log("浏览器续期 FAIL: 写回 token JSON 失败")
        return 1

    renew_token.log("OK: 浏览器续期成功（expires_at=" + str(new_exp)
                    + "，access_token fp=" + new_at[:16] + "...）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
