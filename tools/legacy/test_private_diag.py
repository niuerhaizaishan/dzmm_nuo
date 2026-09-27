#!/usr/bin/env python3
"""临时：打印御褚私聊 textarea 的 placeholder，对比群聊。"""
import asyncio
import sys

sys.path.insert(0, "/app")
from bot import Sender, ORIGIN

YUCHU_CHAT = "4c52490e-839d-4c82-8064-23acaa1037a8"


async def main():
    sender = Sender(profile_dir="pw_profile_greet")
    await sender.start()
    try:
        await sender.page.goto(ORIGIN + "/chat?c=" + YUCHU_CHAT, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)
        tas = await sender.page.query_selector_all("textarea")
        print("御褚私聊 textarea 数量:", len(tas))
        for ta in tas:
            try:
                ph = await ta.get_attribute("placeholder")
                vis = await ta.is_visible()
                print("  placeholder=", repr(ph), "| visible=", vis)
            except Exception as e:
                print("  err:", str(e)[:60])
    finally:
        await sender.close()


asyncio.run(main())
