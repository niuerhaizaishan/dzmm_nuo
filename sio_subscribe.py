"""DZMM Socket.IO 订阅器 — 监听 message:new，累积到 buffer，断线重连 + 补拉信号.

用法（在 bot.py 里）：
    sub = SioSubscriber(GROUPS)
    await sub.start()          # 建立长连接 + join 所有群，后台持续监听
    msgs = sub.drain(cid)      # 取某群累积的新消息（非阻塞）
    if sub.need_resync(cid):   # 断线重连后返回 True，主循环触发 HTTP 补拉

机制（前端逆向 + 实测确认，2026-08-22）：
    1. token 来自 GET /api/auth/token（需 dzmm.ai cookie）
    2. Socket.IO 连接，auth={token}，url 带 query `timestamp + syncMode=http`，
       transports=["polling","websocket"]——关键：必须 polling + syncMode=http 服务端才会推送，
       纯 websocket 会连上但收不到任何事件（已踩坑，实测验证）
    3. 连接后服务端自动推送 connected（含 groupChatLimits）→ message:joined（syncMode）→ message:new
    4. 监听 message:new → {chatroomId, message:{message_id, chatroom_id, sent_by, sent_at, content{text,type}, metadata}}
    5. python-socketio 自带 reconnection 断线重连；重连成功后置 resync 标记，主循环据此补拉
"""
import asyncio
import os
import sys
import json
import time

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if p not in ("", _ROOT)]  # 避开项目自己的 socketio.py
import token_store
import requests
import socketio
import aiohttp
import env_config

ORIGIN = env_config.get_dzmm_origin()
env_config.set_process_proxy_env()  # 设置/清空 HTTP(S)_PROXY + WS(S)_PROXY（直连时清空）


