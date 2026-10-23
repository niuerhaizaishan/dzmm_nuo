import asyncio
from datetime import datetime, timezone

import pytest

import dzmm
from runtime import chat_archive as archive
from runtime import chat_archive_query as query
from runtime import recap
from runtime.ctx import PRIMARY_CID


def message(mid, stamp, text="昨晚一起讨论了旅行计划", kind="text"):
    return {"message_id": mid, "sent_by": "member", "sender_name": "历史昵称",
            "sent_at": stamp, "content": {"type": kind, "text": text}}


@pytest.mark.parametrize("before", [
    "2026-10-18 16:00:00", "2026-10-18T16:00:00Z",
    "2026-10-18 16:00:00+00", "2026-10-18T16:00:00+00:00",
    "2026-10-19T00:00:00+08:00",
])
def test_history_cursor_is_always_a_valid_utc_datetime(monkeypatch, before):
    calls = []
    monkeypatch.setattr(dzmm, "api", lambda path, payload, cookies, timeout: calls.append(payload) or {})
    dzmm.get_messages("group", before=before, cookies={})
    assert calls[0]["json"]["before"] == "2026-10-18T16:00:00Z"
    assert "meta" not in calls[0]


def test_latest_fetch_keeps_the_undefined_cursor(monkeypatch):
    calls = []
    monkeypatch.setattr(dzmm, "api", lambda path, payload, cookies, timeout: calls.append(payload) or {})
    dzmm.get_messages("group", cookies={})
    assert calls[0]["json"]["before"] is None
    assert calls[0]["meta"]["values"]["before"] == ["undefined"]


def test_history_paging_normalizes_fractional_utc_offsets(monkeypatch):
    calls = []
    pages = [
        [message("middle", "2026-10-18 15:00:00.123+00")],
        [message("start", "2026-10-18T10:00:00Z")],
    ]

    def api(path, payload, cookies, timeout):
        calls.append(payload["json"]["before"])
        return {"messages": pages.pop(0)}

    monkeypatch.setattr(dzmm, "api", api)
    messages = dzmm.get_messages_since("group", "2026-10-18 10:00:00", cookies={},
                                       until_time="2026-10-18 16:00:00")
    assert calls == ["2026-10-18T16:00:00Z", "2026-10-18T15:00:00.123000Z"]
    assert [item["message_id"] for item in messages] == ["start", "middle"]


def test_history_window_compares_instants_instead_of_string_formats(monkeypatch):
    page = [message("before", "2026-10-18 09:59:59+00"),
            message("start", "2026-10-18T18:00:00+08:00"),
            message("inside", "2026-10-18T15:59:59.999Z"),
            message("end", "2026-10-18T16:00:00Z")]
    monkeypatch.setattr(dzmm, "get_messages", lambda *args: page)
    messages = dzmm.get_messages_since("group", "2026-10-18 10:00:00+00", cookies={},
                                       until_time="2026-10-18 16:00:00+00")
    assert [item["message_id"] for item in messages] == ["start", "inside"]


@pytest.mark.parametrize("query_text,start_hour,end_hour,label", [
    ("昨天晚上发生了什么", 18, 24, "昨天晚上"),
    ("昨晚发生了什么", 18, 24, "昨天晚上"),
    ("昨日晚间", 18, 24, "昨天晚上"),
    ("昨天早上", 0, 9, "昨天早上"),
    ("昨天上午", 0, 12, "昨天上午"),
    ("昨天中午", 11, 14, "昨天中午"),
    ("昨天下午", 12, 18, "昨天下午"),
    ("昨天", 0, 24, "昨天"),
])
def test_yesterday_subperiods_have_precise_boundaries(monkeypatch, query_text, start_hour, end_hour, label):
    fixed = datetime(2026, 10, 19, 10, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(recap, "datetime", FixedDatetime)
    since, until, actual_label = recap._recap_window(PRIMARY_CID, "requester", query_text)
    day0 = datetime(2026, 10, 18).timestamp()
    assert (since, until, actual_label) == (day0 + start_hour * 3600, day0 + end_hour * 3600, label)


@pytest.mark.parametrize("stamp", ["2026-10-18 16:00:00+00", "2026-10-18T16:00:00Z",
                                   "2026-10-18T16:00:00+00:00", "2026-10-19T00:00:00+08:00"])
def test_recap_timestamp_accepts_platform_and_archive_formats(stamp):
    assert recap._msg_ts({"sent_at": stamp}) == datetime(2026, 10, 18, 16, tzinfo=timezone.utc).timestamp()


def test_archive_window_is_readonly_bounded_and_preserves_sender_metadata(dbdir):
    database = dbdir / "archive.db"
    since = archive.timestamp("2026-10-18T10:00:00Z")
    until = archive.timestamp("2026-10-18T16:00:00Z")
    assert query.recap_messages(PRIMARY_CID, since, until, path=database) == []
    assert not database.exists()
    rows = [message("before", "2026-10-18T09:59:59Z"), message("start", "2026-10-18T10:00:00Z"),
            message("middle", "2026-10-18T12:00:00Z"), message("late", "2026-10-18T15:00:00Z"),
            message("end", "2026-10-18T16:00:00Z")]
    archive.append_batch(rows, group_id=PRIMARY_CID, source="live_http", path=database)
    before = database.read_bytes()
    selected = query.recap_messages(PRIMARY_CID, since, until, limit=2, path=database)
    assert [item["message_id"] for item in selected] == ["middle", "late"]
    assert selected[0]["sender_name"] == "历史昵称"
    assert selected[0]["sent_by"] == "member"
    assert selected[0]["content"]["text"] == rows[2]["content"]["text"]
    assert query.recap_messages("tea", since, until, path=database) == []
    assert database.read_bytes() == before


@pytest.fixture
def recap_environment(monkeypatch, state, dbdir):
    since = archive.timestamp("2026-10-18T10:00:00Z")
    until = archive.timestamp("2026-10-18T16:00:00Z")
    database = dbdir / "archive.db"
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(database))
    monkeypatch.setattr(recap, "_tune_scope", lambda *args: True)
    monkeypatch.setattr(recap, "_recap_window", lambda *args: (since, until, "昨天晚上"))
    monkeypatch.setattr(recap, "get_name", lambda uid: "提问人" if uid == "requester" else uid)
    monkeypatch.setattr(recap, "get_full_name", recap.get_name)
    monkeypatch.setattr(recap, "is_self", lambda uid: uid == "me")
    monkeypatch.setattr(recap, "log", lambda *args: None)
    transcripts = []

    def generate(transcript, *args):
        transcripts.append(transcript)
        return "💬 讨论了旅行安排\n🎭 一起交流计划\n📌 没有待回复事项"

    monkeypatch.setattr(recap, "generate_recap", generate)
    return database, transcripts


