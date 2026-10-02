# -*- coding: utf-8 -*-
"""P12-AZ 部署后复核：在**部署后的代码**里直接调 `Sender._sio_send_long()`。

和探针①/② 的区别：那两个是把 Socket.IO 的流程抄了一遍；
这个用的是**容器里那份 bot.py**（所以它同时验证了接线、预算、读回新鲜度判定）。

往自检群 `bot测试`（12 人）真发一张 **278 字 / 9 行**的运势卡形状文本，
然后打印：
  · `_sio_send_long` 的返回值与耗时；
  · 群里落地文本的真实长度与行数（一条 = 长度与发出的一致）。

用法（容器内）：`python /app/probe_deployed_sio_long.py`
"""
import asyncio
import sys
import time

sys.path.insert(0, "/app")

import bot                                                      # noqa: E402

CID = "f71ef7ff-5faf-4e5d-9dd6-8e930c574de3"     # bot测试（自检群，12 人）
TAG = "P12AZCHK" + str(int(time.time()))[-5:]


def main():
    card = ("{} 🤖 🔮 墨莲 今日运势 83分（吉）\n"
            "————————————\n"
            "📜 命盘 →  本命—：土 | 名格：火 |  今日：丁未火（阴） → 本命受生、名格比和\n"
            "📅 宜社交、表白 ｜ 忌独处、争执 ｜ 冲牛\n"
            "🧮 命基55 + 名格8 + 本命15 − 阴阳3 + 人和8 = 83\n"
            "📈 💰财运78 · 💼事业89 · 💗感情88 · 🩺健康75\n"
            "🍀 幸运：绛红 · 7 · 正南\n"
            "————————————\n"
            "墨莲姐姐今天本命土受生、名格火比和，事业感情都旺旺的～"
            "就是健康75分垫底，忙起来别又忘了好好吃饭呀，小小糯心疼你♡").format(TAG)
    print("发出 {} 字 / {} 行 | tag = {}".format(
        len(card), card.count("\n") + 1, TAG))
    s = bot.Sender.__new__(bot.Sender)          # 不需要浏览器
    t0 = time.time()
    ok = asyncio.run(bot.Sender._sio_send_long(s, card, CID))
    cost = time.time() - t0
    print("_sio_send_long -> {}（{:.1f}s，预算 {}s / 最多 {} 次）".format(
        ok, cost, bot.SIO_LONG_BUDGET_SEC, bot.SIO_LONG_MAX_TRIES))

    time.sleep(2)
    got = []
    for m in bot.get_messages(CID, 10) or []:
        t = (m.get("content") or {}).get("text") or ""
        if TAG in t:
            got.append(t)
    if not got:
        print("❌ 读回：没找到（说明这条路这轮没成，生产会退回切分打字）")
        return 1
    for t in got:
        print("✅ 读回：{} 字 / {} 行（{}）".format(
            len(t), t.count("\n") + 1,
            "与发出**完全一致** ⇒ 一条" if t == card else "与发出不一致！"))
    return 0 if (got and got[0] == card and ok) else 1


if __name__ == "__main__":
    sys.exit(main())
