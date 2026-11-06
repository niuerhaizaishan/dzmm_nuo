"""茶水间双人剧情：事务额度、参与者互斥、原指令幂等与恢复。

2026-11-05 起支持「付费概率玩法」：首段要扣摸鱼币、按职级差抽一次概率、成功给目标分账。
钱包本身不在这个模块里（见 runtime/office_wallet.py），这里只负责**状态与账务记录**：
每一笔扣款/分账/退款都有自己的幂等编号，状态落库，重启后能继续核对或补偿。
"""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from runtime.office_rules import MAX_CONTINUES

TEA_CID = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
IDLE_SECONDS = 300
GENERATION_SECONDS = 120
ZONE = timezone(timedelta(hours=8))
_INIT_LOCK = threading.Lock()

# 账务动作的幂等键后缀（同一会话内唯一）
KV_FEE = "fee"                   # 首段扣款
KV_REFUND = "refund"             # 首段退款
KV_SPLIT = "split"               # 目标分账
KV_CONT = "cont"                 # 续写扣款（cont:<n>）
KV_CONT_REFUND = "contrefund"    # 续写退款
KV_CONT_RETURN = "contreturn"    # 原目标本人续写时的返还


class RuleError(ValueError):
    pass


def local_day(now):
    return datetime.fromtimestamp(now, ZONE).date().isoformat()


NEW_SESSION_COLUMNS = {
    "fee": "INTEGER NOT NULL DEFAULT 0",
    "extra": "INTEGER NOT NULL DEFAULT 0",
    "base_probability": "REAL NOT NULL DEFAULT 0",
    "probability": "REAL NOT NULL DEFAULT 0",
    "roll": "REAL",                            # 0~1 随机数（持久化，重试复用）
    "outcome": "TEXT NOT NULL DEFAULT ''",     # win / lose
    "relation": "TEXT NOT NULL DEFAULT ''",    # up / down / peer
    "split_ratio": "REAL NOT NULL DEFAULT 0",
    "payer": "TEXT NOT NULL DEFAULT ''",       # 最近一段的付款人
    "paid_continues": "INTEGER NOT NULL DEFAULT 0",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    cid TEXT, uid TEXT, allowed INTEGER NOT NULL, updated REAL NOT NULL,
    PRIMARY KEY(cid,uid));
CREATE TABLE IF NOT EXISTS commands (
    cid TEXT, mid TEXT, created REAL NOT NULL, PRIMARY KEY(cid,mid));
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, cid TEXT NOT NULL, actor TEXT NOT NULL, target TEXT NOT NULL,
    day TEXT NOT NULL, status TEXT NOT NULL, deadline REAL NOT NULL,
    last_ref TEXT NOT NULL, history TEXT NOT NULL DEFAULT '[]',
    profiles TEXT NOT NULL DEFAULT '[]', continues INTEGER NOT NULL DEFAULT 0,
    operation TEXT NOT NULL, prior_deadline REAL NOT NULL DEFAULT 0,
    output TEXT NOT NULL DEFAULT '', platform_mid TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS participants (
    cid TEXT, uid TEXT, session_id TEXT NOT NULL, PRIMARY KEY(cid,uid));
CREATE TABLE IF NOT EXISTS quota (
    cid TEXT, uid TEXT, day TEXT, session_id TEXT NOT NULL, state TEXT NOT NULL,
    PRIMARY KEY(cid,uid,day));
