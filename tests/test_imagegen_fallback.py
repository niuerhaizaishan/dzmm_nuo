import asyncio
import pytest

from runtime import imagegen as I
from runtime.imagegen_store import Store
from runtime.transport_bot import BotApiAmbiguous, BotApiRejected


def _setup(dbdir):
    store = Store(dbdir / "imagegen.db")
    store.reserve(I.PRIMARY_CID, "m", "u", "generate", 0)
    token = store.save_image(I.PRIMARY_CID, "m", b"x", "image/png")
    ref = {"id": "m", "sentBy": "u",
           "content": {"type": "text", "text": "/生图 x"}}
    store.save_reference(I.PRIMARY_CID, "m", ref)
    return store, token, ref


def test_account_rejection_falls_back_to_official_bot(dbdir):
    async def scenario():
        class Sender:
            def __init__(self): self.calls = []
            async def send_photo_reply_with_id(self, *args, **kwargs):
                self.calls.append("account"); raise BotApiRejected("offline")
            async def send_photo_with_id(self, *args, **kwargs):
                self.calls.append("bot"); return "bot-mid"
        store, token, ref = _setup(dbdir)
        sender = Sender()
        service = I.Service(store=store, client=object())
        await service.deliver(sender, I.PRIMARY_CID, "m", "https://x/" + token, "u", ref)
        assert sender.calls == ["account", "bot"]
        assert store.get(I.PRIMARY_CID, "m")["status"] == "sent"
    asyncio.run(scenario())


def test_ambiguous_account_failure_does_not_fallback(dbdir):
    async def scenario():
        class Sender:
            async def send_photo_reply_with_id(self, *args, **kwargs):
                raise BotApiAmbiguous("unknown")
            async def send_photo_with_id(self, *args, **kwargs):
                raise AssertionError("must not duplicate")
        store, token, ref = _setup(dbdir)
        try:
            await I.Service(store=store, client=object()).deliver(
                Sender(), I.PRIMARY_CID, "m", "https://x/" + token, "u", ref)
        except I.ImagegenError as error:
            assert "待核对" in str(error)
    asyncio.run(scenario())


def test_ambiguous_official_failure_stays_unknown_instead_of_retryable(dbdir):
    async def scenario():
        class Sender:
            async def send_photo_reply_with_id(self, *args, **kwargs):
                raise BotApiRejected("offline")
            async def send_photo_with_id(self, *args, **kwargs):
                raise BotApiAmbiguous("timeout")
        store, token, ref = _setup(dbdir)
        try:
            await I.Service(store=store, client=object()).deliver(
                Sender(), I.PRIMARY_CID, "m", "https://x/" + token, "u", ref)
        except I.ImagegenError as error:
            assert "待核对" in str(error)
        assert store.get(I.PRIMARY_CID, "m")["status"] == "delivery_unknown"
    asyncio.run(scenario())


