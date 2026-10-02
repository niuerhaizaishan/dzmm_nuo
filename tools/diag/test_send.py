import asyncio, sys, os, shutil, subprocess
sys.path.insert(0, "/app")
import token_store, env_config

CID = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"  # 裳姐姐群
ORIGIN = "https://www.dzmm.ai"

async def main():
    from playwright.async_api import async_playwright

    # 复制 bot 的常驻 profile 到 /tmp（不影响运行中的 bot）
    src = "/app/pw_profile"
    dst = "/tmp/pw_profile_copy"
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print("已复制 bot 常驻 profile -> /tmp/pw_profile_copy")

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
    await asyncio.sleep(15)

    ta = await page.query_selector('textarea[placeholder*="输入消息"]')
    if not ta:
        print("❌ 找不到 textarea（profile 副本页面异常）")
        await ctx.close(); await pw.stop(); return

    test_text = "🔧 profile副本验证 " + str(int(asyncio.get_event_loop().time()))
    await ta.click(timeout=5000)
    await page.keyboard.insert_text(test_text)
    await asyncio.sleep(0.5)
    await page.keyboard.press("Enter")
    print("已回车:", test_text)
    await asyncio.sleep(5)

    from dzmm import get_messages
    msgs = get_messages(CID, 10)
    found = any(test_text in (m.get("content") or {}).get("text", "") for m in msgs)
    print("API 读到测试消息:", "✅ 真实投递" if found else "❌ 未投递（profile 副本也假成功）")

    await ctx.close()
    await pw.stop()
    if os.path.exists(dst):
        shutil.rmtree(dst)

asyncio.run(main())
