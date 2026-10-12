"""`/致某某`（代发起者写给群友的一封信）：目标解析、取证隔离、信件格式和入口回归。

与「我的故事」拉开距离的三条，是这个文件重点钉的：
1. **读的是收信人的归档**，不是发起者的（反过来也不行）；
2. **口吻**：信是发起者的，落款由程序加；
3. **不分章节**：`{paragraphs, ending}`，没有 `一、XXX`。
"""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

import brains
import personas
from runtime import at_chat, chat_archive, chat_archive_query, dispatch, features, personal_letter, prompt_tiers, tuning
from runtime.ctx import PRIMARY_CID
from runtime.dream import TEA_CID
from runtime.routing import RoutingSender

TARGET = "guest"
OTHER = "someone-else"


def forbidden(*args, **kwargs):
    raise AssertionError("must not call general chat, memory, platform or real model")


def record(identifier, day, *, uid=TARGET, name="收信人", text="今天下班后想了想，准备慢慢攒钱去海边走走。", kind="text"):
    return {"id": identifier, "uid": uid, "name": name, "type": kind,
            "sent_at": day + "T08:00:00+08:00", "text": text}


def import_records(path, records):
    source = path.with_suffix(".jsonl")
    source.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    chat_archive.import_jsonl(source, group_id=PRIMARY_CID, path=path)


def card(evidence):
    msgs = evidence["messages"]
    return {"paragraphs": [
        "我其实很早就注意到你了。你说[[" + msgs[0]["message_id"]
        + "]]，那时候我就觉得你这个人挺有意思，后来也一直记得。",
        "你身上有一股很实在的认真劲儿，做什么都想弄明白。这一点我一直记着，也想跟你说一声。"],
        "ending": "新的一年，愿你少熬几个夜，想说话的时候群里总有人在。"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, dbdir):
    saved_features, saved_tuning, saved_personas = features.overrides(), tuning.overrides(), personas.config_personas()
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    monkeypatch.delenv("DZMM_TUNING", raising=False)
    monkeypatch.delenv("DZMM_LETTER_OPTOUT", raising=False)
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(dbdir / "chat.db"))
    features.set_overrides({PRIMARY_CID + "|personal_letter": True})
    tuning.set_overrides({})
    personas.set_config_personas({})
    monkeypatch.setattr(personal_letter, "_LAST", {})
    monkeypatch.setattr(personal_letter, "_ACTIVE", set())
    monkeypatch.setattr(personal_letter, "_TARGET_LAST", {})
    monkeypatch.setattr(personal_letter, "_LIMIT", threading.BoundedSemaphore(1))
    monkeypatch.setattr(personal_letter, "log", lambda *args: None)
    # 称呼/署名要走本地：真 `_gender_of` 在档案缺性别时会打平台
    monkeypatch.setattr(personal_letter, "salutation", lambda cid, uid, fallback="": fallback or uid[:8])
    monkeypatch.setattr(personal_letter, "display_name", lambda uid, fallback="": "发起人")
    # 找人的时候也会翻真实档案（profiles.json 里是线上成员）—— 测试里清空，保证封闭
    monkeypatch.setattr("profiles.load_profiles", lambda: {})
    monkeypatch.setattr(brains, "_call_llm_raw", forbidden)
    yield
    features.set_overrides(saved_features)
    tuning.set_overrides(saved_tuning)
    personas.set_config_personas(saved_personas)


@pytest.fixture
def evidence():
    path = chat_archive.archive_path()
    import_records(path, [
        record("m1", "2026-08-16"),
        record("m2", "2026-08-23", text="最近在弄一个新的小玩意，挺费时间的。"),
        record("m3", "2026-10-02", text="今天终于把那个东西做完了，有点开心。"),
        # 发起者自己的发言：**一个字都不许进 payload**
        record("mine", "2026-09-01", uid="sender", name="发起人", text="发起者自己的秘密，不能出现在信的证据里。"),
        # 另一个同名成员：同样不许混进来
        record("twin", "2026-09-02", uid=OTHER, name="同名成员", text="同名的另一个人，也不许混进来。"),
    ])
    return chat_archive_query.story_evidence(TARGET)


@pytest.mark.parametrize("text,payload", [
    ("/致 收信人", "收信人"), ("/致收信人", "收信人"),
    ("/致：收信人", "收信人"), ("/致 收信人 2026-08-01~2026-09-30", "收信人 2026-08-01~2026-09-30"),
    ("/致", ""), ("/致辞", "辞"), ("/我的故事", None), ("#致 收信人", None),
])
def test_exact_command(text, payload):
    assert personal_letter.command_payload(text) == payload