CREATE TABLE IF NOT EXISTS notices (
    id TEXT PRIMARY KEY, cid TEXT NOT NULL, reference TEXT NOT NULL,
    text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS wallet_ops (
    key TEXT PRIMARY KEY, cid TEXT NOT NULL, sid TEXT NOT NULL, kind TEXT NOT NULL,
    uid TEXT NOT NULL, amount INTEGER NOT NULL, state TEXT NOT NULL,
    remote TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
"""


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("DZMM_OFFICE_DB") or
                         Path(__file__).resolve().parent.parent / "data" / "office.db")

    # ---- 事务与迁移 ----
    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            with _INIT_LOCK:
                for attempt in range(6):
                    try:
                        conn.execute("PRAGMA journal_mode=WAL")
                        break
                    except sqlite3.OperationalError as error:
                        if "locked" not in str(error).lower() or attempt == 5:
                            raise
                        time.sleep(0.02 * (attempt + 1))
                conn.executescript(SCHEMA)
                self._migrate(conn)
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                yield conn

    @staticmethod
    def _migrate(conn):
        """老库就地补列（SQLite 没有 ADD COLUMN IF NOT EXISTS）。"""
        have = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        for name, decl in NEW_SESSION_COLUMNS.items():
            if name not in have:
                conn.execute("ALTER TABLE sessions ADD COLUMN {} {}".format(name, decl))

    # ---- 账务 ----
    def wallet_record(self, key, cid, sid, kind, uid, amount, state="pending",
                      remote="", detail=""):
        with self.transaction() as conn:
            return bool(conn.execute(
                "INSERT OR IGNORE INTO wallet_ops VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, cid, sid, kind, uid, int(amount), state, remote, detail,
                 time.time())).rowcount)

    def wallet_update(self, key, state, remote="", detail=""):
        with self.transaction() as conn:
            conn.execute("UPDATE wallet_ops SET state=?, remote=?, detail=?, updated=? WHERE key=?",
                         (state, remote, detail, time.time(), key))

    def wallet_op(self, key):
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM wallet_ops WHERE key=?", (key,)).fetchone()
            return dict(row) if row else None

    def wallet_pending(self, sid=None):
        """待核对/待补发的账务（重启后补偿用）。"""
        with self.transaction() as conn:
            if sid:
                rows = conn.execute("SELECT * FROM wallet_ops WHERE sid=? AND state "
                                    "IN ('pending','unknown','owed')", (sid,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM wallet_ops WHERE state "
                                    "IN ('pending','unknown','owed')").fetchall()
            return [dict(row) for row in rows]

    # ---- 偏好与幂等 ----
    def claim_command(self, cid, mid, now=None):
        if not mid:
            return False
        with self.transaction() as conn:
            return bool(conn.execute("INSERT OR IGNORE INTO commands VALUES (?,?,?)",
                                     (cid, mid, time.time() if now is None else now)).rowcount)

    @staticmethod
    def _allowed(conn, cid, uid):
        row = conn.execute("SELECT allowed FROM preferences WHERE cid=? AND uid=?",
                           (cid, uid)).fetchone()
        return row is None or bool(row[0])

    # ---- 会话 ----
    @staticmethod
    def _close(conn, session, status):
        conn.execute("UPDATE sessions SET status=? WHERE id=?", (status, session["id"]))
        conn.execute("DELETE FROM participants WHERE session_id=?", (session["id"],))
        # 仅"未尝试投递的首段"可释放额度；sending / ambiguous 保守保留。
        if session["status"] in ("generating", "ready") and not json.loads(session["history"]):
            conn.execute("DELETE FROM quota WHERE session_id=? AND state='reserved'",
                         (session["id"],))

    def preference(self, cid, uid, allowed, now=None):
        now = time.time() if now is None else now
        with self.transaction() as conn:
            conn.execute("INSERT INTO preferences VALUES (?,?,?,?) ON CONFLICT(cid,uid) DO UPDATE SET "
                         "allowed=excluded.allowed,updated=excluded.updated",
                         (cid, uid, int(allowed), now))
            if not allowed:
                row = self._current(conn, cid, uid)
                if row:
                    self._close(conn, row, "refused")

    @staticmethod
    def _current(conn, cid, uid):
        return conn.execute("SELECT s.* FROM sessions s JOIN participants p ON s.id=p.session_id "
                            "WHERE p.cid=? AND p.uid=?", (cid, uid)).fetchone()

    def current(self, cid, uid):
        with self.transaction() as conn:
            row = self._current(conn, cid, uid)
            return dict(row) if row else None

    def get(self, sid):
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            return dict(row) if row else None

    def status(self, cid, uid, now=None):
        now = time.time() if now is None else now
        with self.transaction() as conn:
            used = conn.execute("SELECT 1 FROM quota WHERE cid=? AND uid=? AND day=?",
                                (cid, uid, local_day(now))).fetchone()
            row = self._current(conn, cid, uid)
            pending = conn.execute("SELECT COUNT(*) FROM wallet_ops WHERE cid=? AND uid=? "
                                   "AND state IN ('pending','unknown','owed')",
                                   (cid, uid)).fetchone()[0]
            data = {"allowed": self._allowed(conn, cid, uid),
                    "remaining": 0 if used else 1, "active": bool(row),
                    "pending_ops": int(pending or 0)}
            if row:
                current = dict(row)
                data.update({
                    "session": current["id"],
                    "continues": current["continues"],
                    "paid_continues": current["paid_continues"],
                    "fee": current["fee"],
                    "outcome": current["outcome"],
                })
            return data

    def start(self, cid, actor, target, reference, profiles, now=None, **billing):
        """预占额度并建会话。

        billing 可带 fee/extra/base_probability/probability/roll/relation/split_ratio；
        缺省就是老行为（不收费）。返回 (sid, operation)。
        """
        now = time.time() if now is None else now
        with self.transaction() as conn:
            if not self._allowed(conn, cid, target):
                raise RuleError("对方已拒绝潜规则，等对方自己发送 #允许潜规则 再来。")
            if self._current(conn, cid, actor) or self._current(conn, cid, target):
                raise RuleError("你们当中有人还在另一场剧情里哦～先结束当前会话再来。")
            sid, operation, day = str(uuid.uuid4()), str(uuid.uuid4()), local_day(now)
            try:
                conn.execute("INSERT INTO quota VALUES (?,?,?,?,?)",
                             (cid, actor, day, sid, "reserved"))
            except sqlite3.IntegrityError:
                raise RuleError("今天的机会已经用掉啦～明天再来，先安心上班！") from None
            conn.execute(
                "INSERT INTO sessions (id,cid,actor,target,day,status,deadline,last_ref,profiles,"
                "operation,payer,fee,extra,base_probability,probability,roll,relation,split_ratio) "
                "VALUES (?,?,?,?,?,'generating',?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, cid, actor, target, day, now + GENERATION_SECONDS,
                 json.dumps(reference, ensure_ascii=False),
                 json.dumps(profiles, ensure_ascii=False), operation, actor,
                 int(billing.get("fee") or 0), int(billing.get("extra") or 0),
                 float(billing.get("base_probability") or 0),
                 float(billing.get("probability") or 0),
                 billing.get("roll"),
                 str(billing.get("relation") or ""), float(billing.get("split_ratio") or 0)))
            conn.executemany("INSERT INTO participants VALUES (?,?,?)",
                             [(cid, actor, sid), (cid, target, sid)])
            return sid, operation

    def begin_continue(self, cid, uid, reference, profiles, now=None,
                       limit=None, expected_sid=None, payer=None):
        now = time.time() if now is None else now
        limit = MAX_CONTINUES if limit is None else limit
        with self.transaction() as conn:
            row = self._current(conn, cid, uid)
            if (not row or row["status"] != "active" or row["deadline"] <= now
                    or (expected_sid is not None and row["id"] != expected_sid)):
                raise RuleError("当前没有可以继续的会话，可能已经结束或正在处理上一段。")
            if row["continues"] >= limit:
                raise RuleError("本场续写次数已经用完啦～请发送 #潜规则结束 收尾。")
            if row["outcome"] == "lose":
                raise RuleError("这一场没有成功，不能续写。")
            if not self._allowed(conn, cid, row["target"]):
                self._close(conn, row, "refused")
                return None
            operation = str(uuid.uuid4())
            conn.execute("UPDATE sessions SET status='generating',prior_deadline=deadline,"
                         "deadline=?,operation=?,last_ref=?,profiles=?,payer=? WHERE id=?",
                         (now + GENERATION_SECONDS, operation,
                          json.dumps(reference, ensure_ascii=False),
                          json.dumps(profiles, ensure_ascii=False),
                          payer or uid, row["id"]))
            return row["id"], operation

    def ready(self, sid, operation, output, now=None):
        now = time.time() if now is None else now
        with self.transaction() as conn:
            return bool(conn.execute(
                "UPDATE sessions SET status='ready',output=? WHERE id=? AND operation=? "
                "AND status='generating' AND deadline>?",
                (output, sid, operation, now)).rowcount)

    def set_outcome(self, sid, operation, outcome):
        with self.transaction() as conn:
            conn.execute("UPDATE sessions SET outcome=? WHERE id=? AND operation=?",
                         (outcome, sid, operation))

    def fail_generation(self, sid, operation):
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=? AND operation=?",
                               (sid, operation)).fetchone()
            if not row or row["status"] not in ("generating", "ready"):
                return False
            if json.loads(row["history"]):
                conn.execute("UPDATE sessions SET status='active',deadline=prior_deadline,"
                             "output='' WHERE id=?", (sid,))
            else:
                self._close(conn, row, "failed")
            return True

    def dispatch(self, sid, operation, now=None):
        """发送前最后检查，持久记录 sending；进程崩溃时不盲目重发。"""
        now = time.time() if now is None else now
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=? AND operation=?",
                               (sid, operation)).fetchone()
            if not row or row["status"] != "ready" or row["deadline"] <= now:
                return None
            if not self._allowed(conn, row["cid"], row["target"]):
                self._close(conn, row, "refused")
                return None
            conn.execute("UPDATE sessions SET status='sending',deadline=? WHERE id=?",
                         (now + IDLE_SECONDS, sid))
            return dict(row)

    def delivered(self, sid, operation, platform_mid, now=None, *, outcome=None):
        """确认落地：记账、累加历史与续写计数，成功转 active、概率失败直接关本场。"""
        now = time.time() if now is None else now
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=? AND operation=?",
                               (sid, operation)).fetchone()
            if not row:
                return None
            history = json.loads(row["history"])
            conn.execute("UPDATE quota SET state='used' WHERE session_id=?", (sid,))
            if row["status"] != "sending":
                return None
            final_outcome = outcome or row["outcome"] or "win"
            if final_outcome == "lose":
                self._close(conn, row, "lost")
                return dict(row)
            paid = row["paid_continues"] + (1 if history else 0)
            conn.execute(
                "UPDATE sessions SET status='active',deadline=?,history=?,continues=?,"
                "paid_continues=?,platform_mid=?,outcome=? WHERE id=?",
                (now + IDLE_SECONDS, json.dumps(history + [row["output"]], ensure_ascii=False),
                 row["continues"] + int(bool(history)), paid, platform_mid,
                 final_outcome, sid))
            return dict(row)

    def ambiguous(self, sid, operation):
        with self.transaction() as conn:
            conn.execute("UPDATE sessions SET status='ambiguous' WHERE id=? AND operation=? "
                         "AND status='sending'", (sid, operation))

    def retry_delivery(self, sid, operation):
        """仅在发送器确定未投递时调用，保留同一份正文及原指令。"""
        with self.transaction() as conn:
            return bool(conn.execute(
                "UPDATE sessions SET status='ready' WHERE id=? AND operation=? "
                "AND status IN ('ready','sending')", (sid, operation)).rowcount)

    def end(self, cid, uid, status="ended", expected_sid=None):
        with self.transaction() as conn:
            row = self._current(conn, cid, uid)
            if not row or (expected_sid is not None and row["id"] != expected_sid):
                return False
            self._close(conn, row, status)
            return True

    def release_quota(self, sid):
        """系统失败：退还当日发起次数（只撤 reserved，已用掉的不动）。"""
        with self.transaction() as conn:
            conn.execute("DELETE FROM quota WHERE session_id=? AND state='reserved'", (sid,))

    def sweep(self, cid, now=None, enabled=True):
        now = time.time() if now is None else now
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT s.* FROM sessions s WHERE cid=? AND status IN "
                "('generating','ready','active','sending','ambiguous') AND (deadline<=? OR ?=0)",
                (cid, now, int(enabled))).fetchall()
            for row in rows:
                self._close(conn, row, "expired" if enabled else "disabled")
                text = ("已连续 5 分钟无互动，本场结束。" if row["status"] == "active" else
                        "本场处理超时或投递结果待核对，会话已结束。") if enabled else "本功能已关闭，本场结束。"
                conn.execute("INSERT OR IGNORE INTO notices (id,cid,reference,text) VALUES (?,?,?,?)",
                             (row["id"], cid, row["last_ref"], text))
            notices = conn.execute("SELECT * FROM notices WHERE cid=? AND state='pending'",
                                   (cid,)).fetchall()
            conn.executemany("UPDATE notices SET state='sending' WHERE id=?",
                             [(r["id"],) for r in notices])
            return [dict(row) for row in notices]

    def notice_result(self, notice_id, success):
        with self.transaction() as conn:
            conn.execute("UPDATE notices SET state=? WHERE id=?",
                         ("sent" if success else "ambiguous", notice_id))
