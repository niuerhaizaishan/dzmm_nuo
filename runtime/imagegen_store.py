"""生成媒体任务与原始结果；独立于公开图集，仅凭随机能力地址读取。"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

import env_config


def store_path():
    return Path(os.environ.get("DZMM_IMAGEGEN_DB") or env_config.data_dir() / "imagegen.db")


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else store_path()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS imagegen_jobs (
            cid TEXT NOT NULL, mid TEXT NOT NULL, uid TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, mode TEXT NOT NULL,
            status TEXT NOT NULL, token TEXT UNIQUE, data BLOB, mime TEXT,
            sent_id TEXT, PRIMARY KEY (cid, mid))""")
        conn.execute("CREATE INDEX IF NOT EXISTS imagegen_user ON imagegen_jobs(cid,uid,created)")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(imagegen_jobs)")}
        if "reference" not in columns:
            conn.execute("ALTER TABLE imagegen_jobs ADD COLUMN reference TEXT")
        if "request_id" not in columns:
            conn.execute("ALTER TABLE imagegen_jobs ADD COLUMN request_id TEXT")
        if "platform_url" not in columns:
            conn.execute("ALTER TABLE imagegen_jobs ADD COLUMN platform_url TEXT")
        for name in ("platform_media", "result_reply_status", "result_reply_id"):
            if name not in columns:
                conn.execute("ALTER TABLE imagegen_jobs ADD COLUMN " + name + " TEXT")
        conn.commit()
        return conn

    def get(self, cid, mid):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM imagegen_jobs WHERE cid=? AND mid=?", (cid, mid)).fetchone()
            return dict(row) if row else None

    def reserve(self, cid, mid, uid, mode, cooldown):
        now = time.time()
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM imagegen_jobs WHERE cid=? AND mid=?", (cid, mid)).fetchone():
                return "duplicate"
            if conn.execute("SELECT 1 FROM imagegen_jobs WHERE cid=? AND uid=? AND created>?",
                            (cid, uid, now - cooldown)).fetchone():
                return "cooldown"
            conn.execute("INSERT INTO imagegen_jobs(cid,mid,uid,created,updated,mode,status) VALUES(?,?,?,?,?,?,?)",
                         (cid, mid, uid, now, now, mode, "queued"))
            return "queued"

    def status(self, cid, mid, status, sent_id=None):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET status=?,updated=?,sent_id=? WHERE cid=? AND mid=?",
                         (status, time.time(), sent_id, cid, mid))

    def mode(self, cid, mid, mode):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET mode=? WHERE cid=? AND mid=?", (mode, cid, mid))

    def save_reference(self, cid, mid, reference):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET reference=? WHERE cid=? AND mid=?",
                         (json.dumps(reference, ensure_ascii=False), cid, mid))

    def save_request_id(self, cid, mid, request_id):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET request_id=? WHERE cid=? AND mid=?",
                         (request_id, cid, mid))

    def reference(self, cid, mid):
        row = self.get(cid, mid)
        return json.loads(row["reference"]) if row and row["reference"] else None

    def save_media(self, cid, mid, data, mime):
        token = secrets.token_hex(32)
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET data=?,mime=?,token=?,status='ready',updated=? WHERE cid=? AND mid=?",
                         (data, mime, token, time.time(), cid, mid))
        return token

    def save_image(self, cid, mid, data, mime):
        """兼容旧调用；媒体表按 MIME 同时保存图片和视频。"""
        return self.save_media(cid, mid, data, mime)

    def image(self, token):
        # A read must never create a database or accept a path as a token.
        if not re.fullmatch(r"[a-f0-9]{64}", token or "") or not self.path.is_file():
            return None
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT data,mime FROM imagegen_jobs WHERE token=? AND data IS NOT NULL",
                               (token,)).fetchone()
            return {"data": row[0], "mime": row[1]} if row else None

    media = image

    def platform_url(self, token):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT platform_url FROM imagegen_jobs WHERE token=?", (token,)).fetchone()
            return row[0] if row else None

    def save_platform_url(self, token, url):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET platform_url=? WHERE token=?", (url, token))

    def platform_media(self, token):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT platform_media FROM imagegen_jobs WHERE token=?", (token,)).fetchone()
            return json.loads(row[0]) if row and row[0] else None

    def save_platform_media(self, token, content):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET platform_media=?,platform_url=? WHERE token=?",
                         (json.dumps(content), content["videoUrl"], token))

    def claim_result_reply(self, cid, mid):
        # A possibly delivered receipt must never be replayed after a restart.
        with closing(self.connect()) as conn, conn:
            changed = conn.execute("UPDATE imagegen_jobs SET result_reply_status='sending' "
                "WHERE cid=? AND mid=? AND status='sent' AND "
                "(result_reply_status IS NULL OR result_reply_status='failed')", (cid, mid))
            return changed.rowcount == 1

    def result_reply(self, cid, mid, status, reply_id=None):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET result_reply_status=?,result_reply_id=? WHERE cid=? AND mid=?",
                         (status, reply_id, cid, mid))

    def recover(self, kind=None):
        # Neither interrupted generation nor an ambiguous send may be replayed automatically.
        # 图片和视频共用一个文件，懒启动其中一个服务时不能改掉另一个的在途状态。
        scope = {None: "", "image": " AND mode NOT LIKE 'video%'",
                 "video": " AND mode LIKE 'video%'"}[kind]
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE imagegen_jobs SET status='interrupted',updated=? WHERE status IN ('queued','generating')" + scope,
                         (time.time(),))
            conn.execute("UPDATE imagegen_jobs SET status='delivery_unknown',updated=? WHERE status='sending'" + scope,
                         (time.time(),))
