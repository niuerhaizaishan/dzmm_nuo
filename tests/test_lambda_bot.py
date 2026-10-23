"""两群人设、瑞雯专属控制/问候，以及 ROSE 隔离的行为回归。"""
import asyncio
import copy
from datetime import datetime, timezone

import pytest

import personas as P
from personas.lambda_persona import LAMBDA_EXTRA, MR_JU_CID, RUIWEN_CID
from personas.registry import ROSE_CID, VV_UID
from runtime import features, lambda_bot as L
from runtime.outbox import EXPIRED, FAILED, OutboxSender, OutboxStore


class Sender:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok
        self.last_failure = "rejected"

    async def send(self, text, cid, **kwargs):
        self.sent.append((cid, text))
        return self.ok


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    saved_map = P.config_personas()
    saved_texts = P.content_overrides()
    saved_features = features.overrides()
    saved_cid = P.current_cid()
    P.set_config_personas({})
    P.set_content_overrides({})
    features.set_overrides({})
    storage = {}
    monkeypatch.setattr(L.state_store, "group_get", lambda cid, key, default=None:
                        copy.deepcopy(storage.get(cid, {}).get(key, default)))
    monkeypatch.setattr(L.state_store, "top_get", lambda key, default=None:
                        copy.deepcopy(storage.get(key, default)))
    monkeypatch.setattr(L.state_store, "top_set", lambda key, value:
                        storage.__setitem__(key, copy.deepcopy(value)))
    monkeypatch.setattr(L, "log", lambda *args: None)
    monkeypatch.setattr(L, "_gender_of", lambda *args: "未知")
    yield storage
    P.set_config_personas(saved_map)
    P.set_content_overrides(saved_texts)
    features.set_overrides(saved_features)
    P.set_cid(saved_cid)


def message(text, uid="guest", mid="m1", **content):
    return {"sent_by": uid, "message_id": mid, "content": {"text": text, **content}}


def process(storage, messages, sender=None, cid=RUIWEN_CID, dry=False):
    sender = sender or Sender()
    gs = copy.deepcopy(storage.get(cid, {}))

    def save(c, state):
        storage[c] = copy.deepcopy(state)

    keep = asyncio.run(L.filter_pending(sender, cid, messages, gs, dry=dry,
                                       save_group=save, is_self=lambda uid: uid == "bot"))
    return keep, sender


def test_persona_scope_and_rose_unchanged():
    for cid in (RUIWEN_CID, MR_JU_CID):
        assert P.persona_key_for(cid) == "lambda"
        assert P.me(cid) == "Λʌ"
        assert P.persona_for(cid)["extra"] == LAMBDA_EXTRA
        assert "Λʌ" in P.all_names(cid)
        assert "小秘书" not in P.p("intro_text", cid)
        assert not P.p("intro_photo", cid)
        assert cid not in P.ROOMS
    assert P.persona_key_for(ROSE_CID) == "rose_butler"
    assert P.me(ROSE_CID) == "薇丝"
    assert "摘取玫瑰花" in P.persona_for(ROSE_CID)["extra"]
    assert P.persona_key_for("unrelated") == "nuo"
    assert P.health("lambda") == []


def test_group_backgrounds_are_separate():
    tavern = P.apply_persona("你是小小糯～。", RUIWEN_CID)
    orange = P.apply_persona("你是小小糯～。", MR_JU_CID)
    assert tavern.endswith("这里是瑞雯的酒馆；Vv 专属合体/分离由程序处理。")
    assert orange.endswith("这里是 mr.橘的群。没有酒馆营业、Vv 合体/分离设定，不提瑞雯群的关系或记忆。")
    assert "未知" in tavern and "不能把「未知」当成女性" in tavern


