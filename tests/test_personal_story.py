"""个人故事的取证隔离、真实引文、模糊分段输出和入口回归。

2026-10-07：阶段不再带日期（改成程序编号的「第一阶段…」），成品允许出现引号。
"""
import asyncio
import copy
import json
import sqlite3
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest

import brains
import personas
from runtime import at_chat, chat_archive, chat_archive_query, dispatch, features, personal_story, prompt_tiers, tuning
from runtime.ctx import PRIMARY_CID
from runtime.dream import TEA_CID
from runtime.routing import RoutingSender


def forbidden(*args, **kwargs):
    raise AssertionError("must not call general chat, memory, platform or real model")


def record(identifier, day, *, uid="guest", text="今天下班后想了想，准备慢慢攒钱去海边走走。", kind="text"):
    return {"id": identifier, "uid": uid, "name": "同名成员", "type": kind,
            "sent_at": day + "T08:00:00+08:00", "text": text}


def import_records(path, records):
    source = path.with_suffix(".jsonl")
    source.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    chat_archive.import_jsonl(source, group_id=PRIMARY_CID, path=path)


def card(evidence):
    first, last = evidence["messages"][0], evidence["messages"][-1]
    return {"opening": "某人，你的故事要从八月中旬那次冒泡说起——",
            "stages": [
        {"title": "刚进群那阵",
         "text": "刚开始那阵，你聊起了自己的日常。你说[[" + first["message_id"]
                 + "]]，留下了对下一段生活的期待。日子就这样在聊天里向前走，没有什么大事，但也一直没停。"},
        {"title": "后来提起的愿望",
         "text": "到了后来，你又提起了自己的愿望：[[" + last["message_id"]
                 + "]]。这仍是一份计划，记录里还没有它实现的消息，你把它说给群里听，然后继续过自己的日子。"}],
        "ending": "故事暂时停在这些日常里。愿你忙碌之余，也能留一点时间给自己，让想做的事情慢慢走进生活，不必急着完成什么。"}


def head(evidence):
    """成品的前缀：开场白 + 第一段标题。"""
    return card(evidence)["opening"] + "\n\n一、刚进群那阵\n"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, dbdir):
    saved_features, saved_tuning, saved_personas = features.overrides(), tuning.overrides(), personas.config_personas()
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    monkeypatch.delenv("DZMM_TUNING", raising=False)
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(dbdir / "chat.db"))
    features.set_overrides({PRIMARY_CID + "|personal_story": True})
    tuning.set_overrides({})
    personas.set_config_personas({})
    monkeypatch.setattr(personal_story, "_LAST", {})
    monkeypatch.setattr(personal_story, "_ACTIVE", set())
    monkeypatch.setattr(personal_story, "_LIMIT", threading.BoundedSemaphore(1))
    monkeypatch.setattr(personal_story, "log", lambda *args: None)
    # 名字走本地：真 `display_name` 会查平台缓存（开场白要点名，所以 payload 里带 name）
    monkeypatch.setattr(personal_story, "display_name", lambda uid, fallback="": "发起人")
    monkeypatch.setattr(brains, "_call_llm_raw", forbidden)
    yield
    features.set_overrides(saved_features)
    tuning.set_overrides(saved_tuning)
    personas.set_config_personas(saved_personas)


@pytest.fixture
def evidence():
    path = chat_archive.archive_path()
    import_records(path, [record("m1", "2026-08-16"), record("m2", "2026-08-23"),
                          record("m3", "2026-10-02"),
                          record("other", "2026-09-01", uid="other-user", text="另一位同名成员的秘密，不许混入故事。")])
    return chat_archive_query.story_evidence("guest")


@pytest.mark.parametrize("text,payload", [
    ("/我的故事", ""), ("/我的故事：2026-08-01~2026-09-30", "2026-08-01~2026-09-30"),
    ("/我的故事集", None), ("#我的故事", None), ("请用/我的故事", None),
])
def test_exact_command(text, payload):
    assert personal_story.command_payload(text) == payload


