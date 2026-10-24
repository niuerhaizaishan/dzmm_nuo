"""触觉控制服务：令牌鉴权、控制接口、状态输出。"""
import asyncio

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from runtime.haptics.mock import MockClient
from runtime.haptics.scheduler import Scheduler
from runtime.haptics.service import create_app

TOKEN = "test-token"


def build():
    client = MockClient()
    scheduler = Scheduler(client, min_interval_ms=0)
    app = create_app(token=TOKEN, scheduler=scheduler)
    return client, scheduler, app


def test_page_is_served_and_api_requires_token():
    async def scenario():
        client, scheduler, app = build()
        server = TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as session:
                page = await session.get(server.make_url("/"))
                body = await page.text()
                assert page.status == 200 and "触觉控制台" in body
                for path in ("/api/status", "/api/stop"):
                    response = await session.get(server.make_url(path)) if path == "/api/status" \
                        else await session.post(server.make_url(path), json={})
                    assert response.status == 401, path
                response = await session.post(server.make_url("/api/stop"), json={},
                                              headers={"X-Haptics-Token": "wrong"})
                assert response.status == 401, "错误的令牌也要拒绝"
                response = await session.get(server.make_url("/api/status"),
                                             headers={"X-Haptics-Token": TOKEN})
                assert response.status == 200
                data = await response.json()
                assert data["caps"]["max_level"] > 0 and data["device"]["connected"] is False
        finally:
            await server.close()
    asyncio.run(scenario())


def test_connect_pulse_stop_flow_over_http():
    async def scenario():
        client, scheduler, app = build()
        server = TestServer(app)
        await server.start_server()
        headers = {"X-Haptics-Token": TOKEN}
        try:
            async with ClientSession() as session:
                response = await session.post(server.make_url("/api/connect"),
                                              json={"url": "ws://mock"}, headers=headers)
                assert response.status == 200 and (await response.json())["ok"]
                response = await session.post(server.make_url("/api/pulse"),
                                              json={"level": 0.5, "duration_ms": 300,
                                                    "event_id": "ui-1"}, headers=headers)
                assert response.status == 200
                body = await response.json()
                assert body["accepted"] and body["value"] == 10 and body["device"]
                # 同一 event_id 再来一次要被去重
                response = await session.post(server.make_url("/api/pulse"),
                                              json={"level": 0.5, "duration_ms": 300,
                                                    "event_id": "ui-1"}, headers=headers)
                assert response.status == 409
                assert (await response.json())["reason"] == "duplicate"
                response = await session.post(server.make_url("/api/stop"), json={}, headers=headers)
                body = await response.json()
                assert body["stopped"] and body["reached_device"]
                assert not scheduler.active
        finally:
            await server.close()
    asyncio.run(scenario())


def test_caps_can_be_tightened_at_runtime():
    async def scenario():
        client, scheduler, app = build()
        server = TestServer(app)
        await server.start_server()
        headers = {"X-Haptics-Token": TOKEN}
        try:
            async with ClientSession() as session:
                await session.post(server.make_url("/api/connect"), json={"url": "ws://mock"}, headers=headers)
                response = await session.post(server.make_url("/api/caps"),
                                              json={"max_level": 0.2, "max_duration_ms": 500,
                                                    "min_interval_ms": 0}, headers=headers)
                caps = (await response.json())["caps"]
                assert caps["max_level"] == 0.2 and caps["max_duration_ms"] == 500
                response = await session.post(server.make_url("/api/pulse"),
                                              json={"level": 0.9, "duration_ms": 9000,
                                                    "event_id": "cap"}, headers=headers)
                body = await response.json()
                assert body["level"] == 0.2 and body["duration_ms"] == 500
        finally:
            await server.close()
    asyncio.run(scenario())


def test_connect_rejects_bad_url():
    async def scenario():
        client, scheduler, app = build()
        server = TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as session:
                response = await session.post(server.make_url("/api/connect"),
                                              json={"url": "http://example.com"}, headers={"X-Haptics-Token": TOKEN})
                assert response.status == 400
        finally:
            await server.close()
    asyncio.run(scenario())


def test_mock_mode_without_token_is_allowed_only_when_mock():
    async def scenario():
        app = create_app(mock=True)
        server = TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as session:
                response = await session.get(server.make_url("/api/status"))
                assert response.status == 200, "mock 模式下没有令牌也该能本地自测"
        finally:
            await server.close()
    asyncio.run(scenario())
