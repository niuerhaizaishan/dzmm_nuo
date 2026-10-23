"""群收图真调用：当前成员、引用原图、持久化、重复/错误/重放和分派。"""
import asyncio
from io import BytesIO

from PIL import Image
import pytest

from runtime import gallery as G


def png(color="pink"):
    output = BytesIO()
    Image.new("RGB", (12, 8), color).save(output, format="PNG")
    return output.getvalue()


def message():
    return {"message_id": "command1", "content": {"type": "text", "text": "/保存 姐妹日常",
        "reference": {"id": "photo1", "content": {"type": "image", "text": "https://forged.invalid/p.png"}}}}


class Platform:
    def __init__(self):
        self.members = {"member"}
        self.calls = []
        self.image = {"message_id": "photo1", "chatroom_id": G.PRIMARY_CID,
                      "content": {"type": "image", "text": "https://rls.cheggpt.com/real.png"}}

    async def member(self, uid):
        self.calls.append(("member", uid))
        if uid not in self.members:
            raise G.GalleryError("姐妹图集仅限当前妹子群成员使用。")
        return "测试群友"

    async def original(self, mid, sent_at=""):
        self.calls.append(("original", mid))
        return self.image

    async def download(self, url):
        self.calls.append(("download", url))
        return png()


def run(service, cmd="/保存 姐妹日常", *, uid="member", cid=G.PRIMARY_CID, msg=None, dry=False):
    return asyncio.run(service.reply(cid, uid, cmd, message() if msg is None else msg, dry=dry))


@pytest.mark.parametrize("category", G.CATEGORIES)
@pytest.mark.parametrize("url_field", ["text", "url"])
def test_save_original_bytes_survive_restart_and_deduplicate(dbdir, category, url_field):
    path = dbdir / "gallery.db"
    platform = Platform()
    original_url = platform.image["content"].pop("text")
    platform.image["content"][url_field] = original_url
    service = G.Service(G.Store(path), platform)
    assert "已保存到「" + category in run(service, "/保存 " + category)
    assert ("download", "https://rls.cheggpt.com/real.png") in platform.calls
    assert not any("forged" in str(call) for call in platform.calls)
    restarted = G.Store(path)
    rows = restarted.list("member")
    assert len(rows) == 1 and rows[0]["category"] == category
    assert restarted.image(rows[0]["id"])["data"] == png()
    assert rows[0]["filename"].endswith(".png")
    assert "不会重复收录" in run(G.Service(restarted, platform), "/保存 " + category)
    assert len(restarted.list("member")) == 1


def test_members_only_and_no_private_database_created(dbdir):
    platform = Platform()
    path = dbdir / "gallery.db"
    service = G.Service(G.Store(path), platform)
    assert "仅限" in run(service, uid="outsider")
    assert "仅限" in run(service, cid="other-room")
    assert not path.exists()
    assert not any(call[0] == "original" for call in platform.calls)


def test_link_command_no_media_fetch(dbdir):
    platform = Platform()
    service = G.Service(G.Store(dbdir / "gallery.db"), platform)
    assert run(service, "/漫画") == G.CARD_INTRO
    assert "https://" not in G.CARD_INTRO
    assert not any(call[0] in ("download", "original") for call in platform.calls)
    assert not service.store.path.exists()


@pytest.mark.parametrize("cmd,msg,expected", [
    ("/保存", message(), "可选图集"),
    ("/保存 其它", message(), "可选图集"),
    ("/保存 姐妹日常", {"content": {}}, "先回复"),
])
def test_usage_does_not_download_or_write(dbdir, cmd, msg, expected):
    platform = Platform()
    service = G.Service(G.Store(dbdir / "gallery.db"), platform)
    assert expected in run(service, cmd, msg=msg)
    assert not any(call[0] == "download" for call in platform.calls)
    assert not service.store.path.exists()


def test_text_and_cross_group_reference_not_saved(dbdir):
    platform = Platform()
    service = G.Service(G.Store(dbdir / "gallery.db"), platform)
    platform.image["content"]["type"] = "text"
    assert "不是可保存的图片" in run(service)
    platform.image["chatroom_id"] = "other-room"
    assert "只能保存妹子群" in run(service)
    assert not service.store.path.exists()


