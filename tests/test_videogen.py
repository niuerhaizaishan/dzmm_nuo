"""视频计费去重、成员/引用边界、队列生命周期和真实 REST 协议。"""
import asyncio
import base64
import copy
from io import BytesIO

import av
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from PIL import Image

from runtime import features, imagegen, tuning, videogen as V
from runtime.gallery import GalleryError, GalleryPlatform, Store as GalleryStore
from runtime.gallery_api import create_app
from runtime.imagegen_store import Store
from runtime.transport_bot import BotApiAmbiguous, BotApiRejected


@pytest.fixture(scope="module")
def movie():
    output = BytesIO()
    with av.open(output, "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for _ in range(3):
            for packet in stream.encode(av.VideoFrame.from_image(Image.new("RGB", (64, 48), "pink"))):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return output.getvalue()


def png():
    output = BytesIO()
    Image.new("RGB", (16, 12), "orange").save(output, format="PNG")
    return output.getvalue()


def message(mid="cmd1", uid="member", reference=None):
    content = {"type": "text", "text": "/生视频 橘猫走过草地"}
    if reference:
        content["reference"] = reference
    return {"message_id": mid, "sent_by": uid, "chatroom_id": V.PRIMARY_CID, "content": content}


class Platform:
    def __init__(self):
        self.commands = {}
        self.allowed = True
        self.photo = {"message_id": "photo", "chatroom_id": V.PRIMARY_CID,
                      "content": {"type": "image", "url": "https://rls.cheggpt.com/original.png"}}

    async def member(self, uid):
        if not self.allowed:
            raise GalleryError("仅限当前群成员使用。")
        return "测试成员"

    async def original(self, mid, sent_at=""):
        return self.commands[mid] if mid in self.commands else self.photo

    async def download(self, url):
        assert url == "https://rls.cheggpt.com/original.png"
        return png()


class Client:
    key = "test-key"

    def __init__(self, data):
        self.calls, self.block, self.error = [], None, None
        self.data = data

    async def generate(self, prompt, source=None, *, on_submitted=None, **kwargs):
        self.calls.append((prompt, source))
        if on_submitted:
            on_submitted("remote-job")
        if self.block:
            await self.block.wait()
        if self.error:
            raise self.error
        return self.data


class Sender:
    def __init__(self):
        self.notices, self.videos = [], []
        self.error = None
        self.ready = True

    def can_send_video_reply(self, cid):
        return self.ready

    async def send(self, text, cid):
        self.notices.append(text)
        return True

    async def send_reply_with_id(self, text, cid, reference):
        self.notices.append(text)
        return "video-receipt"

    async def send_video_reply_with_id(self, url, cid, reference, caption=""):
        self.videos.append((url, cid, copy.deepcopy(reference), caption))
        if self.error:
            raise self.error
        return "video-mid"


@pytest.fixture(autouse=True)
def switches(monkeypatch):
    monkeypatch.setattr(features, "_OVERRIDES", {V.PRIMARY_CID + "|videogen": True})
    monkeypatch.setattr(tuning, "_OVERRIDES", {})
    monkeypatch.setenv("DZMM_FEATURES", "on")
    monkeypatch.delenv("DZMM_IMAGEGEN_PUBLIC_BASE", raising=False)
    monkeypatch.setattr(V, "log", lambda *_: None)


def build(tmp_path, movie):
    platform, client, sender = Platform(), Client(movie), Sender()
    return V.Service(Store(tmp_path / "generated.db"), platform, client), platform, client, sender


async def submit(service, platform, sender, msg=None, **kwargs):
    msg = msg or message()
    platform.commands[msg["message_id"]] = msg
    await service.handle(sender, V.PRIMARY_CID, msg["sent_by"], msg["content"]["text"], msg, **kwargs)


async def finish(service):
    await asyncio.gather(*list(service.tasks.values()))


@pytest.mark.parametrize("reference", [None, {"id": "photo"}])
def test_video_generation_persists_original_command_and_never_recharges(tmp_path, movie, reference):
    async def scenario():
        service, platform, client, sender = build(tmp_path, movie)
        await submit(service, platform, sender, message(reference=reference))
        await submit(service, platform, sender, message(reference=reference))
        await finish(service)
        row = service.store.get(V.PRIMARY_CID, "cmd1")
        assert row["status"] == "sent" and row["data"] == movie and row["mime"] == "video/mp4"
        assert row["request_id"] == "remote-job"
        assert len(client.calls) == len(sender.videos) == 1
        assert client.calls[0][1] == ((png(), "image/png") if reference else None)
        assert sender.videos[0][2]["id"] == "cmd1"
        assert sender.videos[0][2]["content"]["text"] == message()["content"]["text"]
        restarted = V.Service(Store(service.store.path), platform, client)
        await submit(restarted, platform, sender)
        assert not restarted.tasks and len(client.calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["valid", "disabled", "outside", "slash-disabled"])
def test_dispatch_consumes_video_commands_without_chat_fallback(monkeypatch, tmp_path, movie, target):
    from runtime import dispatch
    async def scenario():
        service, platform, client, sender = build(tmp_path, movie)
        monkeypatch.setattr(V, "_service", service)
        msg = message()
        platform.commands["cmd1"] = msg
        cid = "other" if target == "outside" else V.PRIMARY_CID
        if target == "disabled":
            features._OVERRIDES[cid + "|videogen"] = False
        if target == "slash-disabled":
            features._OVERRIDES[cid + "|slash_cmds"] = False
        await dispatch.handle_slash(sender, cid, msg["content"]["text"], "member", False,
            {"last_id": ""}, [msg], msg, {}, _save_group=lambda *a: None,
            _mark_inflight=lambda *a: None, _remember_turn=lambda *a: None,
            rank_summary=lambda *a: None, sync_profiles=lambda *a: None)
        await finish(service)
        assert len(client.calls) == (1 if target == "valid" else 0)
        if target != "valid":
            assert not sender.notices
    asyncio.run(scenario())


def test_generated_image_can_animate_without_fetching_public_url(tmp_path, movie):
    async def scenario():
        service, platform, client, sender = build(tmp_path, movie)
        service.store.reserve(V.PRIMARY_CID, "old", "other", "generate", 0)
        token = service.store.save_image(V.PRIMARY_CID, "old", png(), "image/png")
        platform.photo["content"]["url"] = imagegen.image_base_url() + "/" + token
        await submit(service, platform, sender, message(reference={"id": "photo"}))
        await finish(service)
        assert client.calls[0][1] == (png(), "image/png")
        assert service.store.get(V.PRIMARY_CID, "cmd1")["status"] == "sent"
    asyncio.run(scenario())


@pytest.mark.parametrize("broken", ["text", "video", "foreign", "wrong-author", "lost-reference", "denied"])
def test_invalid_source_or_member_never_reaches_paid_post(tmp_path, movie, broken):
    async def scenario():
        service, platform, client, sender = build(tmp_path, movie)
        msg = message(reference={"id": "photo"})
        platform.commands["cmd1"] = copy.deepcopy(msg)
        if broken in ("text", "video"):
            platform.photo["content"]["type"] = broken
        elif broken == "foreign":
            platform.photo["chatroom_id"] = "other"
        elif broken == "wrong-author":
            platform.commands["cmd1"]["sent_by"] = "other"
        elif broken == "lost-reference":
            platform.commands["cmd1"]["content"].pop("reference")
        else:
            platform.allowed = False
        await service.handle(sender, V.PRIMARY_CID, "member", msg["content"]["text"], msg)
        await finish(service)
        assert not client.calls and not sender.videos
    asyncio.run(scenario())


def test_queue_counts_are_reported_before_wait_and_cancel_does_not_recharge(monkeypatch, tmp_path, movie):
    async def scenario():
        service, platform, client, sender = build(tmp_path, movie)
        monkeypatch.setattr(V, "get_full_name", lambda uid: uid)
        client.block = asyncio.Event()
        for i in range(6):
            await submit(service, platform, sender, message("m" + str(i), "u" + str(i)))
        assert len(service.tasks) == 5
        for _ in range(100):
            if len(sender.notices) >= 6 and client.calls:
                break
            await asyncio.sleep(.01)
        assert len(client.calls) == 1
        for i in range(1, 5):
                assert any(f"u{i}，小小糯收到啦！正在努力想象中~前面有 {i-1} 个视频排队，另有 1 个正在处理" in x for x in sender.notices)
        assert any("队列已满" in x for x in sender.notices)
        await service.close()
        assert not service.tasks
        assert service.store.get(V.PRIMARY_CID, "m0")["status"] == "interrupted"
        restarted = V.Service(Store(service.store.path), platform, client)
        await submit(restarted, platform, sender, message("m0", "u0"))
        assert not restarted.tasks and len(client.calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["generate", "send"])
def test_cancellation_switch_and_membership_preserve_generated_bytes(monkeypatch, tmp_path, movie, phase):
    async def scenario():
        service, platform, client, sender = build(tmp_path, movie)
        client.block = asyncio.Event()
        await submit(service, platform, sender)
        for _ in range(100):
            if client.calls:
                break
            await asyncio.sleep(.01)
        if phase == "generate":
            features._OVERRIDES[V.PRIMARY_CID + "|videogen"] = False
        else:
            platform.allowed = False
        client.block.set()
        await finish(service)
        assert not sender.videos
        assert service.store.get(V.PRIMARY_CID, "cmd1")["status"] == "ready"
    asyncio.run(scenario())


@pytest.mark.parametrize("ambiguous", [False, True])
def test_send_rejection_can_resume_bytes_but_unknown_send_never_repeats(tmp_path, movie, ambiguous):
    async def scenario():
        service, platform, client, sender = build(tmp_path, movie)
        sender.error = BotApiAmbiguous("unknown") if ambiguous else BotApiRejected("offline")
        await submit(service, platform, sender)
        await finish(service)
        assert service.store.get(V.PRIMARY_CID, "cmd1")["status"] == ("delivery_unknown" if ambiguous else "send_failed")
        sender.error = None
        await submit(service, platform, sender)
        await finish(service)
        assert len(client.calls) == 1 and len(sender.videos) == (1 if ambiguous else 2)
    asyncio.run(scenario())


def test_unavailable_video_channel_dry_cooldown_and_invalid_bytes_do_not_publish(tmp_path, movie):
    async def scenario():
        service, platform, client, sender = build(tmp_path, movie)
        sender.ready = False
        await submit(service, platform, sender)
        assert not client.calls and not service.tasks
        sender.ready = True
        await submit(service, platform, sender, dry=True)
        assert not client.calls and service.store.get(V.PRIMARY_CID, "cmd1") is None
        client.data = b"not mp4"
        await submit(service, platform, sender)
        await finish(service)
        assert not sender.videos and service.store.get(V.PRIMARY_CID, "cmd1")["status"] == "failed"
        await submit(service, platform, sender, message("second"))
        assert len(client.calls) == 1 and any("频繁" in x for x in sender.notices)
    asyncio.run(scenario())


def test_shared_database_recovery_never_changes_other_live_media_jobs(tmp_path):
    store = Store(tmp_path / "shared.db")
    for mid, mode in [("img", "pending"), ("vid", "video_pending")]:
        store.reserve(V.PRIMARY_CID, mid, mid, mode, 0)
        store.status(V.PRIMARY_CID, mid, "generating")
    V.Service(store=store, client=object())
    assert store.get(V.PRIMARY_CID, "img")["status"] == "generating"
    assert store.get(V.PRIMARY_CID, "vid")["status"] == "interrupted"
    store.status(V.PRIMARY_CID, "vid", "generating")
    imagegen.Service(store=store, client=object())
    assert store.get(V.PRIMARY_CID, "vid")["status"] == "generating"


def test_generated_video_url_range_head_and_gallery_save(monkeypatch, tmp_path, movie):
    async def scenario():
        store = Store(tmp_path / "generated.db")
        store.reserve(V.PRIMARY_CID, "m", "u", "video_generate", 0)
        token = store.save_media(V.PRIMARY_CID, "m", movie, "video/mp4")
        monkeypatch.setenv("DZMM_IMAGEGEN_DB", str(store.path))
        assert await GalleryPlatform().download(imagegen.image_base_url() + "/" + token, video=True) == movie
        with pytest.raises(GalleryError):
            await GalleryPlatform().download(imagegen.image_base_url() + "/" + token)
        server = TestServer(create_app(store=GalleryStore(tmp_path / "gallery.db"), auth=object(), imagegen_store=store))
        await server.start_server()
        try:
            async with ClientSession() as client:
                url = server.make_url("/api/gallery/generated/" + token)
                response = await client.get(url, headers={"Range": "bytes=5-12"})
                assert response.status == 206 and await response.read() == movie[5:13]
                assert response.content_type == "video/mp4"
                response = await client.head(url)
                assert response.status == 200 and int(response.headers["Content-Length"]) == len(movie)
                response = await client.get(url, headers={"Range": "bytes=-0"})
                assert response.status == 416
        finally:
            await server.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.parametrize("duration", [6, 15])
def test_xai_rest_submission_poll_streamed_download_and_key_isolation(monkeypatch, movie, edit, duration):
    async def scenario():
        polls, submitted = [], []
        async def endpoint(request):
            assert request.headers["Authorization"] == "Bearer test-only"
            payload = await request.json()
            assert payload["model"] == V.MODEL and payload["duration"] == duration
            if edit:
                assert "image_url" not in payload
                assert base64.b64decode(payload["image"]["url"].split(",")[1]) == png()
            return web.json_response({"request_id": "job-id"})
        async def poll(request):
            polls.append(1)
            assert request.headers["Authorization"] == "Bearer test-only"
            return web.json_response({"status": "pending"} if len(polls) == 1 else
                {"status": "done", "video": {"url": "https://vidgen.x.ai/result.mp4", "respect_moderation": True}})
        async def download(request):
            assert "Authorization" not in request.headers
            response = web.StreamResponse(headers={"Content-Type": "video/mp4"})
            await response.prepare(request)
            await response.write(movie[:13])
            await response.write(movie[13:])
            return response
        app = web.Application()
        app.router.add_post("/videos/generations", endpoint)
        app.router.add_get("/videos/job-id", poll)
        app.router.add_get("/result", download)
        server = TestServer(app)
        await server.start_server()
        # 仅把供应商 HTTPS 下载 URL 映射到本地模拟端点，API/下载仍经过真实 HTTP。
        original_get = ClientSession.get
        def local_download(self, url, **kwargs):
            if url == "https://vidgen.x.ai/result.mp4":
                url = server.make_url("/result")
            return original_get(self, url, **kwargs)
        monkeypatch.setattr(ClientSession, "get", local_download)
        monkeypatch.setattr(V, "API_ROOT", str(server.make_url("/" )).rstrip("/"))
        monkeypatch.setattr(V, "POLL_INTERVAL", 0)
        try:
            data = await V.XaiVideoClient("test-only").generate("cat", (png(), "image/png") if edit else None,
                duration=duration, on_submitted=submitted.append)
            assert data == movie and len(polls) == 2 and submitted == ["job-id"]
        finally:
            await server.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("reply", ["failed", "malformed", "oversized", "rejected", "moderation", "bad-id", "timeout"])
def test_xai_failures_never_retry_post_or_expose_provider_response(monkeypatch, reply):
    async def scenario():
        calls = []
        async def endpoint(request):
            calls.append(request.method)
            if reply == "rejected":
                return web.Response(status=401, text="provider-secret test-only")
            if reply == "malformed":
                return web.Response(text="provider-secret test-only")
            if reply == "oversized":
                return web.Response(body=b"a" * (V.MAX_RESPONSE_BYTES + 1))
            return web.json_response({"request_id": "../bad" if reply == "bad-id" else "job-id"})
        async def poll(request):
            if reply == "moderation":
                return web.json_response({"status": "done", "video": {"respect_moderation": False}})
            return web.json_response({"status": "pending" if reply == "timeout" else "failed"})
        app = web.Application()
        app.router.add_post("/videos/generations", endpoint)
        app.router.add_get("/videos/job-id", poll)
        server = TestServer(app)
        await server.start_server()
        monkeypatch.setattr(V, "API_ROOT", str(server.make_url("/")).rstrip("/"))
        monkeypatch.setattr(V, "POLL_INTERVAL", .01)
        try:
            with pytest.raises(V.VideogenError) as exc:
                await V.XaiVideoClient("test-only").generate("cat", timeout=.15 if reply == "timeout" else 3)
            assert "test-only" not in str(exc.value) and "provider-secret" not in str(exc.value)
            assert calls == ["POST"]
        finally:
            await server.close()
    asyncio.run(scenario())
