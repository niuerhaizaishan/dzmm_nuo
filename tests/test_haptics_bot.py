"""/震 与 /停：解析、权限、回执（用假客户端，不碰网络）。"""
import asyncio
from pathlib import Path

import pytest

from runtime.haptics import bot_commands as B


class FakeShare:
    def __init__(self, ok=True, detail=""):
        self.calls = []
        self.ok = ok
        self.detail = detail

    async def command(self, level, duration_ms, pattern=None, total_ms=None):
        self.calls.append(("cmd", level, duration_ms, pattern, total_ms))
        return {"ok": self.ok, "detail": self.detail} if not self.ok else {"ok": True}

    async def stop(self, reason=""):
        self.calls.append(("stop", reason))
        return {"ok": True, "detail": ""} if self.ok else {"ok": False, "detail": self.detail}


@pytest.mark.parametrize("text,action", [
    ("/震", "震"), ("/震 30", "震"), ("/震 30 500", "震"), ("/停", "停"),
    ("/预设", "预设"), ("/预设 心跳", "预设"), ("/节奏 全开 20", "预设"),
    ("/震动", None), ("/停一下", None), ("/场景 惩罚", None),
    ("随便说点什么", None), ("", None),
])
def test_command_recognition(text, action):
    assert B.command(text) == action


def test_preset_names_map_to_page_ids():
    assert B.parse("/预设")[0] == {"action": "presets"}
    action, _ = B.parse("/预设 心跳")
    assert action["pattern"] == "heartbeat" and action["total_ms"] == 60000
    action, _ = B.parse("/预设 心跳 30")
    assert action["total_ms"] == 30000
    for name, pid in B.PRESET_LABELS:
        assert B.parse("/预设 " + name)[0]["pattern"] == pid
    # 别名与唯一前缀
    assert B.parse("/预设 长震")[0]["pattern"] == "hold"
    assert B.parse("/预设 心")[0]["pattern"] == "heartbeat"
    assert B.parse("/预设 不存在")[0] is None
    for bad in ("/预设 心跳 1", "/预设 心跳 99", "/预设 心跳 abc"):
        assert B.parse(bad)[0] is None, bad


def test_preset_command_relays_pattern(monkeypatch):
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    share = FakeShare()
    reply = asyncio.run(B.handle("/预设 连抖 20", owner=True, share=share))
    assert "连抖" in reply and "20" in reply
    assert share.calls == [("cmd", 0.0, 20000, "stutter", 20000)]
    reply = asyncio.run(B.handle("/预设 不存在", owner=True, share=share))
    assert "没有这个预设" in reply
    assert len(share.calls) == 1, "没找到预设时不该下发"


def test_parse_defaults_and_ranges():
    assert B.parse("/震")[0] == {"action": "vibrate", "level": 0.3, "duration_ms": 800}
    assert B.parse("/震 50")[0] == {"action": "vibrate", "level": 0.5, "duration_ms": 800}
    assert B.parse("/震 50% 2000")[0] == {"action": "vibrate", "level": 0.5, "duration_ms": 2000}
    assert B.parse("/停")[0] == {"action": "stop"}
    for bad in ("/震 0", "/震 101", "/震 abc", "/震 50 50", "/震 50 20000", "/震 50 300 extra"):
        action, problem = B.parse(bad)
        assert action is None and problem, bad


def test_non_owner_is_refused_before_touching_network(monkeypatch):
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    share = FakeShare()
    reply = asyncio.run(B.handle("/震", owner=False, share=share))
    assert "只有主人" in reply
    assert share.calls == []


def test_missing_token_explains_how_to_enable(monkeypatch):
    monkeypatch.delenv("DZMM_SHARE_PROGRAM_TOKEN", raising=False)
    reply = asyncio.run(B.handle("/震", owner=True, share=FakeShare()))
    assert "通道没开" in reply


