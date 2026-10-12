"""本人画像的维度格式、取证隔离、引用校验和真实入口回归。

2026-10-07：成品允许出现引号，字数窗口放宽到 150～900，重复引用不再致命。
"""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

import brains
import personas
from runtime import at_chat, chat_archive, chat_archive_query, dispatch, features, personal_portrait, personal_story, prompt_tiers, tuning
from runtime.ctx import PRIMARY_CID
from runtime.dream import TEA_CID
from runtime.routing import RoutingSender


def forbidden(*args, **kwargs):
    raise AssertionError("must not call general chat, memory, platform or real model")


def card():
    return {"dimensions": [
        {"title": "兴趣：喜欢语言和创作", "text": "你会聊起相声，也愿意寻找新的创作点子。[[m1]]这条发言把欣赏作品和动手尝试放在了一起。你会请大家推荐，也会把自己的想法说出来，让聊天成为交换灵感的地方。"},
        {"title": "交流：留意具体的人", "text": "你会主动问候熟悉的群友：[[m2]]。从这些聊天里，可以看到你会留意大家是否出现、最近过得怎样。你的关心落在具体的问题上，这些招呼给日常聊天留下了熟悉感。"},
        {"title": "做事：愿意尝试和求助", "text": "你在动手尝试的同时，也愿意听取建议：[[m3]]。遇到还不熟悉的事情，你会把问题说出来，邀请大家一起讨论。研究和求助都出现在你的发言里，想法也在这些来回交流中继续发展。"}],
        "ending": "愿你继续保留好奇和玩心，也给自己留一点随心玩耍、不必完成什么的时间。"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, dbdir):
    saved_features, saved_tuning, saved_personas = features.overrides(), tuning.overrides(), personas.config_personas()
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    monkeypatch.delenv("DZMM_TUNING", raising=False)
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(dbdir / "chat.db"))
    features.set_overrides({PRIMARY_CID + "|personal_portrait": True})
    tuning.set_overrides({})
    personas.set_config_personas({})
    monkeypatch.setattr(personal_portrait, "_LAST", {})
    monkeypatch.setattr(personal_portrait, "_ACTIVE", set())
    monkeypatch.setattr(personal_portrait, "_LIMIT", threading.BoundedSemaphore(1))
    monkeypatch.setattr(personal_portrait, "log", lambda *args: None)
    monkeypatch.setattr(brains, "_call_llm_raw", forbidden)
    yield
    features.set_overrides(saved_features)
    tuning.set_overrides(saved_tuning)
    personas.set_config_personas(saved_personas)


@pytest.fixture
def evidence():
    texts = ["相声十级爱好者在此，最近还想试试写角色卡。", "大家好久不见，最近身体好些了吗？",
             "第一次尝试写团本，姐妹们可以给提提意见。", "最近研究机器人，好多地方还要继续尝试。",
             "今日工作完成，准备出门走走，明天再继续研究。", "今天先睡觉去了，明天再来看看大家的建议。"]
    records = [{"id": "m" + str(number), "uid": "guest", "name": "同名成员", "type": "text",
                "sent_at": "2026-08-{:02d}T08:00:00+08:00".format(number), "text": text}
               for number, text in enumerate(texts, 1)]
    records.append({**records[0], "id": "other", "uid": "other-user", "text": "另一位同名成员的秘密，不能混入画像。"})
    path = chat_archive.archive_path()
    source = path.with_suffix(".jsonl")
    source.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    chat_archive.import_jsonl(source, group_id=PRIMARY_CID, path=path)
    return chat_archive_query.portrait_evidence("guest")


@pytest.mark.parametrize("text,payload", [
    ("/我的画像", ""), ("/我的画像：2026-08-01~2026-09-30", "2026-08-01~2026-09-30"),
    ("/我的画像集", None), ("#我的画像", None), ("请用/我的画像", None),
])
def test_exact_command(text, payload):
    assert personal_portrait.command_payload(text) == payload


