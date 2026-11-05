import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import itertools

import pytest

from runtime import features
from runtime import rose_moderation as moderation
from runtime.moderation_platform import ModerationUnavailable, Platform
from runtime.moderation_store import Store

CID = moderation.ROSE_CID


@pytest.fixture(autouse=True)
def _feature_on():
    """is_command 现在按功能开关判定（多群共用一套流程）—— 测试群里钉成开。"""
    features.set_overrides({CID + "|rose_moderation": True})
    yield
    features.set_overrides({})


class FakePlatform:
    def __init__(self):
        self.admins = {"admin1", "admin2"}
        self.protected = {"owner", "bot"}
        self.messages = {}
        self.actions = []
        self.fail_recall = set()
        self.fail_kick = set()
        self.after_recall = None

    async def authorize(self, cid, actor, target=None):
        if actor not in self.admins:
            raise ModerationUnavailable("只有当前群管理员可以操作")
        if target in self.admins or target in self.protected:
            raise ModerationUnavailable("目标受保护")
        return "名字-" + str(target)

    async def message(self, cid, message_id, sent_at=""):
        if message_id not in self.messages:
            raise ModerationUnavailable("无法找到原消息")
        return self.messages[message_id]

    async def recall(self, cid, message_id):
        self.actions.append(("recall", message_id))
        if message_id in self.fail_recall:
            raise TimeoutError("可能已撤回")
        if self.after_recall:
            self.after_recall()

    async def kick(self, cid, uid):
        self.actions.append(("kick", uid))
        if uid in self.fail_kick:
            raise TimeoutError("可能已踢出")


class FakeSender:
    def __init__(self):
        self.sent = []
        self.failed = False

    async def send_with_id(self, text, cid):
        if self.failed:
            raise TimeoutError("private credential must never be echoed")
        message_id = "prompt-" + str(len(self.sent))
        self.sent.append((message_id, text, cid))
        return message_id


@pytest.fixture
def setup(dbdir):
    return moderation.Service(Store(dbdir / "moderation.db"), FakePlatform()), FakeSender()


def original(message_id="target1", uid="user1", text="原始发言"):
    return {"message_id": message_id, "sent_by": uid, "chatroom_id": CID,
            "sent_at": "2026-10-01T00:00:00Z", "content": {"type": "text", "text": text}}


def commands(service, sender):
    counter = itertools.count()

    async def send(text, quote=None, actor="admin1", message_id=None, dry=False, cid=CID):
        content = {"text": text}
        if quote:
            content["reference"] = {"id": quote, "sentBy": "forged", "content": {"text": "forged"}}
        message = {"message_id": message_id or f"cmd-{next(counter)}", "sent_by": actor,
                   "content": content}
        await service.handle(sender, cid, message, dry)
    return send


async def add_task(service, sender, send, message_id="target1", uid="user1"):
    service.platform.messages[message_id] = original(message_id, uid)
    await send(moderation.CREATE, message_id)
    return service.store.pending(CID)[-1]


