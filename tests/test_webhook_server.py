# -*- coding: utf-8 -*-
"""P12-J：官方 Bot Webhook 接收端（`webhook_server.py`）。

为什么有它：官方**收消息只有 Webhook 一条路**（没有 getUpdates 长轮询）；
现在收消息走侧车账号的 Socket.IO —— 浏览器长连接，会断线重连、受风控影响，
实测投递延迟出现过 10～20 秒。Webhook 是平台服务端直推，秒级且与账号会话解耦。

本文件钉住四件事（前三条是安全边界，不能靠"看着对"）：
1. **Secret 必须校验**：不在白名单 → 401，且**不能**入队；
2. **必须在白名单里**：一个字的 Secret 都不认；
3. **去重**：同一个 message_id 重推只入队一次（平台会重推）；
4. **形状解析**：Telegram 风格 payload → 内部消息形状（含 UTC 的 sent_at）。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webhook_server as W  # noqa: E402

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

SECRET_ROSE = "rose-secret-abc"
SECRET_NUO = "nuo-secret-xyz"
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
BD = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
SECRETS = {SECRET_ROSE: ("weisi_rose", ROSE), SECRET_NUO: ("nuo_main", BD)}


def _payload(cid=ROSE, mid="m-1", text="薇丝在吗", sender="u-1", date=1758900000):
    """Telegram 风格（官方文档给的就是这个形状）。"""
    return {"update_id": 1,
            "message": {"message_id": mid, "chat": {"id": cid},
                        "from": {"id": sender, "first_name": "阿May"},
                        "date": date, "text": text}}


class _Sink:
    def __init__(self):
        self.items = []

    def push(self, cid, msg):
        self.items.append((cid, msg))
        return len(self.items)


@pytest.fixture
def client():
    sink = _Sink()
    app = W.build_app(secrets=SECRETS, push=sink.push, log=lambda m: None,
                      raw_log_budget=0)
    return sink, app


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_no_secret_is_rejected_and_not_queued(client):
    sink, app = client

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await c.post("/bot/webhook", json=_payload())
            assert r.status == 401, await r.text()
    _run(go())
    assert sink.items == [], "校验没过就入队了 —— 这是最严重的错"


def test_wrong_secret_is_rejected(client):
    sink, app = client

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await c.post("/bot/webhook", json=_payload(),
                             headers={W.SECRET_HEADER: SECRET_ROSE + "x"})
            assert r.status == 401
    _run(go())
    assert sink.items == []


def test_good_secret_queues_the_message(client):
    sink, app = client

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await c.post("/bot/webhook", json=_payload(),
                             headers={W.SECRET_HEADER: SECRET_ROSE})
            assert r.status == 200, await r.text()
            body = await r.json()
            assert body.get("ok") is True
    _run(go())
    assert len(sink.items) == 1
    cid, msg = sink.items[0]
    assert cid == ROSE
    assert msg["sent_by"] == "u-1"
    assert msg["content"]["text"] == "薇丝在吗"
    assert msg["content"]["type"] == "text"
    assert msg["_source"] == "webhook"


def test_duplicate_message_id_only_queued_once(client):
    sink, app = client

    async def go():
        async with TestClient(TestServer(app)) as c:
            for _ in range(3):
                r = await c.post("/bot/webhook", json=_payload(mid="dup-1"),
                                 headers={W.SECRET_HEADER: SECRET_ROSE})
                assert r.status == 200
    _run(go())
    assert len(sink.items) == 1, "平台重推被当成新消息了"


def test_message_for_another_chatroom_is_ignored(client):
    """拿玫瑰房的 Secret 推认证群的消息 → 忽略（Bot 与群是绑定的）。"""
    sink, app = client

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await c.post("/bot/webhook", json=_payload(cid=BD),
                             headers={W.SECRET_HEADER: SECRET_ROSE})
            assert r.status == 200
    _run(go())
    assert sink.items == []


def test_junk_payload_is_acked_not_queued(client):
    """解析不出来也要 200（否则平台会一直重推），但绝不入队。"""
    sink, app = client

    async def go():
        async with TestServer(app) as srv:
            async with TestClient(srv) as c:
                r = await c.post("/bot/webhook", data="not json",
                                 headers={W.SECRET_HEADER: SECRET_ROSE})
                assert r.status == 200
                r2 = await c.post("/bot/webhook", json={"foo": "bar"},
                                  headers={W.SECRET_HEADER: SECRET_ROSE})
                assert r2.status == 200
    _run(go())
    assert sink.items == []


# ---------------------------------------------------------------- 纯函数

def test_to_internal_maps_telegram_shape():
    m = W.to_internal(_payload(date=1758900000))
    assert m["chatroom_id"] == ROSE and m["message_id"] == "m-1"
    assert m["sent_by"] == "u-1" and m["content"]["text"] == "薇丝在吗"
    # date 是 unix 秒 → 内部要 UTC 字符串（message_lag 就是拿它算投递延迟的）
    assert m["sent_at"].startswith("2025-09-26")
    assert "T" not in m["sent_at"]


def test_to_internal_accepts_platform_native_shape():
    """平台自己那套字段名（非 Telegram 风格）也要认。"""
    m = W.to_internal({"chatroomId": ROSE,
                       "message": {"message_id": "x-1", "sent_by": "u-9",
                                   "sent_at": "2026-09-26 12:00:00",
                                   "content": {"type": "text", "text": "你好"}}})
    assert m["chatroom_id"] == ROSE and m["sent_by"] == "u-9"
    assert m["sent_at"] == "2026-09-26 12:00:00"
    assert m["content"]["text"] == "你好"


def test_to_internal_returns_none_without_chat_or_sender():
    assert W.to_internal({}) is None
    assert W.to_internal({"message": {"text": "hi"}}) is None
    assert W.to_internal({"message": {"chat": {"id": ROSE}}}) is None


def test_deduper_keeps_recent_only():
    d = W.Deduper(cap=3)
    for i in range(5):
        assert d.seen("m%d" % i) is False
    assert d.seen("m0") is False, "超出窗口的 id 会被忘掉（可接受）"
    assert d.seen("m4") is True
    assert d.seen("") is False, "空 id 不当重复"


def test_secret_env_derived_from_token_env():
    assert W.secret_env_for("DZMM_BOT_TOKEN_ROSE") == "DZMM_BOT_SECRET_ROSE"
    assert W.secret_env_for("DZMM_BOT_TOKEN") == "DZMM_BOT_SECRET"
    assert W.secret_env_for("") == ""


def test_collect_secrets_reads_env(monkeypatch):
    class B:
        def __init__(self, key, cid, tenv):
            self.key, self.cid, self.token_env = key, cid, tenv

    monkeypatch.setenv("DZMM_BOT_TOKEN_ROSE", "t1")
    monkeypatch.setenv("DZMM_BOT_SECRET_ROSE", SECRET_ROSE)
    monkeypatch.delenv("DZMM_BOT_SECRET", raising=False)
    got = W.collect_secrets([B("weisi_rose", ROSE, "DZMM_BOT_TOKEN_ROSE"),
                             B("nuo_main", BD, "DZMM_BOT_TOKEN")])
    assert got == {SECRET_ROSE: ("weisi_rose", ROSE)}, "没配 Secret 的 Bot 不该进白名单"