def test_leaving_during_download_denied_and_dry_run_does_not_write(dbdir):
    platform = Platform()
    service = G.Service(G.Store(dbdir / "gallery.db"), platform)
    assert "[dry]" in run(service, dry=True)
    assert not service.store.path.exists()
    async def download(url):
        platform.members.clear()
        return png()
    platform.download = download
    assert "仅限" in run(service)
    assert not service.store.path.exists()


def test_invalid_image_and_size_limits(dbdir):
    store = G.Store(dbdir / "gallery.db")
    for data in (b"not-an-image", png()[:40], b"x" * (G.MAX_BYTES + 1)):
        with pytest.raises(G.GalleryError):
            store.save("姐妹日常", data, uploader="member", name="群友")
    assert not store.path.exists()


def test_favorites_are_private_persistent_and_do_not_remove_original(dbdir):
    store = G.Store(dbdir / "gallery.db")
    mid, _ = store.save("姐妹日常", png(), uploader="member", name="群友")
    store.favorite("member", mid, True)
    store = G.Store(store.path)
    assert store.list("member")[0]["saved"] == 1
    assert store.list("other-member")[0]["saved"] == 0
    store.favorite("member", mid, False)
    assert store.image(mid)["data"] == png()


def test_delete_restore_preserves_bytes_identity_favorites_and_deduplication(dbdir):
    store = G.Store(dbdir / "gallery.db")
    mid, _ = store.save("姐妹日常", png(), uploader="member", name="群友", title="下午茶")
    other, _ = store.save("个人立绘", png(), uploader="member", name="群友")
    store.favorite("member", mid, True)
    service = G.Service(store, Platform())
    for cmd in (f"/删除图片 {mid}", f"/恢复图片 {mid}"):
        assert "仅限" in run(service, cmd, uid="outsider")
        assert "仅限" in run(service, cmd, cid="other-room")
        assert "[dry]" in run(service, cmd, dry=True)
    assert store.image(mid)
    assert "已删除" in run(service, f"/删除图片 {mid}")
    assert "此前已删除" in run(service, f"/删除图片 {mid}")
    assert G.Store(store.path).image(mid) is None
    assert [r["id"] for r in store.list("member")] == [other]
    with pytest.raises(G.GalleryError):
        store.rename(mid, "不能修改已删除图片")
    with pytest.raises(G.GalleryError):
        store.favorite("member", mid, True)
    with pytest.raises(G.GalleryError, match="/恢复图片"):
        store.save("姐妹日常", png(), uploader="member", name="群友")
    assert "已恢复" in run(service, f"/恢复图片 {mid}")
    assert "已在画廊中" in run(service, f"/恢复图片 {mid}")
    assert store.image(mid)["data"] == png()
    row = next(r for r in store.list("member") if r["id"] == mid)
    assert row["saved"] == 1 and row["title"] == "下午茶"
    assert store.save("姐妹日常", png(), uploader="member", name="群友") == (mid, False)
    assert "图片不存在" in run(service, "/删除图片 999")
    for argument in ("", "0", "-1", "1 2", "1.0", "下午茶", "9" * 40):
        assert "图片编号" in run(service, "/删除图片 " + argument)
    assert len(store.list("member")) == 2


def test_partial_roster_or_left_member_never_uses_historical_cache(monkeypatch):
    platform = G.GalleryPlatform()
    for result in ({"members": []}, {"members": [{"id": "member"}], "hasMore": True},
                   {"members": [{"id": "member", "isBot": True}]}, {"members": [{"id": "other"}]}):
        monkeypatch.setattr(G.dzmm, "api", lambda *a, **kw: result)
        with pytest.raises(G.GalleryError):
            asyncio.run(platform.member("member"))


def test_move_preserves_identity_bytes_metadata_and_favorites(dbdir):
    import sqlite3
    store = G.Store(dbdir / "gallery.db")
    mid, _ = store.save("个人立绘", png(), uploader="member", name="群友", title="下午茶", source="original")
    store.favorite("member", mid, True)
    with sqlite3.connect(store.path) as conn:
        before = conn.execute("SELECT * FROM gallery_images WHERE id=?", (mid,)).fetchone()
        columns = [r[1] for r in conn.execute("PRAGMA table_info(gallery_images)")]
    result = store.move(mid, "姐妹日常")
    assert result["changed"] and result["previous_category"] == "个人立绘"
    assert result["id"] == mid and result["category"] == "姐妹日常"
    with sqlite3.connect(store.path) as conn:
        after = conn.execute("SELECT * FROM gallery_images WHERE id=?", (mid,)).fetchone()
    assert {k:v for k,v in zip(columns,before) if k!="category"} == {k:v for k,v in zip(columns,after) if k!="category"}
    restarted = G.Store(store.path)
    assert restarted.list("member")[0]["saved"] == 1
    assert restarted.image(mid)["data"] == png()
    assert not restarted.move(mid, "姐妹日常")["changed"]
    assert restarted.move(mid, "个人立绘")["changed"]
    for invalid in ("其他", "", None):
        with pytest.raises(G.GalleryError): restarted.move(mid, invalid)
    restarted.set_deleted(mid, True, "member")
    with pytest.raises(G.GalleryError, match="不存在或已删除"): restarted.move(mid, "姐妹日常")


