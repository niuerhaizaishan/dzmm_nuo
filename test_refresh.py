#!/usr/bin/env python3
"""实测：Playwright 浏览器身份能否续期 token（纯读，不发消息）。

验证点：
1. 打开 dzmm.ai 后，页面 localStorage 里的 Supabase session 结构
2. 在页面里 fetch /api/auth/token（浏览器身份 + credentials:include）能否拿到新 access_token
3. 对比新旧 token 的 expires_at / access_token，判断浏览器身份是否真的续期成功
"""
import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path("/app")
sys.path.insert(0, str(ROOT))
import token_store
import env_config

ORIGIN = "https://www.dzmm.ai"


def decode_session(value: str):
    v = value
    if v.startswith("base64-"):
        v = v[len("base64-"):]
    v += "=" * (-len(v) % 4)
    try:
        return json.loads(base64.b64decode(v))
    except Exception as e:
        return {"_err": str(e)}


def main():
    # 1. 读旧 session
    c = token_store.get_cookie("sb-rls-auth-token")
    old = decode_session(c["value"])
    old_exp = old.get("expires_at", 0)
    old_at = old.get("access_token", "")
    print(f"[BEFORE] expires_at={old_exp} remain={old_exp - time.time():.0f}s")
    print(f"[BEFORE] access_token fp={old_at[:20]}...")

    import asyncio
    from playwright.async_api import async_playwright

    async def run():
        pw = await async_playwright().start()
        proxy_url = env_config.get_proxy_url()
        # 用临时 profile，避免和 bot 常驻的 pw_profile 冲突
        ctx = await pw.chromium.launch_persistent_context(
            str(ROOT / "pw_test_refresh"), headless=True,
            channel=env_config.get_channel(),
            viewport={"width": 1280, "height": 800},
            proxy={"server": proxy_url} if proxy_url else None,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
        page = await ctx.new_page()
        # 先 goto 建立 domain，再 add_cookies 注入登录态，再 goto 让页面用新 cookie 初始化
        await page.goto(ORIGIN, wait_until="domcontentloaded", timeout=30000)
        await ctx.add_cookies(token_store.get_cookies_for_playwright())
        await page.goto(ORIGIN, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(6)

        # 2. 读 localStorage（supabase session 结构）
        ls = await page.evaluate(
            "() => { const o={}; for (let i=0;i<localStorage.length;i++){"
            "const k=localStorage.key(i); o[k]=(localStorage.getItem(k)||'').slice(0,150);} return o; }")
        print("[LOCALSTORAGE] keys:", json.dumps(list(ls.keys()), ensure_ascii=False))
        for k, v in ls.items():
            print(f"    {k} = {v[:120]}")

        # 3. 浏览器身份 fetch /api/auth/token
        try:
            r = await page.evaluate(
                "async () => { const r = await fetch('/api/auth/token', {credentials:'include'});"
                " return {status: r.status, body: await r.text()}; }")
            print(f"[FETCH /api/auth/token] status={r['status']}")
            print(f"    body={r['body'][:400]}")
        except Exception as e:
            print(f"[FETCH fail] {str(e)[:150]}")

        # 4. 读 cookie 是否被刷新
        cookies = await ctx.cookies(ORIGIN)
        for ck in cookies:
            if ck["name"] == "sb-rls-auth-token":
                new = decode_session(ck["value"])
                new_exp = new.get("expires_at", 0)
                new_at = new.get("access_token", "")
                print(f"[AFTER-cookie] expires_at={new_exp} remain={new_exp - time.time():.0f}s")
                print(f"[AFTER-cookie] access_token fp={new_at[:20]}...")
                print(f"[DELTA] expires_at 变化 = {new_exp - old_exp} 秒")
                print(f"[REFRESHED] {'YES 浏览器身份续期成功' if new_at and new_at != old_at else 'NO token 未变化'}")
                break
        await ctx.close()
        await pw.stop()

    asyncio.run(run())


if __name__ == "__main__":
    main()
