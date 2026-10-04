"""解梦的真实入口、短回复、作用域和后台任务生命周期回归。"""
import ast
import asyncio
import inspect
import json
import threading
from types import SimpleNamespace

import pytest

import brains
import personas
from runtime import at_chat, dispatch, dream, features, mainloop, prompt_tiers, tuning
from runtime.dream_sources import candidates
from runtime.fortune import is_fortune_cmd
from runtime.routing import RoutingSender
from runtime.sender import ACCOUNT_MSG_MAX_CHARS, _split_for_account
from runtime.transport_bot import BotApiAmbiguous

READING = "这个梦也许在呈现想向前、又有些不安的感觉。梦里的结尾可以作为理解这份情绪的线索，但不代表现实一定会发生什么。"
ADVICE = "记下醒来时最强烈的感受，给自己一点休息。"


def _card(source_id="", reading=READING, advice=ADVICE):
    return json.dumps({"reading": reading, "advice": advice, "source_id": source_id},
                      ensure_ascii=False)


def _forbidden(*args, **kwargs):
    raise AssertionError("must not enter general chat, memory or retry")


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, text, cid, **kwargs):
        self.sent.append((cid, text))
        return True


@pytest.fixture(autouse=True)
def clean(monkeypatch, dbdir):
    saved_features = features.overrides()
    saved_tuning = tuning.overrides()
    saved_personas = personas.config_personas()
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    monkeypatch.delenv("DZMM_TUNING", raising=False)
    features.set_overrides({dream.PRIMARY_CID + "|dream": True, dream.TEA_CID + "|dream": True})
    tuning.set_overrides({dream.TEA_CID + "|dream.hash_cmd": 1})
    personas.set_config_personas({})
    monkeypatch.setattr(dream, "_LAST", {})
    monkeypatch.setattr(dream, "_ACTIVE", set())
    monkeypatch.setattr(dream, "_LIMIT", threading.BoundedSemaphore(2))
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: (_card(), "stop"))
    monkeypatch.setattr(dream, "log", lambda *args: None)
    monkeypatch.setattr(dispatch, "log", lambda *args: None)
    monkeypatch.setattr(at_chat, "log", lambda *args: None)
    monkeypatch.setattr(dispatch, "generate_reply", _forbidden)
    monkeypatch.setattr(at_chat, "generate_reply", _forbidden)
    monkeypatch.setattr(at_chat, "_extract_remember", _forbidden)
    monkeypatch.setattr(at_chat, "_retrieve_memory", _forbidden)
    monkeypatch.setattr(at_chat, "_active_play_hint", lambda cid: None)
    monkeypatch.setattr(at_chat, "_strip_at", lambda text, cid: text.removeprefix("@小小糯 "))
    for name in ("_get_perf_state", "_get_fasao_state", "_get_dj_state", "_get_story_state", "_get_vote_state", "_dice_state"):
        monkeypatch.setattr(dispatch, name, lambda *args: {})
    yield
    features.set_overrides(saved_features)
    tuning.set_overrides(saved_tuning)
    personas.set_config_personas(saved_personas)


@pytest.mark.parametrize("text,payload", [
    ("/解梦", ""), ("#解梦", ""), ("/解梦：梦见下雨", "梦见下雨"),
    ("#解梦 梦见飞起来\n最后很开心", "梦见飞起来\n最后很开心"),
    ("/解梦游戏", None), ("#解梦游戏", None), ("请用 #解梦", None),
    ("/解梦123", None), ("/解梦/帮助", None),
])
def test_exact_commands(text, payload):
    assert dream.command_payload(text) == payload


@pytest.mark.parametrize("text,hit", [
    ("解梦 梦见下雨", True), ("帮我解梦：昨晚一直跑", True),
    ("请帮我解梦一下：梦见下雨", True), ("帮我解一下梦：梦见猫", True),
    ("梦见掉牙了，这个梦是什么意思？", True), ("请分析一下这个梦：梦见猫", True),
    ("梦见你啦", False), ("听说解梦很准", False), ("不要帮我解梦", False),
    ("/解梦游戏", False), ("#解梦游戏", False), ("解梦游戏", False),
])
def test_explicit_natural_intent(text, hit):
    assert (dream.request_payload(text) is not None) is hit


