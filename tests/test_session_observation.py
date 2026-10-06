import asyncio
import base64
import contextvars
import json
import sys
import threading
import time
import types
from contextlib import contextmanager

import pytest

import auto_recover
import session_guard
from runtime import mainloop
from runtime import sender
from runtime import session_obs as observation
from runtime.ctx import set_active_cid


@pytest.fixture
def observed(monkeypatch, dbdir):
    monkeypatch.setenv("DZMM_SESSION_OBS", "on")
    monkeypatch.delenv("DZMM_SESSION_OBS_TRACE", raising=False)
    monkeypatch.delenv("DZMM_SESSION_OBS_PARENT", raising=False)
    monkeypatch.setattr(observation, "DATA_DIR", dbdir)
    monkeypatch.setattr(observation, "_recent", {})
    monkeypatch.setattr(observation, "_count", 0)
    monkeypatch.setattr(observation, "_window", 0.0)
    monkeypatch.setattr(observation, "_dropped", 0)
    monkeypatch.setattr(observation, "_lock", threading.Lock())
    monkeypatch.setattr(observation, "_operation", contextvars.ContextVar("test_observation", default=None))
    monkeypatch.setattr(observation, "_warned_at", float("-inf"))
    monkeypatch.setattr(sender, "log", lambda message: None)
    monkeypatch.setattr(mainloop, "log", lambda message: None)
    return dbdir


def records(directory):
    path = directory / "session_events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def cookie(expires, tag, domain=".dzmm.ai", path="/"):
    payload = {"expires_at": expires, "access_token": "SECRET_ACCESS_" + tag,
               "refresh_token": "SECRET_REFRESH_" + tag, "user": {"id": "PRIVATE_USER"}}
    value = "base64-" + base64.b64encode(json.dumps(payload).encode()).decode()
    return {"name": observation.COOKIE_NAME, "value": value, "domain": domain,
            "path": path, "expires": expires + 100}


class CookieContext:
    def __init__(self, browser_cookie):
        self.values = [dict(browser_cookie)]
        self.added = []
        self.reads = 0
        self.failure = None
        self.delay = 0

    async def cookies(self):
        self.reads += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return [dict(value) for value in self.values]

    async def add_cookies(self, cookies):
        self.added.append([dict(value) for value in cookies])
        self.values = [dict(value) for value in cookies]


def wire_sender(monkeypatch, browser_cookie, file_cookie, *, throttled=False, locked=False, saved=True):
    instance = sender.Sender.__new__(sender.Sender)
    instance.ctx = CookieContext(browser_cookie)
    instance._last_harvest = time.time() if throttled else 0
    instance._last_token_value = ""
    instance._last_reload = 0
    instance._token_dirty = False
    store = {"cookie": dict(file_cookie), "writes": 0}
    monkeypatch.setattr(sender.token_store, "get_cookie", lambda name: dict(store["cookie"]))
    monkeypatch.setattr(sender.token_store, "get_cookies_for_playwright", lambda: [dict(store["cookie"])])

    def update(name, value):
        store["writes"] += 1
        if saved:
            store["cookie"]["value"] = value
        return saved

    @contextmanager
    def guard(reason, timeout):
        yield not locked

    monkeypatch.setattr(sender.token_store, "update_cookie", update)
    monkeypatch.setattr(session_guard, "guard", guard)
    return instance, store


@pytest.mark.parametrize("reason", ["throttled", "lock_busy", "write_returned_false"])
def test_regression_is_observed_without_changing_existing_sync(monkeypatch, observed, reason):
    now = time.time()
    browser_cookie = cookie(now + 3600, "browser")
    file_cookie = cookie(now + 1800, "file")
    instance, store = wire_sender(monkeypatch, browser_cookie, file_cookie,
        throttled=reason == "throttled", locked=reason == "lock_busy", saved=reason != "write_returned_false")
    assert asyncio.run(instance._refresh_token()) is True
    assert instance.ctx.added == [[file_cookie]]
    assert instance._token_dirty is True
    assert instance._last_token_value == file_cookie["value"]
    snapshot = json.loads((observed / "page_token_state.json").read_text())
    assert snapshot["details"]["candidate_expiry_regression"] is True
    assert snapshot["details"]["harvest"] == reason
    assert snapshot["details"]["relations"][0]["relation"] == "browser_exp_later"
    assert store["writes"] == (1 if reason == "write_returned_false" else 0)
    journal = (observed / "session_events.jsonl").read_text()
    for private in (browser_cookie["value"], file_cookie["value"], "SECRET_ACCESS", "SECRET_REFRESH", "PRIVATE_USER"):
        assert private not in journal