def test_owner_command_and_stop_go_through(monkeypatch):
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    share = FakeShare()
    reply = asyncio.run(B.handle("/震 40 600", owner=True, share=share))
    assert reply.startswith("⚡ 40% / 600ms")
    assert share.calls == [("cmd", 0.4, 600, None, None)]
    reply = asyncio.run(B.handle("/停", owner=True, share=share))
    assert reply == "已停。"
    assert share.calls[-1][0] == "stop"


def test_unattached_room_returns_actionable_hint(monkeypatch):
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    share = FakeShare(ok=False, detail="没有已允许的房间")
    reply = asyncio.run(B.handle("/震", owner=True, share=share))
    assert "没连上你的控制页" in reply and "允许小小糯控制" in reply


def test_usage_is_returned_for_bad_params(monkeypatch):
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    reply = asyncio.run(B.handle("/震 abc", owner=True, share=FakeShare()))
    assert "用法" in reply


def test_allowlist_file_grants_permission(tmp_path, monkeypatch):
    allow = tmp_path / "allow.json"
    allow.write_text('{"uids": ["CF1B1360-7F3F-4EF8-9188-07D718FE9173"], "names": ["御褚"]}', encoding="utf-8")
    monkeypatch.setenv("DZMM_HAPTICS_ALLOW_FILE", str(allow))
    monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
    assert B.is_allowed("cf1b1360-7f3f-4ef8-9188-07d718fe9173"), "大小写不该影响"
    assert not B.is_allowed("someone-else")

    share = FakeShare()
    reply = asyncio.run(B.handle("/震 20", uid="cf1b1360-7f3f-4ef8-9188-07d718fe9173", share=share))
    assert reply.startswith("⚡ 20% / 800ms")
    reply = asyncio.run(B.handle("/震 20", uid="someone-else", share=share))
    assert "白名单" in reply
    assert len(share.calls) == 1, "白名单外的调用不该真的下发"


def test_missing_or_broken_allow_file_denies_everyone(tmp_path, monkeypatch):
    monkeypatch.setenv("DZMM_HAPTICS_ALLOW_FILE", str(tmp_path / "nope.json"))
    assert B.allow_uids() == set()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("DZMM_HAPTICS_ALLOW_FILE", str(bad))
    assert B.allow_uids() == set()
    assert not B.is_allowed("anyone")

def test_allowlist_file_grants_permission(monkeypatch):
    allow = Path(__file__).with_name("_allow_test.json")
    allow.write_text('{"uids": ["CF1B1360-7F3F-4EF8-9188-07D718FE9173"], "names": ["御褚"]}',
                     encoding="utf-8")
    try:
        monkeypatch.setenv("DZMM_HAPTICS_ALLOW_FILE", str(allow))
        monkeypatch.setenv("DZMM_SHARE_PROGRAM_TOKEN", "t")
        assert B.is_allowed("cf1b1360-7f3f-4ef8-9188-07d718fe9173"), "大小写不该影响"
        assert not B.is_allowed("someone-else")
        share = FakeShare()
        reply = asyncio.run(B.handle("/震 20", uid="cf1b1360-7f3f-4ef8-9188-07d718fe9173", share=share))
        assert reply.startswith("⚡ 20% / 800ms")
        reply = asyncio.run(B.handle("/震 20", uid="someone-else", share=share))
        assert "白名单" in reply
        assert len(share.calls) == 1, "白名单外的调用不该真的下发"
    finally:
        allow.unlink(missing_ok=True)


def test_missing_or_broken_allow_file_denies_everyone(monkeypatch):
    monkeypatch.setenv("DZMM_HAPTICS_ALLOW_FILE", str(Path(__file__).with_name("_no_such_allow.json")))
    assert B.allow_uids() == set()
    bad = Path(__file__).with_name("_allow_bad.json")
    bad.write_text("{not json", encoding="utf-8")
    try:
        monkeypatch.setenv("DZMM_HAPTICS_ALLOW_FILE", str(bad))
        assert B.allow_uids() == set()
        assert not B.is_allowed("anyone")
    finally:
        bad.unlink(missing_ok=True)
