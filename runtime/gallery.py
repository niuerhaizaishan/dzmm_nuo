"""妹子认证群图集：原消息核验、当前成员权限和原图持久保存。"""
import asyncio
from contextlib import closing
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
import warnings

import requests

import dzmm
from runtime.ctx import PRIMARY_CID
from runtime.moderation_platform import Platform, ModerationUnavailable
from runtime.obs import log

CARD_ID = "3799187"
CARD_URL = "https://www.dzmm.ai/character/" + CARD_ID
CATEGORIES = ("个人立绘", "姐妹日常", "高清美图", "大开脑洞")
PRIVATE_CATEGORY = "私密相册"
ALL_CATEGORIES = CATEGORIES + (PRIVATE_CATEGORY,)
CATEGORY_ALIASES = {"脑洞大开": "大开脑洞"}
MAX_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_PIXELS = 40_000_000
COMMAND = re.compile(r"^/(漫画|图集|保存|删除图片|恢复图片|移动图片)(?=$|\s)(.*)$", re.S)
USAGE = "回复群里的图片或视频，发送 /保存 个人立绘 名称\n名称可不填。可选图集：" + "、".join(ALL_CATEGORIES)
MOVE_USAGE = "移动：/移动图片 图片编号 目标图集\n例如：/移动图片 123 姐妹日常\n可选图集：" + "、".join(ALL_CATEGORIES)
MANAGE_USAGE = "删除：/删除图片 图片编号\n误删恢复：/恢复图片 图片编号\n" + MOVE_USAGE + "\n编号可在图集大图或保存回复中查看，仅妹子群成员可操作。"
CARD_INTRO = "姐妹图集 · 好图一起存\n/漫画 或 /图集 均可打开。公开图集所有人可查看，下载仅限妹子群成员；私密相册仅妹子群成员可查看和操作。\n" + USAGE + "\n" + MANAGE_USAGE


class GalleryError(ValueError):
    """可以直接展示给用户的失败原因，不包含 URL、令牌或平台响应。"""


class GalleryConflict(GalleryError):
    """目标图集已有同一原图，不能覆盖或合并现有记录。"""


class GalleryUnavailable(RuntimeError):
    """平台暂时无法核验；与非成员拒绝分开，内容可安全展示。"""


def platform_unavailable(exc, stage):
    # dzmm.api 的 RuntimeError 可能携带响应正文，只匹配已知认证标记，绝不回显。
    auth_failed = any(marker in str(exc).lower() for marker in (
        "invalid authentication credentials", "unauthorized", "未授权"))
    log("gallery: 平台核验失败 stage=" + stage + " auth=" + str(auth_failed)
        + " exception=" + type(exc).__name__)
    if auth_failed:
        return GalleryUnavailable("机器人登录状态已失效，暂时无法核验群成员和原图；恢复登录后请重试。本次操作未执行。")
    return GalleryUnavailable("平台核验暂时不可用，请稍后重试。本次操作未执行。")


def command(text):
    match = COMMAND.fullmatch((text or "").strip())
    return ("漫画" if match[1] == "图集" else match[1], match[2].strip()) if match else None


def in_scope(cid):
    return cid == PRIMARY_CID


def store_path():
    return Path(os.environ.get("DZMM_GALLERY_DB") or
                Path(__file__).resolve().parent.parent / "data" / "gallery.db")


def picture_title(value, fallback="未命名图片"):
    if not isinstance(value, str) or len(value.strip()) > 80:
        raise GalleryError("图片名称最多 80 字。")
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    return value or fallback


def download_name(title, mime):
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", title).strip(" .")[:80] or "未命名图片"
    return stem + {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
                    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}[mime]