def test_owner_merge_and_split_persist_and_deduplicate(isolated):
    keep, sender = process(isolated, [message("@Λʌ 回到我身上！", VV_UID)])
    assert not keep and L.paused(RUIWEN_CID)
    assert sender.sent == [(RUIWEN_CID, "🌹 " + L.MERGE_TEXT)]
    process(isolated, [message("@Λʌ 回到我身上！", VV_UID)], sender)
    assert len(sender.sent) == 1
    process(isolated, [message("@Λʌ 分离！", VV_UID, "m2")], sender)
    assert not L.paused(RUIWEN_CID)
    assert sender.sent[-1] == (RUIWEN_CID, "🌹 " + L.SPLIT_TEXT)


@pytest.mark.parametrize("gender,expected", [("男生", L.MALE_TEXT), ("女生", L.FEMALE_TEXT),
                                           ("未知", L.UNKNOWN_TEXT)])
def test_non_owner_has_no_control(isolated, monkeypatch, gender, expected):
    monkeypatch.setattr(L, "_gender_of", lambda *args: gender)
    _, sender = process(isolated, [message("@Λʌ 回到我身上！", "fake-vv")])
    assert not L.paused(RUIWEN_CID)
    assert sender.sent == [(RUIWEN_CID, "🌹 " + expected)]
    _, sender = process(isolated, [message("@Λʌ 分离！", "fake-vv", "m2")])
    assert sender.sent == [(RUIWEN_CID, "🌹 " + L.UNKNOWN_TEXT)]


@pytest.mark.parametrize("cid", [ROSE_CID, MR_JU_CID, "elsewhere"])
def test_controls_do_nothing_in_other_groups(isolated, cid):
    msgs = [message("@Λʌ 回到我身上！", VV_UID)]
    keep, sender = process(isolated, msgs, cid=cid)
    assert keep == msgs and not sender.sent and isolated == {}


@pytest.mark.parametrize("text", ["回到我身上！", "引用：@Λʌ 回到我身上！",
                                  "> @Λʌ 回到我身上！", "@别人 回到我身上！",
                                  "@Λʌ小号 回到我身上！", "@Λʌ 回到我身上！还有别的"])
def test_control_requires_exact_direct_command(isolated, text):
    msgs = [message(text, VV_UID)]
    keep, sender = process(isolated, msgs)
    assert keep == msgs and not sender.sent and not L.paused(RUIWEN_CID)


def test_paused_consumes_commands_quotes_and_plain_chat(isolated):
    process(isolated, [message("@Λʌ 回到我身上！", VV_UID)])
    msgs = [message("/查 Vv", mid="m2"), message("普通聊天", mid="m3"),
            message("看看原消息", mid="m4", reference={"content": {"text": "@Λʌ 分离！"}}),
            message("嘿，@Λʌ 在吗", mid="m5"), message("@Λʌ 在吗", mid="m6")]
    keep, sender = process(isolated, msgs)
    assert not keep
    assert sender.sent == [(RUIWEN_CID, "🌹 " + L.PAUSED_TEXT)]
    assert L.paused(RUIWEN_CID)


def test_batch_order_drops_pre_pause_chat_and_only_resumes_later_messages(isolated):
    after = message("@Λʌ 你好", mid="m5")
    keep, sender = process(isolated, [message("@Λʌ 先聊聊", mid="m0"),
                                   message("@Λʌ 回到我身上！", VV_UID, "m1"),
                                   message("/查 Vv", mid="m2"),
                                   message("@Λʌ 分离！", VV_UID, "m3"), after])
    assert keep == [after]
    assert len(sender.sent) == 2


def test_recovery_survives_feature_disable_and_dry_is_read_only(isolated):
    process(isolated, [message("@Λʌ 回到我身上！", VV_UID)], dry=True)
    assert isolated == {}
    process(isolated, [message("@Λʌ 回到我身上！", VV_UID)])
    features.set_overrides({RUIWEN_CID + "|lambda_control": False})
    process(isolated, [message("@Λʌ 分离！", VV_UID, "m2")])
    assert not L.paused(RUIWEN_CID)


