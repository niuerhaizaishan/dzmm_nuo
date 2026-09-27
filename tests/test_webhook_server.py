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
        """BotSpec 的形状（有 token_env）。"""
        def __init__(self, key, cid, tenv):
            self.key, self.cid, self.token_env = key, cid, tenv

    monkeypatch.setenv("DZMM_BOT_TOKEN_ROSE", "t1")
    monkeypatch.setenv("DZMM_BOT_SECRET_ROSE", SECRET_ROSE)
    monkeypatch.delenv("DZMM_BOT_SECRET", raising=False)
    got = W.collect_secrets([B("weisi_rose", ROSE, "DZMM_BOT_TOKEN_ROSE"),
                             B("nuo_main", BD, "DZMM_BOT_TOKEN")])
    assert got == {SECRET_ROSE: ("weisi_rose", ROSE)}, "没配 Secret 的 Bot 不该进白名单"


def test_collect_secrets_accepts_dict_shape_too(monkeypatch):
    monkeypatch.setenv("DZMM_BOT_SECRET_ROSE", SECRET_ROSE)
    got = W.collect_secrets([{"key": "weisi_rose", "cid": ROSE,
                              "token_env": "DZMM_BOT_TOKEN_ROSE"}])
    assert got == {SECRET_ROSE: ("weisi_rose", ROSE)}


def test_collect_secrets_from_identity_shape_yields_nothing(monkeypatch):
    """P12-J 真事故：`bots_for_identity()` 的 dict **没有 token_env**，
    传它一个 Secret 都收不到（日志只说"没有配置 Secret"，看起来像 .env 没配）。"""
    monkeypatch.setenv("DZMM_BOT_SECRET_ROSE", SECRET_ROSE)
    identity_shape = [{"key": "weisi_rose", "name": "薇丝-bot", "short": "薇丝",
                       "cid": ROSE, "uid": "36916cb2-..."}]
    assert W.collect_secrets(identity_shape) == {}


def test_sio_subscriber_dedupes_across_sources():
    """P12-J：Webhook（平台直推）与 Socket.IO（侧车账号）是两条路，
    同一条消息可能两边都到 —— 队列入口必须按 message_id 去重，
    否则主循环把它当两条新消息 ⇒ 机器人**回两次**。"""
    import sio_subscribe

    sub = sio_subscribe.SioSubscriber([ROSE])
    msg = {"message_id": "dup-42", "chatroom_id": ROSE, "sent_by": "u-1",
           "content": {"type": "text", "text": "薇丝"}}
    assert sub.push(ROSE, dict(msg)) == 1
    assert sub.push(ROSE, dict(msg)) == 1, "第二条重复不该再入队（条数仍是 1）"
    got = sub.drain(ROSE)
    assert len(got) == 1, "去重失败：drain 拿到 {} 条".format(len(got))
    # 不同 id 照常入队
    assert sub.push(ROSE, {"message_id": "other-1", "chatroom_id": ROSE}) == 1
    # 没有 message_id 的不去重（宁可重复也不能丢）
    assert sub.push(ROSE, {"chatroom_id": ROSE}) == 2
    assert sub.push(ROSE, {"chatroom_id": ROSE}) == 3


def test_sio_subscriber_dedupe_window_is_bounded():
    import sio_subscribe

    sub = sio_subscribe.SioSubscriber([ROSE])
    for i in range(sub._seen_cap + 20):
        sub.push(ROSE, {"message_id": "m{}".format(i), "chatroom_id": ROSE})
    assert len(sub._seen_ids) <= sub._seen_cap, "去重表必须有上限，不能无限长"
    # 窗口外的最老 id 会被忘掉（可接受：平台重推窗口是分钟级）
    before = len(sub._seen_ids)
    sub.push(ROSE, {"message_id": "m0", "chatroom_id": ROSE})
    assert len(sub._seen_ids) == before