def test_successful_harvest_has_verified_readback(monkeypatch, observed):
    now = time.time()
    browser_cookie = cookie(now + 3600, "browser")
    instance, store = wire_sender(monkeypatch, browser_cookie, cookie(now + 1800, "file"))
    assert asyncio.run(instance._refresh_token()) is True
    assert store["writes"] == 1
    assert instance.ctx.values[0]["value"] == browser_cookie["value"]
    snapshot = json.loads((observed / "page_token_state.json").read_text())
    assert snapshot["details"]["harvest"] == "write_verified"
    assert snapshot["details"]["candidate_expiry_regression"] is False


def test_write_readback_mismatch_does_not_change_harvest_return(monkeypatch, observed):
    now = time.time()
    instance, store = wire_sender(monkeypatch, cookie(now + 3600, "browser"), cookie(now + 1800, "file"))
    monkeypatch.setattr(sender.token_store, "update_cookie", lambda name, value: True)
    assert asyncio.run(instance._harvest_token()) is True
    assert records(observed)[-1]["result"] == "write_readback_mismatch"
    assert store["writes"] == 0


def test_default_off_adds_no_browser_reads_or_files(monkeypatch, observed):
    monkeypatch.delenv("DZMM_SESSION_OBS")
    now = time.time()
    instance, store = wire_sender(monkeypatch, cookie(now + 3600, "browser"),
                                  cookie(now + 1800, "file"), throttled=True)
    assert asyncio.run(instance._refresh_token()) is True
    assert instance.ctx.reads == 0
    assert store["writes"] == 0
    assert records(observed) == []
    assert observation.recovery_env("trace") is None


def test_missing_context_preserves_dry_behavior(observed):
    instance = sender.Sender.__new__(sender.Sender)
    instance.ctx = None
    assert asyncio.run(instance._harvest_token()) is False
    assert asyncio.run(instance._refresh_token()) is True
    assert {record["result"] for record in records(observed)} == {"no_context"}


@pytest.mark.parametrize("failure", ["journal", "snapshot"])
def test_diagnostic_storage_failure_never_breaks_sync(monkeypatch, observed, failure, capsys):
    now = time.time()
    instance, store = wire_sender(monkeypatch, cookie(now + 3600, "browser"),
                                  cookie(now + 1800, "file"), throttled=True)
    if failure == "journal":
        def broken_write(*arguments):
            raise OSError("SECRET_REFRESH_SHOULD_NOT_BE_LOGGED")
        monkeypatch.setattr(observation, "_write", broken_write)
    else:
        original_replace = observation.os.replace

        def broken_replace(source, destination):
            if destination.name == "page_token_state.json":
                raise OSError("SECRET_REFRESH_SHOULD_NOT_BE_LOGGED")
            return original_replace(source, destination)
        monkeypatch.setattr(observation.os, "replace", broken_replace)
    assert asyncio.run(instance._refresh_token()) is True
    assert len(instance.ctx.added) == 1
    assert store["writes"] == 0
    output = capsys.readouterr()
    assert "OSError" in output.err
    assert "SECRET_REFRESH" not in output.err
    assert list(observed.glob(".page_token_state-*.tmp")) == []


