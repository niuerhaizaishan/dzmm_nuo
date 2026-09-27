"""官方 Bot API 发送客户端。

逻辑参考 DDZM-bot `src/dzmm_bot/browser/bot_api.py`（那边已在生产验证），
但改为用本项目已有的 `requests`（不引入 httpx），且 **origin 走配置**
（生产容器用的是 `jadelune.xyz` 镜像，本机测过 `dzmm.ai` 也通 —— Token 跨镜像通用，
所以绝不能硬编码单一镜像域）。

关键设计：**区分"明确拒绝"与"不明确失败"**。

来自 DDZM 接入指南 §8.5 的铁律：
    平台没有明确成功 ACK 时，**不能立即换通道重复发送**，
    否则用户可能看到两条一样的消息。

- `BotApiRejected`  —— 平台**明确告知失败**（可解析的 `ok:false`，或 4xx）。
  此时确定没投递，**可以安全换通道重发**。
- `BotApiAmbiguous` —— 超时 / 网络错误 / 5xx / 响应不可解析 / `ok:true` 但缺 message_id。
  此时**可能已经投递**，绝不能重发。
"""
import requests

__all__ = [
    "BotApiError", "BotApiRejected", "BotApiAmbiguous", "DzmmBotSender",
    "BOT_SEND_PATH", "BOT_PHOTO_PATH",
]

BOT_SEND_PATH = "/api/bot/send-message"
# Telegram 兼容形态，**只吃 URL 图片**（没有 multipart），见 docs/BotAPI能力矩阵.md。
BOT_PHOTO_PATH = "/api/bot/bot{token}/sendPhoto"


class BotApiError(Exception):
    """Bot API 调用失败的基类。"""


class BotApiRejected(BotApiError):
    """平台明确拒绝 —— 确定未投递，可安全换通道重发。"""


class BotApiAmbiguous(BotApiError):
    """失败不明确 —— 可能已投递，**禁止**换通道重发。"""


class DzmmBotSender:
    """最小发送客户端：只做「群文本发送」，不该有私聊/撤回接口。

    刻意**不提供**私聊与撤回能力：官方 Bot 不支持私聊，也不能 `message:recall`。
    把它们做成"拿不到的方法"，比写成注释更可靠。
    """

    def __init__(self, origin: str, token: str, *, proxy: str | None = None,
                 timeout: int = 20, session: requests.Session | None = None):
        if not token:
            raise ValueError("Bot API token 不能为空")
        if not origin:
            raise ValueError("origin 不能为空")
        self.origin = origin.rstrip("/")
        self.token = token
        self.proxy = proxy or None
        self.timeout = timeout
        self._session = session or requests.Session()

    # ------------------------------------------------------------ 发送

    def send_text(self, chatroom_id: str, text: str) -> str:
        """往群里发文本。成功返回平台 message_id；失败按上面两类抛异常。"""
        url = self.origin + BOT_SEND_PATH
        try:
            resp = self._session.post(
                url,
                json={"chatroom_id": chatroom_id, "content": text},
                headers={"X-Bot-Token": self.token, "Content-Type": "application/json"},
                timeout=self.timeout,
                proxies=self._proxies(),
            )
        except requests.RequestException as e:
            raise BotApiAmbiguous("网络错误：{}".format(type(e).__name__)) from e
        return self._finish(resp)

    def send_photo_url(self, chatroom_id: str, photo_url: str,
                       caption: str = "") -> str:
        """按 **URL** 发一张图（P12-L：官方只吃 URL，没有 multipart）。

        走 Telegram 兼容形态 `POST /api/bot/bot<token>/sendPhoto`，
        形态差异（B 形式**一律 200** + `message` 字段）由 `_finish` 统一消化 ——
        失败判定必须与 `send_text` **完全一致**，否则"图片失败"就绕过了那条铁律。
        """
        if not photo_url:
            raise BotApiRejected("图片 URL 为空")
        url = self.origin + BOT_PHOTO_PATH.format(token=self.token)
        payload = {"chat_id": chatroom_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption
        try:
            resp = self._session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
                proxies=self._proxies(),
            )
        except requests.RequestException as e:
            raise BotApiAmbiguous("网络错误：{}".format(type(e).__name__)) from e
        return self._finish(resp)

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _finish(resp) -> str:
        """把响应变成 message_id；两种错误形状都认（见模块顶部那条铁律）."""
        # 5xx：服务端故障，不明确
        if resp.status_code >= 500:
            raise BotApiAmbiguous("HTTP {}".format(resp.status_code))

        try:
            body = resp.json()
        except ValueError as e:
            raise BotApiAmbiguous(
                "响应非 JSON（HTTP {}）：{}".format(resp.status_code, resp.text[:120])) from e

        if not isinstance(body, dict):
            raise BotApiAmbiguous("响应结构异常：{}".format(str(body)[:120]))

        if body.get("ok") is not True:
            # 能解析出的 ok:false = 平台明确告知失败 → 未投递
            msg = body.get("error") or body.get("message") or body.get("details") \
                or "HTTP {}".format(resp.status_code)
            raise BotApiRejected(str(msg)[:200])

        mid = (body.get("result") or {}).get("message_id")
        if not isinstance(mid, str) or not mid:
            # ok=true 却没给 message_id：极可能**已经投递**了 → 当作不明确，禁止重发
            raise BotApiAmbiguous("ok=true 但缺少 message_id")
        return mid

    def _proxies(self):
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}