@pytest.mark.parametrize("text,payload", [
    ("我的故事", ""), ("帮我写我的故事", ""), ("请帮我生成一份我的故事", ""),
    ("帮我整理一下我的故事 2026-08-01~2026-09-30", "2026-08-01~2026-09-30"),
    ("不要帮我写我的故事", None), ("她讲了我的故事", None), ("我的故事很好听", None),
    ("写桃子的故事", None),
])
def test_explicit_at_requests(text, payload):
    assert personal_story.request_payload(text) == payload


def test_date_range_and_local_day_bounds(evidence):
    assert personal_story.date_range("") == ("", "")
    assert personal_story.date_range("2026-08-01至2026-09-30") == ("2026-08-01", "2026-09-30")
    path = chat_archive.archive_path()
    import_records(path, [record("late", "2026-08-23") | {"sent_at": "2026-08-23T23:59:59+08:00"},
                          record("next", "2026-08-24") | {"sent_at": "2026-08-24T00:00:00+08:00"}])
    selected = chat_archive_query.story_evidence("guest", start="2026-08-23", end="2026-08-23")
    assert {item["message_id"] for item in selected["messages"]} == {"m2", "late"}
    for value in ("桃子", "2026-09-30~2026-08-01", "2026-02-30~2026-03-01"):
        with pytest.raises(ValueError):
            personal_story.date_range(value)


def test_self_uid_isolation_and_original_quote_rendering(evidence):
    assert evidence["total"] == 3
    assert {item["message_id"] for item in evidence["messages"]} == {"m1", "m2", "m3"}
    output = personal_story.render_story(personal_story.parse_story(json.dumps(card(evidence), ensure_ascii=False)), evidence)
    assert output.startswith(head(evidence))
    assert "\n\n二、后来提起的愿望\n" in output
    assert "“" + evidence["messages"][0]["text"] + "”" in output
    assert "[[" not in output and "第一章" not in output and output.endswith(card(evidence)["ending"])


def test_missing_archive_is_read_only_and_empty_uid_never_selects_everyone(dbdir):
    path = dbdir / "missing.db"
    assert not chat_archive_query.story_evidence("guest", path=path)["available"]
    assert not path.exists()
    with pytest.raises(ValueError):
        chat_archive_query.story_evidence("", path=path)


def test_stratified_selection_stays_bounded_and_keeps_both_ends(dbdir):
    path = dbdir / "large.db"
    records = [record("row-{}-{}".format(month, number), "2026-{:02d}-15".format(month),
                      text="今天上班，聊起新的计划。" + "一些普通聊天记录。" * 25)
               for month in range(1, 13) for number in range(35)]
    records += [record("attachment", "2026-05-10", kind="image"),
                record("command", "2026-05-10", text="/我的故事"),
                record("url", "2026-05-10", text="https://example.test"),
                record("bot", "2026-05-10", text="🤖 一个机器人编造的故事。")]
    import_records(path, records)
    selected = chat_archive_query.story_evidence("guest", path=path)
    assert len(selected["messages"]) <= 146
    assert sum(len(item["text"]) for item in selected["messages"]) <= 24000
    assert {item["day"][5:7] for item in selected["messages"]} == {"{:02d}".format(month) for month in range(1, 13)}
    assert selected["messages"][0]["day"] == selected["start"] == "2026-01-15"
    assert selected["messages"][-1]["day"] == selected["end"] == "2026-12-15"
    assert all(item["clipped"] and len(item["text"]) == 160 for item in selected["messages"])
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == len(records)


def test_every_excerpt_carries_its_cost_in_chars(dbdir):
    """`chars` 是给模型看的字数成本（引文插进成品后占多少字）。

    没有它，模型算不出成品长度 —— 2026-10-07 实测这是 `length` 失败的根。
    """
    path = dbdir / "chars.db"
    import_records(path, [record("m1", "2026-08-16", text="短句。"),
                          record("m2", "2026-08-17", text="长" * 400)])
    selected = chat_archive_query.story_evidence("guest", path=path)
    for item in selected["messages"]:
        assert item["chars"] == len(item["text"])
    assert max(item["chars"] for item in selected["messages"]) == 160, "长消息只取开头，成本按摘录算"


