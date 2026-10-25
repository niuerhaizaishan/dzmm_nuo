"""网页分享控制：一个房间（房主＝拿着玩具的人）＋ 一个扫码进来的控制者。

链路：
    控制者浏览器 ──WSS──▶ 本服务 ──WSS──▶ 房主浏览器 ──BLE──▶ 玩具
    （扫码进入，带 PIN）          （转发指令、夹紧参数、超时自动停）

设计取舍：
  * 二维码里同时带 sid 和 pin，扫码即控（体验优先）；要收回就「结束分享」重开一个房间。
  * 指令只由房主执行，服务端永远不直接碰蓝牙；房主一断线，控制者立刻失效。
  * 每条指令在服务端就有上限（强度/时长）与看门狗（房主没按时回执就补发停止）。
  * 所有东西都在内存里，重启即清空——这里不需要持久化。
"""
import asyncio
import os
import secrets
import time

from aiohttp import WSMsgType, web

from runtime.obs import log

# 上限默认不设（按你的要求）：1.0 = 强度不限，0 = 时长不限。
# 需要时用环境变量收紧，例如 DZMM_SHARE_MAX_LEVEL=0.4 / DZMM_SHARE_MAX_DURATION_MS=3000。
MAX_LEVEL = float(os.environ.get("DZMM_SHARE_MAX_LEVEL", "1.0"))
MAX_DURATION_MS = int(os.environ.get("DZMM_SHARE_MAX_DURATION_MS", "0"))
MIN_INTERVAL_MS = 300     # 同一控制者两条指令最小间隔
SESSION_TTL = 30 * 60     # 闲置 30 分钟回收
MAX_GUESTS = 1            # 一次只允许一个人控制
PIN_ATTEMPTS = 5          # 连续错 5 次锁房
LOCK_SECONDS = 300
STATIC_DIR = "runtime/haptics/static"


def _digits(n=6):
    return "".join(secrets.choice("0123456789") for _ in range(n))


class Session:
    def __init__(self, sid, pin, base_url):
        self.sid = sid
        self.pin = pin
        self.base_url = base_url.rstrip("/")
        self.host = None
        self.guests = set()
        self.created = time.monotonic()
        self.touched = time.monotonic()
        self.fails = 0
        self.locked_until = 0.0
        self.device = ""
        self.last_cmd = {}
        self.last_ack = {}
        self.seq = 0
        self.stop_task = None

    # ---------- 展示 ----------

    def link(self, with_pin=True):
        query = f"sid={self.sid}" + (f"&pin={self.pin}" if with_pin else "")
        return f"{self.base_url}/c?{query}"

    def state(self):
        return {"host": self.host is not None, "guests": len(self.guests),
                "device": self.device, "last_cmd": self.last_cmd,
                "last_ack": self.last_ack,
                "caps": {"max_level": MAX_LEVEL, "max_duration_ms": MAX_DURATION_MS,
                         "capped": MAX_LEVEL < 1.0 or MAX_DURATION_MS > 0},
                "locked": self.locked_until > time.monotonic()}

    def expired(self):
        return time.monotonic() - self.touched > SESSION_TTL

    def locked(self):
        return self.locked_until > time.monotonic()

    def strike(self):
        self.fails += 1
        if self.fails >= PIN_ATTEMPTS:
            self.locked_until = time.monotonic() + LOCK_SECONDS
            self.fails = 0
            log("haptics-share: 房间因 PIN 连错被锁 " + self.sid)

    def check_pin(self, pin):
        return secrets.compare_digest(str(pin or ""), self.pin)


class Hub:
    def __init__(self):
        self.sessions = {}

    def create(self, base_url):
        self.sweep()
        sid = secrets.token_hex(4)  # 定长 8 位十六进制，避免 base64url 去字符后长度不定
        session = Session(sid, _digits(), base_url)
        self.sessions[sid] = session
        log("haptics-share: 新房间 " + sid)
        return session

    def get(self, sid):
        self.sweep()
        return self.sessions.get(str(sid or ""))

    def drop(self, session):
        self.sessions.pop(session.sid, None)

    def sweep(self):
        for sid, session in list(self.sessions.items()):
            if session.host is None and session.expired():
                self.sessions.pop(sid, None)


