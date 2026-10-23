"""认证群和茶水间消息归档；独立 SQLite，无保留期或自动删除。"""
import asyncio
import hashlib
import json
import math
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from runtime.ctx import PRIMARY_CID
from runtime.office_store import TEA_CID

ARCHIVE_GROUPS = {PRIMARY_CID: "认证妹子群", TEA_CID: "顶层茶水间"}

_warned_at = float("-inf")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  group_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  sender_id TEXT NOT NULL,
  sender_name TEXT NOT NULL,
  name_source TEXT NOT NULL,
  sent_at REAL NOT NULL,
  sent_at_original TEXT NOT NULL,
  message_type TEXT NOT NULL,
  text TEXT NOT NULL,
  content_json TEXT NOT NULL,
  source TEXT NOT NULL,
  source_file_sha256 TEXT NOT NULL,
  archived_at REAL NOT NULL,
  PRIMARY KEY (group_id, message_id),
  CHECK (group_id IN ('""" + PRIMARY_CID + """','""" + TEA_CID + """'))
);
CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(group_id, sent_at, message_id);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(group_id, sender_id, sent_at);
CREATE TABLE IF NOT EXISTS imports (
  sha256 TEXT PRIMARY KEY,
  file_name TEXT NOT NULL,
  imported_at REAL NOT NULL,
  records INTEGER NOT NULL,
  inserted INTEGER NOT NULL,
  duplicates INTEGER NOT NULL
);
"""
_INSERT = """
INSERT OR IGNORE INTO messages
(group_id, message_id, sender_id, sender_name, name_source, sent_at, sent_at_original,
 message_type, text, content_json, source, source_file_sha256, archived_at)
VALUES (:group_id, :message_id, :sender_id, :sender_name, :name_source, :sent_at,
        :sent_at_original, :message_type, :text, :content_json, :source,
        :source_file_sha256, :archived_at)
