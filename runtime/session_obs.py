"""可关闭、有界的会话取证；不参与凭据选择或恢复决策。"""
import asyncio
import base64
import contextvars
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:
    fcntl = None

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COOKIE_NAME = "sb-rls-auth-token"
SAMPLE_TIMEOUT = 0.2
MAX_JOURNAL_BYTES = 512 * 1024
MAX_EVENTS_PER_MINUTE = 120
NORMAL_INTERVAL = 10.0
INSTANCE = str(os.getpid()) + "-" + uuid.uuid4().hex[:12]
_operation = contextvars.ContextVar("session_observation", default=None)
_lock = threading.Lock()
_recent = {}
_window = 0.0
_count = 0
_dropped = 0
_warned_at = float("-inf")


def enabled():
    return os.environ.get("DZMM_SESSION_OBS", "off").strip().lower() in ("1", "on", "true", "yes")


def new_trace():
    return uuid.uuid4().hex


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value) else ""


def recovery_env(trace):
    if not enabled():
        return None
    environment = dict(os.environ)
    environment["DZMM_SESSION_OBS_TRACE"] = _identifier(trace)
    environment["DZMM_SESSION_OBS_PARENT"] = INSTANCE
    return environment


def _warn(error):
    global _warned_at
    now = time.monotonic()
    if now - _warned_at < 60:
        return
    _warned_at = now
    try:
        sys.stderr.write("session_obs: " + type(error).__name__ + " (diagnostic only)\n")
    except Exception:
        _warned_at = now


def begin_sync():
    if not enabled():
        return None
    from runtime.ctx import get_active_cid
    return _operation.set({"trace": new_trace(), "harvest": "not_observed", "cid": get_active_cid()})


def end_sync(context_token):
    if context_token is not None:
        _operation.reset(context_token)


def _fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24] if value else ""


def cookie_state(cookies, origin):
    host = urlsplit(origin).hostname or ""
    entries = []
    total = 0
    for cookie in cookies:
        if cookie.get("name") != COOKIE_NAME:
            continue
        total += 1
        if len(entries) >= 8:
            continue
        value = cookie.get("value") or ""
        domain = str(cookie.get("domain") or "")[:253].lower()
        cookie_expires = cookie.get("expires")
        if not isinstance(cookie_expires, (int, float)) or isinstance(cookie_expires, bool) or not math.isfinite(cookie_expires):
            cookie_expires = None
        entry = {"domain": domain, "path": str(cookie.get("path") or "/")[:256],
                 "fingerprint": _fingerprint(value), "token_expires_at": None,
                 "cookie_expires": cookie_expires, "parse": "invalid",
                 "matches_origin": bool(domain) and (host == domain.lstrip(".") or
                     (domain.startswith(".") and host.endswith(domain)))}
        try:
            encoded = value[7:] if value.startswith("base64-") else value
            if len(encoded) <= 16384:
                payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
                expires = payload.get("expires_at") if isinstance(payload, dict) else None
                if isinstance(expires, (int, float)) and not isinstance(expires, bool) and math.isfinite(expires) and expires > 0:
                    entry["token_expires_at"] = expires
                    entry["parse"] = "ok"
        except (ValueError, TypeError, UnicodeError):
            entry["parse"] = "invalid"
        entries.append(entry)
    return {"sampled_at": time.time(), "status": "ok", "origin": urlsplit(origin).scheme + "://" + host,
            "cookie_count": total,
            "truncated": total > len(entries), "cookies": entries}


def _pairs(browser, stored):
    pairs = []
    for incoming in stored.get("cookies", []):
        matches = [cookie for cookie in browser.get("cookies", [])
                   if cookie["domain"] == incoming["domain"] and cookie["path"] == incoming["path"]]
        current = matches[0] if len(matches) == 1 else None
        relation = "missing_or_ambiguous"
        if current:
            if current["fingerprint"] == incoming["fingerprint"]:
                relation = "same"
            elif current["parse"] != "ok" or incoming["parse"] != "ok":
                relation = "unorderable"
            elif current["token_expires_at"] > incoming["token_expires_at"]:
                relation = "browser_exp_later"
            elif current["token_expires_at"] < incoming["token_expires_at"]:
                relation = "file_exp_later"
            else:
                relation = "different_equal_exp"
        pairs.append({"domain": incoming["domain"], "path": incoming["path"],
                      "matches_origin": incoming["matches_origin"], "relation": relation})
    return pairs


