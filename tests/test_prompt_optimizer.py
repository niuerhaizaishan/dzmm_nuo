"""/提示词 视频：OpenAI-compatible prompt optimization contract."""
import asyncio
import base64
from io import BytesIO
import json

from PIL import Image
import pytest

from runtime import features
from runtime import prompt_optimizer as P

CID = P.PRIMARY_CID


def command(text="猫", reference=None):
    content = {"type": "text", "text": "/提示词 视频 " + text}
    if reference is not None:
        content["reference"] = reference
    return {"message_id": "cmd", "sent_by": "uid", "chatroom_id": CID, "content": content}


def png(size=(32, 24)):
    stream = BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 128)).save(stream, "PNG")
    return stream.getvalue()


class Platform:
    def __init__(self, message):
        self.command = message
        self.photo = {"message_id": "photo", "chatroom_id": CID,
                      "content": {"type": "image", "url": "https://rls.cheggpt.com/test.png"}}
        self.data = png()
        self.downloads = []
        self.reads = []

    async def original(self, mid, sent_at=""):
        self.reads.append(mid)
        return self.command if mid == "cmd" else self.photo

    async def download(self, url):
        self.downloads.append(url)
        return self.data


class Sender:
    def __init__(self):
        self.sent = []

    async def send(self, text, cid):
        self.sent.append((text, cid))
        return True


def test_command_parser_accepts_video_only_and_rejects_other_modes():
    assert P.command_payload("/提示词 视频 一只猫跃过屋顶") == ("视频", "一只猫跃过屋顶")
    assert P.command_payload("／提示词 视频：镜头推进") == ("视频", "镜头推进")
    assert P.command_payload("/提示词 图片 一只猫") is None
    assert P.command_payload("/提示词 视频") == ("视频", "")


def test_extract_content_accepts_plain_and_markdown_wrapped_output():
    assert P._extract_content({"choices": [{"message": {"content": "镜头缓慢推进"}}]}) == "镜头缓慢推进"
    assert P._extract_content({"choices": [{"message": {"content": "```\n镜头缓慢推进\n```"}}]}) == "镜头缓慢推进"


def test_generate_posts_only_safe_prompt_payload(monkeypatch):
    class Content:
        async def iter_chunked(self, limit):
            raw = json.dumps({"choices": [{"message": {"content": "cinematic camera push-in"}}]}).encode()
            yield raw[:15]
            yield raw[15:]
    class Response:
        status = 200
        content = Content()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False

    class Session:
        def __init__(self): self.calls = []
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    async def scenario():
        session = Session()
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-secret")
        result = await P.generate("一只猫在雨夜街道慢慢回头", session=session)
        assert result == "cinematic camera push-in"
        url, kwargs = session.calls[0]
        assert url == "https://www.kurumiai.net/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer test-secret"
        body = kwargs["json"]
        assert body["model"] == "gpt-6.1-sol"
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["content"] == "一只猫在雨夜街道慢慢回头"
        assert "test-secret" not in json.dumps(body)
    asyncio.run(scenario())


def test_handle_uses_small_nuo_style_and_safe_error(monkeypatch, dbdir):
    async def fake_generate(prompt):
        assert prompt == "猫在雨夜慢慢回头"
        return "cinematic cat turning in rain"

    async def scenario():
        saved = features.overrides()
        features.set_overrides({CID + "|prompt_optimize": True})
        monkeypatch.setattr(P, "get_full_name", lambda uid: "小明")
        monkeypatch.setattr(P, "generate", fake_generate)
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-key")
        sender = Sender()
        try:
            message = command("猫在雨夜慢慢回头")
            service = P.Service(P.Store(dbdir / "prompts.db"), Platform(message))
            await service.handle(sender, CID, "uid", "/提示词 视频 猫在雨夜慢慢回头", message)
            await asyncio.gather(*service.tasks.values())
            assert sender.sent == [
                ("小明，小小糯收到啦！正在帮你打磨视频提示词~前面有 0 段提示词排队。", CID),
                ("小明，小小糯整理好啦！\ncinematic cat turning in rain", CID),
            ]
        finally:
            features.set_overrides(saved)
    asyncio.run(scenario())


def test_disabled_prompt_optimizer_does_not_handle(monkeypatch):
    async def scenario():
        saved = features.overrides()
        features.set_overrides({CID + "|prompt_optimize": False})
        try:
            sender = Sender()
            await P.handle(sender, CID, "uid", "/提示词 视频 猫", {})
            assert not sender.sent
        finally:
            features.set_overrides(saved)
    asyncio.run(scenario())


