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

    def __init__(self, account, bot_senders: dict | None = None, on_route=None,
                 official_only: bool = False):
        self._account = account
        self._bots = dict(bot_senders or {})     # {chatroom_id: DzmmBotSender}
        self._on_route = on_route                # 可选的审计回调 (event, cid, detail)
        # P12-J：只走官方通道。开着时，Bot 明确拒绝（BotApiRejected）也**不回退账号** ——
        # 代价是那条消息会丢；好处是"群里每一条都是 Bot 发的"这件事不再有例外。
        # 默认关：回退是为了撞验证码时别丢消息（丢消息比晚一点更糟）。
        self._official_only = bool(official_only)
        # P12-J：**最近一次发送的失败原因**，供出站队列决定"能不能安全重试"。
        # 只有 `rejected` 是"平台明确拒绝、确定未投递"⇒ 可以重发；
        # `ambiguous`（网络错误/非 200/缺 message_id）**绝不能重发**（可能已投递）。
        # 见 runtime/outbox.py 里那条铁律：平台未明确 ACK 时不换通道重发。
        self.last_failure = ""
        self.counts = {"bot_ok": 0, "bot_rejected": 0,
                       "bot_ambiguous": 0, "account": 0,
                       "dropped_official_only": 0}

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

    async def send_with_id(self, text: str, cid: str) -> str:
        """返回确定的消息编号；确认提示禁止模糊失败后换通道重发。

        ⚠️ **一律走账号通道**（屋主 2026-10-12）：本方法目前只有 Rose 管理链路在用
        （提示/结果 + 绑定编号），而撤回/踢出**只能**由账号连接执行
        （官方 Bot 没有撤回/踢出权限）——整条链必须是同一个执行者
        （侧车账号「小小糯～」）。原先"有 Bot 就走 Bot"会让提示以薇丝的身份出现、
        与实际动手的账号身份劈叉，屋主明确不许。

        失败语义不变：这里抛出的异常由 OutboxSender 记 failed，**绝不回退 Bot**
        （换个身份重发比丢一条提示更糟）。
        """
        self._note("account", cid)
        return await self._account.send_with_id(text, cid)

    async def send(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        from runtime import lambda_bot
        if not lambda_bot.is_control_send() and lambda_bot.suppress_outbound(cid):
            return False
        text = lambda_bot.render_text(text, cid)
        bot = self._bots.get(cid)
        if bot is not None:
            try:
                # requests 是同步的，丢线程池，别阻塞事件循环
                mid = await asyncio.to_thread(bot.send_text, cid, text)
                self._note("bot_ok", cid, mid)
                self.last_failure = ""
                return True
            except BotApiRejected as e:
                # 明确拒绝、确定未投递 → 默认允许回退账号（撞验证码时别丢消息）
                self._note("bot_rejected", cid, str(e))
                self.last_failure = "rejected"
                if self._official_only:
                    # 只走官方通道：这条失败会被出站队列**在官方通道上重试**，
                    # 而不是悄悄换成账号发（出站队列按 last_failure 判断可否安全重试）
                    self._note("dropped_official_only", cid, str(e))
                    return False
            except BotApiAmbiguous as e:
                # 可能已投递 → 绝不重发，直接判失败
                self._note("bot_ambiguous", cid, str(e))
                self.last_failure = "ambiguous"
                return False

        self._note("account", cid)
        ok = await self._account.send(text, cid, allow_fallback=allow_fallback)
        self.last_failure = "" if ok else "account_unknown"
        return ok

    async def send_reply_with_id(self, text: str, cid: str, reference: dict, *, guard=None) -> str:
        """茶水间引用回复只走账号；不得悄悄丢失引用或拆分正文。"""
        self._note("account", cid)
        return await self._account.send_reply_with_id(text, cid, reference, guard=guard)

    async def send_character_with_id(self, resource_id: str, cid: str) -> str:
        """官方文本接口不能携带 share，明确路由到账号通道。"""
        self._note("account", cid)
        return await self._account.send_character_with_id(resource_id, cid)
