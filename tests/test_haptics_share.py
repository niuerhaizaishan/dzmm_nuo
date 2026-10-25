"""网页分享控制：房间、PIN、限幅与生命周期（不含真实蓝牙）。"""
import asyncio
import time

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from runtime.haptics import share
from runtime.haptics.share import Hub, create_app


def test_create_session_has_sid_pin_and_links():
    hub = Hub()
    session = hub.create("https://example.com/haptics")
    assert len(session.sid) == 8 and session.pin.isdigit() and len(session.pin) == 6
    assert session.link() == f"https://example.com/haptics/c?sid={session.sid}&pin={session.pin}"
    assert session.link(with_pin=False) == f"https://example.com/haptics/c?sid={session.sid}"
    assert hub.get(session.sid) is session
    assert hub.get("nope") is None


def test_pin_check_and_lockout_after_five_wrong():
    hub = Hub()
    session = hub.create("https://example.com")
    assert session.check_pin(session.pin)
    assert not session.check_pin("000000") if session.pin != "000000" else True
    for _ in range(share.PIN_ATTEMPTS):
        session.strike()
    assert session.locked(), "连续错 5 次必须锁房"
    assert session.state()["locked"] is True


def test_caps_default_open_and_can_be_tightened_by_env():
    hub = Hub()
    session = hub.create("https://example.com")
    caps = session.state()["caps"]
    # 默认不设上限（按用户要求）：强度 1.0、时长 0 表示不限
    assert caps["max_level"] == 1.0 and caps["max_duration_ms"] == 0
    assert caps["capped"] is False
    # 仍然保留的硬性约束
    assert share.MAX_GUESTS == 1 and share.MIN_INTERVAL_MS == 300
    assert share.PIN_ATTEMPTS == 5


def test_idle_session_is_swept_but_occupied_kept():
    hub = Hub()
    idle = hub.create("https://example.com")
    busy = hub.create("https://example.com")
    busy.host = object()  # 有房主连接的不会被回收
    idle.created -= share.SESSION_TTL + 1
    idle.touched -= share.SESSION_TTL + 1
    hub.sweep()
    assert hub.get(idle.sid) is None
    assert hub.get(busy.sid) is busy


def test_pages_and_session_api_over_http():
    async def scenario():
        app = create_app(base_url="https://example.com/haptics")
        server = TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as client:
                page = await client.get(server.make_url("/"))
                assert page.status == 200 and "分享控制" in await page.text()
                guest = await client.get(server.make_url("/c"))
                assert guest.status == 200 and "糯控" in await guest.text()
                qr = await client.get(server.make_url("/qrcode.min.js"))
                assert qr.status == 200 and "QRCode" in await qr.text()
                created = await client.post(server.make_url("/api/session"))
                body = await created.json()
                assert body["ok"] and body["sid"] and body["pin"]
                assert body["link"].startswith("https://example.com/haptics/c?sid=")
                state = await client.get(server.make_url(f"/api/session/{body['sid']}"))
                assert (await state.json())["state"]["host"] is False
                missing = await client.get(server.make_url("/api/session/nope"))
                assert missing.status == 404
                health = await client.get(server.make_url("/healthz"))
                assert (await health.json())["sessions"] >= 1
        finally:
            await asyncio.wait_for(server.close(), timeout=10)
    asyncio.run(scenario())


def test_host_ws_rejects_wrong_pin_and_guest_respects_capacity():
    """WS 只测拒绝路径：真实收发用命令行冒烟脚本验证，避免测试环境里的 WS 关停卡住。"""
    async def scenario():
        app = create_app(base_url="https://example.com")
        server = TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as client:
                body = await (await client.post(server.make_url("/api/session"))).json()
                bad = await client.get(server.make_url(
                    f"/ws/host?sid={body['sid']}&pin=000000"))
                assert bad.status == 403, "PIN 错误必须拒绝"
                ghost = await client.get(server.make_url("/ws/guest?sid=nope&pin=1"))
                assert ghost.status == 403
        finally:
            await asyncio.wait_for(server.close(), timeout=10)
    asyncio.run(scenario())


class _FakeHost:
    """假的房主 WS：只记录服务端转发过来的消息。"""

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def test_pattern_command_relays_id_and_total_ms():
    async def scenario():
        hub = Hub()
        session = hub.create("https://example.com")
        session.host = _FakeHost()
        cmd_id, level, duration, reason, pattern, total_ms = await share._relay_cmd(
            session, {"level": 0.5, "pattern": "pulse", "total_ms": 60000})
        assert reason is None and cmd_id == 1
        assert pattern == "pulse" and total_ms == 60000 and duration == 60000
        assert level == 1.0, "预设自带每一步的强度，手动档位不参与换算"
        relayed = session.host.sent[-1]
        assert relayed["t"] == "cmd" and relayed["pattern"] == "pulse"
        assert relayed["total_ms"] == 60000
    asyncio.run(scenario())


def test_pattern_command_clamps_total_ms_and_rejects_unknown_id():
    async def scenario():
        hub = Hub()
        session = hub.create("https://example.com")
        session.host = _FakeHost()
        _, _, _, reason, _, _ = await share._relay_cmd(session, {"pattern": "no_such"})
        assert reason == "未知预设", "白名单之外的预设必须拒绝"
        _, _, duration, _, _, total_ms = await share._relay_cmd(
            session, {"level": 0.3, "pattern": "tap", "total_ms": 999999})
        assert total_ms == share.MAX_PATTERN_MS and duration == share.MAX_PATTERN_MS
        _, _, _, _, _, total_ms = await share._relay_cmd(
            session, {"level": 0.3, "pattern": "tap", "total_ms": 1})
        assert total_ms == 200, "过短的预设时长也要抬到 200ms 下限"
    asyncio.run(scenario())


def test_level_message_reaches_host_and_is_clamped():
    async def scenario():
        hub = Hub()
        session = hub.create("https://example.com")
        session.host = _FakeHost()
        value, why = await share._relay_level(session, {"value": 0.42})
        assert why is None and abs(value - 0.42) < 1e-9
        relayed = session.host.sent[-1]
        assert relayed["t"] == "level" and abs(relayed["value"] - 0.42) < 1e-9
        assert abs(session.state()["level"] - 0.42) < 1e-9, "实时强度要能回读"
        value, why = await share._relay_level(session, {"value": 5})
        assert value == 1.0 and why is None, "超上限要夹到 1.0"
        value, why = await share._relay_level(session, {"value": -3})
        assert value == 0.0
        value, why = await share._relay_level(session, {"value": "abc"})
        assert value is None and why == "强度不对"
        session.host = None
        value, why = await share._relay_level(session, {"value": 0.5})
        assert value is None and why == "房主不在线"
    asyncio.run(scenario())

