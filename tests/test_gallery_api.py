"""API 权限必须保护列表、原图、收藏和上传；JWT 校验不能跨游戏重放。"""
import asyncio
from io import BytesIO
from types import SimpleNamespace
import time

from aiohttp import ClientSession, FormData
from aiohttp.test_utils import TestServer
from aiohttp import web
from PIL import Image
import pytest

from runtime.gallery import GalleryError, Store
from runtime.gallery_api import PlayerAuth, create_app

UID = "00000000-0000-4000-8000-000000000001"
GAME = "00000000-0000-4000-8000-000000000002"


def test_platform_unavailable_returns_503_and_leaves_images_unchanged(dbdir):
    from runtime.gallery import GalleryUnavailable
    class ExpiredPlatform:
        async def member(self, uid):
            raise GalleryUnavailable("机器人登录状态已失效，请稍后重试。")
    async def scenario():
        store = Store(dbdir / "gallery.db")
        mid, _ = store.save("姐妹日常", png(), uploader=UID, name="群友", title="原名称")
        server = TestServer(create_app(store=store, platform=ExpiredPlatform(), auth=Auth()))
        await server.start_server()
        try:
            async with ClientSession(headers={"Authorization":"Bearer member-token"}) as client:
                for method, path, kwargs in (("put", f"images/{mid}/title", {"json":{"title":"不能保存"}}),
                                              ("post", "upload", {"json":{}})):
                    response = await getattr(client, method)(server.make_url("/api/gallery/" + path), **kwargs)
                    assert response.status == 503 and "登录状态已失效" in await response.text()
                response = await client.get(server.make_url("/api/gallery/items"))
                assert response.status == 200
                assert (await response.json())["items"][0]["title"] == "原名称"
                assert len(store.list(UID)) == 1
        finally:
            await server.close()
    asyncio.run(scenario())


def png():
    output = BytesIO()
    Image.new("RGB", (12, 8), "pink").save(output, format="PNG")
    return output.getvalue()


def test_move_api_permissions_conflicts_and_persistent_favorites(dbdir):
    async def scenario():
        store, platform = Store(dbdir / "gallery.db"), Platform()
        mid, _ = store.save("个人立绘", png(), uploader=UID, name="群友", title="原名称")
        store.favorite(UID, mid, True)
        server = TestServer(create_app(store=store, platform=platform, auth=Auth(), origins=("null",)))
        await server.start_server()
        headers = {"Authorization":"Bearer member-token", "Origin":"null"}
        try:
            async with ClientSession() as client:
                url = server.make_url(f"/api/gallery/images/{mid}/category")
                response = await client.put(url, json={"category":"姐妹日常"})
                assert response.status == 401
                platform.allowed = False
                response = await client.put(url, json={"category":"姐妹日常"}, headers=headers)
                assert response.status == 403 and store.list(UID)[0]["category"] == "个人立绘"
                platform.allowed = True
                response = await client.put(url, json={"category":"姐妹日常"}, headers=headers)
                assert response.status == 200 and (await response.json())["changed"]
                response = await client.put(url, json={"category":"姐妹日常"}, headers=headers)
                assert response.status == 200 and not (await response.json())["changed"]
                row = Store(store.path).list(UID)[0]
                assert row["category"] == "姐妹日常" and row["title"] == "原名称" and row["saved"] == 1
                assert store.image(mid)["data"] == png()
                for payload in ({"category":"其他"}, {}, {"category":None}, [], {"category":["个人立绘"]}):
                    response = await client.put(url, json=payload, headers=headers)
                    assert response.status == 400
                store.save("个人立绘", png(), uploader="other", name="群友", title="重复图")
                response = await client.put(url, json={"category":"个人立绘"}, headers=headers)
                assert response.status == 409 and "未移动" in await response.text()
                store.set_deleted(mid, True, UID)
                response = await client.put(url, json={"category":"高清美图"}, headers=headers)
                assert response.status == 403 and "已删除" in await response.text()
        finally:
            await server.close()
    asyncio.run(scenario())


class Platform:
    allowed = True
    async def member(self, uid):
        if not self.allowed:
            raise GalleryError("仅限当前妹子群成员")
        return "测试成员"


class Auth:
    async def verify(self, token):
        if token != "member-token":
            raise web.HTTPUnauthorized()
        return UID


