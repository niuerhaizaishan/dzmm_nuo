"""小小糯作为「程序控制者」连分享服务：发一条指令、拿回回执、断开。

刻意做成「每次指令一条短连接」：指令频率很低（手动或剧情事件），换来的是不用维护
长连接状态、不受房主页面重连影响；单次成本是一次本机 WSS 握手（几十毫秒）。
"""
import asyncio
import json
import os

from aiohttp import ClientSession

from runtime.obs import log

DEFAULT_BASE = "https://www.nuonuo.pw/haptics"
TIMEOUT = 4.0


def _base():
    return (os.environ.get("DZMM_SHARE_URL") or DEFAULT_BASE).rstrip("/")


def _token():
    return os.environ.get("DZMM_SHARE_PROGRAM_TOKEN", "")


def enabled():
    return bool(_token())


class ShareClient:
    def __init__(self, base_url=None, token=None, *, timeout=TIMEOUT):
        self.base_url = (base_url or _base()).rstrip("/")
        self.token = token if token is not None else _token()
        self.timeout = timeout
        self.last_error = ""

    @property
    def ws_url(self):
        root = self.base_url
        if root.startswith("https://"):
            return "wss://" + root[len("https://"):]
        if root.startswith("http://"):
            return "ws://" + root[len("http://"):]
        return root

    async def _find_session(self):
        """问服务端：现在哪个房间允许机器人控制。"""
        async with ClientSession() as session:
            async with session.get(self.base_url + "/api/program/session",
                                   headers={"X-Program-Token": self.token},
                                   timeout=self.timeout) as response:
                if response.status == 401:
                    self.last_error = "令牌不对"
                    return None
                if response.status != 200:
                    self.last_error = "服务返回 " + str(response.status)
                    return None
                data = await response.json()
                return (data or {}).get("session")

    async def _read_until(self, ws, kinds):
        while True:
            message = await ws.receive(timeout=self.timeout)
            if message.type.name in ("CLOSE", "CLOSED", "ERROR"):
                return None
            try:
                data = json.loads(message.data)
            except (TypeError, ValueError):
                continue
            if data.get("t") in kinds:
                return data

    async def command(self, level, duration_ms, pattern=None, total_ms=None):
        """下发一条振动指令。返回 {"ok": bool, "detail": str}。

        pattern 给了就播预设节奏（房主页本地按时序跑 total_ms 毫秒）；
        这时 level 不参与（预设自带每一步的强度）。
        """
        if not self.token:
            return {"ok": False, "detail": "机器人通道未启用（缺 DZMM_SHARE_PROGRAM_TOKEN）"}
        target = await self._find_session()
        if target is None:
            return {"ok": False, "detail": self.last_error or "没有已允许的房间"}
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                        f"{self.ws_url}/ws/program?sid={target['sid']}&token={self.token}",
                        timeout=self.timeout) as ws:
                    await ws.receive(timeout=self.timeout)  # welcome
                    payload = {"t": "cmd", "level": level, "duration_ms": duration_ms}
                    if pattern:
                        payload["pattern"] = pattern
                        payload["total_ms"] = int(total_ms or duration_ms or 60000)
                    await ws.send_json(payload)
                    reply = await self._read_until(ws, {"sent", "denied"})
                    if reply is None:
                        return {"ok": False, "detail": "设备端没有回应"}
                    if reply["t"] == "denied":
                        return {"ok": False, "detail": str(reply.get("reason") or "被拒绝")}
                    # 正常收工：告诉服务端别把这次断开当成异常去补停止
                    await ws.send_json({"t": "bye"})
                    return {"ok": True, "detail": "", "level": reply.get("level"),
                            "duration_ms": reply.get("duration_ms"),
                            "pattern": reply.get("pattern")}
        except asyncio.TimeoutError:
            return {"ok": False, "detail": "连接超时"}
        except Exception as exc:  # 网络抖动不该把指令处理打挂
            log("haptics-bot: 下发失败 " + type(exc).__name__)
            return {"ok": False, "detail": "连接失败（" + type(exc).__name__ + "）"}

    async def _send(self, payload, want):
        """发一条小程序消息，等一个回执 kind。成功返回 {"ok":True,...}。"""
        if not self.token:
            return {"ok": False, "detail": "机器人通道未启用"}
        target = await self._find_session()
        if target is None:
            return {"ok": False, "detail": self.last_error or "没有已允许的房间"}
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                        f"{self.ws_url}/ws/program?sid={target['sid']}&token={self.token}",
                        timeout=self.timeout) as ws:
                    await ws.receive(timeout=self.timeout)      # welcome
                    await ws.send_json(payload)
                    reply = await self._read_until(ws, want)
                    await ws.send_json({"t": "bye"})
                    if reply is None:
                        return {"ok": False, "detail": "设备端没有回应"}
                    if reply.get("t") == "denied":
                        return {"ok": False, "detail": str(reply.get("reason") or "被拒绝")}
                    return dict(reply, ok=True)
        except asyncio.TimeoutError:
            return {"ok": False, "detail": "连接超时"}
        except Exception as exc:                               # noqa: BLE001
            log("haptics-bot: 下发失败 " + type(exc).__name__)
            return {"ok": False, "detail": "连接失败（" + type(exc).__name__ + "）"}

    async def baseline(self, value):
        """设背景层档位（0 关）。"""
        return await self._send({"t": "baseline", "value": value},
                                {"baseline_ok", "denied"})

    async def beat(self, level, duration_ms, rhythm="steady"):
        """下一条剧情段落：一段涨起来的震动，跑完房主页自己回落到背景。"""
        return await self._send({"t": "beat", "level": level,
                                 "duration_ms": duration_ms, "rhythm": rhythm},
                                {"beat_ok", "denied"})

    async def stop(self, reason="bot"):
        if not self.token:
            return {"ok": False, "detail": "机器人通道未启用"}
        target = await self._find_session()
        if target is None:
            return {"ok": False, "detail": self.last_error or "没有已允许的房间"}
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                        f"{self.ws_url}/ws/program?sid={target['sid']}&token={self.token}",
                        timeout=self.timeout) as ws:
                    await ws.receive(timeout=self.timeout)
                    await ws.send_json({"t": "stop", "reason": reason})
                    await self._read_until(ws, {"result"})
                    await ws.send_json({"t": "bye"})
                    return {"ok": True, "detail": ""}
        except Exception as exc:
            return {"ok": False, "detail": "连接失败（" + type(exc).__name__ + "）"}


_CLIENT = None


def client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = ShareClient()
    return _CLIENT
