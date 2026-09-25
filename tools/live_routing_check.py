"""真机验证**发送链**：不产生任何可见群消息。

做法：让路由把消息交给官方 Bot，但目标 cid 用**不存在的 UUID**。
平台会返回明确的 403 `Bot is not a member of this chatroom` →
`BotApiRejected` → 路由回退到（假的）账号通道。

这样一次性验证四件事，且**什么都不发出去**：
  1. 路由确实把该 cid 判给了 Bot
  2. 真实平台调用与响应形状符合预期
  3. `ok:false` 被正确归类为「明确拒绝」
  4. 明确拒绝时确实回退了账号

用法（本机需走代理，dzmm 域被 SNI 阻断）：
    set DZMM_BOT_TOKEN=...  &  python tools/live_routing_check.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.routing import RoutingSender            # noqa: E402
from runtime.transport_bot import DzmmBotSender      # noqa: E402

BOGUS_CID = "00000000-0000-0000-0000-000000000000"


class RecordingAccount:
    """假账号通道：只记录，不真发。"""

    def __init__(self):
        self.calls = []

    async def send(self, text, cid, allow_fallback=True):
        self.calls.append((text, cid, allow_fallback))
        return True


def main() -> int:
    token = (os.environ.get("DZMM_BOT_TOKEN") or "").strip()
    if not token:
        print("缺少 DZMM_BOT_TOKEN")
        return 1
    origin = (os.environ.get("DZMM_BOT_ORIGIN") or "https://www.dzmm.ai").rstrip("/")
    proxy = os.environ.get("DZMM_PROXY") or "http://127.0.0.1:7890"

    print("origin = {}".format(origin))
    print("proxy  = {}".format(proxy))
    print("目标cid = {}（**不存在的群**，不会发出任何消息）".format(BOGUS_CID))
    print()

    events = []
    account = RecordingAccount()
    bot = DzmmBotSender(origin, token, proxy=proxy)
    router = RoutingSender(account, {BOGUS_CID: bot},
                           on_route=lambda e, c, d="": events.append((e, c, str(d)[:90])))

    ok = asyncio.run(router.send("【链路自检】这条不会发出", BOGUS_CID))
    print("send() 返回 :", ok)
    print("路由事件    :")
    for e in events:
        print("   {}  {}".format(e[0], e[2]))
    print("账号通道被调:", account.calls)
    print("计数        :", router.counts)

    print()
    checks = [
        ("路由判给了 Bot", any(e[0] in ("bot_ok", "bot_rejected", "bot_ambiguous") for e in events)),
        ("平台明确拒绝（bot_rejected）", any(e[0] == "bot_rejected" for e in events)),
        ("明确拒绝后回退了账号", bool(account.calls)),
        ("没有误判为不明确", not any(e[0] == "bot_ambiguous" for e in events)),
    ]
    bad = 0
    for name, passed in checks:
        print("  [{}] {}".format("OK" if passed else "!!", name))
        bad += 0 if passed else 1
    print()
    print("全部通过" if not bad else "有 {} 项未通过".format(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
