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
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from runtime import lambda_bot as _lambda_bot

__all__ = [
    "PENDING", "LEASED", "SENT", "FAILED", "INTERRUPTED_NOTE",
    "OutboxStore", "OutboxSender", "new_worker_id",
]

PENDING = "pending"
LEASED = "leased"
SENT = "sent"
FAILED = "failed"
# P12-BR ⑦：**终态** —— 人工核对后放弃的陈旧记录（多见于"进程重启打断、未确认"）。
# 它**不计入 `failed_count()`、不再重发、也不再出现在失败列表里**，
# 但记录本身保留（审计要能查到"当时那条到底发了什么、为什么放弃"）。
# 为什么要它：真失败（平台拒了）和"不知道发没发"混在同一个 failed 里，
# 状态行的 failed=N 就永远清不干净，巡检的"失败增长"口径也跟着失真。
EXPIRED = "expired"

# P12-J：安全重试的两个参数（只对"明确被拒"的失败生效）
RETRY_MAX_ATTEMPTS = 3      # 连同首次发送，最多试 4 次
RETRY_INTERVAL = 20.0       # 秒；一轮扫描的间隔


def log_line(msg: str) -> None:
    """写一行日志（与 runtime/obs 同源；导入失败也不影响出站）。"""
    try:
        from runtime.obs import log as _log
        _log(msg)
    except Exception:                                          # noqa: BLE001
        print(msg, flush=True)