def test_default_is_off_and_hard_scope_survives_global_force(monkeypatch):
    features.set_overrides({})
    assert not dream.enabled(dream.PRIMARY_CID)
    assert not dream.enabled(dream.TEA_CID)
    monkeypatch.setenv("DZMM_FEATURES", "off")
    assert dream.enabled(dream.PRIMARY_CID)
    assert not dream.enabled("some-other-group")
    personas.set_config_personas({dream.TEA_CID: "ruojin"})
    assert not dream.enabled(dream.TEA_CID)


def test_hash_only_tea_and_opt_in():
    assert dream.is_hash_request(dream.TEA_CID, "#解梦 梦见下雨")
    assert not dream.is_hash_request(dream.PRIMARY_CID, "#解梦 梦见下雨")
    assert not dream.is_hash_request(dream.TEA_CID, "#解梦游戏")
    tuning.set_overrides({})
    assert not dream.is_hash_request(dream.TEA_CID, "#解梦 梦见下雨")


def test_guidance_and_input_limit_never_call_model(monkeypatch):
    monkeypatch.setattr(brains, "_call_llm_raw", _forbidden)
    assert "#解梦" in asyncio.run(dream.dream_reply(dream.TEA_CID, "guest", "#解梦"))
    assert "/解梦" in asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦"))
    assert "/解梦" in asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "请帮我解梦一下", via_at=True))
    assert "2000" in asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 " + "梦" * 2001))
    tuning.set_overrides({"dream.input_limit": 100})
    assert "100" in asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 " + "梦" * 101))
    assert not dream._LAST


def test_request_passes_only_dream_and_candidates_to_one_raw_call(monkeypatch):
    calls = []

    def model(system, user, **kwargs):
        calls.append((system, json.loads(user), kwargs))
        return _card(), "stop"

    monkeypatch.setattr(brains, "_call_llm_raw", model)
    reply = asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 梦见在办公室跑步"))
    assert dream.FOOTER in reply and len(calls) == 1
    system, data, kwargs = calls[0]
    assert system == brains.DREAM_PROMPT
    assert set(data) == {"dream", "max_chars", "candidates"}
    assert data["dream"] == "梦见在办公室跑步" and data["candidates"] == []
    assert kwargs == {"max_tokens": 650, "timeout": 20}


def test_cooldown_is_per_group_and_user(monkeypatch):
    calls = []
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: (calls.append(1) or _card(), "stop"))
    first = asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 梦见奔跑"))
    again = asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 梦见奔跑"))
    other = asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "another", "/解梦 梦见奔跑"))
    tea = asyncio.run(dream.dream_reply(dream.TEA_CID, "guest", "#解梦 梦见奔跑"))
    assert "等一小会儿" in again
    assert first == other == tea and len(calls) == 3
    monkeypatch.setattr(dream.time, "monotonic", lambda: 10 ** 12)
    assert asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 梦见奔跑")) == first
    assert len(calls) == 4


def test_natural_can_be_disabled_without_disabling_explicit_at():
    tuning.set_overrides({"dream.natural": 0})
    assert asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "帮我解梦：梦见下雨", via_at=True)) is None
    assert asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 梦见下雨", via_at=True))


@pytest.mark.parametrize("raw,finish", [
    ("不是 JSON", "stop"), ("[]", "stop"),
    (_card(reading=""), "stop"), (_card(source_id="invented"), "stop"),
    (_card(source_id="sky_fly"), "stop"), (_card(), "length"),
    (_card(reading="这个梦预示你一定发财。"), "stop"),
    (_card(reading="《周公解梦》说这代表好运。"), "stop"),
])
def test_invalid_and_unsafe_outputs_have_one_safe_failure(monkeypatch, raw, finish):
    calls = []
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: (calls.append(1) or raw, finish))
    reply = asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 梦见追逐"))
    assert reply == dream.FAILURE and len(calls) == 1


def test_model_exception_logs_only_type_and_releases_capacity(monkeypatch):
    logs = []

    def broken(*args, **kwargs):
        raise RuntimeError("credential-and-dream-must-not-be-logged")

    monkeypatch.setattr(brains, "_call_llm_raw", broken)
    monkeypatch.setattr(dream, "log", logs.append)
    assert asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 梦见追逐")) == dream.FAILURE
    assert "RuntimeError" in logs[0] and "credential" not in logs[0]
    assert dream._LIMIT.acquire(blocking=False)
    assert dream._LIMIT.acquire(blocking=False)
    dream._LIMIT.release()
    dream._LIMIT.release()