def inspect_media(data, filename="", content_type=""):
    if not data:
        raise GalleryError("文件为空，未保存。")
    ext = Path(filename.replace("\\", "/")).suffix.lower()
    video_mimes = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}
    hinted = str(content_type or "").split(";", 1)[0].lower()
    is_video = (hinted.startswith("video/") or ext in video_mimes
                or data[:4] == b"\x1a\x45\xdf\xa3" or data[4:8] == b"ftyp")
    if is_video:
        if len(data) > MAX_VIDEO_BYTES:
            raise GalleryError("视频超过 50 MB，未保存。")
        webm = data[:4] == b"\x1a\x45\xdf\xa3"
        if not webm and data[4:8] != b"ftyp":
            raise GalleryError("视频格式无法识别，请使用 MP4、WebM 或 MOV。")
        frame = video_frame(data)
        mime = "video/webm" if webm else ("video/quicktime" if data[8:12] == b"qt  " else "video/mp4")
        return frame.width, frame.height, mime, {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}[mime]
    return inspect_image(data)


def video_frame(data):
    """只从内存容器解码首帧；不接受引用外部媒体的容器或浏览器不兼容编码。"""
    import av
    try:
        with av.open(BytesIO(data), options={"protocol_whitelist": "pipe", "enable_drefs": "0"}) as container:
            if not set(container.format.name.split(",")) & {"mov", "mp4", "matroska", "webm"}:
                raise GalleryError("请使用 MP4、WebM 或 MOV 视频。")
            videos = list(container.streams.video)
            if not videos:
                raise GalleryError("文件没有视频画面，未保存。")
            stream = videos[0]
            webm = data[:4] == b"\x1a\x45\xdf\xa3"
            codecs = {"vp8", "vp9", "av1"} if webm else {"h264"}
            if stream.codec_context.name not in codecs:
                raise GalleryError("视频编码暂不支持，请转为 H.264 MP4 后上传。")
            if any(s.codec_context.name not in {"aac", "mp3", "opus", "vorbis"} for s in container.streams.audio):
                raise GalleryError("视频音轨暂不支持，请转为 H.264/AAC MP4 后上传。")
            width, height = stream.codec_context.width, stream.codec_context.height
            if min(width, height) <= 0 or width * height > 4096 * 2160:
                raise GalleryError("视频分辨率太大，请缩小至 4K 以内。")
            stream.codec_context.thread_count = 1
            for index, packet in enumerate(container.demux(stream)):
                if index >= 256:
                    break
                for frame in packet.decode():
                    return frame.to_image()
    except (av.FFmpegError, OSError, ValueError) as exc:
        if isinstance(exc, GalleryError):
            raise
        raise GalleryError("视频损坏或无法解码，未保存。") from exc
    raise GalleryError("视频没有可读取的画面，未保存。")


