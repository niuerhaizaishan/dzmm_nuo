import asyncio
import json
import sqlite3
import threading
import types
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer

import pytest

from admin import dashboard, messages_ui
from runtime import chat_archive as archive
from runtime import chat_archive_query as query
from runtime.ctx import PRIMARY_CID


def historical(mid="m1", *, uid="user-1", name="旧昵称", text="梦见下雨", stamp="2026-07-27 04:44:58.651+00", kind="text"):
    return {"id": mid, "uid": uid, "name": name, "text": text, "sent_at": stamp, "type": kind, "bj": "导出显示时间"}


def live(mid="m1", *, uid="user-1", text="梦见下雨", kind="text", stamp="2026-07-27T04:44:58.651Z"):
    return {"message_id": mid, "sent_by": uid, "sent_at": stamp,
            "chatroom_id": PRIMARY_CID, "content": {"type": kind, "text": text}}


def write_history(path, records):
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8-sig")


def test_import_repeat_and_live_echo_keep_original_name(dbdir):
    source, database = dbdir / "history.jsonl", dbdir / "archive.db"
    write_history(source, [historical(), historical("m2", name="新昵称")])
    first = archive.import_jsonl(source, group_id=PRIMARY_CID, path=database)
    assert first["records"] == first["inserted"] == 2
    again = archive.import_jsonl(source, group_id=PRIMARY_CID, path=database)
    assert again["inserted"] == 0 and again["duplicates"] == 2
    echo = archive.append_batch([live()], group_id=PRIMARY_CID, source="live_push", names={"user-1": "当前名字"}, path=database)
    assert echo == {"inserted": 0, "duplicates": 1, "invalid": 0}
    data = query.search({"uid": "user-1"}, path=database)
    assert {row["sender_name"] for row in data["rows"]} == {"旧昵称", "新昵称"}
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM imports").fetchone()[0] == 1
        assert connection.execute("SELECT source_file_sha256 FROM messages LIMIT 1").fetchone()[0] == first["sha256"]


def test_historical_import_is_atomic_on_bad_record(dbdir):
    source, database = dbdir / "history.jsonl", dbdir / "archive.db"
    write_history(source, [historical(), {**historical("m2"), "sent_at": "bad-time"}])
    with pytest.raises(ValueError, match="invalid_record_at_line_2"):
        archive.import_jsonl(source, group_id=PRIMARY_CID, path=database)
    assert query.search(path=database)["total"] == 0
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM imports").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["text", "system", "sticker", "image", "voice", "video", "share", "recalled", "deleted"])
def test_import_preserves_nontext_and_status_types(dbdir, kind):
    source, database = dbdir / "history.jsonl", dbdir / "archive.db"
    write_history(source, [historical(kind=kind)])
    archive.import_jsonl(source, group_id=PRIMARY_CID, path=database)
    record = query.search(path=database)["rows"][0]
    assert record["message_type"] == kind
    assert record["name_source"] == "historical_export"


def test_only_primary_group_can_be_imported_or_captured(dbdir):
    database = dbdir / "archive.db"
    with pytest.raises(ValueError, match="outside_archive_scope"):
        archive.import_jsonl(dbdir / "missing.jsonl", group_id="tea", path=database)
    assert archive.append_batch([live()], group_id="tea", source="live_push", path=database)["inserted"] == 0
    assert not database.exists()
    result = archive.append_batch([{**live(), "chatroom_id": "tea"}, {**live("private"), "group_id": "private"}, live("valid")],
                                  group_id=PRIMARY_CID, source="live_http", path=database)
    assert result == {"inserted": 1, "duplicates": 0, "invalid": 2}


def test_live_keeps_uid_names_metadata_and_genuine_repeats(dbdir):
    database = dbdir / "archive.db"
    message = live()
    message["content"]["replyTo"] = {"id": "earlier"}
    message["sender_name"] = "消息昵称"
    result = archive.append_batch([message, live("repeat"), live("other", uid="user-2")],
                                  group_id=PRIMARY_CID, source="live_push", names={"user-1": "成员缓存"}, path=database)
    assert result["inserted"] == 3
    records = {row["message_id"]: row for row in query.search(path=database)["rows"]}
    assert records["m1"]["sender_name"] == "消息昵称" and records["m1"]["name_source"] == "message"
    assert records["repeat"]["sender_name"] == "成员缓存" and records["repeat"]["name_source"] == "member_cache"
    assert records["other"]["sender_name"] == "" and records["other"]["name_source"] == "unknown"
    with closing(sqlite3.connect(database)) as connection:
        assert json.loads(connection.execute("SELECT content_json FROM messages WHERE message_id='m1'").fetchone()[0])["replyTo"]["id"] == "earlier"


def test_queries_use_beijing_dates_literal_search_and_stable_uid(dbdir):
    database = dbdir / "archive.db"
    messages = [live("before", stamp="2026-10-06T15:59:59Z"), live("start", stamp="2026-10-06T16:00:00Z", text="100%_测试"),
                live("end", stamp="2026-10-07T15:59:59Z", uid="user-2"), live("after", stamp="2026-10-07T16:00:00Z")]
    archive.append_batch(messages, group_id=PRIMARY_CID, source="live_http", names={"user-1": "同名", "user-2": "同名"}, path=database)
    result = query.search({"start": "2026-10-07", "end": "2026-10-07"}, path=database)
    assert [row["message_id"] for row in result["rows"]] == ["end", "start"]
    assert query.search({"name": "同名"}, path=database)["matches"] == 4
    assert query.search({"uid": "user-2"}, path=database)["matches"] == 1
    assert query.search({"q": "%_"}, path=database)["matches"] == 1
    assert query.search({"q": "' OR 1=1 --"}, path=database)["matches"] == 0


