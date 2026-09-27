"""P12-L：薇丝自我介绍配图（只发图）的守卫测试。

三件事最容易坏，测试盯这三个：

1. **意图判定不能太贪** —— 客人聊别的事不该蹦出一张图（"你是什么意思"这种不能命中），
   而"薇丝你是谁"「介绍一下你自己」必须命中；
2. **发不出去不能把主循环带崩** —— 没有配图 URL（认证群）/ 没有 Bot 通道 / 平台报错，
   都只能返回 False + 日志，绝不能抛异常（它挂在每条回复后面）；
3. **不能连发两张** —— 同一条消息可能被 @ 分支和房间分支各看到一次，要有去重窗口。
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import personas as P                                              # noqa: E402
from runtime import intro_photo as IP                             # noqa: E402
from runtime.transport_bot import (                               # noqa: E402
    BotApiAmbiguous, BotApiRejected, DzmmBotSender,
)

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


@pytest.fixture(autouse=True)
def _fresh():
    IP.reset_dedupe()
    yield
    IP.reset_dedupe()


class _Bot:
    def __init__(self, boom=False):
        self.calls = []
        self.boom = boom

    def send_photo_url(self, cid, url, caption=""):
        self.calls.append((cid, url, caption))
        if self.boom:
            raise BotApiRejected("Bot is not a member of this chatroom")
        return "mid-1234"


class _Sender:
    """只有 `bot_for` 的假 sender（真实链路上 OutboxSender 会透传给 RoutingSender）."""

    def __init__(self, bot):
        self.bot = bot

    def bot_for(self, cid):
        return self.bot


# ---------------------------------------------------------------- 取值

def test_url_only_for_the_rose_room():
    assert IP.photo_url(ROSE).startswith("https://")
    assert IP.photo_url(ROSE).endswith(".png")
    assert IP.photo_url(AUTH) == ""          # 认证群不配图（空 = 不发）
    assert IP.photo_url("不存在的群") == ""


def test_phrase_is_registered_and_labelled():
    keys = P.known_phrase_keys("rose_butler")
    assert "intro_photo" in keys
    assert "intro_photo" in P.PHRASE_LABELS, "新增话术必须补中文标签"
    assert P.p("intro_photo", ROSE) == IP.DEFAULT_URL
    assert not P.p("intro_photo", AUTH)
    assert P.health("rose_butler") == [], P.health("rose_butler")


# ---------------------------------------------------------------- 意图

@pytest.mark.parametrize("text", [
    "你是谁", "薇丝你是谁", "薇丝是谁", "你是谁啊", "你叫什么名字", "你叫啥",
    "介绍一下你自己", "自我介绍一下", "你是干什么的", "@薇丝 你是谁",
])
def test_wants_photo_hits(text):
    assert IP.wants_photo(text, ROSE) is True, text


@pytest.mark.parametrize("text", [
    "", "今天天气不错", "摘玫瑰", "给我来杯酒", "你是什么意思", "这个怎么弄",
    # 太长 = 多半在聊别的（这种长句里即使夹着"你是谁"也不发图）
    "你是谁呀我好想知道你到底是谁呢能不能告诉我一下呗我真的很想认识你一下啦谢谢你哦"
    "顺便说说这间屋子的规矩还有你能变出什么东西来呢",
])
def test_wants_photo_misses(text):
    assert IP.wants_photo(text, ROSE) is False, text


def test_commands_are_not_natural_language_path():
    """`/自我介绍` 走命令分支（那边直接发图），这里必须放行、不重复判定。"""
    assert IP.wants_photo("/自我介绍", ROSE) is False
    assert IP.wants_photo("/你是谁", ROSE) is False


# ---------------------------------------------------------------- 发送

def test_send_posts_the_url_through_the_bot_channel():
    bot = _Bot()
    assert asyncio.run(IP.send(_Sender(bot), ROSE, reason="指令")) is True
    assert bot.calls == [(ROSE, IP.DEFAULT_URL, "")]


def test_send_skips_when_no_url_or_no_bot():
    bot = _Bot()
    assert asyncio.run(IP.send(_Sender(bot), AUTH)) is False       # 本群不配图
    assert bot.calls == []
    assert asyncio.run(IP.send(_Sender(None), ROSE)) is False      # 没有 Bot 通道
    assert asyncio.run(IP.send(object(), ROSE)) is False           # 连 bot_for 都没有


def test_send_never_raises_when_platform_rejects():
    bot = _Bot(boom=True)
    assert asyncio.run(IP.send(_Sender(bot), ROSE)) is False
    assert bot.calls, "应当真的尝试过发送"


def test_maybe_send_dedupes_the_same_message():
    bot = _Bot()
    s = _Sender(bot)
    assert asyncio.run(IP.maybe_send(s, ROSE, "薇丝你是谁")) is True
    assert asyncio.run(IP.maybe_send(s, ROSE, "薇丝你是谁")) is False   # 同一条只发一张
    assert len(bot.calls) == 1
    assert asyncio.run(IP.maybe_send(s, ROSE, "你叫什么")) is True      # 换了问法照样发
    assert len(bot.calls) == 2
    assert asyncio.run(IP.maybe_send(s, ROSE, "今天天气不错")) is False  # 不是问身份
    assert len(bot.calls) == 2


# ---------------------------------------------------------------- 官方通道

class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, proxies=None):
        self.calls.append({"url": url, "json": json})
        return self.response


def _sender(response):
    s = FakeSession(response)
    return DzmmBotSender("https://www.example.invalid", "tok-123", session=s), s


def test_send_photo_url_request_shape():
    bot, session = _sender(FakeResponse(200, {"ok": True, "result": {"message_id": "m9"}}))
    assert bot.send_photo_url(ROSE, IP.DEFAULT_URL) == "m9"
    call = session.calls[0]
    assert call["url"] == ("https://www.example.invalid/api/bot/bot"
                           + "tok-123" + "/sendPhoto")
    assert call["json"] == {"chat_id": ROSE, "photo": IP.DEFAULT_URL}


def test_send_photo_url_caption_is_optional():
    bot, session = _sender(FakeResponse(200, {"ok": True, "result": {"message_id": "m"}}))
    bot.send_photo_url(ROSE, IP.DEFAULT_URL, caption="薇丝")
    assert session.calls[0]["json"]["caption"] == "薇丝"
    bot.send_photo_url(ROSE, IP.DEFAULT_URL)
    assert "caption" not in session.calls[1]["json"]


def test_send_photo_url_keeps_the_two_failure_kinds():
    """图片失败必须和文本失败一样分类：明确被拒可重发，不明确绝不重发。"""
    bot, _ = _sender(FakeResponse(200, {"ok": False, "message": "Bot not found"}))
    with pytest.raises(BotApiRejected):
        bot.send_photo_url(ROSE, IP.DEFAULT_URL)
    bot, _ = _sender(FakeResponse(200, {"ok": True, "result": {}}))
    with pytest.raises(BotApiAmbiguous):
        bot.send_photo_url(ROSE, IP.DEFAULT_URL)
    bot, _ = _sender(FakeResponse(503, None))
    with pytest.raises(BotApiAmbiguous):
        bot.send_photo_url(ROSE, IP.DEFAULT_URL)
    bot, _ = _sender(FakeResponse(200, None))
    with pytest.raises(BotApiAmbiguous):
        bot.send_photo_url(ROSE, IP.DEFAULT_URL)


def test_send_photo_url_rejects_empty_url():
    bot, _ = _sender(FakeResponse(200, {"ok": True}))
    with pytest.raises(BotApiRejected):
        bot.send_photo_url(ROSE, "")


# ---------------------------------------------------------------- 接线

def test_bot_wires_all_three_paths():
    """三个挂点一个都不能少（屋主 2026-09-27 定："两处都发 + 自然语言也发"）."""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert src.count("_intro_photo.send(") >= 2, "进房问好 + /自我介绍 两处要发"
    assert src.count("_intro_photo.maybe_send(") >= 2, "房间消息 + @提问 两处要发"
    assert 'reason="进房问好"' in src
    assert 'reason="指令"' in src