@pytest.mark.parametrize("text,source", [
    ("梦见我飞到天上了", "sky_fly"), ("夢見我飛到天上了", "sky_fly"),
    ("梦见飞机飞到天上", None), ("梦见飞到天上又摔下来", None),
    ("梦见雨停，天晴了", "rain_clear"), ("梦见雨不停，天不晴", None),
    ("梦见枯树开花", "dry_tree_flower"), ("梦见枯树旁边有花", None),
    ("梦见乌云散了", None), ("梦见云散，太阳出来了", "cloud_sun"),
])
def test_sources_require_action_and_do_not_match_negation(text, source):
    hits = candidates(text)
    assert ([item["id"] for item in hits] == [source]) if source else not hits


@pytest.mark.parametrize("cid,budget", [(dream.PRIMARY_CID, 230), (dream.TEA_CID, 180)])
def test_quote_inserted_by_code_and_whole_reply_is_short(monkeypatch, cid, budget):
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: (
        _card(source_id="sky_fly", reading="可能是在体验自由与向前的感觉。" * 35, advice="好好休息。" * 30), "stop"))
    reply = asyncio.run(dream.dream_reply(cid, "guest", "解梦 梦见飞到天上", via_at=True))
    assert "飛上天富貴大吉" in reply and "天地日月星辰" in reply
    assert reply.endswith(dream.FOOTER) and len(reply) <= budget
    if cid == dream.TEA_CID:
        assert len("🤖 " + reply) <= ACCOUNT_MSG_MAX_CHARS
        assert len(_split_for_account("🤖 " + reply)) == 1


def test_small_budget_keeps_footer_and_omits_quote(monkeypatch):
    tuning.set_overrides({"dream.max_chars": 100})
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: (_card(source_id="sky_fly"), "stop"))
    reply = asyncio.run(dream.dream_reply(dream.PRIMARY_CID, "guest", "/解梦 梦见飞到天上"))
    assert len(reply) <= 100 and reply.endswith(dream.FOOTER) and "古籍趣读" not in reply


def test_prompt_contract_and_selftest():
    assert prompt_tiers.check_contract("DREAM_PROMPT", _card()) == []
    assert prompt_tiers.check_contract("DREAM_PROMPT", _card(source_id="fake"))
    assert prompt_tiers.check_contract("DREAM_PROMPT", '{"reading":"梦","advice":3,"source_id":""}')
    assert prompt_tiers.run_selftest("DREAM_PROMPT", llm=lambda *args, **kwargs: _card())["ok"]


async def _slash(sender, cid, text):
    group_state, state = {"last_id": ""}, {}
    messages = [{"message_id": "dream-message", "sent_by": "guest", "content": {"text": text}}]
    await dispatch.handle_slash(
        sender, cid, text, "guest", False, group_state, messages, messages[0], state,
        _save_group=lambda *args: None, _mark_inflight=lambda *args: None,
        _remember_turn=lambda *args: None, rank_summary=_forbidden, sync_profiles=_forbidden)
    return group_state


async def _at(sender, cid, text):
    group_state, state = {"last_id": ""}, {}
    messages = [{"message_id": "at-dream-message", "sent_by": "guest", "content": {"text": text}}]
    await at_chat.handle_at_chat(
        sender, cid, text, "guest", False, group_state, state, messages, messages[0], False,
        _save_group=lambda *args: None, _mark_inflight=lambda *args: None,
        _remember_turn=lambda *args: None, _recent_turns=_forbidden, _seed_turns=_forbidden)
    return group_state


@pytest.mark.parametrize("cid,text,via_at", [
    (dream.PRIMARY_CID, "/解梦 梦见下雨", False),
    (dream.TEA_CID, "#解梦 梦见下雨", False),
    (dream.PRIMARY_CID, "@小小糯 解梦 梦见下雨，请记住这件事", True),
    (dream.TEA_CID, "@小小糯 帮我解梦：梦见跑步", True),
])
def test_actual_dispatch_and_at_entry(cid, text, via_at):
    sender = FakeSender()
    state = asyncio.run(_at(sender, cid, text) if via_at else _slash(sender, cid, text))
    assert state["last_id"] and len(sender.sent) == 1
    target, reply = sender.sent[0]
    assert target == cid and reply.startswith("🤖 糯糯看梦～")
    assert reply.endswith(dream.FOOTER)


