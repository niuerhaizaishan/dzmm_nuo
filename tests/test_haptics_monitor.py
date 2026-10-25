"""/监控：指令解析 + 名单读写（不碰网络）。"""
import asyncio
import json
import os
import uuid

import pytest

from runtime.haptics import bot_commands as B
from runtime.haptics import raid

_HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    path = os.path.join(_HERE, "haptics-monitor-{}.json".format(uuid.uuid4().hex[:8]))
    monkeypatch.setenv("DZMM_HAPTICS_RAID_FILE", path)
    raid.reset()
    yield path
    raid.reset()
    try:
        os.remove(path)
    except OSError:
        pass


def test_monitor_command_forms():
    link = "https://www.dzmm.ai/chat?c=b32a240f-ebd0-4086-bc33-da4a66098923"
    assert B.command("/监控") == "监控"
    assert B.command("#监控") == "监控"
    assert B.command("/停止监控") == "停止监控"
    assert B.parse("/监控 " + link)[0]["target"] == link
    assert B.find_cid(link) == "b32a240f-ebd0-4086-bc33-da4a66098923"
    assert B.find_cid("没有链接") is None


def test_monitor_takes_cid_from_quote(_clean):
    """引用那条链接再发 /监控 → 从被引用的原文里抠 ID。"""
    cid = "b32a240f-ebd0-4086-bc33-da4a66098923"
    reply = asyncio.run(B.handle("/监控", owner=True, quote="看看这个：https://x/chat?c=" + cid))
    assert "已开始监控" in reply
    conf = json.load(open(_clean, encoding="utf-8"))
    assert conf["cids"] == [cid] and conf["enabled"] is True


def test_monitor_requires_owner(_clean):
    cid = "b32a240f-ebd0-4086-bc33-da4a66098923"
    reply = asyncio.run(B.handle("/监控 " + cid, owner=False))
    assert "只有主人" in reply


def test_monitor_without_cid_explains_usage(_clean):
    reply = asyncio.run(B.handle("/监控", owner=True))
    assert "没找到群 ID" in reply


def test_unmonitor_removes_cid(_clean):
    cid = "b32a240f-ebd0-4086-bc33-da4a66098923"
    asyncio.run(B.handle("/监控 " + cid, owner=True))
    assert raid.is_raid(cid) is True
    reply = asyncio.run(B.handle("/停止监控 " + cid, owner=True))
    assert "已停止监控" in reply
    assert raid.is_raid(cid) is False
