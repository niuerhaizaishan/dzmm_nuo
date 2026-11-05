"""真实视频字节覆盖上传、引用、封面、Range、私密门禁和生命周期。"""
import asyncio
from io import BytesIO
import av
from PIL import Image
import pytest
from aiohttp import ClientSession, FormData, web
from aiohttp.test_utils import TestServer
from runtime import gallery as G
from runtime.gallery_api import create_app

@pytest.fixture(params=['mp4', 'webm'])
def movie(request):
    output = BytesIO()
    with av.open(output, 'w', format=request.param) as container:
        stream = container.add_stream('libx264' if request.param == 'mp4' else 'libvpx-vp9', rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
        for _ in range(3):
            frame = av.VideoFrame.from_image(Image.new('RGB', (64, 48), 'pink'))
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    return output.getvalue(), request.param

def test_video_validation_and_store_lifecycle(tmp_path, movie):
    data, ext = movie
    width, height, mime, extension = G.inspect_media(data)
    assert (width, height, mime, extension) == (64, 48, 'video/'+ext, '.'+ext)
    store = G.Store(tmp_path/'media.db')
    mid, created = store.save('个人立绘', data, uploader='member', name='测试', title='小视频')
    assert created and not store.save('个人立绘', data, uploader='member', name='测试')[1]
    assert G.Store(store.path).image(mid)['data'] == data
    store.favorite('member', mid, True)
    store.rename(mid, '新名字')
    store.move(mid, G.PRIVATE_CATEGORY)
    assert store.image(mid) is None
    row = store.list('member', only_private=True)[0]
    assert row['id'] == mid and row['saved'] and row['title'] == '新名字'
    assert G.download_name(row['title'], mime) == '新名字.'+ext
    store.set_deleted(mid, True, 'member')
    assert store.image(mid, include_private=True) is None
    store.set_deleted(mid, False, 'member')
    assert store.image(mid, include_private=True)['data'] == data

def test_gif_is_preserved_as_animated_image(tmp_path):
    output = BytesIO()
    Image.new('RGB', (24, 16), 'red').save(output, format='GIF', save_all=True,
        append_images=[Image.new('RGB', (24, 16), 'blue')], duration=40, loop=0)
    data = output.getvalue()
    assert G.inspect_media(data, 'moving.gif') == (24, 16, 'image/gif', '.gif')
    store = G.Store(tmp_path/'gif.db')
    mid, _ = store.save('姐妹日常', data, uploader='member', name='测试', filename='moving.gif')
    row = store.list('member')[0]
    assert row['mime'] == 'image/gif' and row['filename'] == 'moving.gif'
    assert store.image(mid)['data'] == data

@pytest.mark.parametrize('data, filename', [(b'fake', 'a.mp4'), (b'\0\0\0\x18ftypisom'+b'\0'*40, 'a.mp4'), (b'\x1a\x45\xdf\xa3'+b'\0'*40, 'a.webm')])
def test_invalid_video_rejected(data, filename):
    with pytest.raises(G.GalleryError): G.inspect_media(data, filename)

def test_video_limits(monkeypatch, movie):
    data, ext = movie
    monkeypatch.setattr(G, 'MAX_VIDEO_BYTES', len(data)-1)
    with pytest.raises(G.GalleryError, match='50 MB'): G.inspect_media(data)

def test_group_video_without_filename(tmp_path, movie):
    data, ext = movie
    class Platform:
        async def member(self, uid): return '成员'
        async def original(self, mid, sent_at=''):
            return {'message_id': mid, 'chatroom_id': G.PRIMARY_CID, 'content': {'type': 'video', 'videoUrl': 'https://rls.cheggpt.com/video'}}
        async def download(self, url, *, video=False):
            assert video
            return data
    store = G.Store(tmp_path/'media.db')
    msg = {'content': {'reference': {'id': 'video1'}}}
    result = asyncio.run(G.Service(store, Platform()).reply(G.PRIMARY_CID, 'member', '/保存 姐妹日常 午后', msg))
    assert '已保存到「姐妹日常」' in result and '视频编号' in result
    row = store.list('member')[0]
    assert row['title'] == '午后'
    assert store.image(row['id'])['data'] == data

def test_video_upload_range_private_and_revocation(tmp_path, movie):
    data, ext = movie
    class Platform:
        allowed = True
        async def member(self, uid):
            if not self.allowed: raise G.GalleryError('仅限成员')
            return '成员'
    class Auth:
        async def verify(self, token):
            if token != 'member': raise web.HTTPUnauthorized()
            return 'member'
    async def scenario():
        store, platform = G.Store(tmp_path/'media.db'), Platform()
        server = TestServer(create_app(store=store, platform=platform, auth=Auth(), origins=('null',)))
        await server.start_server()
        headers = {'Authorization': 'Bearer member', 'Origin': 'null'}
        try:
            async with ClientSession() as client:
                form = FormData(); form.add_field('category', G.PRIVATE_CATEGORY);form.add_field('title', '私密视频');form.add_field('images', data, filename='clip.'+ext, content_type='video/'+ext)
                response = await client.post(server.make_url('/api/gallery/upload'), data=form, headers=headers)
                assert response.status == 201, await response.text()
                mid = (await response.json())['ids'][0]
                root = '/api/gallery/private-images/'+str(mid)
                for suffix in ('', '?size=thumb'):
                    response = await client.get(server.make_url('/api/gallery/images/'+str(mid)+suffix))
                    assert response.status == 404
                    response = await client.get(server.make_url(root+suffix))
                    assert response.status == 401
                response = await client.get(server.make_url('/api/gallery/items'))
                assert (await response.json())['items'] == []
                response = await client.get(server.make_url(root+'?size=thumb'), headers=headers)
                assert response.status == 200 and response.content_type == 'image/webp'
                with Image.open(BytesIO(await response.read())) as frame: assert frame.size == (64,48)
                response = await client.get(server.make_url(root), headers=headers|{'Range': 'bytes=0-31'})
                assert response.status == 206 and await response.read() == data[:32]
                assert response.headers['Content-Range'] == f'bytes 0-31/{len(data)}'
                response = await client.get(server.make_url(root), headers=headers|{'Range':'bytes=-16'})
                assert response.status == 206 and await response.read() == data[-16:]
                response = await client.get(server.make_url(root), headers=headers|{'Range':f'bytes={len(data)}-'})
                assert response.status == 416
                response = await client.head(server.make_url(root), headers=headers)
                assert response.status == 200 and int(response.headers['Content-Length']) == len(data)
                platform.allowed = False
                for suffix in ('','?size=thumb'):
                    response = await client.get(server.make_url(root+suffix), headers=headers|{'Range':'bytes=0-31'})
                    assert response.status == 403
                platform.allowed = True
                store.set_deleted(mid, True, 'member')
                response = await client.get(server.make_url(root+'?size=thumb'), headers=headers)
                assert response.status == 404
        finally: await server.close()
    asyncio.run(scenario())
