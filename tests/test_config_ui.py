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
    d = {"csrf": csrf}
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
