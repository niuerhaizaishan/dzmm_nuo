# -*- coding: utf-8 -*-
"""P12-AZ 探针③：能不能用**已经连着的那条订阅长连接**发消息？

`sio_subscribe.SioSubscriber` 已经为读消息维护着一条 Socket.IO 长连接
（`auth={token}`、`transports=["polling","websocket"]`、断线自动重连）。
P12-AZ 想让它**顺手也能发**：这条路不用握手 ⇒ 不会撞
`connect=400 {"code":1,"message":"Session ID unknown"}`
（HTTP 那条路实测要重试 4~6 次才成）。

本探针在自检群 `bot测试` 里真发真读回，连发 3 条 **271 字 / 9 行**的运势卡形状，
统计这条路的成功率与耗时。

用法（容器内）：`python /app/probe_live_socket_send.py`
"""
import asyncio
import inspect
import json
import sys
import time
import uuid

sys.path.insert(0, "/app")

import dzmm                                                    # noqa: E402
from runtime import identity as ID                              # noqa: E402

CID = "f71ef7ff-5faf-4e5d-9dd6-8e930c574de3"     # bot测试（自检群，12 人）
TAG = "P12CLIVE" + str(int(time.time()))[-5:]


def _card(mark):
    return ("{} {} 🤖 🔮 墨莲 今日运势 83分（吉）\n"
            "————————————\n"
            "📜 命盘 →  本命—：土 | 名格：火 |  今日：丁未火（阴） → 本命受生、名格比和\n"
            "📅 宜社交、表白 ｜ 忌独处、争执 ｜ 冲牛\n"
            "🧮 命基55 + 名格8 + 本命15 − 阴阳3 + 人和8 = 83\n"
            "📈 💰财运78 · 💼事业89 · 💗感情88 · 🩺健康75\n"
            "🍀 幸运：绛红 · 7 · 正南\n"
            "————————————\n"
            "墨莲姐姐今天本命土受生、名格火比和，事业感情都旺旺的～"
            "就是健康75分垫底，忙起来别又忘了好好吃饭呀，小小糯心疼你♡").format(mark, TAG)


def _landed(text):
    try:
        for m in dzmm.get_messages(CID, 20) or []:
            t = (m.get("content") or {}).get("text") or ""
            if t and t[:10] == text[:10]:
                return t
    except Exception as e:                                      # noqa: BLE001
        print("  读回异常:", str(e)[:80])
    return None


async def main():
    from sio_subscribe import SioSubscriber

    print("tag =", TAG, "| uid =", ID.NUO_UID[:8])
    sub = SioSubscriber([CID])
    task = asyncio.create_task(sub.start())
    # 这条路建立连接也可能要试好几次（HTTP 那条路实测要 4~6 次），给足 90s
    for i in range(90):
        await asyncio.sleep(1)
        if sub.is_connected:
            break
        if i and i % 15 == 0:
            print("  …还在连（已等 {}s）".format(i))
    print("长连接状态：{}（等了 {}s）".format(
        "已连接" if sub.is_connected else "❌ 没连上", i + 1))
    if not sub.is_connected:
        task.cancel()
        return 1

    print("python-socketio emit 是协程吗：{}".format(
        inspect.iscoroutinefunction(sub._sio.emit)))
    sent = []
    for i in range(1, 4):
        text = _card("LIVE{}".format(i))
        mid = str(uuid.uuid4())
        msg = {"message_id": mid, "chatroom_id": CID, "sent_by": ID.NUO_UID,
               "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
               "content": {"type": "text", "text": text}}
        t0 = time.time()
        try:
            r = sub._sio.emit("message:send", {"chatroomId": CID, "message": msg})
            if inspect.isawaitable(r):
                await r
            err = ""
        except Exception as e:                                  # noqa: BLE001
            err = str(e)[:80]
        await asyncio.sleep(2)
        got = _landed(text)
        sent.append(bool(got))
        print("#{} emit{}{} → {}（{:.1f}s）".format(
            i, "（异常：" + err + "）" if err else " ok",
            "", "✅ 完整落地 {} 字".format(len(got)) if got else "❌ 没落地",
            time.time() - t0))
        await asyncio.sleep(1)

    print()
    print("成功率 {}/3".format(sum(sent)))
    try:
        await sub.stop()
    except Exception:                                           # noqa: BLE001
        pass
    task.cancel()
    return 0 if all(sent) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