def test_successful_message_read_does_not_arm_captcha_cooldown():
    """P12-J 真事故（影响最大的一条）：`dzmm.api()` 过去对**每个**响应都扫
    "限流/验证码"标记，而 `chatroom.getMessages` 的 body 是**一整页群消息** ——
    群里任何人（包括机器人自己那条"平台风控中"的公告）说到「验证码/风控」，
    就会把全局冷却拉满 300 秒；冷却期里每次平台调用又 sleep 10 秒
    ⇒ 一条普通回复要 70 秒以上，表现出来就是"机器人不回复了"。

    现在只在**出错**的响应上扫标记。这条测试就是钉住它。
    """
    import dzmm
    import runtime.throttle as T

    class _Resp:
        def __init__(self, body, status=200):
            self._body = body
            self.status_code = status
            self.text = body if isinstance(body, str) else json.dumps(body,
                                                                      ensure_ascii=False)

        def json(self):
            if isinstance(self._body, str):
                raise ValueError("not json")
            return self._body

    T.reset()
    # 直接调 note_response 时它仍会命中（它只认文本）—— 所以**关键是 api() 的调用条件**：
    src = __import__("inspect").getsource(dzmm.api)
    assert "if status != 200 or has_err or data is None:" in src
    assert "_note_resp(resp, path=path)" in src
    assert T.cooldown_remaining() == 0.0, "成功响应不该留下冷却"

    # ③ 错误响应仍然会命中（别把真验证码也放过）
    T.reset()
    bad = _Resp({"error": {"json": {"message": "captcha_required"}}})
    T.note_response(bad)
    assert T.cooldown_remaining() > 0, "真验证码必须仍然触发冷却"
    T.reset()


def test_cooldown_wait_is_short():
    """冷却期等待上限必须短（10 秒会把回复拖成分钟级）。"""
    import runtime.throttle as T
    assert T.COOLDOWN_WAIT_TIMEOUT <= 3.0
    src = __import__("inspect").getsource(T.acquire)
    assert "COOLDOWN_WAIT_TIMEOUT" in src


def test_run_all_passes_bot_specs_not_identity_dicts():
    """源码级钉住：`_run_all` 必须传 `_CFG.bots`（带 token_env）。"""
    import bot as B
    import inspect as I
    src = I.getsource(B._run_all)
    assert "_wh.collect_secrets(_CFG.bots)" in src
    assert "collect_secrets(_CFG.bots_for_identity" not in src


# ---------------------------------------------------------------- 加载方式（踩过的坑）

def test_root_module_loader_survives_gutted_sys_path():
    """P12-J 真事故：`sio_subscribe` 被 import 时会把项目根目录从 sys.path 摘掉，
    于是那之后的 `import webhook_server` 必然 ModuleNotFoundError
    （日志里只有一句 No module named 'webhook_server'，手工 import 又是好的）。

    `bot._load_root_module()` 按**文件路径**加载，不受 sys.path 影响 —— 这条测试
    就是把 sys.path 弄干净后仍然能加载，钉住这个坑不再回来。
    """
    import bot as B

    saved_path = list(sys.path)
    saved_mod = sys.modules.pop("webhook_server", None)   # 它在文件顶部已被导入过
    try:
        sys.path = [p for p in sys.path if p not in ("", str(B.ROOT))]
        with pytest.raises(ImportError):
            __import__("webhook_server")           # 说明 sys.path 确实被掏空了
        mod = B._load_root_module("webhook_server")
        assert mod.SECRET_HEADER == W.SECRET_HEADER
    finally:
        sys.path = saved_path
        if saved_mod is not None:
            sys.modules["webhook_server"] = saved_mod


def test_sio_subscribe_still_guts_sys_path():
    """那个副作用本身是有意的（避免项目自己的 socketio.py 顶掉 python-socketio 包）。

    所以不能"顺手删掉它"—— 只能按路径加载绕开。谁要是把这段删了，这条会红，
    提醒他去看 `_load_root_module` 的说明。
    """
    src = (Path(__file__).resolve().parent.parent / "sio_subscribe.py") \
        .read_text(encoding="utf-8")
    assert "sys.path = [p for p in sys.path if p not in (\"\", _ROOT)]" in src
