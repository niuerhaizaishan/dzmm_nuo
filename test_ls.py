import asyncio, sys, os, shutil
sys.path.insert(0, "/app")
import token_store, env_config

CID = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"
ORIGIN = "https://www.dzmm.ai"

async def main():
    from playwright.async_api import async_playwright

    dst = "/tmp/pw_profile_ls"
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree("/app/pw_profile", dst)

    cookies = token_store.get_cookies_for_playwright()
    pw = await async_playwright().start()
    proxy_url = env_config.get_proxy_url()
    ctx = await pw.chromium.launch_persistent_context(
        dst, headless=True, channel=env_config.get_channel(),
        viewport={"width": 1280, "height": 800},
        proxy={"server": proxy_url} if proxy_url else None,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
    page = await ctx.new_page()

    await ctx.add_cookies(cookies)
    await page.goto(ORIGIN + "/chat?c=" + CID, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(12)

    # 读 localStorage 所有 key
    ls = await page.evaluate("() => { const o = {}; for (let i=0; i<localStorage.length; i++) { const k = localStorage.key(i); o[k] = localStorage.getItem(k); } return o; }")
    print("=== localStorage keys ===")
    for k, v in ls.items():
        print(" ", k, "=", (v or "")[:80])

    # 读 sessionStorage
    ss = await page.evaluate("() => { const o = {}; for (let i=0; i<sessionStorage.length; i++) { const k = sessionStorage.key(i); o[k] = sessionStorage.getItem(k); } return o; }")
    print("=== sessionStorage keys ===")
    for k, v in ss.items():
        print(" ", k, "=", (v or "")[:80])

    # 对比当前 cookie token
    import base64, json, time
    tc = [c for c in cookies if c["name"] == "sb-rls-auth-token"][0]
    v = tc["value"]
    p = v[len("base64-"):] if v.startswith("base64-") else v
    p += "=" * (-len(p) % 4)
    d = json.loads(base64.b64decode(p))
    print("=== 当前 JSON cookie token expires_at:", d.get("expires_at"), "剩余:", round(d.get("expires_at",0)-time.time()), "s")

    await ctx.close()
    await pw.stop()
    if os.path.exists(dst):
        shutil.rmtree(dst)

asyncio.run(main())