def test_queued_request_does_not_block_and_duplicate_after_restart_never_calls_api(monkeypatch, dbdir):
    async def scenario():
        saved = features.overrides()
        features.set_overrides({CID + "|prompt_optimize": True})
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-key")
        gate = asyncio.Event()
        calls = []
        async def generate(prompt):
            calls.append(prompt)
            await gate.wait()
            return "雨夜中景，一只猫轻轻回头，镜头缓慢推进。"
        monkeypatch.setattr(P, "generate", generate)
        store = P.Store(dbdir / "prompts.db")
        sender = Sender()
        message = command()
        message["message_id"] = "same"
        platform = Platform(message)
        async def original(*_): return message
        platform.original = original
        service = P.Service(store, platform)
        try:
            await service.handle(sender, CID, "uid", "/提示词 视频 猫", message)
            assert service.tasks  # returned while model is pending
            await service.handle(sender, CID, "uid", "/提示词 视频 猫", message)
            gate.set()
            await asyncio.gather(*service.tasks.values())
            await P.Service(store).handle(sender, CID, "uid", "/提示词 视频 猫", message)
            assert calls == ["猫"] and store.get(CID, "same")["status"] == "sent"
            assert len(sender.sent) == 2
        finally:
            await service.close()
            features.set_overrides(saved)
    asyncio.run(scenario())


def test_unknown_delivery_keeps_generated_prompt_without_regenerating(monkeypatch, dbdir):
    async def scenario():
        saved = features.overrides()
        features.set_overrides({CID + "|prompt_optimize": True})
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-key")
        calls = []
        async def generate(prompt): calls.append(prompt); return "已保存的提示词"
        monkeypatch.setattr(P, "generate", generate)
        class UnknownSender(Sender):
            async def send(self, text, cid):
                self.sent.append((text, cid))
                if "整理好啦" in text: raise TimeoutError("provider secret must not be printed")
                return True
        sender = UnknownSender()
        store = P.Store(dbdir / "prompts.db")
        message = command()
        service = P.Service(store, Platform(message))
        try:
            await service.handle(sender, CID, "uid", "/提示词 视频 猫", message)
            await asyncio.gather(*service.tasks.values())
            row = store.get(CID, "cmd")
            assert row["result"] == "已保存的提示词" and row["status"] == "delivery_unknown"
            await P.Service(store).handle(sender, CID, "uid", "/提示词 视频 猫", message)
            assert calls == ["猫"] and len(sender.sent) == 2
        finally:
            features.set_overrides(saved)
    asyncio.run(scenario())


