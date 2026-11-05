"""妹子认证群 /生图：文字生成、引用原图编辑、有限后台任务及持久去重。"""
import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
import json
import os
import re
from urllib.parse import urlsplit

import aiohttp

from runtime.ctx import PRIMARY_CID
from runtime.features import on as _feat_on
from runtime.gallery import GalleryError, GalleryPlatform, GalleryUnavailable, inspect_image
from runtime.imagegen_store import Store
from runtime.identity import get_full_name
from runtime.moderation_platform import ModerationUnavailable
from runtime.obs import log
from runtime.text import _normalize_command_prefix
from runtime.transport_bot import BotApiRejected
from runtime.tuning import get as _tune

MODEL = "grok-imagine-image-2.0"
API_BASE = "https://api.x.ai/v1/images/"
USAGE = "文生图：/生图 提示词\n图生图：回复一张图片，发送 /生图 修改要求\n每次生成一张；可回复生成结果继续修改。"
COMMAND = re.compile(r"^/生图(?=$|\s|[：:])\s*[：:]?\s*(.*)$", re.S)
MAX_RESULT_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 15 * 1024 * 1024


class ImagegenError(ValueError):
    """可展示的固定错误；永不包含请求头、原始服务响应或原图地址。"""


def command_payload(text):
    match = COMMAND.fullmatch(_normalize_command_prefix(text))
    return match[1].strip() if match else None


def in_scope(cid):
    return cid == PRIMARY_CID


def enabled(cid):
    return in_scope(cid) and _feat_on(cid, "imagegen")


def image_base_url():
    value = os.environ.get("DZMM_IMAGEGEN_PUBLIC_BASE", "https://www.nuonuo.pw/api/gallery/generated").rstrip("/")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ImagegenError("生图图片服务地址尚未配置好，请联系管理员。")
    return value


class XaiClient:
    def __init__(self, key=None):
        self.key = (os.environ.get("XAI_API_KEY", "") if key is None else key).strip()

    async def generate(self, prompt, source=None, *, timeout=150):
        if not self.key:
            raise ImagegenError("生图服务尚未配置，请联系管理员。")
        payload = {"model": MODEL, "prompt": prompt, "n": 1, "response_format": "b64_json"}
        path = "generations"
        if source is not None:
            path = "edits"
            data, mime = source
            payload["image"] = {"type": "image_url", "url": "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")}
        else:
            payload["aspect_ratio"] = "1:1"
        try:
            async with aiohttp.ClientSession(trust_env=True,
                    timeout=aiohttp.ClientTimeout(total=timeout, connect=15)) as session:
                async with session.post(API_BASE + path, json=payload,
                        headers={"Authorization": "Bearer " + self.key}, allow_redirects=False) as response:
                    if response.status != 200:
                        hint = (await response.content.read(4096)).decode("utf-8", errors="replace").lower()
                        error = {401: "生图服务认证失败，请联系管理员。",
                                 403: "生图服务暂无访问权限，请联系管理员。",
                                 402: "生图服务余额不足，请联系管理员。",
                                 429: "生图服务请求过多，请稍后再试。",
                                 400: "生图请求被服务拒绝，请调整提示词或原图后再试。"}.get(
                                     response.status, "生图服务暂时不可用，请稍后再试。")
                        if "incorrect api key" in hint or "invalid api key" in hint:
                            error = "生图服务认证失败，请联系管理员。"
                        elif (("insufficient" in hint and ("credit" in hint or "balance" in hint))
                              or "doesn't have any credits" in hint):
                            error = "生图服务余额不足，请联系管理员。"
                        raise ImagegenError(error)
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise ImagegenError("生成结果过大，本次未发送。")
            result = json.loads(raw)
            encoded = result["data"][0]["b64_json"]
            if not isinstance(encoded, str) or len(encoded) > MAX_RESPONSE_BYTES:
                raise ValueError("invalid image")
            data = base64.b64decode(encoded, validate=True)
            if len(data) > MAX_RESULT_BYTES:
                raise ImagegenError("生成结果过大，本次未发送。")
            return data
        except aiohttp.ConnectionTimeoutError as exc:
            raise ImagegenError("暂时无法连接生图服务，请稍后再试。") from exc
        except asyncio.TimeoutError as exc:
            raise ImagegenError("生图等待超时，结果尚不确定，本次不会自动重复生成。") from exc
        except aiohttp.ClientError as exc:
            raise ImagegenError("生图连接中断，结果尚不确定，本次不会自动重复生成。") from exc
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error) as exc:
            if isinstance(exc, ImagegenError):
                raise
            raise ImagegenError("生图服务没有返回有效图片，本次未发送。") from exc


