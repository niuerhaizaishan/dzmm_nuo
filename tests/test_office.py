"""茶水间规则、并发、拒绝取消、引用单条发送与员工接入合同。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from runtime import employee_api, features, office, office_scene, office_wallet
from runtime.office_store import Store, TEA_CID, RuleError, local_day
from runtime.outbox import OutboxSender, OutboxStore
from runtime.routing import RoutingSender
from runtime import sender as account
from runtime.reply_delivery import ReplyContentRejected, ReplyNotSent
_saved_path = sys.path[:]
try:
    from sio_subscribe import SioSubscriber
finally:
    # 订阅器为导入第三方 socketio 临时移除项目路径；不要污染后续测试收集。
    sys.path[:] = _saved_path

REF = {"id": "cmd1", "sentBy": "a", "content": {"type": "text", "text": "#潜规则 b"}}
TEXT = "办公室里灯光温柔，两个人放下文件聊起今天的趣事。" * 20
FAIL_TEXT = "她伸手过去，被对方侧身让开，只抓到一把空气。" * 5


def profile(uid, level=1, dept="d", gender="unknown"):
    return {"platform_id": uid, "display_name": uid, "department_id": dept,
            "department_name": "同一个部门", "rank_name": "职级", "rank_level": level, "gender": gender}


@pytest.mark.parametrize("prefix", ["#", "＃"])
@pytest.mark.parametrize("action", ["允许潜规则", "拒绝潜规则", "潜规则状态", "潜规则继续", "潜规则结束", "潜规则"])
def test_hash_command_prefixes_preserve_payload(prefix, action):
    assert office.command("  " + prefix + action + " @成员 ＃全角方向  ") == (action, "@成员 ＃全角方向")


@pytest.mark.parametrize("prefix", ["#", "＃"])
@pytest.mark.parametrize("direction", ["", " 第二天 ＃保留全角方向"])
def test_short_continue_alias_normalizes_to_office_action(prefix, direction):
    assert office.command("  " + prefix + "继续" + direction) == ("潜规则继续", direction.strip())


@pytest.mark.parametrize("prefix", ["#", "＃"])
@pytest.mark.parametrize("action", ["结束", "潜规则结束"])
def test_end_aliases_normalize_to_same_action(prefix, action):
    assert office.command(prefix + action) == ("潜规则结束", "")


@pytest.mark.parametrize("text", ["/潜规则 b", "／允许潜规则", "#潜规则外传", "#其他游戏", "潜规则 b",
                                 "/继续", "／继续", "#继续工作", "继续"])
def test_office_only_accepts_exact_hash_commands(text):
    assert office.command(text) is None


@pytest.fixture
def store(dbdir):
    return Store(dbdir / "office.db")


class _WalletStub:
    """测试用钱包：默认余额 999，同一幂等键重复调用返回同一笔（不重复记账）。"""

    def __init__(self, default=999):
        self.default = default
        self.balances = {}
        self.ops = {}
        self.fail_spend = False
        self.fail_grant = False

    def balance(self, uid):
        return {"ok": True, "balance": self.balances.get(uid, self.default)}

    def spend(self, uid, amount, key, reason=""):
        if key in self.ops:
            return dict(self.ops[key], replayed=True)
        if self.fail_spend:
            raise office_wallet.WalletUnavailable("钱包挂了")
        bal = self.balances.get(uid, self.default)
        if bal < amount:
            raise office_wallet.WalletRefused("余额不足：现有 {} 币".format(bal))
        bal -= amount
        self.balances[uid] = bal
        result = {"ok": True, "balance": bal, "tx": "tx-" + key, "amount": amount}
        self.ops[key] = result
        return result

    def grant(self, uid, amount, key, reason=""):
        if key in self.ops:
            return dict(self.ops[key], replayed=True)
        if self.fail_grant:
            raise office_wallet.WalletUnavailable("发币失败")
        bal = self.balances.get(uid, self.default) + amount
        self.balances[uid] = bal
        result = {"ok": True, "balance": bal, "tx": "tx-" + key, "amount": amount}
        self.ops[key] = result
        return result

    def lookup(self, key):
        if key not in self.ops:
            return {"ok": True, "state": "absent"}
        return {"ok": True, "state": "applied", "tx": self.ops[key]["tx"]}


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    saved = features.overrides()
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    features.set_overrides({TEA_CID + "|office": True})
    monkeypatch.setattr(office, "log", lambda *a: None)
    monkeypatch.setattr(office, "TASKS", set())
    monkeypatch.setattr(office, "_gender_of", lambda *a: "未知")
    # 平台性别默认"没有值"；要测平台优先的用例自己再 monkeypatch 一次
    monkeypatch.setattr(office, "_platform_gender", lambda cid, uid: "")
    monkeypatch.setattr(office, "get_full_name", lambda uid: "成员-" + uid)
    monkeypatch.delenv("DZMM_EMPLOYEE_PROFILE_MODE", raising=False)
    monkeypatch.delenv("DZMM_EMPLOYEE_RANK_ORDER", raising=False)
    wallet = _WalletStub()
    monkeypatch.setattr(office, "wallet_adapter", lambda: wallet)
    monkeypatch.setattr(office, "_test_wallet", wallet, raising=False)
    yield
    features.set_overrides(saved)


def activate(store, now=1000):
    store.preference(TEA_CID, "b", True, now)
    sid, op = store.start(TEA_CID, "a", "b", REF, [], now)
    assert store.ready(sid, op, TEXT, now)
    assert store.dispatch(sid, op, now)
    store.delivered(sid, op, "platform1", now)
    return sid, op


def test_default_allow_start_continue_and_dispatch_without_preference(store):
    assert store.status(TEA_CID, "b", 1000)["allowed"]
    sid, op = store.start(TEA_CID, "a", "b", REF, [], 1000)
    assert store.ready(sid, op, TEXT, 1000)
    assert Store(store.path).dispatch(sid, op, 1000)
    store.delivered(sid, op, "first", 1000)
    sid2, op2 = Store(store.path).begin_continue(TEA_CID, "b", REF, [], 1001)
    assert sid2 == sid
    assert store.ready(sid, op2, TEXT, 1001)
    assert Store(store.path).dispatch(sid, op2, 1001)
    with store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM preferences").fetchone()[0] == 0


def test_explicit_refusal_and_allow_survive_restarts(store):
    store.preference(TEA_CID, "b", False)
    assert not Store(store.path).status(TEA_CID, "b", 1000)["allowed"]
    with pytest.raises(RuleError, match="已拒绝"):
        Store(store.path).start(TEA_CID, "a", "b", REF, [], 1000)
    assert store.current(TEA_CID, "a") is None
    assert Store(store.path).status(TEA_CID, "new-member", 1000)["allowed"]
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


def test_no_daily_limit_but_only_one_session_at_a_time(store):
    """2026-11-06：每天次数不限；仍然一次只能在一场里。"""
    sid, _ = activate(store)
    store.preference(TEA_CID, "c", True)
    with pytest.raises(RuleError, match="另一场剧情"):
        Store(store.path).start(TEA_CID, "a", "c", REF, [], 1001)
    assert store.end(TEA_CID, "b")
    # 同一天可以立刻再来一场（不再受每日一次限制）
    same_day, _ = Store(store.path).start(TEA_CID, "a", "c", REF, [], 1001)
    assert same_day
    assert store.end(TEA_CID, "a")
    again, _ = Store(store.path).start(TEA_CID, "a", "c", REF, [], 1002)
    assert again


def test_midnight_beijing_and_reserved_day():
    assert local_day(1792511999) != local_day(1792512000)  # 2026-10-20 16:00 UTC


@pytest.mark.parametrize("stage", ["generating", "ready", "sending", "active"])
def test_refuse_always_closes_and_never_delivers_after(store, stage):
    """拒绝一定关掉会话；拒绝之后迟到的投递不会被接受（额度概念已移除）。"""
    store.preference(TEA_CID, "b", True)
    sid, op = store.start(TEA_CID, "a", "b", REF, [], 1000)
    if stage != "generating":
        store.ready(sid, op, TEXT, 1000)
    if stage in ("sending", "active"):
        store.dispatch(sid, op, 1000)
    if stage == "active":
        store.delivered(sid, op, "mid", 1000)
    store.preference(TEA_CID, "b", False, 1001)
    assert store.get(sid)["status"] == "refused"
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
    assert store.current(TEA_CID, "a") is None


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
    assert store.get(sid)["status"] == "expired"


def test_generation_crash_restores_quota_but_other_sessions_survive(store):
    store.preference(TEA_CID, "b", True)
    sid, op = store.start(TEA_CID, "a", "b", REF, [], 1000)
    store.preference(TEA_CID, "d", True)
    other, _ = store.start(TEA_CID, "c", "d", REF, [], 1100)
    store.sweep(TEA_CID, 1120)
    assert store.get(sid)["status"] == "expired"
    assert store.get(other)["status"] == "generating"
    assert store.current(TEA_CID, "a") is None


@pytest.mark.parametrize("a,b", [(2, 3), (2, 2), (5, 2)])
@pytest.mark.parametrize("department", ["d", "other", None])
def test_any_rank_direction_is_allowed_regardless_of_department(a, b, department):
    """2026-11-05 起：任何方向都能发起（概率由职级差决定），部门不参与资格判定。"""
    p1, p2 = profile("a", a), profile("b", b)
    if department is None:
        p1.pop("department_id")
        p2.pop("department_id")
    else:
        p2["department_id"] = department
    office.eligibility(p1, p2)


def test_unverifiable_rank_is_refused_without_charging():
    with pytest.raises(RuleError, match="职级"):
        office.eligibility({"rank_level": None}, {"rank_level": 3})


def test_higher_rank_can_target_other_or_unknown_department():
    office.eligibility(profile("a", 3, dept="first"), profile("b", 1, dept="second"))
    office.eligibility({"rank_level": 3}, {"rank_level": 1})


@pytest.mark.parametrize("gender,expected", [("male", "男"), ("female", "女"), ("unknown", "未知"), (None, "未知")])
def test_employee_profile_gender(gender, expected):
    assert employee_api.normalize_profile(profile("a", gender=gender), "a")["gender"] == expected


@pytest.mark.parametrize("changes", [{"rank_level": True}, {"rank_level": None}, {"rank_level": "3"},
                                     {"rank_name": ""}, {"rank_name": None},
                                     {"platform_id": "other"}, {"employment_status": "inactive"}])
def test_bad_employee_data_fails_closed(changes):
    with pytest.raises(employee_api.EmployeeUnavailable):
        employee_api.normalize_profile({**profile("a"), **changes}, "a")


@pytest.mark.parametrize("departments", [{}, {"department_id": None, "department_name": None},
                                       {"department_id": "", "department_name": " "},
                                       {"department_id": 123, "department_name": {}}])
def test_optional_department_fields_do_not_block_profiles(departments):
    data = profile("a")
    data.pop("department_id")
    data.pop("department_name")
    normalized = employee_api.normalize_profile({**data, **departments}, "a")
    assert normalized["department_id"] == normalized["department_name"] == ""
    assert normalized["platform_id"] == "a" and normalized["rank_level"] == 1


def test_valid_department_fields_are_preserved():
    normalized = employee_api.normalize_profile(profile("a", dept="sales"), "a")
    assert normalized["department_id"] == "sales"
    assert normalized["department_name"] == "同一个部门"


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


@pytest.mark.parametrize("raw", ["", "null", "{}", "[]", '[true]', '[1]', '[""]', '["主管", " 主管 "]'])
def test_invalid_rank_order_fails_before_network(monkeypatch, raw):
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_PATH", "/users/{platform_id}/game-quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_MODE", "game_quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_RANK_ORDER", raw)
    monkeypatch.setattr(employee_api.requests, "request", lambda *a, **kw: pytest.fail("unexpected network"))
    with pytest.raises(employee_api.EmployeeUnavailable, match="职级顺序"):
        employee_api.Client().profile("a")


@pytest.mark.parametrize("limit", [-1, 0, 99, None])
def test_game_quota_uses_confirmed_rank_order_and_preserves_identity(monkeypatch, limit):
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_PATH", "/users/{platform_id}/game-quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_MODE", "game_quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_RANK_ORDER", json.dumps(["实习生", "主管", "核心董事会"]))
    data = {"platform_id": "a", "display_name": "昵称", "rank_name": "主管", "rank_limit": limit,
            "unlimited": True, "remaining": None, "rank_level": 999}
    calls = []
    monkeypatch.setattr(employee_api.Client, "_request", lambda *args: calls.append(args) or data)
    result = employee_api.Client().profile("a")
    assert result["rank_level"] == 2 and result["name"] == "昵称"
    assert result["gender"] == "未知" and result["department_id"] == ""
    assert calls[0][1:] == ("GET", "/users/a/game-quota")
    data["platform_id"] = "other"
    with pytest.raises(employee_api.EmployeeUnavailable):
        employee_api.Client().profile("a")


def test_unrecognized_rank_never_gets_default_level(monkeypatch):
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_PATH", "/users/{platform_id}/game-quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_MODE", "game_quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_RANK_ORDER", '["主管"]')
    monkeypatch.setattr(employee_api.Client, "_request", lambda *a: {"platform_id": "a", "rank_name": "新职级"})
    with pytest.raises(employee_api.EmployeeUnavailable, match="无法判断"):
        employee_api.Client().profile("a")


def test_game_quota_does_not_enable_profile_mode_implicitly(monkeypatch):
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_PATH", "/users/{platform_id}/game-quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_RANK_ORDER", '["主管"]')
    monkeypatch.setattr(employee_api.Client, "_request", lambda *a: {"platform_id": "a", "rank_name": "主管"})
    with pytest.raises(employee_api.EmployeeUnavailable):
        employee_api.Client().profile("a")


def test_unknown_profile_mode_does_not_request_network(monkeypatch):
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_PATH", "/users/{platform_id}/game-quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_MODE", "guess")
    monkeypatch.setattr(employee_api.requests, "request", lambda *a, **kw: pytest.fail("unexpected network"))
    with pytest.raises(employee_api.EmployeeUnavailable, match="类型配置"):
        employee_api.Client().profile("a")


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
    async def members(self, cid, *, limit=None):
        assert limit == 2000
        return {uid: {"protected": False, "name": uid} for uid in ("a", "b", "c", "d")}
    monkeypatch.setattr(office.Platform, "members", members)
    monkeypatch.setattr(office, "generate", lambda *a: TEXT)
    monkeypatch.setattr(office, "generate_failure", lambda *a, **k: FAIL_TEXT)
    # 默认钉成"一定赢"，需要测失败路径的用例自己再 monkeypatch 一次。
    monkeypatch.setattr(office, "_roll", lambda: 0.0)
    store.preference(TEA_CID, "b", True)
    return FakeSender(), store, FakeClient()


async def issue(service, text, mid="m1", uid="a", *, quote=None, quote_key="reference"):
    sender, store, client = service
    msg = {"message_id": mid, "sent_by": uid, "content": {"type": "text", "text": text}}
    if quote is not None:
        msg["content"][quote_key] = quote
    await office.handle(sender, TEA_CID, uid, text, msg, store=store, client=client)


async def drain():
    tasks = list(office.TASKS)
    if tasks:
        await asyncio.gather(*tasks)


def stories(sender):
    """只看剧情/提示正文：排除生成前那条「已扣 X 币」的即时扣费提醒。"""
    return [item for item in sender.sent if "已扣" not in item[0]]


@pytest.mark.parametrize("prefix", ["#", "＃"])
@pytest.mark.parametrize("direction", ["", " 开完会后聊一聊"])
@pytest.mark.parametrize("quote_key", ["reference", "quote", "reply"])
def test_quoted_start_uses_verified_author_and_quotes_actual_command(service, monkeypatch, prefix, direction, quote_key):
    sender, store, client = service
    async def original(self, cid, mid, stamp):
        assert (cid, mid, stamp) == (TEA_CID, "original-mid", "")
        return {"message_id": mid, "chatroom_id": cid, "sent_by": "b"}
    monkeypatch.setattr(office.Platform, "message", original)
    monkeypatch.setattr(client, "match", lambda *a: pytest.fail("quote must not use name matching"))
    generated = []
    def generate(people, history, payload):
        generated.append((people, payload))
        return TEXT
    monkeypatch.setattr(office, "generate", generate)
    quoted = {"id": "original-mid", "sentBy": "forged-author", "content": {
        "type": "text", "text": "untrusted direction", "reference": {"sentBy": "nested-author"}}}
    async def run():
        await issue(service, prefix + "潜规则" + direction, quote=quoted, quote_key=quote_key)
        await drain()
    asyncio.run(run())
    row = store.current(TEA_CID, "a")
    assert row["actor"] == "a" and row["target"] == "b" and row["status"] == "active"
    assert generated[0][1] == direction.strip()
    ref = sender.sent[0][2]
    assert ref["id"] == "m1" and ref["sentBy"] == "a"
    assert ref["content"] == {"type": "text", "text": prefix + "潜规则" + direction}


@pytest.mark.parametrize("failure", ["missing", "wrong_id", "other_room", "no_author", "self", "outside", "refused"])
def test_unverifiable_or_ineligible_quote_never_starts_or_charges(service, monkeypatch, failure):
    sender, store, client = service
    async def original(self, cid, mid, stamp):
        if failure == "missing":
            raise office.ModerationUnavailable("not found")
        return {"message_id": "wrong" if failure == "wrong_id" else mid,
                "chatroom_id": "other" if failure == "other_room" else cid,
                "sent_by": {"no_author": "", "self": "a", "outside": "outsider"}.get(failure, "b")}
    monkeypatch.setattr(office.Platform, "message", original)
    monkeypatch.setattr(client, "match", lambda *a: pytest.fail("invalid quote must not fall back to name matching"))
    if failure == "refused":
        store.preference(TEA_CID, "b", False)
    async def run():
        await issue(service, "#潜规则", quote={"id": "original-mid", "sentBy": "b"})
        await drain()
    asyncio.run(run())
    assert store.current(TEA_CID, "a") is None
    assert len(sender.sent) == 1 and sender.sent[0][2]["id"] == "m1"


@pytest.mark.parametrize("quote", [{}, [], "bad", {"id": 123}])
def test_malformed_quote_is_rejected_without_network(service, monkeypatch, quote):
    async def no_network(*args):
        pytest.fail("malformed quote must not query platform")
    monkeypatch.setattr(office.Platform, "message", no_network)
    async def run():
        await issue(service, "#潜规则", quote=quote)
        await drain()
    asyncio.run(run())
    assert "引用消息" in service[0].sent[0][0]
    assert service[1].current(TEA_CID, "a") is None


@pytest.mark.parametrize("end", ["#结束", "＃结束", "#潜规则结束", "＃潜规则结束"])
def test_end_alias_only_ends_senders_own_session(service, monkeypatch, end):
    sender, store, client = service
    monkeypatch.setattr(client, "profile", lambda uid: employee_api.normalize_profile(
        profile(uid, 3 if uid in ("a", "c") else 1), uid))
    async def run():
        await issue(service, "#潜规则 b", "first", "a")
        await issue(service, "#潜规则 d", "second", "c")
        await drain()
        await issue(service, end, "stop", "b")
    asyncio.run(run())
    assert store.current(TEA_CID, "a") is None
    assert store.current(TEA_CID, "c")["status"] == "active"
    assert sender.sent[-1][2]["id"] == "stop" and sender.sent[-1][2]["sentBy"] == "b"
    assert "本场结束" in sender.sent[-1][0]


@pytest.mark.parametrize("continuation", ["#潜规则继续 第二天", "#继续", "＃继续 第二天"])
def test_service_start_continue_end_quote_each_command(service, continuation):
    sender, store, _ = service
    async def run():
        await issue(service, "#潜规则 @b 开完会后")
        await drain()
        await issue(service, continuation, "m2", "b")
        await drain()
        await issue(service, "#潜规则结束", "m3", "b")
        await issue(service, "#潜规则 @b", "m1")  # 重放原指令
    asyncio.run(run())
    told = stories(sender)
    assert [s[2]["id"] for s in told] == ["m1", "m2", "m3"]
    assert told[0][0].endswith(TEXT) and len(told) == 3
    assert "【会话 " in told[0][0] and "a × b" in told[0][0]
    assert told[1][2]["content"]["text"] == continuation
    assert all("\n" not in text and "\r" not in text for text, _, _ in sender.sent)
    assert not store.current(TEA_CID, "a")


def test_short_continue_without_session_replies_to_command(service):
    sender, store, _ = service
    async def run():
        await issue(service, "#继续")
        await drain()
    asyncio.run(run())
    assert len(sender.sent) == 1
    assert "没有可以继续的会话" in sender.sent[0][0]
    assert sender.sent[0][2]["id"] == "m1"


def test_two_sessions_finish_out_of_order_and_continue_with_own_history(service, monkeypatch):
    sender, store, client = service
    monkeypatch.setattr(client, "profile", lambda uid: employee_api.normalize_profile(
        profile(uid, 3 if uid in ("a", "c") else 1), uid))
    second_sent = threading.Event()
    original_send = sender.send_reply_with_id

    async def send(text, cid, reference, **kwargs):
        mid = await original_send(text, cid, reference, **kwargs)
        if reference["sentBy"] == "c" and "已扣" not in text:
            second_sent.set()          # 等的是 c 的**正文**，不是扣费提醒
        return mid
    sender.send_reply_with_id = send

    def generate(people, history, direction):
        actor = people[0]["platform_id"]
        if actor == "a":
            assert second_sent.wait(3), "second session should send independently"
        assert all(("alpha" in h) == (actor == "a") for h in history)
        return actor + " " + direction + "。" + TEXT
    monkeypatch.setattr(office, "generate", generate)

    async def run():
        await asyncio.gather(issue(service, "#潜规则 b alpha", "start-a", "a"),
                             issue(service, "#潜规则 d beta", "start-c", "c"))
        await drain()
        first = store.current(TEA_CID, "a")
        second = store.current(TEA_CID, "c")
        assert first["id"] != second["id"]
        second_sent.clear()
        await asyncio.gather(issue(service, "#继续 alpha-next", "next-a", "a"),
                             issue(service, "#继续 beta-next", "next-c", "c"))
        await drain()
        return first["id"], second["id"]
    first_id, second_id = asyncio.run(run())
    told = stories(sender)
    # 并发两场互不干扰：每条正文引用自己的指令，且各自的续写排在自己的首段之后。
    # 不写死两场之间的全局交错顺序（那只是调度巧合）。
    ids = [s[2]["id"] for s in told]
    assert sorted(ids) == ["next-a", "next-c", "start-a", "start-c"]
    for start, nxt in (("start-a", "next-a"), ("start-c", "next-c")):
        assert ids.index(start) < ids.index(nxt)
    for text, _, ref in told:
        actor = ref["sentBy"]
        sid = first_id if actor == "a" else second_id
        assert "会话 " + sid[:8] in text
        assert "回复 成员-" + actor in text
        assert ("alpha" in text) == (actor == "a")
        assert ref["content"]["text"].startswith("#")
    assert store.get(first_id)["continues"] == store.get(second_id)["continues"] == 1


def test_identical_errors_for_different_commands_are_distinct_and_quoted(service):
    sender, _, _ = service
    async def run():
        for mid, uid in (("first", "a"), ("second", "c"), ("third", "a")):
            await issue(service, "#继续", mid, uid)
        await drain()
    asyncio.run(run())
    assert len(sender.sent) == 3
    assert len({r[0] for r in sender.sent}) == 3
    assert {(r[2]["id"], r[2]["sentBy"]) for r in sender.sent} == {
        ("first", "a"), ("second", "c"), ("third", "a")}
    assert all("没有可以继续的会话" in r[0] for r in sender.sent)


@pytest.mark.parametrize("missing_department", [False, True])
def test_cross_department_snapshot_survives_rank_change(service, monkeypatch, missing_department):
    """2026-11-05：任何方向都能续写；分账比例用首段快照，不随中途升降职改变。"""
    sender, store, client = service
    levels = {"a": 3, "b": 1}
    def employee(uid):
        data = profile(uid, levels[uid], dept=uid)
        if missing_department:
            data.pop("department_id")
            data.pop("department_name")
        return employee_api.normalize_profile(data, uid)
    monkeypatch.setattr(client, "profile", employee)
    async def run():
        await issue(service, "#潜规则 b")
        await drain()
        row = store.current(TEA_CID, "a")
        assert row and row["status"] == "active"
        assert abs(row["split_ratio"] - 0.5) < 1e-9, "上级→下级：目标分账 50%"
        assert store.current(TEA_CID, "a")["id"] == row["id"]
        await issue(service, "#潜规则继续", "m2", "b")
        await drain()
        assert store.current(TEA_CID, "a")["continues"] == 1
        assert abs(store.current(TEA_CID, "a")["split_ratio"] - 0.5) < 1e-9
        levels["b"] = 4                       # 目标升职：续写仍按首段比例，且不再拒绝
        await issue(service, "#潜规则继续", "m3", "b")
        await drain()
    asyncio.run(run())
    told = stories(sender)
    assert [s[2]["id"] for s in told] == ["m1", "m2", "m3"]
    assert all(s[0].endswith(TEXT) for s in told)
    assert "续写进度2/5" in told[2][0]
    assert abs(store.current(TEA_CID, "a")["split_ratio"] - 0.5) < 1e-9


def test_cross_department_respects_explicit_target_refusal(service, monkeypatch):
    sender, store, client = service
    monkeypatch.setattr(client, "profile", lambda uid: employee_api.normalize_profile(
        profile(uid, 3 if uid == "a" else 1, dept=uid), uid))
    store.preference(TEA_CID, "b", False)
    async def run():
        await issue(service, "#潜规则 b")
        await drain()
    asyncio.run(run())
    assert len(sender.sent) == 1 and "已拒绝" in sender.sent[0][0]
    assert not store.current(TEA_CID, "a")


def test_profiles_fill_missing_gender_from_existing_platform_lookup(service, monkeypatch):
    calls = []
    def gender(uid, cid, write_back):
        calls.append((uid, cid, write_back))
        return "女生" if uid == "a" else "男生"
    monkeypatch.setattr(office, "_gender_of", gender)
    people = asyncio.run(office._profiles(TEA_CID, "a", "b", service[2]))
    assert [p["gender"] for p in people] == ["女", "男"]
    assert calls == [("a", TEA_CID, False), ("b", TEA_CID, False)]


def test_profiles_keep_explicit_api_gender_and_unknown_fallback(service, monkeypatch):
    def employee(uid):
        return employee_api.normalize_profile(profile(uid, 3 if uid == "a" else 1,
                                                      gender="female" if uid == "a" else "unknown"), uid)
    monkeypatch.setattr(service[2], "profile", employee)
    calls = []
    monkeypatch.setattr(office, "_gender_of", lambda uid, *a: calls.append(uid) or "未知")
    people = asyncio.run(office._profiles(TEA_CID, "a", "b", service[2]))
    assert [p["gender"] for p in people] == ["女", "未知"]
    assert calls == ["b"]


def test_real_employee_adapter_starts_and_unknown_rank_preserves_quota(service, monkeypatch):
    sender, store, _ = service
    monkeypatch.setenv("DZMM_INTEGRATION_BASE_URL", "https://example.invalid/api/integration")
    monkeypatch.setenv("DZMM_INTEGRATION_API_KEY", "test-only-secret")
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_PATH", "/users/{platform_id}/game-quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_PROFILE_MODE", "game_quota")
    monkeypatch.setenv("DZMM_EMPLOYEE_RANK_ORDER", '["实习生", "主管"]')
    ranks = {"a": "未确认新职级", "b": "实习生"}
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url))
        if url.endswith("/users/match"):
            data = {"status": "matched", "matches": [{"platform_id": "b"}]}
        else:
            uid = url.split("/")[-2]
            data = {"platform_id": uid, "display_name": uid, "rank_name": ranks[uid], "rank_limit": 0}
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(employee_api.requests, "request", request)
    adapter = (sender, store, employee_api.Client())
    async def run():
        await issue(adapter, "#潜规则 b")
        await drain()
        assert not store.current(TEA_CID, "a")
        ranks["a"] = "主管"
        await issue(adapter, "#潜规则 b", "m2")
        await drain()
        assert store.current(TEA_CID, "a")["status"] == "active"
        store.end(TEA_CID, "a")
        ranks["a"] = "未确认新职级"
        await issue(adapter, "#潜规则 b", "m3")
        await drain()
    asyncio.run(run())
    told = stories(sender)
    assert "无法判断" in told[0][0]
    assert told[1][0].endswith(TEXT)
    assert "无法判断" in told[2][0]
    assert not store.current(TEA_CID, "a")
    assert office._test_wallet.balances.get("a") == 999 - 5
    assert all(method in ("GET", "POST") and "/coins/" not in url for method, url in calls)


def test_refusal_during_generation_suppresses_stale_output(service, monkeypatch):
    sender, store, _ = service
    started, release = threading.Event(), threading.Event()
    def generate(*args):
        started.set()
        assert release.wait(3)
        return TEXT
    monkeypatch.setattr(office, "generate", generate)
    async def run():
        await issue(service, "#潜规则 b")
        assert await asyncio.to_thread(started.wait, 3)
        await issue(service, "#拒绝潜规则", "refuse", "b")
        release.set()
        await drain()
    asyncio.run(run())
    told = stories(sender)
    assert len(told) == 1 and told[0][2]["id"] == "refuse"
    assert "已扣" in sender.sent[0][0], "扣费提醒在生成前就已经发出"


def test_generation_failure_releases_first_quota(service, monkeypatch):
    sender, store, _ = service
    monkeypatch.setattr(office, "generate", lambda *args: (_ for _ in ()).throw(ValueError("not valid")))
    async def run():
        await issue(service, "#潜规则 b")
        await drain()
    asyncio.run(run())
    assert sender.sent[0][2]["id"] == "m1"


def test_foreign_continue_rejected_without_query_or_quota(service):
    sender, store, _ = service
    activate(store)
    async def run():
        await issue(service, "#潜规则继续", uid="c")
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
    assert calls[1][1]["previous_draft"] == "太短。"
    assert "3个非空白字符" in calls[1][1]["length_correction"]
    assert any(word in calls[0][0] for word in ("未知则使用姓名", "未知则用姓名"))


def test_generator_repairs_obviously_short_drafts_with_measured_feedback(monkeypatch):
    calls = []
    drafts = ["字" * 149 + "。", "字" * 189 + "。", "字" * 519 + "。"]
    def raw(prompt, content, **kwargs):
        calls.append(json.loads(content))
        return drafts[len(calls) - 1], "stop"
    monkeypatch.setattr(office_scene.brains, "_call_llm_raw", raw)
    assert office_scene.generate([], [], "日常") == drafts[-1]
    assert len(calls) == 3
    for i, length in ((1, 150), (2, 190)):
        assert calls[i]["previous_draft"] == drafts[i - 1]
        assert str(length) + "个非空白字符" in calls[i]["length_correction"]


def test_generator_exhausted_length_repair_reports_safe_metrics(monkeypatch):
    monkeypatch.setattr(office_scene.brains, "_call_llm_raw", lambda *a, **kw: ("字" * 189 + "。", "stop"))
    with pytest.raises(office_scene.SceneGenerationError) as result:
        office_scene.generate([], [], "日常")
    assert result.value.length == 190 and result.value.attempts == 3
    assert result.value.finish == "stop"
    assert "字字" not in str(result.value)


def test_generator_repair_obeys_total_deadline(monkeypatch):
    clock = iter((0, 0, 45))
    monkeypatch.setattr(office_scene.time, "monotonic", lambda: next(clock))
    calls = []
    def raw(*a, **kw):
        calls.append(kw["timeout"])
        return "字" * 189 + "。", "stop"
    monkeypatch.setattr(office_scene.brains, "_call_llm_raw", raw)
    with pytest.raises(office_scene.SceneGenerationError) as result:
        office_scene.generate([], [], "日常", timeout=45)
    assert calls == [45] and result.value.attempts == 1


def test_specific_length_failure_quotes_command_and_preserves_quota(service, monkeypatch):
    sender, store, _ = service
    def fail(*args):
        raise office_scene.SceneGenerationError("office_scene_invalid_length_or_finish", length=190,
                                                finish="stop", attempts=3)
    monkeypatch.setattr(office, "generate", fail)
    async def run():
        await issue(service, "#潜规则 b")
        await drain()
    asyncio.run(run())
    told = stories(sender)
    assert len(told) == 1 and "内容过短" in told[0][0]
    assert told[0][2]["id"] == "m1"


@pytest.mark.parametrize("length", [200, 353, 381, 399, 400, 600])
def test_complete_short_scene_is_accepted_without_length_rewrite(monkeypatch, length):
    calls = []
    text = "字" * (length - 1) + "。"
    def raw(*a, **kw):
        calls.append(kw)
        return text, "stop"
    monkeypatch.setattr(office_scene.brains, "_call_llm_raw", raw)
    assert office_scene.generate([], [], "日常") == text
    assert len(calls) == 1


@pytest.mark.parametrize("length,finish,ending", [(199, "stop", "。"), (601, "stop", "。"),
    (353, "length", "。"), (353, "stop", "字"), (353, "content_filter", "。")])
def test_scene_still_rejects_obviously_short_or_incomplete_text(monkeypatch, length, finish, ending):
    monkeypatch.setattr(office_scene.brains, "_call_llm_raw",
                        lambda *a, **kw: ("字" * (length - 1) + ending, finish))
    with pytest.raises(office_scene.SceneGenerationError):
        office_scene.generate([], [], "日常")


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
        if "已扣" not in text:                 # 扣费提醒不算一次投递
            calls.append((text, reference["id"]))
        if len(calls) < 2:
            raise ReplyNotSent("平台明确拒绝")
        return "confirmed"
    sender.send_reply_with_id = send
    async def run():
        await issue(service, "#潜规则 b")
        await drain()
    asyncio.run(run())
    assert len(generate_calls) == 1 and len(calls) == 2 and calls[0] == calls[1]
    assert store.current(TEA_CID, "a")["status"] == "active"


@pytest.mark.parametrize("continuation", [False, True])
def test_content_rejection_does_not_retry_and_only_refunds_unsent_start(service, continuation):
    sender, store, _ = service
    original_send = sender.send_reply_with_id
    attempts = []
    async def rejected(text, cid, reference, *, guard=None):
        if guard:
            assert guard()
            attempts.append((text, reference))
            raise ReplyContentRejected("平台拒绝重复内容")
        return await original_send(text, cid, reference)
    async def run():
        if continuation:
            await issue(service, "#潜规则 b", "start")
            await drain()
        sender.send_reply_with_id = rejected
        await issue(service, "#继续" if continuation else "#潜规则 b", "rejected")
        await drain()
    asyncio.run(run())
    assert len(attempts) == 1
    assert "平台拒绝了本段内容" in sender.sent[-1][0]
    assert sender.sent[-1][2]["id"] == "rejected"
    assert store.current(TEA_CID, "a") is None or continuation
    row = store.current(TEA_CID, "a")
    if continuation:
        assert row["status"] == "active" and row["continues"] == 0
    else:
        assert row is None


def test_platform_duplicate_rejection_is_distinct_from_retryable_failure():
    async def run():
        async def ack(*args, **kwargs):
            return {"success": False, "error": "请勿发送重复内容", "errorCode": "VALIDATION_ERROR"}
        sub = SioSubscriber.__new__(SioSubscriber)
        sub._sio = SimpleNamespace(call=ack)
        sub._connected = asyncio.Event()
        sub._connected.set()
        sub._action_lock = asyncio.Lock()
        with pytest.raises(ReplyContentRejected):
            await sub.send_reply_confirmed(TEA_CID, "a", TEXT, REF)
    asyncio.run(run())


def test_scene_multiline_output_is_one_paragraph_without_truncation(monkeypatch):
    paragraphs = "\n\n".join(TEXT[i:i + 60] for i in range(0, len(TEXT), 60))
    monkeypatch.setattr(office_scene.brains, "_call_llm_raw", lambda *a, **kw: (paragraphs, "stop"))
    result = office_scene.generate([], [], "办公室日常")
    assert "\n" not in result and "\r" not in result
    assert result.replace(" ", "") == TEXT
    assert office_scene.visible_length(result) == office_scene.visible_length(TEXT)


def test_uncertain_send_is_not_retried_or_refunded(service):
    sender, store, _ = service
    calls = []
    async def send(text, cid, reference, *, guard=None):
        if guard:
            assert guard()
        if "已扣" not in text:
            calls.append(text)
        raise TimeoutError("test only")
    sender.send_reply_with_id = send
    async def run():
        await issue(service, "#潜规则 b")
        await drain()
    asyncio.run(run())
    assert len(calls) == 1 and calls[0].endswith(TEXT)
    assert store.current(TEA_CID, "a")["status"] == "ambiguous"
    assert office._test_wallet.balances.get("a") == 999 - 5


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


@pytest.mark.parametrize("change", ["author", "command"])
def test_native_quote_rejects_wrong_author_or_command(monkeypatch, change):
    async def send(*args, **kwargs):
        return "known-mid"
    monkeypatch.setattr(account, "_LIVE_SIO", SimpleNamespace(send_reply_confirmed=send))
    ref = json.loads(json.dumps(REF))
    if change == "author":
        ref["sentBy"] = "another-person"
    else:
        ref["content"]["text"] = "another-command"
    monkeypatch.setattr(account, "get_messages", lambda *args: [{"message_id": "known-mid",
        "sent_by": account.NUO_UID, "content": {"type": "text", "text": TEXT, "reference": ref}}])
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


# ---------------------------------------------------------------- 付费概率玩法

def test_paid_start_charges_and_splits(service):
    sender, store, _ = service
    async def run():
        await issue(service, "#潜规则 b 40 开会")
        await drain()
    asyncio.run(run())
    assert "已扣 45 币" in sender.sent[0][0], "生成之前先给扣费提醒"
    head = stories(sender)[0][0]
    assert "本次支付45币" in head
    assert "基础5币＋加币40币" in head
    assert "成功率90%" in head and "判定成功" in head
    assert "b获得22币" in head
    wallet = office._test_wallet
    assert wallet.balances.get("a") == 999 - 45
    assert wallet.balances.get("b") == 999 + 22


def test_paid_start_rejects_overpay_without_charging(service):
    sender, store, _ = service
    async def run():
        await issue(service, "#潜规则 b 51")
        await drain()
    asyncio.run(run())
    assert "50" in sender.sent[0][0]
    assert store.current(TEA_CID, "a") is None
    assert office._test_wallet.balances.get("a") in (None, 999)


def test_paid_losing_roll_charges_but_no_split(service, monkeypatch):
    sender, store, _ = service
    monkeypatch.setattr(office, "_roll", lambda: 0.999)
    async def run():
        await issue(service, "#潜规则 b 0")
        await drain()
    asyncio.run(run())
    assert "已扣 5 币" in sender.sent[0][0]
    head = stories(sender)[0][0]
    assert "判定失败" in head and "今日发起次数已使用" in head
    wallet = office._test_wallet
    assert wallet.balances.get("a") == 999 - 5
    assert wallet.balances.get("b") in (None, 999)
    assert store.current(TEA_CID, "a") is None


def test_rules_command_lists_fee_probability_and_split(service):
    sender, store, _ = service
    async def run():
        await issue(service, "#潜规则规则")
        await drain()
    asyncio.run(run())
    text = stories(sender)[0][0]
    assert "5 币" in text and "99%" in text and "上级→下级 50%" in text


def test_charge_notices_precede_generation_and_hide_result(service, monkeypatch):
    """生成之前先发一条「已扣 X 币」，而且不能剧透判定结果。"""
    sender, store, _ = service
    order = []
    monkeypatch.setattr(office, "generate", lambda *a: order.append("generate") or TEXT)
    original = sender.send_reply_with_id

    async def send(text, cid, reference, **kwargs):
        order.append("notice" if "已扣" in text else "story")
        return await original(text, cid, reference, **kwargs)
    sender.send_reply_with_id = send

    async def run():
        await issue(service, "#潜规则 b 40 开会")
        await drain()
        await issue(service, "#潜规则继续 接着来", "m2")
        await drain()
    asyncio.run(run())

    notices = [item[0] for item in sender.sent if "已扣" in item[0]]
    assert len(notices) == 2
    assert "已扣 45 币" in notices[0] and "成功率 90%" in notices[0]
    assert "余额 954" in notices[0]
    assert "续写第 1 段" in notices[1] and "已扣 5 币" in notices[1]
    for text in notices:
        assert "判定" not in text and "失败" not in text, "扣费提醒不能剧透判定结果"
    assert order == ["notice", "generate", "story", "notice", "generate", "story"]


# ---------------------------------------------------------------- 性别缺失时的处理

@pytest.mark.parametrize("name,expected", [
    ("奈宝姐姐", "女"), ("兔子酱", "女"), ("鸽子（🩷豚豚的主人🩷）", "女"),
    ("夜班的大叔", "男"), ("阿辉先生", "男"), ("小少爷", "男"),
    ("小小糯", ""), ("沐白", ""), ("彻", ""), ("", ""), (None, ""),
])
def test_guess_gender_from_name(name, expected):
    assert office.rules.guess_gender(name) == expected


def test_unknown_gender_is_guessed_from_name_and_told_to_player(service, monkeypatch):
    """性别资料缺失：按名字选一个直接写，并在扣费提醒里说明。"""
    sender, store, client = service
    names = {"a": "发起者", "b": "奈宝姐姐"}
    monkeypatch.setattr(client, "profile", lambda uid: employee_api.normalize_profile(
        {**profile(uid, 3 if uid == "a" else 1), "display_name": names[uid]}, uid))
    seen = []
    monkeypatch.setattr(office, "generate",
                        lambda people, history, direction: seen.append(people) or TEXT)

    async def run():
        await issue(service, "#潜规则 b")
        await drain()
    asyncio.run(run())

    notice = sender.sent[0][0]
    assert "性别资料没填" in notice and "按名字写作「女」" in notice
    target = seen[0][1]
    assert target["gender"] == "女"
    assert "gender_note" not in target and "gender_source" not in target


def test_unknown_gender_without_name_hint_uses_neutral_wording(service):
    sender, store, _ = service
    seen = []
    import runtime.office as _office
    original = office.generate
    office.generate = lambda people, history, direction: seen.append(people) or TEXT
    try:
        async def run():
            await issue(service, "#潜规则 b")
            await drain()
        asyncio.run(run())
    finally:
        office.generate = original
    notice = sender.sent[0][0]
    assert "中性写法" in notice
    assert seen[0][1]["gender"] == "未知"


def test_platform_gender_wins_and_no_note(service, monkeypatch):
    sender, store, _ = service
    monkeypatch.setattr(office, "_platform_gender",
                        lambda cid, uid: "女" if uid == "b" else "男")
    seen = []
    monkeypatch.setattr(office, "generate",
                        lambda people, history, direction: seen.append(people) or TEXT)

    async def run():
        await issue(service, "#潜规则 b")
        await drain()
    asyncio.run(run())

    notice = sender.sent[0][0]
    assert "性别资料没填" not in notice
    assert seen[0][0]["gender"] == "男" and seen[0][1]["gender"] == "女"


def test_continue_keeps_first_segment_gender(service, monkeypatch):
    """首段按名字猜成「女」，之后平台补了性别也不在本场改口。"""
    sender, store, client = service
    names = {"a": "发起者", "b": "奈宝姐姐"}
    monkeypatch.setattr(client, "profile", lambda uid: employee_api.normalize_profile(
        {**profile(uid, 3 if uid == "a" else 1), "display_name": names[uid]}, uid))
    seen = []
    monkeypatch.setattr(office, "generate",
                        lambda people, history, direction: seen.append(people) or TEXT)

    async def run():
        await issue(service, "#潜规则 b")
        await drain()
        monkeypatch.setattr(office, "_platform_gender", lambda cid, uid: "男")
        await issue(service, "#潜规则继续", "m2")
        await drain()
    asyncio.run(run())

    assert seen[0][1]["gender"] == "女"          # 首段：按名字
    assert seen[1][1]["gender"] == "女"          # 续写：沿用快照，不被平台新值改掉


# ---------------------------------------------------------------- 帮助 / 规则（多行）

def test_help_command_keeps_line_breaks_and_is_free(service):
    sender, store, _ = service
    before = dict(office._test_wallet.balances)

    async def run():
        await issue(service, "#潜规则帮助")
        await drain()
    asyncio.run(run())

    assert len(sender.sent) == 1
    text = sender.sent[0][0]
    assert "潜规则怎么玩" in text and "发起：" in text and "#潜规则规则" in text
    assert "\n" in text, "帮助是多行文案，不能被压成一行"
    assert "扣费" not in text
    assert office._test_wallet.balances == before, "帮助不花钱"
    assert store.current(TEA_CID, "a") is None, "帮助不建会话"


def test_rules_command_keeps_line_breaks(service):
    sender, store, _ = service
    async def run():
        await issue(service, "#潜规则规则")
        await drain()
    asyncio.run(run())
    text = sender.sent[0][0]
    assert "潜规则规则" in text and "\n" in text and "次数：不限" in text


def test_help_needs_no_payload_and_ignores_extra(service):
    sender, store, _ = service
    async def run():
        await issue(service, "#潜规则帮助 多余参数")
        await drain()
    asyncio.run(run())
    assert "不需要额外参数" in sender.sent[0][0]