"""


def archive_path():
    configured = os.environ.get("DZMM_CHAT_ARCHIVE_DB", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parent.parent / "data" / "chat_archive.db"


def enabled():
    return os.environ.get("DZMM_CHAT_ARCHIVE", "off").strip().lower() in ("on", "1", "true", "yes")


def timestamp(value):
    if isinstance(value, bool):
        raise ValueError("invalid_time")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        result = parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    else:
        raise ValueError("missing_time")
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid_time")
    datetime.fromtimestamp(result, timezone.utc)
    return result


def _string(value, field, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("invalid_" + field)
    return value


def normalize(message, *, group_id, source, names=None, source_file_sha256=""):
    if group_id not in ARCHIVE_GROUPS:
        raise ValueError("outside_archive_scope")
    if not isinstance(message, dict):
        raise ValueError("invalid_message")
    for key in ("chatroom_id", "chat_id", "cid", "group_id"):
        if message.get(key) and message[key] != group_id:
            raise ValueError("message_group_mismatch")
    if source not in ("historical_import", "live_push", "live_http"):
        raise ValueError("invalid_source")
    historical = source == "historical_import"
    content = message.get("content") or {}
    if not isinstance(content, dict):
        raise ValueError("invalid_content")
    sender_id = _string(message.get("uid") if historical else message.get("sent_by"), "sender_id")
    sender_name = message.get("name") if historical else message.get("sender_name") or message.get("sent_by_name")
    name_source = "historical_export" if historical and sender_name else "message" if sender_name else "unknown"
    if not sender_name and names and sender_id in names:
        sender_name = names[sender_id]
        name_source = "member_cache"
    sent_at = message.get("sent_at")
    text = _string(message.get("text") if historical else content.get("text"), "text")
    message_type = _string(message.get("type") if historical else content.get("type") or "text", "type", required=True)
    if historical:
        content = {"type": message_type, "text": text}
    return {
        "group_id": group_id,
        "message_id": _string(message.get("id") if historical else message.get("message_id"), "message_id", required=True),
        "sender_id": sender_id,
        "sender_name": _string(sender_name, "sender_name"),
        "name_source": name_source,
        "sent_at": timestamp(sent_at),
        "sent_at_original": str(sent_at),
        "message_type": message_type,
        "text": text,
        "content_json": json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        "source": source,
        "source_file_sha256": source_file_sha256,
        "archived_at": time.time(),
    }


def _connect(path, timeout):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=timeout)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        # SQLite 不能原地修改 CHECK。事务重建旧表，失败即回滚，绝不清空原归档。
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'").fetchone()
            if old and "CHECK (group_id = '" in old[0]:
                create = _SCHEMA.split(";")[0].replace("messages (", "messages_v2 (")
                connection.execute(create)
                before = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
                connection.execute("INSERT INTO messages_v2 SELECT * FROM messages")
                after = connection.execute("SELECT count(*) FROM messages_v2").fetchone()[0]
                if before != after:
                    raise RuntimeError("archive_migration_count_mismatch")
                connection.execute("DROP TABLE messages")
                connection.execute("ALTER TABLE messages_v2 RENAME TO messages")
        connection.executescript(_SCHEMA)
        if os.name != "nt":
            for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                if candidate.exists():
                    candidate.chmod(0o600)
        return connection
    except Exception:
        connection.close()
        raise


def append_batch(messages, *, group_id, source, names=None, path=None):
    if group_id not in ARCHIVE_GROUPS:
        return {"inserted": 0, "duplicates": 0, "invalid": 0}
    rows = []
    invalid = 0
    for message in messages:
        try:
            rows.append(normalize(message, group_id=group_id, source=source, names=names))
        except (ValueError, TypeError, OverflowError, OSError):
            invalid += 1
    if not rows:
        return {"inserted": 0, "duplicates": 0, "invalid": invalid}
    with closing(_connect(path or archive_path(), 0.25)) as connection, connection:
        before = connection.total_changes
        connection.executemany(_INSERT, rows)
        inserted = connection.total_changes - before
    return {"inserted": inserted, "duplicates": len(rows) - inserted, "invalid": invalid}


def import_jsonl(file_path, *, group_id, path=None):
    if group_id not in ARCHIVE_GROUPS:
        raise ValueError("outside_archive_scope")
    file_path = Path(file_path)
    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    file_hash = digest.hexdigest()
    records = 0
    inserted = 0
    with closing(_connect(path or archive_path(), 15)) as connection, connection:
        with file_path.open(encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    row = normalize(message, group_id=group_id, source="historical_import", source_file_sha256=file_hash)
                except (ValueError, TypeError, OverflowError, OSError) as error:
                    raise ValueError("invalid_record_at_line_" + str(line_number)) from error
                records += 1
                inserted += connection.execute(_INSERT, row).rowcount
        connection.execute(
            "INSERT OR IGNORE INTO imports (sha256,file_name,imported_at,records,inserted,duplicates) VALUES (?,?,?,?,?,?)",
            (file_hash, file_path.name, time.time(), records, inserted, records - inserted))
    return {"records": records, "inserted": inserted, "duplicates": records - inserted, "sha256": file_hash}


def _warn(reason):
    global _warned_at
    now = time.monotonic()
    if now - _warned_at >= 60:
        _warned_at = now
        try:
            from runtime.obs import log
            log("chat_archive: " + reason + "（归档失败或跳过，不影响群消息处理）")
        except Exception:
            return


async def capture(group_id, messages, *, source):
    if not enabled() or group_id not in ARCHIVE_GROUPS or not messages:
        return
    try:
        from runtime.identity import _name_cache
        names = dict(_name_cache)
        result = await asyncio.to_thread(append_batch, messages, group_id=group_id, source=source, names=names)
        if result["invalid"]:
            _warn("invalid_records=" + str(result["invalid"]))
    except Exception as error:
        _warn(type(error).__name__)
