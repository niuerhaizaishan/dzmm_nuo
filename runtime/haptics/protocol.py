"""Buttplug 协议 v4 最小客户端（只依赖 aiohttp，不引入 websockets/pydantic）。

为什么自己写而不是用 buttplug-py：机器人镜像的依赖是钉死版本重建的（见 requirements.txt
顶部注释），多引 websockets + pydantic 两个包就多一份升级风险；而我们只需要四件事——
握手、列设备、下发输出、停止。协议本身是 JSON over WebSocket，一百多行足够，且能对着
自带假服务器测试。

协议要点（对照 buttplug-py 的 _messages 实现核对过）：
  客户端 → {"Id":1,"MessageType":"RequestServerInfo","ClientName":"...",
            "ProtocolVersionMajor":4,"ProtocolVersionMinor":0}
  服务器 → {"Id":1,"MessageType":"ServerInfo","MaxPingTime":100,...}
  设备上线 → {"MessageType":"DeviceAdded","DeviceIndex":0,"DeviceName":"LVS-...",
              "DeviceFeatures":{"0":{"FeatureIndex":0,"Output":{"Vibrate":{"Value":[0,20]}}}}}
  下发输出 → {"Id":n,"MessageType":"OutputCmd","DeviceIndex":0,"FeatureIndex":0,
              "Command":{"Vibrate":{"Value":10}}}
  停止     → {"Id":n,"MessageType":"StopCmd","Outputs":true,"Inputs":false}
"""
import asyncio
import json
import time

from aiohttp import ClientSession, WSMsgType

from runtime.obs import log

PROTOCOL_MAJOR = 4
PROTOCOL_MINOR = 0
DEFAULT_PING_MS = 30_000


class HapticsUnavailable(RuntimeError):
    """连不上设备端（手机上的 Intiface 没开、地址不对、断网）。"""


class DeviceFeature:
    def __init__(self, index, description, outputs):
        self.index = index
        self.description = description or ""
        self.outputs = outputs or {}

    def range_of(self, kind):
        value = self.outputs.get(kind)
        if not value:
            return None
        low, high = value
        return int(low), int(high)

    def to_json(self):
        return {"index": self.index, "description": self.description,
                "outputs": {key: list(value) for key, value in self.outputs.items()}}


class Device:
    def __init__(self, index, name, display, features):
        self.index = index
        self.name = name
        self.display = display or name
        self.features = features

    def kinds(self):
        found = []
        for feature in self.features:
            for kind in feature.outputs:
                if kind not in found:
                    found.append(kind)
        return found

    def preferred_feature(self, kind):
        for feature in self.features:
            if kind in feature.outputs:
                return feature
        return None

    def to_json(self):
        return {"index": self.index, "name": self.name, "display": self.display,
                "kinds": self.kinds(), "features": [f.to_json() for f in self.features]}


def _parse_features(raw):
    features = []
    for key, value in (raw or {}).items():
        try:
            index = int(value.get("FeatureIndex", key))
        except (TypeError, ValueError):
            index = 0
        outputs = {}
        for kind, definition in (value.get("Output") or {}).items():
            limit = (definition or {}).get("Value") or [0, 20]
            try:
                outputs[str(kind)] = (int(limit[0]), int(limit[1]))
            except (TypeError, ValueError, IndexError):
                outputs[str(kind)] = (0, 20)
        features.append(DeviceFeature(index, value.get("FeatureDescription"), outputs))
    return features


