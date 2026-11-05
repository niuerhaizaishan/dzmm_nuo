"""生图真调用服务测试：计费请求去重、原图核验、失败恢复与有限后台任务。"""
import asyncio
import base64
from io import BytesIO

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from PIL import Image
import pytest

from runtime import features, imagegen as I, tuning
from runtime.gallery import Store as GalleryStore, GalleryError
from runtime.gallery_api import create_app
from runtime.imagegen_store import Store
from runtime.outbox import OutboxSender, OutboxStore
from runtime.routing import RoutingSender
from runtime.transport_bot import BotApiAmbiguous, BotApiRejected


def png():
    stream = BytesIO()
    Image.new("RGB", (16, 12), "orange").save(stream, format="PNG")
    return stream.getvalue()


def message(mid="cmd1", uid="member", reference=None):
    content = {"type": "text", "text": "/生图 水彩橘猫"}
    if reference:
        content["reference"] = reference
    return {"message_id": mid, "sent_by": uid, "chatroom_id": I.PRIMARY_CID, "content": content}


class Platform:
    def __init__(self):
        self.commands = {}
        self.photo = {"message_id": "photo1", "chatroom_id": I.PRIMARY_CID,
                      "content": {"type": "image", "url": "https://rls.cheggpt.com/original.png"}}
        self.downloads = []
        self.allowed = True

    async def member(self, uid):
        if not self.allowed:
            raise GalleryError("仅限当前群成员使用。")
        return "测试成员"

    async def original(self, mid, sent_at=""):
        return self.commands[mid] if mid in self.commands else self.photo

    async def download(self, url):
        assert url == "https://rls.cheggpt.com/original.png"
        self.downloads.append(url)
        return png()


class Client:
    key = "fake-key"

    def __init__(self):
        self.calls = []
        self.error = None
        self.block = None

    async def generate(self, prompt, source=None, **kwargs):
        self.calls.append((prompt, source))
        if self.block:
            await self.block.wait()
        if self.error:
            raise self.error
        return png()


class Sender:
    def __init__(self):
        self.notices = []
        self.photos = []
        self.fallback = []
        self.error = None
        self.fallback_error = None

    def bot_for(self, cid):
        return self

    def can_send_photo_reply(self, cid):
        return True

    async def send_photo_with_id(self, url, cid, caption=""):
        self.fallback.append((url, cid, caption))
        if self.fallback_error:
            raise self.fallback_error
        return "bot-image"

    async def send(self, text, cid):
        self.notices.append(text)
        return True

    async def send_reply_with_id(self, text, cid, reference):
        self.notices.append(text)
        return "image-receipt"

    async def send_photo_reply_with_id(self, url, cid, reference, caption=""):
        self.photos.append((url, cid, caption, reference))
        if self.error:
            raise self.error
        return "sent-image"


@pytest.fixture(autouse=True)
def switches(monkeypatch):
    monkeypatch.setattr(features, "_OVERRIDES", {I.PRIMARY_CID + "|imagegen": True})
    monkeypatch.setattr(tuning, "_OVERRIDES", {})
    monkeypatch.setenv("DZMM_FEATURES", "on")
    monkeypatch.delenv("DZMM_IMAGEGEN_PUBLIC_BASE", raising=False)
    monkeypatch.setattr(I, "log", lambda *_: None)


def build(dbdir):
    platform, client, sender = Platform(), Client(), Sender()
    service = I.Service(Store(dbdir / "imagegen.db"), platform, client)
    return service, platform, client, sender


async def submit(service, platform, sender, msg=None, *, cid=I.PRIMARY_CID, dry=False):
    msg = msg or message()
    platform.commands[msg["message_id"]] = msg
    await service.handle(sender, cid, msg["sent_by"], msg["content"]["text"], msg, dry=dry)


async def finish(service):
    await asyncio.gather(*list(service.tasks.values()))