def inspect_image(data):
    from PIL import Image, UnidentifiedImageError
    if not data or len(data) > MAX_BYTES:
        raise GalleryError("图片为空或超过 10 MB，未保存。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                kind, (width, height) = image.format, image.size
                if kind not in ("JPEG", "PNG", "WEBP", "GIF"):
                    raise GalleryError("只支持 JPG、PNG、WebP、GIF 图片。")
                if width * height > MAX_PIXELS:
                    raise GalleryError("图片尺寸太大，请缩小后再存。")
                image.verify()
            # verify 对部分格式只检查结构；再解码确认文件没有截断。
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise GalleryError("图片损坏或尺寸太大，未保存。") from exc
    mime, extension = {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png"),
                       "WEBP": ("image/webp", ".webp"), "GIF": ("image/gif", ".gif")}[kind]
    return width, height, mime, extension


class Store:
    """原始字节与元信息同一事务；断电/重启不依赖群图片链接。"""
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else store_path()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gallery_images (
                id INTEGER PRIMARY KEY, category TEXT NOT NULL, digest TEXT NOT NULL,
                data BLOB NOT NULL, mime TEXT NOT NULL, filename TEXT NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL,
                uploader TEXT NOT NULL, name TEXT NOT NULL, caption TEXT NOT NULL,
                source_message_id TEXT NOT NULL, created REAL NOT NULL,
                UNIQUE(category, digest));
            CREATE TABLE IF NOT EXISTS gallery_favorites (
                uid TEXT NOT NULL, image_id INTEGER NOT NULL, PRIMARY KEY(uid,image_id));
        """)
        # 两个进程可能同时首次打开旧库，用写事务串行化增量迁移。
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if "title" not in {row[1] for row in conn.execute("PRAGMA table_info(gallery_images)")}:
                conn.execute("ALTER TABLE gallery_images ADD COLUMN title TEXT NOT NULL DEFAULT ''")
                rows = conn.execute("SELECT id,filename FROM gallery_images").fetchall()
                for row in rows:
                    conn.execute("UPDATE gallery_images SET title=? WHERE id=?",
                                 (picture_title(Path(row["filename"]).stem[:80]), row["id"]))
            columns = {row[1] for row in conn.execute("PRAGMA table_info(gallery_images)")}
            if "deleted_at" not in columns:
                conn.execute("ALTER TABLE gallery_images ADD COLUMN deleted_at REAL NOT NULL DEFAULT 0")
            if "deleted_by" not in columns:
                conn.execute("ALTER TABLE gallery_images ADD COLUMN deleted_by TEXT NOT NULL DEFAULT ''")
        return conn

    def save(self, category, data, *, uploader, name, filename="", caption="", source="", title="", content_type=""):
        if category not in ALL_CATEGORIES or not uploader:
            raise GalleryError("图集名称无效。\n" + USAGE)
        width, height, mime, extension = inspect_media(data, filename=filename, content_type=content_type)
        digest = hashlib.sha256(data).hexdigest()
        filename = Path(filename.replace("\\", "/")).name
        # 只保留显示用文件名，响应头不用用户输入，文件名不会变成磁盘路径。
        filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)[:160]
        if not filename:
            filename = "姐妹图集-" + digest[:12] + extension
        title = picture_title(title, Path(filename).stem[:80] or "未命名图片")
        with closing(self.connect()) as conn, conn:
            cursor = conn.execute("""INSERT OR IGNORE INTO gallery_images
                (category,digest,data,mime,filename,width,height,uploader,name,caption,
                 source_message_id,created,title) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (category, digest, data, mime, filename, width, height, uploader,
                 str(name)[:100], str(caption)[:160], source, time.time(), title))
            row = conn.execute("SELECT id,deleted_at FROM gallery_images WHERE category=? AND digest=?",
                               (category, digest)).fetchone()
            if row["deleted_at"]:
                raise GalleryError("这张图片已删除，如需恢复请发送 /恢复图片 " + str(row["id"]))
            return row["id"], bool(cursor.rowcount)

    def list(self, uid, *, include_private=False, only_private=False):
        # 私密相册只认成员身份：默认不含私密，only_private 只取私密，include_private 全取。
        if only_private:
            scope, extra = "category=?", (PRIVATE_CATEGORY,)
        elif include_private:
            scope, extra = "1", ()
        else:
            scope, extra = "category<>?", (PRIVATE_CATEGORY,)
        with closing(self.connect()) as conn:
            rows = conn.execute("""SELECT i.id,category,filename,width,height,uploader,name,title,mime,
                caption,created, (SELECT count(*) FROM gallery_favorites f WHERE f.image_id=i.id) saves,
                EXISTS(SELECT 1 FROM gallery_favorites f WHERE f.image_id=i.id AND f.uid=?) saved
                FROM gallery_images i WHERE deleted_at=0 AND """ + scope + """ ORDER BY i.id DESC""",
                (uid,) + extra).fetchall()
            return [dict(row) for row in rows]

    def image(self, image_id, *, include_private=False):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT data,mime,title,category FROM gallery_images WHERE id=? AND deleted_at=0 AND (? OR category<>?)",
                               (image_id, 1 if include_private else 0, PRIVATE_CATEGORY)).fetchone()
            return dict(row) if row else None

    def exists(self, image_id, *, include_private=False):
        """只查存在性，不读原图数据；私密图片默认视为不存在。"""
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT 1 FROM gallery_images WHERE id=? AND deleted_at=0 AND (? OR category<>?)",
                               (image_id, 1 if include_private else 0, PRIVATE_CATEGORY)).fetchone()
            return row is not None

    def rename(self, image_id, title):
        title = picture_title(title)
        with closing(self.connect()) as conn, conn:
            if not conn.execute("UPDATE gallery_images SET title=? WHERE id=? AND deleted_at=0", (title, image_id)).rowcount:
                raise GalleryError("图片不存在。")
        return title

    def favorite(self, uid, image_id, saved):
        with closing(self.connect()) as conn, conn:
            if not conn.execute("SELECT 1 FROM gallery_images WHERE id=? AND deleted_at=0", (image_id,)).fetchone():
                raise GalleryError("图片不存在。")
            if saved:
                conn.execute("INSERT OR IGNORE INTO gallery_favorites VALUES (?,?)", (uid, image_id))
            else:
                conn.execute("DELETE FROM gallery_favorites WHERE uid=? AND image_id=?", (uid, image_id))

    def move(self, image_id, category):
        if category not in ALL_CATEGORIES:
            raise GalleryError("目标图集无效。\n" + MOVE_USAGE)
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT title,category,digest FROM gallery_images WHERE id=? AND deleted_at=0",
                               (image_id,)).fetchone()
            if row is None:
                raise GalleryError("图片不存在或已删除，请核对图片编号。")
            changed = row["category"] != category
            if changed:
                duplicate = conn.execute("SELECT id,deleted_at FROM gallery_images WHERE category=? AND digest=?",
                                         (category, row["digest"])).fetchone()
                if duplicate is not None:
                    state = "有一张已删除的同图记录" if duplicate["deleted_at"] else "已有同一张图片"
                    raise GalleryConflict("「" + category + "」" + state + "（编号 " + str(duplicate["id"])
                                          + "），未移动，原图保留在「" + row["category"] + "」。")
                conn.execute("UPDATE gallery_images SET category=? WHERE id=?", (category, image_id))
            return {"id":image_id, "title":row["title"], "previous_category":row["category"],
                    "category":category, "changed":changed}

    def set_deleted(self, image_id, deleted, uid):
        """软删除保留原图、编号与收藏，恢复不产生副本；重复指令不会切换状态。"""
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT title,deleted_at FROM gallery_images WHERE id=?", (image_id,)).fetchone()
            if row is None:
                raise GalleryError("图片不存在，请核对图片编号。")
            changed = bool(row["deleted_at"]) != deleted
            if changed:
                conn.execute("UPDATE gallery_images SET deleted_at=?,deleted_by=? WHERE id=?",
                             (time.time() if deleted else 0, uid if deleted else "", image_id))
            return {"id": image_id, "title": row["title"], "deleted": deleted, "changed": changed}