@pytest.mark.parametrize("change", [
    # 编造的 ID（归档里不存在）—— 防编造这条底线
    lambda data: data["stages"][0].update(text="你说[[invented]]，今天过得很好。"),
    lambda data: data["stages"][0].update(text="你说[[m1]]。" + "生活" * 900),
    lambda data: data["stages"][0].update(text="   "),
    # 开场白：缺、太短、混进引用
    lambda data: data.pop("opening"),
    lambda data: data.update(opening="嗨。"),
    lambda data: data.update(opening="你的故事从这句引用开始[[" + "m1]]，后面还有很长很长的一段要讲。"),
    # 标题：缺、太短、带序号/日期、重复
    lambda data: data["stages"][0].pop("title"),
    lambda data: data["stages"][0].update(title="短"),
    lambda data: data["stages"][0].update(title="第一阶段：刚进群"),
    lambda data: data["stages"][0].update(title="2026年8月"),
    lambda data: data["stages"][0].update(title="# 标题"),
    lambda data: data["stages"][1].update(title=data["stages"][0]["title"]),
    lambda data: data["stages"].extend([{"title": "又一段", "text": "又一段。"}] * 4),
    lambda data: data.update(stages=[]),
    lambda data: data.update(ending="祝你开心[[m3]]，未来慢慢走向想去的地方。"),
    lambda data: data.update(ending="太好啦。"),
    # 内容合法但成品太短 —— 走的是 render 的字数下限，不是 parse
    lambda data: data.update(stages=[{"title": "只有一段", "text": "嗯。"}],
                             ending="愿你每天都开心一点，慢慢走向自己想去的地方。"),
])
def test_invalid_dates_invented_dialogue_and_references_are_rejected(evidence, change):
    data = copy.deepcopy(card(evidence))
    change(data)
    with pytest.raises(ValueError):
        personal_story.render_story(personal_story.parse_story(json.dumps(data, ensure_ascii=False)), evidence)


def test_opening_comes_first_and_names_the_person(evidence):
    """2026-10-07 屋主实测：「太突兀，前面要加一句 XXX 你的精彩故事从哪天开始展开」。

    开场白是**模型写的第一段**（所以能点名、能说"从哪说起"），程序把它排在所有分段之前。
    """
    data = copy.deepcopy(card(evidence))
    data["opening"] = "  某某，你的故事要从七月末刚进群那天说起——  "
    output = personal_story.render_story(personal_story.parse_story(json.dumps(data, ensure_ascii=False)), evidence)
    assert output.startswith("某某，你的故事要从七月末刚进群那天说起——\n\n一、")
    assert output.index("某某，你的故事") < output.index("一、刚进群那阵")
    assert "第一阶段" not in output


def test_stage_titles_are_numbered_by_the_program(evidence):
    """屋主 2026-10-07：「不要用第一阶段、第二阶段，就用"一、XXX"取一个题目」。

    编号由程序加（一、二、三），模型只给标题文字；标题两头多余标点会被清掉。
    """
    data = copy.deepcopy(card(evidence))
    data["stages"][0]["title"] = "  刚进群那阵：  "
    data["stages"].append({"title": "开学以后", "text": "开学之后你换了节奏，群里提到[["
                          + evidence["messages"][-1]["message_id"] + "]]，日子照常往前走。"})
    output = personal_story.render_story(personal_story.parse_story(json.dumps(data, ensure_ascii=False)), evidence)
    assert output.startswith(head(evidence))
    assert "\n\n二、后来提起的愿望\n" in output
    assert "\n\n三、开学以后\n" in output
    assert "第一阶段" not in output and "、：" not in output


