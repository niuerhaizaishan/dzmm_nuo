# -*- coding: utf-8 -*-
"""P12-J：出站队列的**安全重试**。

屋主一问把这件事捅出来了：`OutboxSender.send()` 是同步内联的，失败就
`mark_failed` —— **没有任何东西会再看它一眼**（模块注释原话："没有轮询 pending
的后台 worker"），于是"Bot 被拒"这类**确定没送到**的消息就永久丢了。

修法按代码里那条铁律来（DDZM 接入指南 §8.5：**平台未明确 ACK 时绝不换通道重发**）：
  · `rejected`  = 平台明确拒绝、**确定未投递** ⇒ 可以在**官方通道上**重试（有限次）
  · `ambiguous` = 网络错误/非 200/缺 message_id ⇒ **可能已投递** ⇒ 绝不重发
  · `account_unknown` = 账号通道失败、不知情 ⇒ 也不重发

本文件钉住：原因记录、只重试 rejected、次数上限、模糊的永远不动。
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import runtime.outbox as OB          # noqa: E402
from runtime.routing import RoutingSender, BotApiRejected, BotApiAmbiguous  # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"


class _Bot:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = 0

    def send_text(self, cid, text):
        self.calls += 1
        if self.exc:
            raise self.exc
        return "mid-1"


class _Acct:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0

    async def send(self, text, cid, allow_fallback=True):
        self.calls += 1
        return self.ok


def test_router_records_why_it_failed():
    """失败原因要被记下来 —— 出站队列靠它决定"能否安全重试"。"""
    acct = _Acct()
    r = RoutingSender(acct, {ROSE: _Bot(BotApiRejected("Bot is not a member"))})
    assert asyncio.run(r.send("x", ROSE)) is True     # 默认回退账号 ⇒ 成功
    assert r.last_failure == ""                       # 账号发成功了

    r2 = RoutingSender(acct, {ROSE: _Bot(BotApiRejected("nope"))},
                       official_only=True)
    assert asyncio.run(r2.send("x", ROSE)) is False
    assert r2.last_failure == "rejected", "明确被拒 ⇒ 可以安全重试"

    r3 = RoutingSender(acct, {ROSE: _Bot(BotApiAmbiguous("网络错误"))})
    assert asyncio.run(r3.send("x", ROSE)) is False
    assert r3.last_failure == "ambiguous", "模糊失败 ⇒ 绝不能重发"


def _mk(dbdir, bot_exc=None, acct_ok=True, official_only=False):
    store = OB.OutboxStore(dbdir / "outbox.db")
    router = RoutingSender(_Acct(acct_ok), {ROSE: _Bot(bot_exc)},
                           official_only=official_only)
    return OB.OutboxSender(router, store, retry_max=2, retry_interval=0.01), store


def test_rejected_send_is_retried_via_official_channel(dbdir):
    """明确被拒 ⇒ 出站队列在官方通道上重试，最终成功（不再永久丢）。"""
    async def go():
        sender, store = _mk(dbdir, bot_exc=BotApiRejected("Bot is not a member"),
                            official_only=True)
        try:
            assert await sender.send("你好", ROSE) is False     # 第一次：被拒
            rows = store.by_status("failed")
            assert len(rows) == 1 and "[rejected]" in rows[0]["last_error"]
            # 让 Bot 恢复正常（模拟"重新把 Bot 拉进群"）
            sender._inner._bots[ROSE].exc = None
            n = await sender.retry_once()
            assert n == 1
            assert sender.retry_counts["resent_ok"] == 1
            assert store.by_status("failed") == [], "重试成功后不该还挂在 failed"
            assert store.by_status("sent"), "应当标记为已发送"
        finally:
            store.close()
    asyncio.run(go())


def test_ambiguous_send_is_never_retried(dbdir):
    """可能已投递 ⇒ **绝不重发**（重发就是重复打扰群友）。"""
    async def go():
        sender, store = _mk(dbdir, bot_exc=BotApiAmbiguous("HTTP 502"))
        try:
            assert await sender.send("你好", ROSE) is False
            rows = store.by_status("failed")
            assert len(rows) == 1 and "[ambiguous]" in rows[0]["last_error"]
            n = await sender.retry_once()
            assert n == 0 and sender.retry_counts["skipped_unsafe"] == 1
            assert store.by_status("failed"), "模糊失败要**留在审计里**，不是重发"
        finally:
            store.close()
    asyncio.run(go())


def test_account_unknown_is_never_retried(dbdir):
    async def go():
        store = OB.OutboxStore(dbdir / "o2.db")
        router = RoutingSender(_Acct(ok=False), {})      # 没有 Bot ⇒ 走账号，账号失败
        sender = OB.OutboxSender(router, store, retry_max=2)
        try:
            assert await sender.send("你好", ROSE) is False
            err = store.by_status("failed")[0]["last_error"]
            assert "[account_unknown]" in err
            assert await sender.retry_once() == 0
            assert sender.retry_counts["skipped_unsafe"] == 1
        finally:
            store.close()
    asyncio.run(go())


def test_retry_is_bounded(dbdir):
    """一直失败也不能无限重试（重试次数有上限，超了就留在 failed 里）。"""
    async def go():
        sender, store = _mk(dbdir, bot_exc=BotApiRejected("still rejected"),
                            official_only=True)
        try:
            await sender.send("你好", ROSE)
            for _ in range(5):
                await sender.retry_once()
            assert sender.retry_counts["gave_up"] >= 1
            rows = store.by_status("failed")
            assert rows and int(rows[0]["attempt_count"]) >= sender.retry_max
        finally:
            store.close()
    asyncio.run(go())


def test_dry_mode_has_no_retry_worker(dbdir):
    store = OB.OutboxStore(dbdir / "o3.db")
    try:
        sender = OB.OutboxSender(_Acct(), store, dry=True)
        assert sender.start_retry_worker() is None
        assert asyncio.run(sender.retry_once()) == 0
    finally:
        store.close()


# ---------------------------------------------------------------- 同批多命令不能丢

def test_requeue_puts_messages_back_without_dedupe():
    """P12-J 真事故：主循环每轮只处理**一条**命令，而 `drain()` 会把整个队列取空 ——
    同一批里剩下的命令就被静默丢了（紫苑发 `/运势` 没反应，因为同批先有一条 `/写诗`）。

    放回队列要**绕过去重表**（它们不是重复，只是还没轮到），并打 `_requeued` 标记
    （主循环的 last_id 过滤会丢掉"批尾那条"，而放回的往往正好是批尾）。
    """
    import sio_subscribe

    sub = sio_subscribe.SioSubscriber([ROSE])
    a = {"message_id": "c1", "chatroom_id": ROSE, "content": {"text": "/写诗 x"}}
    b = {"message_id": "c2", "chatroom_id": ROSE, "content": {"text": "/运势"}}
    sub.push(ROSE, a)
    sub.push(ROSE, b)
    got = sub.drain(ROSE)
    assert len(got) == 2
    # 假设主循环挑走 a（/写诗），把 b 放回
    assert sub.requeue(ROSE, [b]) == 1
    again = sub.drain(ROSE)
    assert len(again) == 1 and again[0]["message_id"] == "c2"
    assert again[0].get("_requeued") is True
    # 再放回一次也要能进来（去重表不该拦它）
    assert sub.requeue(ROSE, [again[0]]) == 1
    assert len(sub.drain(ROSE)) == 1


def test_loop_does_not_break_on_the_first_command():
    """源码级钉住：命令扫描不许"见到第一条就 break"，并且要 requeue 剩下的。"""
    import inspect
    import runtime.mainloop as B   # P3-19：main_loop 体搬迁至此（bot 桥前缀，子串兼容）
    src = inspect.getsource(B.run_main_loop)
    a1 = "# 检查 pending 里有没有 / 指令或 @ 消息"
    a2 = "# `cmd` 优先于 @ / 房间分支"
    assert a1 in src and a2 in src, "命令扫描那段的锚点变了，请更新这条测试"
    seg = src[src.index(a1):src.index(a2)]
    # 只看真代码：注释里也会出现 "break" 这个词
    code = [ln for ln in seg.splitlines() if not ln.strip().startswith("#")]
    assert not any("break" in ln for ln in code), \
        "又出现'见到第一条命令就 break'了 —— 同批的命令会丢"
    assert "_leftover" in seg, "没看到把剩余消息收集起来的逻辑"
    assert "subscriber.requeue(cid, _leftover)" in src