class Service:
    def __init__(self, store=None, platform=None, client=None):
        self.store = store or Store()
        self.platform = platform or GalleryPlatform()
        self.client = client or XaiClient()
        self.tasks = {}
        self.processing = set()
        self.waiting = set()
        self.users = set()
        self.semaphore = asyncio.Semaphore(2)
        self.admission = asyncio.Lock()
        self.store.recover("image")

    async def original_command(self, cid, uid, prompt, message):
        """Always re-read the command: missing push reference must not turn an edit into generation."""
        mid = message.get("message_id")
        if not mid:
            raise ImagegenError("无法核验这条生图指令，请重新发送。")
        original = await self.platform.original(str(mid), str(message.get("sent_at") or ""))
        content = original.get("content") or {}
        if (original.get("message_id") != mid or original.get("sent_by") != uid
                or original.get("chatroom_id") not in (None, cid)
                or command_payload(content.get("text")) != prompt):
            raise ImagegenError("无法核验这条生图指令，请重新发送。")
        self.store.save_reference(cid, mid, {"id": mid, "sentBy": uid,
            "content": {"type": "text", "text": content.get("text") or ""}})
        return original

    async def source(self, cid, uid, prompt, message):
        original = await self.original_command(cid, uid, prompt, message)
        content = original.get("content") or {}
        quoted = content.get("reference") or content.get("quote") or content.get("reply")
        if not quoted:
            # A push reference absent from history is an uncertain edit, not text-to-image.
            pushed = message.get("content") or {}
            if pushed.get("reference") or pushed.get("quote") or pushed.get("reply"):
                raise ImagegenError("暂时无法核验引用图片，请稍后重新回复原图。")
            return None
        if not isinstance(quoted, dict) or not (quoted.get("id") or quoted.get("message_id")):
            raise ImagegenError("引用信息不完整，请重新回复原图。")
        photo_id = str(quoted.get("id") or quoted.get("message_id"))
        photo = await self.platform.original(photo_id, str(quoted.get("sent_at") or quoted.get("sentAt") or ""))
        if photo.get("message_id") != photo_id or photo.get("chatroom_id") not in (None, cid):
            raise ImagegenError("无法核验引用图片，请重新回复本群原图。")
        image = photo.get("content") or {}
        if str(image.get("type") or "").lower() != "image":
            raise ImagegenError("请回复一张图片，再发送 /生图 修改要求；文字和视频不能作为原图。")
        url = image.get("url") or image.get("text") or image.get("imageUrl") or image.get("image_url")
        if not isinstance(url, str):
            raise ImagegenError("无法读取引用图片，请重新发送原图。")
        # Our own generated URLs are resolved locally; no authenticated HTTP request or public gallery insertion.
        prefix = image_base_url() + "/"
        own = self.store.image(url[len(prefix):]) if url.startswith(prefix) else None
        data = own["data"] if own else await self.platform.download(url)
        _, _, mime, _ = await asyncio.to_thread(inspect_image, data)
        if mime == "image/gif":
            raise ImagegenError("图生图请使用 JPG、PNG 或 WebP 静态图片。")
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
            if len(prompt) > int(_tune("imagegen.input_limit", cid)):
                raise ImagegenError("提示词最多 2000 字，请缩短后再试。")
            mid = str(message.get("message_id") or "")
            if not mid or message.get("sent_by") != uid or message.get("chatroom_id") not in (None, cid):
                raise ImagegenError("无法核验这条生图指令，请重新发送。")
            async with self.admission:
                if (cid, mid) in self.tasks:
                    return
                previous = self.store.get(cid, mid)
                if previous and previous["uid"] != uid:
                    raise ImagegenError("生图指令身份不一致，未执行。")
                if previous and previous["status"] not in ("ready", "send_failed"):
                    return
                if uid in self.users:
                    raise ImagegenError("你上一张图还在处理中，请等完成后再试。")
                if len(self.tasks) >= 7:
                    raise ImagegenError("生图队列已满，请稍后再试。")
                # 小小糯原生引用不可用时，结果仍可由官方 Bot 发出；不要在
                # 调用 xAI 前提前拒绝，否则兜底永远没有机会执行。
                account_ready = bool(getattr(sender, "can_send_photo_reply", lambda _cid: False)(cid))
                bot_ready = callable(getattr(sender, "bot_for", None)) and sender.bot_for(cid) is not None
                if not account_ready and not bot_ready:
                    raise ImagegenError("本群图片发送通道尚未就绪，请联系管理员。")
                if not dry and not previous and not self.client.key:
                    raise ImagegenError("生图服务尚未配置，请联系管理员。")
                image_base_url()
                if dry:
                    await self.platform.member(uid)
                    if not previous:
                        await self.source(cid, uid, prompt, message)
                    return
                if not previous:
                    result = self.store.reserve(cid, mid, uid, "pending",
                                                int(_tune("imagegen.cooldown", cid)))
                    if result == "duplicate":
                        return
                    if result == "cooldown":
                        raise ImagegenError("生图提交有点频繁，稍等一分钟再来吧。")
                self.users.add(uid)
                self.waiting.add((cid, mid))
                task = asyncio.create_task(self.run(sender, cid, mid, uid, prompt, message, previous))
                self.tasks[(cid, mid)] = task
        except (ImagegenError, GalleryError, GalleryUnavailable, ModerationUnavailable) as exc:
            # Platform messages are fixed application errors, not raw API responses.
            await self.notice(sender, cid, "不过，" + str(exc), dry, name=name)
        except Exception as exc:
            log("imagegen: 提交失败 " + type(exc).__name__)
            await self.notice(sender, cid, "不过，这次生图没有提交成功，请稍后再试。", dry, name=name)

    @staticmethod
    def display_name(uid):
        return str(get_full_name(uid)).replace("\n", " ")[:60] or "群友"

    async def notice(self, sender, cid, text, dry=False, *, name=None):
        if not dry:
            try:
                prefix = (str(name).replace("\n", " ")[:60] if name else "小伙伴")
                await sender.send(prefix + "，小小糯收到啦！" + text, cid)
            except Exception as exc:
                log("imagegen: 提示发送未确认 " + type(exc).__name__)

    def queue_ahead(self, key):
        ahead = []
        for candidate in self.tasks:
            if candidate == key:
                break
            ahead.append(candidate)
        return (sum(candidate in self.waiting for candidate in ahead),
                sum(candidate in self.processing for candidate in ahead))

    @asynccontextmanager
    async def slot(self, key):
        async with self.semaphore:
            self.waiting.discard(key)
            self.processing.add(key)
            try:
                yield
            finally:
                self.processing.discard(key)

    async def queue_notice(self, sender, cid, mid, uid, previous):
        waiting, working = self.queue_ahead((cid, mid))
        name = self.display_name(uid)
        await self.notice(sender, cid, "正在努力想象中~前面有 " + str(waiting)
                          + " 张图排队，另有 " + str(working) + " 张图正在处理。"
                          + ("这次会把已经生成的图片发给你。" if previous else ""), name=name)

    async def run(self, sender, cid, mid, uid, prompt, message, previous):
        name = "群友"
        announcement = None
        try:
            if not enabled(cid):
                self.store.status(cid, mid, "cancelled")
                return
            # Acquire FIFO slots before any network wait. Queue notices run separately
            # so waiting users see their position while both generation slots are busy.
            announcement = asyncio.create_task(self.queue_notice(sender, cid, mid, uid, previous))
            async with self.slot((cid, mid)):
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
                    self.store.mode(cid, mid, "edit" if source else "generate")
                    self.store.status(cid, mid, "generating")
                    data = await self.client.generate(prompt, source, timeout=int(_tune("imagegen.timeout", cid)))
                    _, _, mime, _ = await asyncio.to_thread(inspect_image, data)
                    token = self.store.save_image(cid, mid, data, mime)
                if not enabled(cid):
                    return  # Keep ready bytes, do not publish after the switch is disabled.
                await self.platform.member(uid)
                if not enabled(cid):
                    return
                await self.deliver(sender, cid, mid, image_base_url() + "/" + token,
                                   str(name).replace("\n", " ")[:60] + " · " + ("图生图" if (previous or {}).get("mode") == "edit" or source else "文生图"),
                                   self.store.reference(cid, mid))
        except asyncio.CancelledError:
            row = self.store.get(cid, mid)
            if row and row["status"] in ("queued", "generating", "sending"):
                self.store.status(cid, mid, "delivery_unknown" if row["status"] == "sending" else "interrupted")
            raise
        except (ImagegenError, GalleryError, GalleryUnavailable, ModerationUnavailable) as exc:
            row = self.store.get(cid, mid)
            if row and row["status"] not in ("ready", "send_failed", "delivery_unknown"):
                self.store.status(cid, mid, "failed")
                await self.notice(sender, cid, "不过，" + str(exc), name=str(name)[:60])
        except Exception as exc:
            log("imagegen: 任务失败 " + type(exc).__name__)
            row = self.store.get(cid, mid)
            if row and row["status"] not in ("ready", "send_failed", "delivery_unknown", "sent"):
                self.store.status(cid, mid, "delivery_unknown" if row["status"] == "sending" else "failed")
            await self.notice(sender, cid, "不过，本次图片未能确认发送，结果已记录，不会自动重复生成。", name=str(name)[:60])
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
            raise ImagegenError("无法核验最初的生图指令，图片已保存，本次未发送。")
        self.store.status(cid, mid, "sending")
        try:
            sent_id = await sender.send_photo_reply_with_id(url, cid, reference, caption=caption)
            if not sent_id:
                raise BotApiRejected("发送通道当前不可用")
        except BotApiRejected:
            try:
                sent_id = await sender.send_photo_with_id(url, cid, caption=caption)
                if not sent_id:
                    raise RuntimeError("官方 Bot 缺少图片消息编号")
            except BotApiRejected:
                self.store.status(cid, mid, "send_failed")
                raise ImagegenError("图片已生成并保存，但账号和官方 Bot 都发送失败；无需重新生成。")
            except Exception:
                self.store.status(cid, mid, "delivery_unknown")
                raise ImagegenError("图片已生成并保存，官方 Bot 发送结果待核对；为避免重复发送，不再重试。")
        except Exception:
            self.store.status(cid, mid, "delivery_unknown")
            raise ImagegenError("图片已生成并保存，账号发送结果待核对；为避免重复发送，未切换通道。")
        self.store.status(cid, mid, "sent", sent_id)
        from runtime.generated_media import send_result_reply
        await send_result_reply(sender, self.store, cid, mid, reference, "图片")

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.store.recover("image")
        self.tasks.clear()
        self.processing.clear()
        self.waiting.clear()
        self.users.clear()


_service = None


async def handle(sender, cid, uid, text, message, *, dry=False):
    global _service
    if not enabled(cid):
        return
    if _service is None:
        _service = Service()
    await _service.handle(sender, cid, uid, text, message, dry=dry)


async def shutdown():
    global _service
    if _service is not None:
        await _service.close()
        _service = None