def _write(record, snapshot, important):
    global _window, _count, _dropped
    now = time.monotonic()
    key = (record["event"], record.get("result", ""))
    if not important and now - _recent.get(key, float("-inf")) < NORMAL_INTERVAL:
        _dropped += 1
        return
    if now - _window >= 60:
        _window, _count = now, 0
    if _count >= MAX_EVENTS_PER_MINUTE:
        _dropped += 1
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / ".session_obs.lock", "a+b") as lockfile:
        if fcntl is not None:
            try:
                fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _dropped += 1
                return
        elif os.name == "nt":
            import msvcrt
            if lockfile.seek(0, os.SEEK_END) == 0:
                lockfile.write(b"0")
                lockfile.flush()
            lockfile.seek(0)
            try:
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                _dropped += 1
                return
        record["dropped_since_last_write"] = _dropped
        payload = json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
        if len(payload) > 32768:
            raise ValueError("observation_oversized")
        journal = DATA_DIR / "session_events.jsonl"
        if journal.exists() and journal.stat().st_size + len(payload) > MAX_JOURNAL_BYTES:
            os.replace(journal, DATA_DIR / "session_events.jsonl.1")
        with open(journal, "a", encoding="utf-8") as stream:
            stream.write(payload)
        if snapshot:
            temporary = DATA_DIR / (".page_token_state-" + INSTANCE + ".tmp")
            try:
                temporary.write_text(payload, encoding="utf-8")
                os.replace(temporary, DATA_DIR / "page_token_state.json")
            finally:
                temporary.unlink(missing_ok=True)
        _count += 1
        _dropped = 0
        if len(_recent) >= 64:
            _recent.clear()
        _recent[key] = now


def event(name, *, trace="", cid="", source="", result="", error_type="",
          child_pid=None, elapsed=None, login_mode="", parent_trace="", details=None,
          snapshot=False, important=True):
    if not enabled():
        return
    try:
        operation = _operation.get() or {}
        record = {"schema": 1, "at": time.time(), "instance": INSTANCE,
                  "pid": os.getpid(), "event": _identifier(name),
                  "trace": _identifier(trace) or operation.get("trace") or
                      _identifier(os.environ.get("DZMM_SESSION_OBS_TRACE", "")) or new_trace(),
                  "parent_instance": _identifier(os.environ.get("DZMM_SESSION_OBS_PARENT", ""))}
        for key, value in (("source", source), ("result", result), ("error_type", error_type),
                           ("login_mode", login_mode), ("parent_trace", parent_trace)):
            if value:
                record[key] = _identifier(value)
        if cid:
            record["group_fingerprint"] = _fingerprint(cid)
        if operation.get("cid"):
            record["task_group_fingerprint"] = _fingerprint(operation["cid"])
        if isinstance(child_pid, int):
            record["child_pid"] = child_pid
        if elapsed is not None:
            record["elapsed_seconds"] = round(elapsed, 3)
        if details is not None:
            record["details"] = details
        if _lock.acquire(blocking=False):
            try:
                _write(record, snapshot, important)
            finally:
                _lock.release()
    except Exception as error:
        _warn(error)


def harvest(result, *, browser=None, stored=None, origin="", error_type=""):
    if not enabled():
        return
    try:
        operation = _operation.get()
        if operation is not None:
            operation["harvest"] = result
        details = {}
        if browser is not None:
            details["browser_context_cookie"] = cookie_state(browser, origin)
        if stored is not None:
            details["file_cookie"] = cookie_state(stored, origin)
        event("harvest", result=result, error_type=error_type, details=details,
              important=result in ("write_returned_false", "write_readback_mismatch", "error"))
    except Exception as error:
        _warn(error)


def harvest_written(saved, browser_cookie, reader, origin):
    if not enabled():
        return
    try:
        stored = reader() or {}
        matches = stored.get("value") == browser_cookie.get("value")
        result = "write_verified" if saved and matches else "write_readback_mismatch" if saved else "write_returned_false"
        harvest(result, browser=[browser_cookie], stored=[stored], origin=origin)
    except Exception as error:
        harvest("write_verify_error", error_type=type(error).__name__)


async def _browser_state(context, origin):
    try:
        cookies = await asyncio.wait_for(context.cookies(), timeout=SAMPLE_TIMEOUT)
        return cookie_state(cookies, origin)
    except Exception as error:
        return {"sampled_at": time.time(), "status": "sample_error",
                "error_type": type(error).__name__, "cookies": []}


async def sync_before(context, cookies, origin):
    if not enabled():
        return None
    try:
        operation = _operation.get() or {}
        stored = cookie_state(cookies, origin)
        before = {"browser_context_cookie": await _browser_state(context, origin),
                  "file_cookie": stored,
                  "harvest": operation.get("harvest", "not_observed")}
        before["relations"] = _pairs(before["browser_context_cookie"], before["file_cookie"])
        important = any(pair["relation"] != "same" for pair in before["relations"])
        event("cookie_sync_before", result=before["harvest"], details=before,
              snapshot=True, important=important)
        return before
    except Exception as error:
        _warn(error)
        return None


async def sync_after(context, before, origin):
    if before is None or not enabled():
        return
    try:
        after = await _browser_state(context, origin)
        pairs = _pairs(after, before["file_cookie"])
        regression = any(pair["matches_origin"] and pair["relation"] == "browser_exp_later" and
                         any(current["domain"] == pair["domain"] and current["path"] == pair["path"] and
                             current["relation"] == "same" for current in pairs)
                         for pair in before["relations"])
        details = dict(before, browser_context_cookie_after=after,
                       relations_after=pairs, candidate_expiry_regression=regression)
        event("cookie_sync_after", result="expiry_regression_candidate" if regression else "observed",
              details=details, snapshot=True, important=regression or after["status"] != "ok")
    except Exception as error:
        _warn(error)