def greet(sender, hour, *, day=21, groups=(RUIWEN_CID,), minute=0, mute=(), dry=False):
    asyncio.run(L.scheduled_greetings(sender, dry, groups=groups, mute_cids=mute,
                                     now=datetime(2026, 10, day, hour, minute, tzinfo=L.BJT)))


def test_greetings_exact_times_text_timezone_and_daily_dedupe(isolated):
    sender = Sender()
    for h in (7, 12, 22, 23):
        greet(sender, h)
    greet(sender, 8)
    greet(sender, 8, minute=1)
    greet(sender, 0)
    greet(sender, 0, minute=2)
    assert sender.sent == [(RUIWEN_CID, "🌹 " + L.MORNING_TEXT),
                           (RUIWEN_CID, "🌹 " + L.EVENING_TEXT)]
    # UTC 00:00 是北京时间 08:00；次日应再发一次。
    asyncio.run(L.scheduled_greetings(sender, False, groups=[RUIWEN_CID],
                                     now=datetime(2026, 10, 22, tzinfo=timezone.utc)))
    assert len(sender.sent) == 3


def test_greetings_scope_disabled_paused_and_no_late_replay(isolated):
    sender = Sender()
    greet(sender, 8, groups=(ROSE_CID, MR_JU_CID))
    greet(sender, 8, mute=(RUIWEN_CID,))
    greet(sender, 8, dry=True)
    features.set_overrides({RUIWEN_CID + "|lambda_greetings": False})
    greet(sender, 8)
    assert sender.sent == [] and isolated == {}
    features.set_overrides({})
    process(isolated, [message("@Λʌ 回到我身上！", VV_UID)])
    greet(sender, 8)
    process(isolated, [message("@Λʌ 分离！", VV_UID, "m2")])
    greet(sender, 8, minute=1)
    greet(sender, 0, minute=6)
    assert not sender.sent


def test_failed_greeting_is_not_automatically_duplicated(isolated):
    sender = Sender(ok=False)
    greet(sender, 8)
    greet(sender, 8, minute=1)
    assert len(sender.sent) == 1


def test_outbox_blocks_paused_business_but_allows_control_and_other_groups(isolated, dbdir):
    store = OutboxStore(dbdir / "lambda.db")
    sender = Sender()
    queued = OutboxSender(sender, store)
    try:
        process(isolated, [message("@Λʌ 回到我身上！", VV_UID)], queued)
        assert asyncio.run(queued.send("🤖 不应出现", RUIWEN_CID)) is False
        assert asyncio.run(queued.send("🤖 mr.橘正常", MR_JU_CID)) is True
        assert asyncio.run(queued.send("🤖 ROSE正常", ROSE_CID)) is True
        assert sender.sent == [(RUIWEN_CID, "🌹 " + L.MERGE_TEXT),
                               (MR_JU_CID, "🌹 mr.橘正常"), (ROSE_CID, "🤖 ROSE正常")]
        assert asyncio.run(queued.send_with_id("管理确认", RUIWEN_CID)) == ""
    finally:
        store.close()


def test_old_rejected_messages_never_replay_after_resume(isolated, dbdir):
    store = OutboxStore(dbdir / "lambda-retry.db")
    sender = Sender()
    queued = OutboxSender(sender, store)
    try:
        old = store.enqueue(chatroom_id=RUIWEN_CID, text="旧回复")
        store.mark_failed(old, "sender 返回 False [rejected]")
        other = store.enqueue(chatroom_id=MR_JU_CID, text="mr.橘的回复")
        store.mark_failed(other, "sender 返回 False [rejected]")
        process(isolated, [message("@Λʌ 回到我身上！", VV_UID)], queued)
        assert store.get(old)["status"] == EXPIRED
        assert store.get(other)["status"] == FAILED
        process(isolated, [message("@Λʌ 分离！", VV_UID, "m2")], queued)
        assert asyncio.run(queued.retry_once()) == 1
        assert (MR_JU_CID, "mr.橘的回复") in sender.sent
        assert not any(text == "旧回复" for cid, text in sender.sent)
    finally:
        store.close()