# 进程重启打断发送后，留在记录里的原因（运维看到就知道"不是平台拒了"，
# 而是"不知道有没有发出去"）
INTERRUPTED_NOTE = "进程重启打断：未确认是否已投递，不自动重发（避免重复打扰）"

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
        # P12-AA（2026-09-27 体检）：连接必须能跨线程用，访问用一把可重入锁串行化
        # —— 与 `runtime/store.py` 对齐。默认 `check_same_thread=True` 会让工作线程
        # 一碰就抛 `ProgrammingError`，被 except 吞掉后表现成"偶发丢队列"；
        # 今天没炸只因调用点恰好都在事件循环线程，是 P12-U 事故的同款隐患。
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=10,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------ 写入

    def enqueue(self, *, chatroom_id: str, text: str, content_type: str = "text",
                delivery_kind: str = "group",
                recall_after_seconds: int | None = None) -> str:
        oid = str(uuid.uuid4())
        with self._lock:
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
        with self._lock:
            cur = self._conn.execute(
                "UPDATE outbound SET status=?, lease_worker_id=?, lease_token=?,"
                " lease_expires_at=?, attempt_count=attempt_count+1"
                " WHERE id=? AND (status=? OR (status=? AND lease_expires_at < ?))",
                (LEASED, worker_id, str(uuid.uuid4()), now + ttl,
                 oid, PENDING, LEASED, now))
            self._conn.commit()
            return cur.rowcount == 1

    def mark_sent(self, oid: str, platform_sent_id: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbound SET status=?, platform_sent_id=?, sent_at=?,"
                " lease_worker_id=NULL, lease_token=NULL, lease_expires_at=NULL, last_error=NULL"
                " WHERE id=?",
                (SENT, platform_sent_id, time.time(), oid))
            self._conn.commit()

    def mark_failed(self, oid: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbound SET status=?, last_error=?,"
                " lease_worker_id=NULL, lease_token=NULL, lease_expires_at=NULL"
                " WHERE id=?",
                (FAILED, str(error)[:500], oid))
            self._conn.commit()

    def reclaim_expired(self) -> int:
        """把租约过期的任务退回 pending，供重新领取。返回退回条数。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE outbound SET status=?, lease_worker_id=NULL, lease_token=NULL,"
                " lease_expires_at=NULL WHERE status=? AND lease_expires_at < ?",
                (PENDING, LEASED, time.time()))
            self._conn.commit()
            return cur.rowcount

    def revive(self, oid: str) -> bool:
        """把一条 **failed** 记录放回 pending，供安全重试重新领取（P12-J）。

        为什么需要它：`lease()` 只接受 `pending` 或"租约已过期的 leased" ——
        `failed` 的记录它不认。所以"失败记录永远没人再碰"有**两层**原因：
        既没有轮询的任务，也没法重新领租约。重试前先 revive，再 lease。
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE outbound SET status=?, lease_worker_id=NULL, lease_token=NULL,"
                " lease_expires_at=NULL WHERE id=? AND status=?",
                (PENDING, oid, FAILED))
            self._conn.commit()
            return cur.rowcount == 1

    def abandon_interrupted(self, reason: str) -> int:
        """把**上次进程留下的**未终态记录标成 failed。返回条数。

        ### 为什么需要这个

        `OutboxSender.send()` 是**同步内联**的：入队 → 领租约 → 发 → 标记。
        因此"进程在发送途中被杀"（部署重启、OOM、被 kill）会留下一条
        `leased` 记录（甚至 pending），而**没有任何代码会再去处理它** ——
        没有轮询 pending 的后台 worker，`reclaim_expired()` 也一直没人调用。
        结果就是这条记录永远挂在"未送达"里，运维看到的队列深度是错的
        （2026-09-25 真出现过一条：22:30:43 发给玫瑰群的问候，
        被随后的容器重建打断，之后一直挂着）。

        ### 为什么不重发

        被打断的那一刻**无法知道平台到底收没收到**（发送是同步 HTTP，
        可能在写回标记之前就死了）。重发就有重复打扰群友的风险。
        铁律（DDZM 接入指南 §8.5）：**平台未明确 ACK 时绝不换通道重发** ——
        这里同理：只把记录改成 failed 并写明原因，把"不确定"如实留在审计里，
        而不是猜一个结果。

        启动时调用（那时不可能有在飞的发送，所有非终态都是上次遗留的）。
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE outbound SET status=?, last_error=?, lease_worker_id=NULL,"
                " lease_token=NULL, lease_expires_at=NULL"
                " WHERE status IN (?, ?)",
                (FAILED, str(reason)[:500], PENDING, LEASED))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------ 读取

    def get(self, oid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM outbound WHERE id=?", (oid,)).fetchone()
        return dict(row) if row else None

    def pending_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM outbound WHERE status IN (?,?)",
                (PENDING, LEASED)).fetchone()
        return int(row["n"])

    def recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbound ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (n,)).fetchall()
        return [dict(r) for r in rows]

    def by_status(self, status: str, n: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbound WHERE status=? ORDER BY created_at LIMIT ?",
                (status, n)).fetchall()
        return [dict(r) for r in rows]

    def expire(self, ids, reason: str) -> int:
        """把陈旧失败**结案**成 `EXPIRED`（终态）。返回改了几条。

        P12-BR ⑦：只改状态 + 追一条人工结案理由进 `last_error`；
        **不删记录、不改正文、不重发**。`failed_count()` 只认 `failed`，
        所以结案之后状态行立刻干净。
        """
        ids = [i for i in (ids or []) if i]
        if not ids:
            return 0
        note = "[已结案] " + str(reason or "")
        n = 0
        with self._lock:
            for i in ids:
                cur = self._conn.execute(
                    "UPDATE outbound SET status=?, "
                    "last_error=coalesce(last_error,'') || ' | ' || ? "
                    "WHERE id=? AND status=?", (EXPIRED, note, i, FAILED))
                n += cur.rowcount or 0
            self._conn.commit()
        return n

    def discard_lambda_pending(self, cid: str, before: float) -> int:
        """休眠时结案本群尚未投递的业务消息，不触碰在途或其他群记录。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE outbound SET status=?, last_error=? WHERE chatroom_id=? "
                "AND created_at<=? AND status IN (?,?)",
                (EXPIRED, "Λʌ 休眠：取消待发，不再重放", cid, before, PENDING, FAILED))
            self._conn.commit()
            return cur.rowcount

    def failed_count(self) -> int:
        """失败记录条数（P12-AB：巡检靠它发现"某条消息静默丢了"）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM outbound WHERE status=?", (FAILED,)).fetchone()
        return int(row["n"])

    def recent_failed(self, n: int = 1) -> list[dict]:
        """最近的失败记录（新→旧）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbound WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (FAILED, n)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
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
                 dry: bool = False, lease_ttl: float = DEFAULT_LEASE_TTL,
                 retry_max: int = None, retry_interval: float = None):
        self._inner = inner
        self._store = store
        self._worker_id = worker_id or new_worker_id()
        self._dry = dry
        self._lease_ttl = lease_ttl
        self._locks: dict[str, asyncio.Lock] = {}
        # P12-J：**只重试"明确被拒"的那一类**（确定未投递）。
        # 模糊失败（网络错误/非 200/缺 message_id）绝不重发 —— 可能已经投递，
        # 重发就是重复打扰群友（见本模块顶部那条铁律）。
        self.retry_max = RETRY_MAX_ATTEMPTS if retry_max is None else int(retry_max)
        self.retry_interval = (RETRY_INTERVAL if retry_interval is None
                               else float(retry_interval))
        self.retry_counts = {"scanned": 0, "resent_ok": 0, "resent_fail": 0,
                             "skipped_unsafe": 0, "gave_up": 0}
        self._retry_task = None

    # 透传未覆盖的属性/方法给真实 sender
    def __getattr__(self, name):
        return getattr(self._inner, name)

    # ---- 队列观测（P12-AB：巡检与状态行要能看见"有没有消息丢了"）----

    def failed_count(self) -> int:
        try:
            return int(self._store.failed_count())
        except Exception:                                      # noqa: BLE001
            return 0

    def recent_failed(self, n: int = 1) -> list:
        try:
            return list(self._store.recent_failed(n))
        except Exception:                                      # noqa: BLE001
            return []

    def pending_count(self) -> int:
        try:
            return int(self._store.pending_count())
        except Exception:                                      # noqa: BLE001
            return 0

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def _lock(self, cid: str) -> asyncio.Lock:
        lock = self._locks.get(cid)
        if lock is None:
            lock = self._locks[cid] = asyncio.Lock()
        return lock

    async def send(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        control = _lambda_bot.is_control_send()
        created_at = time.time()
        if not control and _lambda_bot.suppress_outbound(cid):
            return False
        text = _lambda_bot.render_text(text, cid)
        # dry 模式完全走原路径，不落库、无副作用（保持与现网一致）
        if self._dry:
            return await self._inner.send(text, cid, allow_fallback=allow_fallback)

        oid = self._store.enqueue(chatroom_id=cid, text=text,
                                  content_type="lambda_control" if control else "text")
        async with self._lock(cid):          # 同群串行，保证顺序
            if not control and _lambda_bot.suppress_outbound(cid, created_at):
                self._store.discard_lambda_pending(
                    cid, float(_lambda_bot.presence(cid).get("paused_at") or created_at))
                return False
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
                # 记下**原因**：`rejected` 可以安全重试，`ambiguous` 绝不能
                self._store.mark_failed(oid, "sender 返回 False [{}]".format(
                    getattr(self._inner, "last_failure", "") or "unknown"))
            return bool(ok)

    async def send_with_id(self, text: str, cid: str) -> str:
        """确认提示持久记录平台编号；失败不加入自动重试。"""
        if _lambda_bot.suppress_outbound(cid):
            return ""
        text = _lambda_bot.render_text(text, cid)
        if self._dry:
            return ""
        oid = self._store.enqueue(chatroom_id=cid, text=text)
        async with self._lock(cid):
            if _lambda_bot.suppress_outbound(cid):
                self._store.discard_lambda_pending(cid, time.time())
                return ""
            if not self._store.lease(oid, self._worker_id, self._lease_ttl):
                self._store.mark_failed(oid, "确认提示领取失败")
                return ""
            try:
                message_id = await self._inner.send_with_id(text, cid)
                if not isinstance(message_id, str) or not message_id:
                    raise RuntimeError("缺少平台消息编号")
            except Exception as error:
                self._store.mark_failed(oid, "确认提示未确认：" + type(error).__name__)
                raise
            self._store.mark_sent(oid, platform_sent_id=message_id)
            return message_id

    async def send_reply_with_id(self, text: str, cid: str, reference: dict, *, guard=None) -> str:
        """保存正文和引用；专用类型禁止普通重试器把 JSON 当纯文本重放。"""
        if self._dry or _lambda_bot.suppress_outbound(cid):
            return ""
        payload = json.dumps({"text": text, "reference": reference, "single_message": True}, ensure_ascii=False)
        oid = self._store.enqueue(chatroom_id=cid, text=payload, content_type="office_reply")
        async with self._lock(cid):
            if _lambda_bot.suppress_outbound(cid):
                self._store.expire([oid], "回复已取消")
                return ""
            if not self._store.lease(oid, self._worker_id, self._lease_ttl):
                self._store.mark_failed(oid, "引用回复领取失败")
                return ""
            try:
                mid = await self._inner.send_reply_with_id(text, cid, reference, guard=guard)
                if not isinstance(mid, str) or not mid:
                    self._store.mark_failed(oid, "引用回复已取消或尚未投递")
                    return ""
            except Exception as error:
                self._store.mark_failed(oid, "引用回复结果待核对：" + type(error).__name__)
                raise
            self._store.mark_sent(oid, platform_sent_id=mid)
            return mid

    async def send_character_with_id(self, resource_id: str, cid: str) -> str:
        """分享卡独立排队、记录类型和平台编号，失败不自动改发文本。"""
        if _lambda_bot.suppress_outbound(cid):
            return ""
        if self._dry:
            return ""
        oid = self._store.enqueue(chatroom_id=cid, text=resource_id, content_type="share")
        async with self._lock(cid):
            if _lambda_bot.suppress_outbound(cid):
                self._store.discard_lambda_pending(cid, time.time())
                return ""
            if not self._store.lease(oid, self._worker_id, self._lease_ttl):
                self._store.mark_failed(oid, "分享卡领取失败")
                return ""
            try:
                message_id = await self._inner.send_character_with_id(resource_id, cid)
                if not isinstance(message_id, str) or not message_id:
                    raise RuntimeError("缺少分享卡平台编号")
            except Exception as error:
                self._store.mark_failed(oid, "分享卡未确认：" + type(error).__name__)
                raise
            self._store.mark_sent(oid, platform_sent_id=message_id)
            return message_id

    def cancel_lambda_pending(self, cid: str, before: float) -> int:
        if cid != _lambda_bot.RUIWEN_CID:
            return 0
        return self._store.discard_lambda_pending(cid, before)

    # ------------------------------------------------------------ 安全重试（P12-J）
    async def retry_worker(self) -> None:
        """后台：把**明确被拒**的失败记录在官方通道上重试几次。

        为什么需要：`send()` 是同步内联的，失败就 `mark_failed` —— 没有任何东西
        会再看它一眼（模块注释里写着"没有轮询 pending 的后台 worker"），
        于是"Bot 被拒"这类**确定没送到**的消息就永久丢了。
        为什么只重试这一类：`rejected` = 平台明确拒绝、确定未投递 ⇒ 重发安全；
        `ambiguous` 可能已经送到 ⇒ 重发会重复打扰群友（铁律：未 ACK 不重发）。
        """
        while True:
            try:
                await asyncio.sleep(self.retry_interval)
                await self.retry_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:                             # noqa: BLE001
                try:
                    log_line("outbox: 重试任务异常 " + str(e)[:80])
                except Exception:                              # noqa: BLE001
                    pass

    def start_retry_worker(self):
        """由 `_run_all` 在启动后调用；dry 模式不起（不落库）。"""
        if self._dry or self._retry_task is not None:
            return self._retry_task
        try:
            self._retry_task = asyncio.get_running_loop().create_task(self.retry_worker())
        except RuntimeError:
            self._retry_task = None
        return self._retry_task

    async def retry_once(self) -> int:
        """扫一轮失败记录并按安全规则重试。返回重试条数。"""
        if self._dry:
            return 0
        try:
            self._store.reclaim_expired()
        except Exception as e:                                 # noqa: BLE001
            # P12-AB：这个失败原来是静默的 —— 后果是"租约过期的记录永远回不到
            # pending"，也就是那条消息再也不会被重试，而日志里什么都没有。
            try:
                log_line("⚠️ outbox: 回收过期租约失败 " + str(e)[:80])
            except Exception:                                  # noqa: BLE001
                pass
        try:
            rows = self._store.by_status("failed", n=50)
        except Exception:                                      # noqa: BLE001
            return 0
        n = 0
        for it in rows or ():
            self.retry_counts["scanned"] += 1
            oid = str(it.get("id") or "")
            err = str(it.get("last_error") or "")
            try:
                attempts = int(it.get("attempt_count") or 0)
            except Exception:                                  # noqa: BLE001
                attempts = 0
            if it.get("content_type", "text") != "text" or "[rejected]" not in err:
                self.retry_counts["skipped_unsafe"] += 1
                continue
            if attempts > self.retry_max:
                self.retry_counts["gave_up"] += 1
                continue
            cid = str(it.get("chatroom_id") or "")
            text = str(it.get("text") or "")
            if not cid or not text:
                continue
            async with self._lock(cid):
                if _lambda_bot.suppress_outbound(cid, it.get("created_at")):
                    self._store.expire([oid], "Λʌ 休眠：取消旧回复，不再重放")
                    continue
                # failed 的记录领不到租约（lease 只认 pending / 过期 leased）
                # ⇒ 先放回 pending 再领
                if not self._store.revive(oid):
                    continue
                if not self._store.lease(oid, self._worker_id, self._lease_ttl):
                    continue
                try:
                    ok = await self._inner.send(text, cid)
                except Exception as e:                         # noqa: BLE001
                    self._store.mark_failed(oid, "{}: {}".format(
                        type(e).__name__, str(e)))
                    self.retry_counts["resent_fail"] += 1
                    continue
                if ok:
                    self._store.mark_sent(oid)
                    self.retry_counts["resent_ok"] += 1
                else:
                    self._store.mark_failed(oid, "sender 返回 False [{}]".format(
                        getattr(self._inner, "last_failure", "") or "unknown"))
                    self.retry_counts["resent_fail"] += 1
            n += 1
        return n
