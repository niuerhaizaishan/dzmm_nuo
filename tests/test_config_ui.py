"""P11 配置编辑页（`admin/config_ui.py` + `/edit`）的守卫测试。

这个页面的风险很具体：**它是这个项目里唯一能写文件的地方**。
所以在它身上要钉的恰好是那几条：

  1. 没登录进不来（与只读面板同一套会话）
  2. 没有 CSRF token 一律拒绝
  3. **校验不过一个字都不写**（不是"写了再回滚"）
  4. 每次保存留下备份，能回滚
  5. 改完必须明确告诉用户"要重启才生效"
  6. 只写 config.json 一个文件（不是"什么都能写"）
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "admin"))

import dashboard as dash  # noqa: E402
from admin import config_ui as ui  # noqa: E402
from runtime import config_store as cs  # noqa: E402

import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"
CID_ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
USER = "nuo"
PW = "correct-horse-battery"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _req(base, path, method="GET", headers=None, data=None):
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        headers = dict(headers or {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(base + path, method=method,
                                 headers=headers or {}, data=body)
    try:
        with _OPENER.open(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


@pytest.fixture(autouse=True)
def _clean_state():
    dash._sessions.clear()
    dash._fails.clear()
    dash._csrf_tokens.clear()
    cs._cache = None
    yield
    dash._sessions.clear()
    dash._fails.clear()
    dash._csrf_tokens.clear()
    cs._cache = None


@pytest.fixture
def edit_server(monkeypatch, dbdir):
    """登录保护 + 配置指向临时文件的服务器。"""
    cfgp = dbdir / "config.json"
    monkeypatch.setenv("DZMM_CONFIG", str(cfgp))
    monkeypatch.setenv("DZMM_ADMIN_USER", USER)
    monkeypatch.setenv("DZMM_ADMIN_PASSWORD", PW)
    monkeypatch.setenv("DZMM_BOT_TOKEN", "dummy-token")
    cs._cache = None
    from http.server import ThreadingHTTPServer
    import threading
    if True:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:{}".format(srv.server_address[1])
        try:
            yield base, cfgp
        finally:
            srv.shutdown()
            srv.server_close()


@pytest.fixture
def edit_server_nopw(monkeypatch, dbdir):
    """**未配密码**的服务器（= 仅回环 / SSH 隧道模式）。

    这个模式下 `_require_session()` 直接放行，所以浏览器永远拿不到 session cookie。
    曾经因此 CSRF 键对不上（存 `sid or "-"`、查 `sid`）：
    页面能打开，但保存和回滚**一律 400**。
    """
    cfgp = dbdir / "config.json"
    monkeypatch.setenv("DZMM_CONFIG", str(cfgp))
    monkeypatch.delenv("DZMM_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DZMM_BOT_TOKEN", "dummy-token")
    cs._cache = None
    from http.server import ThreadingHTTPServer
    import threading
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    try:
        yield base, cfgp
    finally:
        srv.shutdown()
        srv.server_close()


def _login(base):
    st, _body, hdrs = _req(base, "/login", method="POST",
                           data={"username": USER, "password": PW})
    assert st == 303, st
    return {"Cookie": "{}={}".format(
        dash.SESSION_COOKIE,
        [p.split("=", 1)[1] for p in (hdrs.get("Set-Cookie") or "").split(";")
         if p.strip().startswith(dash.SESSION_COOKIE)][0])}


def _csrf_from(body):
    import re
    m = re.search(r"name=['\"]?csrf['\"]?\s+value='([^']*)'", body)
    if not m:
        raise AssertionError("页面里没有 csrf token")
    return m.group(1)


BASE_GROUPS = [
    {"cid": CID_A, "alias": "认证妹子群", "enabled": True, "mute": False,
     "screen": True, "push": True, "persona": ""},
    {"cid": CID_ROSE, "alias": "玫瑰房", "enabled": True, "mute": False,
     "screen": False, "push": False, "persona": ""},
]
BASE_BOTS = [{"key": "nuo_main", "name": "小小糯-bot", "short": "小小糯",
              "cid": CID_A, "token_env": "DZMM_BOT_TOKEN"}]


def _form(groups, bots, csrf):
    d = {"csrf": csrf,
         # 真实页面会带上这两个隐藏字段（渲染时存在哪些群/Bot），
         # 保存前的"旧页面"闸门靠它判断。测试里也照带，才和浏览器一致。
         "__groups_seen": ",".join(g.get("cid", "") for g in groups if g.get("cid")),
         "__bots_seen": ",".join(b.get("key", "") for b in bots if b.get("key"))}
    for i, g in enumerate(groups):
        for k in ("cid", "alias", "persona"):
            d["groups[{}][{}]".format(i, k)] = g.get(k, "")
        for k in ("enabled", "mute", "screen", "push"):
            if g.get(k):
                d["groups[{}][{}]".format(i, k)] = "1"
    for i, b in enumerate(bots):
        for k in ("key", "name", "short", "cid", "token_env", "persona"):
            d["bots[{}][{}]".format(i, k)] = b.get(k, "")
        if b.get("enabled", True):
            d["bots[{}][enabled]".format(i)] = "1"
    return d


# ---------------------------------------------------------------- 访问控制

def test_edit_requires_login(edit_server):
    base, _ = edit_server
    st, body, _ = _req(base, "/edit")
    assert "请登录" in body and "保存配置" not in body


def test_edit_renders_form_when_logged_in(edit_server):
    base, _ = edit_server
    cookie = _login(base)
    st, body, _ = _req(base, "/edit", headers=cookie)
    assert st == 200
    assert "保存配置" in body
    assert "name='csrf'" in body or "name=csrf" in body


def test_edit_never_echoes_a_token_value(edit_server):
    base, _ = edit_server
    monkeypatch_val = "dummy-token"
    cookie = _login(base)
    _st, body, _ = _req(base, "/edit", headers=cookie)
    assert monkeypatch_val not in body


def test_post_edit_without_csrf_is_refused_and_writes_nothing(edit_server):
    base, cfgp = edit_server
    cookie = _login(base)
    d = _form(BASE_GROUPS, BASE_BOTS, "wrong-token")
    st, body, _ = _req(base, "/edit", method="POST", data=d, headers=cookie)
    assert st == 400
    assert "CSRF" in body
    assert not cfgp.exists(), "被拒的请求不该产生任何文件"


# ---------------------------------------------------------------- 保存

def test_valid_save_writes_file_and_backup(edit_server):
    base, cfgp = edit_server
    cookie = _login(base)
    _st, page, _ = _req(base, "/edit", headers=cookie)
    csrf = _csrf_from(page)
    st, body, _ = _req(base, "/edit", method="POST", headers=cookie,
                       data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    assert st == 200
    assert "已保存" in body
    assert "重启" in body, "必须明确告诉用户要重启才生效"
    saved = json.loads(cfgp.read_text(encoding="utf-8"))
    assert [g["cid"] for g in saved["groups"]] == [CID_A, CID_ROSE]
    assert saved["bots"][0]["key"] == "nuo_main"


def test_second_save_leaves_a_backup(edit_server):
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    g2 = [dict(BASE_GROUPS[0], mute=True), BASE_GROUPS[1]]
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(g2, BASE_BOTS, csrf))
    backups = list(cs.backup_dir().glob("config-*.json"))
    assert len(backups) == 1
    old = json.loads(backups[0].read_text(encoding="utf-8"))
    assert old["groups"][0]["mute"] is False


def test_invalid_config_is_rejected_and_file_untouched(edit_server):
    """**校验不过一个字都不写** —— 不是"写了再回滚"。"""
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    before = cfgp.read_text(encoding="utf-8")

    # 两个 Bot 绑同一个群 → 必须拒绝
    bad_bots = BASE_BOTS + [{"key": "nuo_two", "name": "x", "cid": CID_A,
                             "token_env": "DZMM_BOT_TOKEN"}]
    st, body, _ = _req(base, "/edit", method="POST", headers=cookie,
                       data=_form(BASE_GROUPS, bad_bots, csrf))
    assert st == 400
    assert "未写入" in body
    assert cfgp.read_text(encoding="utf-8") == before


def test_bot_bound_to_unknown_group_is_rejected(edit_server):
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    bad = [dict(BASE_BOTS[0], cid="ffffffff-0000-0000-0000-000000000000")]
    st, body, _ = _req(base, "/edit", method="POST", headers=cookie,
                       data=_form(BASE_GROUPS, bad, csrf))
    assert st == 400
    assert "不在 groups 里" in body
    assert not cfgp.exists()


def test_same_content_reports_no_change(edit_server):
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    st, body, _ = _req(base, "/edit", method="POST", headers=cookie,
                       data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    assert "没有检测到改动" in body


def test_save_appends_audit_line(edit_server):
    base, _ = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    lines = ui.read_audit(ROOT / ui.AUDIT_NAME)
    assert lines, "应当留下审计记录"
    last = lines[-1]
    assert "ip=127.0.0.1" in last, last
    # 内容必须能看出"改了什么"（旧值 -> 新值）
    assert "->" in last or "新增" in last or "删除" in last, last
    assert CID_A[:8] in last or "nuo_main" in last, last


# ---------------------------------------------------------------- 回滚

def test_rollback_restores_previous_version(edit_server):
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    g2 = [dict(BASE_GROUPS[0], alias="改过的名字"), BASE_GROUPS[1]]
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(g2, BASE_BOTS, csrf))
    assert json.loads(cfgp.read_text(encoding="utf-8"))["groups"][0]["alias"] == "改过的名字"

    name = sorted(cs.backup_dir().glob("config-*.json"))[0].name
    st, body, _ = _req(base, "/edit/rollback", method="POST", headers=cookie,
                       data={"csrf": csrf, "name": name})
    assert st == 200
    assert "已回滚" in body
    assert json.loads(cfgp.read_text(encoding="utf-8"))["groups"][0]["alias"] == "认证妹子群"


def test_rollback_without_csrf_is_refused(edit_server):
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    st, _body, _ = _req(base, "/edit/rollback", method="POST", headers=cookie,
                        data={"csrf": "nope", "name": "config-x.json"})
    assert st == 400


# ---------------------------------------------------------------- 纯函数

def test_parse_form_ignores_blank_rows():
    form = {
        "csrf": ["t"],
        "groups[0][cid]": ["a"], "groups[0][alias]": ["A"],
        "groups[0][enabled]": ["1"],
        "groups[1][cid]": [""], "groups[1][alias]": ["空行"],
        "groups[2][cid]": ["b"], "groups[2][alias]": ["B"],
        "bots[0][key]": ["k"], "bots[0][cid]": ["a"], "bots[0][token_env]": ["T"],
        "bots[1][key]": [""],
    }
    raw = ui.parse_form(form)
    assert [g["cid"] for g in raw["groups"]] == ["a", "b"]
    assert [b["key"] for b in raw["bots"]] == ["k"]


def test_diff_summary_reports_field_changes():
    old = {"groups": [{"cid": CID_A, "alias": "A", "mute": False}], "bots": []}
    new = {"groups": [{"cid": CID_A, "alias": "A", "mute": True}], "bots": []}
    s = ui.diff_summary(old, new)
    assert any("mute" in x for x in s)
    assert not any("alias" in x for x in s)


def test_diff_summary_ignores_fields_missing_from_the_file():
    """旧文件里没写的字段 = 默认值，不是"改动"。

    真实症状：早期 hand-write 的 config.json 里 bots[0] 没有 `enabled`，
    表单提交上来的是显式 `enabled=True`；不补默认值就会报一条假改动
    （"打开页面什么都不动点保存 → 1 项改动"），还白多一份备份 + 一行审计。
    """
    old = {"groups": [{"cid": CID_A, "alias": "A"}],          # 没有 mute/screen/push
           "bots": [{"key": "k", "cid": CID_A, "token_env": "T"}]}   # 没有 enabled
    new = {"groups": [{"cid": CID_A, "alias": "A", "enabled": True,
                       "mute": False, "screen": False, "push": False, "persona": ""}],
           "bots": [{"key": "k", "name": "", "short": "", "cid": CID_A,
                     "token_env": "T", "persona": "", "enabled": True}]}
    assert ui.diff_summary(old, new) == []


def test_diff_summary_still_reports_a_real_flag_flip():
    """补默认值不能把真改动也吃掉。"""
    old = {"groups": [{"cid": CID_A, "alias": "A"}], "bots": []}
    new = {"groups": [{"cid": CID_A, "alias": "A", "mute": True}], "bots": []}
    assert any("mute" in x for x in ui.diff_summary(old, new))


def test_diff_summary_reports_add_and_remove():
    old = {"groups": [{"cid": CID_A}], "bots": []}
    new = {"groups": [{"cid": CID_B}], "bots": []}
    s = " ".join(ui.diff_summary(old, new))
    assert "新增群" in s and "删除群" in s


def test_diff_summary_silent_when_nothing_changed():
    raw = {"groups": [{"cid": CID_A, "alias": "A"}], "bots": []}
    assert ui.diff_summary(raw, raw) == []


def test_pending_change_detects_newer_file(dbdir):
    """配置文件比进程新 ⇒ 未生效。拿不到进程时刻就不误报。"""
    p = dbdir / "config.json"
    p.write_text("{}", encoding="utf-8")
    import time
    now = time.time()
    assert ui.pending_change(p, now - 60) is True       # 文件比进程新
    assert ui.pending_change(p, now + 60) is False      # 进程比文件新
    assert ui.pending_change(p, None) is False          # 拿不到就不误报
    assert ui.pending_change(dbdir / "nope.json", now - 60) is False


def test_list_backups_and_restore(dbdir):
    bd = dbdir / "config_backups"
    bd.mkdir()
    (bd / "config-20260101-000000.json").write_text(
        json.dumps({"version": 1, "groups": [], "bots": []}), encoding="utf-8")
    names = ui.list_backups(bd)
    assert names == ["config-20260101-000000.json"]
    assert ui.restore_backup(bd, names[0])["version"] == 1
    with pytest.raises(FileNotFoundError):
        ui.restore_backup(bd, "nope.json")


def test_restore_backup_rejects_path_traversal(dbdir):
    """备份名来自表单 —— 必须 basename 化，不能让它读到任意路径。"""
    bd = dbdir / "config_backups"
    bd.mkdir()
    secret = dbdir / "secret.json"
    secret.write_text('{"version": 1, "groups": [], "bots": []}', encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        ui.restore_backup(bd, "../secret.json")


def test_render_edit_escapes_values():
    raw = {"version": 1, "groups": [
        {"cid": CID_A, "alias": "<script>alert(1)</script>"}], "bots": []}
    html = ui.render_edit(raw, csrf="t")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_edit_warns_when_pending():
    html = ui.render_edit({"version": 1, "groups": [], "bots": []},
                          csrf="t", pending=True)
    assert "尚未生效" in html
    assert "docker compose restart dzmm-bot" in html
    # 「未生效」提示里绝不能教用户用 up -d：改的只是挂载的 config.json，
    # compose 看不出变化，up -d 会直接跳过、什么都不做。
    assert "docker compose up -d dzmm-bot" not in html
    assert "登出" in html, "重启会把会话清掉，得先说明"


# ------------------------------------------- 保存后必须逐条摊开"改了什么"

def test_save_page_lists_every_change(edit_server):
    """光一句"已保存"看不出顺手翻了别的开关 —— 必须逐条列出来。

    真实事故：验收脚本拼 body 时没判 `checked`，把没勾的复选框也提交了，
    一次"只改别名"的保存顺带把 4 个群的静默/筛查/推送全翻成 true，
    而页面上只写着"已保存"。所以这里钉住：改了什么，就得看见什么。
    """
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    g2 = [dict(BASE_GROUPS[0], mute=True, alias="新别名"), BASE_GROUPS[1]]
    st, body, _ = _req(base, "/edit", method="POST", headers=cookie,
                       data=_form(g2, BASE_BOTS, csrf))
    assert st == 200
    assert "本次改动 2 项" in body, "改动条数要写清楚"
    assert "新别名" in body and "mute" in body, "每条改动都要列出来"
    assert body.count("<li class='mono'>") >= 2


def test_render_edit_lists_change_details():
    html = ui.render_edit({"version": 1, "groups": [], "bots": []}, csrf="t",
                          changes=["群 bd7fe6a4 alias : 'a' -> 'b'",
                                   "群 4a56be71 mute : False -> True"])
    assert "本次改动 2 项" in html
    assert "alias : &#x27;a&#x27; -&gt; &#x27;b&#x27;" in html
    assert "mute : False -&gt; True" in html


def test_render_edit_has_no_change_block_when_clean():
    html = ui.render_edit({"version": 1, "groups": [], "bots": []}, csrf="t")
    assert "本次改动" not in html


# ------------------------------------------- 被禁用的群不能从页面上消失

DISABLED_GROUPS = [
    {"cid": CID_A, "alias": "认证妹子群", "enabled": True, "mute": False,
     "screen": True, "push": True, "persona": ""},
    {"cid": CID_ROSE, "alias": "玫瑰房", "enabled": False, "mute": False,
     "screen": False, "push": False, "persona": ""},
]


def test_as_dict_keeps_disabled_groups():
    """`as_dict()` 必须给出**全部**配置项，不是只给生效的那些。

    真实事故：用户在页面上把 4 个群取消勾选「启用」并保存，再打开页面 ——
    那 4 个群整行消失了（页面是用 as_dict 渲染的，而它只序列化生效的群），
    于是**再也勾不回来**；更糟的是这时候改任何别的东西再保存，
    配置里那 4 行会被整行删掉，别名和人设一起没。
    """
    raw = {"version": 1, "groups": DISABLED_GROUPS, "bots": []}
    cfg = cs._parse(raw, {})
    d = cfg.as_dict()
    assert len(d["groups"]) == 2, "被禁用的群不能从 as_dict 里消失"
    assert [g["cid"] for g in d["groups"]] == [CID_A, CID_ROSE], "顺序要保持"
    # 但运行期语义不变：生效的只有 enabled 的那些
    assert list(cfg.groups) == [CID_A]


def test_page_still_shows_a_disabled_group(edit_server):
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    g2 = [DISABLED_GROUPS[0], dict(DISABLED_GROUPS[1])]
    st, _b, _h = _req(base, "/edit", method="POST", headers=cookie,
                      data=_form(g2, BASE_BOTS, csrf))
    assert st == 200, st
    st, page, _ = _req(base, "/edit", headers=cookie)
    assert CID_ROSE in page, "被禁用的群必须还在页面上（否则勾不回来）"
    assert "name='groups[1][cid]' value='{}'".format(CID_ROSE) in page
    # 而且它的「启用」是**没勾**的
    import re
    tag = re.search(r"<input[^>]*name='groups\[1\]\[enabled\]'[^>]*>", page)
    assert tag and "checked" not in tag.group(0), "禁用状态要如实渲染成未勾选"


def test_save_keeps_disabled_groups_and_alias(edit_server):
    """在含"被禁用群"的页面上改别的东西再保存，那几行不能被写丢。"""
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    st, _b, _h = _req(base, "/edit", method="POST", headers=cookie,
                      data=_form(DISABLED_GROUPS, BASE_BOTS, csrf))
    assert st == 200, st
    # 现在把启用群的别名改掉再存一次（表单来自"完整"页面）
    page = _req(base, "/edit", headers=cookie)[1]
    csrf = _csrf_from(page)
    g2 = [dict(DISABLED_GROUPS[0], alias="改过的名字"), DISABLED_GROUPS[1]]
    st, body, _ = _req(base, "/edit", method="POST", headers=cookie,
                       data=_form(g2, BASE_BOTS, csrf))
    assert st == 200 and "已保存" in body, body[:300]
    saved = json.loads(cfgp.read_text(encoding="utf-8"))
    assert len(saved["groups"]) == 2, "被禁用的群被写丢了"
    assert saved["groups"][1]["alias"] == "玫瑰房"
    assert saved["groups"][1]["enabled"] is False


def test_stale_page_cannot_delete_groups(edit_server):
    """旧页面（没有那几行）提交上来，必须整单拒绝 —— 不许静默删配置。"""
    base, cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    st, _b, _h = _req(base, "/edit", method="POST", headers=cookie,
                      data=_form(DISABLED_GROUPS, BASE_BOTS, csrf))
    assert st == 200 and cfgp.exists()
    before = cfgp.read_text(encoding="utf-8")
    # 模拟"旧页面"：只提交第一个群，隐藏字段里也没有第二个群
    stale = _form([DISABLED_GROUPS[0]], BASE_BOTS, csrf)
    stale["__groups_seen"] = CID_A
    st, body, _ = _req(base, "/edit", method="POST", headers=cookie, data=stale)
    assert st == 400
    assert "旧的一份" in body
    assert CID_ROSE[:8] in body, "要指出到底缺了哪个"
    assert cfgp.read_text(encoding="utf-8") == before, "一个字都不许写"


# -------------------------------- 未配密码（回环/隧道）模式：页面必须能存能回滚

def test_nopassword_mode_can_save(edit_server_nopw):
    """未配密码时没有 session cookie，CSRF 的键必须两边一致，保存才能通过。

    回归：曾经渲染表单存 `sid or "-"`、校验查 `sid` ⇒ 该模式下一律 400
    「CSRF 校验失败」——页面能看不能改。生产配了密码，所以从没暴露。
    """
    base, cfgp = edit_server_nopw
    st, page, _ = _req(base, "/edit")
    assert st == 200 and "保存配置" in page
    csrf = _csrf_from(page)
    st, body, _ = _req(base, "/edit", method="POST",
                       data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    assert st == 200, body[:300]
    assert "已保存" in body
    assert cfgp.is_file(), "文件应当真的写出来了"
    saved = json.loads(cfgp.read_text(encoding="utf-8"))
    assert [g["cid"] for g in saved["groups"]] == [CID_A, CID_ROSE]


def test_nopassword_mode_can_roll_back(edit_server_nopw):
    base, cfgp = edit_server_nopw
    csrf = _csrf_from(_req(base, "/edit")[1])
    assert _req(base, "/edit", method="POST",
                data=_form(BASE_GROUPS, BASE_BOTS, csrf))[0] == 200
    # 第二次保存会留下第一版的备份
    csrf = _csrf_from(_req(base, "/edit")[1])
    g2 = [dict(BASE_GROUPS[0], alias="改过的"), BASE_GROUPS[1]]
    assert _req(base, "/edit", method="POST",
                data=_form(g2, BASE_BOTS, csrf))[0] == 200
    backups = cs.backup_dir().glob("config-*.json")
    name = sorted(p.name for p in backups)[-1]
    st, body, _ = _req(base, "/edit/rollback", method="POST",
                       data={"csrf": csrf, "name": name})
    assert st == 200, body[:200]
    assert "已回滚" in body
    saved = json.loads(cfgp.read_text(encoding="utf-8"))
    assert saved["groups"][0]["alias"] == "认证妹子群"


def test_nopassword_mode_backup_diff_is_reachable(edit_server_nopw):
    base, _cfgp = edit_server_nopw
    csrf = _csrf_from(_req(base, "/edit")[1])
    _req(base, "/edit", method="POST", data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    csrf = _csrf_from(_req(base, "/edit")[1])
    g2 = [dict(BASE_GROUPS[0], alias="新别名"), BASE_GROUPS[1]]
    _req(base, "/edit", method="POST", data=_form(g2, BASE_BOTS, csrf))
    name = sorted(p.name for p in cs.backup_dir().glob("config-*.json"))[-1]
    st, page, _ = _req(base, "/edit/backup?name=" + name)
    assert st == 200
    assert "逐行" in page or "差异" in page
    assert "新别名" in page, "差异里要能看到改了什么"
    assert "回滚到这一版" in page


# ---------------------------------------------------------------- 备份：列全与差异

def test_list_backups_returns_all_by_default(dbdir):
    bd = dbdir / "config_backups"
    bd.mkdir()
    for i in range(15):
        (bd / "config-202609{:02d}-000000.json".format(i + 1)).write_text(
            "{}", encoding="utf-8")
    assert len(ui.list_backups(bd)) == 15, "默认必须列全（曾经只列 10 份）"
    assert len(ui.list_backups(bd, 5)) == 5
    assert ui.list_backups(bd)[0].endswith("15-000000.json"), "新的在前"


def test_backup_path_rejects_traversal_and_junk(dbdir):
    bd = dbdir / "config_backups"
    bd.mkdir()
    (bd / "config-20260925-000000.json").write_text("{}", encoding="utf-8")
    for bad in ("../secret.json", "/etc/passwd", "config-x.txt", "", "secret.json"):
        with pytest.raises(FileNotFoundError):
            ui.backup_path(bd, bad)
    assert ui.backup_path(bd, "config-20260925-000000.json").name == \
        "config-20260925-000000.json"


def test_unified_diff_marks_added_and_removed():
    lines = ui.unified_diff('{\n  "a": 1\n}', '{\n  "a": 2\n}')
    assert any(x.startswith("-") and "1" in x for x in lines)
    assert any(x.startswith("+") and "2" in x for x in lines)


def test_backup_detail_page_escapes_and_shows_summary():
    old = {"version": 1, "groups": [{"cid": CID_A, "alias": "<b>x</b>"}], "bots": []}
    cur = {"version": 1, "groups": [{"cid": CID_A, "alias": "y"}], "bots": []}
    html = ui.render_backup_detail("config-1.json", old, cur,
                                   '{"alias": "<b>x</b>"}', '{"alias": "y"}',
                                   csrf="tok")
    assert "&lt;b&gt;" in html and "<b>x</b>" not in html
    assert "回滚到这一版" in html and "config-1.json" in html


def test_edit_page_links_to_backup_diff(edit_server):
    base, _cfgp = edit_server
    cookie = _login(base)
    csrf = _csrf_from(_req(base, "/edit", headers=cookie)[1])
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form(BASE_GROUPS, BASE_BOTS, csrf))
    _req(base, "/edit", method="POST", headers=cookie,
         data=_form([dict(BASE_GROUPS[0], alias="x"), BASE_GROUPS[1]],
                    BASE_BOTS, csrf))
    page = _req(base, "/edit", headers=cookie)[1]
    assert "/edit/backup?name=config-" in page, "备份要能点进去看差异"


def test_edit_page_shows_effective_persona(edit_server):
    """人设空格子要写清"留空 = 会用哪个"，并把可用的人设名列出来。

    这个字段曾经（a）填了不起作用、（b）名单要用户自己猜。
    现在：留空 → placeholder 显示实际会用的人设；名字列在表头下方。
    """
    base, _cfgp = edit_server
    cookie = _login(base)
    page = _req(base, "/edit", headers=cookie)[1]
    assert "留空 = nuo" in page
    assert "人设（填人设名）" in page
    assert "nuo" in page and "rose_butler" in page
    # Bot 表不再有"人设"列（Bot 只是通道，人设按群配）
    import re
    assert not re.search(r"bots\[\d+\]\[persona\]", page)


def test_unseen_deletions_unit():
    form = {"__groups_seen": CID_A, "__bots_seen": "nuo_main"}
    old = {"groups": [{"cid": CID_A}, {"cid": CID_ROSE}],
           "bots": [{"key": "nuo_main"}]}
    new = {"groups": [{"cid": CID_A}], "bots": [{"key": "nuo_main"}]}
    assert ui.unseen_deletions(old, new, form) == ["群 " + CID_ROSE[:8]]
    # 页面见过它、用户又清空了 cid ⇒ 这是"有意删除"，放行
    form2 = {"__groups_seen": CID_A + "," + CID_ROSE}
    assert ui.unseen_deletions(old, new, form2) == []
    # 全新的一单（老配置里没有）不算删除
    assert ui.unseen_deletions({"groups": [], "bots": []}, new, form) == []