def test_waiting_message_is_cancelled_without_blocking_future_messages(isolated, dbdir):
    store = OutboxStore(dbdir / "lambda-wait.db")
    inner = Sender()
    queued = OutboxSender(inner, store)

    def save(cid, gs):
        isolated[cid] = copy.deepcopy(gs)

    async def run():
        lock = queued._lock(RUIWEN_CID)
        await lock.acquire()
        old = asyncio.create_task(queued.send("排队旧消息", RUIWEN_CID))
        await asyncio.sleep(0)
        merge = asyncio.create_task(L.filter_pending(
            queued, RUIWEN_CID, [message("@Λʌ 回到我身上！", VV_UID)], {},
            dry=False, save_group=save, is_self=lambda uid: False))
        await asyncio.sleep(0)
        assert L.paused(RUIWEN_CID)
        lock.release()
        assert await old is False
        await merge
        await L.filter_pending(queued, RUIWEN_CID, [message("@Λʌ 分离！", VV_UID, "m2")],
                               copy.deepcopy(isolated[RUIWEN_CID]), dry=False,
                               save_group=save, is_self=lambda uid: False)
        assert await queued.send("恢复后的新消息", RUIWEN_CID)

    try:
        asyncio.run(run())
        assert [text for cid, text in inner.sent] == ["🌹 " + L.MERGE_TEXT,
                                                     "🌹 " + L.SPLIT_TEXT, "🌹 恢复后的新消息"]
        assert len(store.by_status(EXPIRED)) == 1
    finally:
        store.close()


def test_state_save_failure_never_claims_control_success(isolated):
    sender = Sender()
    with pytest.raises(RuntimeError, match="状态未持久保存"):
        asyncio.run(L.filter_pending(sender, RUIWEN_CID,
                                     [message("@Λʌ 回到我身上！", VV_UID)], {},
                                     dry=False, save_group=lambda *args: None,
                                     is_self=lambda uid: False))
    assert not sender.sent and not L.paused(RUIWEN_CID)


@pytest.mark.parametrize("gender,address", [("男生", "弟弟"), ("女生", "妹妹"),
                                           ("未知", "不叫弟弟或妹妹")])
def test_real_chat_entry_carries_current_gender_outside_truncated_name(isolated, monkeypatch, gender, address):
    from runtime import at_chat as chat
    captured = []
    monkeypatch.setattr(chat, "_active_play_hint", lambda cid: None)
    monkeypatch.setattr(chat, "_feat_on", lambda cid, key: key == "chat")
    monkeypatch.setattr(chat, "_gender_of", lambda *args: gender)
    monkeypatch.setattr(chat, "_retrieve_memory", lambda *args: [])
    monkeypatch.setattr(chat, "get_name", lambda uid: "一个超过八个字的超长昵称")
    monkeypatch.setattr(chat, "_recap_is_noise", lambda *args: False)
    monkeypatch.setattr(chat, "log", lambda *args: None)

    def generate(ctx, **kwargs):
        captured.extend(ctx)
        return "在呢。"

    monkeypatch.setattr(chat, "generate_reply", generate)
    sender = Sender()
    msg = message("@Λʌ 今天有点难过")
    asyncio.run(chat.handle_at_chat(
        sender, RUIWEN_CID, msg["content"]["text"], "guest", False, {}, {}, [msg], msg, False,
        _save_group=lambda *args: None, _mark_inflight=lambda *args: None,
        _remember_turn=lambda *args: None, _seed_turns=lambda *args: None,
        _recent_turns=lambda *args, **kwargs: [{"sender": "旧称呼", "text": "妹妹，昨天聊过。"}]))
    frame = captured[-1]["text"]
    assert "【当前对话者】性别：" + gender in frame
    assert address in frame
    assert sender.sent[-1][1] == "🤖 在呢。"  # 交给共用出站层替换 🌹，不强行补叹号。