async def _broadcast_guests(session, payload):
    for ws in list(session.guests):
        try:
            await ws.send_json(payload)
        except Exception:
            session.guests.discard(ws)


async def _to_host(session, payload):
    if session.host is None:
        return False
    try:
        await session.host.send_json(payload)
        return True
    except Exception:
        session.host = None
        return False


def create_app(*, base_url="", hub=None):
    hub = hub or Hub()
    here = __import__("pathlib").Path(__file__).resolve().parent

    def page(name):
        async def handler(request):
            path = here / "static" / name
            return web.Response(text=path.read_text(encoding="utf-8"), content_type="text/html")
        return handler

    async def new_session(request):
        session = hub.create(base_url or str(request.url.origin()))
        return web.json_response({"ok": True, "sid": session.sid, "pin": session.pin,
                                  "link": session.link(), "link_no_pin": session.link(with_pin=False)})

    async def session_state(request):
        session = hub.get(request.match_info["sid"])
        if session is None:
            return web.json_response({"ok": False, "error": "not_found"}, status=404)
        return web.json_response({"ok": True, "state": session.state()})

    async def host_ws(request):
        session = hub.get(request.query.get("sid"))
        if session is None or not session.check_pin(request.query.get("pin")):
            raise web.HTTPForbidden(text="房间不存在或 PIN 不对")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        if session.host is not None:
            try:
                await session.host.close(code=4001, message=b"replaced")
            except Exception:
                pass
        session.host = ws
        session.touched = time.monotonic()
        log("haptics-share: 房主接入 " + session.sid)
        await _broadcast_guests(session, {"t": "state", "state": session.state()})
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                session.touched = time.monotonic()
                data = message.json() if hasattr(message, "json") else {}
                kind = data.get("t")
                if kind == "hello":
                    session.device = str(data.get("device") or "")[:60]
                    await _broadcast_guests(session, {"t": "state", "state": session.state()})
                elif kind == "ack":
                    session.last_ack = {"id": data.get("id"), "ok": bool(data.get("ok")),
                                        "raw": str(data.get("raw") or "")[:40]}
                    await _broadcast_guests(session, {"t": "result", "id": data.get("id"),
                                                      "ok": bool(data.get("ok")),
                                                      "raw": session.last_ack["raw"]})
                elif kind == "state":
                    session.device = str(data.get("device") or session.device)[:60]
                    await _broadcast_guests(session, {"t": "state", "state": session.state()})
        finally:
            if session.host is ws:
                session.host = None
                if session.stop_task:
                    session.stop_task.cancel()
                    session.stop_task = None
                log("haptics-share: 房主断开 " + session.sid)
                await _broadcast_guests(session, {"t": "closed", "reason": "房主已断开"})
        return ws

    async def guest_ws(request):
        session = hub.get(request.query.get("sid"))
        if session is None:
            raise web.HTTPForbidden(text="房间不存在")
        if session.locked():
            raise web.HTTPForbidden(text="PIN 试错过多，房间已锁定，请等几分钟或重新开房")
        if not session.check_pin(request.query.get("pin")):
            session.strike()
            raise web.HTTPForbidden(text="PIN 不对")
        if len(session.guests) >= MAX_GUESTS:
            raise web.HTTPForbidden(text="已经有人在控制了，请等对方退出")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        session.guests.add(ws)
        session.touched = time.monotonic()
        last_send = 0.0
        log("haptics-share: 控制者接入 " + session.sid)
        await ws.send_json({"t": "welcome", "state": session.state()})
        await _to_host(session, {"t": "guest_joined"})
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                session.touched = time.monotonic()
                data = message.json() if hasattr(message, "json") else {}
                kind = data.get("t")
                if kind == "stop":
                    await _to_host(session, {"t": "stop", "reason": "控制者停止"})
                    await ws.send_json({"t": "result", "id": None, "ok": True, "raw": "stop"})
                    continue
                if kind != "cmd":
                    continue
                now = time.monotonic()
                if (now - last_send) * 1000 < MIN_INTERVAL_MS:
                    await ws.send_json({"t": "denied", "reason": "太快了，慢一点"})
                    continue
                try:
                    level = float(data.get("level", 0))
                except (TypeError, ValueError):
                    level = 0.0
                try:
                    duration = int(data.get("duration_ms") or 1000)
                except (TypeError, ValueError):
                    duration = 1000
                level = max(0.0, min(level, MAX_LEVEL)) if MAX_LEVEL < 1.0 else min(max(level, 0.0), 1.0)
                duration = max(200, min(duration, MAX_DURATION_MS)) if MAX_DURATION_MS > 0 else max(200, duration)
                if session.host is None:
                    await ws.send_json({"t": "denied", "reason": "房主不在线"})
                    continue
                last_send = now
                session.seq += 1
                cmd_id = session.seq
                session.last_cmd = {"id": cmd_id, "level": level, "duration_ms": duration,
                                    "at": time.time()}
                sent = await _to_host(session, {"t": "cmd", "id": cmd_id, "level": level,
                                                "duration_ms": duration})
                if not sent:
                    await ws.send_json({"t": "denied", "reason": "房主不在线"})
                    continue
                await ws.send_json({"t": "sent", "id": cmd_id, "level": level,
                                    "duration_ms": duration})
                await _broadcast_guests(session, {"t": "state", "state": session.state()})
                if session.stop_task:
                    session.stop_task.cancel()
                session.stop_task = asyncio.create_task(
                    _watchdog(session, cmd_id, duration + 500))
        finally:
            session.guests.discard(ws)
            log("haptics-share: 控制者退出 " + session.sid)
            if not session.guests:
                await _to_host(session, {"t": "stop", "reason": "控制者已退出"})
        return ws

    async def _watchdog(session, cmd_id, delay_ms):
        """房主没在预期时间内回执，就补发一次停止，避免指令一直挂着。"""
        try:
            await asyncio.sleep(delay_ms / 1000.0)
        except asyncio.CancelledError:
            return
        if session.last_ack.get("id") != cmd_id:
            await _to_host(session, {"t": "stop", "reason": "回执超时"})
            log("haptics-share: 指令 " + str(cmd_id) + " 回执超时，已补发停止")

    async def qrlib(request):
        path = here / "static" / "qrcode.min.js"
        return web.Response(text=path.read_text(encoding="utf-8"),
                            content_type="application/javascript")

    async def health(request):
        return web.json_response({"ok": True, "sessions": len(hub.sessions)})

    app = web.Application()
    app.router.add_get("/", page("host.html"))
    app.router.add_get("/c", page("guest.html"))
    app.router.add_get("/qrcode.min.js", qrlib)
    app.router.add_post("/api/session", new_session)
    app.router.add_get("/api/session/{sid}", session_state)
    app.router.add_get("/ws/host", host_ws)
    app.router.add_get("/ws/guest", guest_ws)
    app.router.add_get("/healthz", health)
    app["hub"] = hub
    return app

def main(argv=None):
    """独立跑这个分享服务：python -m runtime.haptics.share"""
    import argparse
    import os

    parser = argparse.ArgumentParser(description="糯控分享控制服务")
    parser.add_argument("--host", default=os.environ.get("DZMM_SHARE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DZMM_SHARE_PORT", "8097")))
    parser.add_argument("--public", default=os.environ.get("DZMM_SHARE_PUBLIC", ""),
                        help="对外基址，例如 https://www.nuonuo.pw/haptics（不含结尾斜杠）")
    args = parser.parse_args(argv)
    app = create_app(base_url=args.public)
    log("haptics-share: 监听 " + args.host + ":" + str(args.port) + " 对外基址=" + (args.public or "(按请求推断)"))
    web.run_app(app, host=args.host, port=args.port, print=lambda message: None)


if __name__ == "__main__":
    main()
