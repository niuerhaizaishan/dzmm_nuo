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
import json
import os
import re
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
PROGRAM_TOKEN = os.environ.get("DZMM_SHARE_PROGRAM_TOKEN", "")  # 机器人（小小糯）用的令牌；没配就关掉程序通道
# 预设白名单：页面里定义的 id，服务端只做校验不透传细节。
PATTERN_IDS = {"tap", "pulse", "heartbeat", "ramp", "ramp_down", "stutter", "hold"}
MAX_PATTERN_MS = 60000       # 单条预设上限 60 秒
MAX_DURATION_MS_CAP = 60000  # 单条单脉冲上限（预设之外的兜底）
LEVEL_MIN_INTERVAL_MS = 150  # 实时强度消息的节流
# 剧情段落（beat）：一段"涨起来的震动"，跑完自动回落到背景层。
BEAT_RHYTHMS = {"steady", "breathe", "heartbeat", "wave", "stutter"}
BEAT_MAX_MS = 20000          # 单段上限 20 秒（屋主定的）
BEAT_MIN_MS = 800
# 群友白名单：房主页勾选的人能用 /震 /预设 /停。机器人每次指令都重读这个文件。
ALLOW_FILE = os.environ.get("DZMM_HAPTICS_ALLOW_FILE", "/app/data/haptics_allow.json")
ALLOW_MAX = 20               # 最多勾几个人
UID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
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
        self.allow_program = False   # 房主是否显式允许机器人控制
        self.program = None          # 机器人那条 WS
        self.last_cmd = {}
        self.last_ack = {}
        self.level = 0.0             # 当前实时强度（预设/长按执行期间可随时改）
        self.baseline = 0.0          # 背景层档位（0=关）
        self.beat = {}               # 最近一条剧情段落
        self.seq = 0
        self.stop_task = None

    # ---------- 展示 ----------

    def link(self, with_pin=True):
        query = f"sid={self.sid}" + (f"&pin={self.pin}" if with_pin else "")
        return f"{self.base_url}/c?{query}"

    def state(self):
        return {"host": self.host is not None, "guests": len(self.guests),
                "program": self.program is not None, "allow_program": self.allow_program,
                "device": self.device, "last_cmd": self.last_cmd,
                "last_ack": self.last_ack, "level": self.level,
                "baseline": self.baseline, "beat": self.beat,
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


def _program_ok(value):
    return bool(PROGRAM_TOKEN) and secrets.compare_digest(str(value or ""), PROGRAM_TOKEN)


def read_allowlist():
    """读群友白名单。坏文件/缺文件＝空表（宁可不给权限）。"""
    try:
        with open(ALLOW_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:                                          # noqa: BLE001
        return {"uids": [], "names": []}
    rows = data.get("uids") if isinstance(data, dict) else data
    names = data.get("names") if isinstance(data, dict) else []
    return {"uids": [str(item) for item in (rows or [])],
            "names": [str(item) for item in (names or [])]}


def write_allowlist(uids, names=None):
    """写群友白名单（原子替换）。返回 (新表, 错误)。"""
    clean = []
    for item in uids or []:
        value = str(item or "").strip().lower()
        if not value:
            continue
        if not UID_RE.match(value):
            return None, "有个 uid 格式不对"
        if value not in clean:
            clean.append(value)
    if len(clean) > ALLOW_MAX:
        return None, "最多勾 %d 个人" % ALLOW_MAX
    payload = {"uids": clean,
               "names": [str(item) for item in (names or [])][:len(clean)],
               "updated": time.strftime("%Y-%m-%d %H:%M")}
    # 注意：这个文件是**单文件 bind mount**，对它 os.replace 会 EBUSY —— 只能原地写。
    try:
        with open(ALLOW_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception as exc:                                   # noqa: BLE001
        log("haptics-share: 白名单写入失败 " + type(exc).__name__)
        return None, "写不进去（服务没挂载白名单文件？）"
    log("haptics-share: 白名单更新 " + str(len(clean)) + " 人")
    return {"uids": clean, "names": payload["names"]}, None



async def _relay_cmd(session, data):
    """夹紧参数并转发给房主。成功返回 (cmd_id, level, duration, None, pattern, total_ms)。"""
    try:
        level = float(data.get("level", 0))
    except (TypeError, ValueError):
        level = 0.0
    pattern = data.get("pattern") or ""
    if pattern and pattern not in PATTERN_IDS:
        return None, None, None, "未知预设", None, None
    try:
        duration = int(data.get("duration_ms") or 1000)
    except (TypeError, ValueError):
        duration = 1000
    total_ms = 0
    if pattern:
        try:
            total_ms = int(data.get("total_ms") or MAX_PATTERN_MS)
        except (TypeError, ValueError):
            total_ms = MAX_PATTERN_MS
        total_ms = max(200, min(total_ms, MAX_PATTERN_MS))
        duration = total_ms
        # 预设自带每一步的强度（那是节奏的一部分），所以 level 不参与；
        # 手动强度只走 level 通道，两者互不换算。
        level = 1.0
    level = max(0.0, min(level, MAX_LEVEL)) if MAX_LEVEL < 1.0 else min(max(level, 0.0), 1.0)
    if not pattern:
        duration = max(200, min(duration, MAX_DURATION_MS)) if MAX_DURATION_MS > 0 else max(200, min(duration, MAX_DURATION_MS_CAP))
    if session.host is None:
        return None, None, None, "房主不在线", None, None
    session.seq += 1
    cmd_id = session.seq
    session.last_cmd = {"id": cmd_id, "level": level, "duration_ms": duration, "at": time.time()}
    payload = {"t": "cmd", "id": cmd_id, "level": level, "duration_ms": duration}
    if pattern:
        payload["pattern"] = pattern
        payload["total_ms"] = total_ms
    if not await _to_host(session, payload):
        return None, None, None, "房主不在线", None, None
    return cmd_id, level, duration, None, pattern, total_ms


async def _relay_level(session, data):
    """实时强度：只改"下一条脉冲用多大"，本身不产生事件。成功返回 (level, None)。"""
    try:
        level = float(data.get("value", 0))
    except (TypeError, ValueError):
        return None, "强度不对"
    level = min(max(level, 0.0), MAX_LEVEL if MAX_LEVEL < 1.0 else 1.0)
    if session.host is None:
        return None, "房主不在线"
    session.level = level
    if not await _to_host(session, {"t": "level", "value": level}):
        return None, "房主不在线"
    return level, None


async def _relay_baseline(session, data):
    """背景层：一直轻嗡的底噪。0 = 关。"""
    try:
        value = float(data.get("value", 0))
    except (TypeError, ValueError):
        return None, "强度不对"
    value = min(max(value, 0.0), MAX_LEVEL if MAX_LEVEL < 1.0 else 1.0)
    if session.host is None:
        return None, "房主不在线"
    session.baseline = value
    if not await _to_host(session, {"t": "baseline", "value": value}):
        return None, "房主不在线"
    return value, None


async def _relay_beat(session, data):
    """剧情段落：一段涨起来的震动，跑完由房主页自己回落到底噪。"""
    rhythm = str(data.get("rhythm") or "steady")
    if rhythm not in BEAT_RHYTHMS:
        return None, "未知节奏"
    try:
        level = float(data.get("level", 0))
    except (TypeError, ValueError):
        return None, "强度不对"
    try:
        duration = int(data.get("duration_ms") or 6000)
    except (TypeError, ValueError):
        duration = 6000
    level = max(0.0, min(level, MAX_LEVEL if MAX_LEVEL < 1.0 else 1.0))
    duration = max(BEAT_MIN_MS, min(duration, BEAT_MAX_MS))
    if session.host is None:
        return None, "房主不在线"
    session.beat = {"level": level, "duration_ms": duration, "rhythm": rhythm,
                    "at": time.time()}
    if not await _to_host(session, {"t": "beat", "level": level,
                                    "duration_ms": duration, "rhythm": rhythm}):
        return None, "房主不在线"
    return session.beat, None


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
        last_level = 0.0
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
                if kind == "level":
                    now_lv = time.monotonic()
                    if (now_lv - last_level) * 1000 < LEVEL_MIN_INTERVAL_MS:
                        continue
                    last_level = now_lv
                    value, why = await _relay_level(session, data)
                    if why:
                        await ws.send_json({"t": "denied", "reason": why})
                        continue
                    await ws.send_json({"t": "level_ok", "value": value})
                    continue
                if kind == "baseline":
                    value, why = await _relay_baseline(session, data)
                    if why:
                        await ws.send_json({"t": "denied", "reason": why})
                        continue
                    await ws.send_json({"t": "baseline_ok", "value": value})
                    continue
                if kind == "beat":
                    beat, why = await _relay_beat(session, data)
                    if why:
                        await ws.send_json({"t": "denied", "reason": why})
                        continue
                    await ws.send_json({"t": "beat_ok", "beat": beat})
                    continue
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
                cmd_id, level, duration, reason, pattern, total_ms = await _relay_cmd(session, data)
                if reason:
                    await ws.send_json({"t": "denied", "reason": reason})
                    continue
                last_send = now
                sent = {"t": "sent", "id": cmd_id, "level": level, "duration_ms": duration}
                if pattern:
                    sent["pattern"] = pattern
                    sent["total_ms"] = total_ms
                await ws.send_json(sent)
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

    # ---------- 机器人（小小糯）通道 ----------

    async def program_allow(request):
        """房主页面上的「允许小小糯控制」开关。"""
        payload = await request.json()
        session = hub.get(payload.get("sid"))
        if session is None or not session.check_pin(payload.get("pin")):
            return web.json_response({"ok": False, "error": "not_found"}, status=403)
        session.allow_program = bool(payload.get("allow"))
        if not session.allow_program and session.program is not None:
            try:
                await session.program.close(code=4002, message=b"revoked")
            except Exception:
                pass
            session.program = None
            await _to_host(session, {"t": "stop", "reason": "机器人已撤回"})
        session.touched = time.monotonic()
        await _broadcast_guests(session, {"t": "state", "state": session.state()})
        log("haptics-share: 机器人许可=" + str(session.allow_program) + " " + session.sid)
        return web.json_response({"ok": True, "allow_program": session.allow_program})

    async def program_session(request):
        """机器人问：现在有哪个房间允许我控制？"""
        token = request.headers.get("X-Program-Token") or request.query.get("token", "")
        if not _program_ok(token):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        best = None
        for session in hub.sessions.values():
            if not (session.allow_program and session.host is not None):
                continue
            if best is None or session.touched > best.touched:
                best = session
        if best is None:
            return web.json_response({"ok": True, "session": None})
        return web.json_response({"ok": True, "session": {"sid": best.sid, "device": best.device,
                                                          "caps": best.state()["caps"]}})

    async def program_ws(request):
        if not _program_ok(request.query.get("token")):
            raise web.HTTPUnauthorized(text="令牌不对")
        session = hub.get(request.query.get("sid"))
        if session is None:
            raise web.HTTPForbidden(text="房间不存在")
        if not session.allow_program:
            raise web.HTTPForbidden(text="房主没有允许机器人控制")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        if session.program is not None and session.program is not ws:
            try:
                await session.program.close(code=4001, message=b"replaced")
            except Exception:
                pass
        session.program = ws
        session.graceful = False   # 客户端发 bye 表示"指令发完就走"，别再补停止
        session.touched = time.monotonic()
        last_send = 0.0
        last_level = 0.0
        log("haptics-share: 机器人接入 " + session.sid)
        await ws.send_json({"t": "welcome", "state": session.state()})
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                session.touched = time.monotonic()
                data = message.json() if hasattr(message, "json") else {}
                kind = data.get("t")
                if kind == "level":
                    now_lv = time.monotonic()
                    if (now_lv - last_level) * 1000 < LEVEL_MIN_INTERVAL_MS:
                        continue
                    last_level = now_lv
                    value, why = await _relay_level(session, data)
                    if why:
                        await ws.send_json({"t": "denied", "reason": why})
                        continue
                    await ws.send_json({"t": "level_ok", "value": value})
                    continue
                if kind == "baseline":
                    value, why = await _relay_baseline(session, data)
                    if why:
                        await ws.send_json({"t": "denied", "reason": why})
                        continue
                    await ws.send_json({"t": "baseline_ok", "value": value})
                    continue
                if kind == "beat":
                    beat, why = await _relay_beat(session, data)
                    if why:
                        await ws.send_json({"t": "denied", "reason": why})
                        continue
                    await ws.send_json({"t": "beat_ok", "beat": beat})
                    continue
                if kind == "stop":
                    await _to_host(session, {"t": "stop", "reason": "机器人停止"})
                    await ws.send_json({"t": "result", "id": None, "ok": True, "raw": "stop"})
                    continue
                if kind == "bye":
                    session.graceful = True
                    continue
                if kind != "cmd":
                    continue
                now = time.monotonic()
                if (now - last_send) * 1000 < MIN_INTERVAL_MS:
                    await ws.send_json({"t": "denied", "reason": "rate_limited"})
                    continue
                cmd_id, level, duration, reason, pattern, total_ms = await _relay_cmd(session, data)
                if reason:
                    await ws.send_json({"t": "denied", "reason": reason})
                    continue
                last_send = now
                sent = {"t": "sent", "id": cmd_id, "level": level, "duration_ms": duration}
                if pattern:
                    sent["pattern"] = pattern
                    sent["total_ms"] = total_ms
                await ws.send_json(sent)
                await _broadcast_guests(session, {"t": "state", "state": session.state()})
                if session.stop_task:
                    session.stop_task.cancel()
                session.stop_task = asyncio.create_task(
                    _watchdog(session, cmd_id, duration + 500))
        finally:
            if session.program is ws:
                session.program = None
                last_send = 0.0
                if not getattr(session, "graceful", False):
                    await _to_host(session, {"t": "stop", "reason": "机器人异常断开"})
                    log("haptics-share: 机器人异常断开，已补停止 " + session.sid)
                else:
                    log("haptics-share: 机器人正常收工 " + session.sid)
        return ws

    async def allowlist_api(request):
        """房主页的「群友权限」：读/写都能用，但要 sid + pin（等于房主本人）。"""
        if request.method == "GET":
            sid = request.query.get("sid")
            pin = request.query.get("pin")
        else:
            payload = await request.json()
            sid = payload.get("sid")
            pin = payload.get("pin")
            request["payload"] = payload
        session = hub.get(sid)
        if session is None or not session.check_pin(pin):
            return web.json_response({"ok": False, "error": "not_found"}, status=403)
        if request.method == "GET":
            return web.json_response({"ok": True, "allow": read_allowlist()})
        payload = request["payload"]
        allow, error = write_allowlist(payload.get("uids") or [], payload.get("names") or [])
        if error:
            return web.json_response({"ok": False, "error": error}, status=400)
        session.touched = time.monotonic()
        return web.json_response({"ok": True, "allow": allow})

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
    app.router.add_post("/api/program/allow", program_allow)
    app.router.add_get("/api/allow", allowlist_api)
    app.router.add_post("/api/allow", allowlist_api)
    app.router.add_get("/api/program/session", program_session)
    app.router.add_get("/ws/program", program_ws)
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
