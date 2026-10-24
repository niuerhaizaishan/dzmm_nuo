"""Buttplug v4 客户端：握手、设备解析、输出映射、停止、错误处理。

这里用「假 ws 对象」而不是真 socket：仓库的 conftest 会导入 bot（带 Playwright / socket.io
线程），这种情况下再起真 aiohttp WS 服务器、关停时会互相卡住（纯 HTTP 的测试不受影响）。
真实 socket 的行为由阶段 0 的真机脚本验证（见 docs/Lovense接入-方案-2026-10-24.md）。
"""
import asyncio
import json

import pytest

from runtime.haptics.protocol import ButtplugClient, HapticsUnavailable

DEFAULT_FEATURES = {"0": {"FeatureIndex": 0, "FeatureDescription": "vibrate",
                          "Output": {"Vibrate": {"Value": [0, 20]}}}}


class FakeWS:
    """模拟 aiohttp 的 WebSocketResponse：记录发出去的消息。"""

    def __init__(self):
        self.sent = []
        self.closed = False

    async def send_str(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        self.closed = True


def connected_client(features=None):
    """造一个「已连上」的客户端，ws 换成假对象；features 为 None 表示没有设备。"""
    client = ButtplugClient("test")
    ws = FakeWS()
    client._ws = ws
    client.connected = True
    if features is not None:
        client._dispatch(json.dumps({"MessageType": "DeviceAdded", "DeviceIndex": 0,
                                     "DeviceName": "LVS-Fake", "DeviceDisplayName": None,
                                     "DeviceFeatures": features}))
    return client, ws


def test_device_added_parses_features_and_ranges():
    client, ws = connected_client(DEFAULT_FEATURES)
    device = client.device()
    assert device.display == "LVS-Fake"
    assert device.kinds() == ["Vibrate"]
    assert device.preferred_feature("Vibrate").range_of("Vibrate") == (0, 20)
    assert device.to_json()["features"][0]["outputs"] == {"Vibrate": [0, 20]}


def test_missing_display_name_falls_back_to_device_name():
    client = ButtplugClient()
    client._ws = FakeWS()
    client.connected = True
    client._dispatch(json.dumps({"MessageType": "DeviceAdded", "DeviceIndex": 2,
                                 "DeviceName": "LVS-Raw", "DeviceFeatures": DEFAULT_FEATURES}))
    assert client.device().display == "LVS-Raw"


def test_device_removed_and_device_list_reset():
    client, ws = connected_client(DEFAULT_FEATURES)
    client._dispatch(json.dumps({"MessageType": "DeviceRemoved", "DeviceIndex": 0}))
    assert client.device() is None
    client._dispatch(json.dumps({"MessageType": "DeviceList", "Devices": {
        "1": {"DeviceIndex": 1, "DeviceName": "LVS-Two", "DeviceFeatures": DEFAULT_FEATURES}}}))
    assert client.device().index == 1 and client.device().display == "LVS-Two"


def test_output_maps_level_to_device_range_and_sends_v4_shape():
    client, ws = connected_client(DEFAULT_FEATURES)
    asyncio.run(client.output(0.5))
    sent = ws.sent[-1]
    assert sent["MessageType"] == "OutputCmd"
    assert sent["DeviceIndex"] == 0 and sent["FeatureIndex"] == 0
    assert sent["Command"] == {"Vibrate": {"Value": 10}}
    asyncio.run(client.output(0))
    assert ws.sent[-1]["Command"] == {"Vibrate": {"Value": 0}}
    asyncio.run(client.output(1))
    assert ws.sent[-1]["Command"] == {"Vibrate": {"Value": 20}}
    assert client.sent == 3


def test_output_rejects_unsupported_kind_and_missing_device():
    client, ws = connected_client({"0": {"FeatureIndex": 0, "Output": {"Rotate": {"Value": [0, 20]}}}})
    with pytest.raises(HapticsUnavailable):
        asyncio.run(client.output(0.5, kind="Vibrate"))
    asyncio.run(client.output(0.5, kind="Rotate"))
    client.devices.clear()
    with pytest.raises(HapticsUnavailable):
        asyncio.run(client.output(0.5, kind="Rotate"))


def test_stop_all_sends_stopcmd_and_reports_when_disconnected():
    client, ws = connected_client(DEFAULT_FEATURES)
    assert asyncio.run(client.stop_all())
    assert ws.sent[-1]["MessageType"] == "StopCmd"
    assert ws.sent[-1]["Outputs"] is True and ws.sent[-1]["Inputs"] is False
    client.connected = False
    ws.closed = True
    assert asyncio.run(client.stop_all()) is False


def test_failed_send_surfaces_as_unavailable():
    client, ws = connected_client(DEFAULT_FEATURES)

    async def boom(payload):
        raise ConnectionResetError("peer gone")

    ws.send_str = boom
    with pytest.raises(HapticsUnavailable):
        asyncio.run(client.output(0.5))


def test_send_on_closed_socket_is_unavailable():
    client, ws = connected_client(DEFAULT_FEATURES)
    ws.closed = True
    with pytest.raises(HapticsUnavailable):
        asyncio.run(client.output(0.5))


def test_error_message_rejects_pending_waiter():
    client = ButtplugClient()
    client._ws = FakeWS()
    client.connected = True

    async def scenario():
        future = asyncio.get_running_loop().create_future()
        client._ok_waiters[7] = future
        client._dispatch(json.dumps({"Id": 7, "MessageType": "Error",
                                     "ErrorMessage": "device busy", "ErrorCode": 1}))
        with pytest.raises(HapticsUnavailable):
            await future

    asyncio.run(scenario())


def test_ok_and_server_info_resolve_waiters():
    client = ButtplugClient()
    client._ws = FakeWS()
    client.connected = True

    async def scenario():
        loop = asyncio.get_running_loop()
        first, second = loop.create_future(), loop.create_future()
        client._ok_waiters[1], client._ok_waiters[2] = first, second
        client._dispatch(json.dumps({"Id": 1, "MessageType": "Ok"}))
        client._dispatch(json.dumps({"Id": 2, "MessageType": "ServerInfo",
                                     "ServerName": "Intiface", "MaxPingTime": 100,
                                     "ProtocolVersionMajor": 4, "ProtocolVersionMinor": 0}))
        assert await first == {}
        assert (await second)["ServerName"] == "Intiface"

    asyncio.run(scenario())


def test_old_protocol_version_is_rejected():
    client = ButtplugClient()
    ws = FakeWS()
    client._ws = ws

    async def send_str(payload):
        data = json.loads(payload)
        ws.sent.append(data)
        client._dispatch(json.dumps({"Id": data["Id"], "MessageType": "ServerInfo",
                                     "ServerName": "old", "MaxPingTime": 100,
                                     "ProtocolVersionMajor": 1, "ProtocolVersionMinor": 0}))

    ws.send_str = send_str
    with pytest.raises(HapticsUnavailable):
        asyncio.run(client._handshake())


def test_status_reports_connection_and_devices():
    client, ws = connected_client(DEFAULT_FEATURES)
    client.url = "ws://127.0.0.1:12345"
    status = client.status()
    assert status["connected"] is True and status["url"].endswith("12345")
    assert status["devices"][0]["display"] == "LVS-Fake"
