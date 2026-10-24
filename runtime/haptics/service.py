"""触觉控制服务：控制页 + 少量 API，跑在回环端口上，由 Caddy 决定是否对外。

安全模型：所有 /api 都要 X-Haptics-Token（或同名 cookie），token 来自环境变量；
没有配置 token 时服务只允许 --mock 模式启动，绝不裸奔到公网。
"""
import argparse
import hmac
import os

from aiohttp import web

from runtime.obs import log
from runtime.haptics.mock import MockClient
from runtime.haptics.protocol import ButtplugClient
from runtime.haptics.scheduler import Scheduler

TOKEN_HEADER = "X-Haptics-Token"
PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小小糯 · 触觉控制台</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;background:#14161a;color:#e6e6ea;font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif}
main{max-width:560px;margin:0 auto;padding:18px}
h1{font-size:18px;margin:6px 0 16px;font-weight:600}
h1 small{color:#8b93a1;font-weight:400;font-size:12px;margin-left:8px}
.card{background:#1c1f25;border:1px solid #2a2f38;border-radius:12px;padding:14px;margin-bottom:14px}
label{display:block;font-size:12px;color:#8b93a1;margin:10px 0 4px}
input[type=text],input[type=password]{width:100%;padding:10px;border-radius:8px;border:1px solid #333a45;background:#12151a;color:#e6e6ea}
input[type=range]{width:100%}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
button{flex:1;min-width:96px;padding:11px;border-radius:9px;border:0;background:#2f3743;color:#dfe4ec;font-size:14px;cursor:pointer}
button.primary{background:#b0533f;color:#fff}button.danger{background:#7d2b22;color:#fff}
button:disabled{opacity:.45;cursor:default}
#status{white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;color:#9aa4b2}
#log{font:12px/1.6 ui-monospace,Consolas,monospace;color:#8b93a1;max-height:180px;overflow:auto}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;margin-right:6px}
.on{background:#204a2f;color:#7ee2a8}.off{background:#4a2020;color:#f09c9c}
</style></head><body><main>
<h1>小小糯 · 触觉控制台<small id="pill" class="pill off">未连接</small></h1>

<div class="card">
  <label>控制令牌（服务器环境变量 DZMM_HAPTICS_TOKEN）</label>
  <input type="password" id="token" placeholder="粘贴令牌后点任意按钮">
  <label>设备端地址（手机上 Intiface 的 Buttplug WS）</label>
  <input type="text" id="url" placeholder="ws://100.x.y.z:12345">
  <div class="row"><button id="btn-connect" class="primary">连接</button>
  <button id="btn-disconnect">断开</button></div>
</div>

<div class="card">
  <label>强度 <b id="level-label">30%</b></label>
  <input type="range" id="level" min="1" max="100" value="30">
  <label>时长 <b id="dur-label">1200ms</b></label>
  <input type="range" id="dur" min="200" max="4000" step="100" value="1200">
  <div class="row"><button id="btn-pulse" class="primary">测试一下</button>
  <button id="btn-stop" class="danger">立即停止</button></div>
</div>

<div class="card"><label>状态</label><div id="status">—</div></div>
<div class="card"><label>操作日志</label><div id="log"></div></div>
</main><script>
const $=s=>document.querySelector(s);
const keep={token:localStorage.getItem("haptics_token")||"",url:localStorage.getItem("haptics_url")||""};
$("#token").value=keep.token;$("#url").value=keep.url;
const logline=(t)=>{const d=document.createElement("div");d.textContent=new Date().toLocaleTimeString()+"  "+t;$("#log").prepend(d);};
async function call(path,body){
  const token=$("#token").value.trim();localStorage.setItem("haptics_token",token);
  const url=$("#url").value.trim();localStorage.setItem("haptics_url",url);
  const res=await fetch(path,{method:body===undefined?"GET":"POST",
    headers:{"X-Haptics-Token":token,"Content-Type":"application/json"},
    body:body===undefined?undefined:JSON.stringify(body)});
  const text=await res.text();let data;try{data=JSON.parse(text)}catch{data={raw:text}}
  if(!res.ok){logline("✗ "+path+" → "+res.status+" "+(data.detail||data.error||text).toString().slice(0,120));return data}
  return data;
}
async function refresh(){
  try{
    const d=await call("/api/status");
    const dev=(d.device&&d.device.devices||[]).map(x=>x.display+"("+x.kinds.join(",")+")").join(" / ")||"无";
    $("#pill").className="pill "+(d.device&&d.device.connected?"on":"off");
    $("#pill").textContent=d.device&&d.device.connected?"已连接":"未连接";
    $("#status").textContent=["设备端: "+(d.device&&d.device.connected?"已连接":"未连接"),
      "地址: "+((d.device&&d.device.url)||"—"),"设备: "+dev,
      "活动: "+(d.active?"是，剩余 "+d.remaining_ms+"ms":"否"),
      "上限: 强度 "+(d.caps.max_level*100).toFixed(0)+"% / 时长 "+d.caps.max_duration_ms+"ms",
      "统计: "+JSON.stringify(d.stats),
      "最近: "+JSON.stringify(d.last)].join("\\n");
  }catch(e){$("#status").textContent="需要令牌：填入 DZMM_HAPTICS_TOKEN 后刷新（"+e+"）"}
}
function pct(){return Number($("#level").value)/100}
$("#level").oninput=()=>{$("#level-label").textContent=$("#level").value+"%"};
$("#dur").oninput=()=>{$("#dur-label").textContent=$("#dur").value+"ms"};
$("#btn-connect").onclick=async()=>{
  const d=await call("/api/connect",{url:$("#url").value.trim()});
  logline(d&&d.ok?"✓ 已连接":"✗ 连接失败 "+(d&&(d.detail||d.error)||""));refresh()};
$("#btn-disconnect").onclick=async()=>{await call("/api/disconnect",{});logline("已断开");refresh()};
$("#btn-pulse").onclick=async()=>{
  const d=await call("/api/pulse",{level:pct(),duration_ms:Number($("#dur").value),event_id:"ui-"+Date.now()});
  logline(d&&d.accepted?"✓ 已发送 "+Math.round(d.level*100)+"% / "+d.duration_ms+"ms（设备值 "+d.value+"）":"✗ "+(d&&d.reason||"失败"));refresh()};
$("#btn-stop").onclick=async()=>{const d=await call("/api/stop",{});logline("■ 已停止 reached="+(d&&d.reached_device));refresh()};
$("#token").onchange=refresh;
refresh();setInterval(refresh,3000);
</script></body></html>"""


def create_app(*, token="", client=None, scheduler=None, mock=False):
    client = client or getattr(scheduler, "client", None) or (MockClient() if mock else ButtplugClient())
    scheduler = scheduler or Scheduler(client)

    def authorized(request):
        if mock and not token:
            return True
        given = request.headers.get(TOKEN_HEADER) or request.cookies.get("haptics_token", "")
        return bool(token) and hmac.compare_digest(given, token)

    async def page(request):
        return web.Response(text=PAGE, content_type="text/html")

    async def status(request):
        if not authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return web.json_response(scheduler.status())

    async def connect(request):
        if not authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        payload = await request.json()
        url = (payload.get("url") or "").strip()
        if not url.startswith("ws"):
            return web.json_response({"ok": False, "detail": "地址要以 ws:// 或 wss:// 开头"}, status=400)
        try:
            await client.connect(url)
        except Exception as exc:
            return web.json_response({"ok": False, "detail": str(exc)}, status=502)
        return web.json_response({"ok": True, "device": client.status()})

    async def disconnect(request):
        if not authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        await scheduler.stop("disconnect")
        await client.close()
        return web.json_response({"ok": True})

    async def pulse(request):
        if not authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        payload = await request.json()
        result = await scheduler.submit(
            payload.get("event_id") or "",
            payload.get("level", 0.3),
            payload.get("duration_ms", 1200),
            source=payload.get("source") or "ui")
        status = 200 if result.get("accepted") else 409
        return web.json_response(result, status=status)

    async def stop(request):
        if not authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return web.json_response(await scheduler.stop("manual"))

    async def caps(request):
        if not authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        payload = await request.json()
        if "max_level" in payload:
            scheduler.max_level = max(0.05, min(1.0, float(payload["max_level"])))
        if "max_duration_ms" in payload:
            scheduler.max_duration_ms = max(200, int(payload["max_duration_ms"]))
        if "min_interval_ms" in payload:
            scheduler.min_interval_ms = max(0, int(payload["min_interval_ms"]))
        return web.json_response({"ok": True, "caps": scheduler.status()["caps"]})

    async def on_shutdown(app):
        await scheduler.shutdown()

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/connect", connect)
    app.router.add_post("/api/disconnect", disconnect)
    app.router.add_post("/api/pulse", pulse)
    app.router.add_post("/api/stop", stop)
    app.router.add_post("/api/caps", caps)
    app.on_shutdown.append(on_shutdown)
    return app



def main(argv=None):
    parser = argparse.ArgumentParser(description="小小糯触觉控制服务")
    parser.add_argument("--host", default=os.environ.get("DZMM_HAPTICS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DZMM_HAPTICS_PORT", "8096")))
    parser.add_argument("--mock", action="store_true", help="用假设备跑通链路，不碰真实硬件")
    args = parser.parse_args(argv)
    token = os.environ.get("DZMM_HAPTICS_TOKEN", "")
    if not token and not args.mock:
        raise SystemExit("缺少 DZMM_HAPTICS_TOKEN：不给令牌就不启动（控制页可公开访问，不能裸奔）")
    if not token:
        log("haptics: mock 模式启动，未校验令牌（仅本地调试）")
    web.run_app(create_app(token=token, mock=args.mock), host=args.host, port=args.port,
                print=lambda message: None)


if __name__ == "__main__":
    main()