@pytest.mark.parametrize("deleted", [False, True])
def test_move_duplicate_target_never_overwrites_or_merges(dbdir, deleted):
    store = G.Store(dbdir / "gallery.db")
    mid, _ = store.save("个人立绘", png(), uploader="member", name="群友", title="原名称")
    target, _ = store.save("姐妹日常", png(), uploader="other", name="群友", title="目标名称")
    store.favorite("member", mid, True)
    store.set_deleted(target, deleted, "member")
    with pytest.raises(G.GalleryConflict, match="未移动"):
        store.move(mid, "姐妹日常")
    source = next(r for r in store.list("member") if r["id"] == mid)
    assert source["category"] == "个人立绘" and source["saved"] == 1 and source["title"] == "原名称"
    assert (store.image(target) is None) == deleted


def test_move_command_permissions_usage_dry_run_and_idempotence(dbdir):
    store = G.Store(dbdir / "gallery.db")
    mid, _ = store.save("个人立绘", png(), uploader="member", name="群友", title="下午茶")
    platform = Platform()
    service = G.Service(store, platform)
    cmd = f"/移动图片 {mid} 姐妹日常"
    assert "仅限" in run(service, cmd, uid="outsider")
    assert "仅限" in run(service, cmd, cid="other-room")
    assert "[dry]" in run(service, cmd, dry=True)
    assert store.list("member")[0]["category"] == "个人立绘"
    assert "移动到「姐妹日常」" in run(service, cmd)
    assert "无需移动" in run(service, cmd)
    for arg in ("", "1", "1 其他", "1 姐妹日常 多余", "-1 姐妹日常", "0 姐妹日常", "9"*40+" 姐妹日常"):
        assert G.MOVE_USAGE == run(service, "/移动图片 " + arg)
    assert "不存在" in run(service, "/移动图片 999 姐妹日常")
    assert not any(c[0] in ("original", "download") for c in platform.calls)


@pytest.mark.parametrize("title", ["", " 万圣节限定版", " 万圣节 限定版"])
def test_save_category_alias_uses_canonical_album_and_preserves_title(dbdir, title):
    store = G.Store(dbdir / "gallery.db")
    service = G.Service(store, Platform())
    assert "已保存到「大开脑洞」" in run(service, "/保存 脑洞大开" + title)
    row = store.list("member")[0]
    assert row["category"] == "大开脑洞"
    if title: assert row["title"] == title.strip()
    assert "不会重复收录" in run(service, "/保存 大开脑洞" + title)
    assert len(store.list("member")) == 1
    assert "仅限" in run(service, "/保存 脑洞大开" + title, uid="outsider")


def test_alias_command_reference_recovery_checks_original_raw_command(dbdir):
    platform = Platform()
    image = platform.image
    text = "/保存 脑洞大开 限定版"
    async def original(mid, sent_at=""):
        if mid == "command1":
            return {**message(), "sent_by":"member", "chatroom_id":G.PRIMARY_CID,
                    "content":{**message()["content"], "text":text}}
        return image
    platform.original = original
    service = G.Service(G.Store(dbdir / "gallery.db"), platform)
    msg = {"message_id":"command1", "content":{"type":"text", "text":text}}
    assert "已保存到「大开脑洞」" in run(service, text, msg=msg)
    assert service.store.list("member")[0]["title"] == "限定版"


def test_move_alias_uses_same_album_without_creating_alias_category(dbdir):
    store = G.Store(dbdir / "gallery.db")
    mid, _ = store.save("个人立绘", png(), uploader="member", name="群友")
    store.favorite("member", mid, True)
    service = G.Service(store, Platform())
    assert "[dry]" in run(service, f"/移动图片 {mid} 脑洞大开", dry=True)
    assert store.list("member")[0]["category"] == "个人立绘"
    assert "移动到「大开脑洞」" in run(service, f"/移动图片 {mid} 脑洞大开")
    assert "无需移动" in run(service, f"/移动图片 {mid} 大开脑洞")
    row = store.list("member")[0]
    assert row["category"] == "大开脑洞" and row["saved"] == 1 and row["id"] == mid


