"""发送路由：按路由表在「官方 Bot」与「普通账号」之间选择通道。

分层位置（自外向内）：

    OutboxSender      ← P3：落库 / 租约 / 按群串行
      └─ RoutingSender ← 本模块：选通道
           ├─ DzmmBotSender      官方 Bot（群文本）
           └─ Sender             普通账号（私聊 / 图片 / 分享 / 撤回 / 其他群）

路由判定**按 cid 归属**，而不是靠猜"是群还是私聊"：
`bot_cids` 里只会放进真正装了 Bot 的群（现阶段只有认证群一个）。
其他群、私聊、以及 `_rollback_last_id` 之类的既有路径一律走账号 —— 与现网一致。

铁律（DDZM 接入指南 §8.5）：**平台未明确 ACK 时，绝不换通道重发。**
所以只有 `BotApiRejected`（明确拒绝，确定未投递）才回退账号；
`BotApiAmbiguous`（超时/网络/5xx/响应不可解析）**直接放弃，不重发**。
"""
import asyncio

from .transport_bot import BotApiAmbiguous, BotApiRejected

__all__ = ["RoutingSender"]


class RoutingSender:
    """把 `account` 包一层：装了 Bot 的群优先走 Bot，其余照旧走账号。

    未覆盖的属性（`start` / `navigate` / `close` …）透传给账号 sender，
    因此调用点与 `main_loop` 的其余代码都无需改动。
    """

    def __init__(self, account, bot_senders: dict | None = None, on_route=None):
        self._account = account
        self._bots = dict(bot_senders or {})     # {chatroom_id: DzmmBotSender}
        self._on_route = on_route                # 可选的审计回调 (event, cid, detail)
        self.counts = {"bot_ok": 0, "bot_rejected": 0,
                       "bot_ambiguous": 0, "account": 0}

    def __getattr__(self, name):
        return getattr(self._account, name)

    @property
    def bot_cids(self) -> tuple:
        return tuple(self._bots)

    def bot_for(self, cid: str):
        return self._bots.get(cid)

    def _note(self, event: str, cid: str, detail: str = "") -> None:
        self.counts[event] = self.counts.get(event, 0) + 1
        if self._on_route:
            try:
                self._on_route(event, cid, detail)
            except Exception:                     # 审计失败绝不影响发送
                pass

    async def send(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        bot = self._bots.get(cid)
        if bot is not None:
            try:
                # requests 是同步的，丢线程池，别阻塞事件循环
                mid = await asyncio.to_thread(bot.send_text, cid, text)
                self._note("bot_ok", cid, mid)
                return True
            except BotApiRejected as e:
                # 明确拒绝、确定未投递 → 允许回退账号
                self._note("bot_rejected", cid, str(e))
            except BotApiAmbiguous as e:
                # 可能已投递 → 绝不重发，直接判失败
                self._note("bot_ambiguous", cid, str(e))
                return False

        self._note("account", cid)
        return await self._account.send(text, cid, allow_fallback=allow_fallback)
