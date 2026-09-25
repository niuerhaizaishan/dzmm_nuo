"""发送路由单测：通道选择 + 回退铁律。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.routing import RoutingSender           # noqa: E402
from runtime.transport_bot import BotApiAmbiguous, BotApiRejected  # noqa: E402

GROUP = "group-cid"
OTHER = "other-group-cid"


class FakeAccount:
    def __init__(self, result=True):
        self.result = result
        self.calls = []
        self.marker = "account-attr"

    async def send(self, text, cid, allow_fallback=True):
        self.calls.append((text, cid, allow_fallback))
        return self.result

    def start(self):
        return "account-started"


class FakeBot:
    def __init__(self, exc=None, mid="mid-1"):
        self.exc = exc
        self.mid = mid
        self.calls = []

    def send_text(self, chatroom_id, text):
        self.calls.append((chatroom_id, text))
        if self.exc:
            raise self.exc
        return self.mid


def build(bot=None, account_result=True):
    account = FakeAccount(account_result)
    events = []
    r = RoutingSender(account, {GROUP: bot} if bot else {},
                      on_route=lambda e, c, d="": events.append((e, c, d)))
    return r, account, events


# ---------------------------------------------------------------- 属性透传

def test_proxies_unknown_attributes():
    r, account, _ = build()
    assert r.marker == "account-attr"
    assert r.start() == "account-started"


def test_bot_cids_exposed():
    r, _, _ = build(FakeBot())
    assert r.bot_cids == (GROUP,)
    assert r.bot_for(GROUP) is not None
    assert r.bot_for(OTHER) is None


# ---------------------------------------------------------------- 选通道

def test_group_with_bot_uses_bot():
    bot = FakeBot()
    r, account, events = build(bot)
    assert asyncio.run(r.send("你好", GROUP)) is True
    assert bot.calls == [(GROUP, "你好")]
    assert account.calls == []
    assert events[0][0] == "bot_ok"


def test_other_group_uses_account():
    r, account, events = build(FakeBot())
    assert asyncio.run(r.send("你好", OTHER)) is True
    assert account.calls == [("你好", OTHER, True)]
    assert events[0][0] == "account"


def test_no_bot_configured_uses_account():
    r, account, _ = build(None)
    assert asyncio.run(r.send("x", GROUP)) is True
    assert account.calls == [("x", GROUP, True)]


def test_allow_fallback_forwarded_to_account():
    r, account, _ = build(None)
    asyncio.run(r.send("x", OTHER, allow_fallback=False))
    assert account.calls == [("x", OTHER, False)]


# ---------------------------------------------------------------- 回退铁律

def test_explicit_rejection_falls_back_to_account():
    """明确拒绝 = 确定没投递 → 允许回退账号。"""
    bot = FakeBot(exc=BotApiRejected("Bot is not a member of this chatroom"))
    r, account, events = build(bot)
    assert asyncio.run(r.send("你好", GROUP)) is True
    assert bot.calls == [(GROUP, "你好")]
    assert account.calls == [("你好", GROUP, True)]
    assert [e[0] for e in events] == ["bot_rejected", "account"]


def test_ambiguous_failure_does_not_fall_back():
    """不明确 = 可能已投递 → **绝不**换通道重发（DDZM 铁律）。"""
    bot = FakeBot(exc=BotApiAmbiguous("网络错误：Timeout"))
    r, account, events = build(bot)
    assert asyncio.run(r.send("你好", GROUP)) is False
    assert bot.calls == [(GROUP, "你好")]
    assert account.calls == []                    # ← 关键：账号通道一次都没被调用
    assert [e[0] for e in events] == ["bot_ambiguous"]


def test_account_result_is_returned():
    r, _, _ = build(None, account_result=False)
    assert asyncio.run(r.send("x", OTHER)) is False


# ---------------------------------------------------------------- 审计

def test_counts_accumulate():
    bot = FakeBot()
    r, _, _ = build(bot)
    asyncio.run(r.send("a", GROUP))       # bot_ok
    asyncio.run(r.send("b", OTHER))       # account
    assert r.counts["bot_ok"] == 1
    assert r.counts["account"] == 1
    assert r.counts["bot_ambiguous"] == 0


def test_broken_audit_callback_does_not_break_send():
    account = FakeAccount()

    def boom(*_a, **_kw):
        raise RuntimeError("audit down")

    r = RoutingSender(account, {}, on_route=boom)
    assert asyncio.run(r.send("x", OTHER)) is True