def test_offline_account_can_generate_upload_and_deliver_via_official_bot(monkeypatch, dbdir):
    import dzmm
    from runtime import features, sender as account_sender
    from runtime.outbox import OutboxSender, OutboxStore
    from runtime.routing import RoutingSender
    from test_imagegen import Platform, Client, png, submit, finish

    async def scenario():
        uploads, bot_calls = [], []
        storage_url = "https://rls.cheggpt.com/storage/v1/object/public/chat_images/generated.png"
        class Response:
            status_code = 200
            def json(self):
                return {"result": {"data": {"json": {"url": storage_url}}}}
        def upload(url, **kwargs):
            uploads.append(url)
            assert url.endswith("/api/trpc/chatroom.uploadImage")
            assert kwargs["files"]["file"][1:] == (png(), "image/png")
            assert kwargs["data"] == {"chatroomId": I.PRIMARY_CID}
            assert kwargs["headers"]["Origin"] == dzmm.ORIGIN
            assert kwargs["headers"]["Referer"] == dzmm.ORIGIN + "/"
            assert not kwargs["allow_redirects"]
            return Response()
        class Bot:
            def send_photo_url(self, cid, url, caption=""):
                bot_calls.append(url)
                return "bot-mid"
        monkeypatch.setattr(features, "_OVERRIDES", {I.PRIMARY_CID + "|imagegen": True})
        monkeypatch.setattr(account_sender, "_LIVE_SIO", None)
        monkeypatch.setattr(dzmm.requests, "post", upload)
        monkeypatch.setattr(dzmm, "get_cookies", lambda: {})
        monkeypatch.setattr(dzmm, "get_messages", lambda *a, **kw: [
            {"message_id": "bot-mid", "content": {"type": "image"}}])
        platform, client = Platform(), Client()
        store = Store(dbdir / "generated.db")
        monkeypatch.setenv("DZMM_IMAGEGEN_DB", str(store.path))
        outbox = OutboxStore(dbdir / "outbox.db")
        sender = OutboxSender(RoutingSender(account_sender.Sender(), {I.PRIMARY_CID: Bot()}), outbox)
        # 排队通知走已有文本通道，本测试聚焦实际图片发送链。
        async def notice(*a, **kw): return True
        monkeypatch.setattr(sender, "send", notice)
        service = I.Service(store=store, platform=platform, client=client)
        try:
            await submit(service, platform, sender)
            await finish(service)
            assert len(client.calls) == len(uploads) == 1
            assert bot_calls == [storage_url]
            assert store.get(I.PRIMARY_CID, "cmd1")["status"] == "sent"
            assert len(outbox.by_status("sent")) == 1
            from runtime.generated_media import official_photo_url
            row = store.get(I.PRIMARY_CID, "cmd1")
            assert await official_photo_url(I.image_base_url() + "/" + row["token"], I.PRIMARY_CID) == storage_url
            assert len(uploads) == 1, "重试发送不能重复上传平台存储"
        finally:
            outbox.close()
    asyncio.run(scenario())


def test_native_generated_image_uses_platform_storage_and_preserves_quote(monkeypatch, dbdir):
    import dzmm
    from test_imagegen_delivery import chain
    storage_url = "https://rls.cheggpt.com/storage/v1/object/public/chat_images/generated.jpg"
    uploads = []
    class Response:
        status_code = 200
        def json(self):
            return {"result": {"data": {"json": {"url": storage_url}}}}
    def upload(*args, **kwargs):
        assert kwargs["headers"]["Origin"] == dzmm.ORIGIN
        assert kwargs["headers"]["Referer"] == dzmm.ORIGIN + "/"
        uploads.append(1)
        return Response()
    monkeypatch.setattr(dzmm.requests, "post", upload)
    monkeypatch.setattr(dzmm, "get_cookies", lambda: {})
    store, token, reference = _setup(dbdir)
    monkeypatch.setenv("DZMM_IMAGEGEN_DB", str(store.path))
    async def scenario():
        sender, outbox, socket = chain(monkeypatch, dbdir)
        try:
            await sender.send_photo_reply_with_id(I.image_base_url() + "/" + token,
                I.PRIMARY_CID, reference, caption="文生图")
            content = socket.calls[0][1]["message"]["content"]
            assert content == {"type": "image", "url": storage_url,
                               "alt": "文生图", "reference": reference}
            assert len(uploads) == 1 and store.platform_url(token) == storage_url
            assert len(outbox.by_status("sent")) == 1
        finally:
            outbox.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["rejected", "network", "bad-url"])
def test_upload_failure_stops_before_official_send_and_keeps_original(monkeypatch, dbdir, failure):
    import dzmm
    from runtime.generated_media import official_photo_url
    store, token, _ = _setup(dbdir)
    monkeypatch.setenv("DZMM_IMAGEGEN_DB", str(store.path))
    monkeypatch.setattr(dzmm, "get_cookies", lambda: {})
    class Response:
        status_code = 403 if failure == "rejected" else 200
        def json(self):
            return {"result": {"data": {"json": {"url": "https://foreign.test/a.png"}}}}
    def upload(*a, **kw):
        if failure == "network":
            raise OSError("provider-secret")
        return Response()
    monkeypatch.setattr(dzmm.requests, "post", upload)
    with pytest.raises(BotApiRejected) as exc:
        asyncio.run(official_photo_url(I.image_base_url() + "/" + token, I.PRIMARY_CID))
    assert "provider-secret" not in str(exc.value)
    assert store.image(token)["data"] == b"x"