def test_platform_session_failure_is_safe_and_retry_after_recovery_succeeds(monkeypatch, dbdir):
    logs = []
    monkeypatch.setattr(G, "log", logs.append)
    def expired(*args, **kwargs):
        raise RuntimeError("tRPC chatroom.getMembers: Invalid authentication credentials secret-response")
    monkeypatch.setattr(G.dzmm, "api", expired)
    platform = G.GalleryPlatform()
    fixture = Platform()
    platform.original, platform.download = fixture.original, fixture.download
    service = G.Service(G.Store(dbdir / "gallery.db"), platform)
    assert "登录状态已失效" in run(service)
    assert not service.store.path.exists() and not fixture.calls
    assert "secret-response" not in str(logs)
    monkeypatch.setattr(G.dzmm, "api", lambda *a, **kw: {"members":[{"id":"member"}]})
    assert "已保存到" in run(service)
    assert len(service.store.list("member")) == 1


def test_original_lookup_failure_is_distinct_from_not_found(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("tRPC chatroom.getMessages: Invalid authentication credentials secret-response")
    monkeypatch.setattr(G.Platform, "message", unavailable)
    with pytest.raises(G.GalleryUnavailable, match="登录状态已失效") as raised:
        asyncio.run(G.GalleryPlatform().original("photo1", "2026-10-21T00:00:00Z"))
    assert "secret-response" not in str(raised.value)
    async def not_found(*args, **kwargs):
        raise G.ModerationUnavailable("无法找到原消息，未执行操作")
    monkeypatch.setattr(G.Platform, "message", not_found)
    with pytest.raises(G.ModerationUnavailable, match="无法找到原消息"):
        asyncio.run(G.GalleryPlatform().original("photo1", "2026-10-21T00:00:00Z"))


def test_missing_push_reference_is_recovered_from_verified_command(dbdir):
    platform = Platform()
    image = platform.image
    async def original(mid, sent_at=""):
        if mid == "command1":
            return {**message(), "sent_by":"member", "chatroom_id":G.PRIMARY_CID}
        assert mid == "photo1"
        return image
    platform.original = original
    service = G.Service(G.Store(dbdir / "gallery.db"), platform)
    msg = {"message_id":"command1", "content":{"type":"text","text":"/保存 姐妹日常"}}
    assert "已保存" in run(service, msg=msg)
    assert len(service.store.list("member")) == 1
    async def forged(mid, sent_at=""):
        return {**message(), "sent_by":"someone-else"}
    platform.original = forged
    assert "无法核验" in run(service, msg=msg)


def test_picture_titles_optional_command_names_and_rename_persist(dbdir):
    store = G.Store(dbdir / "gallery.db")
    service = G.Service(store, Platform())
    assert "已保存" in run(service, "/保存 个人立绘 春日 立绘")
    row = store.list("member")[0]
    assert row["title"] == "春日 立绘"
    assert "不会重复收录" in run(service, "/保存 个人立绘 新名称")
    assert store.list("member")[0]["title"] == "春日 立绘"
    store.rename(row["id"], "改好的名字")
    assert G.Store(store.path).image(row["id"])["title"] == "改好的名字"
    assert G.download_name("坏/路径:*", "image/png") == "坏_路径__.png"
    with pytest.raises(G.GalleryError):
        store.rename(row["id"], "长" * 81)


def test_old_database_title_migration_preserves_images_and_favorites(dbdir):
    import sqlite3
    path = dbdir / "old.db"
    store = G.Store(path)
    mid, _ = store.save("个人立绘", png(), uploader="member", name="private", filename="旧名字.png")
    store.favorite("member", mid, True)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE gallery_images DROP COLUMN title")
        conn.execute("ALTER TABLE gallery_images DROP COLUMN deleted_at")
        conn.execute("ALTER TABLE gallery_images DROP COLUMN deleted_by")
    row = G.Store(path).list("member")[0]
    assert row["title"] == "旧名字" and row["saved"] == 1
    assert G.Store(path).image(mid)["data"] == png()


def test_old_image_uses_archive_only_as_time_index(monkeypatch, dbdir):
    import sqlite3
    from runtime import chat_archive, moderation_platform
    path = dbdir / "archive.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE messages(group_id TEXT,message_id TEXT,sent_at_original TEXT)")
        conn.execute("INSERT INTO messages VALUES(?,?,?)", (G.PRIMARY_CID,"old-photo","2026-10-20T00:00:00Z"))
    monkeypatch.setattr(chat_archive, "archive_path", lambda:path)
    called=[]
    async def read(self,cid,mid,stamp):
        called.append((cid,mid,stamp))
        return {"message_id":mid,"content":{"type":"image","url":"https://rls.cheggpt.com/real.png"}}
    monkeypatch.setattr(moderation_platform.Platform,"message",read)
    row=asyncio.run(G.GalleryPlatform().original("old-photo"))
    assert row["content"]["type"] == "image"
    assert called == [(G.PRIMARY_CID,"old-photo","2026-10-20T00:00:00Z")]


