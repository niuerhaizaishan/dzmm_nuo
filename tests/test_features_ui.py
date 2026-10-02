"""功能与参数页（P12-D）：矩阵 / 参数 / 按群集合 / 强制复位 / CSP。

这一页的价值在于"配了真生效"，所以测试分三层：

1. **渲染**：65 个功能都在矩阵里、三态单选、参数带默认值与范围、
   停用的群要标出来、HTML 转义（不留注入点）。
2. **解析**：三态语义（跟随/开/关）、只写"与默认不同"的覆盖、
   参数留空 = 不覆盖、集合与默认一致就不写覆盖。
3. **端到端**：页面上勾的开关，过一遍真正的保存 → 再读回来 → 运行期生效。
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
from admin import features_ui as fui  # noqa: E402
from runtime import config_store as cs  # noqa: E402
from runtime import features as F  # noqa: E402
from runtime import tuning as T  # noqa: E402

import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

USER, PW = "nuo", "pw12345678"
CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "4a56be71-9ca6-4785-b45f-18725d187f1a"


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
def _clean(monkeypatch):
    dash._sessions.clear()
    dash._fails.clear()
    dash._csrf_tokens.clear()
    cs._cache = None
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    monkeypatch.delenv("DZMM_TUNING", raising=False)
    F.set_overrides({})
    T.set_overrides({})
    T.set_scopes({})
    yield
    cs._cache = None
    F.set_overrides({})
    T.set_overrides({})
    T.set_scopes({})


@pytest.fixture
def srv(monkeypatch, dbdir):
    cfgp = dbdir / "config.json"
    cfgp.write_text(json.dumps({
        "version": cs.SCHEMA_VERSION,
        "groups": [{"cid": CID_A, "alias": "认证妹子群", "enabled": True,
                    "screen": True, "push": True, "mute": False, "persona": ""},
                   {"cid": CID_B, "alias": "玫瑰房", "enabled": False,
                    "screen": False, "push": False, "mute": False, "persona": ""}],
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
    import re
    return re.search(r"name=['\"]?csrf['\"]?\s+value='([^']*)'", page).group(1)


# ---------------------------------------------------------------- 1. 渲染

def test_page_requires_login(srv):
    base, _ = srv
    st, body, _ = _req(base, "/features")
    assert st == 200 and "请登录" in body


def test_matrix_lists_every_feature(srv):
    base, _ = srv
    cookie = _login(base)
    st, page, _ = _req(base, "/features", headers=cookie)
    assert st == 200
    missing = [f.key for f in F.FEATURES if "gfeat[{}]".format(f.key) not in page]
    assert not missing, "矩阵里漏了功能：{}".format(missing)
    # 每个功能一行，且每行有"全局"三态 + 每群三态
    assert "跟随" in page and ">开<" in page and ">关<" in page
    assert "认证妹子群" in page and "玫瑰房" in page
    assert "群已停用" in page, "停用的群要标出来"


def test_params_show_default_range_and_unit(srv):
    base, _ = srv
    cookie = _login(base)
    _st, page, _ = _req(base, "/features", headers=cookie)
    assert "gtune[heat.climax_threshold]" in page
    assert "placeholder='默认 100'" in page
    assert "data-lo='1'" in page and "data-hi='1000'" in page
    assert "gtune[devil.base_prob]" in page
    assert "tune[{}][dice.timeout]".format(CID_A) in page, "按群覆盖也要能填"


def test_scope_and_reset_controls_present(srv):
    base, _ = srv
    cookie = _login(base)
    _st, page, _ = _req(base, "/features", headers=cookie)
    assert "scope[trend][{}]".format(CID_A) in page
    assert "scope[recap][{}]".format(CID_A) in page
    assert "action='/edit/reset'" in page and "复位" in page


def test_html_is_escaped(srv):
    """功能名/参数值都可能来自配置 ⇒ 一律转义（页面上不留注入点）。"""
    base, cfgp = srv
    d = json.loads(cfgp.read_text(encoding="utf-8"))
    d["groups"][0]["alias"] = "<script>alert(1)</script>"
    cfgp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    cs._cache = None
    cookie = _login(base)
    _st, page, _ = _req(base, "/features", headers=cookie)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_no_inline_javascript(srv):
    """CSP 只放开了 script-src 'self' ⇒ 页面里不许有内联脚本/事件属性。"""
    base, _ = srv
    cookie = _login(base)
    _st, page, _ = _req(base, "/features", headers=cookie)
    import re
    assert not re.search(r"<script(?![^>]*src=)", page), "不许有内联 <script>"
    assert "onclick" not in page and "onchange" not in page
    assert "<script src='/static/app.js' defer>" in page


def test_csp_allows_only_self_script(srv):
    base, _ = srv
    cookie = _login(base)
    st, _b, h = _req(base, "/features")
    csp = h.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp
    # 2026-10-02 线上事故：style-src 只有 'unsafe-inline' 时外链
    # /static/*.css 被静默拒载（整站裸 HTML）—— 外链样式必须 'self'。
    assert "style-src 'self'" in csp, csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "default-src 'none'" in csp
    assert h.get("X-Frame-Options") == "DENY"


def test_static_js_is_served_and_whitelisted(srv):
    base, _ = srv
    cookie = _login(base)
    st, body, h = _req(base, "/static/app.js", headers=cookie)
    assert st == 200 and "javascript" in h.get("Content-Type", "")
    assert "textContent" in body
    # 不许**使用** innerHTML（注释里提到这个词没关系）
    assert ".innerHTML" not in body and "insertAdjacentHTML" not in body, \
        "脚本要用 textContent（避免 XSS 面）"
    assert "eval(" not in body
    # 白名单：其它路径不给
    st2, _b2, _h2 = _req(base, "/static/../dashboard.py", headers=cookie)
    assert st2 == 404
    st3, _b3, _h3 = _req(base, "/static/nope.js", headers=cookie)
    assert st3 == 404


# ---------------------------------------------------------------- 2. 解析

def _reg():
    return F.registry()


def _params():
    return T.registry()


def _scopes():
    return tuple(T.SCOPE_DEFAULTS.items())


BASE = {"version": cs.SCHEMA_VERSION,
        "groups": [{"cid": CID_A, "alias": "A", "enabled": True},
                   {"cid": CID_B, "alias": "B", "enabled": True}],
        "bots": [{"key": "k", "cid": CID_A, "token_env": "T"}]}


def _pf(form, base=None, registry=None, scopes=None, params=None):
    """调用 parse_features，并自动带上"这一页渲染过"的隐藏标记。"""
    f = dict(form)
    f.setdefault("feat_form", ["1"])
    return fui.parse_features(f, base if base is not None else BASE,
                              registry or _reg(), scopes or _scopes(),
                              params or _params())


def test_tristate_semantics():
    """"" = 跟随（不写覆盖）；on/off = 覆盖。"""
    assert _pf({"gfeat[insult]": [""]})["features"] == {}, "选「默认」不该写覆盖"
    assert _pf({"gfeat[insult]": ["off"]})["features"] == {"insult": False}
    assert _pf({"gfeat[devil_roast]": ["on"]})["features"] == {}, \
        "默认就是开的功能，选「开」等于没改"


def test_partial_form_leaves_everything_untouched():
    """**没有标记 = 不是这一页发的** ⇒ 一个字都不动。

    复选框没勾时什么都不提交，与"整节没渲染"长得一模一样；
    按"空"处理会把默认非空的集合（趋势/回顾只给认证群）静默清空。
    """
    base = dict(BASE, features={"insult": False},
                tuning={"devil.base_prob": 0.2}, scopes={"trend": [CID_B]})
    p = fui.parse_features({"csrf": ["x"]}, base, _reg(), _scopes(), _params())
    assert p["untouched"] is True
    assert p["features"] == {"insult": False}
    assert p["tuning"] == {"devil.base_prob": 0.2}
    assert p["scopes"] == {"trend": [CID_B]}


def test_scope_section_not_rendered_is_left_alone():
    """即使带了页面标记，某个集合那一节没渲染也不该被清空。"""
    p = _pf({"gfeat[dice]": ["on"]}, base=BASE)
    assert p["scopes"] == {}, "没渲染的集合不进结果"


def test_only_differences_are_written():
    p = _pf({"gfeat[dice]": ["on"],          # 默认开 ⇒ 不写
             "gfeat[insult]": ["off"],       # 与默认不同 ⇒ 写
             "gfeat[{}][vote]".format(CID_A): ["off"],   # 按群覆盖 ⇒ 写
             "gtune[devil.base_prob]": ["0.03"],          # 与默认相同 ⇒ 不写
             "gtune[heat.climax_threshold]": ["120"]})    # 不同 ⇒ 写
    assert p["features"] == {"insult": False}
    assert p["tuning"] == {"heat.climax_threshold": 120}, \
        "参数要**规范化成数字**（别把数字写成字符串）"
    assert p["group_features"] == {CID_A: {"vote": False}}


def test_blank_param_means_no_override():
    p = _pf({"gtune[devil.base_prob]": [""],
             "tune[{}][dice.timeout]".format(CID_A): [""]})
    assert p["tuning"] == {} and p["group_tuning"] == {}


def test_per_group_following_global_needs_no_override():
    base = dict(BASE, features={"vote": False})
    p = _pf({"gfeat[{}][vote]".format(CID_A): ["off"]}, base=base)
    assert p["group_features"] == {}, "跟全局一样就不必写按群覆盖"


def test_scope_equal_to_default_is_not_written():
    """勾选结果与代码默认一致 ⇒ 不写覆盖（否则等于把默认值抄进配置）。"""
    from runtime.ctx import PRIMARY_CID
    # 浏览器会把"当前生效"的勾原样提交回来：trend 与 recap 都默认含认证群
    form = {"scope_seen[trend]": ["1"], "scope_seen[recap]": ["1"],
            "scope[trend][{}]".format(PRIMARY_CID): ["1"],
            "scope[recap][{}]".format(PRIMARY_CID): ["1"]}
    p = _pf(form)
    assert p["scopes"] == {}, "与默认一致就不该写覆盖"
    # 改成另一个群 ⇒ 与默认不同，要写
    form.pop("scope[trend][{}]".format(PRIMARY_CID))
    form["scope[trend][{}]".format(CID_B)] = ["1"]
    p = _pf(form)
    assert p["scopes"] == {"trend": [CID_B]}
    # trend 全不勾（但渲染过）⇒ 写空列表；recap 的勾还在 ⇒ 与默认一致，不写
    form.pop("scope[trend][{}]".format(CID_B))
    p = _pf(form)
    assert p["scopes"] == {"trend": []}, \
        "渲染过又全不勾 = 用户要把这个集合清空，必须写下来；recap 没动就不写"


def test_diff_lists_feature_and_param_changes():
    old = {"version": cs.SCHEMA_VERSION, "groups": BASE["groups"], "bots": BASE["bots"],
           "features": {"insult": False}, "tuning": {"dice.timeout": 60}}
    parsed = {"features": {"insult": True}, "tuning": {"dice.timeout": "60"},
              "scopes": {}, "group_features": {}, "group_tuning": {}}
    out = fui.features_diff(old, parsed, _reg(), _scopes())
    assert any("羞辱" in x for x in out), out
    assert not any("dice.timeout" in x for x in out), "没变的不该出现在清单里"


# ---------------------------------------------------------------- 3. 端到端

def test_save_end_to_end_and_takes_effect(srv, monkeypatch):
    """页面上关掉一个功能 → 保存 → 配置里真的有 → **运行期真的生效**。"""
    base, cfgp = srv
    cookie = _login(base)
    page = _req(base, "/features", headers=cookie)[1]
    csrf = _csrf(page)
    st, body, _ = _req(base, "/features", method="POST", headers=cookie, data={
        "csrf": csrf,
        "feat_form": "1",
        "gfeat[insult]": "off",
        "gfeat[{}][vote]".format(CID_A): "off",
        "gtune[heat.climax_threshold]": "120",
    })
    assert st == 200, body[:300]
    assert "已保存" in body and "本次改动" in body
    saved = json.loads(cfgp.read_text(encoding="utf-8"))
    assert saved["features"] == {"insult": False}
    assert saved["tuning"] == {"heat.climax_threshold": 120}, \
        "落盘的参数应当是数字（规范化过）"
    g = next(x for x in saved["groups"] if x["cid"] == CID_A)
    assert g["features"] == {"vote": False}
    # 群与 Bot 一个都没被这一页碰到
    assert [x["cid"] for x in saved["groups"]] == [CID_A, CID_B]
    assert saved["bots"][0]["key"] == "nuo_main"

    # 运行期生效：把这份配置喂给运行期模块（等价于重启后加载）
    F.set_overrides({k: v for k, v in saved["features"].items()})
    assert F.on("any", "insult") is False
    assert F.on("any", "vote") is True
    T.set_overrides(dict(saved["tuning"]))
    assert T.get("heat.climax_threshold") == 120


def test_save_rejects_out_of_range_param(srv):
    base, cfgp = srv
    cookie = _login(base)
    csrf = _csrf(_req(base, "/features", headers=cookie)[1])
    before = cfgp.read_text(encoding="utf-8")
    st, body, _ = _req(base, "/features", method="POST", headers=cookie,
                       data={"csrf": csrf, "feat_form": "1",
                             "gtune[devil.base_prob]": "5"})
    assert st == 400 and "未写入" in body
    assert cfgp.read_text(encoding="utf-8") == before


def test_save_without_csrf_is_refused(srv):
    base, cfgp = srv
    cookie = _login(base)
    before = cfgp.read_text(encoding="utf-8")
    st, _b, _h = _req(base, "/features", method="POST", headers=cookie,
                      data={"gfeat[insult]": "off"})
    assert st == 400
    assert cfgp.read_text(encoding="utf-8") == before


def test_no_change_is_reported(srv):
    base, _ = srv
    cookie = _login(base)
    csrf = _csrf(_req(base, "/features", headers=cookie)[1])
    st, body, _ = _req(base, "/features", method="POST", headers=cookie,
                       data={"csrf": csrf, "gfeat[dice]": "on"})
    assert st == 200 and "没有检测到改动" in body


def test_reset_endpoint_contract(srv, monkeypatch):
    """强制复位端点：**只做三件事** —— 校验 CSRF、调 `reset_feature`、记审计。

    这里用记录器替掉 `reset_feature`：端点自己的契约是"参数传对 + 不碰配置"，
    而"状态真的被清了"由 `test_feature_registry.py` 的单元测试负责
    （那里用 conftest 的 `state` 夹具，没有跨线程的存储语义问题）。
    """
    from runtime import features as feats
    calls = []

    def fake_reset(cid, key):
        calls.append((cid, key))
        return ["fasao"]

    monkeypatch.setattr(feats, "reset_feature", fake_reset)
    base, cfgp = srv
    cookie = _login(base)
    csrf = _csrf(_req(base, "/features", headers=cookie)[1])
    before = cfgp.read_text(encoding="utf-8")
    st, body, _ = _req(base, "/edit/reset", method="POST", headers=cookie,
                       data={"csrf": csrf, "cid": CID_A, "key": "scene"})
    assert st == 200 and "已复位" in body and "fasao" in body
    assert calls == [(CID_A, "scene")], "端点要用表单里的 cid/key 去调复位"
    assert cfgp.read_text(encoding="utf-8") == before, "复位不该改配置"
    assert "已复位" in cs.audit_path().read_text(encoding="utf-8"), "复位要留审计"


def test_reset_endpoint_reports_bad_key(srv):
    """未知功能 key：要报错，不能静默当成功。"""
    base, _ = srv
    cookie = _login(base)
    csrf = _csrf(_req(base, "/features", headers=cookie)[1])
    st, body, _ = _req(base, "/edit/reset", method="POST", headers=cookie,
                       data={"csrf": csrf, "cid": CID_A, "key": "no_such"})
    assert st == 400 and "复位失败" in body


def test_reset_without_csrf_is_refused(srv):
    base, _ = srv
    cookie = _login(base)
    st, _b, _h = _req(base, "/edit/reset", method="POST", headers=cookie,
                      data={"cid": CID_A, "key": "scene"})
    assert st == 400


def test_edit_page_still_works_and_links_here(srv):
    """老页面不受影响，并且有入口能点过来。"""
    base, _ = srv
    cookie = _login(base)
    st, page, _ = _req(base, "/edit", headers=cookie)
    assert st == 200 and "保存配置" in page
    assert "href='/features'" in page
