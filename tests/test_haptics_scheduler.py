"""触觉调度器：限幅、去重、限流、自动停止、替换与急停。"""
import asyncio

import pytest

from runtime.haptics.mock import MockClient
from runtime.haptics.scheduler import Scheduler


def run(coro):
    return asyncio.run(coro)


async def ready(**kwargs):
    client = MockClient()
    await client.connect("ws://mock")
    return client, Scheduler(client, **kwargs)


def test_clamps_level_and_duration_to_caps():
    async def scenario():
        client, scheduler = await ready(max_level=0.4, max_duration_ms=2000, min_interval_ms=0)
        result = await scheduler.submit("e1", level=0.95, duration_ms=9000)
        assert result["accepted"]
        assert result["level"] == 0.4 and result["duration_ms"] == 2000
        assert result["value"] == 8  # 0..20 区间，0.4*20
        assert scheduler.stats["clamped_level"] == 1 and scheduler.stats["clamped_duration"] == 1
        await scheduler.stop("test")
    run(scenario())


def test_duplicate_event_id_is_rejected_within_ttl():
    async def scenario():
        client, scheduler = await ready(min_interval_ms=0)
        assert (await scheduler.submit("dupe", 0.3, 500))["accepted"]
        second = await scheduler.submit("dupe", 0.3, 500)
        assert not second["accepted"] and second["reason"] == "duplicate"
        assert scheduler.stats["rejected"] == 1
        await scheduler.stop("test")
    run(scenario())


def test_rate_limit_blocks_second_command_immediately():
    async def scenario():
        client, scheduler = await ready(min_interval_ms=500)
        assert (await scheduler.submit("a", 0.3, 300))["accepted"]
        blocked = await scheduler.submit("b", 0.3, 300)
        assert blocked["reason"] == "rate_limited"
        await scheduler.stop("test")
    run(scenario())


def test_auto_stop_fires_and_marks_inactive():
    async def scenario():
        client, scheduler = await ready(min_interval_ms=0)
        await scheduler.submit("auto", 0.3, 200)
        assert scheduler.active
        await asyncio.sleep(0.45)
        assert not scheduler.active
        assert ("stop",) == tuple(row[:1] for row in client.history[-1:])[0]
    run(scenario())


def test_newer_command_replaces_older_timer():
    async def scenario():
        client, scheduler = await ready(min_interval_ms=0)
        await scheduler.submit("first", 0.3, 300)
        await asyncio.sleep(0.1)
        await scheduler.submit("second", 0.5, 1500)
        await asyncio.sleep(0.35)  # 第一条第 300ms 的计时器若还在，此刻会误停
        assert scheduler.active, "旧指令的计时器不该把新指令停掉"
        await scheduler.stop("test")
        assert not scheduler.active
    run(scenario())


def test_stop_is_idempotent_and_clears_activity():
    async def scenario():
        client, scheduler = await ready(min_interval_ms=0)
        await scheduler.submit("one", 0.3, 900)
        first = await scheduler.stop("manual")
        second = await scheduler.stop("manual")
        assert first["reached_device"] and second["reached_device"]
        assert not scheduler.active
        await asyncio.sleep(1.0)
        assert not scheduler.active
    run(scenario())


def test_unavailable_and_no_device_are_rejected_not_raised():
    async def scenario():
        client = MockClient()
        scheduler = Scheduler(client, min_interval_ms=0)
        assert (await scheduler.submit("x", 0.3, 300))["reason"] == "unavailable"
        await client.connect("ws://mock")
        client.devices.clear()
        assert (await scheduler.submit("y", 0.3, 300))["reason"] == "no_device"
    run(scenario())


@pytest.mark.parametrize("level,duration", [(0, 500), (-1, 500), ("x", 500), (0.3, 0), (0.3, -5), (0.3, "y")])
def test_invalid_input_is_rejected(level, duration):
    async def scenario():
        client, scheduler = await ready(min_interval_ms=0)
        result = await scheduler.submit("bad", level, duration)
        assert not result["accepted"] and result["reason"] in {"bad_level", "bad_duration"}
        await scheduler.stop("test")
    run(scenario())


def test_shutdown_stops_device_and_disconnects():
    async def scenario():
        client, scheduler = await ready(min_interval_ms=0)
        await scheduler.submit("s", 0.3, 5000)
        await scheduler.shutdown()
        assert not client.connected and not scheduler.active
    run(scenario())