def test_quotes_and_own_stage_labels_are_handled_not_rejected(evidence):
    """2026-10-07 屋主口径：可以出现引号；阶段标题由程序生成。

    引号不再致命（旧版禁止任何引号字符，真模型写「剧情游戏」就整篇作废）；
    模型自己写的「第一阶段」前缀会被剥掉，且占位符外面已有的引号不会变成双引号。
    """
    data = copy.deepcopy(card(evidence))
    first = evidence["messages"][0]
    data["stages"][0]["text"] = ("第一阶段：那阵子你写过「剧情游戏」的更新公告，"
                                 "也说过“忙完这阵就休息”，还留了一句[[" + first["message_id"] + "]]。"
                                 "后来你把话题转回日常，继续在群里冒泡，没什么大事发生。")
    output = personal_story.render_story(personal_story.parse_story(json.dumps(data, ensure_ascii=False)), evidence)
    assert output.startswith("某人，你的故事要从八月中旬那次冒泡说起——\n\n一、")
    assert "「剧情游戏」" in output and "“忙完这阵就休息”" in output
    assert "““" not in output and "””" not in output
    assert "“" + first["text"] + "”" in output
    assert "第一阶段：第一阶段" not in output


def test_extra_fields_are_ignored_not_fatal(evidence):
    """模型爱自己加字段（实测见过 `text_placeholder`、`start`、`title`）。

    多写的键无害，为它把整篇作废不值当 —— 只有"必须的字段缺失"才拒绝。
    """
    data = copy.deepcopy(card(evidence))
    data["version"] = 3
    for stage in data["stages"]:
        stage["start"] = "2026-08-16"
        stage["text_placeholder"] = ""
    output = personal_story.render_story(personal_story.parse_story(json.dumps(data, ensure_ascii=False)), evidence)
    assert output.startswith(head(evidence)) and "[[message_id]]" not in output


def test_generation_uses_one_model_call_and_only_self_evidence(monkeypatch, evidence):
    calls = []

    def model(system, payload, **kwargs):
        data = json.loads(payload)
        calls.append((system, data, kwargs))
        return json.dumps(card(data), ensure_ascii=False), "stop"

    monkeypatch.setattr(brains, "_call_llm_raw", model)
    reply = asyncio.run(personal_story.story_reply(PRIMARY_CID, "guest", "/我的故事"))
    assert reply.startswith(head(evidence)) and len(calls) == 1
    assert {item["message_id"] for item in calls[0][1]["messages"]} == {"m1", "m2", "m3"}
    assert calls[0][1]["target_chars"] == 500
    assert calls[0][1]["name"] == "发起人", "开场白要点名，所以要把名字传进去"
    assert "同名成员" not in json.dumps(calls[0][1], ensure_ascii=False)
    assert "等一小会儿" in asyncio.run(personal_story.story_reply(PRIMARY_CID, "guest", "/我的故事"))
    assert len(calls) == 1 and not personal_story._ACTIVE


def test_material_and_bad_range_return_clear_hints_without_model(evidence):
    assert "只能生成你自己" in asyncio.run(personal_story.story_reply(PRIMARY_CID, "guest", "/我的故事 桃子"))
    assert "还不够" in asyncio.run(personal_story.story_reply(PRIMARY_CID, "guest", "/我的故事 2026-08-16~2026-08-16"))
    assert "没有找到" not in asyncio.run(personal_story.story_reply(PRIMARY_CID, "missing-user", "/我的故事"))


@pytest.mark.parametrize("finish,reply", [("length", "{}"), ("stop", "not json")])
def test_invalid_model_result_returns_failure_and_releases_capacity(monkeypatch, evidence, finish, reply):
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: (reply, finish))
    assert asyncio.run(personal_story.story_reply(PRIMARY_CID, "guest", "/我的故事")) == personal_story.FAILURE
    assert not personal_story._ACTIVE and personal_story._LIMIT.acquire(blocking=False)
    personal_story._LIMIT.release()
    # 失败不吃冷却：不然用户要白等一个 cooldown 才能重试
    assert (PRIMARY_CID, "guest") not in personal_story._LAST


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
def test_actual_entry_and_existing_bot_route(monkeypatch, evidence, entry_mocks, via_at):
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: (json.dumps(card(evidence), ensure_ascii=False), "stop"))
    official, account = [], FakeSender()

    def send_text(cid, text):
        official.append((cid, text))
        return True

    sender = RoutingSender(account, {PRIMARY_CID: SimpleNamespace(send_text=send_text)})
    text = "@小小糯 我的故事" if via_at else "/我的故事"
    state = asyncio.run(entry(sender, PRIMARY_CID, text, via_at=via_at))
    assert state["last_id"] == "request" and len(official) == 1 and not account.sent
    assert official[0][1].startswith("🤖 " + head(evidence))
    assert "\n\n二、后来提起的愿望\n" in official[0][1] and official[0][1].endswith(card(evidence)["ending"])
    assert dispatch.is_dj_command_cmd("/我的故事") is None