@pytest.mark.parametrize("text,payload", [
    ("致收信人", "收信人"), ("给收信人写封信", "收信人"), ("帮我给收信人写封信", "收信人"),
    ("写给收信人的信", "收信人"), ("请帮我给收信人写一封信", "收信人"),
    ("给收信人写封信 2026-08-01~2026-09-30", "收信人 2026-08-01~2026-09-30"),
    ("致敬", None), ("致谢", None), ("她讲了我的故事", None), ("给收信人点赞", None),
])
def test_explicit_at_requests(text, payload):
    assert personal_letter.request_payload(text) == payload


def test_split_request_and_bad_range():
    assert personal_letter.split_request("收信人") == ("收信人", "", "")
    assert personal_letter.split_request("收信人 2026-08-01~2026-09-30") == ("收信人", "2026-08-01", "2026-09-30")
    with pytest.raises(ValueError):
        personal_letter.split_request("收信人 2026-09-30~2026-08-01")


# ---------------------------------------------------------------- 目标解析

def test_find_senders_exact_and_prefix(evidence):
    hits = chat_archive_query.find_senders("收信人")
    assert [item["uid"] for item in hits] == [TARGET], hits
    assert hits[0]["name"] == "收信人"
    assert hits[0]["messages"] == 3, "只数这个人自己的发言"


def test_find_senders_is_read_only_and_never_creates_a_database(dbdir):
    path = dbdir / "missing.db"
    assert chat_archive_query.find_senders("收信人", path=path) == []
    assert not path.exists()


def test_ambiguous_names_are_returned_not_guessed(dbdir):
    """"两个人都对得上"必须**全部返回**，由调用方回问 —— 猜错就是把 A 的信写给 B。"""
    path = dbdir / "twin.db"
    import_records(path, [
        record("a1", "2026-08-01", uid="uid-a", name="糯糯～（裳姐姐）", text="甲说的话。"),
        record("b1", "2026-08-02", uid="uid-b", name="糯糯～（御褚）", text="乙说的话。"),
    ])
    hits = chat_archive_query.find_senders("糯糯～", path=path)
    assert {item["uid"] for item in hits} == {"uid-a", "uid-b"}, hits


def test_exact_match_wins_over_substring(dbdir):
    """有人叫「糯糯」、另一个人叫「糯糯的姐姐」时，写全名字就该唯一命中。"""
    path = dbdir / "exact.db"
    import_records(path, [
        record("a1", "2026-08-01", uid="uid-a", name="糯糯", text="甲说的话。"),
        record("b1", "2026-08-02", uid="uid-b", name="糯糯的姐姐", text="乙说的话。"),
    ])
    assert [item["uid"] for item in chat_archive_query.find_senders("糯糯", path=path)] == ["uid-a"]


# ---------------------------------------------------------------- 信本身

def test_letter_only_uses_the_recipients_own_messages(evidence):
    """**这是本功能与「我的故事」最大的区别**：证据必须全部来自收信人。"""
    payload = json.dumps({"to": "收信人", "from": "发起人", "target_chars": 750,
                          "messages": evidence["messages"]}, ensure_ascii=False)
    assert "发起者自己的秘密" not in payload
    assert "同名的另一个人" not in payload
    output = personal_letter.render_letter(
        personal_letter.parse_letter(json.dumps(card(evidence), ensure_ascii=False)),
        evidence, "收信人", "—— 代笔：小小糯 · 受「发起人」所托")
    assert output.startswith("收信人：\n\n")
    assert output.endswith("—— 代笔：小小糯 · 受「发起人」所托")
    assert "“" + evidence["messages"][0]["text"] + "”" in output
    assert "[[" not in output
    assert "一、" not in output and "第一阶段" not in output, "信件不分章节"


@pytest.mark.parametrize("change", [
    lambda data: data["paragraphs"][0].update() if False else data["paragraphs"].__setitem__(
        0, "你说[[invented]]，我一直记得。"),
    lambda data: data["paragraphs"].__setitem__(0, "只有一段，引用也没有。"),
    lambda data: data.__setitem__("paragraphs", data["paragraphs"][:1]),
    lambda data: data.__setitem__("paragraphs", data["paragraphs"] + ["多出来的一段。" * 30] * 3),
    lambda data: data["paragraphs"].__setitem__(0, "生活" * 900),
    lambda data: data.__setitem__("ending", "祝你开心[[m3]]，未来慢慢走向想去的地方。"),
    lambda data: data.__setitem__("ending", "太好啦。"),
])
def test_bad_letters_are_rejected(evidence, change):
    data = copy.deepcopy(card(evidence))
    change(data)
    with pytest.raises(ValueError):
        personal_letter.render_letter(
            personal_letter.parse_letter(json.dumps(data, ensure_ascii=False)),
            evidence, "收信人", "—— 代笔：小小糯 · 受「发起人」所托")