def test_single_confirm_any_admin_and_replay(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        task = await add_task(service, sender, send)
        assert "识别到食死徒，是否对其使用杀戮咒？" in sender.sent[-1][1]
        assert "原始发言" in sender.sent[-1][1]
        await send(moderation.CONFIRM, task["prompt_id"], actor="admin2", message_id="confirm-id")
        await send(moderation.CONFIRM, task["prompt_id"], actor="admin2", message_id="confirm-id")
        await send(moderation.CONFIRM, task["prompt_id"])
    asyncio.run(scenario())
    assert service.platform.actions == [("recall", "target1"), ("kick", "user1")]
    assert sum(text == moderation.SUCCESS for _, text, _ in sender.sent) == 1


def test_bare_single_confirm(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        await add_task(service, sender, send)
        await send(moderation.CONFIRM)
    asyncio.run(scenario())
    assert service.platform.actions == [("recall", "target1"), ("kick", "user1")]


@pytest.mark.parametrize("quoted", [True, False])
def test_batch_snapshot_excludes_new_and_cancelled_tasks(setup, quoted):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        first = await add_task(service, sender, send, "one", "user1")
        await add_task(service, sender, send, "two", "user2")
        await send(moderation.CONFIRM)
        preview = sender.sent[-1][0]
        assert "共 2 条" in sender.sent[-1][1]
        assert service.platform.actions == []
        await add_task(service, sender, send, "new", "user3")
        await send(moderation.CANCEL, first["prompt_id"])
        await send(moderation.CONFIRM, preview if quoted else None, actor="admin2")
    asyncio.run(scenario())
    assert service.platform.actions == [("recall", "two"), ("kick", "user2")]
    assert [task["message_id"] for task in service.store.pending(CID)] == ["new"]


def test_direct_cancel_all_invalidates_preview(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        await add_task(service, sender, send, "one")
        await add_task(service, sender, send, "two")
        await send(moderation.CONFIRM)
        preview = sender.sent[-1][0]
        await send(moderation.CANCEL)
        assert "已取消 2 条" in sender.sent[-1][1]
        await send(moderation.CONFIRM, preview)
    asyncio.run(scenario())
    assert service.store.pending(CID) == []
    assert service.platform.actions == []


def test_quote_cancel_only_one(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        task = await add_task(service, sender, send, "one")
        await add_task(service, sender, send, "two")
        await send(moderation.CANCEL, task["prompt_id"])
    asyncio.run(scenario())
    assert [task["message_id"] for task in service.store.pending(CID)] == ["two"]


@pytest.mark.parametrize("text", [moderation.CREATE, moderation.CONFIRM, moderation.CANCEL])
def test_non_admin_cannot_create_confirm_or_cancel(setup, text):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        task = await add_task(service, sender, send)
        await send(text, task["prompt_id"] if text != moderation.CREATE else "target1", actor="guest")
    asyncio.run(scenario())
    assert len(service.store.pending(CID)) == 1
    assert service.platform.actions == []
    assert "只有当前群管理员" in sender.sent[-1][1]


@pytest.mark.parametrize("target", ["owner", "bot", "admin2"])
def test_protected_targets_not_created(setup, target):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        service.platform.messages["target1"] = original(uid=target)
        await send(moderation.CREATE, "target1")
    asyncio.run(scenario())
    assert service.store.pending(CID) == []
    assert service.platform.actions == []


def test_duplicate_creation_keeps_one_task(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        await add_task(service, sender, send)
        await send(moderation.CREATE, "target1")
    asyncio.run(scenario())
    assert len(service.store.pending(CID)) == 1
    assert "已有任务" in sender.sent[-1][1]


def test_unrelated_quote_cannot_confirm_all(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        await add_task(service, sender, send)
        await send(moderation.CONFIRM, "unrelated-bot-message")
    asyncio.run(scenario())
    assert service.platform.actions == []
    assert len(service.store.pending(CID)) == 1


def test_batch_same_user_retracts_each_kicks_once(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        await add_task(service, sender, send, "one")
        await add_task(service, sender, send, "two")
        await send(moderation.CONFIRM)
        await send(moderation.CONFIRM)
    asyncio.run(scenario())
    assert service.platform.actions == [("recall", "one"), ("recall", "two"), ("kick", "user1")]


@pytest.mark.parametrize("phase", ["recall", "kick", "target", "actor", "author"])
def test_partial_and_ambiguous_failures_never_claim_success_or_retry(setup, phase):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        task = await add_task(service, sender, send)
        if phase == "recall":
            service.platform.fail_recall.add("target1")
        elif phase == "kick":
            service.platform.fail_kick.add("user1")
        elif phase == "target":
            service.platform.admins.add("user1")
        elif phase == "actor":
            service.platform.after_recall = lambda: service.platform.admins.discard("admin1")
        else:
            service.platform.messages["target1"]["sent_by"] = "other-user"
        await send(moderation.CONFIRM, task["prompt_id"])
        actions = list(service.platform.actions)
        await send(moderation.CONFIRM, task["prompt_id"], actor="admin2")
        assert service.platform.actions == actions
    asyncio.run(scenario())
    assert moderation.SUCCESS not in [text for _, text, _ in sender.sent]
    if phase in ("recall", "target", "actor", "author"):
        assert not any(action == "kick" for action, _ in service.platform.actions)


def test_one_failed_recall_prevents_same_user_kick_but_other_users_continue(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        await add_task(service, sender, send, "one")
        await add_task(service, sender, send, "two")
        await add_task(service, sender, send, "three", "user2")
        service.platform.fail_recall.add("two")
        await send(moderation.CONFIRM)
        await send(moderation.CONFIRM)
    asyncio.run(scenario())
    assert ("kick", "user1") not in service.platform.actions
    assert ("kick", "user2") in service.platform.actions
    assert any("1 条完成，2 条未完成" in text for _, text, _ in sender.sent)


def test_concurrent_confirm_cancel_only_one_wins(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        task = await add_task(service, sender, send)
        await asyncio.gather(send(moderation.CONFIRM, task["prompt_id"]),
                             send(moderation.CANCEL, task["prompt_id"], actor="admin2"))
    asyncio.run(scenario())
    assert len(service.platform.actions) <= 2
    assert not service.store.pending(CID)


def test_atomic_store_claim_across_connections(setup):
    service, sender = setup
    send = commands(service, sender)
    task = asyncio.run(add_task(service, sender, send))
    another = Store(service.store.path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.select(CID, [task["id"]], "admin1"),
                                [service.store, another]))
    assert sum(len(rows) for rows in results) == 1


def test_restart_preserves_pending_preview_and_no_expiry(setup, monkeypatch):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        await add_task(service, sender, send, "one")
        await add_task(service, sender, send, "two")
        await send(moderation.CONFIRM)
        restarted = moderation.Service(Store(service.store.path), service.platform)
        monkeypatch.setattr("runtime.moderation_store.time.time", lambda: 9999999999)
        await commands(restarted, sender)(moderation.CONFIRM, actor="admin2", message_id="after-restart")
    asyncio.run(scenario())
    assert len(service.platform.actions) == 3


def test_restart_never_replays_executing_task(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        task = await add_task(service, sender, send)
        service.store.select(CID, [task["id"]], "admin1")
        service.store.progress(task["id"], "撤回结果未确认")
        restarted = moderation.Service(Store(service.store.path), service.platform)
        await commands(restarted, sender)(moderation.CONFIRM, task["prompt_id"], message_id="after-restart")
    asyncio.run(scenario())
    assert service.platform.actions == []
    assert "撤回结果未确认" in sender.sent[-1][1]


def test_failed_prompt_is_not_confirmable_and_can_be_reissued(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        service.platform.messages["target1"] = original()
        sender.failed = True
        await send(moderation.CREATE, "target1")
        assert service.store.pending(CID) == []
        sender.failed = False
        await send(moderation.CONFIRM)
        assert service.platform.actions == []
        await send(moderation.CREATE, "target1")
        assert len(service.store.pending(CID)) == 1
    asyncio.run(scenario())


def test_multi_page_preview_can_confirm_by_any_page(setup):
    service, sender = setup
    tasks = []
    for number in range(65):
        message = original(f"target-{number}", f"user-{number}", "长消息" * 40)
        task, _ = service.store.create(CID, message, "长昵称" * 20, "admin1")
        service.store.bind("task", task["id"], f"task-prompt-{number}")
        tasks.append(task)
    asyncio.run(service.preview(sender, CID, service.store.pending(CID)))
    assert len(sender.sent) > 1
    batches = [service.store.reference(CID, message_id) for message_id, _, _ in sender.sent]
    assert all(kind == "batch" for kind, _ in batches)
    assert len({batch["id"] for _, batch in batches}) == 1


def test_dry_and_other_rooms_have_no_side_effects(setup):
    service, sender = setup
    send = commands(service, sender)

    async def scenario():
        await send(moderation.CREATE, "target1", dry=True)
        await send(moderation.CREATE, "target1", cid="other-room")
    asyncio.run(scenario())
    assert sender.sent == []
    assert service.store.read("SELECT * FROM commands") == []
    assert service.platform.actions == []


def test_disabled_or_broken_gate_does_not_create_database(monkeypatch):
    """功能关闭/闸门坏：不建库、不执行，指令落回普通斜杠分派（is_command False）。"""
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    features.set_overrides({})
    monkeypatch.setattr(moderation, "_service", None)
    message = {"content": {"text": moderation.CREATE}, "sent_by": "admin1", "message_id": "cmd"}
    assert asyncio.run(moderation.handle(FakeSender(), CID, message)) is False
    assert moderation._service is None
    def broken_gate(*args):
        raise RuntimeError("config error")
    monkeypatch.setattr(moderation, "_feat_on", broken_gate)
    assert asyncio.run(moderation.handle(FakeSender(), CID, message)) is False
    assert moderation._service is None


def test_platform_uses_real_admin_role_and_creator_not_local_owners(monkeypatch):
    import dzmm
    from runtime import identity
    def api(path, payload, **kwargs):
        if path == "chat.listAll":
            return [{"id": CID, "data": {"creatorId": "owner"}}]
        return {"members": [{"id": "owner"}, {"id": "admin", "metadata": {"role": "admin"}},
                            {"id": "guest"}, {"id": identity.NUO_UID, "metadata": {"role": "admin"}}]}
    monkeypatch.setattr(dzmm, "api", api)
    platform = Platform()
    asyncio.run(platform.authorize(CID, "owner"))
    asyncio.run(platform.authorize(CID, "admin", "guest"))
    with pytest.raises(ModerationUnavailable):
        asyncio.run(platform.authorize(CID, "guest"))
    with pytest.raises(ModerationUnavailable):
        asyncio.run(platform.authorize(CID, "admin", "owner"))


@pytest.mark.parametrize("result", [{}, {"members": [], "hasMore": True},
                                  {"members": [{"id": "x", "metadata": {"role": "unknown"}}]}])
def test_unknown_member_schema_fails_closed(monkeypatch, result):
    import dzmm
    monkeypatch.setattr(dzmm, "api", lambda path, *args, **kwargs:
                        [{"id": CID, "data": {"creatorId": "owner"}}] if path == "chat.listAll" else result)
    with pytest.raises(ModerationUnavailable):
        asyncio.run(Platform().authorize(CID, "owner"))


@pytest.mark.parametrize("limit,count,extra,accepted", [
    (None, 1000, {}, False),
    (None, 1125, {}, False),
    (2000, 1125, {}, True),
    (2000, 2000, {}, False),
    (2000, 2001, {}, False),
    (2000, 1125, {"hasMore": True}, False),
    (2000, 1125, {"nextCursor": "next"}, False),
])
def test_member_limit_accepts_large_complete_room_only(monkeypatch, limit, count, extra, accepted):
    import dzmm
    calls = []
    def api(path, payload, **kwargs):
        if path == "chat.listAll":
            return [{"id": CID, "data": {"creatorId": "owner"}}]
        calls.append(payload["json"]["limit"])
        return {"members": [{"id": f"user-{n}"} for n in range(count)], **extra}
    monkeypatch.setattr(dzmm, "api", api)
    monkeypatch.setattr(dzmm, "MEMBER_FETCH_LIMIT", 1000)
    if accepted:
        assert len(asyncio.run(Platform().members(CID, limit=limit))) == count
    else:
        with pytest.raises(ModerationUnavailable, match="完整成员"):
            asyncio.run(Platform().members(CID, limit=limit))
    assert calls == [1000 if limit is None else limit]


def test_old_message_rechecked_near_original_timestamp(monkeypatch):
    import dzmm
    calls = []
    def messages(cid, **kwargs):
        calls.append(kwargs)
        return [original()]
    monkeypatch.setattr(dzmm, "get_messages", messages)
    message = asyncio.run(Platform().message(CID, "target1", "2026-10-01T00:00:00Z"))
    assert message["sent_by"] == "user1"
    cursor = datetime.fromisoformat(calls[0]["before"])
    assert cursor == datetime(2026, 10, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert calls[0]["before"].endswith("Z")


def test_missing_original_message_fails_closed(monkeypatch):
    import dzmm
    monkeypatch.setattr(dzmm, "get_messages", lambda *args, **kwargs: [])
    with pytest.raises(ModerationUnavailable):
        asyncio.run(Platform().message(CID, "forged-id"))


def test_kick_requires_post_action_membership_verification(monkeypatch):
    import dzmm
    calls = []
    monkeypatch.setattr(dzmm, "remove_member", lambda cid, uid: calls.append(uid))
    platform = Platform()
    async def members(cid):
        return {"user1": {}}
    monkeypatch.setattr(platform, "members", members)
    with pytest.raises(ModerationUnavailable):
        asyncio.run(platform.kick(CID, "user1"))
    assert calls == ["user1"]


def test_missing_executor_permission_still_allows_admin_cancel(monkeypatch):
    import dzmm
    from runtime import identity
    def api(path, *args, **kwargs):
        if path == "chat.listAll":
            return [{"id": CID, "data": {"creatorId": "owner"}}]
        return {"members": [{"id": "owner"}, {"id": identity.NUO_UID}, {"id": "user1"}]}
    monkeypatch.setattr(dzmm, "api", api)
    asyncio.run(Platform().authorize(CID, "owner"))
    with pytest.raises(ModerationUnavailable, match="执行账号"):
        asyncio.run(Platform().authorize(CID, "owner", "user1"))


def test_dispatch_enters_moderation_and_preserves_cursor_protocol(setup, monkeypatch):
    from runtime import dispatch
    service, sender = setup
    monkeypatch.setattr(moderation, "_service", service)
    monkeypatch.setattr(moderation, "_feat_on", lambda *args: True)
    service.platform.messages["target1"] = original()
    message = {"message_id": "dispatch-cmd", "sent_by": "admin1", "content": {
        "text": moderation.CREATE, "reference": {"id": "target1"}}}
    saved = []
    inflight = []
    state = {}
    group_state = {}
    def forbidden(*args):
        raise AssertionError("must not enter game or AI dispatch")
    asyncio.run(dispatch.handle_slash(sender, CID, moderation.CREATE, "admin1", False,
                                    group_state, [message], message, state,
                                    _save_group=lambda *args: saved.append(args),
                                    _mark_inflight=lambda *args: inflight.append(args),
                                    _remember_turn=forbidden, rank_summary=forbidden,
                                    sync_profiles=forbidden))
    assert group_state["last_id"] == "dispatch-cmd"
    assert saved and inflight == [(CID, "dispatch-cmd")]
    assert len(service.store.pending(CID)) == 1


def test_mainloop_admin_commands_bypass_owner_and_chat_gates_without_losing_batch(setup, monkeypatch, state, dbdir):
    import bot
    import types
    from runtime import mainloop
    service, sender = setup
    monkeypatch.setattr(moderation, "_service", service)
    monkeypatch.setattr(moderation, "_feat_on", lambda *args: True)
    service.platform.messages["target1"] = original()
    messages = [{"message_id": f"loop-{number}", "sent_by": "admin1", "content": {
        "text": text, **({"reference": {"id": "target1"}} if number == 0 else {})}}
        for number, text in enumerate([moderation.CREATE, moderation.CONFIRM])]
    buffer = list(messages)
    def drain(cid):
        drained = list(buffer)
        buffer.clear()
        return drained
    def requeue(cid, rows):
        for row in rows:
            row["_requeued"] = True
            buffer.append(row)
    subscriber = types.SimpleNamespace(is_connected=False, drain_private=lambda: {},
                                       drain=drain, requeue=requeue,
                                       need_resync=lambda cid: False)
    for name in ("_load_member_names", "_remember_turn", "_mark_inflight", "_clear_inflight",
                 "set_persona_cid", "_set_active_cid"):
        monkeypatch.setattr(bot, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "ROOT", dbdir)
    monkeypatch.setattr(bot, "MUTE_CIDS", set())
    monkeypatch.setattr(bot, "PUSH_CIDS", set())
    monkeypatch.setattr(bot, "learn_bot_uid", lambda uid: False)
    monkeypatch.setattr(bot, "get_name", lambda uid: uid)
    monkeypatch.setattr(bot, "is_self", lambda uid: False)
    monkeypatch.setattr(bot, "_detect_joins", lambda messages: [])
    monkeypatch.setattr(bot, "_dice_state", lambda: {})
    monkeypatch.setattr(bot, "message_lag", lambda messages: 0)
    monkeypatch.setattr(bot._profile_sync, "scan_load", lambda: {})
    monkeypatch.setattr(bot, "_feat_on", lambda cid, key: key == "at_only")
    def forbidden(*args):
        raise AssertionError("moderation must not enter owner refusal")
    monkeypatch.setattr(bot, "_room_cmd_refused", forbidden)
    async def scenario():
        await mainloop.run_main_loop(0, False, True, groups=[CID], sender=sender,
                                     state=state, subscriber=subscriber, is_primary=False, B=bot)
        assert len(service.store.pending(CID)) == 1
        assert [row["message_id"] for row in buffer] == ["loop-1"]
        await mainloop.run_main_loop(0, False, True, groups=[CID], sender=sender,
                                     state=state, subscriber=subscriber, is_primary=False, B=bot)
    asyncio.run(scenario())
    assert service.platform.actions == [("recall", "target1"), ("kick", "user1")]
    assert buffer == []


def test_reference_timestamp_is_only_a_lookup_hint_and_not_target_identity(setup):
    service, sender = setup
    service.platform.messages["target1"] = original(uid="user1")
    message = {"message_id": "timed-quote", "sent_by": "admin1", "content": {
        "text": moderation.CREATE, "reference": {"id": "target1", "sentBy": "admin2",
                                                  "sentAt": "2026-10-01T00:00:00Z"}}}
    asyncio.run(service.handle(sender, CID, message))
    assert service.store.pending(CID)[0]["uid"] == "user1"


def test_cancelled_target_can_be_reissued_but_old_prompt_cannot_confirm_it(setup):
    service, sender = setup
    send = commands(service, sender)
    async def scenario():
        old_task = await add_task(service, sender, send)
        await send(moderation.CANCEL, old_task["prompt_id"])
        new_task = await add_task(service, sender, send)
        assert new_task["id"] != old_task["id"]
        await send(moderation.CONFIRM, old_task["prompt_id"])
        assert service.platform.actions == []
        assert len(service.store.pending(CID)) == 1
        await send(moderation.CONFIRM, new_task["prompt_id"])
    asyncio.run(scenario())
    assert service.platform.actions == [("recall", "target1"), ("kick", "user1")]
