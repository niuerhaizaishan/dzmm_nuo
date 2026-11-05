"""认证群 /生视频：有限后台队列、xAI 异步任务和持久化原生引用结果。"""
import asyncio
import base64
import json
import os
import re
from urllib.parse import urlsplit

import aiohttp

from runtime.ctx import PRIMARY_CID
from runtime.features import on as _feat_on
from runtime.gallery import GalleryError, GalleryPlatform, GalleryUnavailable, inspect_image, inspect_media
from runtime.imagegen import image_base_url
from runtime.imagegen_store import Store
from runtime.identity import get_full_name
from runtime.moderation_platform import ModerationUnavailable
from runtime.obs import log
from runtime.text import _normalize_command_prefix
from runtime.transport_bot import BotApiRejected
from runtime.tuning import get as _tune

MODEL = "grok-imagine-video-1.5"
API_ROOT = "https://api.x.ai/v1"
POLL_INTERVAL = 5
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 65536
COMMAND = re.compile(r"^/生视频(?=$|\s|[：:])\s*[：:]?\s*(.*)$", re.S)
USAGE = "文生视频：/生视频 提示词\n图生视频：回复一张图片，发送 /生视频 动起来\n每次生成一个 6 秒、480p 短视频；结果回复最初的指令。"


class VideogenError(ValueError):
    """固定用户文案，不回显凭据、原图地址或服务原始响应。"""


def command_payload(text):
    match = COMMAND.fullmatch(_normalize_command_prefix(text))
    return match[1].strip() if match else None


def in_scope(cid):
    return cid == PRIMARY_CID


def enabled(cid):
    return in_scope(cid) and _feat_on(cid, "videogen")


async def _read_limited(response, limit):
    data = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise VideogenError("生视频返回结果过大，本次未发送。")
    return bytes(data)


class XaiVideoClient:
    def __init__(self, key=None):
        self.key = (os.environ.get("XAI_API_KEY", "") if key is None else key).strip()

    async def generate(self, prompt, source=None, *, timeout=300, on_submitted=None):
        if not self.key:
            raise VideogenError("生视频服务尚未配置，请联系管理员。")
        payload = {"model": MODEL, "prompt": prompt, "duration": 6, "resolution": "480p"}
        if source is None:
            payload["aspect_ratio"] = "1:1"
        else:
            data, mime = source
            # REST 使用 image.url；image_url 是 SDK 的形参，不能直接传给 REST。
            payload["image"] = {"url": "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")}
        headers = {"Authorization": "Bearer " + self.key}
        try:
            # 轮询与下载均计入总预算；不重试计费 POST。
            async with asyncio.timeout(timeout):
                async with aiohttp.ClientSession(trust_env=True,
                        timeout=aiohttp.ClientTimeout(total=45, connect=15)) as session:
                    async with session.post(API_ROOT + "/videos/generations", json=payload,
                            headers=headers, allow_redirects=False) as response:
                        result = await self._json(response)
                    request_id = result.get("request_id")
                    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", request_id):
                        raise VideogenError("生视频服务没有返回有效任务编号，本次不会自动重复生成。")
                    if on_submitted:
                        on_submitted(request_id)
                    while True:
                        await asyncio.sleep(POLL_INTERVAL)
                        async with session.get(API_ROOT + "/videos/" + request_id,
                                headers=headers, allow_redirects=False) as response:
                            result = await self._json(response)
                        status = result.get("status")
                        if status == "pending":
                            continue
                        if status == "failed":
                            raise VideogenError("生视频任务失败，请调整提示词或原图后再试。")
                        video = result.get("video")
                        if status != "done" or not isinstance(video, dict):
                            raise VideogenError("生视频服务返回异常，本次不会自动重复生成。")
                        if video.get("respect_moderation") is False:
                            raise VideogenError("生视频结果未通过服务审核，本次未发送。")
                        url = video.get("url")
                        parts = urlsplit(url) if isinstance(url, str) else None
                        if (not parts or parts.scheme != "https" or not parts.hostname
                                or parts.username or parts.password):
                            raise VideogenError("生视频服务没有返回有效视频地址。")
                        # 媒体下载不携带 API Authorization，也不跟随重定向。
                        async with session.get(url, allow_redirects=False,
                                timeout=aiohttp.ClientTimeout(total=60, connect=15)) as download:
                            if download.status != 200:
                                raise VideogenError("生成视频下载失败，本次未发送。")
                            return await _read_limited(download, MAX_VIDEO_BYTES)
        except (VideogenError, asyncio.CancelledError):
            raise
        except asyncio.TimeoutError as exc:
            raise VideogenError("生视频等待超时，结果尚不确定，本次不会自动重复生成。") from exc
        except (aiohttp.ClientError, ValueError, TypeError) as exc:
            raise VideogenError("生视频连接或返回异常，结果尚不确定，本次不会自动重复生成。") from exc

    @staticmethod
    async def _json(response):
        raw = await _read_limited(response, MAX_RESPONSE_BYTES)
        if response.status not in (200, 201, 202):
            hint = raw.decode("utf-8", errors="replace").lower()
            if response.status in (401, 403) or "incorrect api key" in hint or "invalid api key" in hint:
                raise VideogenError("生视频服务认证失败，请联系管理员。")
            if response.status == 402 or ("insufficient" in hint and ("credit" in hint or "balance" in hint)):
                raise VideogenError("生视频服务余额不足，请联系管理员。")
            if response.status == 429:
                raise VideogenError("生视频服务请求过多，请稍后再试。")
            raise VideogenError("生视频服务拒绝或未确认请求，本次不会自动重复生成。")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise VideogenError("生视频服务返回异常，本次不会自动重复生成。")
        return result