def test_vision_request_contains_validated_image_bytes_and_visual_guidance(monkeypatch):
    class Content:
        async def iter_chunked(self, limit):
            yield json.dumps({"choices": [{"message": {"content": "主体轻轻转头，镜头缓慢推进。"}}]}).encode()
    class Response:
        status = 200
        content = Content()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    class Session:
        calls = []
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()
    async def scenario():
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-secret")
        session = Session()
        await P.generate("慢慢回头", source=png((2400, 1200)), session=session)
        body = session.calls[0][1]["json"]
        content = body["messages"][1]["content"]
        assert content[0] == {"type": "text", "text": "慢慢回头"}
        url = content[1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            assert image.size == (1536, 768) and image.mode == "RGB"
            assert "exif" not in image.info
        system = body["messages"][0]["content"]
        assert "实际主体" in system and "本次未附图片" not in system
        assert "test-secret" not in json.dumps(body)
    asyncio.run(scenario())


def test_recover_missing_push_reference_and_ignore_forged_url(monkeypatch, dbdir):
    async def scenario():
        monkeypatch.setattr(features, "_OVERRIDES", {CID + "|prompt_optimize": True})
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-key")
        original = command(reference={"id": "photo", "content": {"url": "https://forged.invalid/private"}})
        platform = Platform(original)
        service = P.Service(P.Store(dbdir / "vision.db"), platform)
        calls = []
        async def generate(prompt, *, source=None):
            calls.append((prompt, source)); return "保持原图主体和构图，轻轻转头。"
        monkeypatch.setattr(P, "generate", generate)
        sender = Sender()
        await service.handle(sender, CID, "uid", "/提示词 视频 猫", command())
        await asyncio.gather(*service.tasks.values())
        assert platform.reads == ["cmd", "photo"]
        assert platform.downloads == ["https://rls.cheggpt.com/test.png"]
        assert calls == [("猫", platform.data)]
        assert "结合原图整理好啦" in sender.sent[-1][0]
        assert service.store.get(CID, "cmd")["status"] == "sent"
    asyncio.run(scenario())


@pytest.mark.parametrize("broken", ["foreign-photo", "text", "video", "empty-reference", "missing-id",
                                       "wrong-photo", "wrong-user", "wrong-command", "push-only-reference"])
def test_unverified_reference_never_reaches_paid_api(monkeypatch, dbdir, broken):
    async def scenario():
        monkeypatch.setattr(features, "_OVERRIDES", {CID + "|prompt_optimize": True})
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-key")
        original = command(reference={"id": "photo"})
        pushed = command()
        platform = Platform(original)
        if broken == "foreign-photo": platform.photo["chatroom_id"] = "another-room"
        elif broken in ("text", "video"): platform.photo["content"]["type"] = broken
        elif broken == "empty-reference": original["content"]["reference"] = {}
        elif broken == "missing-id": original["content"]["reference"] = {"content": {"type": "image"}}
        elif broken == "wrong-photo": platform.photo["message_id"] = "other-photo"
        elif broken == "wrong-user": original["sent_by"] = "other-user"
        elif broken == "wrong-command": original["content"]["text"] = "/提示词 视频 另一条要求"
        else:
            original["content"].pop("reference")
            pushed["content"]["reference"] = {"id": "photo"}
        service = P.Service(P.Store(dbdir / "bad-ref.db"), platform)
        calls = []
        async def generate(*args, **kwargs):
            calls.append((args, kwargs)); raise AssertionError("Must not call paid API")
        monkeypatch.setattr(P, "generate", generate)
        sender = Sender()
        await service.handle(sender, CID, "uid", "/提示词 视频 猫", pushed)
        await asyncio.gather(*service.tasks.values())
        assert service.store.get(CID, "cmd")["status"] == "failed"
        assert not platform.downloads and not calls
        assert "小小糯收到啦！不过，" in sender.sent[-1][0]
    asyncio.run(scenario())


def test_generated_image_reference_reads_local_bytes_without_http(monkeypatch, dbdir):
    from runtime.gallery import GalleryPlatform
    from runtime.imagegen_store import Store
    from runtime.imagegen import image_base_url
    import requests
    async def scenario():
        monkeypatch.setenv("DZMM_IMAGEGEN_DB", str(dbdir / "images.db"))
        store = Store()
        store.reserve(CID, "old-image", "other-user", "generate", 0)
        data = png()
        token = store.save_image(CID, "old-image", data, "image/png")
        original = command(reference={"id": "photo"})
        platform = Platform(original)
        platform.photo["content"]["url"] = image_base_url() + "/" + token
        platform.download = GalleryPlatform().download
        def no_http(*args, **kwargs): raise AssertionError("Generated image should be read locally")
        monkeypatch.setattr(requests, "get", no_http)
        result = await P.Service(P.Store(dbdir / "prompts.db"), platform).source(CID, "uid", "猫", original)
        assert result == data
    asyncio.run(scenario())


def test_visual_copy_respects_exif_orientation_and_rejects_animation():
    stream = BytesIO()
    image = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif(); exif[274] = 6
    image.save(stream, "JPEG", exif=exif)
    url = P._image_data_url(stream.getvalue())
    with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as visual:
        assert visual.size == (20, 40) and not visual.getexif()
    stream = BytesIO()
    image.save(stream, "GIF")
    with pytest.raises(P.PromptOptimizeError, match="静态图片"):
        P._image_data_url(stream.getvalue())


def test_invalid_image_is_rejected_before_post_and_no_text_fallback(monkeypatch):
    class Session:
        def post(self, *args, **kwargs): raise AssertionError("No paid request for invalid image")
    async def scenario():
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-key")
        with pytest.raises(P.PromptOptimizeError, match="原图损坏"):
            await P.generate("回头", source=b"broken image", session=Session())
    asyncio.run(scenario())


def test_vision_rejection_is_explicit_and_not_retried_as_text(monkeypatch):
    class Response:
        status = 400
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    class Session:
        def __init__(self): self.calls = 0
        def post(self, *args, **kwargs): self.calls += 1; return Response()
    async def scenario():
        monkeypatch.setenv("PROMPT_OPTIMIZER_API_KEY", "test-key")
        session = Session()
        with pytest.raises(P.PromptOptimizeError, match="图文请求"):
            await P.generate("回头", source=png(), session=session)
        assert session.calls == 1
    asyncio.run(scenario())


def test_actual_dispatch_honors_both_gates_and_owns_help(monkeypatch):
    from runtime import dispatch as D
    async def scenario():
        called = []
        async def handle(*args, **kwargs): called.append((args, kwargs))
        monkeypatch.setattr(P, "handle", handle)
        saved = features.overrides()
        sender = Sender()
        message = {"message_id": "cmd", "sent_by": "uid", "content": {"text": "/提示词 视频 猫"}}
        try:
            for slash, feature in [(True, True), (False, True), (True, False)]:
                features.set_overrides({CID + "|slash_cmds": slash, CID + "|prompt_optimize": feature})
                await D.handle_slash(sender, CID, "/提示词 视频 猫", "uid", False, {}, [message], message, {},
                    _save_group=lambda *a: None, _mark_inflight=lambda *a: None,
                    _remember_turn=lambda *a: None, rank_summary=lambda *a: "", sync_profiles=lambda: "")
            assert len(called) == 1 and called[0][0][-1] == message
            assert not sender.sent
        finally:
            features.set_overrides(saved)
    asyncio.run(scenario())
