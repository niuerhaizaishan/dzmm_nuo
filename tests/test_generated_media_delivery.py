"""平台视频上传与可见引用回执：跨失败/重启不重复投递。"""
import asyncio
import copy

import pytest

import dzmm
from runtime.generated_media import platform_video_content, send_result_reply
from runtime.imagegen_store import Store
from runtime.transport_bot import BotApiAmbiguous, BotApiRejected
from test_imagegen_delivery import chain, REFERENCE

VIDEO = {"videoUrl": "https://rls.cheggpt.com/storage/v1/object/public/chat_videos/a.mp4",
         "thumbnailUrl": "https://rls.cheggpt.com/storage/v1/object/public/chat_videos/a.jpg",
         "duration": 6, "width": 544, "height": 544, "blurhash": "hash",
         "videoAssetId": "asset", "moderationFrameUrls": ["internal"], "outputSize": 100}


def saved(monkeypatch, dbdir):
    store = Store(dbdir / "video.db")
    store.reserve("room", "command", "member", "video_edit", 0)
    token = store.save_media("room", "command", b"saved-video", "video/mp4")
    monkeypatch.setenv("DZMM_IMAGEGEN_DB", str(store.path))
    monkeypatch.setenv("DZMM_IMAGEGEN_PUBLIC_BASE", "https://example.test/generated")
    return store, token, "https://example.test/generated/" + token


def test_video_upload_matches_web_contract_and_cached_media_flows_through_send_chain(monkeypatch, dbdir):
    store, token, url = saved(monkeypatch, dbdir)
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"result": {"data": {"json": copy.deepcopy(VIDEO)}}}
    def upload(endpoint, **kwargs):
        calls.append(endpoint)
        assert endpoint == dzmm.ORIGIN + "/api/trpc/media.uploadVideo"
        assert kwargs["files"]["file"] == ("generated.mp4", b"saved-video", "video/mp4")
        assert "data" not in kwargs
        assert kwargs["headers"]["Origin"] == dzmm.ORIGIN
        assert kwargs["headers"]["Referer"] == dzmm.ORIGIN + "/"
        assert not kwargs["allow_redirects"]
        return Response()
    monkeypatch.setattr(dzmm.requests, "post", upload)
    monkeypatch.setattr(dzmm, "get_cookies", lambda: {})
    async def scenario():
        from runtime.ctx import PRIMARY_CID
        sender, outbox, socket = chain(monkeypatch, dbdir)
        try:
            await sender.send_video_reply_with_id(url, PRIMARY_CID, REFERENCE)
            content = socket.calls[0][1]["message"]["content"]
            assert content == {**{k: v for k, v in VIDEO.items()
                                 if k not in ("videoAssetId", "moderationFrameUrls", "outputSize")},
                               "type": "video", "status": "ready", "reference": REFERENCE}
            assert await platform_video_content(url, PRIMARY_CID) == store.platform_media(token)
            assert len(calls) == 1
            assert store.platform_url(token) == VIDEO["videoUrl"]
        finally:
            outbox.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["rejected", "network", "bad-url"])
def test_video_upload_failure_retains_video_and_never_sends(monkeypatch, dbdir, failure):
    store, token, url = saved(monkeypatch, dbdir)
    class Response:
        status_code = 403 if failure == "rejected" else 200
        def json(self):
            return {"result": {"data": {"json": {**VIDEO, "videoUrl": "https://foreign.test/a.mp4"}}}}
    def upload(*args, **kwargs):
        if failure == "network":
            raise OSError("private-response")
        return Response()
    monkeypatch.setattr(dzmm.requests, "post", upload)
    monkeypatch.setattr(dzmm, "get_cookies", lambda: {})
    async def scenario():
        sender, outbox, socket = chain(monkeypatch, dbdir)
        try:
            with pytest.raises(BotApiRejected) as exc:
                await sender.send_video_reply_with_id(url, "room", REFERENCE)
            assert "private-response" not in str(exc.value)
            assert not socket.calls
            assert store.media(token)["data"] == b"saved-video"
        finally:
            outbox.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["图片", "视频"])
@pytest.mark.parametrize("failure", [None, "reject", "ambiguous"])
def test_visible_receipt_quotes_command_once_and_never_changes_media_status(monkeypatch, dbdir, kind, failure):
    store, _, _ = saved(monkeypatch, dbdir)
    store.status("room", "command", "sent", "media-id")
    reference = {"id": "command", "sentBy": "member", "content": {"type": "text", "text": "/生图 x"}}
    class Sender:
        calls = []
        async def send_reply_with_id(self, text, cid, ref):
            self.calls.append((text, cid, ref))
            if failure:
                raise (BotApiRejected if failure == "reject" else BotApiAmbiguous)("test")
            return "receipt-id"
    sender = Sender()
    async def scenario():
        await send_result_reply(sender, store, "room", "command", reference, kind)
        row = store.get("room", "command")
        assert row["status"] == "sent" and row["sent_id"] == "media-id"
        assert sender.calls == [("member，小小糯已经把" + kind + "发好啦！", "room", reference)]
        assert row["result_reply_status"] == {None: "sent", "reject": "failed", "ambiguous": "unknown"}[failure]
        if failure != "reject":
            # Fresh Store simulates a restart. Neither success nor uncertainty is replayed.
            await send_result_reply(sender, Store(store.path), "room", "command", reference, kind)
            assert len(sender.calls) == 1
    asyncio.run(scenario())


def test_receipt_cannot_be_claimed_twice_or_before_media_sent(monkeypatch, dbdir):
    store, _, _ = saved(monkeypatch, dbdir)
    assert not store.claim_result_reply("room", "command")
    store.status("room", "command", "sent", "media-id")
    assert store.claim_result_reply("room", "command")
    assert not Store(store.path).claim_result_reply("room", "command")


@pytest.mark.parametrize("kind", ["image", "video"])
def test_service_delivers_media_then_visible_original_command_reply(monkeypatch, dbdir, kind):
    from runtime import imagegen, videogen
    from runtime.ctx import PRIMARY_CID
    store, token, url = saved(monkeypatch, dbdir)
    store.reserve(PRIMARY_CID, "command", "member", kind, 0)
    reference = {**REFERENCE, "id": "command"}
    store.save_reference(PRIMARY_CID, "command", reference)
    async def uploaded(*a):
        return {**{k: v for k, v in VIDEO.items()
                  if k not in ("videoAssetId", "moderationFrameUrls", "outputSize")},
                "type": "video", "status": "ready"}
    async def photo(*a):
        return VIDEO["thumbnailUrl"]
    monkeypatch.setattr("runtime.generated_media.platform_video_content", uploaded)
    monkeypatch.setattr("runtime.generated_media.official_photo_url", photo)
    async def scenario():
        sender, outbox, socket = chain(monkeypatch, dbdir)
        try:
            module = imagegen if kind == "image" else videogen
            service = module.Service.__new__(module.Service)
            service.store = store
            await service.deliver(sender, PRIMARY_CID, "command", url, "result", reference)
            contents = [payload["message"]["content"] for _, payload in socket.calls]
            assert [c["type"] for c in contents] == [kind, "text"]
            assert contents[1]["reference"] == reference
            row = store.get(PRIMARY_CID, "command")
            assert row["status"] == row["result_reply_status"] == "sent"
            assert row["sent_id"] != row["result_reply_id"]
        finally:
            outbox.close()
    asyncio.run(scenario())
