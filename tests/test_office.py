"""茶水间规则、并发、拒绝取消、引用单条发送与员工接入合同。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from runtime import employee_api, features, office, office_scene
from runtime.office_store import Store, TEA_CID, RuleError, local_day
from runtime.outbox import OutboxSender, OutboxStore
from runtime.routing import RoutingSender
from runtime import sender as account
from runtime.reply_delivery import ReplyNotSent
_saved_path = sys.path[:]
try:
    from sio_subscribe import SioSubscriber
finally:
    # 订阅器为导入第三方 socketio 临时移除项目路径；不要污染后续测试收集。
    sys.path[:] = _saved_path

REF = {"id": "cmd1", "sentBy": "a", "content": {"type": "text", "text": "/潜规则 b"}}
TEXT = "办公室里灯光温柔，两个人放下文件聊起今天的趣事。" * 20


def profile(uid, level=1, dept="d", gender="unknown"):
    return {"platform_id": uid, "display_name": uid, "department_id": dept,
            "department_name": "同一个部门", "rank_name": "职级", "rank_level": level, "gender": gender}


@pytest.fixture
def store(dbdir):
    return Store(dbdir / "office.db")


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    saved = features.overrides()
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    features.set_overrides({TEA_CID + "|office": True})
    monkeypatch.setattr(office, "log", lambda *a: None)
    monkeypatch.setattr(office, "TASKS", set())
    yield
    features.set_overrides(saved)


def activate(store, now=1000):
    store.preference(TEA_CID, "b", True, now)
    sid, op = store.start(TEA_CID, "a", "b", REF, [], now)
    assert store.ready(sid, op, TEXT, now)
    assert store.dispatch(sid, op, now)
    store.delivered(sid, op, "platform1", now)
    return sid, op


def test_default_refuse_and_restarts_keep_switch(store):
    assert not store.status(TEA_CID, "b", 1000)["allowed"]
    with pytest.raises(RuleError, match="还没开启"):
        store.start(TEA_CID, "a", "b", REF, [], 1000)
    store.preference(TEA_CID, "b", True)
    assert Store(store.path).status(TEA_CID, "b")["allowed"]


def test_command_idempotency_and_batch_concurrency(store):
    with ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(lambda _: store.claim_command(TEA_CID, "same"), range(8)))
    assert sum(claims) == 1
    assert not store.claim_command(TEA_CID, "")


def test_concurrent_starts_only_one_participant_and_quota(store):
    store.preference(TEA_CID, "b", True)
    def start(number):
        try:
            return store.start(TEA_CID, "actor" + str(number), "b", REF, [], 1000)
        except RuleError:
            return None
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(start, range(4)))
    assert sum(row is not None for row in rows) == 1


def test_daily_once_cannot_change_target_or_restart(store):
    sid, _ = activate(store)
    assert store.end(TEA_CID, "b")
    assert not store.current(TEA_CID, "a")
    store.preference(TEA_CID, "c", True)
    with pytest.raises(RuleError, match="今天的机会"):
        Store(store.path).start(TEA_CID, "a", "c", REF, [], 1001)
    assert store.status(TEA_CID, "a", 1001)["remaining"] == 0
    assert store.start(TEA_CID, "a", "c", REF, [], 1000 + 86400)


def test_midnight_beijing_and_reserved_day():
    assert local_day(1792511999) != local_day(1792512000)  # 2026-10-20 16:00 UTC


@pytest.mark.parametrize("stage,remaining", [("generating", 1), ("ready", 1), ("sending", 0), ("active", 0)])
def test_refuse_releases_only_definitely_unsent_first_segment(store, stage, remaining):
    store.preference(TEA_CID, "b", True)
    sid, op = store.start(TEA_CID, "a", "b", REF, [], 1000)
    if stage != "generating":
        store.ready(sid, op, TEXT, 1000)
    if stage in ("sending", "active"):
        store.dispatch(sid, op, 1000)
    if stage == "active":
        store.delivered(sid, op, "mid", 1000)
    store.preference(TEA_CID, "b", False, 1001)
    assert store.status(TEA_CID, "a", 1001)["remaining"] == remaining
    assert not store.current(TEA_CID, "a")
    assert not store.ready(sid, op, TEXT, 1001)
    store.delivered(sid, op, "late", 1002)
    assert store.get(sid)["status"] == "refused"


def test_idle_deadline_and_continue_refresh_only_after_success(store):
    sid, _ = activate(store)
    assert store.sweep(TEA_CID, 1299) == []
    sid2, op = store.begin_continue(TEA_CID, "b", {**REF, "id": "continue"}, [], 1299)
    assert sid2 == sid and store.sweep(TEA_CID, 1301) == []
    store.ready(sid, op, TEXT, 1302)
    assert store.dispatch(sid, op, 1302)
    store.delivered(sid, op, "p2", 1303)
    assert store.get(sid)["deadline"] == 1603
    assert store.get(sid)["continues"] == 1
    notes = Store(store.path).sweep(TEA_CID, 1603)
    assert len(notes) == 1 and json.loads(notes[0]["reference"])["id"] == "continue"
    assert notes[0]["text"] == "已连续 5 分钟无互动，本场结束。"
    assert store.sweep(TEA_CID, 1700) == []
    assert store.status(TEA_CID, "a", 1700)["remaining"] == 0


def test_failed_continue_restores_old_deadline_and_stale_operation_is_rejected(store):
    sid, old = activate(store)
    _, op = store.begin_continue(TEA_CID, "a", REF, [], 1100)
    assert not store.ready(sid, old, TEXT, 1101)
    assert store.fail_generation(sid, op)
    assert store.get(sid)["deadline"] == 1300
    assert len(store.sweep(TEA_CID, 1300)) == 1


def test_stale_profile_query_cannot_continue_or_end_a_new_session(store):
    old_sid, _ = activate(store)
    store.end(TEA_CID, "b")
    new_sid, op = store.start(TEA_CID, "c", "b", REF, [], 1100)
    store.ready(new_sid, op, TEXT, 1100)
    store.dispatch(new_sid, op, 1100)
    store.delivered(new_sid, op, "new-mid", 1100)
    with pytest.raises(RuleError):
        store.begin_continue(TEA_CID, "b", REF, [], 1101, expected_sid=old_sid)
    assert not store.end(TEA_CID, "b", "ineligible", expected_sid=old_sid)
    assert store.current(TEA_CID, "b")["id"] == new_sid


def test_ambiguous_delivery_never_refunds_or_revives(store):
    store.preference(TEA_CID, "b", True)
    sid, op = store.start(TEA_CID, "a", "b", REF, [], 1000)
    store.ready(sid, op, TEXT, 1000)
    store.dispatch(sid, op, 1000)
    store.ambiguous(sid, op)
    assert not store.dispatch(sid, op, 1001)
    store.sweep(TEA_CID, 1300)
    assert store.status(TEA_CID, "a", 1301)["remaining"] == 0


def test_generation_crash_restores_quota_but_other_sessions_survive(store):
    store.preference(TEA_CID, "b", True)
    sid, op = store.start(TEA_CID, "a", "b", REF, [], 1000)
    store.preference(TEA_CID, "d", True)
    other, _ = store.start(TEA_CID, "c", "d", REF, [], 1100)
    store.sweep(TEA_CID, 1120)
    assert store.get(sid)["status"] == "expired"
    assert store.get(other)["status"] == "generating"
    assert store.status(TEA_CID, "a", 1120)["remaining"] == 1


@pytest.mark.parametrize("a,b,message", [(2, 3, office.RANK_DENIED), (2, 2, office.EQUAL_DENIED)])
def test_ranks_and_department_priority(a, b, message):
    p1, p2 = profile("a", a), profile("b", b)
    with pytest.raises(RuleError, match=message):
        office.eligibility(p1, p2)
    p2["department_id"] = "other"
    with pytest.raises(RuleError, match=office.DEPARTMENT_DENIED):
        office.eligibility(p1, p2)


@pytest.mark.parametrize("gender,expected", [("male", "男"), ("female", "女"), ("unknown", "未知"), (None, "未知")])
def test_employee_profile_gender(gender, expected):
    assert employee_api.normalize_profile(profile("a", gender=gender), "a")["gender"] == expected


@pytest.mark.parametrize("changes", [{"rank_level": True}, {"rank_level": None}, {"department_id": ""},
                                     {"platform_id": "other"}, {"employment_status": "inactive"}])
def test_bad_employee_data_fails_closed(changes):
    with pytest.raises(employee_api.EmployeeUnavailable):
        employee_api.normalize_profile({**profile("a"), **changes}, "a")


def test_missing_key_or_profile_path_never_requests_network(monkeypatch):
    monkeypatch.setattr(employee_api.requests, "request", lambda *a, **kw: pytest.fail("unexpected network"))
    monkeypatch.delenv("DZMM_INTEGRATION_API_KEY", raising=False)
    monkeypatch.delenv("DZMM_EMPLOYEE_PROFILE_PATH", raising=False)
    client = employee_api.Client()
    with pytest.raises(employee_api.EmployeeUnavailable):
        client.match("x")
    with pytest.raises(employee_api.EmployeeUnavailable):
        client.profile("a")


def test_employee_requests_verify_tls_no_redirect_and_no_error_leak(monkeypatch):
    monkeypatch.setenv("DZMM_INTEGRATION_API_KEY", "test-only-secret")
    monkeypatch.setenv("DZMM_INTEGRATION_BASE_URL", "https://example.invalid/api/integration")
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_PATH", "/users/{platform_id}/profile")
    captured = []
    def request(method, url, **kwargs):
        captured.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: profile("a"))
    monkeypatch.setattr(employee_api.requests, "request", request)
    assert employee_api.Client().profile("a")["name"] == "a"
    method, url, kwargs = captured[0]
    assert method == "GET" and url.endswith("/users/a/profile")
    assert kwargs.get("verify", True) is True and kwargs["allow_redirects"] is False
    assert "test-only-secret" not in url
    monkeypatch.setattr(employee_api.requests, "request", lambda *a, **kw: SimpleNamespace(status_code=401))
    with pytest.raises(employee_api.EmployeeUnavailable) as error:
        employee_api.Client().profile("a")
    assert "test-only-secret" not in str(error.value)


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send_reply_with_id(self, text, cid, reference, *, guard=None):
        if guard and not guard():
            return ""
        self.sent.append((text, cid, reference))
        return "reply-" + str(len(self.sent))


class FakeClient:
    def match(self, target):
        return target.lstrip("@")

    def profile(self, uid):
        return employee_api.normalize_profile(profile(uid, 3 if uid == "a" else 1), uid)


@pytest.fixture
def service(monkeypatch, store):
    async def members(self, cid):
        return {uid: {"protected": False, "name": uid} for uid in ("a", "b", "c", "d")}
    monkeypatch.setattr(office.Platform, "members", members)
    monkeypatch.setattr(office, "generate", lambda *a: TEXT)
    store.preference(TEA_CID, "b", True)
    return FakeSender(), store, FakeClient()


async def issue(service, text, mid="m1", uid="a"):
    sender, store, client = service
    msg = {"message_id": mid, "sent_by": uid, "content": {"type": "text", "text": text}}
    await office.handle(sender, TEA_CID, uid, text, msg, store=store, client=client)


async def drain():
    tasks = list(office.TASKS)
    if tasks:
        await asyncio.gather(*tasks)


def test_service_start_continue_end_quote_each_command(service):
    sender, store, _ = service
    async def run():
        await issue(service, "/潜规则 @b 开完会后")
        await drain()
        await issue(service, "/潜规则继续 第二天", "m2", "b")
        await drain()
        await issue(service, "/潜规则结束", "m3", "b")
        await issue(service, "/潜规则 @b", "m1")  # 重放原指令
    asyncio.run(run())
    assert [s[2]["id"] for s in sender.sent] == ["m1", "m2", "m3"]
    assert sender.sent[0][0] == "🤖 " + TEXT and len(sender.sent) == 3
    assert not store.current(TEA_CID, "a")


def test_refusal_during_generation_suppresses_stale_output(service, monkeypatch):
    sender, store, _ = service
    started, release = threading.Event(), threading.Event()
    def generate(*args):
        started.set()
        assert release.wait(3)
        return TEXT
    monkeypatch.setattr(office, "generate", generate)
    async def run():
        await issue(service, "/潜规则 b")
        assert await asyncio.to_thread(started.wait, 3)
        await issue(service, "/拒绝潜规则", "refuse", "b")
        release.set()
        await drain()
    asyncio.run(run())
    assert len(sender.sent) == 1 and sender.sent[0][2]["id"] == "refuse"
    assert store.status(TEA_CID, "a")["remaining"] == 1


def test_generation_failure_releases_first_quota(service, monkeypatch):
    sender, store, _ = service
    monkeypatch.setattr(office, "generate", lambda *args: (_ for _ in ()).throw(ValueError("not valid")))
    async def run():
        await issue(service, "/潜规则 b")
        await drain()
    asyncio.run(run())
    assert store.status(TEA_CID, "a")["remaining"] == 1
    assert sender.sent[0][2]["id"] == "m1"


def test_foreign_continue_rejected_without_query_or_quota(service):
    sender, store, _ = service
    activate(store)
    async def run():
        await issue(service, "/潜规则继续", uid="c")
        await drain()
    asyncio.run(run())
    assert "没有可以继续" in sender.sent[0][0]
    assert store.current(TEA_CID, "a")["continues"] == 0


def test_generator_payload_preserves_roles_genders_and_retry(monkeypatch):
    calls = []
    profiles = [employee_api.normalize_profile(profile("a", 3, gender="female"), "a"),
                employee_api.normalize_profile(profile("b", gender="male"), "b")]
    def raw(prompt, content, **kwargs):
        calls.append((prompt, json.loads(content), kwargs))
        return ("太短。", "stop") if len(calls) == 1 else (TEXT, "stop")
    monkeypatch.setattr(office_scene.brains, "_call_llm_raw", raw)
    assert office_scene.generate(profiles, ["上一段。"], "下一段") == TEXT
    assert len(calls) == 2
    assert [x["gender"] for x in calls[0][1]["participants"]] == ["女", "男"]
    assert "length_correction" in calls[1][1]
    assert "未知则使用姓名" in calls[0][0]


def test_quoted_delivery_is_one_socket_emit_and_outbox_is_not_replayed(dbdir, monkeypatch):
    calls = []
    sub = SioSubscriber.__new__(SioSubscriber)
    async def ack(event, payload, **kwargs):
        calls.append((event, payload))
        return {"success": True}
    sub._sio = SimpleNamespace(call=ack)
    sub._connected = asyncio.Event()
    sub._connected.set()
    sub._action_lock = asyncio.Lock()
    monkeypatch.setattr(account, "get_messages", lambda *args: [calls[-1][1]["message"]])
    monkeypatch.setattr(account, "_LIVE_SIO", sub)
    raw_sender = account.Sender.__new__(account.Sender)
    raw_sender._send_locks = {}
    route = RoutingSender(raw_sender, {TEA_CID: object()})  # 即使有官方路由，也必须走账号引用
    db = OutboxStore(dbdir / "outbox.db")
    try:
        sender = OutboxSender(route, db)
        mid = asyncio.run(sender.send_reply_with_id(TEXT, TEA_CID, REF))
        assert mid and len(calls) == 1
        message = calls[0][1]["message"]
        assert message["content"]["text"] == TEXT
        assert message["content"]["reference"] == REF
        records = db.by_status("sent")
        assert len(records) == 1 and records[0]["content_type"] == "office_reply"
        assert json.loads(records[0]["text"])["reference"]["id"] == "cmd1"
        assert records[0]["platform_sent_id"] == mid
    finally:
        db.close()


def test_guard_after_queue_cancels_without_send(dbdir, monkeypatch):
    async def no_send(*args):
        pytest.fail("must not emit cancelled response")
    sub = SioSubscriber.__new__(SioSubscriber)
    sub._sio = SimpleNamespace(call=no_send)
    sub._connected = asyncio.Event()
    sub._connected.set()
    sub._action_lock = asyncio.Lock()
    monkeypatch.setattr(account, "_LIVE_SIO", sub)
    raw = account.Sender.__new__(account.Sender)
    raw._send_locks = {}
    db = OutboxStore(dbdir / "o.db")
    try:
        sender = OutboxSender(RoutingSender(raw), db)
        assert asyncio.run(sender.send_reply_with_id(TEXT, TEA_CID, REF, guard=lambda: False)) == ""
        assert asyncio.run(sender.retry_once()) == 0
    finally:
        db.close()


def test_explicit_not_sent_retries_same_output_and_reference(service, monkeypatch):
    sender, store, _ = service
    calls = []
    generate_calls = []
    monkeypatch.setattr(office, "generate", lambda *args: generate_calls.append(args) or TEXT)
    async def send(text, cid, reference, *, guard=None):
        if guard and not guard():
            return ""
        calls.append((text, reference["id"]))
        if len(calls) < 2:
            raise ReplyNotSent("平台明确拒绝")
        return "confirmed"
    sender.send_reply_with_id = send
    async def run():
        await issue(service, "/潜规则 b")
        await drain()
    asyncio.run(run())
    assert len(generate_calls) == 1 and len(calls) == 2 and calls[0] == calls[1]
    assert store.status(TEA_CID, "a")["remaining"] == 0


def test_uncertain_send_is_not_retried_or_refunded(service):
    sender, store, _ = service
    calls = []
    async def send(text, cid, reference, *, guard=None):
        if guard:
            assert guard()
        calls.append(text)
        raise TimeoutError("test only")
    sender.send_reply_with_id = send
    async def run():
        await issue(service, "/潜规则 b")
        await drain()
    asyncio.run(run())
    assert calls == ["🤖 " + TEXT]
    assert store.current(TEA_CID, "a")["status"] == "ambiguous"
    assert store.status(TEA_CID, "a")["remaining"] == 0


def test_native_quote_must_survive_readback(dbdir, monkeypatch):
    sub = SimpleNamespace()
    async def send(*args, **kwargs):
        return "known-mid"
    sub.send_reply_confirmed = send
    monkeypatch.setattr(account, "_LIVE_SIO", sub)
    monkeypatch.setattr(account, "get_messages", lambda *args: [{"message_id": "known-mid",
        "sent_by": account.NUO_UID, "content": {"type": "text", "text": TEXT}}])
    raw = account.Sender.__new__(account.Sender)
    raw._send_locks = {}
    with pytest.raises(RuntimeError, match="不自动重发"):
        asyncio.run(raw.send_reply_with_id(TEXT, TEA_CID, REF))


def test_native_guard_runs_after_waiting_for_action_lock(monkeypatch):
    async def run():
        sub = SioSubscriber.__new__(SioSubscriber)
        async def no_send(*args, **kwargs):
            pytest.fail("cancelled while waiting for platform lock")
        sub._sio = SimpleNamespace(call=no_send)
        sub._connected = asyncio.Event()
        sub._connected.set()
        sub._action_lock = asyncio.Lock()
        await sub._action_lock.acquire()
        allowed = [True]
        task = asyncio.create_task(sub.send_reply_confirmed(TEA_CID, "a", TEXT, REF, guard=lambda: allowed[0]))
        await asyncio.sleep(0)
        allowed[0] = False
        sub._action_lock.release()
        assert await task == ""
    asyncio.run(run())
