"""出站队列（outbox）：所有出站集中到一个出口，落库、串行、可审计。

设计参考 DDZM-bot：`runtime/contracts.py` 的 `OutboundMessage` +
`browser/worker.py` 的「租约领取 → 发送 → 回写 platform_sent_id」。

P3 阶段的目标与边界：
  1. **仍只走旧通道**（Playwright 账号），不改任何调用点
  2. 靠**装饰 `Sender`** 实现 —— `bot.py` 里 39 个发送点（22 处直接
     `sender.send` + 17 处 `_send`）全部汇聚到 `Sender.send()`，包一层即可
  3. 行为与现网**逐项一致**：`send()` 仍然同步返回 bool，调用方语义不变
  4. 落库带完整状态机与租约字段，P4 的异步 worker 直接复用

铁律（来自 DDZM 接入指南 §8.5）：
  **平台未明确 ACK 时，绝不换通道重发。**
所以本模块**不做自动改道重试**；失败只记录状态，由上层决定。

存储用 SQLite（WAL）。单群 MVP 足够；P7 迁 PostgreSQL 时只需换 `OutboxStore`。
"""
import asyncio
import sqlite3
import time
import uuid
from pathlib import Path

__all__ = [
    "PENDING", "LEASED", "SENT", "FAILED",
    "OutboxStore", "OutboxSender", "new_worker_id",
]

PENDING = "pending"
LEASED = "leased"
SENT = "sent"
FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbound (
  id                    TEXT PRIMARY KEY,
  created_at            REAL NOT NULL,
  chatroom_id           TEXT NOT NULL,
  text                  TEXT NOT NULL,
  content_type          TEXT NOT NULL DEFAULT 'text',
  delivery_kind         TEXT NOT NULL DEFAULT 'group',
  recall_after_seconds  INTEGER,
  status                TEXT NOT NULL,
  attempt_count         INTEGER NOT NULL DEFAULT 0,
  lease_worker_id       TEXT,
  lease_token           TEXT,
  lease_expires_at      REAL,
  platform_sent_id      TEXT,
  last_error            TEXT,
  sent_at               REAL
);
CREATE INDEX IF NOT EXISTS idx_outbound_status ON outbound(status, created_at);
CREATE INDEX IF NOT EXISTS idx_outbound_cid    ON outbound(chatroom_id, created_at);
"""

DEFAULT_LEASE_TTL = 120.0


def new_worker_id() -> str:
    return "outbox-" + uuid.uuid4().hex[:8]


class OutboxStore:
    """出站任务的持久化与状态机。同步 SQLite —— 单条写入亚毫秒，P3 可接受。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        parent = Path(self.path).parent
        if str(parent) and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------ 写入

    def enqueue(self, *, chatroom_id: str, text: str, content_type: str = "text",
                delivery_kind: str = "group",
                recall_after_seconds: int | None = None) -> str:
        oid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO outbound (id, created_at, chatroom_id, text, content_type,"
            " delivery_kind, recall_after_seconds, status) VALUES (?,?,?,?,?,?,?,?)",
            (oid, time.time(), chatroom_id, text, content_type,
             delivery_kind, recall_after_seconds, PENDING))
        self._conn.commit()
        return oid

    def lease(self, oid: str, worker_id: str, ttl: float = DEFAULT_LEASE_TTL) -> bool:
        """领取租约。仅当 pending、或租约已过期时可领。返回是否领到。"""
        now = time.time()
        cur = self._conn.execute(
            "UPDATE outbound SET status=?, lease_worker_id=?, lease_token=?,"
            " lease_expires_at=?, attempt_count=attempt_count+1"
            " WHERE id=? AND (status=? OR (status=? AND lease_expires_at < ?))",
            (LEASED, worker_id, str(uuid.uuid4()), now + ttl,
             oid, PENDING, LEASED, now))
        self._conn.commit()
        return cur.rowcount == 1

    def mark_sent(self, oid: str, platform_sent_id: str | None = None) -> None:
        self._conn.execute(
            "UPDATE outbound SET status=?, platform_sent_id=?, sent_at=?,"
            " lease_worker_id=NULL, lease_token=NULL, lease_expires_at=NULL, last_error=NULL"
            " WHERE id=?",
            (SENT, platform_sent_id, time.time(), oid))
        self._conn.commit()

    def mark_failed(self, oid: str, error: str) -> None:
        self._conn.execute(
            "UPDATE outbound SET status=?, last_error=?,"
            " lease_worker_id=NULL, lease_token=NULL, lease_expires_at=NULL"
            " WHERE id=?",
            (FAILED, str(error)[:500], oid))
        self._conn.commit()

    def reclaim_expired(self) -> int:
        """把租约过期的任务退回 pending，供重新领取。返回退回条数。"""
        cur = self._conn.execute(
            "UPDATE outbound SET status=?, lease_worker_id=NULL, lease_token=NULL,"
            " lease_expires_at=NULL WHERE status=? AND lease_expires_at < ?",
            (PENDING, LEASED, time.time()))
        self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------ 读取

    def get(self, oid: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM outbound WHERE id=?", (oid,)).fetchone()
        return dict(row) if row else None

    def pending_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM outbound WHERE status IN (?,?)",
            (PENDING, LEASED)).fetchone()
        return int(row["n"])

    def recent(self, n: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM outbound ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (n,)).fetchall()
        return [dict(r) for r in rows]

    def by_status(self, status: str, n: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM outbound WHERE status=? ORDER BY created_at LIMIT ?",
            (status, n)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class OutboxSender:
    """把任意 `send(text, cid, allow_fallback=True) -> bool` 的 sender 包一层出站队列。

    未知属性一律透传，所以 `sender.start()` / `navigate()` / `close()` 等无需改动，
    `bot.py` 的调用点也**一个都不用改**。
    """

    def __init__(self, inner, store: OutboxStore, worker_id: str | None = None,
                 dry: bool = False, lease_ttl: float = DEFAULT_LEASE_TTL):
        self._inner = inner
        self._store = store
        self._worker_id = worker_id or new_worker_id()
        self._dry = dry
        self._lease_ttl = lease_ttl
        self._locks: dict[str, asyncio.Lock] = {}

    # 透传未覆盖的属性/方法给真实 sender
    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def _lock(self, cid: str) -> asyncio.Lock:
        lock = self._locks.get(cid)
        if lock is None:
            lock = self._locks[cid] = asyncio.Lock()
        return lock

    async def send(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        # dry 模式完全走原路径，不落库、无副作用（保持与现网一致）
        if self._dry:
            return await self._inner.send(text, cid, allow_fallback=allow_fallback)

        oid = self._store.enqueue(chatroom_id=cid, text=text)
        async with self._lock(cid):          # 同群串行，保证顺序
            if not self._store.lease(oid, self._worker_id, self._lease_ttl):
                self._store.mark_failed(oid, "领取租约失败")
                return False
            try:
                ok = await self._inner.send(text, cid, allow_fallback=allow_fallback)
            except Exception as e:
                self._store.mark_failed(oid, "{}: {}".format(type(e).__name__, str(e)))
                raise
            if ok:
                self._store.mark_sent(oid)
            else:
                self._store.mark_failed(oid, "sender 返回 False")
            return bool(ok)
