#!/usr/bin/env python3
"""对比 Socket.IO vs Playwright 群聊/私聊发送：真实投递率 + 单条耗时。

结论依据：发带唯一时间戳的消息，随后用 tRPC get_messages 读回验证是否真实投递
（sent_by==NUO_UID 且文本匹配）。Socket.IO 只回 HTTP 状态码，不代表投递，故必须读回。
"""
import asyncio, sys, time, os, shutil
sys.path.insert(0, "/app")
import importlib.util
import token_store, env_config

ORIGIN = env_config.get_dzmm_origin()
GROUP_CID = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"    # 裳姐姐群（测试群）
PRIVATE_CID = "4c52490e-839d-4c82-8064-23acaa1037a8"  # 御褚私聊
UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"          # 小小糯 NUO_UID
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def delivered(text: str, cid: str, limit: int = 20):
    """读回验证：True=真实投递，False=未投递(假成功)，None=读回失败。"""
    from dzmm import get_messages
    try:
        msgs = get_messages(cid, limit)
    except Exception as e:
        print("    get_messages 异常:", str(e)[:70])
        return None
    for m in msgs:
        if m.get("sent_by") != UID:
            continue
        if text in (m.get("content") or {}).get("text", ""):
            return True
    return False


def run_sio(cid: str, label: str, n: int = 3):
    """Socket.IO（DZMMSocket polling 直发）发送 n 条。"""
    spec = importlib.util.spec_from_file_location("dzmm_socketio", "/app/socketio.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = mod.DZMMSocket(ORIGIN)
    cookie = token_store.get_cookie_string()
    ok_n = del_n = 0
    tot = 0.0
    for i in range(n):
        text = "🔧SIO测试 %d" % (int(time.time() * 1000) % 1000000)
        t0 = time.time()
        ok = d.send_message(cid, UID, text, cookie)
        dt = time.time() - t0
        time.sleep(2)
        dv = delivered(text, cid)
        ok_n += 1 if ok else 0
        del_n += 1 if dv else 0
        tot += dt
        print("  SIO #%d 返回%s 投递%s %.2fs" % (
            i + 1, "OK" if ok else "FAIL", "✅" if dv else "❌假成功", dt))
    print("[%s] SIO  → 返回成功 %d/%d，真实投递 %d/%d，均 %.2fs/条\n"
          % (label, ok_n, n, del_n, n, tot / n))


async def run_pw(cid: str, label: str, n: int = 3):
    """Playwright 真实浏览器发送 n 条（复制 profile 副本，避免干扰运行中的 bot）。"""
    from playwright.async_api import async_playwright
    src = "/app/pw_profile"
    dst = "/tmp/pw_cmp_%d" % int(time.time())
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    cookies = token_store.get_cookies_for_playwright()
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        dst, headless=True, channel=env_config.get_channel(),
        viewport={"width": 1280, "height": 800},
        proxy={"server": env_config.get_proxy_url()} if env_config.get_proxy_url() else None,
        user_agent=UA,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
    page = await ctx.new_page()
    await page.goto(ORIGIN + "/chat?c=" + cid, wait_until="domcontentloaded", timeout=30000)
    await ctx.add_cookies(cookies)
    # 轮询等输入框（最多 20s）
    for _ in range(40):
        ta = await page.query_selector('textarea[placeholder*="输入消息"]')
        if ta and await ta.is_visible():
            break
        await asyncio.sleep(0.5)
    del_n = 0
    tot = 0.0
    for i in range(n):
        text = "🔧PW测试 %d" % (int(time.time() * 1000) % 1000000)
        t0 = time.time()
        try:
            ta = await page.query_selector('textarea[placeholder*="输入消息"]')
            if not ta or not await ta.is_visible():
                print("  PW #%d 找不到输入框(可能等回复锁)" % (i + 1))
                continue
            await ta.click(timeout=5000)
            await asyncio.sleep(0.3)
            await page.keyboard.insert_text(text)
            await page.keyboard.press("Enter")
            await asyncio.sleep(1.5)
            dv = delivered(text, cid)
            del_n += 1 if dv else 0
            dt = time.time() - t0
            tot += dt
            print("  PW #%d 投递%s %.2fs" % (i + 1, "✅" if dv else "❌假成功", dt))
        except Exception as e:
            print("  PW #%d 异常 %s" % (i + 1, str(e)[:60]))
        await asyncio.sleep(1)
    print("[%s] PW  → 真实投递 %d/%d，均 %.2fs/条\n" % (label, del_n, n, tot / n))
    await ctx.close()
    await pw.stop()
    shutil.rmtree(dst, ignore_errors=True)


def main():
    print("ORIGIN=%s UID=%s\n" % (ORIGIN, UID[:8]))
    which = sys.argv[1] if len(sys.argv) > 1 else "group"
    if which in ("group", "all"):
        print("========== 群聊对比 ==========")
        run_sio(GROUP_CID, "群聊", 3)
        asyncio.run(run_pw(GROUP_CID, "群聊", 3))
    if which in ("private", "all"):
        print("========== 私聊对比 ==========")
        run_sio(PRIVATE_CID, "私聊", 2)
        asyncio.run(run_pw(PRIVATE_CID, "私聊", 2))


main()