@pytest.mark.parametrize("url", ["http://rls.cheggpt.com/x", "https://127.0.0.1/x",
    "https://rls.cheggpt.com.attacker.invalid/x", "https://rls.cheggpt.com:8443/x",
    "https://user:password@rls.cheggpt.com/x"])
def test_arbitrary_url_not_fetched(monkeypatch, url):
    monkeypatch.setattr(G.requests, "get", lambda *a, **kw: pytest.fail("unexpected download"))
    with pytest.raises(G.GalleryError):
        asyncio.run(G.GalleryPlatform().download(url))


@pytest.mark.parametrize("status,oversize", [(302, False), (404, False), (200, True)])
def test_cdn_download_rejects_redirect_failures_and_oversize(monkeypatch, status, oversize):
    class Response:
        status_code = status
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def iter_content(self, size):
            assert size == 65536
            yield b"x" * (G.MAX_BYTES + 1) if oversize else png()
    def get(url, **kwargs):
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert "cookies" not in kwargs
        return Response()
    monkeypatch.setattr(G.requests, "get", get)
    with pytest.raises(G.GalleryError):
        asyncio.run(G.GalleryPlatform().download("https://rls.cheggpt.com/photo.png"))


def test_dispatch_handles_gallery_even_during_other_games(monkeypatch, dbdir):
    from runtime import dispatch as D
    calls = []
    class Sender:
        async def send(self, text, cid):
            calls.append((text, cid))
            return True
    service = G.Service(G.Store(dbdir / "gallery.db"), Platform())
    monkeypatch.setattr(D._gallery, "command_reply", service.reply)
    monkeypatch.setattr(D, "_feat_on", lambda *a: True)
    monkeypatch.setattr(D, "_get_perf_state", lambda *a: pytest.fail("must not enter other game"))
    msg = message()
    asyncio.run(D.handle_slash(Sender(), G.PRIMARY_CID, "/保存 姐妹日常", "member", False,
        {}, [msg], msg, {}, _save_group=lambda *a: None, _mark_inflight=lambda *a: None,
        _remember_turn=lambda *a: None, rank_summary=lambda: "", sync_profiles=lambda: ""))
    assert "已保存到「姐妹日常」" in calls[0][0]


@pytest.mark.parametrize("action", ["删除图片", "恢复图片", "移动图片"])
def test_delete_restore_dispatch_does_not_enter_other_games(monkeypatch, dbdir, action):
    from runtime import dispatch as D
    calls = []
    class Sender:
        async def send(self, text, cid):
            calls.append(text)
            return True
    store = G.Store(dbdir / "gallery.db")
    mid, _ = store.save("姐妹日常", png(), uploader="member", name="群友")
    store.set_deleted(mid, action == "恢复图片", "member")
    monkeypatch.setattr(D._gallery, "command_reply", G.Service(store, Platform()).reply)
    monkeypatch.setattr(D, "_feat_on", lambda *a: True)
    monkeypatch.setattr(D, "_get_perf_state", lambda *a: pytest.fail("must not enter other game"))
    cmd = f"/{action} {mid}" + (" 个人立绘" if action == "移动图片" else "")
    msg = {"message_id":"command", "content":{"type":"text", "text":cmd}}
    asyncio.run(D.handle_slash(Sender(), G.PRIMARY_CID, cmd, "member", False,
        {}, [msg], msg, {}, _save_group=lambda *a: None, _mark_inflight=lambda *a: None,
        _remember_turn=lambda *a: None, rank_summary=lambda: "", sync_profiles=lambda: ""))
    assert ("已删除" if action == "删除图片" else "移动到" if action == "移动图片" else "已恢复") in calls[0]


