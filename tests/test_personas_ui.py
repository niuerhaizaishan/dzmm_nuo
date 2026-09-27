"""人设与话术页（P12-E）：渲染 / 解析 / 预览 / 保存 / 恢复默认。

这一页的价值同样在于"配了真生效"，测试分三层：

1. **渲染**：每个人设的 3 段文本 + 全部话术都在页面上、HTML 转义、CSP 不变；
2. **解析**：逐格与代码默认比对（一样不写覆盖、**空串算改动**）、
   草稿画得回去、没有页面标记时一个字都不动；
3. **端到端**：真的 POST 一次 → 配置里真的有 → 运行期真的生效；
   预览**一个字都不写**。
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "admin"))

import personas as P  # noqa: E402
import dashboard as dash  # noqa: E402
from admin import personas_ui as pui  # noqa: E402
from runtime import config_store as cs  # noqa: E402

import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

USER, PW = "nuo", "pw12345678"
CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "4a56be71-9ca6-4785-b45f-18725d187f1a"
FIELDS = ("name_full", "name_short", "extra")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
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
def _clean():
    dash._sessions.clear()
    dash._fails.clear()
    dash._csrf_tokens.clear()
    cs._cache = None
    P.set_content_overrides({})
    P.set_config_personas({})
    yield
    cs._cache = None
    P.set_content_overrides({})
    P.set_config_personas({})


@pytest.fixture
def srv(monkeypatch, dbdir):
    cfgp = dbdir / "config.json"
    cfgp.write_text(json.dumps({
        "version": cs.SCHEMA_VERSION,
        "groups": [{"cid": CID_A, "alias": "认证妹子群", "enabled": True,
                    "screen": True, "push": True, "mute": False, "persona": ""},
                   {"cid": CID_B, "alias": "玫瑰房", "enabled": True,
                    "screen": False, "push": False, "mute": False,
                    "persona": "rose_butler"}],
        "bots": [{"key": "nuo_main", "name": "小小糯-bot", "cid": CID_A,
                  "token_env": "DZMM_BOT_TOKEN", "enabled": True}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DZMM_CONFIG", str(cfgp))
    monkeypatch.setenv("DZMM_ADMIN_USER", USER)
    monkeypatch.setenv("DZMM_ADMIN_PASSWORD", PW)
    monkeypatch.setenv("DZMM_BOT_TOKEN", "dummy")
    cs._cache = None
    from http.server import ThreadingHTTPServer
    import threading
    s = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(s.server_address[1])
    try:
        yield base, cfgp
    finally:
        s.shutdown()
        s.server_close()


def _login(base):
    st, _b, h = _req(base, "/login", method="POST",
                     data={"username": USER, "password": PW})
    assert st == 303
    return {"Cookie": "{}={}".format(
        dash.SESSION_COOKIE,
        [p.split("=", 1)[1] for p in (h.get("Set-Cookie") or "").split(";")
         if p.strip().startswith(dash.SESSION_COOKIE)][0])}


def _csrf(page):
    return re.search(r"name=['\"]?csrf['\"]?\s+value='([^']*)'", page).group(1)


def _page_form(csrf, **over):
    """按页面会提交的样子，把每一格都填成**代码默认**，再叠加改动。"""
    form = {"csrf": csrf, "persona_form": "1", "action": "save"}
    for key in P.PERSONAS:
        form["pseen[{}]".format(key)] = "1"
        d = P.PERSONAS[key]
        for f in FIELDS:
            form["ptext[{}][{}]".format(key, f)] = d.get(f) or ""
        dph = dict(P.PHRASES["nuo"])
        dph.update(P.PHRASES.get(key) or {})
        for pk in P.known_phrase_keys(key):
            form["pphrase[{}][{}]".format(key, pk)] = dph.get(pk) or ""
    form.update(over)
    return form


# ---------------------------------------------------------------- 1. 渲染

def test_page_requires_login(srv):
    base, _ = srv
    st, body, _ = _req(base, "/personas")
    assert st == 200 and "请登录" in body


def test_page_lists_every_persona_text_and_phrase(srv):
    base, _ = srv
    cookie = _login(base)
    st, page, _ = _req(base, "/personas", headers=cookie)
    assert st == 200
    for key in P.PERSONAS:
        assert "ptext[{}][name_full]".format(key) in page
        assert "ptext[{}][extra]".format(key) in page
        for pk in P.known_phrase_keys(key):
            assert "pphrase[{}][{}]".format(key, pk) in page, \
                "话术 {} / {} 没画出来".format(key, pk)
        assert "pseen[{}]".format(key) in page, "要有'这一节渲染过'的标记"
    # 中文标签、默认值、用法说明都在
    assert P.PHRASE_LABELS["mem_saved"] in page
    assert "代码默认" in page
    assert "目前没有群在用它" in page or "用于：" in page
    assert "href='/personas/reset'" in page or "action='/personas/reset'" in page


def test_page_has_csp_and_no_inline_script(srv):
    """CSP 不变，且页面里不留内联事件处理器（会被 CSP 拦掉、看着像坏了）。"""
    base, _ = srv
    cookie = _login(base)
    st, page, h = _req(base, "/personas", headers=cookie)
    assert st == 200
    assert "script-src 'self'" in h.get("Content-Security-Policy", "")
    assert h.get("X-Frame-Options") == "DENY"
    assert "onsubmit" not in page and "<script" not in page


def test_page_escapes_persona_text(srv, monkeypatch, dbdir):
    """人设文本是自由输入 → 必须转义（别在运维后台里开一个 XSS）。"""
    base, cfgp = srv
    cookie = _login(base)
    raw = json.loads(cfgp.read_text(encoding="utf-8"))
    raw["personas"] = {"nuo": {"extra": "<script>alert(1)</script>"}}
    cfgp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cs._cache = None
    st, page, _ = _req(base, "/personas", headers=cookie)
    assert st == 200
    assert "&lt;script&gt;" in page and "<script>alert" not in page


def test_page_marks_changed_fields(srv, dbdir):
    base, cfgp = srv
    cookie = _login(base)
    raw = json.loads(cfgp.read_text(encoding="utf-8"))
    raw["personas"] = {"rose_butler": {"name_short": "玫瑰管家"}}
    cfgp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cs._cache = None
    _st, page, _ = _req(base, "/personas", headers=cookie)
    assert "已改 1 处" in page


# ---------------------------------------------------------------- 2. 解析

RAW = {"version": 3,
       "groups": [{"cid": CID_A, "alias": "A", "persona": ""},
                  {"cid": CID_B, "alias": "B", "persona": "rose_butler"}],
       "bots": []}


def test_parse_only_writes_differences():
    parsed = pui.parse_personas(_page_form("x"), RAW)
    assert parsed["personas"] == {}, "一个字都没改 ⇒ 一个覆盖都不写"
    assert parsed["untouched"] is False


def test_parse_changed_field_and_phrase():
    parsed = pui.parse_personas(_page_form(
        "x", **{"ptext[rose_butler][name_short]": "玫瑰管家",
                "pphrase[nuo][mem_saved]": "记住啦"}), RAW)
    assert parsed["personas"]["rose_butler"] == {"name_short": "玫瑰管家"}
    assert parsed["personas"]["nuo"]["phrases"] == {"mem_saved": "记住啦"}


def test_parse_empty_string_is_a_change():
    """清空 extra = **真的不要这段设定**（不是"没写"）—— 见模块说明。"""
    parsed = pui.parse_personas(
        _page_form("x", **{"ptext[rose_butler][extra]": ""}), RAW)
    assert parsed["personas"]["rose_butler"] == {"extra": ""}
    assert RAW["groups"][1]["persona"] == "rose_butler", "前置条件"


def test_parse_reverting_to_default_drops_the_override():
    raw = dict(RAW, personas={"rose_butler": {"name_short": "玫瑰管家"}})
    parsed = pui.parse_personas(_page_form("x"), raw)
    assert parsed["personas"] == {}, "改回默认 = 取消覆盖"


def test_parse_without_page_marker_inherits():
    """不是这一页发的表单（旧页面/脚本/半截提交）⇒ 一个字都不动。"""
    raw = dict(RAW, personas={"rose_butler": {"name_short": "玫瑰管家"}})
    parsed = pui.parse_personas({"csrf": ["x"]}, raw)
    assert parsed["untouched"] is True
    assert parsed["personas"] == {"rose_butler": {"name_short": "玫瑰管家"}}


def test_parse_unrendered_persona_inherits():
    raw = dict(RAW, personas={"rose_butler": {"name_short": "玫瑰管家"}})
    form = _page_form("x")
    form.pop("pseen[rose_butler]")           # 这一节没画出来
    form.pop("ptext[rose_butler][name_short]")
    parsed = pui.parse_personas(form, raw)
    assert parsed["personas"]["rose_butler"] == {"name_short": "玫瑰管家"}


def test_parse_normalises_crlf():
    parsed = pui.parse_personas(
        _page_form("x", **{"ptext[nuo][extra]": "第一行\r\n第二行"}), RAW)
    assert parsed["personas"]["nuo"]["extra"] == "第一行\n第二行"


def test_diff_reports_text_changes():
    raw = dict(RAW, personas={"rose_butler": {"name_short": "玫瑰管家"}})
    new = pui.parse_personas(
        _page_form("x", **{"ptext[rose_butler][name_short]": "糯管家",
                           "pphrase[nuo][mem_saved]": "记住啦"}), raw)
    lines = pui.personas_diff(raw, new)
    assert any("简称" in x and "玫瑰管家" in x and "糯管家" in x for x in lines)
    assert any("记忆 · 已记住" in x for x in lines)
    assert pui.personas_diff(raw, raw) == []


def test_effective_helpers_treat_empty_as_a_value():
    d = {"name_short": "默认名", "extra": "默认设定"}
    assert pui.effective_field(d, {}, "name_short") == "默认名"
    assert pui.effective_field(d, {"name_short": ""}, "name_short") == ""
    assert pui.effective_phrases({"a": "1"}, {"phrases": {"a": "", "b": "2"}}) == {
        "a": "", "b": "2"}


# ---------------------------------------------------------------- 3. 预览

def test_persona_preview_uses_draft_and_leaves_globals_alone():
    raw = {"personas": {"rose_butler": {"name_short": "糯管家", "extra": ""}}}
    pv = pui.persona_preview(raw, CID_B, "")
    assert pv["key"] == "rose_butler"
    assert "糯管家" in pv["system"]
    assert P.PERSONAS["rose_butler"]["extra"] not in pv["system"], "草稿清空了 extra"
    assert pv["phrases"]["mem_saved"] == P.PHRASES["rose_butler"]["mem_saved"]
    assert P.content_overrides() == {}, "预览不能把草稿写进全局"
    assert P.persona_for(CID_B)["name_short"] == P.PERSONAS["rose_butler"]["name_short"]


def test_persona_preview_health_uses_draft():
    raw = {"personas": {"nuo": {"phrases": {"mem_saved": "啊" * 1001}}}}
    pv = pui.persona_preview(raw, CID_A, "")
    assert any("mem_saved" in x for x in pv["health"])


def test_persona_preview_try_message_calls_model_with_draft(monkeypatch):
    """试说一句：调模型的**那一刻**，临时覆盖必须已经生效。

    真实路径是 `brains._call_llm` 内部调 `personas.apply_persona()` 注入人设，
    所以这里替换掉 `_call_llm`，在它里面问一句"现在生效的人设是谁"。
    """
    import brains
    seen = {}

    def fake_llm(system, user, **kw):
        seen["system"] = system
        seen["user"] = user
        seen["persona"] = P.persona_for(CID_B)
        return "（试说）玫瑰房今日有酒。"

    monkeypatch.setattr(brains, "_call_llm", fake_llm)
    raw = {"personas": {"rose_butler": {"name_short": "糯管家", "extra": ""}}}
    pv = pui.persona_preview(raw, CID_B, "你是谁？")
    assert pv["reply"] == "（试说）玫瑰房今日有酒。"
    assert seen["persona"]["name_short"] == "糯管家", "草稿必须已经生效"
    assert seen["persona"]["extra"] == "", "草稿清空的 extra 也要生效"
    assert "你是谁？" in seen["user"]
    assert P.content_overrides() == {}
    assert P.current_cid() == "", "预览结束后 cid 要复位"


def test_persona_preview_reports_model_failure(monkeypatch):
    import brains

    def boom(*a, **kw):
        raise RuntimeError("no api key")

    monkeypatch.setattr(brains, "_call_llm", boom)
    pv = pui.persona_preview({"personas": {}}, CID_A, "在吗")
    assert pv["error"] and "no api key" in pv["error"]
    assert pv["reply"] is None


# ---------------------------------------------------------------- 4. 端到端

def test_preview_endpoint_writes_nothing(srv):
    base, cfgp = srv
    cookie = _login(base)
    page = _req(base, "/personas", headers=cookie)[1]
    before = cfgp.read_text(encoding="utf-8")
    st, body, _ = _req(base, "/personas", method="POST", headers=cookie,
                       data=_page_form(_csrf(page), action="preview",
                                       preview_cid=CID_B,
                                       **{"ptext[rose_butler][name_short]": "糯管家"}))
    assert st == 200 and "预览" in body
    assert "糯管家" in body, "草稿要画回表单，别把用户刚打的字吃了"
    assert cfgp.read_text(encoding="utf-8") == before, "预览一个字都不能写"


def test_save_end_to_end_and_takes_effect(srv):
    base, cfgp = srv
    cookie = _login(base)
    page = _req(base, "/personas", headers=cookie)[1]
    st, body, _ = _req(base, "/personas", method="POST", headers=cookie,
                       data=_page_form(_csrf(page),
                                       **{"ptext[rose_butler][name_short]": "玫瑰管家",
                                          "ptext[rose_butler][extra]": "",
                                          "pphrase[nuo][mem_saved]": "记住啦～"}))
    assert st == 200 and "已保存" in body and "本次改动" in body
    saved = json.loads(cfgp.read_text(encoding="utf-8"))
    assert saved["personas"]["rose_butler"]["name_short"] == "玫瑰管家"
    assert saved["personas"]["rose_butler"]["extra"] == ""
    assert saved["personas"]["nuo"]["phrases"]["mem_saved"] == "记住啦～"
    # 这一页不碰别的节
    assert [g["cid"] for g in saved["groups"]] == [CID_A, CID_B]
    assert saved["bots"][0]["key"] == "nuo_main"
    assert "features" not in saved and "tuning" not in saved

    # 运行期生效：把落盘的这份喂进 personas 层（等价于重启后加载）
    P.set_config_personas({CID_B: "rose_butler"})
    P.set_content_overrides(cs.load({
        "groups": (CID_A, CID_B), "mute": (), "screen": (), "push": (),
        "aliases": {}, "bots": [], "personas": tuple(P.PERSONAS)}).personas)
    assert P.persona_for(CID_B)["name_short"] == "玫瑰管家"
    assert P.persona_for(CID_B)["extra"] == ""
    assert P.p("mem_saved", CID_A) == "记住啦～"
    assert P.apply_persona(
        "你是{0}（简称{1}）。".format(P.DEFAULT_NAME_FULL, P.DEFAULT_NAME_SHORT),
        CID_B).endswith("。"), "extra 被清空后不该再追加任何设定"


def test_save_rejects_empty_name(srv):
    base, cfgp = srv
    cookie = _login(base)
    page = _req(base, "/personas", headers=cookie)[1]
    before = cfgp.read_text(encoding="utf-8")
    st, body, _ = _req(base, "/personas", method="POST", headers=cookie,
                       data=_page_form(_csrf(page),
                                       **{"ptext[nuo][name_short]": ""}))
    assert st == 400 and "未写入" in body
    assert cfgp.read_text(encoding="utf-8") == before


def test_save_rejects_overlong_phrase(srv):
    """话术超平台上限：**拒绝**（而且要说清楚原因），不能等发出去被截断。"""
    base, cfgp = srv
    cookie = _login(base)
    page = _req(base, "/personas", headers=cookie)[1]
    before = cfgp.read_text(encoding="utf-8")
    st, body, _ = _req(base, "/personas", method="POST", headers=cookie,
                       data=_page_form(_csrf(page),
                                       **{"pphrase[nuo][mem_saved]": "啊" * 1001}))
    assert st == 400 and "平台上限" in body
    assert cfgp.read_text(encoding="utf-8") == before


def test_full_page_form_exceeds_the_old_body_cap():
    """整页回传**稍微一长**就顶破原来 64KB 的线 —— 上限必须放宽。

    2026-09-27 实测：这一页要回传 3 个人设的文本 + 全部话术，URL 编码后中文一字 9 字节；
    整页本身就 ~55KB，用户随手把一条话术写长一点就越过 `MAX_BODY * 16 = 64KB`，
    保存直接 400「请求格式不对」（页面看起来坏了，真因却是体积上限）。
    这条钉住"别再把上限调回去"。
    """
    from admin import dashboard as _d
    small = len(urllib.parse.urlencode(_page_form("x"), doseq=True))
    big = len(urllib.parse.urlencode(
        _page_form("x", **{"pphrase[nuo][mem_saved]": "啊" * 1001}), doseq=True))
    assert big > 64 * 1024, "一条长话术就能顶破旧上限（整页约 {}KB）".format(small // 1024)
    assert _d.MAX_FORM_BODY > big * 2, "上限要留足余量：{}".format(_d.MAX_FORM_BODY)


def test_save_without_csrf_is_refused(srv):
    base, cfgp = srv
    cookie = _login(base)
    before = cfgp.read_text(encoding="utf-8")
    st, _b, _h = _req(base, "/personas", method="POST", headers=cookie,
                      data={"persona_form": "1", "ptext[nuo][extra]": "x"})
    assert st == 400
    assert cfgp.read_text(encoding="utf-8") == before


def test_no_change_is_reported(srv):
    base, _ = srv
    cookie = _login(base)
    page = _req(base, "/personas", headers=cookie)[1]
    st, body, _ = _req(base, "/personas", method="POST", headers=cookie,
                       data=_page_form(_csrf(page)))
    assert st == 200 and "没有检测到改动" in body


def test_reset_one_persona(srv):
    base, cfgp = srv
    cookie = _login(base)
    raw = json.loads(cfgp.read_text(encoding="utf-8"))
    raw["personas"] = {"rose_butler": {"name_short": "玫瑰管家"},
                       "nuo": {"extra": "额外"}}
    cfgp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cs._cache = None
    page = _req(base, "/personas", headers=cookie)[1]
    st, body, _ = _req(base, "/personas/reset", method="POST", headers=cookie,
                       data={"csrf": _csrf(page), "persona": "rose_butler"})
    assert st == 200 and "已恢复人设 rose_butler 的默认" in body
    saved = json.loads(cfgp.read_text(encoding="utf-8"))
    assert "rose_butler" not in saved["personas"]
    assert saved["personas"]["nuo"] == {"extra": "额外"}, "别人的改动不能被牵连"


def test_reset_all_personas(srv):
    base, cfgp = srv
    cookie = _login(base)
    raw = json.loads(cfgp.read_text(encoding="utf-8"))
    raw["personas"] = {"rose_butler": {"name_short": "玫瑰管家"}}
    cfgp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cs._cache = None
    page = _req(base, "/personas", headers=cookie)[1]
    st, body, _ = _req(base, "/personas/reset", method="POST", headers=cookie,
                       data={"csrf": _csrf(page), "persona": "*"})
    assert st == 200 and "已恢复全部人设的默认" in body
    assert "personas" not in json.loads(cfgp.read_text(encoding="utf-8"))


def test_reset_nothing_to_do(srv):
    base, cfgp = srv
    cookie = _login(base)
    page = _req(base, "/personas", headers=cookie)[1]
    before = cfgp.read_text(encoding="utf-8")
    st, body, _ = _req(base, "/personas/reset", method="POST", headers=cookie,
                       data={"csrf": _csrf(page), "persona": "rose_butler"})
    assert st == 200 and "本来就都是默认值" in body
    assert cfgp.read_text(encoding="utf-8") == before


def test_reset_without_csrf_is_refused(srv):
    base, cfgp = srv
    cookie = _login(base)
    before = cfgp.read_text(encoding="utf-8")
    st, _b, _h = _req(base, "/personas/reset", method="POST", headers=cookie,
                      data={"persona": "*"})
    assert st == 400
    assert cfgp.read_text(encoding="utf-8") == before


def test_other_pages_link_here(srv):
    base, _ = srv
    cookie = _login(base)
    for path in ("/", "/edit", "/features"):
        st, page, _ = _req(base, path, headers=cookie)
        assert st == 200, path
        assert "href='/personas'" in page, "{} 上没有入口".format(path)