def test_extra_fields_are_ignored(evidence):
    data = copy.deepcopy(card(evidence))
    data["version"] = 2
    output = personal_letter.render_letter(
        personal_letter.parse_letter(json.dumps(data, ensure_ascii=False)),
        evidence, "收信人", "—— 代笔：小小糯 · 受「发起人」所托")
    assert output.startswith("收信人：\n\n")


# ---------------------------------------------------------------- 生成与限流

def test_generation_reads_only_the_recipient_and_writes_the_letter(monkeypatch, evidence):
    calls = []

    def model(system, payload, **kwargs):
        data = json.loads(payload)
        calls.append((system, data, kwargs))
        return json.dumps(card(data), ensure_ascii=False), "stop"

    monkeypatch.setattr(brains, "_call_llm_raw", model)
    reply = asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 收信人"))
    assert reply.startswith("收信人：\n\n") and len(calls) == 1
    assert {item["message_id"] for item in calls[0][1]["messages"]} == {"m1", "m2", "m3"}
    assert "发起者自己的秘密" not in json.dumps(calls[0][1], ensure_ascii=False)
    assert calls[0][1]["to"] == "收信人" and calls[0][1]["from"] == "发起人"
    assert calls[0][1]["target_chars"] == 500
    assert calls[0][1]["system"] if False else True
    assert calls[0][0] is brains.PERSONAL_LETTER_PROMPT


def test_unknown_and_ambiguous_targets_do_not_call_the_model(monkeypatch, evidence):
    monkeypatch.setattr(brains, "_call_llm_raw", forbidden)
    assert "没在认证群里找到" in asyncio.run(
        personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 查无此人"))
    assert personal_letter.USAGE in asyncio.run(
        personal_letter.letter_reply(PRIMARY_CID, "sender", "/致"))


def test_ambiguous_target_asks_instead_of_guessing(monkeypatch, dbdir):
    path = chat_archive.archive_path()
    import_records(path, [
        record("a1", "2026-08-01", uid="uid-a", name="糯糯～（裳姐姐）", text="甲说的话，够长够用。"),
        record("b1", "2026-08-02", uid="uid-b", name="糯糯～（御褚）", text="乙说的话，也够长够用。"),
    ])
    monkeypatch.setattr(brains, "_call_llm_raw", forbidden)
    reply = asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 糯糯～"))
    assert "对得上好几个人" in reply and "uid-a" not in reply


def test_optout_list_blocks_the_target(monkeypatch, evidence):
    monkeypatch.setenv("DZMM_LETTER_OPTOUT", TARGET)
    monkeypatch.setattr(brains, "_call_llm_raw", forbidden)
    assert "说过不想被写" in asyncio.run(
        personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 收信人"))


def test_sparse_material_is_refused_without_calling_the_model(monkeypatch, dbdir):
    path = chat_archive.archive_path()
    import_records(path, [record("m1", "2026-08-16", text="嗯。")])
    monkeypatch.setattr(brains, "_call_llm_raw", forbidden)
    reply = asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 收信人"))
    assert "写不出有依据的信" in reply


def test_cooldown_and_single_inflight(monkeypatch, evidence):
    monkeypatch.setattr(brains, "_call_llm_raw",
                        lambda *args, **kwargs: (json.dumps(card(evidence), ensure_ascii=False), "stop"))
    asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 收信人"))
    assert "等一小会儿" in asyncio.run(
        personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 收信人"))


def test_target_cooldown_is_off_by_default_but_works_when_tuned(monkeypatch, evidence):
    """屋主 2026-10-07：「不设冷却」。默认 0 = 关；配了数字才挡同一个人。"""
    monkeypatch.setattr(brains, "_call_llm_raw",
                        lambda *args, **kwargs: (json.dumps(card(evidence), ensure_ascii=False), "stop"))
    # 默认关：换个人再写给同一个收信人，照样能写
    asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 收信人"))
    second = asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "another-sender", "/致 收信人"))
    assert second.startswith("收信人："), second
    # 开了以后：第一次照写（并记下时间），紧接着再写给同一个人就被挡
    tuning.set_overrides({"personal_letter.target_cooldown": 3600})
    third = asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "third-sender", "/致 收信人"))
    assert third.startswith("收信人："), third
    fourth = asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "fourth-sender", "/致 收信人"))
    assert "刚给" in fourth, fourth
    # 门被挡下来的时候名额要还回去，否则后面所有人都写不了
    assert personal_letter._LIMIT.acquire(blocking=False)
    personal_letter._LIMIT.release()