@pytest.mark.parametrize("text,payload", [
    ("我的画像", ""), ("帮我写我的画像", ""), ("请帮我生成一份我的画像", ""),
    ("帮我整理一下我的画像 2026-08-01~2026-09-30", "2026-08-01~2026-09-30"),
    ("不要帮我写我的画像", None), ("她讲了我的画像", None), ("我的画像很好看", None),
    ("写桃子的画像", None),
])
def test_explicit_at_requests(text, payload):
    assert personal_portrait.request_payload(text) == payload


def test_uid_dates_original_quotes_and_dimension_format(evidence):
    assert evidence["total"] == 6
    assert {item["message_id"] for item in evidence["messages"]} == {"m" + str(number) for number in range(1, 7)}
    selected = chat_archive_query.portrait_evidence("guest", start="2026-08-02", end="2026-08-03")
    assert {item["message_id"] for item in selected["messages"]} == {"m2", "m3"}
    output = personal_portrait.render_portrait(personal_portrait.parse_portrait(json.dumps(card(), ensure_ascii=False)), evidence)
    assert "1. 兴趣：喜欢语言和创作\n" in output and "2. 交流：留意具体的人\n" in output
    assert "“" + evidence["messages"][0]["text"] + "”" in output
    assert "[[" not in output and "第一章" not in output and "2026年" not in output
    assert 200 <= len(output) <= 900 and output.endswith(personal_portrait.SCOPE + card()["ending"])


@pytest.mark.parametrize("change", [
    lambda data: data["dimensions"][0].update(text="你说[[invented]]，今天过得很好。"),
    lambda data: data["dimensions"][0].update(text="你说[[other]]，今天过得很好。"),
    lambda data: data["dimensions"][0].update(title="第一章"),
    lambda data: data["dimensions"][0].update(title="2026年8月1日~8月30日"),
    lambda data: data["dimensions"][0].update(title="兴趣\n新的标题"),
    lambda data: data["dimensions"][1].update(title=data["dimensions"][0]["title"]),
    lambda data: data["dimensions"][0].update(text="有趣的生活，但是没有证据引用。"),
    lambda data: data["dimensions"][0].update(text="你说[[m1]]。" + "生活" * 500),
    lambda data: data["dimensions"][0].update(text="你说[[m1]]，还说[[bad id]]。"),
    lambda data: data.update(ending="祝你开心[[m3]]，未来慢慢走向想去的地方。"),
    lambda data: data.update(dimensions=data["dimensions"][:2]),
])
def test_unknown_quotes_duplicate_dimensions_and_bad_shape_are_rejected(evidence, change):
    data = copy.deepcopy(card())
    change(data)
    with pytest.raises(ValueError):
        personal_portrait.render_portrait(personal_portrait.parse_portrait(json.dumps(data, ensure_ascii=False)), evidence)


def test_quotes_and_repeated_references_are_allowed(evidence):
    """2026-10-07 屋主口径：可以出现引号；同一条发言引两次不再是致命错误。

    旧版把「出现任何引号字符」和「重复引用」都当整篇作废 —— 真模型 6/6 死在前者。
    防编造那条底线（ID 必须真实存在）不变。
    """
    data = copy.deepcopy(card())
    data["dimensions"][0]["text"] = ("你会聊起「剧情游戏」这类更新，也说过“忙完再休息”，"
                                     "还会带上自己的原话：[[m1]]。这些都在群里出现过，"
                                     "后来你把话题转回日常，继续和大家交换想法和点子。")
    data["dimensions"][1]["text"] = data["dimensions"][1]["text"].replace("[[m2]]", "[[m1]]")
    output = personal_portrait.render_portrait(personal_portrait.parse_portrait(json.dumps(data, ensure_ascii=False)), evidence)
    assert "「剧情游戏」" in output and "“忙完再休息”" in output
    assert "““" not in output and "””" not in output
    assert output.count("“" + evidence["messages"][0]["text"] + "”") == 2


def test_extra_fields_are_ignored_not_fatal(evidence):
    """与「我的故事」同口径：多写的字段忽略，不因为无害的键整篇作废。"""
    data = copy.deepcopy(card())
    data["version"] = 2
    for dimension in data["dimensions"]:
        dimension["weight"] = 1
    output = personal_portrait.render_portrait(personal_portrait.parse_portrait(json.dumps(data, ensure_ascii=False)), evidence)
    assert output.startswith(personal_portrait.INTRO) and "1. " in output