@pytest.mark.parametrize("timeout", [False, True])
def test_browser_observation_failure_is_bounded_and_does_not_change_send(monkeypatch, observed, timeout):
    now = time.time()
    instance, _ = wire_sender(monkeypatch, cookie(now + 3600, "browser"),
                              cookie(now + 1800, "file"), throttled=True)
    monkeypatch.setattr(observation, "SAMPLE_TIMEOUT", 0.01)
    instance.ctx.delay = 1 if timeout else 0
    instance.ctx.failure = None if timeout else RuntimeError("SECRET_TOKEN_ERROR")
    started = time.monotonic()
    assert asyncio.run(instance._refresh_token()) is True
    assert time.monotonic() - started < 1
    snapshot = json.loads((observed / "page_token_state.json").read_text())
    assert snapshot["details"]["browser_context_cookie_after"]["status"] == "sample_error"
    assert "SECRET_TOKEN_ERROR" not in (observed / "session_events.jsonl").read_text()


@pytest.mark.parametrize("payload", ["broken", "base64-W10=", "base64-e30="])
def test_invalid_cookie_is_unorderable_and_never_recorded_raw(payload):
    browser = cookie(time.time() + 3600, "browser")
    stored = dict(browser, value=payload)
    browser_state = observation.cookie_state([browser], sender.ORIGIN)
    file_state = observation.cookie_state([stored], sender.ORIGIN)
    assert file_state["cookies"][0]["parse"] == "invalid"
    assert observation._pairs(browser_state, file_state)[0]["relation"] == "unorderable"
    assert payload not in json.dumps(file_state)


def test_equal_expiry_different_credentials_and_cookie_expiry_are_distinct():
    expires = time.time() + 3600
    browser = observation.cookie_state([cookie(expires, "browser")], sender.ORIGIN)
    stored = observation.cookie_state([cookie(expires, "file")], sender.ORIGIN)
    assert observation._pairs(browser, stored)[0]["relation"] == "different_equal_exp"
    assert browser["cookies"][0]["cookie_expires"] != browser["cookies"][0]["token_expires_at"]


def test_invalid_cookie_expiry_metadata_cannot_leak_secret_values():
    entry = dict(cookie(time.time() + 3600, "browser"), expires="SECRET_COOKIE_VALUE")
    summary = observation.cookie_state([entry], sender.ORIGIN)
    assert summary["cookies"][0]["cookie_expires"] is None
    assert "SECRET_COOKIE_VALUE" not in json.dumps(summary)


def test_newer_file_still_updates_browser_without_regression(monkeypatch, observed):
    now = time.time()
    file_cookie = cookie(now + 3600, "file")
    instance, _ = wire_sender(monkeypatch, cookie(now + 1800, "browser"), file_cookie)
    assert asyncio.run(instance._refresh_token()) is True
    assert instance.ctx.values == [file_cookie]
    snapshot = json.loads((observed / "page_token_state.json").read_text())
    assert snapshot["details"]["candidate_expiry_regression"] is False
    assert snapshot["details"]["relations"][0]["relation"] == "file_exp_later"


def test_foreign_origin_never_counts_as_regression(monkeypatch, observed):
    now = time.time()
    instance, _ = wire_sender(monkeypatch, cookie(now + 3600, "browser", domain="foreign.example"),
                              cookie(now + 1800, "file", domain="foreign.example"), throttled=True)
    assert asyncio.run(instance._refresh_token()) is True
    snapshot = json.loads((observed / "page_token_state.json").read_text())
    assert snapshot["details"]["candidate_expiry_regression"] is False
    assert snapshot["details"]["browser_context_cookie"]["origin"] == sender.ORIGIN


def test_multidomain_and_duplicate_cookies_are_not_conflated():
    now = time.time()
    primary = cookie(now + 3600, "browser")
    foreign = cookie(now + 7200, "foreign", domain="other.example")
    browser = observation.cookie_state([foreign, primary], sender.ORIGIN)
    stored = observation.cookie_state([cookie(now + 1800, "file")], sender.ORIGIN)
    assert observation._pairs(browser, stored)[0]["relation"] == "browser_exp_later"
    assert browser["cookies"][0]["matches_origin"] is False
    duplicated = observation.cookie_state([primary, primary], sender.ORIGIN)
    assert observation._pairs(duplicated, stored)[0]["relation"] == "missing_or_ambiguous"
    scoped = observation.cookie_state([dict(primary, path="/other")], sender.ORIGIN)
    assert observation._pairs(scoped, stored)[0]["relation"] == "missing_or_ambiguous"