def test_public_reads_and_member_only_upload_rename(dbdir):
    async def scenario():
        store, platform = Store(dbdir / "gallery.db"), Platform()
        server = TestServer(create_app(store=store, platform=platform, auth=Auth(), origins=("null",)))
        await server.start_server()
        headers = {"Authorization": "Bearer member-token", "Origin": "null"}
        async with ClientSession() as client:
            try:
                response = await client.get(server.make_url("/api/gallery/items"))
                assert response.status == 200 and (await response.json())["items"] == []
                response = await client.put(server.make_url("/api/gallery/images/1/title"), json={"title":"denied"})
                assert response.status == 401
                response = await client.post(server.make_url("/api/gallery/upload"), json={}, headers=headers)
                assert response.status == 400
                form = FormData()
                form.add_field("category", "姐妹日常")
                form.add_field("title", "下午茶")
                form.add_field("caption", "一起存图")
                form.add_field("images", png(), filename="photo.png", content_type="image/png")
                response = await client.post(server.make_url("/api/gallery/upload"), data=form, headers=headers)
                assert response.status == 201, await response.text()
                image_id = (await response.json())["ids"][0]
                response = await client.get(server.make_url("/api/gallery/items"))
                row = (await response.json())["items"][0]
                assert row["title"] == "下午茶" and row["filename"] == "下午茶.png"
                assert not ({"uploader","name","source_message_id","saved"} & row.keys())
                response = await client.get(server.make_url(f"/api/gallery/images/{image_id}"))
                assert await response.read() == png() and "Content-Disposition" in response.headers
                response = await client.put(server.make_url(f"/api/gallery/images/{image_id}/title"),json={"title":"新的名称"},headers=headers)
                assert response.status == 200
                assert Store(store.path).list(UID)[0]["title"] == "新的名称"
                response = await client.put(server.make_url(f"/api/gallery/images/{image_id}/favorite"),json={"saved":True},headers=headers)
                assert response.status == 200
                response = await client.get(server.make_url("/api/gallery/me"),headers=headers)
                assert (await response.json()) == {"can_edit":True,"mine":[image_id],"saved":[image_id]}
                platform.allowed = False
                response = await client.get(server.make_url("/api/gallery/me"),headers=headers)
                assert (await response.json())["can_edit"] is False
                for path in ("items",f"images/{image_id}"):
                    response = await client.get(server.make_url("/api/gallery/"+path))
                    assert response.status == 200
                for method,path,kwargs in (("put",f"images/{image_id}/title",{"json":{"title":"denied"}}),("post","upload",{"json":{}})):
                    response = await getattr(client,method)(server.make_url("/api/gallery/"+path),headers=headers,**kwargs)
                    assert response.status == 403
                assert store.list(UID)[0]["title"] == "新的名称"
                response = await client.get(server.make_url("/api/gallery/items"), headers={"Origin":"https://attacker.invalid"})
                assert response.status == 403
            finally:
                await server.close()
    asyncio.run(scenario())


def test_group_command_and_api_use_same_persistent_gallery(dbdir):
    from runtime import gallery as G
    from tests.test_gallery import Platform as GroupPlatform, message
    async def scenario():
        path = dbdir / "gallery.db"
        reply = await G.Service(Store(path), GroupPlatform()).reply(
            G.PRIMARY_CID, "member", "/保存 姐妹日常", message())
        assert "已保存" in reply
        server = TestServer(create_app(store=Store(path), platform=Platform(), auth=Auth()))
        await server.start_server()
        try:
            async with ClientSession(headers={"Authorization": "Bearer member-token"}) as client:
                response = await client.get(server.make_url("/api/gallery/items"))
                rows = (await response.json())["items"]
                assert len(rows) == 1 and rows[0]["category"] == "姐妹日常"
                response = await client.get(server.make_url(f'/api/gallery/images/{rows[0]["id"]}'))
                assert response.status == 200 and await response.read() == png()
        finally:
            await server.close()
    asyncio.run(scenario())


def test_public_thumbnail_is_small_and_preserves_original(dbdir):
    async def scenario():
        store = Store(dbdir / "gallery.db")
        output = BytesIO()
        Image.effect_noise((1800, 1200), 80).convert("RGB").save(output, format="PNG")
        original = output.getvalue()
        image_id, _ = store.save("姐妹日常", original, uploader=UID, name="private", filename="original.png")
        server = TestServer(create_app(store=store, platform=Platform(), auth=Auth(), origins=("null",)))
        await server.start_server()
        try:
            async with ClientSession() as client:
                path = server.make_url(f"/api/gallery/images/{image_id}")
                response = await client.get(str(path)+"?size=thumb", headers={"Origin":"null"})
                assert response.status == 200 and response.content_type == "image/webp"
                assert response.headers["Access-Control-Allow-Origin"] == "null"
                preview = await response.read()
                with Image.open(BytesIO(preview)) as image:
                    assert image.size == (640, 427)
                assert len(preview) < len(original) / 5
                response = await client.get(str(path)+"?size=thumb")
                assert await response.read() == preview
                response = await client.get(path)
                assert await response.read() == original
                assert store.image(image_id)["data"] == original
                response = await client.get(str(path)+"?size=giant")
                assert response.status == 400
                response = await client.get(server.make_url("/api/gallery/images/999?size=thumb"))
                assert response.status == 404
                response = await client.get(str(path)+"?size=thumb", headers={"Origin":"https://attacker.invalid"})
                assert response.status == 403
        finally:
            await server.close()
    asyncio.run(scenario())


