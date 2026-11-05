"""视频经过真实 Socket.IO、路由和 Outbox，回读引用与身份后才算成功。"""
import asyncio
import copy
import json

import pytest

from runtime import sender as account_sender
from runtime.ctx import PRIMARY_CID
from runtime.transport_bot import BotApiAmbiguous, BotApiRejected, DzmmBotSender
from test_imagegen_delivery import chain, REFERENCE, URL


def reference():
    value = copy.deepcopy(REFERENCE)
    value["content"]["text"] = "/生视频 橘猫走过草地"
    return value


def test_native_video_reply_is_confirmed_and_recorded_as_media(monkeypatch, dbdir):
    async def scenario():
        sender, store, socket = chain(monkeypatch, dbdir)
        try:
            ref = reference()
            mid = await sender.send_video_reply_with_id(URL, PRIMARY_CID, ref, caption="图生视频")
            assert len(socket.calls) == 1
            row = socket.calls[0][1]["message"]
            assert row["sent_by"] == account_sender.NUO_UID and row["message_id"] == mid
            assert row["content"]["type"] == "video" and row["content"]["videoUrl"] == URL
            assert row["content"]["reference"] == ref
            audit = store.by_status("sent")[0]
            assert audit["content_type"] == "generated_video" and audit["platform_sent_id"] == mid
            assert json.loads(audit["text"])["reference"] == ref
        finally:
            store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("readback", ["missing", "wrong-message", "wrong-author", "wrong-text", "not-image", "wrong-sender"])
def test_video_quote_or_identity_readback_failure_never_duplicates(monkeypatch, dbdir, readback):
    async def scenario():
        sender, store, socket = chain(monkeypatch, dbdir, readback=readback)
        try:
            with pytest.raises(BotApiAmbiguous):
                await sender.send_video_reply_with_id(URL, PRIMARY_CID, reference())
            assert len(socket.calls) == 1 and not store.by_status("sent")
            assert await sender.retry_once() == 0
        finally:
            store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("behavior,error", [("reject", BotApiRejected), ("timeout", BotApiAmbiguous)])
def test_video_ack_rejection_and_ambiguous_failure_have_different_recovery(monkeypatch, dbdir, behavior, error):
    async def scenario():
        sender, store, socket = chain(monkeypatch, dbdir, behavior=behavior)
        try:
            with pytest.raises(error):
                await sender.send_video_reply_with_id(URL, PRIMARY_CID, reference())
            assert len(socket.calls) == 1 and len(store.by_status("failed")) == 1
            assert await sender.retry_once() == 0
        finally:
            store.close()
    asyncio.run(scenario())


def test_disconnected_video_channel_never_guesses_official_endpoint(monkeypatch, dbdir):
    async def scenario():
        sender, store, socket = chain(monkeypatch, dbdir)
        monkeypatch.setattr(account_sender, "_LIVE_SIO", None)
        try:
            assert not sender.can_send_video_reply(PRIMARY_CID)
            with pytest.raises(BotApiRejected):
                await sender.send_video_reply_with_id(URL, PRIMARY_CID, reference())
            assert not socket.calls
            assert not hasattr(DzmmBotSender, "send_video_url")
        finally:
            store.close()
    asyncio.run(scenario())