def test_one_model_call_receives_only_self_messages(monkeypatch, evidence):
    calls = []

    def model(system, payload, **kwargs):
        calls.append((system, json.loads(payload), kwargs))
        return json.dumps(card(), ensure_ascii=False), "stop"

    monkeypatch.setattr(brains, "_call_llm_raw", model)
    reply = asyncio.run(personal_portrait.portrait_reply(PRIMARY_CID, "guest", "/我的画像"))
    assert reply.startswith(personal_portrait.INTRO) and len(calls) == 1
    assert calls[0][1]["target_chars"] == 500
    assert {item["message_id"] for item in calls[0][1]["messages"]} == {"m" + str(number) for number in range(1, 7)}
    assert "同名成员" not in json.dumps(calls[0][1], ensure_ascii=False)
    assert "等一小会儿" in asyncio.run(personal_portrait.portrait_reply(PRIMARY_CID, "guest", "/我的画像"))
    assert len(calls) == 1 and not personal_portrait._ACTIVE
    assert personal_story._LAST.get((PRIMARY_CID, "guest")) is None


def test_bad_range_sparse_material_and_missing_archive_do_not_call_model(evidence, monkeypatch, dbdir):
    assert "只能生成你自己" in asyncio.run(personal_portrait.portrait_reply(PRIMARY_CID, "guest", "/我的画像 桃子"))
    assert "还不够" in asyncio.run(personal_portrait.portrait_reply(PRIMARY_CID, "guest", "/我的画像 2026-08-01~2026-08-01"))
    path = dbdir / "missing.db"
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(path))
    assert "没有找到" in asyncio.run(personal_portrait.portrait_reply(PRIMARY_CID, "another", "/我的画像"))
    assert not path.exists()


@pytest.mark.parametrize("finish,reply", [("length", "{}"), ("stop", "not json")])
def test_model_failure_releases_capacity_after_bounded_retry(monkeypatch, evidence, finish, reply):
    """2026-10-07：失败会**带原因重试一次**（真模型偶发坏 JSON / 编造 ID），
    所以调用次数是 MAX_ATTEMPTS 而不是 1；上限仍然有，不会无限重试。"""
    calls = []

    def model(*args, **kwargs):
        calls.append(1)
        return reply, finish

    monkeypatch.setattr(brains, "_call_llm_raw", model)
    assert asyncio.run(personal_portrait.portrait_reply(PRIMARY_CID, "guest", "/我的画像")) == personal_portrait.FAILURE
    assert len(calls) == personal_story.MAX_ATTEMPTS and not personal_portrait._ACTIVE
    assert personal_portrait._LIMIT.acquire(blocking=False)
    personal_portrait._LIMIT.release()
    assert (PRIMARY_CID, "guest") not in personal_portrait._LAST


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, text, cid, **kwargs):
        self.sent.append((cid, text))
        return True


async def entry(sender, cid, text, *, via_at=False):
    group_state, state = {"last_id": ""}, {}
    messages = [{"message_id": "request", "sent_by": "guest", "content": {"text": text}}]
    if via_at:
        await at_chat.handle_at_chat(sender, cid, text, "guest", False, group_state, state, messages,
                                    messages[0], False, _save_group=lambda *args: None,
                                    _mark_inflight=lambda *args: None, _remember_turn=lambda *args: None,
                                    _recent_turns=forbidden, _seed_turns=forbidden)
    else:
        await dispatch.handle_slash(sender, cid, text, "guest", False, group_state, messages, messages[0], state,
                                    _save_group=lambda *args: None, _mark_inflight=lambda *args: None,
                                    _remember_turn=lambda *args: None, rank_summary=forbidden, sync_profiles=forbidden)
    return group_state