class Service:
    def __init__(self, store=None, platform=None, client=None):
        self.store = store or Store()
        self.platform = platform or GalleryPlatform()
        self.client = client or XaiVideoClient()
        self.tasks = {}
        self.waiting, self.processing, self.users = set(), set(), set()
        self.semaphore = asyncio.Semaphore(1)
        self.admission = asyncio.Lock()
        self.store.recover("video")

    async def original_command(self, cid, uid, prompt, message):
        mid = str(message.get("message_id") or "")
        original = await self.platform.original(mid, str(message.get("sent_at") or ""))
        content = original.get("content") or {}
        if (not mid or original.get("message_id") != mid or original.get("sent_by") != uid
                or original.get("chatroom_id") not in (None, cid)
                or command_payload(content.get("text")) != prompt):
            raise VideogenError("无法核验这条生视频指令，请重新发送。")
        self.store.save_reference(cid, mid, {"id": mid, "sentBy": uid,
            "content": {"type": "text", "text": content.get("text") or ""}})
        return original

    async def source(self, cid, uid, prompt, message):
        original = await self.original_command(cid, uid, prompt, message)
        content = original.get("content") or {}
        quoted = content.get("reference") or content.get("quote") or content.get("reply")
        if not quoted:
            pushed = message.get("content") or {}
            if pushed.get("reference") or pushed.get("quote") or pushed.get("reply"):
                raise VideogenError("暂时无法核验引用图片，请重新回复原图。")
            return None
        photo_id = str(quoted.get("id") or quoted.get("message_id") or "") if isinstance(quoted, dict) else ""
        if not photo_id:
            raise VideogenError("引用信息不完整，请重新回复原图。")
        photo = await self.platform.original(photo_id, str(quoted.get("sent_at") or quoted.get("sentAt") or ""))
        image = photo.get("content") or {}
        if photo.get("message_id") != photo_id or photo.get("chatroom_id") not in (None, cid):
            raise VideogenError("无法核验引用图片，请重新回复本群原图。")
        if str(image.get("type") or "").lower() != "image":
            raise VideogenError("请回复一张静态图片，再发送 /生视频。")
        url = image.get("url") or image.get("text") or image.get("imageUrl") or image.get("image_url")
        if not isinstance(url, str):
            raise VideogenError("无法读取引用图片，请重新发送原图。")
        prefix = image_base_url() + "/"
        own = self.store.image(url[len(prefix):]) if url.startswith(prefix) else None
        data = own["data"] if own else await self.platform.download(url)
        _, _, mime, _ = await asyncio.to_thread(inspect_image, data)
        if mime == "image/gif":
            raise VideogenError("图生视频请使用 JPG、PNG 或 WebP 静态图片。")
        return data, mime

    async def handle(self, sender, cid, uid, text, message, *, dry=False):
        prompt = command_payload(text)
        if prompt is None or not enabled(cid):
            return
        name = self.display_name(uid)
        try:
            if not prompt:
                await self.notice(sender, cid, USAGE, dry, name=name)
                return
            limit = int(_tune("videogen.input_limit", cid))
            if len(prompt) > limit:
                raise VideogenError(f"提示词最多 {limit} 字，请缩短后再试。")
            mid = str(message.get("message_id") or "")
            if not mid or message.get("sent_by") != uid or message.get("chatroom_id") not in (None, cid):
                raise VideogenError("无法核验这条生视频指令，请重新发送。")
            async with self.admission:
                if (cid, mid) in self.tasks:
                    return
                previous = self.store.get(cid, mid)
                if previous and previous["uid"] != uid:
                    raise VideogenError("生视频指令身份不一致，未执行。")
                if previous and previous["status"] not in ("ready", "send_failed"):
                    return
                if uid in self.users:
                    raise VideogenError("你上一个视频还在处理中，请等完成后再试。")
                if len(self.tasks) >= 5:
                    raise VideogenError("生视频队列已满，请稍后再试。")
                if not getattr(sender, "can_send_video_reply", lambda _cid: False)(cid):
                    raise VideogenError("账号视频通道未就绪，官方 Bot 暂无视频接口；本次未提交生成。")
                if not dry and not previous and not self.client.key:
                    raise VideogenError("生视频服务尚未配置，请联系管理员。")
                image_base_url()
                if dry:
                    await self.platform.member(uid)
                    if not previous:
                        await self.source(cid, uid, prompt, message)
                    return
                if not previous:
                    result = self.store.reserve(cid, mid, uid, "video_pending", int(_tune("videogen.cooldown", cid)))
                    if result == "duplicate":
                        return
                    if result == "cooldown":
                        raise VideogenError("生视频提交有点频繁，请稍后再试。")
                self.users.add(uid)
                self.waiting.add((cid, mid))
                self.tasks[(cid, mid)] = asyncio.create_task(self.run(sender, cid, mid, uid, prompt, message, previous))
        except (VideogenError, GalleryError, GalleryUnavailable, ModerationUnavailable) as exc:
            await self.notice(sender, cid, "不过，" + str(exc), dry, name=name)
        except Exception as exc:
            log("videogen: 提交失败 " + type(exc).__name__)
            await self.notice(sender, cid, "不过，这次生视频没有提交成功，请稍后再试。", dry, name=name)

    @staticmethod
    def display_name(uid):
        return str(get_full_name(uid)).replace("\n", " ")[:60] or "群友"

    async def notice(self, sender, cid, text, dry=False, *, name=None):
        if not dry:
            try:
                prefix = (str(name).replace("\n", " ")[:60] if name else "小伙伴")
                await sender.send(prefix + "，小小糯收到啦！" + text, cid)
            except Exception as exc:
                log("videogen: 提示发送未确认 " + type(exc).__name__)

    async def queue_notice(self, sender, cid, mid, uid, previous):
        ahead = []
        for key in self.tasks:
            if key == (cid, mid):
                break
            ahead.append(key)
        waiting = sum(key in self.waiting for key in ahead)
        working = sum(key in self.processing for key in ahead)
        name = self.display_name(uid)
        await self.notice(sender, cid, f"正在努力想象中~前面有 {waiting} 个视频排队，另有 {working} 个正在处理。"
                          + ("这次会把已经生成的视频发给你。" if previous else ""), name=name)

    async def run(self, sender, cid, mid, uid, prompt, message, previous):
        announcement = None
        try:
            if not enabled(cid):
                self.store.status(cid, mid, "cancelled")
                return
            announcement = asyncio.create_task(self.queue_notice(sender, cid, mid, uid, previous))
            async with self.semaphore:
                self.waiting.discard((cid, mid))
                self.processing.add((cid, mid))
                await announcement
                if not enabled(cid):
                    self.store.status(cid, mid, "cancelled")
                    return
                name = await self.platform.member(uid)
                source = None
                if previous:
                    token = previous["token"]
                    if not self.store.reference(cid, mid):
                        await self.original_command(cid, uid, prompt, message)
                else:
                    source = await self.source(cid, uid, prompt, message)
                    if not enabled(cid):
                        self.store.status(cid, mid, "cancelled")
                        return
                    self.store.mode(cid, mid, "video_edit" if source else "video_generate")
                    self.store.status(cid, mid, "generating")
                    data = await self.client.generate(prompt, source, timeout=int(_tune("videogen.timeout", cid)),
                        on_submitted=lambda request_id: self.store.save_request_id(cid, mid, request_id))
                    _, _, mime, _ = await asyncio.to_thread(inspect_media, data, "result.mp4", "video/mp4")
                    if mime != "video/mp4":
                        raise VideogenError("生成结果不是有效 MP4，本次未发送。")
                    token = self.store.save_media(cid, mid, data, mime)
                if not enabled(cid):
                    return
                await self.platform.member(uid)
                if not enabled(cid):
                    return
                editing = source is not None or (previous or {}).get("mode") == "video_edit"
                await self.deliver(sender, cid, mid, image_base_url() + "/" + token,
                    str(name).replace("\n", " ")[:60] + " · " + ("图生视频" if editing else "文生视频"),
                    self.store.reference(cid, mid))
        except asyncio.CancelledError:
            row = self.store.get(cid, mid)
            if row and row["status"] in ("queued", "generating", "sending"):
                self.store.status(cid, mid, "delivery_unknown" if row["status"] == "sending" else "interrupted")
            raise
        except (VideogenError, GalleryError, GalleryUnavailable, ModerationUnavailable) as exc:
            row = self.store.get(cid, mid)
            if row and row["status"] not in ("ready", "send_failed", "delivery_unknown"):
                self.store.status(cid, mid, "failed")
            await self.notice(sender, cid, "不过，" + str(exc), name=self.display_name(uid))
        except Exception as exc:
            log("videogen: 任务失败 " + type(exc).__name__)
            row = self.store.get(cid, mid)
            if row and row["status"] not in ("ready", "send_failed", "delivery_unknown", "sent"):
                self.store.status(cid, mid, "delivery_unknown" if row["status"] == "sending" else "failed")
            await self.notice(sender, cid, "不过，本次视频处理未完成，结果已记录，不会自动重复生成。", name=self.display_name(uid))
        finally:
            if announcement is not None:
                if not announcement.done():
                    announcement.cancel()
                await asyncio.gather(announcement, return_exceptions=True)
            self.processing.discard((cid, mid))
            self.waiting.discard((cid, mid))
            self.users.discard(uid)
            self.tasks.pop((cid, mid), None)

    async def deliver(self, sender, cid, mid, url, caption, reference):
        if not reference or reference.get("id") != mid:
            raise VideogenError("无法核验最初的生视频指令，视频已保存，本次未发送。")
        self.store.status(cid, mid, "sending")
        try:
            sent = await sender.send_video_reply_with_id(url, cid, reference, caption=caption)
            if not sent:
                raise RuntimeError("缺少视频平台编号")
        except BotApiRejected:
            self.store.status(cid, mid, "send_failed")
            raise VideogenError("视频已生成并保存，但账号视频通道拒绝发送；官方 Bot 暂无视频接口，无需重新生成。")
        except Exception:
            self.store.status(cid, mid, "delivery_unknown")
            raise VideogenError("视频已生成并保存，发送结果待核对；为避免重复发送，不再重试。")
        self.store.status(cid, mid, "sent", sent)
        from runtime.generated_media import send_result_reply
        await send_result_reply(sender, self.store, cid, mid, reference, "视频")

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.store.recover("video")
        self.tasks.clear()
        self.waiting.clear()
        self.processing.clear()
        self.users.clear()


_service = None


async def handle(sender, cid, uid, text, message, *, dry=False):
    global _service
    if enabled(cid):
        if _service is None:
            _service = Service()
        await _service.handle(sender, cid, uid, text, message, dry=dry)


async def shutdown():
    global _service
    if _service is not None:
        await _service.close()
        _service = None
