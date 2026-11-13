"""Member-only original-file downloads; scoped grants never contain the player's JWT."""
import asyncio
import secrets
import time
from aiohttp import web


def add_download_routes(app, *, store, platform, uid_key, image_handler):
    grants = {}  # One five-minute, single-file grant per member; no disk or logs.

    async def issue(request):
        try:
            image_id = int(request.match_info['image_id'])
            if not 0 < image_id <= 9223372036854775807:
                raise ValueError
        except ValueError:
            raise web.HTTPBadRequest()
        row = await asyncio.to_thread(store.image, image_id, include_private=True)
        if row is None:
            raise web.HTTPNotFound()
        now = time.monotonic()
        for key, grant in list(grants.items()):
            if grant['expires'] <= now or grant['uid'] == request[uid_key]:
                del grants[key]
        if len(grants) >= 1024:
            raise web.HTTPServiceUnavailable(text='下载繁忙，请稍后重试。')
        token = secrets.token_urlsafe(32)
        grants[token] = dict(uid=request[uid_key], image_id=image_id,
                             category=row['category'], expires=now + 300)
        return web.json_response({'token': token, 'expires_in': 300})

    async def private_file(request):
        bearer = request.headers.get('Authorization', '')
        token = bearer[16:] if bearer.startswith('GalleryDownload ') else ''
        grant = grants.get(token)
        if not grant or grant['expires'] <= time.monotonic():
            grants.pop(token, None)
            raise web.HTTPUnauthorized(text='下载凭证已过期，请回到图集重新点击保存。')
        if str(grant['image_id']) != request.match_info['image_id']:
            raise web.HTTPNotFound()
        await platform.member(grant['uid'])
        row = await asyncio.to_thread(store.image, grant['image_id'], include_private=True)
        if row is None or row['category'] != grant['category']:
            raise web.HTTPNotFound(text='文件已删除或移动，请回到图集重新打开。')
        return await image_handler(request, private=True)

    async def page(request):
        return web.Response(text=DOWNLOAD_PAGE, content_type='text/html', headers={
            'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow',
            'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src blob:; media-src blob:; base-uri 'none'; frame-ancestors 'none'",
        })

    app.router.add_post('/api/gallery/images/{image_id}/download-ticket', issue)
    app.router.add_get('/api/gallery/downloads/{image_id}', private_file)
    app.router.add_get('/api/gallery/download', page)


DOWNLOAD_PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>保存原文件 · 姐妹图集</title>
<style>body{margin:0;background:#faf9f6;color:#414a3d;font:16px/1.7 system-ui}main{max-width:620px;margin:40px auto;padding:24px}h1{font-size:24px;overflow-wrap:anywhere}button,a{display:inline-block;border:0;border-radius:9px;padding:12px 20px;background:#aa5745;color:white;text-decoration:none;font:inherit;cursor:pointer;margin:6px 10px 6px 0}button:disabled{opacity:.5}img,video{max-width:100%;max-height:60vh;display:block;margin:24px auto}p{overflow-wrap:anywhere}input{width:100%}[hidden]{display:none!important}</style>
<main><p>姐妹图集 · 原文件下载</p><h1 id="name">正在准备文件…</h1>
<p id="status" role="status">正在读取原文件，大视频可能需要一点时间。</p>
<label id="external-hint" hidden>请复制下面的地址，粘贴到浏览器地址栏重新打开：<input id="external-url" readonly aria-label="完整下载地址"></label>
<a id="save" hidden>保存文件</a><button id="share" type="button" hidden>系统分享 / 存储到文件</button>
<button id="retry" type="button" hidden>重新加载</button>
<p>电脑：点击保存后，在浏览器下载列表查看。手机：也可使用系统分享，或长按图片保存；视频可通过播放器菜单保存。</p>
<img id="image" alt="原图" hidden><video id="video" controls playsinline preload="metadata" hidden></video></main>
<script>(()=>{'use strict';
const $=id=>document.getElementById(id),id=new URLSearchParams(location.search).get('id');
const token=new URLSearchParams(location.hash.slice(1)).get('ticket');
// Keep scoped credentials out of history, referrers and server access logs.
try{history.replaceState(null,'',location.pathname+location.search);}
catch{
 $('name').textContent='请在浏览器中打开';
 $('status').textContent='当前窗口仍受小游戏限制。复制下面的地址到浏览器打开后，即可保存原图片或视频。';
 $('external-url').value=location.href;$('external-hint').hidden=false;
 $('external-url').onclick=()=>{$('external-url').select();};
 return;
}
let file,url;
async function load(){
 $('retry').hidden=true;$('status').textContent='正在读取原文件，大视频可能需要一点时间。';
 if(!/^\\d+$/.test(id||'')){$('status').textContent='下载地址无效，请回到图集重新打开。';return;}
 if(!token){$('name').textContent='下载仅限妹子群成员';$('status').textContent='请登录 DZMM 后从图集点击保存，取得有效的成员下载地址。旧的公开下载地址已停用。';return;}
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),120000);
 try{
  const response=await fetch('downloads/'+id+'?download=1',{credentials:'omit',headers:{Authorization:'GalleryDownload '+token},signal:controller.signal});
  if(!response.ok)throw new Error(response.status===404?'文件不存在、已删除，或私密下载地址已失效。请回到图集重新点击保存。':(await response.text()).slice(0,160)||'文件读取失败');
  const blob=await response.blob();
  if(!/^(image|video)\\//.test(blob.type))throw new Error('返回的文件格式不正确，请回图集重试。');
  const raw=response.headers.get('Content-Disposition')||'',match=raw.match(/filename\\*=UTF-8''([^;]+)/i);
  const name=match?decodeURIComponent(match[1]):'原文件';
  file=new File([blob],name,{type:blob.type});url=URL.createObjectURL(blob);
  $('name').textContent=name;$('status').textContent='原文件已准备好（'+(blob.size/1024/1024).toFixed(1)+' MB），点击下面保存。';
  // All downloads keep member credentials in headers and save the authorized original Blob.
  $('save').href=url;$('save').download=name;$('save').textContent=blob.type.startsWith('video/')?'保存视频':'保存图片';$('save').hidden=false;
  const media=$(blob.type.startsWith('video/')?'video':'image');media.src=url;media.hidden=false;
  try{$('share').hidden=!(navigator.canShare&&navigator.canShare({files:[file]}));}catch{}
 }catch(error){$('status').textContent=error.name==='AbortError'?'读取超时，请重试。':error.message;$('retry').hidden=false;}
 finally{clearTimeout(timer);}
}
$('retry').onclick=load;
$('save').onclick=()=>{$('status').textContent='已交给浏览器处理，请查看下载列表；手机也可以使用系统分享。';};
$('share').onclick=async()=>{try{await navigator.share({files:[file]});}catch(error){if(error.name!=='AbortError')$('status').textContent='系统分享不可用，请使用保存按钮或长按原图。';}};
load();})();</script></html>'''
