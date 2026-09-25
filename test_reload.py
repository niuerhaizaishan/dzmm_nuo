#!/usr/bin/env python3
"""测 reload_page 耗时（用独立 profile，避免与主循环冲突）。"""
import asyncio
import sys
import time

sys.path.insert(0, "/app")
import bot


async def main():
    sender = bot.Sender(profile_dir="pw_profile_diag")
    try:
        await sender.start()
    except Exception as e:
        print("start 失败:", str(e)[:100])
        return
    print("start ok", flush=True)
    # 测两次 reload
    for i in range(2):
        t0 = time.time()
        await sender.reload_page()
        print(f"reload #{i+1} 耗时: {time.time() - t0:.2f}s", flush=True)
    await sender.close()


asyncio.run(main())
