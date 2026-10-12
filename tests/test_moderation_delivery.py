import asyncio

import pytest

from runtime import sender as account_sender
from runtime.outbox import OutboxSender, OutboxStore
from runtime.routing import RoutingSender
from sio_subscribe import SioSubscriber


class FakeSocket:
    def __init__(self, acknowledgement=None, error=None):
        self.acknowledgement = acknowledgement
        self.error = error
        self.calls = []

    async def call(self, event, payload, timeout):
        self.calls.append((event, payload, timeout))
        if self.error:
            raise self.error
        return self.acknowledgement


def subscriber(socket):
    connection = SioSubscriber(["rose"])
    connection._sio = socket
    connection._connected.set()
    return connection


def test_delete_protocol_requires_positive_ack():
    """管理员删别人的消息走 message:delete（2026-10-12 实测：message:recall
    只能撤自己的，撤别人永远 CANNOT_RECALL）。"""
    socket = FakeSocket({"success": True})
    asyncio.run(subscriber(socket).recall_message("rose", "original-id"))
    assert socket.calls == [("message:delete", {"chatroomId": "rose", "messageId": "original-id"}, 10)]


@pytest.mark.parametrize("ack", [None, {}, {"success": False}, {"success": 1}, {"ok": True}])
def test_delete_missing_or_negative_ack_fails_without_retry(ack):
    socket = FakeSocket(ack)
    with pytest.raises(RuntimeError):
        asyncio.run(subscriber(socket).recall_message("rose", "original-id"))
    assert len(socket.calls) == 1


def test_delete_socket_timeout_never_retries():
    socket = FakeSocket(error=TimeoutError())
    with pytest.raises(TimeoutError):
        asyncio.run(subscriber(socket).recall_message("rose", "original-id"))
    assert len(socket.calls) == 1


def test_delete_disconnected_socket_has_no_effect():
    connection = subscriber(FakeSocket({"success": True}))
    connection._connected.clear()
    with pytest.raises(RuntimeError):
        asyncio.run(connection.recall_message("rose", "original-id"))
    assert connection._sio.calls == []


def test_account_prompt_id_comes_from_confirmed_socket(monkeypatch):
    socket = FakeSocket({"success": True})
    monkeypatch.setattr(account_sender, "_LIVE_SIO", subscriber(socket))
    account = account_sender.Sender()
    message_id = asyncio.run(account.send_with_id("确认提示", "rose"))
    event, payload, _ = socket.calls[0]
    assert event == "message:send"
    assert message_id == payload["message"]["message_id"]
    assert payload["message"]["content"]["text"] == "确认提示"


class FakeAccount:
    def __init__(self):
        self.calls = []

    async def send_with_id(self, text, cid):
        self.calls.append((text, cid))
        return "account-id"


class FakeBot:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def send_text(self, cid, text):
        self.calls.append((text, cid))
        if self.error:
            raise self.error
        return "bot-id"


def test_prompt_never_uses_the_room_bot_identity():
    """Rose 管理链路（屋主 2026-10-12）：提示/结果与撤回/踢出必须是**同一个执行者**
    （侧车账号「小小糯～」）。撤回/踢出只能走账号连接，所以确认提示**绝不走 Bot**
    —— 哪怕本群配了 Bot（rose 的 Bot 是薇丝），提示也不许以薇丝的身份出现。"""
    account = FakeAccount()
    bot = FakeBot()
    routing = RoutingSender(account, {"rose": bot})
    assert asyncio.run(routing.send_with_id("确认提示", "rose")) == "account-id"
    assert bot.calls == []
    assert asyncio.run(routing.send_with_id("确认提示", "account-room")) == "account-id"
    assert bot.calls == []


def test_account_prompt_failure_propagates_without_retry_or_fallback(dbdir):
    """账号通道失败：异常原样上抛（OutboxSender 记 failed），绝不换 Bot 通道重发
    —— 换个身份重发比丢一条提示更糟。"""
    class BrokenAccount:
        async def send_with_id(self, text, cid):
            raise RuntimeError("账号连接不可用")

    store = OutboxStore(dbdir / "outbox.db")
    bot = FakeBot()
    try:
        sender = OutboxSender(RoutingSender(BrokenAccount(), {"rose": bot}), store)
        with pytest.raises(RuntimeError):
            asyncio.run(sender.send_with_id("确认提示", "rose"))
        # 只记异常类型名（"确认提示未确认：RuntimeError"），错误正文（可能含敏感信息）不落库
        assert store.by_status("failed")[0]["last_error"] == "确认提示未确认：RuntimeError"
        assert asyncio.run(sender.retry_once()) == 0
        assert bot.calls == []
    finally:
        store.close()