@pytest.fixture
def entry_mocks(monkeypatch):
    monkeypatch.setattr(dispatch, "log", lambda *args: None)
    monkeypatch.setattr(at_chat, "log", lambda *args: None)
    monkeypatch.setattr(dispatch, "generate_reply", forbidden)
    monkeypatch.setattr(at_chat, "generate_reply", forbidden)
    monkeypatch.setattr(at_chat, "_extract_remember", forbidden)
    monkeypatch.setattr(at_chat, "_active_play_hint", lambda cid: None)
    monkeypatch.setattr(at_chat, "_strip_at", lambda text, cid: text.removeprefix("@小小糯 "))
    for name in ("_get_perf_state", "_get_fasao_state", "_get_dj_state", "_get_story_state", "_get_vote_state", "_dice_state"):
        monkeypatch.setattr(dispatch, name, lambda *args: {})


@pytest.mark.parametrize("via_at", [False, True])
def test_actual_entry_preserves_dimension_format_and_bot_route(monkeypatch, evidence, entry_mocks, via_at):
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: (json.dumps(card(), ensure_ascii=False), "stop"))
    official, account = [], FakeSender()

    def send_text(cid, text):
        official.append((cid, text))
        return True

    sender = RoutingSender(account, {PRIMARY_CID: SimpleNamespace(send_text=send_text)})
    state = asyncio.run(entry(sender, PRIMARY_CID, "@小小糯 我的画像" if via_at else "/我的画像", via_at=via_at))
    assert state["last_id"] == "request" and len(official) == 1 and not account.sent
    assert official[0][1].startswith("🤖 " + personal_portrait.INTRO)
    assert "\n\n2. " in official[0][1] and official[0][1].endswith(card()["ending"])
    assert dispatch.is_dj_command_cmd("/我的画像") is None


@pytest.mark.parametrize("cid,via_at,disabled", [(TEA_CID, False, False), (TEA_CID, True, False),
                                                 (PRIMARY_CID, False, True), (PRIMARY_CID, True, True)])
def test_disabled_and_outside_requests_never_fall_back(entry_mocks, cid, via_at, disabled):
    features.set_overrides({"personal_portrait": not disabled})
    sender = FakeSender()
    asyncio.run(entry(sender, cid, "@小小糯 我的画像" if via_at else "/我的画像", via_at=via_at))
    assert not sender.sent


def test_other_persona_and_at_noise_are_silent(monkeypatch, entry_mocks):
    personas.set_config_personas({PRIMARY_CID: "ruojin"})
    assert not personal_portrait.enabled(PRIMARY_CID)
    personas.set_config_personas({})
    monkeypatch.setattr(at_chat, "_recap_is_noise", lambda *args: True)
    sender = FakeSender()
    asyncio.run(entry(sender, PRIMARY_CID, "@小小糯 我的画像", via_at=True))
    assert not sender.sent


def test_help_and_prompt_contract(entry_mocks):
    sender = FakeSender()
    asyncio.run(entry(sender, PRIMARY_CID, "/帮助"))
    assert "/我的画像" in sender.sent[-1][1]
    features.set_overrides({})
    asyncio.run(entry(sender, PRIMARY_CID, "/帮助"))
    assert "/我的画像" not in sender.sent[-1][1]
    assert prompt_tiers.check_contract("PERSONAL_PORTRAIT_PROMPT", json.dumps(card(), ensure_ascii=False)) == []
    assert prompt_tiers.check_contract("PERSONAL_PORTRAIT_PROMPT", "随便写一段")


def test_timeout_keeps_capacity_until_worker_finishes(monkeypatch, evidence):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def model(*args, **kwargs):
        entered.set()
        try:
            release.wait(5)
            return json.dumps(card(), ensure_ascii=False), "stop"
        finally:
            finished.set()

    monkeypatch.setattr(brains, "_call_llm_raw", model)
    tuning.set_overrides({"personal_portrait.timeout": 1})

    async def scenario():
        first = asyncio.create_task(personal_portrait.portrait_reply(PRIMARY_CID, "guest", "/我的画像"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert "有些慢" in await first
            assert (PRIMARY_CID, "guest") in personal_portrait._ACTIVE
            assert not personal_portrait._LIMIT.acquire(blocking=False)
            assert "另一份画像" in await personal_portrait.portrait_reply(PRIMARY_CID, "another", "/我的画像")
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)

    asyncio.run(scenario())
    assert not personal_portrait._ACTIVE and personal_portrait._LIMIT.acquire(blocking=False)
    personal_portrait._LIMIT.release()
