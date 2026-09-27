"""JSON → SQLite 存储内核（P7）。

### 为什么（**已逐条核实**，不是推测）

> 起草时我曾断言"两个进程在无锁地写同一个 `state.json`"——**那是错的**。
> `check_bot_health.py` 里的 `load_state/save_state` 读写的是它**自己的**
> `check_health_state.json`（该文件第 24 行）。核实后 `state.json` 的写者**只有 `bot.py`**。
> 下面三条是核实过的事实：

1. **写是非原子的** → 截断即静默清零。
   `save_state` 用 `Path.write_text` 直接覆盖（`profiles.py` 注释说明：这些文件是
   **文件 bind mount**，`os.replace` 会 `Device busy`，所以只能原地写）。
   写到一半被 kill（容器 recreate / OOM）→ 留下截断的 JSON →
   而 `load_state()` 解析失败时**静默返回 `{}`** → **全群的发情值 / 高潮计数 /
   状态机 / 记忆 / 投票 一起无声清零**，且没有任何告警。
2. **写放大**：`state.json` 约 50KB，而 `bot.py` 里约 45 个写入点，**每次改动都重写整文件**。
3. **`profiles.json` 有两个潜在写者**：`bot.py` 与手工脚本 `batch_register.py`
   都走 `profiles.py` 的"读整个文件→改→写回整个文件"，同时跑会丢更新。

SQLite（WAL）一次解决三条：事务原子、按 key 增量写、多写者安全。
它放在 `./data` **目录挂载**上（不是文件 bind mount），所以不存在 `os.replace` 的 EBUSY 问题。

### 与方案的偏离（有意）

方案 P7 写的是 **PostgreSQL**。这里改用 **SQLite**：

- 现在只有**一个** bot 进程（"多群多 Bot、多服务"的设想已在范围收窄时去掉），
  PG 的跨机并发能力用不上；
- 宿主机 2 核 / 1.8G 内存，再起一个 PG 容器是**多一个故障域、多一份内存**；
- 上面三条 SQLite 全部能解决。

若将来确实要多进程/多机，**`Store` 是唯一需要替换的东西**。

### 回滚

`DZMM_STORE=json` 立刻退回原来的 JSON 读写；**JSON 文件保留不删**。
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

__all__ = ["Store", "SCOPE_GROUP", "SCOPE_PROFILE", "SCOPE_META",
           "default_db_path"]

SCOPE_GROUP = "group"
SCOPE_PROFILE = "profile"
SCOPE_META = "meta"

IMPORT_MARKER = "json_imported"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  scope      TEXT NOT NULL,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);
"""


def default_db_path(root: Path | str | None = None) -> Path:
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    return root / "data" / "store.db"


class Store:
    """极简 KV 存储：`(scope, key) -> JSON 值`。

    刻意不建业务表 —— 现有代码到处是 `state[cid]["whatever"]` 这种自由结构，
    强行关系化等于重写业务。先用事务与并发安全把"会丢数据"这件事解决掉。

    ⚠️ **必须能在任意线程里用**（2026-09-27 事故）：
    调用方大量走 `asyncio.to_thread(...)`（发骚/剧情/投票/公演的生成函数里顺手写状态），
    而 `Store` 是**模块级单例**（`state_store._store`）——它一旦在主线程建好，
    工作线程再碰它就会撞上 sqlite3 的 `check_same_thread` 默认保护：

        sqlite3.ProgrammingError: SQLite objects created in a thread can only be
        used in that same thread.

    而 `state_store` 把这个异常**吞掉并退回 JSON 文件**，于是同一个群的状态被劈成两半：
      · 主线程（`last_id`/发情值/指令链里的读写）→ sqlite
      · 工作线程（`scene()` 写发骚会话、`sync_profiles()` 写档案）→ state.json
    两边互相看不见。实测症状：`/发骚 X` 明明回了内容，发骚会话却只写进 state.json，
    主线程的收尾分支读到 sqlite 里"没有进行中的发骚" ⇒ `/结束` 永远静默。

    做法：`check_same_thread=False` + 一把可重入锁（sqlite3 的连接对象本身不是
    线程安全的，必须串行化）；真正的跨进程并发由 WAL + `busy_timeout` 兜住。
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        parent = Path(self.path).parent
        if str(parent) and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=15, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------ 读

    def load_scope(self, scope: str) -> dict:
        with self._lock:
            rows = list(self._conn.execute(
                "SELECT key, value FROM kv WHERE scope=?", (scope,)))
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                continue
        return out

    def get(self, scope: str, key: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM kv WHERE scope=? AND key=?",
                (scope, str(key))).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except Exception:
            return None

    # ------------------------------------------------------------ 写

    def save_scope(self, scope: str, mapping: dict) -> None:
        """整 scope 覆盖式 upsert（单事务）。

        注意：**不会删除** scope 里本次未出现的 key —— 多进程各自只写自己那部分。
        """
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")     # 先拿写锁，避免升级死锁
            try:
                for k, v in mapping.items():
                    self._conn.execute(
                        "INSERT INTO kv(scope, key, value, updated_at) VALUES(?,?,?,?)"
                        " ON CONFLICT(scope, key) DO UPDATE SET"
                        " value=excluded.value, updated_at=excluded.updated_at",
                        (scope, str(k), json.dumps(v, ensure_ascii=False), now))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def set(self, scope: str, key: str, value) -> None:
        self.save_scope(scope, {key: value})

    def delete(self, scope: str, key: str) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM kv WHERE scope=? AND key=?",
                                   (scope, str(key)))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------ meta

    def meta_get(self, k: str, default=None):
        with self._lock:
            row = self._conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["v"])
        except Exception:
            return default

    def meta_set(self, k: str, v) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(k, v) VALUES(?,?)"
                " ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v, ensure_ascii=False)))

    # ------------------------------------------------------------ 统计

    def counts(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT scope, COUNT(*) n FROM kv GROUP BY scope").fetchall()
        return {r["scope"]: r["n"] for r in rows}

    def updated_at(self, scope: str, key: str):
        """这一行最后写入的时间（对账用：判断 sqlite 与 JSON 谁更新）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT updated_at FROM kv WHERE scope=? AND key=?",
                (scope, str(key))).fetchone()
        return float(row["updated_at"]) if row is not None else None

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------ 一次性导入

    def import_json(self, state_json: Path, profiles_json: Path) -> dict:
        """把老的 JSON 导进 SQLite。**幂等**（靠 meta 标记），且**不删除** JSON。

        ⚠️ `state.json` 的**顶层键全部保留**，不要按"是不是 UUID"过滤 ——
        除 cid 外还有 `dice_game`（骰子局状态）这类键；过滤会让那些功能读成空。
        """
        if self.meta_get(IMPORT_MARKER):
            return {"skipped": True}

        stats = {"group": 0, "profiles": 0}

        try:
            raw = json.loads(Path(state_json).read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw:
                self.save_scope(SCOPE_GROUP, raw)
                stats["group"] = len(raw)
        except Exception:
            pass

        try:
            profs = json.loads(Path(profiles_json).read_text(encoding="utf-8"))
            if isinstance(profs, dict):
                self.save_scope(SCOPE_PROFILE, profs)
                stats["profiles"] = len(profs)
        except Exception:
            pass

        self.meta_set(IMPORT_MARKER, {"at": time.time(), **stats})
        return stats


def store_enabled() -> bool:
    """`DZMM_STORE=json` 可立即回退到 JSON 后端。"""
    v = (os.environ.get("DZMM_STORE") or "sqlite").strip().lower()
    return v not in ("json", "off", "0", "no", "false")