class GalleryPlatform:
    async def member(self, uid):
        def load():
            result = dzmm.api("chatroom.getMembers", {"json": {
                "chatroomId": PRIMARY_CID, "limit": dzmm.MEMBER_FETCH_LIMIT}}, timeout=5)
            rows = result.get("members")
            if (not isinstance(rows, list) or not rows or len(rows) >= dzmm.MEMBER_FETCH_LIMIT
                    or result.get("hasMore") or result.get("nextCursor")
                    or any(not isinstance(row, dict) or not row.get("id") for row in rows)):
                raise GalleryError("暂时无法核验完整群成员名单，请稍后再试。")
            row = next((row for row in rows if row["id"] == uid and not row.get("isBot")), None)
            if row is None:
                raise GalleryError("姐妹图集仅限当前妹子群成员使用。")
            return row.get("fullName") or "群友"
        try:
            return await asyncio.to_thread(load)
        except (RuntimeError, requests.RequestException) as exc:
            raise platform_unavailable(exc, "member") from exc

    async def original(self, message_id, sent_at=""):
        if not sent_at:
            # 归档只用作时间索引，原图内容和地址仍以平台回查结果为准。
            from runtime.chat_archive import archive_path
            path = archive_path()
            if path.is_file():
                def locate():
                    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                        row = conn.execute("SELECT sent_at_original FROM messages WHERE group_id=? AND message_id=?",
                                           (PRIMARY_CID, message_id)).fetchone()
                        return row[0] if row else ""
                sent_at = await asyncio.to_thread(locate)
        try:
            return await Platform().message(PRIMARY_CID, message_id, sent_at)
        except ModerationUnavailable:
            raise
        except (RuntimeError, requests.RequestException) as exc:
            raise platform_unavailable(exc, "original") from exc

    async def download(self, url, *, video=False):
        def load():
            # Generated pictures are already durable local originals. Keep /保存 working
            # even when sendPhoto stores our URL without copying it to the platform CDN.
            generated_base = os.environ.get("DZMM_IMAGEGEN_PUBLIC_BASE",
                "https://www.nuonuo.pw/api/gallery/generated").rstrip("/") + "/"
            if isinstance(url, str) and url.startswith(generated_base):
                from runtime.imagegen_store import Store as ImagegenStore
                image = ImagegenStore().image(url[len(generated_base):])
                if image is None:
                    raise GalleryError("生成媒体不存在，请重新发送原文件后再保存。")
                if video and not str(image.get("mime") or "").startswith("video/"):
                    raise GalleryError("引用的生成文件不是视频。")
                if not video and not str(image.get("mime") or "").startswith("image/"):
                    raise GalleryError("引用的生成文件不是图片。")
                return image["data"]
            parts = urlsplit(url)
            # 只从已观察到的平台图片 CDN 取原图；不跟随跳转、不发送账号 Cookie。
            if (parts.scheme != "https" or parts.hostname != "rls.cheggpt.com"
                    or parts.username or parts.password or parts.port not in (None, 443)):
                raise GalleryError("这张图片的来源暂不支持，未保存。")
            with requests.get(url, stream=True, timeout=(5, 15), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise GalleryError("原图暂时下载不了，请重新发图后再试。")
                data = bytearray()
                started = time.monotonic()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    limit = MAX_VIDEO_BYTES if video else MAX_BYTES
                    if len(data) > limit or time.monotonic() - started > 60:
                        raise GalleryError("视频超过 50 MB 或下载超时，未保存。" if video else "图片超过 10 MB 或下载超时，未保存。")
                return bytes(data)
        return await asyncio.to_thread(load)


class Service:
    def __init__(self, store=None, platform=None):
        self.store = store or Store()
        self.platform = platform or GalleryPlatform()

    async def reply(self, cid, uid, text, message, *, dry=False):
        parsed = command(text)
        if parsed is None:
            return None
        if not in_scope(cid):
            return "姐妹图集仅限在妹子群内使用。"
        try:
            name = await self.platform.member(uid)
            action, argument = parsed
            if action == "漫画":
                return CARD_INTRO
            if action == "移动图片":
                parts = argument.split()
                if len(parts) == 2:
                    parts[1] = CATEGORY_ALIASES.get(parts[1], parts[1])
                if len(parts) != 2 or not re.fullmatch(r"[1-9][0-9]{0,17}", parts[0]) or parts[1] not in ALL_CATEGORIES:
                    return MOVE_USAGE
                if dry:
                    return "[dry] 已核验成员权限，本次没有移动图片。"
                result = await asyncio.to_thread(self.store.move, int(parts[0]), parts[1])
                return ("已将「" + result["title"] + "」移动到「" + result["category"] + "」。" if result["changed"]
                        else "「" + result["title"] + "」已经在「" + result["category"] + "」里，无需移动。") + "\n图片编号：" + parts[0]
            if action in ("删除图片", "恢复图片"):
                if not re.fullmatch(r"[1-9][0-9]{0,17}", argument):
                    return MANAGE_USAGE
                if dry:
                    return "[dry] 已核验成员权限，本次没有删除或恢复图片。"
                result = await asyncio.to_thread(self.store.set_deleted, int(argument), action == "删除图片", uid)
                status = ("已删除" if result["changed"] else "此前已删除") if result["deleted"] else (
                    "已恢复" if result["changed"] else "图片已在画廊中")
                reply = status + "：「" + result["title"] + "」（编号 " + argument + "）。"
                return reply + ("\n误删可发送 /恢复图片 " + argument if result["deleted"] else "")
            parts = argument.split(maxsplit=1)
            category = CATEGORY_ALIASES.get(parts[0], parts[0]) if parts else ""
            title = picture_title(parts[1], "") if len(parts) > 1 else ""
            if category not in ALL_CATEGORIES:
                return USAGE
            content = message.get("content") or {}
            quoted = content.get("reference") or content.get("quote") or content.get("reply") or {}
            if not isinstance(quoted, dict):
                quoted = {}
            message_id = quoted.get("id") or quoted.get("message_id")
            if not message_id:
                # 部分实时推送缺少 reference；只回查当前指令，不猜最近一张图。
                command_id = message.get("message_id")
                if command_id:
                    refreshed = await self.platform.original(str(command_id), str(message.get("sent_at") or ""))
                    if (refreshed.get("message_id") != command_id or refreshed.get("sent_by") != uid
                            or refreshed.get("chatroom_id") not in (None, PRIMARY_CID)
                            or command((refreshed.get("content") or {}).get("text")) != parsed):
                        raise GalleryError("无法核验这条保存指令，未保存。")
                    quoted = (refreshed.get("content") or {}).get("reference") or {}
                    if not isinstance(quoted, dict):
                        quoted = {}
                    message_id = quoted.get("id") or quoted.get("message_id")
                if not message_id:
                    return "先回复要保存的那张图片，再发指令哦～\n" + USAGE
            original = await self.platform.original(str(message_id),
                str(quoted.get("sent_at") or quoted.get("sentAt") or ""))
            if original.get("message_id") != str(message_id):
                raise GalleryError("原消息编号不一致，未保存。")
            if original.get("chatroom_id") not in (None, PRIMARY_CID):
                raise GalleryError("只能保存妹子群里的图片或视频。")
            image = original.get("content") or {}
            # 平台图片通常用 url/text，视频消息使用 videoUrl；兼容 webhook 与历史回查的两种命名。
            url = (image.get("url") or image.get("text") or image.get("videoUrl")
                   or image.get("video_url") or image.get("imageUrl") or image.get("image_url"))
            media_type = str(image.get("type") or "").lower()
            video = media_type == "video"
            if media_type not in ("image", "video") or not isinstance(url, str) or not url.startswith("https://"):
                raise GalleryError("回复的消息不是可保存的图片或视频，请回复原文件消息。")
            if dry:
                return "[dry] 已核验原图，将保存到「" + category + "」，本次没有写入。"
            filename = str(image.get("filename") or image.get("fileName") or image.get("name") or "")
            data = await self.platform.download(url, video=True) if video else await self.platform.download(url)
            # 下载可能花时间，写入前再次确认成员身份，权限失败绝不落库。
            name = await self.platform.member(uid)
            image_id, created = await asyncio.to_thread(self.store.save, category, data,
                uploader=uid, name=name, filename=filename, source=str(message_id), title=title,
                content_type="video/mp4" if video else "")
            return ("已保存到「" if created else "这张图已经在「") + category + (
                "」啦～" if created else "」里啦，不会重复收录～") + "\n" + CARD_URL + ("\n视频编号：" if video else "\n图片编号：") + str(image_id)
        except (GalleryError, GalleryUnavailable, ModerationUnavailable) as exc:
            log("gallery: 操作被拒 " + type(exc).__name__)
            return str(exc)
        except Exception as exc:
            log("gallery: 操作失败 " + type(exc).__name__)
            return "这次图集操作没能成功，请稍后再试。"


async def command_reply(cid, uid, text, message, *, dry=False):
    return await Service().reply(cid, uid, text, message, dry=dry)
