"""生成媒体的平台上传适配和可见的原指令引用回执。"""
import asyncio
import os
from urllib.parse import urlsplit

import requests

import dzmm
from runtime.imagegen_store import Store
from runtime.transport_bot import BotApiRejected


async def official_photo_url(url, cid):
    prefix = os.environ.get("DZMM_IMAGEGEN_PUBLIC_BASE",
        "https://www.nuonuo.pw/api/gallery/generated").rstrip("/") + "/"
    if not isinstance(url, str) or not url.startswith(prefix):
        return url
    token = url[len(prefix):]

    def upload():
        store = Store()
        image = store.image(token)
        if image is None or image["mime"] not in ("image/jpeg", "image/png", "image/webp"):
            raise BotApiRejected("生成图片不存在，无法上传平台存储")
        cached = store.platform_url(token)
        if cached:
            return cached
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[image["mime"]]
        try:
            # 与平台网页 chatroom.uploadImage 的 multipart file 请求相同。
            # HTTP 上传失败尚未发群消息，属于可安全停止/重试的发送前失败。
            response = requests.post(dzmm.ORIGIN + "/api/trpc/chatroom.uploadImage",
                files={"file": ("generated" + extension, image["data"], image["mime"])},
                data={"chatroomId": cid},
                cookies=dzmm.get_cookies(), headers={"User-Agent": dzmm.BROWSER_UA,
                    "Origin": dzmm.ORIGIN, "Referer": dzmm.ORIGIN + "/"},
                proxies=dzmm.PROXIES, timeout=(10, 45), allow_redirects=False)
            if response.status_code != 200:
                raise BotApiRejected("平台图片上传被拒绝，图片已保留")
            body = response.json()
            result = body["result"]["data"]["json"]
            target = result["url"]
            parts = urlsplit(target)
            if (parts.scheme != "https" or parts.hostname != "rls.cheggpt.com"
                    or parts.port not in (None, 443) or parts.username or parts.password
                    or not parts.path.startswith("/storage/v1/object/public/") or parts.query or parts.fragment):
                raise ValueError("invalid storage URL")
        except BotApiRejected:
            raise
        except Exception as exc:
            # 不转发包含 Cookie、URL、存储响应或用户信息的底层错误。
            raise BotApiRejected("平台图片上传未完成，图片已保留") from exc
        store.save_platform_url(token, target)
        return target

    return await asyncio.to_thread(upload)


def video_content(result):
    """只发送网页视频消息的字段，不发送上传内部数据或自定义 URL 字段。"""
    for key in ("videoUrl", "thumbnailUrl"):
        target = result.get(key)
        if key == "thumbnailUrl" and not target:
            continue
        parts = urlsplit(target or "")
        if (parts.scheme != "https" or parts.hostname != "rls.cheggpt.com"
                or parts.port not in (None, 443) or parts.username or parts.password
                or not parts.path.startswith("/storage/v1/object/public/") or parts.query or parts.fragment):
            raise ValueError("invalid video storage URL")
    content = {"type": "video", "status": "ready", "videoUrl": result["videoUrl"]}
    for key in ("thumbnailUrl", "blurhash", "duration", "width", "height"):
        if result.get(key) is not None:
            content[key] = result[key]
    return content


async def platform_video_content(url, cid):
    prefix = os.environ.get("DZMM_IMAGEGEN_PUBLIC_BASE",
        "https://www.nuonuo.pw/api/gallery/generated").rstrip("/") + "/"
    if not isinstance(url, str) or not url.startswith(prefix):
        return {"type": "video", "status": "ready", "videoUrl": url}
    token = url[len(prefix):]

    def upload():
        store = Store()
        cached = store.platform_media(token)
        if cached:
            return video_content(cached)
        media = store.media(token)
        if media is None or media["mime"] != "video/mp4":
            raise BotApiRejected("生成视频不存在，无法上传平台存储")
        try:
            response = requests.post(dzmm.ORIGIN + "/api/trpc/media.uploadVideo",
                files={"file": ("generated.mp4", media["data"], media["mime"])},
                cookies=dzmm.get_cookies(), headers={"User-Agent": dzmm.BROWSER_UA,
                    "Origin": dzmm.ORIGIN, "Referer": dzmm.ORIGIN + "/"},
                proxies=dzmm.PROXIES, timeout=(10, 180), allow_redirects=False)
            if response.status_code != 200:
                raise BotApiRejected("平台视频上传被拒绝，视频已保留")
            content = video_content(response.json()["result"]["data"]["json"])
        except BotApiRejected:
            raise
        except Exception as exc:
            raise BotApiRejected("平台视频上传未完成，视频已保留") from exc
        store.save_platform_media(token, content)
        return content

    return await asyncio.to_thread(upload)


async def send_result_reply(sender, store, cid, mid, reference, kind):
    """图片/视频组件不显示 reference；用文字原生引用提供可见的任务对应关系。

    媒体已经投递成功，回执失败不得触发媒体重发；模糊失败也不重发回执。
    """
    from runtime.reply_delivery import ReplyNotSent
    from runtime.obs import log
    if not reference or reference.get("id") != mid or not store.claim_result_reply(cid, mid):
        return
    try:
        from runtime.identity import get_full_name
        name = str(get_full_name(reference["sentBy"])).replace("\n", " ")[:60] or "小伙伴"
        reply_id = await sender.send_reply_with_id(name + "，小小糯已经把" + kind + "发好啦！", cid, reference)
        if not reply_id:
            raise RuntimeError("缺少结果回执编号")
    except (ReplyNotSent, BotApiRejected):
        store.result_reply(cid, mid, "failed")
        log("generated_media: 引用回执未发送，媒体不重发")
    except Exception:
        store.result_reply(cid, mid, "unknown")
        log("generated_media: 引用回执未确认，媒体和回执均不重发")
    else:
        store.result_reply(cid, mid, "sent", reply_id)
