"""无设备时用的假后端：接口与 ButtplugClient 一致，行为可观测。

用途：没有手机/Intiface 在场时，也能把「控制页 → 调度器 → 客户端」整条链路跑通、
写回归测试；--mock 起来的服务不会碰任何真实硬件。
"""
import time

from runtime.haptics.protocol import Device, DeviceFeature


class MockClient:
    def __init__(self, names=("LVS-Mock-01",)):
        self.connected = False
        self.url = ""
        self.server_name = "mock"
        self.sent = 0
        self.history = []
        self.devices = {}
        for index, name in enumerate(names):
            self.devices[index] = Device(index, name, name,
                                         [DeviceFeature(0, "motor", {"Vibrate": (0, 20)})])
        self._names = names

    async def connect(self, url, *, timeout=12):
        self.url = url
        self.connected = True
        self.devices = {index: Device(index, name, name,
                                      [DeviceFeature(0, "motor", {"Vibrate": (0, 20)})])
                        for index, name in enumerate(self._names)}
        self.history.append(("connect", url, time.time()))
        return {"ServerName": "mock", "MaxPingTime": 100}

    async def close(self):
        self.connected = False
        self.devices.clear()

    def device(self, index=None):
        if index is None:
            return next(iter(self.devices.values()), None)
        return self.devices.get(int(index))

    async def output(self, level, *, kind="Vibrate", index=None):
        device = self.device(index)
        if device is None:
            raise RuntimeError("没有已连接的设备")
        feature = device.preferred_feature(kind)
        low, high = feature.range_of(kind)
        value = int(round(low + (high - low) * max(0.0, min(1.0, float(level)))))
        self.sent += 1
        self.history.append(("output", device.display, value, time.time()))
        return device, value

    async def stop_all(self):
        if not self.connected:
            return False
        self.history.append(("stop", time.time()))
        return True

    async def request_device_list(self):
        return None

    async def start_scanning(self):
        return None

    async def stop_scanning(self):
        return None

    def status(self):
        return {"connected": self.connected, "url": self.url, "server": "mock",
                "sent": self.sent, "last_error": "",
                "devices": [device.to_json() for device in self.devices.values()],
                "history": [list(row) for row in self.history[-20:]]}