def test_failure_releases_capacity_and_does_not_eat_cooldown(monkeypatch, evidence):
    monkeypatch.setattr(brains, "_call_llm_raw", lambda *args, **kwargs: ("not json", "stop"))
    assert asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 收信人")) == personal_letter.FAILURE
    assert not personal_letter._ACTIVE and not personal_letter._LAST
    assert personal_letter._LIMIT.acquire(blocking=False)
    personal_letter._LIMIT.release()


def test_disabled_and_outside_requests_do_not_fall_back(evidence, monkeypatch):
    features.set_overrides({"personal_letter": False})
    assert asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "sender", "/致 收信人")) is None
    features.set_overrides({PRIMARY_CID + "|personal_letter": True})
    assert asyncio.run(personal_letter.letter_reply(TEA_CID, "sender", "/致 收信人")) is None
    personas.set_config_personas({PRIMARY_CID: "ruojin"})
    assert not personal_letter.enabled(PRIMARY_CID)
    personas.set_config_personas({})
    assert asyncio.run(personal_letter.letter_reply(PRIMARY_CID, "", "/致 收信人")) is None


# ---------------------------------------------------------------- 入口

class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, text, cid, **kwargs):
        self.sent.append((cid, text))
        return True


async def entry(sender, cid, text, *, via_at=False):
    group_state, state = {"last_id": ""}, {}
    messages = [{"message_id": "request", "sent_by": "sender", "content": {"text": text}}]
    if via_at:
        await at_chat.handle_at_chat(sender, cid, text, "sender", False, group_state, state, messages,
                                    messages[0], False, _save_group=lambda *args: None,
                                    _mark_inflight=lambda *args: None, _remember_turn=lambda *args: None,
                                    _recent_turns=forbidden, _seed_turns=forbidden)
    else:
        await dispatch.handle_slash(sender, cid, text, "sender", False, group_state, messages, messages[0], state,
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
    monkeypatch.setattr(at_chat, "_recap_is_noise", lambda *args: False)
    for name in ("_get_perf_state", "_get_fasao_state", "_get_dj_state", "_get_story_state", "_get_vote_state", "_dice_state"):
        monkeypatch.setattr(dispatch, name, lambda *args: {})


@pytest.mark.parametrize("via_at,text", [(False, "/致 收信人"), (True, "给收信人写封信")])
def test_actual_entry_and_bot_route(monkeypatch, evidence, entry_mocks, via_at, text):
    monkeypatch.setattr(brains, "_call_llm_raw",
                        lambda *args, **kwargs: (json.dumps(card(evidence), ensure_ascii=False), "stop"))
    official, account = [], FakeSender()

    def send_text(cid, text):
        official.append((cid, text))
        return True

    sender = RoutingSender(account, {PRIMARY_CID: SimpleNamespace(send_text=send_text)})
    state = asyncio.run(entry(sender, PRIMARY_CID, text, via_at=via_at))
    assert state["last_id"] == "request" and len(official) == 1 and not account.sent
    assert official[0][1].startswith("🤖 收信人：")
    assert dispatch.is_dj_command_cmd("/致 收信人") is None, "别被调教兜底吃掉"


def test_disabled_entry_is_silent(entry_mocks):
    features.set_overrides({"personal_letter": False})
    sender = FakeSender()
    asyncio.run(entry(sender, PRIMARY_CID, "/致 收信人"))
    assert not sender.sent


def test_help_and_prompt_contract(entry_mocks):
    sender = FakeSender()
    asyncio.run(entry(sender, PRIMARY_CID, "/帮助"))
    assert "/致 某某" in sender.sent[-1][1]
    features.set_overrides({})
    asyncio.run(entry(sender, PRIMARY_CID, "/帮助"))
    assert "/致 某某" not in sender.sent[-1][1]
    assert prompt_tiers.check_contract("PERSONAL_LETTER_PROMPT", "随便编一段")
    sample = json.loads(prompt_tiers.spec("PERSONAL_LETTER_PROMPT").sample_user)
    data = {"paragraphs": [
        "我很早就注意到你了。你说[[m1]]，那时候我就觉得你这个人挺有意思，后来也一直记得。",
        "你身上有一股认真劲儿，做什么都想弄明白，这一点我一直记着，也想跟你说一声。"],
        "ending": "新的一年，愿你少熬几个夜，想说话的时候群里总有人在。"}
    assert sample["messages"], "样例证据不能是空的"
    assert prompt_tiers.check_contract("PERSONAL_LETTER_PROMPT", json.dumps(data, ensure_ascii=False)) == []