def test_thumbnail_orientation_and_transparency():
    from runtime.gallery_api import make_thumbnail
    output = BytesIO()
    image = Image.new("RGB", (1200, 800), "pink")
    exif = Image.Exif()
    exif[274] = 6
    image.save(output, format="JPEG", exif=exif)
    with Image.open(BytesIO(make_thumbnail(output.getvalue()))) as thumb:
        assert thumb.size == (427, 640)
    output = BytesIO()
    Image.new("RGBA", (800, 800), (255, 0, 0, 0)).save(output, format="PNG")
    with Image.open(BytesIO(make_thumbnail(output.getvalue()))) as thumb:
        assert thumb.getpixel((0, 0))[3] == 0


def test_delete_permissions_public_reads_cache_and_group_restore(dbdir):
    from runtime import gallery as G
    from tests.test_gallery import Platform as GroupPlatform
    async def scenario():
        store = Store(dbdir / "gallery.db")
        image_id, _ = store.save("姐妹日常", png(), uploader=UID, name="群友", title="下午茶")
        store.favorite(UID, image_id, True)
        platform = Platform()
        server = TestServer(create_app(store=store, platform=platform, auth=Auth(), origins=("null",)))
        await server.start_server()
        headers = {"Authorization": "Bearer member-token", "Origin": "null"}
        try:
            async with ClientSession() as client:
                path = server.make_url(f"/api/gallery/images/{image_id}")
                assert (await client.get(str(path)+"?size=thumb")).status == 200
                assert (await client.delete(path)).status == 401
                platform.allowed = False
                assert (await client.delete(path, headers=headers)).status == 403
                assert store.image(image_id)
                platform.allowed = True
                response = await client.options(path, headers={"Origin":"null","Access-Control-Request-Method":"DELETE"})
                assert "DELETE" in response.headers["Access-Control-Allow-Methods"]
                response = await client.delete(path, headers=headers)
                assert response.status == 200 and (await response.json())["deleted"] is True
                assert (await client.delete(path, headers=headers)).status == 200
                for suffix in ("", "?size=thumb"):
                    assert (await client.get(str(path)+suffix)).status == 404
                response = await client.get(server.make_url("/api/gallery/items"))
                assert (await response.json())["items"] == []
                response = await client.get(server.make_url("/api/gallery/me"), headers=headers)
                assert (await response.json()) == {"can_edit":True,"mine":[],"saved":[]}
                for suffix in ("/title", "/favorite"):
                    response = await client.put(str(path)+suffix, headers=headers, json={"title":"不可改","saved":True})
                    assert response.status == 403
                service = G.Service(Store(store.path), GroupPlatform())
                assert "已恢复" in await service.reply(G.PRIMARY_CID,"member",f"/恢复图片 {image_id}",{})
                assert (await client.get(path)).status == 200
                assert (await client.get(str(path)+"?size=thumb")).status == 200
                response = await client.get(server.make_url("/api/gallery/me"), headers=headers)
                assert (await response.json())["saved"] == [image_id]
        finally:
            await server.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("game_id", [GAME, "3799187"])
def test_jwt_requires_our_game_signature_issuer_expiry_and_member_id(game_id):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    auth = PlayerAuth(game_id)
    auth.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key()))
    claims = {"sub": UID, "aud": game_id, "iss": "dzmm-gamefy", "exp": time.time() + 120, "iat": time.time()}
    token = jwt.encode(claims, private, algorithm="RS256")
    assert asyncio.run(auth.verify(token)) == UID
    for changes, key in (({"aud": UID}, private), ({"aud": [game_id, UID]}, private), ({"iss": "attacker"}, private),
                         ({"exp": time.time() - 10}, private), ({"sub": "invalid"}, private), ({}, other)):
        with pytest.raises(web.HTTPUnauthorized):
            asyncio.run(auth.verify(jwt.encode({**claims, **changes}, key, algorithm="RS256")))
    for invalid in ("", "0", "any-game", "*"):
        with pytest.raises(ValueError):
            PlayerAuth(invalid)
