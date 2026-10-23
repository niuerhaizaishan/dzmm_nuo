"""游戏入口：独立介绍文本、类型正确的分享卡与明确投递确认。"""
import asyncio

import pytest

from runtime import dispatch, gallery, sender as account_sender
from runtime.outbox import OutboxSender, OutboxStore
from runtime.routing import RoutingSender
from sio_subscribe import SioSubscriber


class Socket:
    def __init__(self, *, ack=True, timeout=False):
        self.calls = []
        self.ack = ack
        self.timeout = timeout

    async def call(self, event, payload, timeout):
        self.calls.append((event, payload))
        if self.timeout:
            raise TimeoutError()
        return {"success": self.ack}


def connection(socket):
    subscriber = SioSubscriber([gallery.PRIMARY_CID])
    subscriber._sio = socket
    subscriber._connected.set()
    return subscriber


class Bot:
    def __init__(self):
        self.calls = []

    def send_text(self, cid, text):
        self.calls.append((cid, text))
        return "intro-id"


def test_comic_dispatch_sends_intro_then_typed_card_through_audited_chain(monkeypatch, dbdir):
    socket, bot = Socket(), Bot()
    monkeypatch.setattr(account_sender, "_LIVE_SIO", connection(socket))
    monkeypatch.setattr(dispatch, "_feat_on", lambda *a: True)
    async def reply(*a, **kw):
        return gallery.CARD_INTRO
    monkeypatch.setattr(gallery, "command_reply", reply)
    with_store = OutboxStore(dbdir / "outbox.db")
    try:
        chain = OutboxSender(RoutingSender(account_sender.Sender(), {gallery.PRIMARY_CID: bot}), with_store)
        msg = {"message_id": "command", "content": {"text": "/漫画"}}
        asyncio.run(dispatch.handle_slash(chain, gallery.PRIMARY_CID, "/漫画", "member", False,
            {}, [msg], msg, {}, _save_group=lambda *a: None, _mark_inflight=lambda *a: None,
            _remember_turn=lambda *a: None, rank_summary=lambda: "", sync_profiles=lambda: ""))
        assert bot.calls == [(gallery.PRIMARY_CID, "🤖 " + gallery.CARD_INTRO)]
        assert "https://" not in bot.calls[0][1]
        assert len(socket.calls) == 1
        event, payload = socket.calls[0]
        assert event == "message:send"
        assert payload["message"]["content"] == {
            "type": "share", "shareType": "character", "resourceId": "3799187"}
        assert payload["message"]["sent_by"] == account_sender.NUO_UID
        rows = with_store.by_status("sent")
        assert {row["content_type"] for row in rows} == {"text", "share"}
        share = next(row for row in rows if row["content_type"] == "share")
        assert share["platform_sent_id"] == payload["message"]["message_id"]
        assert share["text"] == "3799187"
    finally:
        with_store.close()


@pytest.mark.parametrize("timeout", [False, True])
def test_share_failure_is_not_retried_or_converted_to_text(monkeypatch, dbdir, timeout):
    socket, bot = Socket(ack=False, timeout=timeout), Bot()
    monkeypatch.setattr(account_sender, "_LIVE_SIO", connection(socket))
    store = OutboxStore(dbdir / "outbox.db")
    try:
        chain = OutboxSender(RoutingSender(account_sender.Sender(), {gallery.PRIMARY_CID: bot}), store)
        with pytest.raises((RuntimeError, TimeoutError)):
            asyncio.run(chain.send_character_with_id(gallery.CARD_ID, gallery.PRIMARY_CID))
        assert len(socket.calls) == 1 and bot.calls == []
        row = store.by_status("failed")[0]
        assert row["content_type"] == "share"
        # 即使旧记录误带可重试文本标记，share 也绝不能进入文本重试。
        store.mark_failed(row["id"], "sender 返回 False [rejected]")
        assert asyncio.run(chain.retry_once()) == 0
        assert len(socket.calls) == 1 and bot.calls == []
    finally:
        store.close()


def test_share_dry_run_and_disconnected_socket_do_not_send(monkeypatch, dbdir):
    socket = Socket()
    subscriber = connection(socket)
    subscriber._connected.clear()
    monkeypatch.setattr(account_sender, "_LIVE_SIO", subscriber)
    account = account_sender.Sender()
    with pytest.raises(RuntimeError):
        asyncio.run(account.send_character_with_id(gallery.CARD_ID, gallery.PRIMARY_CID))
    store = OutboxStore(dbdir / "outbox.db")
    try:
        assert asyncio.run(OutboxSender(account, store, dry=True).send_character_with_id(gallery.CARD_ID, gallery.PRIMARY_CID)) == ""
        assert store.pending_count() == 0 and socket.calls == []
    finally:
        store.close()


@pytest.mark.parametrize("allowed,intro_ok", [(False, True), (True, False)])
def test_denial_or_failed_intro_never_sends_card(monkeypatch, allowed, intro_ok):
    calls = []
    class Sender:
        async def send(self, text, cid):
            calls.append(text)
            return intro_ok
        async def send_character_with_id(self, *args):
            pytest.fail("must not send card")
    async def reply(*args, **kwargs):
        return gallery.CARD_INTRO if allowed else "姐妹图集仅限当前妹子群成员使用。"
    monkeypatch.setattr(gallery, "command_reply", reply)
    monkeypatch.setattr(dispatch, "_feat_on", lambda *a: True)
    msg = {"message_id": "command", "content": {"text": "/漫画"}}
    async def run():
        await dispatch.handle_slash(Sender(), gallery.PRIMARY_CID, "/漫画", "member", False,
            {}, [msg], msg, {}, _save_group=lambda *a: None, _mark_inflight=lambda *a: None,
            _remember_turn=lambda *a: None, rank_summary=lambda: "", sync_profiles=lambda: "")
    if intro_ok:
        asyncio.run(run())
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(run())
    assert len(calls) == 1
