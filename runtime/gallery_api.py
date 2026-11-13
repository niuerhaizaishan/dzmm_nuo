"""图集公开阅读；个人收藏需玩家身份，上传、改名与删除需当前妹子群成员。"""
import asyncio
import os
import re
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from uuid import UUID

from aiohttp import web

from runtime.gallery import ALL_CATEGORIES, CATEGORIES, PRIVATE_CATEGORY, GalleryError, GalleryConflict, GalleryUnavailable, GalleryPlatform, MAX_BYTES, MAX_VIDEO_BYTES, Store, download_name, picture_title, inspect_media, video_frame
from runtime.imagegen_store import Store as ImagegenStore

UID_KEY = web.RequestKey("gallery_uid", str)


def make_thumbnail(data, mime="image/png"):
    from PIL import Image, ImageOps
    with (video_frame(data) if mime.startswith("video/") else Image.open(BytesIO(data))) as original:
        image = ImageOps.exif_transpose(original)
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        output = BytesIO()
        image.save(output, format="WEBP", quality=80, method=4)
        return output.getvalue()


class PlayerAuth:
    def __init__(self, audience):
        import jwt
        # 平台存在数字 ID 与 UUID；只接受配置的一种精确 audience，不猜测或跨游戏兼容。
        audience = str(audience).strip()
        numeric = audience.isascii() and audience.isdecimal() and int(audience) > 0
        try:
            uuid_id = str(UUID(audience)) == audience
        except ValueError:
            uuid_id = False
        if not numeric and not uuid_id:
            raise ValueError("DZMM_GALLERY_GAME_ID must be this game's numeric ID or canonical UUID")
        self.audience = audience
        self.jwt = jwt
        self.jwks = jwt.PyJWKClient("https://www.dzmm.ai/api/gamefy/.well-known/jwks.json",
            timeout=5, lifespan=3600,
            headers={"User-Agent": "DZMM-Gallery/1.0", "Accept": "application/json"})

    async def verify(self, token):
        def decode():
            try:
                key = self.jwks.get_signing_key_from_jwt(token)
                claims = self.jwt.decode(token, key.key, algorithms=["RS256"],
                    issuer="dzmm-gamefy", audience=self.audience,
                    options={"require": ["exp", "iat", "sub", "iss", "aud"], "strict_aud": True})
                return str(UUID(claims["sub"]))
            except self.jwt.InvalidAudienceError as exc:
                # 仅记录已验签的游戏标识，方便部署核对；绝不自动接受其它游戏或记录令牌。
                from runtime.obs import log
                claims = self.jwt.decode(token, key.key, algorithms=["RS256"],
                    issuer="dzmm-gamefy", options={"verify_aud": False,
                    "require": ["exp", "iat", "sub", "iss", "aud"]})
                audience = claims.get("aud")
                if isinstance(audience, str) and len(audience) <= 64:
                    try:
                        safe_id = str(UUID(audience))
                    except ValueError:
                        safe_id = audience if audience.isascii() and audience.isdecimal() else "invalid-format"
                    log("gallery_api: signed game audience mismatch expected=" + self.audience + " actual=" + safe_id)
                raise web.HTTPUnauthorized(text="图集的游戏身份配置待核对，请稍后再试。") from exc
            except (self.jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
                from runtime.obs import log
                log("gallery_api: identity rejected " + type(exc).__name__)
                raise web.HTTPUnauthorized(text="请在 DZMM 中重新登录后打开图集。") from exc
        return await asyncio.to_thread(decode)


def create_app(*, store=None, platform=None, auth=None, origins=(), imagegen_store=None):
    store = store or Store()
    platform = platform or GalleryPlatform()
    auth = auth or PlayerAuth(os.environ.get("DZMM_GALLERY_GAME_ID", ""))
    allowed = frozenset(origins)
    generated_store = imagegen_store or ImagegenStore()

    @web.middleware
    async def access(request, handler):
        origin = request.headers.get("Origin")
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        if origin and request.path.startswith("/api/gallery/"):
            if origin not in allowed:
                return web.Response(status=403, text="来源未获允许。", headers=headers)
            headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, Range",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS"})
        try:
            if request.method == "OPTIONS":
                return web.Response(status=204, headers=headers)
            public_read = request.method in ("GET", "HEAD") and (
                request.path == "/api/gallery/items" or
                request.path == "/api/gallery/download" or
                re.fullmatch(r"/api/gallery/downloads/\d+", request.path) or
                re.fullmatch(r"/api/gallery/images/\d+", request.path) or
                re.fullmatch(r"/api/gallery/generated/[a-f0-9]{64}", request.path))
            private_read = request.method in ("GET", "HEAD") and (
                request.path == "/api/gallery/private-items" or
                re.fullmatch(r"/api/gallery/private-images/\d+", request.path))
            # Viewing stays public; explicit attachment downloads require a current member,
            # including HEAD, byte ranges and old links published before scoped tickets.
            member_download = request.method in ("GET", "HEAD") and request.query.get("download") == "1" and bool(
                re.fullmatch(r"/api/gallery/(?:images|private-images)/\d+", request.path))
            if member_download:
                public_read = False
            if request.path.startswith("/api/gallery/") and not public_read:
                bearer = request.headers.get("Authorization", "")
                if not bearer.startswith("Bearer ") or len(bearer) > 8192:
                    raise web.HTTPUnauthorized(text="请从 DZMM 登录后打开图集。")
                request[UID_KEY] = await auth.verify(bearer[7:])
                if member_download or private_read or (request.method not in ("GET", "HEAD") and not request.path.endswith("/favorite")):
                    await platform.member(request[UID_KEY])
            response = await handler(request)
        except GalleryUnavailable as exc:
            response = web.Response(status=503, text=str(exc))
        except GalleryConflict as exc:
            response = web.Response(status=409, text=str(exc))
        except GalleryError as exc:
            response = web.Response(status=403, text=str(exc))
        except web.HTTPException as exc:
            response = web.Response(status=exc.status, body=exc.body, headers=exc.headers)
        except Exception as exc:
            from runtime.obs import log
            log("gallery_api: 请求失败 " + type(exc).__name__)
            response = web.Response(status=503, text="图集暂时不可用，请稍后再试。")
        response.headers.update(headers)
        return response

    app = web.Application(middlewares=[access], client_max_size=100 * 1024 * 1024 + 65536)
    # Derived previews stay in bounded memory; the stored original is never changed.
    thumbnails = OrderedDict()
    thumbnail_bytes = 0
    thumbnail_workers = asyncio.Semaphore(2)

    async def listing(request, *, private=False):
        rows = await asyncio.to_thread(store.list, "", only_private=private)
        # 公开图集不公布上传者 ID/昵称、来源消息或个人收藏状态。
        items = [{key: row[key] for key in ("id", "category", "title", "mime", "width", "height", "caption", "created", "saves")}
                 | {"filename": download_name(row["title"], row["mime"])} for row in rows]
        return web.json_response({"items": items, "categories": ([PRIVATE_CATEGORY] if private else CATEGORIES)})

    async def private_listing(request):
        return await listing(request, private=True)

    async def session(request):
        rows = await asyncio.to_thread(store.list, request[UID_KEY])
        can_view_private = False
        try:
            await platform.member(request[UID_KEY])
            can_edit = True
            can_view_private = True
        except GalleryError:
            can_edit = False
        if can_view_private:
            rows = await asyncio.to_thread(store.list, request[UID_KEY], include_private=True)
        return web.json_response({"can_edit": can_edit,
            "can_view_private": can_view_private,
            "mine": [row["id"] for row in rows if row["uploader"] == request[UID_KEY]],
            "saved": [row["id"] for row in rows if row["saved"]]})

    async def image(request, *, private=False):
        nonlocal thumbnail_bytes
        size = request.query.get("size", "original")
        if size not in ("original", "thumb"):
            raise web.HTTPBadRequest(text="图片尺寸参数无效。")
        try:
            image_id = int(request.match_info["image_id"])
        except ValueError as exc:
            raise web.HTTPNotFound() from exc
        row = await asyncio.to_thread(store.image, image_id, include_private=private)
        if row is None:
            raise web.HTTPNotFound()
        if size == "thumb":
            async with thumbnail_workers:
                if image_id not in thumbnails:
                    data = await asyncio.to_thread(make_thumbnail, row["data"], row["mime"])
                    thumbnails[image_id] = data
                    thumbnail_bytes += len(data)
                    while thumbnail_bytes > 32 * 1024 * 1024:
                        _, evicted = thumbnails.popitem(last=False)
                        thumbnail_bytes -= len(evicted)
                data = thumbnails[image_id]
                thumbnails.move_to_end(image_id)
            return web.Response(body=data, content_type="image/webp")
        from urllib.parse import quote
        data = row["data"]
        disposition = "attachment" if request.query.get("download") == "1" else "inline"
        headers = {
            "Content-Disposition": disposition + "; filename*=UTF-8''" + quote(download_name(row["title"], row["mime"])),
            "Accept-Ranges": "bytes",
            "Access-Control-Expose-Headers": "Content-Disposition, Content-Range, Accept-Ranges"}
        status = 200
        # 成员及软删除核验在 Range 处理和封面缓存之前完成。
        if request.headers.get("Range"):
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers["Range"])
            if not match or not any(match.groups()):
                raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{len(data)}"})
            first, last = match.groups()
            start = int(first) if first else max(0, len(data) - int(last))
            end = min(int(last), len(data) - 1) if first and last else len(data) - 1
            if start >= len(data) or start > end:
                raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{len(data)}"})
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            data, status = data[start:end + 1], 206
        return web.Response(body=data, status=status, content_type=row["mime"], headers=headers)

    async def private_image(request):
        return await image(request, private=True)

    async def rename(request):
        try:
            payload = await request.json()
            image_id = int(request.match_info["image_id"])
            title = picture_title(payload["title"])
        except (ValueError, KeyError, TypeError) as exc:
            raise web.HTTPBadRequest(text="图片名称无效，最多 80 字。") from exc
        title = await asyncio.to_thread(store.rename, image_id, title)
        return web.json_response({"title": title})

    async def favorite(request):
        try:
            payload = await request.json()
            image_id = int(request.match_info["image_id"])
            saved = payload["saved"]
            if type(saved) is not bool:
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise web.HTTPBadRequest(text="收藏参数无效。") from exc
        if not await asyncio.to_thread(store.exists, image_id):
            # 收藏不要求成员身份，但私密图片只对成员存在；对其他人一律按不存在处理，不暴露编号。
            await platform.member(request[UID_KEY])
            if not await asyncio.to_thread(store.exists, image_id, include_private=True):
                raise web.HTTPNotFound()
        await asyncio.to_thread(store.favorite, request[UID_KEY], image_id, saved)
        return web.json_response({"saved": saved})

    async def move(request):
        try:
            payload = await request.json()
            image_id = int(request.match_info["image_id"])
            category = payload["category"]
            if image_id <= 0 or image_id > 9223372036854775807 or category not in ALL_CATEGORIES:
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise web.HTTPBadRequest(text="请选择有效图片编号和目标图集。") from exc
        result = await asyncio.to_thread(store.move, image_id, category)
        return web.json_response(result)

    async def remove(request):
        try:
            image_id = int(request.match_info["image_id"])
            if image_id <= 0 or image_id > 9223372036854775807:
                raise ValueError
        except ValueError as exc:
            raise web.HTTPBadRequest(text="图片编号无效。") from exc
        result = await asyncio.to_thread(store.set_deleted, image_id, True, request[UID_KEY])
        return web.json_response(result)

    async def upload(request):
        if request.content_type != "multipart/form-data":
            raise web.HTTPBadRequest(text="请选择图片或视频后再上传。")
        reader = await request.multipart()
        category, caption, title, files = "", "", "", []
        total = 0
        async for field in reader:
            if field.name in ("category", "caption", "title"):
                data = bytearray()
                while chunk := await field.read_chunk(4096):
                    data.extend(chunk)
                    if len(data) > 1024:
                        raise web.HTTPBadRequest(text="配文或分区名称过长。")
                try:
                    value = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise web.HTTPBadRequest(text="配文或分区名称编码无效。") from exc
                if field.name == "category":
                    category = value
                elif field.name == "caption":
                    caption = value
                else:
                    title = value
            elif field.name == "images" and field.filename:
                if len(files) >= 30:
                    raise web.HTTPBadRequest(text="一次最多上传 30 个文件。")
                data = bytearray()
                while chunk := await field.read_chunk(65536):
                    data.extend(chunk)
                    total += len(chunk)
                    if len(data) > MAX_VIDEO_BYTES or total > 100 * 1024 * 1024:
                        raise web.HTTPRequestEntityTooLarge(max_size=MAX_VIDEO_BYTES, actual_size=len(data))
                files.append((field.filename, bytes(data), field.headers.get("Content-Type", "")))
            else:
                raise web.HTTPBadRequest(text="上传字段无效。")
        if category not in ALL_CATEGORIES or not files or len(caption) > 160:
            raise web.HTTPBadRequest(text="请选择图集和图片或视频，配文最多 160 字。")
        title = picture_title(title, "")
        for filename, data, content_type in files:
            async with thumbnail_workers:
                await asyncio.to_thread(inspect_media, data, filename, content_type)
        name = await platform.member(request[UID_KEY])
        ids = []
        for filename, data, content_type in files:
            async with thumbnail_workers:
                image_id, _ = await asyncio.to_thread(store.save, category, data,
                    uploader=request[UID_KEY], name=name, filename=filename, caption=caption, content_type=content_type,
                    title=(title if len(files) == 1 else f"{title[:76]} {len(ids)+1}") if title else "")
            ids.append(image_id)
        return web.json_response({"ids": ids}, status=201)

    root = Path(__file__).resolve().parent.parent / "gamefy-comic" / "publish"
    # 生产镜像只跑 API 时不一定带前端目录；缺了就不挂静态路由，别让进程起不来。
    has_publish = root.is_dir()

    async def index(request):
        return web.Response(text=(root / "index.html").read_text(encoding="utf-8"), content_type="text/html")

    async def config(request):
        return web.Response(text='window.GALLERY_API_BASE="/api/gallery";',
                            content_type="application/javascript")

    async def generated_image(request):
        image = await asyncio.to_thread(generated_store.image, request.match_info["token"])
        if image is None:
            raise web.HTTPNotFound(text="图片不存在。")
        data, status = image["data"], 200
        headers = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
                   "Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff"}
        # 视频播放器/平台拉取可按字节读取；URL 不变，原始数据不重复存储。
        if request.headers.get("Range"):
            match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", request.headers["Range"])
            if not match or not any(match.groups()):
                raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{len(data)}"})
            first, last = match.groups()
            start = int(first) if first else max(0, len(data) - int(last))
            end = min(int(last), len(data) - 1) if first and last else len(data) - 1
            if start >= len(data) or start > end:
                raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{len(data)}"})
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            data, status = data[start:end + 1], 206
        return web.Response(body=data, status=status, content_type=image["mime"], headers=headers)

    app.router.add_get("/api/gallery/generated/{token:[a-f0-9]{64}}", generated_image)
    app.router.add_get("/api/gallery/items", listing)
    app.router.add_get("/api/gallery/private-items", private_listing)
    app.router.add_get("/api/gallery/me", session)
    app.router.add_get("/api/gallery/images/{image_id}", image)
    app.router.add_get("/api/gallery/private-images/{image_id}", private_image)
    app.router.add_delete("/api/gallery/images/{image_id}", remove)
    app.router.add_put("/api/gallery/images/{image_id}/favorite", favorite)
    app.router.add_put("/api/gallery/images/{image_id}/title", rename)
    app.router.add_put("/api/gallery/images/{image_id}/category", move)
    app.router.add_post("/api/gallery/upload", upload)
    from runtime.gallery_download import add_download_routes
    add_download_routes(app, store=store, platform=platform, uid_key=UID_KEY, image_handler=image)
    if has_publish:
        app.router.add_get("/", index)
        app.router.add_get("/config.js", config)
        app.router.add_static("/", root, show_index=False)
    return app


if __name__ == "__main__":
    origins = [x.strip() for x in os.environ.get("DZMM_GALLERY_ORIGINS", "").split(",") if x.strip()]
    web.run_app(create_app(origins=origins), host="127.0.0.1",
                port=int(os.environ.get("DZMM_GALLERY_PORT", "8775")), access_log=None)