class SioSubscriber:
    def __init__(self, groups: list):
        self.groups = list(groups)
        self._buffer = {}       # cid -> [msg, ...]（群聊 message:new 推送累积）
        self._private_buffer = {}  # cid -> [msg, ...]（私聊 one_on_one 消息累积）
        self._lock = asyncio.Lock()
        self._resync = set()    # 需要 HTTP 补拉的群（断线重连后置入）
        self._sio = None
        self._http = None
        self._connected = asyncio.Event()
        self._last_connect_ts = 0.0
        self._reconnect_count = 0

    # ---------- 对外接口 ----------
    def drain(self, cid: str) -> list:
        """非阻塞取某群累积的新消息（一次性取空）。"""
        return self._buffer.pop(cid, [])

    def drain_private(self) -> dict:
        """非阻塞取所有私聊累积的新消息，返回 {cid: [msg, ...]}（一次性取空）。"""
        out = dict(self._private_buffer)
        self._private_buffer.clear()
        return out

    def need_resync(self, cid: str) -> bool:
        """断线重连后返回 True，主循环应触发一次 HTTP 补拉；消费后清除标记。"""
        return cid in self._resync

    def clear_resync(self, cid: str) -> None:
        self._resync.discard(cid)

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    # ---------- 内部 ----------
    def _get_token(self) -> str:
        # 优先从 token JSON 直接 decode access_token（renew_token 持续写回，是新鲜的），
        # 避免撞验证码时 /api/auth/token 返回 418 导致 Socket.IO 重连拿不到 token。
        tok = self._token_from_json()
        if tok:
            return tok
        # 兜底：调 /api/auth/token（正常状态下两者等价）
        try:
            cookies = token_store.get_cookie_dict()
            r = requests.get(ORIGIN + "/api/auth/token", cookies=cookies, timeout=15,
                             proxies=env_config.get_proxies())
            return (r.json() or {}).get("access_token", "")
        except Exception:
            return ""

    def _token_from_json(self) -> str:
        """从 token JSON 的 sb-rls-auth-token cookie 里 decode 出未过期的 access_token。"""
        try:
            import base64
            c = token_store.get_cookie("sb-rls-auth-token")
            if not c:
                return ""
            v = c.get("value", "")
            payload = v[len("base64-"):] if v.startswith("base64-") else v
            payload += "=" * (-len(payload) % 4)
            d = json.loads(base64.b64decode(payload))
            # 只要 access_token 未过期（留 60s 余量）
            if d.get("expires_at", 0) - time.time() > 60:
                return d.get("access_token", "")
        except Exception:
            pass
        return ""

    def _token_stale(self) -> bool:
        """access_token 已过期 或 renew 撞验证码退避中 → 此时重连也拿不到 token，应拉长退避。"""
        try:
            import base64
            c = token_store.get_cookie("sb-rls-auth-token")
            if c:
                v = c.get("value", "")
                payload = v[len("base64-"):] if v.startswith("base64-") else v
                payload += "=" * (-len(payload) % 4)
                d = json.loads(base64.b64decode(payload))
                if d.get("expires_at", 0) - time.time() < 0:
                    return True  # access_token 已过期
        except Exception:
            pass
        # renew_token 撞验证码时的退避信号文件（renew_browser_backoff.json）
        # 检查时间戳是否在 1 小时退避期内（过期则视为已恢复，避免风控解除后仍卡 1 小时退避）
        try:
            bf = _ROOT / "renew_browser_backoff.json"
            if bf.exists():
                data = json.loads(bf.read_text(encoding="utf-8"))
                if time.time() - data.get("ts", 0) < 3600:
                    return True
        except Exception:
            pass
        return False

    async def start(self) -> None:
        """建立长连接，后台持续监听 message:new。断线自动重连。"""
        self._http = aiohttp.ClientSession(trust_env=True)
        retry_delay = 5
        while True:
            try:
                await self._connect()
                retry_delay = 5  # 连接成功，重置退避
            except Exception as e:
                print(f"[sio] 连接失败: {str(e)[:80]}，{retry_delay}s 后重试")
                await asyncio.sleep(retry_delay)
                # token 过期/撞验证码时拉长退避（重连也拿不到 token，别反复打接口加剧风控）
                cap = 3600 if self._token_stale() else 300
                retry_delay = min(retry_delay * 2, cap)
                continue
            # 等 namespace 连接确认（on_connect → _connected.set()）
            try:
                await asyncio.wait_for(self._connected.wait(), timeout=10)
            except asyncio.TimeoutError:
                print("[sio] namespace 连接超时，清理后重试")
                await self._cleanup()
                continue
            # 连接稳定，等断开（on_disconnect → _connected.clear()）
            while self._connected.is_set():
                await asyncio.sleep(1)
            print("[sio] 连接已断开，准备重连")
            await self._cleanup()

    async def _connect(self) -> None:
        token = self._get_token()
        if not token:
            raise RuntimeError("token 获取失败")
        self._sio = socketio.AsyncClient(
            http_session=self._http,
            reconnection=False,  # 手动重连，避免与 start() 循环冲突
        )
        self._connected.clear()

        @self._sio.on("connect")
        async def _on_connect():
            self._last_connect_ts = time.time()
            self._connected.set()
            # 连接成功（含首次连接和每次重连）后标记需补拉，把连接窗口期漏掉的消息补回来
            # （bot 重启时 Socket.IO 首次连接可能超时重连，期间发的消息会漏，靠这次 HTTP 补拉兜住）
            for cid in self.groups:
                self._resync.add(cid)
            print("[sio] ✅ 已连接（服务端自动推送所属群消息，无需 join）")

        @self._sio.on("disconnect")
        async def _on_disconnect():
            self._connected.clear()
            # 断线：所有群标记为需补拉
            for cid in self.groups:
                self._resync.add(cid)
            print("[sio] ⚠️ 已断开，标记需补拉")

        @self._sio.on("message:new")
        async def _on_new(d):
            try:
                cid = d.get("chatroomId", "")
                msg = d.get("message") or {}
                if not cid:
                    return
                async with self._lock:
                    if cid in self.groups:
                        self._buffer.setdefault(cid, []).append(msg)
                    else:
                        # 私聊（one_on_one）消息，累积到私聊 buffer
                        self._private_buffer.setdefault(cid, []).append(msg)
            except Exception:
                pass

        # 关键：必须走 polling transport + syncMode=http query，服务端才会推送
        # connected / message:joined / message:new。纯 websocket 会连上但收不到推送。
        ts = str(int(time.time() * 1000))
        url = ORIGIN + "/?timestamp=" + ts + "&syncMode=http"
        await self._sio.connect(
            url,
            socketio_path="/ws/matching",
            transports=["polling", "websocket"],
            auth={"token": token},
            headers={"Origin": ORIGIN, "User-Agent": "Mozilla/5.0 DZMM-Agent/1.0"},
            wait=False,  # 不等待 namespace 连接（后台连接），避免 wait_timeout 超时抛异常
        )

    async def _cleanup(self) -> None:
        """断开当前 sio 连接（不关 http session，session 复用）。"""
        try:
            if self._sio:
                await self._sio.disconnect()
        except Exception:
            pass

    async def stop(self) -> None:
        try:
            if self._sio:
                await self._sio.disconnect()
        except Exception:
            pass
        try:
            if self._http:
                await self._http.close()
        except Exception:
            pass