def test_outbox_persists_prompt_platform_id(dbdir):
    store = OutboxStore(dbdir / "outbox.db")
    try:
        sender = OutboxSender(RoutingSender(FakeAccount(), {"rose": FakeBot()}), store)
        assert asyncio.run(sender.send_with_id("确认提示", "rose")) == "account-id"
        row = store.by_status("sent")[0]
        assert row["platform_sent_id"] == "account-id"
        assert row["text"] == "确认提示"
    finally:
        store.close()


def test_failed_prompt_is_audited_but_never_background_retried(dbdir):
    store = OutboxStore(dbdir / "outbox.db")
    bot = FakeBot()

    class LeakyAccount:
        async def send_with_id(self, text, cid):
            raise RuntimeError("private credential must never be echoed")

    try:
        sender = OutboxSender(RoutingSender(LeakyAccount(), {"rose": bot}), store)
        with pytest.raises(RuntimeError):
            asyncio.run(sender.send_with_id("确认提示", "rose"))
        # 只记异常类型名，错误正文（可能含敏感信息）不落库
        assert "private credential" not in store.by_status("failed")[0]["last_error"]
        assert asyncio.run(sender.retry_once()) == 0
        assert bot.calls == []
    finally:
        store.close()


def test_outbox_dry_does_not_send_or_enqueue(dbdir):
    store = OutboxStore(dbdir / "outbox.db")
    account = FakeAccount()
    try:
        sender = OutboxSender(account, store, dry=True)
        assert asyncio.run(sender.send_with_id("确认提示", "rose")) == ""
        assert account.calls == []
        assert store.pending_count() == 0
    finally:
        store.close()


@pytest.mark.parametrize("compat", [False, True])
def test_socket_handshake_fallback_preserves_token_and_message_delivery(monkeypatch, compat):
    import sio_subscribe
    connections = []
    callbacks = {}
    class Client:
        def __init__(self, **kwargs):
            pass
        def on(self, event):
            def register(callback):
                callbacks[event] = callback
                return callback
            return register
        async def connect(self, url, **kwargs):
            connections.append((url, kwargs))
    monkeypatch.setattr(sio_subscribe.socketio, "AsyncClient", Client)
    monkeypatch.setattr(sio_subscribe.token_store, "get_cookie_string", lambda: "test-cookie=synthetic")
    sub = SioSubscriber(["rose"])
    sub._compat_connect = compat
    sub._http = object()
    monkeypatch.setattr(sub, "_get_token", lambda: "synthetic-token")
    async def scenario():
        await sub._connect()
        await callbacks["connect"]()
        await callbacks["message:new"]({"chatroomId": "rose", "message": {
            "message_id": "natural-message", "sent_by": "synthetic-user", "content": {"text": "offline"}}})
    asyncio.run(scenario())
    url, options = connections[0]
    assert options["auth"] == {"token": "synthetic-token"}
    assert options["socketio_path"] == "/ws/matching"
    assert sub.is_connected and sub.need_resync("rose")
    assert sub.drain("rose")[0]["message_id"] == "natural-message"
    if compat:
        assert url == sio_subscribe.ORIGIN
        assert options["headers"]["Cookie"] == "test-cookie=synthetic"
        assert options["transports"] == ["websocket", "polling"]
    else:
        assert "syncMode=http" in url
        assert "Cookie" not in options["headers"]
        assert options["transports"] == ["polling", "websocket"]


def test_namespace_timeout_switches_handshake_without_sending(monkeypatch):
    import sio_subscribe
    sub = SioSubscriber(["rose"])
    modes = []
    async def wait(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError()
    async def connect():
        modes.append(sub._compat_connect)
        if len(modes) == 2:
            raise asyncio.CancelledError()
    async def cleanup():
        return None
    monkeypatch.setattr(sub, "_connect", connect)
    monkeypatch.setattr(sub, "_cleanup", cleanup)
    monkeypatch.setattr(sio_subscribe.asyncio, "wait_for", wait)
    async def scenario():
        try:
            with pytest.raises(asyncio.CancelledError):
                await sub.start()
        finally:
            await sub._http.close()
    asyncio.run(scenario())
    assert modes == [False, True]
