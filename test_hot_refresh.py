import asyncio, sys, json, base64, time, os, shutil
sys.path.insert(0, "/app")
import token_store

PROFILE = "/tmp/test_hot_refresh_profile"

async def main():
    from playwright.async_api import async_playwright

    # 1. 读 JSON 里最新 token cookie
    latest = token_store.get_cookies_for_playwright()
    latest_token = [c for c in latest if c["name"] == "sb-rls-auth-token"][0]

    # 2. 构造「过期」token（模拟一小时没发消息，Playwright 里的旧 cookie）
    def decode(v):
        p = v[len("base64-"):] if v.startswith("base64-") else v
        p += "=" * (-len(p) % 4)
        return json.loads(base64.b64decode(p))
    def encode(d):
        return "base64-" + base64.b64encode(json.dumps(d, separators=(",", ":")).encode()).decode()

    old_cookie = dict(latest_token)
    d = decode(latest_token["value"])
    d["expires_at"] = int(time.time()) - 100  # 过期 100 秒
    old_cookie["value"] = encode(d)

    # 3. 启动 Playwright，add 过期 cookie（模拟 bot 长时间未发的旧 cookie）
    if os.path.exists(PROFILE):
        shutil.rmtree(PROFILE)
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        PROFILE, headless=True, args=["--no-sandbox", "--disable-gpu"])
    page = await ctx.new_page()
    await ctx.add_cookies([old_cookie])

    # 4. 验证 context 里是过期 cookie
    c1 = [c for c in await ctx.cookies() if c["name"] == "sb-rls-auth-token"][0]
    remain_before = decode(c1["value"])["expires_at"] - time.time()
    print(f"刷新前 context cookie 剩余: {remain_before:.0f}s （负数=已过期，模拟一小时没发消息）")

    # 5. 模拟 _refresh_token：无条件从 JSON 读最新 cookie add_cookies
    latest = token_store.get_cookies_for_playwright()
    await ctx.add_cookies(latest)

    # 6. 验证 context 里已被更新为最新值
    c2 = [c for c in await ctx.cookies() if c["name"] == "sb-rls-auth-token"][0]
    remain_after = decode(c2["value"])["expires_at"] - time.time()
    print(f"刷新后 context cookie 剩余: {remain_after:.0f}s （正数=已刷新为最新）")

    assert remain_before < 0, "前置条件不成立：模拟的旧 cookie 居然没过期"
    assert remain_after > 0, "刷新失败！旧 cookie 没被覆盖"
    print("✅ 测试通过：过期 cookie 被无条件刷新为最新值")

    await ctx.close()
    await pw.stop()
    if os.path.exists(PROFILE):
        shutil.rmtree(PROFILE)

asyncio.run(main())
