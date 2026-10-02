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
import inspect
import os
import sys
import json
import time
import uuid
from collections import OrderedDict

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
        # P12-J：**跨来源去重**。Webhook（平台直推）与 Socket.IO（侧车账号推送）
        # 是两条独立的路，同一条消息可能两边都到 —— 内容一模一样、message_id 相同。
        # 不去重的话主循环会把它当成两条新消息 ⇒ 机器人**回两次**。
        # 主循环那边原有的去重只认"上一条 last_id"，挡不住这种重复。
        self._seen_ids: "OrderedDict[str, float]" = OrderedDict()
        self._seen_cap = 500

    # ---------- 对外接口 ----------
    def _dup(self, msg: dict) -> bool:
        """True = 这条 message_id 已经见过（跨来源重复），应丢弃。"""
        mid = str((msg or {}).get("message_id") or "")
        if not mid:
            return False
        if mid in self._seen_ids:
            return True
        self._seen_ids[mid] = time.time()
        while len(self._seen_ids) > self._seen_cap:
            self._seen_ids.popitem(last=False)
        return False

    def push(self, cid: str, msg: dict) -> int:
        """把一条消息放进这个群的待处理队列，返回入队后条数。

        P12-J：Webhook 收到平台直推的消息时用它入队 —— 于是"读消息"有两条来源
        （Socket.IO 与 Webhook），而主循环那边一行都不用改（`drain` 取的是同一个队列）。
        重复来源的消息在这里被丢掉（见 `_dup`）。
        """
        if self._dup(msg):
            return len(self._buffer.get(cid) or [])
        # P12-J：打来源标记，便于"收消息到底走哪条路"可证（webhook vs socket）
        if isinstance(msg, dict) and not msg.get("_source"):
            msg["_source"] = "sio"
        buf = self._buffer.setdefault(cid, [])
        buf.append(msg)
        return len(buf)

    def requeue(self, cid: str, msgs: list) -> int:
        """把"这一轮没轮到处理"的消息放回队列（**不做去重**）。

        为什么需要：主循环每轮只处理**一条**命令（`cmd` 优先，先到先处理），
        而 `drain()` 会把整个 buffer 取空 —— 同一批里剩下的命令就这么被静默丢掉了。
        2026-09-27 现场：紫苑发 `/运势` 没反应，因为同一批里先有一条 `/写诗`，
        主循环挑走 `/写诗` 后 `last_id` 直接跳到批尾，`/运势` 再没人看过。
        放回去时**要绕过去重表** —— 它们不是重复，只是还没轮到。
        """
        if not msgs:
            return len(self._buffer.get(cid) or [])
        buf = self._buffer.setdefault(cid, [])
        for m in msgs:
            # 打标记：主循环的 `last_id` 过滤会丢掉"批尾那条"，
            # 而被放回队列的往往正好是批尾（它就是最新的一条）
            if isinstance(m, dict):
                m["_requeued"] = True
            buf.append(m)
        return len(buf)

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

    # ---------- 发送（P12-AZ） ----------
    async def send_text(self, cid: str, user_id: str, text: str) -> str:
        """用**当前这条长连接**替账号发一条消息。返回 message_id；这条连接不可用时返回 ""。

        为什么要它（P12-AZ）：账号的另一条发送路 `socketio.DZMMSocket`（HTTP）每次都得
        重新握手，而握手会撞 `connect=400 {"code":1,"message":"Session ID unknown"}`
        —— 实测要重试 4~6 次才成。这条连接是**读消息用的那条**，早就建立好了，
        还带 `python-socketio` 的断线重连。

        ⚠️ 返回值只代表"emit 出去了"，**不代表消息落地**：平台上 emit 成功 ≠ 群里有。
        调用方必须去群里读回（`bot.Sender._verify_landed`）。
        """
        if not self._sio or not self._connected.is_set():
            return ""
        mid = str(uuid.uuid4())
        msg = {"message_id": mid, "chatroom_id": cid, "sent_by": user_id,
               "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
               "content": {"type": "text", "text": text}}
        try:
            # python-socketio 的 AsyncClient.emit 是协程；万一换成同步实现也别炸
            r = self._sio.emit("message:send", {"chatroomId": cid, "message": msg})
            if inspect.isawaitable(r):
                await r
        except Exception as e:                                  # noqa: BLE001
            print("[sio] ⚠️ emit 发送失败: " + str(e)[:60])
            return ""
        return mid

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
        """会话是不是真失效 → 决定重连退避拉多长（True = 拉长到 1 小时）。

        P12-AJ 修正：**不再看 access_token 的 `expires_at`**。
        实测（2026-09-29）：`/api/auth/token` 只是回显会话里当前的 access_token，
        access_token 一过期它就 401，但**业务调用往往照常工作**（业务侧 unauthorized
        全程只报过 6 次）。拿 exp 当"会话失效"，会在整整一小时内把 Socket.IO 的
        重连退避顶到 3600 秒 —— 那段时间读消息只能靠 HTTP 兜底，正是"机器人变慢/漏消息"
        观感的来源之一。
        现在只看 renew_token 写的**业务探针**结果（`data/session_state.json`）。

        ⚠️ P12-AR：只认 `probe == "unauth"` 为死。`probe` 还有 "error"/"backoff"
        两种"这一轮没验证会话"的取值，而它们都会让 `alive` 为 False ——
        按 `alive is False` 判的话，一次验证码退避就会把重连退避顶到 3600 秒，
        又变回 P12-AJ 刚修掉的那个坑。
        """
        try:
            st = env_config.data_dir() / "session_state.json"
            if st.exists():
                d = json.loads(st.read_text(encoding="utf-8"))
                if str(d.get("probe")) == "unauth" \
                        and time.time() - float(d.get("at") or 0) < 1800:
                    return True
        except Exception:
            pass
        # renew_token 撞验证码时的退避信号文件（路径见 `env_config.renew_backoff_file()`）
        # 检查时间戳是否在 1 小时退避期内（过期则视为已恢复，避免风控解除后仍卡 1 小时退避）
        # ⚠️ P12-AI：两边必须读**同一个路径** —— 它现在在挂载的 `data/` 下
        # （原先 /app 根下，容器一重建就丢，退避形同虚设）。
        try:
            bf = env_config.renew_backoff_file()
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
                        # P12-J：走 push() 而不是直接 append —— 它带走跨来源去重
                        # （webhook 可能已经送过同一条）
                        self.push(cid, msg)
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
