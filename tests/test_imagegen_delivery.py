"""生图原生引用真实发送链；原消息编号、作者和正文都必须读回一致。"""
import asyncio
import copy
import json

import pytest

from runtime import sender as account_sender
from runtime.ctx import PRIMARY_CID
from runtime.outbox import OutboxSender, OutboxStore
from runtime.routing import RoutingSender
from runtime.transport_bot import BotApiAmbiguous, BotApiRejected
from sio_subscribe import SioSubscriber

REFERENCE = {"id": "generation-command", "sentBy": "member",
             "content": {"type": "text", "text": "/生图 水彩橘猫"}}
URL = "https://example.test/api/gallery/generated/" + "a" * 64


class Socket:
    def __init__(self, behavior="ok"):
        self.calls = []
        self.behavior = behavior

    async def call(self, event, payload, timeout):
        self.calls.append((event, copy.deepcopy(payload)))
        if self.behavior == "timeout":
            raise TimeoutError()
        return {"success": self.behavior != "reject"}


class Bot:
    def send_photo_url(self, *args, **kwargs):
        raise AssertionError("A native image reply must never silently fall back to an unquoted Bot photo")


def chain(monkeypatch, dbdir, behavior="ok", readback="ok"):
    socket = Socket(behavior)
    subscriber = SioSubscriber([PRIMARY_CID])
    subscriber._sio = socket
    subscriber._connected.set()
    monkeypatch.setattr(account_sender, "_LIVE_SIO", subscriber)
    def messages(*args, **kwargs):
        row = copy.deepcopy(socket.calls[-1][1]["message"])
        if readback == "missing":
            row["content"].pop("reference")
        elif readback == "wrong-message":
            row["content"]["reference"]["id"] = "source-photo"
        elif readback == "wrong-author":
            row["content"]["reference"]["sentBy"] = "someone-else"
        elif readback == "wrong-text":
            row["content"]["reference"]["content"]["text"] = "/生图 another request"
        elif readback == "not-image":
            row["content"]["type"] = "text"
        elif readback == "wrong-sender":
            row["sent_by"] = "someone-else"
        return [row]
    monkeypatch.setattr(account_sender, "get_messages", messages)
    store = OutboxStore(dbdir / "outbox.db")
    routed = RoutingSender(account_sender.Sender(), {PRIMARY_CID: Bot()})
    return OutboxSender(routed, store), store, socket


def test_image_and_original_command_reference_are_one_native_message(monkeypatch, dbdir):
    async def scenario():
        sender, store, socket = chain(monkeypatch, dbdir)
        try:
            assert sender.can_send_photo_reply(PRIMARY_CID)
            mid = await sender.send_photo_reply_with_id(URL, PRIMARY_CID, REFERENCE, caption="群友 · 图生图")
            assert len(socket.calls) == 1
            event, payload = socket.calls[0]
            assert event == "message:send" and payload["chatroomId"] == PRIMARY_CID
            row = payload["message"]
            assert mid == row["message_id"] and row["sent_by"] == account_sender.NUO_UID
            assert row["content"] == {"type": "image", "url": URL,
                                      "alt": "群友 · 图生图", "reference": REFERENCE}
            audit = store.by_status("sent")[0]
            assert audit["content_type"] == "generated_image" and audit["platform_sent_id"] == mid
            assert json.loads(audit["text"])["reference"] == REFERENCE
        finally:
            store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("readback", ["missing", "wrong-message", "wrong-author", "wrong-text", "not-image", "wrong-sender"])
def test_missing_or_changed_quote_is_never_recorded_as_sent_or_retried(monkeypatch, dbdir, readback):
    async def scenario():
        sender, store, socket = chain(monkeypatch, dbdir, readback=readback)
        try:
            with pytest.raises(BotApiAmbiguous):
                await sender.send_photo_reply_with_id(URL, PRIMARY_CID, REFERENCE)
            assert len(socket.calls) == 1 and not store.by_status("sent")
            assert len(store.by_status("failed")) == 1
            assert await sender.retry_once() == 0
            assert len(socket.calls) == 1
        finally:
            store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("behavior,error", [("reject", BotApiRejected), ("timeout", BotApiAmbiguous)])
def test_native_image_failure_never_sends_an_unquoted_replacement(monkeypatch, dbdir, behavior, error):
    async def scenario():
        sender, store, socket = chain(monkeypatch, dbdir, behavior=behavior)
        try:
            with pytest.raises(error):
                await sender.send_photo_reply_with_id(URL, PRIMARY_CID, REFERENCE)
            assert len(socket.calls) == 1 and len(store.by_status("failed")) == 1
            assert await sender.retry_once() == 0
        finally:
            store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("reference", [None, {}, {"id": "command"}, {"sentBy": "member"}])
def test_incomplete_reference_never_falls_back_to_a_plain_photo(monkeypatch, dbdir, reference):
    async def scenario():
        sender, store, socket = chain(monkeypatch, dbdir)
        try:
            with pytest.raises(ValueError):
                await sender.send_photo_reply_with_id(URL, PRIMARY_CID, reference)
            assert not socket.calls and not store.by_status("sent")
        finally:
            store.close()
    asyncio.run(scenario())
