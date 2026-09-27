"""P12-K 线上实拍：在容器里真跑一次星象卡（**不发群**）。

    docker exec dzmm-bot python /tmp/probe_sign_card.py

做两件事：
1. 直接调 `generate_horoscope` 看模型**原始输出**是否守五段格式（这是最大的风险点：
   玫瑰房人设 `ROSE_BUTLER_EXTRA` 在 system prompt 末尾追加了"4~8 句 / 分行不超过 10 行"，
   可能把"五段、每段一行"的指令顶掉）；
2. 调真正的 `_sign_card` 走完整链路，打印卡片成品与耗时。

只读、不写任何状态、不发消息。
"""
import asyncio
import sys
import time
from datetime import datetime

sys.path.insert(0, "/app")

import brains                     # noqa: E402
import personas                   # noqa: E402
import runtime.sign as S          # noqa: E402

CID = "4a56be71-9ca6-4785-b45f-18725d187f1a"       # 玫瑰房
UID = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"       # 屋主 Vv
SIGN = "金牛"

personas.set_cid(CID)                              # 复现线上的人设注入
day = datetime.now().strftime("%Y-%m-%d")
r = S._sign_calc(UID, SIGN, day)

print("== 确定性数据 ==")
print("dims   :", r["dims"], "total:", r["total"], "health:", r["health"])
print("luck   :", r["luck"])
print("速配候选:", personas.ROSE_SIGN_MATCH.get(SIGN))
print()
print("== 模型原始输出 ==")
t0 = time.time()
raw = brains.generate_horoscope(
    SIGN, "土", "金星", r["luck"]["moon"], r["dims"], r["luck"]["yi"],
    r["luck"]["ji"], day, "Vv姐姐", "", r["health"], r["total"])
t1 = time.time()
print(raw)
print("-- 原始输出 {} 字 / {} 行 / 耗时 {:.2f}s".format(
    len(raw), len(raw.split("\n")), t1 - t0))
print()
print("== _split_sections 解析 ==")
secs = S._split_sections(raw)
print("段数:", len(secs), "顺序:", list(secs))
for k, v in secs.items():
    print("  [{}] {} 字".format(k, len(v)))
print()
print("== _sign_card 成品 ==")
t2 = time.time()
card = asyncio.run(S._sign_card(CID, UID, SIGN, 4, 25, "Vv姐姐"))
t3 = time.time()
print(card)
print("-- 卡片 {} 行 / {} 字 / 生成+装配 {:.2f}s".format(
    len(card.split("\n")), len(card), t3 - t2))
