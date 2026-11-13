"""Downloads must keep originals and never turn the private album public."""
import asyncio
from io import BytesIO
from unittest.mock import patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from PIL import Image

from runtime.gallery import Store, GalleryError
from runtime.gallery_api import create_app


class Auth:
    async def verify(self, token):
        if token != 'member':
            raise web.HTTPUnauthorized()
        return 'fixture-member'


class Platform:
    allowed = True

    async def member(self, uid):
        if not self.allowed:
            raise GalleryError('仅限当前妹子群成员')
        return 'fixture'


def media():
    for fmt in ('PNG', 'GIF'):
        out = BytesIO()
        frames = [Image.new('RGB', (32, 32), c) for c in ('red', 'blue')]
        frames[0].save(out, format=fmt, **({'save_all': True, 'append_images': frames[1:], 'duration': 200, 'loop': 0} if fmt == 'GIF' else {}))
        yield fmt.lower(), out.getvalue()
    import av
    out = BytesIO()
    with av.open(out, 'w', format='mp4') as container:
        stream = container.add_stream('libx264', rate=24)
        stream.width = stream.height = 32
        stream.pix_fmt = 'yuv420p'
        for packet in stream.encode(av.VideoFrame.from_image(Image.new('RGB', (32, 32), 'blue'))):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    yield 'mp4', out.getvalue()


def test_original_downloads_and_scoped_private_grants(dbdir):
    async def run():
        store, platform = Store(dbdir / 'downloads.db'), Platform()
        rows = []
        for ext, data in media():
            for category in ('姐妹日常', '私密相册'):
                ident, _ = store.save(category, data, uploader='fixture-member', name='fixture', filename='原文件.' + ext, title='下载名称')
                rows.append((ident, category, ext, data))
        async with TestServer(create_app(store=store, platform=platform, auth=Auth(), origins=('null',))) as server:
            async with ClientSession() as client:
                base = str(server.make_url('/api/gallery/'))
                member = {'Authorization': 'Bearer member', 'Origin': 'null'}
                response = await client.get(base + 'download?id=1')
                assert response.status == 200
                html = await response.text()
                assert 'history.replaceState' in html and 'navigator.share' in html
                assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
                assert response.headers['Cache-Control'] == 'private, no-store'
                for ident, category, ext, data in rows:
                    url = base + f'images/{ident}?download=1'
                    response = await client.get(url)
                    assert response.status == 401
                    assert (await client.head(url)).status == 401
                    assert (await client.get(url, headers={'Range': 'bytes=0-9'})).status == 401
                    platform.allowed = False
                    assert (await client.get(url, headers=member)).status == 403
                    platform.allowed = True
                    response = await client.get(base + f'images/{ident}')
                    assert response.status == (404 if category == '私密相册' else 200)  # viewing unchanged
                    response = await client.get(url, headers=member)
                    assert response.status == (404 if category == '私密相册' else 200)
                    if category != '私密相册':
                        assert await response.read() == data
                        assert response.headers['Content-Disposition'].endswith('.' + ext)
                        assert response.headers['Content-Disposition'].startswith('attachment;')
                    issue = base + f'images/{ident}/download-ticket'
                    assert (await client.post(issue)).status == 401
                    platform.allowed = False
                    assert (await client.post(issue, headers=member)).status == 403
                    platform.allowed = True
                    response = await client.post(issue, headers=member)
                    assert response.status == 200
                    token = (await response.json())['token']
                    headers = {'Authorization': 'GalleryDownload ' + token}
                    download = base + f'downloads/{ident}?download=1'
                    assert (await client.get(download)).status == 401
                    assert (await client.get(download, headers={'Authorization': token})).status == 401
                    assert (await client.get(base + f'downloads/{ident + 999}', headers=headers)).status == 404
                    response = await client.get(download, headers=headers)
                    assert response.status == 200 and await response.read() == data
                    assert response.headers['Content-Disposition'].endswith('.' + ext)
                    response = await client.get(download, headers=headers | {'Range': 'bytes=0-9'})
                    assert response.status == 206 and await response.read() == data[:10]
                    platform.allowed = False
                    assert (await client.get(download, headers=headers)).status == 403
                    platform.allowed = True
                    with patch('runtime.gallery_download.time', type('Clock', (), {'monotonic': staticmethod(lambda: float('inf'))})):
                        assert (await client.get(download, headers=headers)).status == 401
                    token = (await (await client.post(issue, headers=member)).json())['token']
                    headers = {'Authorization': 'GalleryDownload ' + token}
                    await client.post(issue, headers=member)
                    assert (await client.get(download, headers=headers)).status == 401  # new grant revokes old
                    token = (await (await client.post(issue, headers=member)).json())['token']
                    headers = {'Authorization': 'GalleryDownload ' + token}
                    store.move(ident, '高清美图')
                    assert (await client.get(download, headers=headers)).status == 404
                    store.move(ident, category)
                    store.set_deleted(ident, True, 'fixture-member')
                    assert (await client.get(download, headers=headers)).status == 404
    asyncio.run(run())
