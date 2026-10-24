"""触觉（Lovense / Buttplug）接入：协议客户端、指令调度器、控制服务。

链路：小小糯 → Scheduler（限幅/去重/限流/自动停止）→ ButtplugClient → 手机上的
Intiface Central → 蓝牙 → 设备。服务端只做出站连接，不接收任何外部入站。
"""
from runtime.haptics.protocol import ButtplugClient, Device, DeviceFeature, HapticsUnavailable
from runtime.haptics.scheduler import Scheduler

__all__ = ["ButtplugClient", "Device", "DeviceFeature", "HapticsUnavailable", "Scheduler"]