@pytest.mark.parametrize("cid,text,via_at", [
    (dream.PRIMARY_CID, "/解梦 梦见下雨", False),
    (dream.TEA_CID, "#解梦 梦见下雨", False),
    (dream.TEA_CID, "@小小糯 帮我解梦：梦见跑步", True),
    ("other", "/解梦 梦见下雨", False),
    ("other", "@小小糯 帮我解梦：梦见跑步", True),
])
def test_disabled_or_outside_requests_are_consumed_without_fallback(monkeypatch, cid, text, via_at):
    features.set_overrides({"dream": cid == "other"})
    monkeypatch.setattr(brains, "_call_llm_raw", _forbidden)
    sender = FakeSender()
    state = asyncio.run(_at(sender, cid, text) if via_at else _slash(sender, cid, text))
    assert state["last_id"] and not sender.sent


def test_wrong_bare_command_in_each_group_is_silent():
    sender = FakeSender()
    asyncio.run(_slash(sender, dream.TEA_CID, "/解梦 梦见下雨"))
    asyncio.run(_slash(sender, dream.PRIMARY_CID, "#解梦 梦见下雨"))
    assert not sender.sent


def test_help_only_advertises_dream_when_enabled_in_scope():
    sender = FakeSender()
    asyncio.run(_slash(sender, dream.PRIMARY_CID, "/帮助"))
    assert "/解梦" in sender.sent[-1][1]
    tuning.set_overrides({})
    asyncio.run(_slash(sender, dream.TEA_CID, "/帮助"))
    assert "#解梦" not in sender.sent[-1][1] and "解梦 梦境" in sender.sent[-1][1]
    features.set_overrides({})
    asyncio.run(_slash(sender, dream.PRIMARY_CID, "/帮助"))
    assert "解梦" not in sender.sent[-1][1]


def test_at_noise_never_enters_dream_or_memory(monkeypatch):
    monkeypatch.setattr(at_chat, "_recap_is_noise", lambda *args: True)
    monkeypatch.setattr(brains, "_call_llm_raw", _forbidden)
    sender = FakeSender()
    asyncio.run(_at(sender, dream.TEA_CID, "@小小糯 解梦 梦见记忆考核"))
    assert not sender.sent


def test_active_play_is_protected(monkeypatch):
    monkeypatch.setattr(at_chat, "_active_play_hint", lambda cid: "剧情进行中")
    monkeypatch.setattr(dispatch, "_get_story_state", lambda cid: {"active": True})
    monkeypatch.setattr(brains, "_call_llm_raw", _forbidden)
    sender = FakeSender()
    asyncio.run(_slash(sender, dream.PRIMARY_CID, "/解梦 梦见下雨"))
    asyncio.run(_at(sender, dream.TEA_CID, "@小小糯 解梦 梦见下雨"))
    assert len(sender.sent) == 2 and all("剧情进行中" in text for cid, text in sender.sent)


def _scan(cid, texts):
    tree = ast.parse(inspect.getsource(mainloop))
    selection = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
                     and node.iter.id == "pending" and isinstance(node.target, ast.Name)
                     and node.target.id == "m"
                     and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                             and child.func.attr == "is_hash_request" for child in ast.walk(node)))
    bridge = SimpleNamespace(
        is_self=lambda uid: uid == "self", _feat_on=features.on, _tune=tuning.get,
        is_fortune_cmd=is_fortune_cmd, is_at_me=lambda text, cid: text.startswith("@小小糯"),
        _quote_reply_on=lambda cid: False, _quotes_self=lambda message: False,
        _is_other_bot_noise=lambda cid, text: "记忆考核" in text,
        _chat_cooling=lambda cid, uid: 0, _is_room_trigger=lambda *args: False)
    env = {"B": bridge, "_dream": dream, "cid": cid, "log": lambda *args: None,
           "pending": [{"message_id": str(index), "sent_by": "guest", "content": {"text": text}}
                       for index, text in enumerate(texts)],
           "cmd": "", "cmd_sender": "", "at_msg": "", "at_sender": "",
           "_cmd_msg": None, "_at_msg_obj": None, "_room_obj": None,
           "_via_quote": False, "room_hit": None, "_leftover": []}
    exec(compile(ast.Module(body=[selection], type_ignores=[]), "<actual-mainloop-selection>", "exec"), env)
    return env