def test_auth_recap_uses_archive_without_platform_calls(monkeypatch, recap_environment):
    database, transcripts = recap_environment
    archive.append_batch([message("saved", "2026-10-18T12:00:00Z")],
                         group_id=PRIMARY_CID, source="live_http", path=database)
    calls = []
    monkeypatch.setattr(recap, "get_messages_since", lambda *args, **kwargs: calls.append("history") or [])
    monkeypatch.setattr(recap, "_load_member_names", lambda *args: calls.append("members"))
    reply = asyncio.run(recap.recap_absent(PRIMARY_CID, "requester", "昨天晚上发生了什么"))
    assert calls == []
    assert "本次读取本地归档 1 条" in reply
    assert "历史昵称" in transcripts[0] and "旅行计划" in transcripts[0]


@pytest.mark.parametrize("availability", ["missing", "empty_window", "broken", "other_group"])
def test_unavailable_or_empty_archive_falls_back_to_platform(monkeypatch, recap_environment, availability):
    database, transcripts = recap_environment
    cid = PRIMARY_CID
    if availability == "empty_window":
        archive.append_batch([message("old", "2026-10-17T12:00:00Z")],
                             group_id=PRIMARY_CID, source="live_http", path=database)
    elif availability == "broken":
        database.write_bytes(b"invalid sqlite file")
    elif availability == "other_group":
        cid = "tea"
        monkeypatch.setattr(recap, "recap_messages", lambda *args: pytest.fail("another group read auth archive"))
    calls = []

    def fetch(group_id, since, **kwargs):
        calls.append((group_id, since, kwargs))
        return [message("remote", "2026-10-18T12:00:00Z")]

    monkeypatch.setattr(recap, "get_messages_since", fetch)
    monkeypatch.setattr(recap, "_load_member_names", lambda *args: None)
    reply = asyncio.run(recap.recap_absent(cid, "requester", "昨天晚上发生了什么"))
    assert calls == [(cid, "2026-10-18T10:00:00Z", {"max_count": 600, "until_time": "2026-10-18T16:00:00Z"})]
    assert "群里 1 条" in reply and transcripts


def test_platform_failure_does_not_claim_there_were_no_messages(monkeypatch, recap_environment):
    def failure(*args, **kwargs):
        raise RuntimeError("captcha_required")

    monkeypatch.setattr(recap, "get_messages_since", failure)
    reply = asyncio.run(recap.recap_absent(PRIMARY_CID, "requester", "昨天晚上发生了什么"))
    assert "一条消息都没有" not in reply
    assert "翻记录" in reply


def test_archived_media_is_not_mistaken_for_an_empty_evening(monkeypatch, recap_environment):
    database, transcripts = recap_environment
    archive.append_batch([message("photo", "2026-10-18T12:00:00Z", text="", kind="image")],
                         group_id=PRIMARY_CID, source="live_http", path=database)
    reply = asyncio.run(recap.recap_absent(PRIMARY_CID, "requester", "昨天晚上发生了什么"))
    assert "[图片]" in transcripts[0]
    assert "一条消息都没有" not in reply