def test_concurrent_groups_keep_separate_sync_traces(monkeypatch, observed):
    now = time.time()
    first, _ = wire_sender(monkeypatch, cookie(now + 3600, "browser"),
                           cookie(now + 1800, "file"), throttled=True, locked=True)
    second = sender.Sender.__new__(sender.Sender)
    second.ctx = first.ctx
    second._last_harvest = 0
    second._last_token_value = ""
    second._last_reload = 0
    second._token_dirty = False

    async def worker(instance, cid):
        set_active_cid(cid)
        return await instance._refresh_token()

    async def run_both():
        return await asyncio.gather(worker(first, "first_group"), worker(second, "second_group"))

    assert asyncio.run(run_both()) == [True, True]
    before = [record for record in records(observed) if record["event"] == "cookie_sync_before"]
    assert len({record["trace"] for record in before}) == 2
    assert len({record["task_group_fingerprint"] for record in before}) == 2
    assert {record["details"]["harvest"] for record in before} == {"throttled", "lock_busy"}
    assert observation._operation.get() is None


def test_journal_rotation_and_event_rate_are_bounded(monkeypatch, observed):
    monkeypatch.setattr(observation, "MAX_JOURNAL_BYTES", 1024)
    for index in range(20):
        observation.event("test_event", trace=str(index))
    assert (observed / "session_events.jsonl").stat().st_size <= 1024
    assert (observed / "session_events.jsonl.1").stat().st_size <= 1024
    assert len(list(observed.glob("session_events.jsonl*"))) == 2
    monkeypatch.setattr(observation, "MAX_EVENTS_PER_MINUTE", observation._count)
    previous = (observed / "session_events.jsonl").read_bytes()
    observation.event("test_event")
    assert (observed / "session_events.jsonl").read_bytes() == previous
    assert observation._dropped == 1


def test_normal_events_are_sampled_and_errors_remain_visible(observed):
    for index in range(5):
        observation.event("normal", important=False)
    observation.event("failure", result="write_returned_false")
    entries = records(observed)
    assert [entry["event"] for entry in entries] == ["normal", "failure"]
    assert entries[-1]["dropped_since_last_write"] == 4


def test_spawn_and_debounce_keep_decisions_and_link_child(monkeypatch, observed):
    calls = []
    monkeypatch.setattr(mainloop, "_auto_recover_last_spawn", 0.0)
    monkeypatch.setattr(mainloop, "_auto_recover_trace", "")
    monkeypatch.setattr(mainloop.subprocess, "Popen", lambda *arguments, **options:
                        calls.append(options) or types.SimpleNamespace(pid=1234))
    fake_bot = types.SimpleNamespace(HERMES_PY="python", ROOT=".")
    assert mainloop._spawn_auto_recover(fake_bot, "PRIVATE_REASON", trace="first", cid="private-group", source="read") is True
    assert mainloop._spawn_auto_recover(fake_bot, "PRIVATE_REASON", trace="second", source="read") is False
    assert len(calls) == 1
    assert calls[0]["env"]["DZMM_SESSION_OBS_TRACE"] == "first"
    entries = records(observed)
    assert entries[0]["child_pid"] == 1234
    assert entries[1]["parent_trace"] == "first"
    assert "PRIVATE_REASON" not in (observed / "session_events.jsonl").read_text()
    assert "private-group" not in (observed / "session_events.jsonl").read_text()


def test_spawn_failure_keeps_retry_window_and_hides_exception_body(monkeypatch, observed):
    monkeypatch.setattr(mainloop, "_auto_recover_last_spawn", 0.0)

    def failed_spawn(*arguments, **options):
        raise OSError("SECRET_ENVIRONMENT")

    monkeypatch.setattr(mainloop.subprocess, "Popen", failed_spawn)
    fake_bot = types.SimpleNamespace(HERMES_PY="python", ROOT=".")
    assert mainloop._spawn_auto_recover(fake_bot, "reason") is False
    assert mainloop._auto_recover_last_spawn == 0.0
    assert records(observed)[-1]["event"] == "recovery_spawn_failed"
    assert "SECRET_ENVIRONMENT" not in (observed / "session_events.jsonl").read_text()