def test_real_selection_whitelists_hash_before_at_only_and_preserves_batch():
    features.set_overrides({dream.TEA_CID + "|dream": True, dream.TEA_CID + "|at_only": True,
                            dream.TEA_CID + "|slash_cmds": False})
    result = _scan(dream.TEA_CID, ["#解梦 梦见下雨", "#解梦 梦见跑步", "/解梦 梦见猫", "#帮助", "#解梦游戏"])
    assert result["cmd"] == "#解梦 梦见下雨"
    assert [message["content"]["text"] for message in result["_leftover"]] == ["#解梦 梦见跑步"]
    assert not _scan(dream.TEA_CID, ["#解梦 梦见记忆考核"])["cmd"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(features, "on", lambda cid, key: True)
        assert not _scan("other-group", ["#解梦 梦见下雨"])["cmd"]
    tuning.set_overrides({})
    assert not _scan(dream.TEA_CID, ["#解梦 梦见下雨"])["cmd"]


def test_hash_selection_keeps_fortune_working():
    features.set_overrides({dream.TEA_CID + "|dream": True, dream.TEA_CID + "|at_only": True})
    tuning.set_overrides({dream.TEA_CID + "|dream.hash_cmd": 1, dream.TEA_CID + "|fortune.hash_cmd": 1})
    result = _scan(dream.TEA_CID, ["#运势", "#解梦 梦见下雨"])
    assert result["cmd"] == "#运势" and len(result["_leftover"]) == 1


def test_sender_preserves_official_and_account_routes():
    account, official = FakeSender(), []

    def send_text(cid, text):
        official.append((cid, text))
        return "official-message"

    sender = RoutingSender(account, {dream.PRIMARY_CID: SimpleNamespace(send_text=send_text)})
    asyncio.run(_slash(sender, dream.PRIMARY_CID, "/解梦 梦见跑步"))
    asyncio.run(_at(sender, dream.TEA_CID, "@小小糯 解梦 梦见跑步"))
    assert len(official) == len(account.sent) == 1
    assert official[0][0] == dream.PRIMARY_CID and account.sent[0][0] == dream.TEA_CID


def test_ambiguous_bot_send_does_not_add_dream_retry_or_account_fallback():
    account, calls = FakeSender(), []

    def ambiguous(cid, text):
        calls.append((cid, text))
        raise BotApiAmbiguous("uncertain")

    sender = RoutingSender(account, {dream.PRIMARY_CID: SimpleNamespace(send_text=ambiguous)})
    asyncio.run(_slash(sender, dream.PRIMARY_CID, "/解梦 梦见跑步"))
    assert len(calls) == 1 and not account.sent


@pytest.mark.parametrize("cancel", [False, True])
def test_timed_out_or_cancelled_workers_keep_capacity_until_thread_exits(monkeypatch, cancel):
    release, started = threading.Event(), threading.Event()
    calls = []

    def slow_model(*args, **kwargs):
        calls.append(1)
        if len(calls) >= 2:
            started.set()
        if not release.wait(5):
            raise RuntimeError("test worker timed out")
        return _card(), "stop"

    monkeypatch.setattr(brains, "_call_llm_raw", slow_model)
    tuning.set_overrides({"dream.timeout": 1})

    async def scenario():
        tasks = [asyncio.create_task(dream.dream_reply(dream.PRIMARY_CID, uid, "/解梦 梦见跑步"))
                 for uid in ("first", "second")]
        try:
            assert await asyncio.to_thread(started.wait, 2)
            if cancel:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                replies = await asyncio.gather(*tasks)
                assert all("慢" in reply for reply in replies)
            assert "别的梦" in await dream.dream_reply(dream.TEA_CID, "third", "解梦 梦见猫", via_at=True)
            assert len(calls) == 2
            dream._LAST.clear()
            assert "等一小会儿" in await dream.dream_reply(dream.PRIMARY_CID, "first", "/解梦 梦见猫")
            assert len(calls) == 2
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
    assert not dream._ACTIVE
    assert dream._LIMIT.acquire(blocking=False)
    assert dream._LIMIT.acquire(blocking=False)
    dream._LIMIT.release()
    dream._LIMIT.release()
