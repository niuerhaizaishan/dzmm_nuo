#!/usr/bin/env python3
"""临时：验证 dzmm 私聊发送（给糯糯×小小糯私聊发一条测试消息）。"""
import asyncio
import sys

sys.path.insert(0, "/app")
from bot import Sender

PRIVATE = "4c52490e-839d-4c82-8064-23acaa1037a8"  # 小小糯～和御褚的私聊


async def main():
    sender = Sender(profile_dir="pw_profile_greet")
    try:
        await sender.start()
    except Exception as e:
        print("init failed:", str(e)[:80])
        return
    try:
        ok = await sender.send("🎲 御褚姐姐，私聊测试消息～这是骰子游戏的私聊通道验证 🎲2 🎲4 🎲6", PRIVATE)
        print("SEND RESULT:", "✅ 私聊发送成功" if ok else "❌ 失败")
    finally:
        await sender.close()


asyncio.run(main())