@pytest.mark.parametrize("mode", ["already_logged_in", "headless", "manual_captcha", "failed", "verification_failed"])
def test_recovery_outcomes_are_visible_without_changing_actions(monkeypatch, observed, mode):
    state = {"logged_in": mode in ("already_logged_in", "verification_failed")}
    actions = []

    def write(cookies):
        state["logged_in"] = True
        actions.append("write_credentials")
        return True

    headless = types.SimpleNamespace(
        check_logged_in=lambda: state["logged_in"],
        headless_login=lambda email, password: [{"value": "SECRET_LOGIN_COOKIE"}] if mode == "headless" else [],
        write_to_json=write,
        login_manual_captcha=lambda email, password, wait_minutes: [{"value": "SECRET_MANUAL_COOKIE"}] if mode == "manual_captcha" else [])
    relogin = types.SimpleNamespace(load_config=lambda: ("PRIVATE_EMAIL", "SECRET_PASSWORD"),
                                   login_flow=lambda email, password: False)
    monkeypatch.setitem(sys.modules, "headless_relogin", headless)
    monkeypatch.setitem(sys.modules, "auto_relogin", relogin)
    monkeypatch.setenv("DZMM_SESSION_OBS_TRACE", "parent-trace")
    monkeypatch.setenv("DZMM_SESSION_OBS_PARENT", "parent-instance")
    monkeypatch.setattr(auto_recover, "notify", lambda message: actions.append("notify"))
    monkeypatch.setattr(auto_recover, "restart_bot", lambda: actions.append("restart"))
    monkeypatch.setattr(auto_recover, "verify", lambda: actions.append("verify") or mode != "verification_failed")
    monkeypatch.setattr(auto_recover.time, "sleep", lambda seconds: None)
    assert auto_recover._recover_locked() == (1 if mode in ("failed", "verification_failed") else 0)
    entries = records(observed)
    assert all(entry["trace"] == "parent-trace" for entry in entries)
    assert entries[-1]["result"] == ("login_failed" if mode == "failed" else
        "verification_failed" if mode == "verification_failed" else "verified")
    if mode == "failed":
        assert "restart" not in actions
        assert "verify" not in actions
    else:
        assert actions.index("restart") < actions.index("verify")
        decision = next(entry for entry in entries if entry["event"] == "recovery_restart_decision")
        assert decision["login_mode"] == ("already_logged_in" if mode == "verification_failed" else mode)
    data = (observed / "session_events.jsonl").read_text()
    for secret in ("SECRET_PASSWORD", "PRIVATE_EMAIL", "SECRET_LOGIN_COOKIE", "SECRET_MANUAL_COOKIE"):
        assert secret not in data


def test_recovery_lock_skip_does_not_start_login(monkeypatch, observed):
    @contextmanager
    def busy_guard(reason, timeout):
        yield False

    monkeypatch.setattr(session_guard, "guard", busy_guard)
    monkeypatch.setattr(auto_recover, "_recover_locked", lambda: pytest.fail("must not log in"))
    assert auto_recover.main() == 1
    assert records(observed)[-1]["result"] == "lock_busy"


def test_recovery_exception_is_logged_by_type_and_still_propagates(monkeypatch, observed):
    @contextmanager
    def free_guard(reason, timeout):
        yield True

    def broken_recovery():
        raise RuntimeError("SECRET_PASSWORD")

    monkeypatch.setattr(session_guard, "guard", free_guard)
    monkeypatch.setattr(auto_recover, "_recover_locked", broken_recovery)
    with pytest.raises(RuntimeError):
        auto_recover.main()
    assert records(observed)[-1]["error_type"] == "RuntimeError"
    assert "SECRET_PASSWORD" not in (observed / "session_events.jsonl").read_text()