@pytest.mark.parametrize("filters", [{"start": "bad"}, {"page": "-1"}, {"page": "bad"},
                                     {"q": "x" * 257}, {"start": "2026-10-08", "end": "2026-10-07"}])
def test_query_rejects_bad_filters_without_creating_db(dbdir, filters):
    database = dbdir / "archive.db"
    with pytest.raises(ValueError):
        query.search(filters, path=database)
    assert not database.exists()


def test_reader_is_bounded_readonly_and_missing_db_is_not_created(dbdir):
    database = dbdir / "archive.db"
    assert not query.search(path=database)["available"]
    assert not database.exists()
    archive.append_batch([live("m" + str(number)) for number in range(121)], group_id=PRIMARY_CID, source="live_push", path=database)
    before = database.read_bytes()
    assert len(query.search(path=database)["rows"]) == 50
    assert len(query.search({"page": 3}, path=database)["rows"]) == 21
    assert query.search({"page": 4}, path=database)["rows"] == []
    assert database.read_bytes() == before


def test_capture_switch_scope_and_storage_failure_isolation(monkeypatch, dbdir):
    database = dbdir / "archive.db"
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(database))
    monkeypatch.delenv("DZMM_CHAT_ARCHIVE", raising=False)
    asyncio.run(archive.capture(PRIMARY_CID, [live()], source="live_push"))
    assert not database.exists()
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE", "on")
    asyncio.run(archive.capture("tea", [live()], source="live_push"))
    assert not database.exists()
    asyncio.run(archive.capture(PRIMARY_CID, [live()], source="live_push"))
    assert query.search(path=database)["total"] == 1
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("private message should not be logged")
    monkeypatch.setattr(archive, "append_batch", broken)
    warnings = []
    monkeypatch.setattr(archive, "_warn", warnings.append)
    asyncio.run(archive.capture(PRIMARY_CID, [live("new")], source="live_http"))
    assert warnings == ["OperationalError"]


def test_html_escapes_names_text_and_filters(dbdir):
    database = dbdir / "archive.db"
    source = dbdir / "history.jsonl"
    write_history(source, [historical(name="<img src=x>", text="<script>alert(1)</script>")])
    archive.import_jsonl(source, group_id=PRIMARY_CID, path=database)
    page = messages_ui.render(path=database)
    assert "<img src=x>" not in page and "<script>alert(1)</script>" not in page
    assert "&lt;img src=x&gt;" in page and "&lt;script&gt;" in page
    assert "未核实其生效时间" in page
    page = messages_ui.render({"q": "' autofocus onfocus='alert(1)"}, path=database)
    assert "&#x27; autofocus" in page


def test_archive_http_route_requires_existing_admin_session(monkeypatch, dbdir):
    database = dbdir / "archive.db"
    archive.append_batch([live(text="PRIVATE_ARCHIVED_TEXT")], group_id=PRIMARY_CID, source="live_http", path=database)
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(database))
    monkeypatch.setenv("DZMM_ADMIN_PASSWORD", "archive-test-password")
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    target = "http://127.0.0.1:" + str(server.server_address[1]) + "/messages"
    try:
        with opener.open(target) as response:
            assert "PRIVATE_ARCHIVED_TEXT" not in response.read().decode()
        sid = dashboard.new_session()
        request = urllib.request.Request(target, headers={"Cookie": dashboard.SESSION_COOKIE + "=" + sid})
        with opener.open(request) as response:
            assert "PRIVATE_ARCHIVED_TEXT" in response.read().decode()
            assert response.headers["Cache-Control"] == "no-store"
        dashboard.drop_session(sid)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("via_push", [True, False])
def test_mainloop_archives_all_read_messages_before_cursor_and_self_filters(monkeypatch, dbdir, via_push):
    from runtime import mainloop
    database = dbdir / "archive.db"
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE", "on")
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(database))
    messages = [live("old", uid="bot-self"), live("new", kind="image", text="")]
    subscriber = types.SimpleNamespace(is_connected=False, drain_private=lambda: {},
                                       drain=lambda cid: messages if via_push else [],
                                       need_resync=lambda cid: False, clear_resync=lambda cid: None)
    fake_bot = types.SimpleNamespace(
        FAST_INTERVAL=0, ROOT=dbdir, _profile_sync=types.SimpleNamespace(scan_load=lambda: {}),
        set_persona_cid=lambda cid: None, _set_active_cid=lambda cid: None,
        _dice_state=lambda: {}, _ctx=types.SimpleNamespace(captcha_active=lambda: False),
        message_lag=lambda msgs: 0, _remember_turn=lambda *args: None,
        is_self=lambda uid: uid == "bot-self", get_name=lambda uid: "昵称",
        _load_member_names=lambda cid: None, MUTE_CIDS={PRIMARY_CID},
        _save_group=lambda cid, state: None, get_messages=lambda *args: messages,
        PLATFORM_READ_TIMEOUT=1)
    asyncio.run(mainloop.run_main_loop(0, False, True, groups=[PRIMARY_CID], sender=None,
                                     state={PRIMARY_CID: {"last_id": "old"}}, subscriber=subscriber,
                                     is_primary=False, B=fake_bot))
    records = query.search(path=database)["rows"]
    assert {record["message_id"] for record in records} == {"old", "new"}
    assert {record["source"] for record in records} == {"live_push" if via_push else "live_http"}