def test_generate_persists_bytes_and_deduplicates_after_restart(dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        await submit(service, platform, sender)
        await submit(service, platform, sender)
        await finish(service)
        row = service.store.get(I.PRIMARY_CID, "cmd1")
        assert row["status"] == "sent" and row["sent_id"] == "sent-image"
        assert row["data"] == png() and len(client.calls) == len(sender.photos) == 1
        assert sender.photos[0][3] == {"id": "cmd1", "sentBy": "member",
            "content": {"type": "text", "text": message()["content"]["text"]}}
        restarted = I.Service(Store(service.store.path), platform, client)
        await submit(restarted, platform, sender)
        assert not restarted.tasks and len(client.calls) == 1
        assert restarted.store.image(row["token"])["data"] == png()
    asyncio.run(scenario())


def test_missing_push_reference_is_recovered_and_forged_url_is_ignored(dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        original = message(reference={"id": "photo1", "content": {"url": "https://forged.invalid/x"}})
        platform.commands["cmd1"] = original
        await service.handle(sender, I.PRIMARY_CID, "member", original["content"]["text"], message())
        await finish(service)
        assert client.calls[0][1] == (png(), "image/png")
        assert platform.downloads == ["https://rls.cheggpt.com/original.png"]
        assert service.store.get(I.PRIMARY_CID, "cmd1")["mode"] == "edit"
        assert sender.photos[0][3]["id"] == "cmd1", "结果必须引用生图指令，不能引用源图"
    asyncio.run(scenario())


@pytest.mark.parametrize("broken", ["text", "video", "foreign", "wrong-command-user", "missing-photo-id"])
def test_invalid_reference_never_becomes_a_paid_generation(dbdir, broken):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        msg = message(reference={"id": "photo1"})
        if broken in ("text", "video"):
            platform.photo["content"]["type"] = broken
        elif broken == "foreign":
            platform.photo["chatroom_id"] = "other-group"
        elif broken == "missing-photo-id":
            msg["content"]["reference"] = {"content": {"type": "image"}}
        else:
            platform.commands["cmd1"] = {**msg, "sent_by": "someone-else"}
        if broken != "wrong-command-user":
            platform.commands["cmd1"] = msg
        await service.handle(sender, I.PRIMARY_CID, "member", msg["content"]["text"], msg)
        await finish(service)
        assert not client.calls and not sender.photos
        assert service.store.get(I.PRIMARY_CID, "cmd1")["status"] == "failed"
    asyncio.run(scenario())


def test_generated_picture_can_be_edited_without_http_fetch(dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        service.store.reserve(I.PRIMARY_CID, "old", "another", "generate", 0)
        token = service.store.save_image(I.PRIMARY_CID, "old", png(), "image/png")
        platform.photo["content"]["url"] = I.image_base_url() + "/" + token
        await submit(service, platform, sender, message(reference={"id": "photo1"}))
        await finish(service)
        assert not platform.downloads and client.calls[0][1] == (png(), "image/png")
    asyncio.run(scenario())


def test_gallery_can_save_generated_original_without_requesting_public_url(monkeypatch, dbdir):
    from runtime.gallery import GalleryPlatform
    store = Store(dbdir / "generated.db")
    store.reserve(I.PRIMARY_CID, "old", "another", "generate", 0)
    token = store.save_image(I.PRIMARY_CID, "old", png(), "image/png")
    monkeypatch.setenv("DZMM_IMAGEGEN_DB", str(store.path))
    assert asyncio.run(GalleryPlatform().download(I.image_base_url() + "/" + token)) == png()


@pytest.mark.parametrize("target", ["valid", "disabled", "outside", "slash-disabled"])
def test_actual_dispatch_consumes_imagegen_without_chat_fallback(monkeypatch, dbdir, target):
    from runtime import dispatch
    async def scenario():
        service, platform, client, sender = build(dbdir)
        monkeypatch.setattr(I, "_service", service)
        msg = message()
        platform.commands["cmd1"] = msg
        cid = I.PRIMARY_CID if target != "outside" else "other-group"
        if target == "disabled":
            features._OVERRIDES[I.PRIMARY_CID + "|imagegen"] = False
        if target == "slash-disabled":
            features._OVERRIDES[I.PRIMARY_CID + "|slash_cmds"] = False
        state = {}
        await dispatch.handle_slash(sender, cid, msg["content"]["text"], "member", False,
            {"last_id": ""}, [msg], msg, state, _save_group=lambda *a: None,
            _mark_inflight=lambda *a: None, _remember_turn=lambda *a: None,
            rank_summary=lambda *a: None, sync_profiles=lambda *a: None)
        await finish(service)
        assert len(client.calls) == (1 if target == "valid" else 0)
        assert state[cid]["last_id"] == "cmd1"
        if target != "valid":
            assert not sender.notices
    asyncio.run(scenario())


def test_switch_off_while_generating_saves_result_without_sending(monkeypatch, dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        client.block = asyncio.Event()
        await submit(service, platform, sender)
        for _ in range(30):
            if client.calls:
                break
            await asyncio.sleep(0.01)
        features._OVERRIDES[I.PRIMARY_CID + "|imagegen"] = False
        client.block.set()
        await finish(service)
        assert not sender.photos and service.store.get(I.PRIMARY_CID, "cmd1")["data"] == png()
    asyncio.run(scenario())


@pytest.mark.parametrize("ambiguous", [False, True])
def test_send_failure_keeps_result_and_only_clear_rejection_can_resume(dbdir, ambiguous):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        sender.error = BotApiAmbiguous("unknown") if ambiguous else BotApiRejected("rejected")
        sender.fallback_error = BotApiRejected("bot rejected")
        await submit(service, platform, sender)
        await finish(service)
        row = service.store.get(I.PRIMARY_CID, "cmd1")
        assert row["data"] == png()
        assert row["status"] == ("delivery_unknown" if ambiguous else "send_failed")
        sender.error = None
        restarted = I.Service(Store(service.store.path), platform, client)
        await submit(restarted, platform, sender)
        await finish(restarted)
        assert len(client.calls) == 1
        assert len(sender.photos) == (1 if ambiguous else 2)
        assert len(sender.fallback) == (0 if ambiguous else 1)
        assert all(photo[3]["id"] == "cmd1" for photo in sender.photos)
        assert restarted.store.get(I.PRIMARY_CID, "cmd1")["status"] == ("delivery_unknown" if ambiguous else "sent")
    asyncio.run(scenario())


def test_timeout_is_never_recharged_on_replay(dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        client.error = I.ImagegenError("生图等待超时，结果尚不确定，本次不会自动重复生成。")
        await submit(service, platform, sender)
        await finish(service)
        await submit(service, platform, sender)
        assert len(client.calls) == 1 and not sender.photos
        assert any("不会自动重复生成" in text for text in sender.notices)
    asyncio.run(scenario())


def test_limits_keep_main_loop_free_and_cancelled_jobs_are_not_replayed(dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        client.block = asyncio.Event()
        for index in range(8):
            await submit(service, platform, sender, message("m" + str(index), "user" + str(index)))
        assert len(service.tasks) == 7
        assert any("队列已满" in text for text in sender.notices)
        await submit(service, platform, sender, message("busy", "user0"))
        assert any("上一张" in text for text in sender.notices)
        for _ in range(30):
            if len(client.calls) == 2:
                break
            await asyncio.sleep(0.01)
        assert len(client.calls) == 2
        await service.close()
        assert not service.tasks
        restarted = I.Service(Store(service.store.path), platform, client)
        await submit(restarted, platform, sender, message("m0", "user0"))
        assert not restarted.tasks and len(client.calls) == 2
    asyncio.run(scenario())


def test_queue_notices_count_waiting_and_processing_and_keep_fifo(monkeypatch, dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        monkeypatch.setattr(I, "get_full_name", lambda uid: uid)
        client.block = asyncio.Event()
        for index in range(5):
            msg = message("q" + str(index), "user" + str(index))
            msg["content"]["text"] = "/生图 cat" + str(index)
            await submit(service, platform, sender, msg)
        for _ in range(50):
            if len(sender.notices) == 5 and len(client.calls) == 2:
                break
            await asyncio.sleep(0.01)
        for index, (waiting, processing) in enumerate([(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)]):
            notice = next(text for text in sender.notices if "user" + str(index) + "，" in text)
            assert f"小小糯收到啦！正在努力想象中~前面有 {waiting} 张图排队，另有 {processing} 张图正在处理" in notice
        cancelled = service.tasks[(I.PRIMARY_CID, "q2")]
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        msg = message("q5", "user5")
        msg["content"]["text"] = "/生图 cat5"
        await submit(service, platform, sender, msg)
        for _ in range(50):
            if any("user5，" in text for text in sender.notices):
                break
            await asyncio.sleep(0.01)
        assert any("user5，小小糯收到啦！正在努力想象中~前面有 2 张图排队，另有 2 张图正在处理" in text for text in sender.notices)
        client.block.set()
        await finish(service)
        assert [prompt for prompt, _ in client.calls] == ["cat0", "cat1", "cat3", "cat4", "cat5"]
        assert not service.waiting and not service.processing
    asyncio.run(scenario())


def test_legacy_ready_result_gets_original_command_quote_without_regenerating(dbdir):
    from contextlib import closing
    async def scenario():
        store = Store(dbdir / "legacy.db")
        store.reserve(I.PRIMARY_CID, "cmd1", "member", "generate", 0)
        token = store.save_image(I.PRIMARY_CID, "cmd1", png(), "image/png")
        with closing(store.connect()) as conn, conn:
            conn.execute("ALTER TABLE imagegen_jobs DROP COLUMN reference")
        platform, client, sender = Platform(), Client(), Sender()
        client.key = ""
        service = I.Service(Store(store.path), platform, client)
        await submit(service, platform, sender)
        await finish(service)
        assert not client.calls and len(sender.photos) == 1
        assert sender.photos[0][3]["id"] == "cmd1"
        assert service.store.image(token)["data"] == png()
        assert service.store.get(I.PRIMARY_CID, "cmd1")["status"] == "sent"
    asyncio.run(scenario())


def test_disconnected_photo_reply_channel_does_not_charge(monkeypatch, dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        monkeypatch.setattr(sender, "can_send_photo_reply", lambda cid: False)
        monkeypatch.setattr(sender, "bot_for", lambda cid: None)
        await submit(service, platform, sender)
        assert not service.tasks and not client.calls
        assert any("图片发送通道尚未就绪" in text for text in sender.notices)
    asyncio.run(scenario())


def test_scope_switch_cooldown_dry_and_membership_do_not_leak_generation(dbdir):
    async def scenario():
        service, platform, client, sender = build(dbdir)
        await submit(service, platform, sender, cid="other-group")
        features._OVERRIDES[I.PRIMARY_CID + "|imagegen"] = False
        await submit(service, platform, sender)
        features._OVERRIDES[I.PRIMARY_CID + "|imagegen"] = True
        await submit(service, platform, sender, dry=True)
        assert not client.calls and not sender.notices
        assert service.store.get(I.PRIMARY_CID, "cmd1") is None
        await submit(service, platform, sender)
        await finish(service)
        await submit(service, platform, sender, message("cooldown"))
        assert len(client.calls) == 1 and any("频繁" in text for text in sender.notices)
        platform.allowed = False
        await submit(service, platform, sender, message("denied", "outsider"))
        await finish(service)
        assert len(client.calls) == 1
    asyncio.run(scenario())


def test_image_url_reads_original_bytes_but_never_lists_in_public_gallery(dbdir):
    class Auth:
        async def verify(self, token):
            raise AssertionError("Generated image fetch must not require a Gamefy JWT")

    async def scenario():
        store = Store(dbdir / "imagegen.db")
        store.reserve(I.PRIMARY_CID, "m", "member", "generate", 0)
        token = store.save_image(I.PRIMARY_CID, "m", png(), "image/png")
        server = TestServer(create_app(store=GalleryStore(dbdir / "gallery.db"), auth=Auth(), imagegen_store=store))
        await server.start_server()
        try:
            async with ClientSession() as client:
                response = await client.get(server.make_url("/api/gallery/generated/" + token))
                assert response.status == 200 and await response.read() == png()
                assert response.content_type == "image/png" and response.headers["Cache-Control"] == "private, no-store"
                response = await client.get(server.make_url("/api/gallery/generated/" + "0" * 64))
                assert response.status == 404
                response = await client.get(server.make_url("/api/gallery/items"))
                assert (await response.json())["items"] == []
                assert store.image("../imagegen.db") is None
        finally:
            await server.close()
    asyncio.run(scenario())


def test_official_photo_outbox_requires_image_readback_and_never_falls_back(monkeypatch, dbdir):
    import dzmm
    class Bot:
        def send_photo_url(self, cid, url, caption=""):
            return "photo-mid"
    class Account:
        async def send(self, *args, **kwargs):
            raise AssertionError("Images must not fall back to an account")

    async def scenario():
        store = OutboxStore(dbdir / "outbox.db")
        routed = RoutingSender(Account(), {I.PRIMARY_CID: Bot()})
        sender = OutboxSender(routed, store)
        monkeypatch.setattr(dzmm, "get_messages", lambda *a, **k: [
            {"message_id": "photo-mid", "content": {"type": "image"}}])
        assert await sender.send_photo_with_id("https://example.test/a", I.PRIMARY_CID) == "photo-mid"
        row = store.by_status("sent")[0]
        assert row["content_type"] == "generated_image" and row["platform_sent_id"] == "photo-mid"
        monkeypatch.setattr(dzmm, "get_messages", lambda *a, **k: [
            {"message_id": "photo-mid", "content": {"type": "text"}}])
        with pytest.raises(BotApiAmbiguous):
            await sender.send_photo_with_id("https://example.test/b", I.PRIMARY_CID)
        assert await sender.retry_once() == 0
        with pytest.raises(BotApiRejected):
            await routed.send_photo_with_id("https://example.test/b", "other-group")
    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [False, True])
def test_xai_rest_payload_and_success_decode(monkeypatch, edit):
    async def scenario():
        async def endpoint(request):
            assert request.headers["Authorization"] == "Bearer test-only"
            body = await request.json()
            assert body["model"] == I.MODEL and body["n"] == 1
            assert request.path == ("/edits" if edit else "/generations")
            if edit:
                assert body["image"]["type"] == "image_url"
                assert base64.b64decode(body["image"]["url"].split(",")[1]) == png()
            return web.json_response({"data": [{"b64_json": base64.b64encode(png()).decode()}]})
        app = web.Application()
        app.router.add_post("/{action}", endpoint)
        server = TestServer(app)
        await server.start_server()
        try:
            monkeypatch.setattr(I, "API_BASE", str(server.make_url("/")))
            assert await I.XaiClient("test-only").generate("cat", (png(), "image/png") if edit else None) == png()
        finally:
            await server.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 402, 403, 429, 500])
def test_xai_errors_do_not_expose_response_or_key(monkeypatch, status):
    async def scenario():
        async def endpoint(request):
            return web.Response(status=status, text="provider-secret test-only")
        app = web.Application()
        app.router.add_post("/generations", endpoint)
        server = TestServer(app)
        await server.start_server()
        try:
            monkeypatch.setattr(I, "API_BASE", str(server.make_url("/")))
            with pytest.raises(I.ImagegenError) as error:
                await I.XaiClient("test-only").generate("cat")
            assert "provider-secret" not in str(error.value) and "test-only" not in str(error.value)
        finally:
            await server.close()
    asyncio.run(scenario())
