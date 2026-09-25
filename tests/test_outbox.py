"""出站队列单测（P3）。

覆盖：状态机（pending→leased→sent/failed）、租约过期与回收、
按群串行、dry 透传、异常路径、属性透传。
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.outbox import (          # noqa: E402
    FAILED, LEASED, PENDING, SENT,
    OutboxSender, OutboxStore,
)


@pytest.fixture
def store(dbdir):
    s = OutboxStore(dbdir / "outbox.db")
    yield s
    s.close()


class FakeSender:
    """记录调用的假 sender。`result` 控制成功/失败，`boom` 控制抛异常。"""

    def __init__(self, result=True, boom=False, delay=0.0):
        self.result = result
        self.boom = boom
        self.delay = delay
        self.calls = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.marker = "inner-attribute"

    async def send(self, text, cid, allow_fallback=True):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.calls.append((text, cid, allow_fallback))
            if self.boom:
                raise RuntimeError("boom")
            return self.result
        finally:
            self.concurrent -= 1

    def start(self):
        return "started"

    def close(self):
        return "closed"


# ---------------------------------------------------------------- 存储：状态机

def test_enqueue_creates_pending_row(store):
    oid = store.enqueue(chatroom_id="c1", text="你好")
    row = store.get(oid)
    assert row["status"] == PENDING
    assert row["chatroom_id"] == "c1"
    assert row["text"] == "你好"
    assert row["attempt_count"] == 0
    assert row["created_at"] > 0


def test_lease_then_sent(store):
    oid = store.enqueue(chatroom_id="c1", text="x")
    assert store.lease(oid, "w1") is True
    row = store.get(oid)
    assert row["status"] == LEASED
    assert row["lease_worker_id"] == "w1"
    assert row["lease_token"]
    assert row["attempt_count"] == 1

    store.mark_sent(oid, "platform-msg-1")
    row = store.get(oid)
    assert row["status"] == SENT
    assert row["platform_sent_id"] == "platform-msg-1"
    assert row["sent_at"] is not None
    assert row["lease_worker_id"] is None      # 租约已释放
    assert row["last_error"] is None


def test_cannot_lease_twice_while_held(store):
    oid = store.enqueue(chatroom_id="c1", text="x")
    assert store.lease(oid, "w1") is True
    assert store.lease(oid, "w2") is False     # 租约未过期，抢不到
    assert store.get(oid)["lease_worker_id"] == "w1"


def test_can_release_expired_lease(store):
    oid = store.enqueue(chatroom_id="c1", text="x")
    assert store.lease(oid, "w1", ttl=-1) is True      # 立刻过期
    assert store.lease(oid, "w2") is True
    assert store.get(oid)["lease_worker_id"] == "w2"


def test_mark_failed_records_error(store):
    oid = store.enqueue(chatroom_id="c1", text="x")
    store.lease(oid, "w1")
    store.mark_failed(oid, "sender 返回 False")
    row = store.get(oid)
    assert row["status"] == FAILED
    assert row["last_error"] == "sender 返回 False"
    assert row["lease_worker_id"] is None


def test_reclaim_expired_returns_to_pending(store):
    a = store.enqueue(chatroom_id="c1", text="a")
    b = store.enqueue(chatroom_id="c1", text="b")
    store.lease(a, "w1", ttl=-1)
    store.lease(b, "w1", ttl=300)
    assert store.reclaim_expired() == 1
    assert store.get(a)["status"] == PENDING
    assert store.get(b)["status"] == LEASED


def test_pending_count_counts_leased_too(store):
    a = store.enqueue(chatroom_id="c1", text="a")
    store.enqueue(chatroom_id="c1", text="b")
    store.lease(a, "w1")
    assert store.pending_count() == 2
    store.mark_sent(a)
    assert store.pending_count() == 1


def test_recent_is_newest_first(store):
    store.enqueue(chatroom_id="c1", text="old")
    time.sleep(0.01)
    store.enqueue(chatroom_id="c1", text="new")
    assert [r["text"] for r in store.recent(2)] == ["new", "old"]


# ---------------------------------------------------------------- 装饰器：透传

def test_proxies_unknown_attributes(dbdir):
    inner = FakeSender()
    s = OutboxSender(inner, OutboxStore(dbdir / "o.db"))
    assert s.marker == "inner-attribute"        # __getattr__ 透传
    assert s.start() == "started"
    assert s.close() == "closed"


def test_worker_id_is_set(dbdir):
    s = OutboxSender(FakeSender(), OutboxStore(dbdir / "o.db"))
    assert s.worker_id.startswith("outbox-")


# ---------------------------------------------------------------- 装饰器：收发

def test_send_marks_sent_and_returns_true(dbdir):
    store = OutboxStore(dbdir / "o.db")
    inner = FakeSender(result=True)
    s = OutboxSender(inner, store)
    assert asyncio.run(s.send("你好", "c1")) is True
    assert inner.calls == [("你好", "c1", True)]
    rows = store.recent(1)
    assert rows[0]["status"] == SENT
    assert rows[0]["text"] == "你好"


def test_send_records_failure_but_returns_false(dbdir):
    store = OutboxStore(dbdir / "o.db")
    s = OutboxSender(FakeSender(result=False), store)
    assert asyncio.run(s.send("x", "c1")) is False
    assert store.recent(1)[0]["status"] == FAILED


def test_send_propagates_exception_and_records_failure(dbdir):
    store = OutboxStore(dbdir / "o.db")
    s = OutboxSender(FakeSender(boom=True), store)
    with pytest.raises(RuntimeError):
        asyncio.run(s.send("x", "c1"))
    row = store.recent(1)[0]
    assert row["status"] == FAILED
    assert "RuntimeError" in row["last_error"]


def test_allow_fallback_is_forwarded(dbdir):
    store = OutboxStore(dbdir / "o.db")
    inner = FakeSender()
    s = OutboxSender(inner, store)
    asyncio.run(s.send("x", "c1", allow_fallback=False))
    assert inner.calls == [("x", "c1", False)]


def test_dry_mode_bypasses_outbox(dbdir):
    store = OutboxStore(dbdir / "o.db")
    inner = FakeSender()
    s = OutboxSender(inner, store, dry=True)
    assert asyncio.run(s.send("x", "c1")) is True
    assert inner.calls == [("x", "c1", True)]
    assert store.recent(5) == []              # dry 不落库


# ---------------------------------------------------------------- 串行

def test_same_chatroom_sends_are_serialized(dbdir):
    """同群并发发送不得交叠 —— 否则消息顺序会乱。"""
    store = OutboxStore(dbdir / "o.db")
    inner = FakeSender(delay=0.02)
    s = OutboxSender(inner, store)

    async def burst():
        await asyncio.gather(*[s.send("m{}".format(i), "c1") for i in range(5)])

    asyncio.run(burst())
    assert inner.max_concurrent == 1
    assert len(inner.calls) == 5
    assert store.pending_count() == 0
    assert all(r["status"] == SENT for r in store.recent(5))