def test_webhook_keeps_reply_and_image_payload():
    import webhook_server as W
    result = W.to_internal({"message": {"message_id": "cmd", "chat": {"id": G.PRIMARY_CID},
        "from": {"id": "member"}, "text": "/保存 姐妹日常",
        "reply_to_message": {"message_id": "photo1"}}})
    assert result["content"]["reference"]["id"] == "photo1"
    image = W.to_internal({"message": {"message_id": "photo1", "chat": {"id": G.PRIMARY_CID},
        "from": {"id": "member"}, "content": {"type": "image", "text": "https://example.invalid/photo.png", "width": 12}}})
    assert image["content"]["type"] == "image" and image["content"]["width"] == 12


@pytest.mark.parametrize("primary,enabled", [(True, True), (False, True), (True, False)])
def test_mainloop_image_reply_bypasses_at_only_only_in_enabled_primary_group(monkeypatch, state, dbdir, primary, enabled):
    import bot
    from types import SimpleNamespace
    from runtime import dispatch, mainloop
    cid = G.PRIMARY_CID if primary else "other-room"
    service = G.Service(G.Store(dbdir / "gallery.db"), Platform())
    monkeypatch.setattr(dispatch._gallery, "command_reply", service.reply)
    monkeypatch.setattr(dispatch, "_feat_on", lambda cid, key: key == "slash_cmds" and enabled)
    sent = []
    class Sender:
        async def send(self, text, cid):
            sent.append(text)
            return True
        async def send_character_with_id(self, resource_id, cid):
            sent.append({"type": "share", "shareType": "character", "resourceId": resource_id})
            return "share-id"
    buffer = [{**message(), "message_id": "loop-0", "sent_by": "member"},
              {"message_id": "loop-1", "sent_by": "member", "content": {"text": "/漫画"}}]
    def drain(cid):
        rows = list(buffer)
        buffer.clear()
        return rows
    def requeue(cid, rows):
        for row in rows:
            row["_requeued"] = True
            buffer.append(row)
    subscriber = SimpleNamespace(is_connected=False, drain_private=lambda: {}, drain=drain,
                                 requeue=requeue, need_resync=lambda cid: False)
    for name in ("_load_member_names", "_remember_turn", "_mark_inflight", "_clear_inflight",
                 "set_persona_cid", "_set_active_cid"):
        monkeypatch.setattr(bot, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "ROOT", dbdir)
    monkeypatch.setattr(bot, "MUTE_CIDS", set())
    monkeypatch.setattr(bot, "PUSH_CIDS", set())
    monkeypatch.setattr(bot, "learn_bot_uid", lambda uid: False)
    monkeypatch.setattr(bot, "get_name", lambda uid: uid)
    monkeypatch.setattr(bot, "is_self", lambda uid: False)
    monkeypatch.setattr(bot, "is_at_me", lambda *args: False)
    monkeypatch.setattr(bot, "_quotes_self", lambda *args: False)
    monkeypatch.setattr(bot, "_detect_joins", lambda messages: [])
    monkeypatch.setattr(bot, "_dice_state", lambda: {})
    monkeypatch.setattr(bot, "message_lag", lambda messages: 0)
    monkeypatch.setattr(bot._profile_sync, "scan_load", lambda: {})
    monkeypatch.setattr(bot, "_feat_on", lambda cid, key: key == "at_only" or key == "slash_cmds" and enabled)
    monkeypatch.setattr(bot, "_room_cmd_refused", lambda *args: False)
    async def scenario():
        await mainloop.run_main_loop(0, False, True, groups=[cid], sender=Sender(),
            state=state, subscriber=subscriber, is_primary=False, B=bot)
        if primary and enabled:
            assert len(service.store.list("member")) == 1
            assert [row["message_id"] for row in buffer] == ["loop-1"]
            await mainloop.run_main_loop(0, False, True, groups=[cid], sender=Sender(),
                state=state, subscriber=subscriber, is_primary=False, B=bot)
            assert len(sent) == 3 and G.CARD_INTRO in sent[1]
            assert sent[2] == {"type": "share", "shareType": "character", "resourceId": G.CARD_ID}
        else:
            assert sent == [] and not service.store.path.exists()
        assert buffer == []
    asyncio.run(scenario())