class ButtplugClient:
    """连到设备端（Intiface）的 WS 客户端；只发命令，不接收入站连接。"""

    def __init__(self, name="nuonuo-haptics"):
        self.client_name = name
        self.devices = {}
        self.server_name = ""
        self.max_ping_ms = DEFAULT_PING_MS
        self.connected = False
        self.url = ""
        self.last_error = ""
        self.sent = 0
        self._session = None
        self._ws = None
        self._id = 0
        self._reader = None
        self._pinger = None
        self._ok_waiters = {}
        self._scanning = False
        self._closed = False

    def _next_id(self):
        self._id += 1
        return self._id

    async def connect(self, url, *, timeout=12):
        await self.close()
        self._closed = False
        self.url = url
        self._session = ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                url, timeout=timeout, heartbeat=None, max_msg_size=4 * 1024 * 1024)
        except Exception as exc:
            await self.close()
            self.last_error = type(exc).__name__
            raise HapticsUnavailable("连不上设备端（" + type(exc).__name__ + "）") from exc
        self._reader = asyncio.create_task(self._read_loop())
        try:
            info = await asyncio.wait_for(self._handshake(), timeout)
        except Exception as exc:
            await self.close()
            self.last_error = type(exc).__name__
            raise HapticsUnavailable("设备端握手失败（" + type(exc).__name__ + "）") from exc
        self.connected = True
        self.server_name = info.get("ServerName") or ""
        try:
            self.max_ping_ms = max(1000, int(info.get("MaxPingTime") or DEFAULT_PING_MS))
        except (TypeError, ValueError):
            self.max_ping_ms = DEFAULT_PING_MS
        self._pinger = asyncio.create_task(self._ping_loop())
        log("haptics: 已连接设备端 " + url + "（" + (self.server_name or "Intiface") + "）")
        await self.request_device_list()
        await self.start_scanning()
        return info

    async def _handshake(self):
        message_id = self._next_id()
        future = asyncio.get_running_loop().create_future()
        self._ok_waiters[message_id] = future
        await self._send(message_id, "RequestServerInfo", ClientName=self.client_name,
                         ProtocolVersionMajor=PROTOCOL_MAJOR, ProtocolVersionMinor=PROTOCOL_MINOR)
        info = await future
        if int(info.get("ProtocolVersionMajor") or 0) < PROTOCOL_MAJOR:
            raise HapticsUnavailable("设备端协议版本过低，请升级 Intiface Central")
        return info

    async def _send(self, message_id, message_type, **payload):
        if not self._ws or self._ws.closed:
            raise HapticsUnavailable("设备端已断开")
        body = {"Id": message_id, "MessageType": message_type}
        body.update(payload)
        try:
            await self._ws.send_str(json.dumps(body, ensure_ascii=False))
        except HapticsUnavailable:
            raise
        except Exception as exc:
            # 连接中途断掉时统一报「不可用」，避免把 ConnectionResetError 之类漏给调用方。
            self.connected = False
            raise HapticsUnavailable("发送到设备端失败（" + type(exc).__name__ + "）") from exc

    async def _read_loop(self):
        try:
            async for message in self._ws:
                if message.type == WSMsgType.TEXT:
                    self._dispatch(message.data)
                elif message.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = type(exc).__name__
        finally:
            if not self._closed:
                self.connected = False
                self.devices.clear()
                log("haptics: 设备端连接已断开（" + (self.last_error or "peer closed") + "）")

    def _dispatch(self, raw):
        try:
            data = json.loads(raw)
        except ValueError:
            return
        kind = data.get("MessageType")
        message_id = data.get("Id")
        if kind == "Ok":
            future = self._ok_waiters.pop(message_id, None)
            if future and not future.done():
                future.set_result({})
            return
        if kind == "ServerInfo":
            future = self._ok_waiters.pop(message_id, None)
            if future and not future.done():
                future.set_result(data)
            return
        if kind == "Error":
            future = self._ok_waiters.pop(message_id, None)
            if future and not future.done():
                future.set_exception(HapticsUnavailable(str(data.get("ErrorMessage") or "设备端报错")))
            return
        if kind == "DeviceAdded":
            device = Device(int(data.get("DeviceIndex", 0)), data.get("DeviceName") or "",
                            data.get("DeviceDisplayName"),
                            _parse_features(data.get("DeviceFeatures")))
            self.devices[device.index] = device
            log("haptics: 设备上线 " + device.display + "（" + ",".join(device.kinds()) + "）")
            return
        if kind == "DeviceRemoved":
            self.devices.pop(int(data.get("DeviceIndex", 0)), None)
            return
        if kind == "DeviceList":
            self.devices.clear()
            for index, value in (data.get("Devices") or {}).items():
                device = Device(int(value.get("DeviceIndex", index)), value.get("DeviceName") or "",
                                value.get("DeviceDisplayName"),
                                _parse_features(value.get("DeviceFeatures")))
                self.devices[device.index] = device
            return
        if kind == "Ping":
            asyncio.create_task(self._send(self._next_id(), "Ping"))
            return

    async def _ping_loop(self):
        interval = max(1.0, self.max_ping_ms / 3000.0)
        try:
            while self.connected:
                await asyncio.sleep(interval)
                try:
                    await self._send(self._next_id(), "Ping")
                except HapticsUnavailable:
                    return
        except asyncio.CancelledError:
            raise

    async def request_device_list(self):
        await self._send(self._next_id(), "RequestDeviceList")

    async def start_scanning(self):
        if self._scanning:
            return
        await self._send(self._next_id(), "StartScanning")
        self._scanning = True

    async def stop_scanning(self):
        if not self._scanning:
            return
        await self._send(self._next_id(), "StopScanning")
        self._scanning = False

    def device(self, index=None):
        if index is None:
            return next(iter(self.devices.values()), None)
        return self.devices.get(int(index))

    async def output(self, level, *, kind="Vibrate", index=None):
        """把 0..1 的强度映射到设备自身区间后下发；返回 (device, value)。"""
        if not self.connected:
            raise HapticsUnavailable("设备端未连接")
        device = self.device(index)
        if device is None:
            raise HapticsUnavailable("没有已连接的设备")
        feature = device.preferred_feature(kind)
        if feature is None:
            raise HapticsUnavailable("这台设备不支持 " + kind)
        low, high = feature.range_of(kind)
        value = int(round(low + (high - low) * max(0.0, min(1.0, float(level)))))
        await self._send(self._next_id(), "OutputCmd", DeviceIndex=device.index,
                         FeatureIndex=feature.index, Command={kind: {"Value": value}})
        self.sent += 1
        return device, value

    async def stop_all(self):
        if not self._ws or self._ws.closed:
            return False
        try:
            await self._send(self._next_id(), "StopCmd", Outputs=True, Inputs=False)
            return True
        except HapticsUnavailable:
            return False

    async def close(self):
        self._closed = True
        self.connected = False
        self.devices.clear()
        # 顺序很重要：先让 aiohttp 正常走完关闭握手，再取消读协程。
        # 反过来先取消 reader，关闭帧发不出去，对端 handler 会一直挂着等我们。
        if self._ws is not None and not self._ws.closed:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=3)
            except Exception:
                pass
        self._ws = None
        for task in (self._pinger, self._reader):
            if task:
                task.cancel()
        pending = [task for task in (self._pinger, self._reader) if task]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=3)
            except Exception:
                pass
        self._pinger = self._reader = None
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        for future in self._ok_waiters.values():
            if not future.done():
                future.cancel()
        self._ok_waiters.clear()

    def status(self):
        return {"connected": self.connected, "url": self.url,
                "server": self.server_name, "sent": self.sent,
                "last_error": self.last_error,
                "devices": [device.to_json() for device in self.devices.values()]}
