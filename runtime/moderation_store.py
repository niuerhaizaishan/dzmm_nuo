"""持久保存待确认任务、汇总快照及不可重放的操作状态。"""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, cid TEXT NOT NULL, message_id TEXT NOT NULL,
                    uid TEXT NOT NULL, name TEXT NOT NULL, excerpt TEXT NOT NULL,
                    sent_at TEXT NOT NULL, created REAL NOT NULL, actor TEXT NOT NULL,
                    status TEXT NOT NULL, prompt_id TEXT UNIQUE, confirmed_by TEXT,
                    result TEXT, updated REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_target
                    ON tasks(cid,message_id) WHERE status != 'cancelled';
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, cid TEXT NOT NULL, task_ids TEXT NOT NULL,
                    status TEXT NOT NULL, prompt_id TEXT UNIQUE, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commands (
                    cid TEXT NOT NULL, message_id TEXT NOT NULL, actor TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(cid, message_id)
                );
                CREATE TABLE IF NOT EXISTS prompts (
                    cid TEXT NOT NULL, message_id TEXT NOT NULL,
                    kind TEXT NOT NULL, item_id TEXT NOT NULL,
                    PRIMARY KEY(cid, message_id)
                );
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def read(self, sql, args=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, args)]

    def command(self, cid, message_id, actor):
        with self.connect() as connection:
            return connection.execute(
                "INSERT OR IGNORE INTO commands VALUES (?,?,?,?)",
                (cid, message_id, actor, time.time())).rowcount == 1

    def create(self, cid, message, name, actor):
        task_id = uuid4().hex
        now = time.time()
        content = message.get("content") or {}
        excerpt = " ".join(str(content.get("text") or "[非文字消息]").split())[:80]
        with self.connect() as connection:
            created = connection.execute(
                "INSERT OR IGNORE INTO tasks "
                "(id,cid,message_id,uid,name,excerpt,sent_at,created,actor,status,updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,'prepared',?)",
                (task_id, cid, message["message_id"], message["sent_by"], name,
                 excerpt, message.get("sent_at") or "", now, actor, now)).rowcount == 1
        task = self.read("SELECT * FROM tasks WHERE cid=? AND message_id=? AND status != 'cancelled'",
                         (cid, message["message_id"]))[0]
        return task, created

    def bind(self, kind, item_id, prompt_id, extra_ids=()):
        table = "tasks" if kind == "task" else "batches"
        status = "pending" if kind == "task" else "active"
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"UPDATE {table} SET prompt_id=?,status=? "
                               "WHERE id=? AND status='prepared'",
                               (prompt_id, status, item_id))
            cid = connection.execute(f"SELECT cid FROM {table} WHERE id=?", (item_id,)).fetchone()["cid"]
            for message_id in [prompt_id, *extra_ids]:
                connection.execute("INSERT INTO prompts VALUES (?,?,?,?)",
                                   (cid, message_id, kind, item_id))

    def pending(self, cid):
        return self.read("SELECT * FROM tasks WHERE cid=? AND status='pending' "
                         "ORDER BY created,id", (cid,))

    def reference(self, cid, prompt_id):
        prompts = self.read("SELECT * FROM prompts WHERE cid=? AND message_id=?", (cid, prompt_id))
        if prompts:
            prompt = prompts[0]
            table = "tasks" if prompt["kind"] == "task" else "batches"
            rows = self.read(f"SELECT * FROM {table} WHERE cid=? AND id=?", (cid, prompt["item_id"]))
            if rows:
                return prompt["kind"], rows[0]
        return None, None

    def active_batch(self, cid):
        rows = self.read("SELECT * FROM batches WHERE cid=? AND status='active' "
                         "ORDER BY created DESC LIMIT 1", (cid,))
        return rows[0] if rows else None

    def batch(self, cid, tasks):
        batch_id = uuid4().hex
        task_ids = [task["id"] for task in tasks]
        with self.connect() as connection:
            connection.execute("INSERT INTO batches VALUES (?,?,?,'prepared',NULL,?)",
                               (batch_id, cid, json.dumps(task_ids), time.time()))
        return batch_id

    def select(self, cid, task_ids, actor, cancel=False, batch_id=None):
        selected = []
        status = "cancelled" if cancel else "executing"
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if batch_id:
                if connection.execute("UPDATE batches SET status='consumed' "
                                      "WHERE id=? AND cid=? AND status='active'",
                                      (batch_id, cid)).rowcount != 1:
                    return []
            for task_id in task_ids:
                changed = connection.execute(
                    "UPDATE tasks SET status=?,confirmed_by=?,updated=? "
                    "WHERE id=? AND cid=? AND status='pending'",
                    (status, actor, time.time(), task_id, cid)).rowcount
                if changed:
                    selected.append(dict(connection.execute(
                        "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()))
        return selected

    def cancel_all(self, cid, actor):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("UPDATE tasks SET status='cancelled',confirmed_by=?,"
                                       "updated=? WHERE cid=? AND status IN ('pending','prepared')",
                                       (actor, time.time(), cid)).rowcount
            connection.execute("UPDATE batches SET status='cancelled' WHERE cid=? "
                               "AND status IN ('active','prepared')", (cid,))
        return count

    def finish(self, task_id, status, result):
        with self.connect() as connection:
            connection.execute("UPDATE tasks SET status=?,result=?,updated=? "
                               "WHERE id=? AND status='executing'",
                               (status, result, time.time(), task_id))

    def progress(self, task_id, result):
        with self.connect() as connection:
            connection.execute("UPDATE tasks SET result=?,updated=? "
                               "WHERE id=? AND status='executing'",
                               (result, time.time(), task_id))