@pytest.mark.parametrize("cid,via_at,disabled", [(TEA_CID, False, False), (TEA_CID, True, False),
                                                 (PRIMARY_CID, False, True), (PRIMARY_CID, True, True)])
def test_disabled_and_outside_requests_do_not_fall_back(entry_mocks, cid, via_at, disabled):
    features.set_overrides({"personal_story": not disabled})
    sender = FakeSender()
    asyncio.run(entry(sender, cid, "@小小糯 我的故事" if via_at else "/我的故事", via_at=via_at))
    assert not sender.sent


def test_other_persona_and_at_noise_never_generate(monkeypatch, entry_mocks):
    personas.set_config_personas({PRIMARY_CID: "ruojin"})
    assert not personal_story.enabled(PRIMARY_CID)
    personas.set_config_personas({})
    monkeypatch.setattr(at_chat, "_recap_is_noise", lambda *args: True)
    sender = FakeSender()
    asyncio.run(entry(sender, PRIMARY_CID, "@小小糯 我的故事", via_at=True))
    assert not sender.sent


def test_help_and_prompt_contract(entry_mocks):
    sender = FakeSender()
    asyncio.run(entry(sender, PRIMARY_CID, "/帮助"))
    assert "/我的故事" in sender.sent[-1][1]
    features.set_overrides({})
    asyncio.run(entry(sender, PRIMARY_CID, "/帮助"))
    assert "/我的故事" not in sender.sent[-1][1]
    assert prompt_tiers.check_contract("PERSONAL_STORY_PROMPT", "随便编一段")
    data = {"opening": "某某，你的故事要从那个上夜班的夏天说起——",
            "stages": [
        {"title": "刚开始那阵", "text": "刚开始那阵，你聊起了夜班工作，说[[m1]]。这是一份还在等待实现的愿望，也是一段忙碌生活里留下的记录。"},
        {"title": "后来换了节奏", "text": "后来你把作息调了过来，在群里提到[[m2]]，日子忙但也松快，还留着一点对假期的期待没说完。"}],
        "ending": "故事暂时停在这些日常里，愿你忙碌之余，也能留一点时间给自己，让想做的事情慢慢走进生活。"}
    assert prompt_tiers.check_contract("PERSONAL_STORY_PROMPT", json.dumps(data, ensure_ascii=False)) == []


def test_timeout_keeps_worker_capacity_until_thread_finishes(monkeypatch, evidence):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def model(*args, **kwargs):
        entered.set()
        try:
            release.wait(5)
            return json.dumps(card(evidence), ensure_ascii=False), "stop"
        finally:
            finished.set()

    monkeypatch.setattr(brains, "_call_llm_raw", model)
    tuning.set_overrides({"personal_story.timeout": 1})

    async def scenario():
        first = asyncio.create_task(personal_story.story_reply(PRIMARY_CID, "guest", "/我的故事"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert "有些慢" in await first
            assert (PRIMARY_CID, "guest") in personal_story._ACTIVE
            assert not personal_story._LIMIT.acquire(blocking=False)
            assert "另一份故事" in await personal_story.story_reply(PRIMARY_CID, "another", "/我的故事")
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)

    asyncio.run(scenario())
    assert not personal_story._ACTIVE and personal_story._LIMIT.acquire(blocking=False)
    personal_story._LIMIT.release()
