"""热加载（P12-F）：改完配置/提示词**不重启**就生效。

这一步的难点不是"能重读文件"，而是**边界与安全**，所以测试分四层：

1. **该生效的生效**：功能开关 / 参数 / 按群集合 / 人设文本与话术 /
   每群用哪个人设 / 提示词文件 —— 都必须在**不重启**的情况下换掉运行期的值。
2. **不该生效的绝不生效**：群清单、静默·筛查·推送、Bot 注册表是结构性的
   （Socket.IO 订阅通道在启动时就用群列表建好了）⇒ 只报告"需要重启"，不改运行期。
3. **坏配置不半途生效**：文件写坏时必须**保持旧值**、记下原因、且不要每轮刷屏。
4. **整体替换**：换覆盖表时不许留下旧表的键（"半新半旧"比不生效更难查）。
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "admin"))

import personas as P  # noqa: E402
from runtime import config_store as cs  # noqa: E402
from runtime import features as F  # noqa: E402
from runtime import hotload as H  # noqa: E402
from runtime import prompts as RP  # noqa: E402
from runtime import tuning as T  # noqa: E402

CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "4a56be71-9ca6-4785-b45f-18725d187f1a"
CID_X = "11111111-2222-3333-4444-555555555555"

DEFAULTS = {
    "groups": [{"cid": CID_A, "alias": "A"}, {"cid": CID_B, "alias": "B"}],
    "mute": (), "screen": (), "push": (),
    "aliases": {}, "bots": [{"key": "k", "name": "n", "cid": CID_A,
                             "token_env": "T"}],
    "personas": tuple(P.PERSONAS),
}


def _cfg(**over):
    raw = {
        "version": cs.SCHEMA_VERSION,
        "groups": [{"cid": CID_A, "alias": "认证群", "enabled": True, "persona": ""},
                   {"cid": CID_B, "alias": "玫瑰房", "enabled": True,
                    "persona": "rose_butler"}],
        "bots": [{"key": "nuo_main", "name": "小小糯-bot", "cid": CID_A,
                  "token_env": "DZMM_BOT_TOKEN"}],
    }
    raw.update(over)
    return raw


@pytest.fixture
def cfgfile(monkeypatch, dbdir):
    p = dbdir / "config.json"
    p.write_text(json.dumps(_cfg(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DZMM_CONFIG", str(p))
    cs._cache = None
    yield p
    cs._cache = None


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每个用例都从"干净运行期"开始，并把热加载模块状态复位。"""
    F.set_overrides({})
    T.set_overrides({})
    T.set_scopes({})
    P.set_content_overrides({})
    P.set_config_personas({})
    monkeypatch.delenv("DZMM_HOTLOAD", raising=False)
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    monkeypatch.delenv("DZMM_TUNING", raising=False)
    H._LAST.update({"at": 0.0, "mtime": 0.0, "report": None, "error": "",
                    "count": 0})
    H._last_prompt_check[0] = 0.0
    H._prompt_sig[0] = None
    yield
    # 先撤销 monkeypatch：有用例把 `F.set_overrides` 换成会抛异常的桩，
    # 而 monkeypatch 的收尾比这个夹具晚 ⇒ 不先撤销，下面的复位自己会炸。
    monkeypatch.undo()
    F.set_overrides({})
    T.set_overrides({})
    T.set_scopes({})
    P.set_content_overrides({})
    P.set_config_personas({})


def _write(path, raw, bump=True):
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    if bump:
        # 显式推一下 mtime：某些文件系统的 mtime 粒度较粗，
        # 连续两次写可能落在同一刻度上，测试就会假失败。
        t = time.time() + 2
        os.utime(str(path), (t, t))


def _live():
    """假的"运行期结构性配置"：与文件里的初始那份一致。"""
    return {"groups": (CID_A, CID_B), "mute": (), "screen": (), "push": (),
            "bots": (("nuo_main", CID_A),)}


def _boot(cfgfile, live=None, defaults=None):
    cs.reload(defaults or DEFAULTS)
    H.configure(defaults or DEFAULTS, live or _live)


# ---------------------------------------------------------------- 1. 该生效的生效

def test_no_change_is_a_noop(cfgfile):
    _boot(cfgfile)
    assert H.maybe_reload() is None
    assert H.status()["count"] == 0


def test_feature_switch_takes_effect_without_restart(cfgfile):
    _boot(cfgfile)
    assert F.on(CID_A, "insult") is True          # 代码默认
    _write(cfgfile, _cfg(features={"insult": False}))
    rep = H.maybe_reload()
    assert rep and rep["ok"], rep
    assert F.on(CID_A, "insult") is False, "热加载后开关必须变了"
    assert any("insult" in x for x in rep["changes"]["features"])
    assert H.status()["count"] == 1
    assert H.page_state()["pending"] is False, "应用过就不该再显示待生效"


def test_tuning_and_scopes_take_effect(cfgfile):
    _boot(cfgfile)
    _write(cfgfile, _cfg(tuning={"heat.climax_threshold": 137},
                         scopes={"trend": [CID_B]}))
    H.maybe_reload()
    assert T.get("heat.climax_threshold") == 137
    assert T.in_scope("trend", CID_B) is True
    assert T.in_scope("trend", CID_A) is False


def test_persona_texts_and_phrases_take_effect(cfgfile):
    _boot(cfgfile)
    assert P.p("mem_saved", CID_A) == P.PHRASES["nuo"]["mem_saved"]
    _write(cfgfile, _cfg(personas={"nuo": {"name_short": "糯糯子",
                                           "phrases": {"mem_saved": "记住啦"}}}))
    H.maybe_reload()
    assert P.persona_for(CID_A)["name_short"] == "糯糯子"
    assert P.p("mem_saved", CID_A) == "记住啦"
    assert P.persona_key_for(CID_B) == "rose_butler", "别的人设不受影响"


def test_group_persona_selection_takes_effect(cfgfile):
    """`groups[].persona` 是热配置：换人设不用重启（群清单本身不是）。"""
    _boot(cfgfile)
    assert P.persona_key_for(CID_B) == "rose_butler"
    raw = _cfg()
    raw["groups"][1]["persona"] = "nuo"
    _write(cfgfile, raw)
    H.maybe_reload()
    assert P.persona_key_for(CID_B) == "nuo"


def test_whole_table_is_replaced_not_merged(cfgfile):
    """整体替换：新表里没有的键必须消失（否则会留下"上一版还开着"的鬼）。"""
    _boot(cfgfile)
    _write(cfgfile, _cfg(features={"insult": False}))
    H.maybe_reload()
    assert F.overrides() == {"insult": False}
    _write(cfgfile, _cfg(features={"vote": False}))
    H.maybe_reload()
    assert F.overrides() == {"vote": False}, "旧键 insult 必须被清掉"
    assert F.on(CID_A, "insult") is True, "回到代码默认"


def test_prompt_file_change_takes_effect(cfgfile, monkeypatch, dbdir):
    """提示词文件改了也要生效：**不用重启、不用改配置**。"""
    import brains
    pd = dbdir / "prompts"
    pd.mkdir()
    monkeypatch.setenv("DZMM_PROMPT_DIR", str(pd))
    RP.clear_cache()
    saved = {n: getattr(brains, n) for n in brains._PROMPT_DEFAULTS}
    _boot(cfgfile)
    name = sorted(brains._PROMPT_DEFAULTS)[0]
    original = getattr(brains, name)
    try:
        (pd / (name + ".md")).write_text("【热加载测试】" + original, encoding="utf-8")
        H._last_prompt_check[0] = 0.0            # 别等那 5 秒的节流
        H._prompt_sig[0] = ("force", 0, 0.0, 0)
        rep = H.maybe_reload()
        assert rep and rep["source"] == "prompts", rep
        assert getattr(brains, name).startswith("【热加载测试】")
        assert name in rep["prompts"]
    finally:
        for n, v in saved.items():
            setattr(brains, n, v)
        RP.clear_cache()


def test_env_off_disables_everything(cfgfile, monkeypatch):
    _boot(cfgfile)
    monkeypatch.setenv("DZMM_HOTLOAD", "off")
    _write(cfgfile, _cfg(features={"insult": False}))
    assert H.maybe_reload() is None
    assert F.on(CID_A, "insult") is True, "关了热加载就不该偷偷生效"
    assert H.status()["enabled"] is False


# ---------------------------------------------------------------- 2. 结构性：只报告

def test_structural_changes_are_reported_not_applied(cfgfile):
    _boot(cfgfile)
    raw = _cfg()
    raw["groups"].append({"cid": CID_X, "alias": "新群", "enabled": True})
    _write(cfgfile, raw)
    rep = H.maybe_reload()
    assert rep and rep["ok"]
    assert rep["structural"], "加了群必须报「需要重启」"
    assert any("群清单" in x for x in rep["structural"])
    st = H.page_state()
    assert st["pending"] is False, "已经处理过这个版本了"
    assert st["structural"], \
        "但结构性差异**不会**自己消失（它永远不会被应用）⇒ 必须一直提示重启"


def test_structural_diff_detects_each_kind(cfgfile):
    _boot(cfgfile)
    cfg = cs._parse(_cfg(mute=[], screen=[], push=[]), DEFAULTS)
    assert H.structural_diff(live=_live(), cfg=cfg) == []

    raw = _cfg()
    raw["groups"][0]["enabled"] = False          # 群清单/启用变了
    cfg2 = cs._parse(raw, DEFAULTS)
    assert H.structural_diff(live=_live(), cfg=cfg2)

    raw = _cfg()
    raw["groups"][0]["mute"] = True
    cfg3 = cs._parse(raw, DEFAULTS)
    assert any("静默" in x for x in H.structural_diff(live=_live(), cfg=cfg3))

    raw = _cfg()
    raw["bots"][0]["key"] = "another"
    cfg4 = cs._parse(raw, DEFAULTS)
    assert any("Bot" in x for x in H.structural_diff(live=_live(), cfg=cfg4))


def test_page_state_pending_until_applied(cfgfile):
    _boot(cfgfile)
    assert H.page_state()["pending"] is False
    _write(cfgfile, _cfg(features={"insult": False}))
    st = H.page_state()
    assert st["pending"] is True, "文件比运行期新 ⇒ 待生效"
    H.maybe_reload()
    assert H.page_state()["pending"] is False


# ---------------------------------------------------------------- 3. 坏配置

def test_invalid_config_keeps_old_values_and_does_not_spam(cfgfile):
    _boot(cfgfile)
    _write(cfgfile, _cfg(features={"insult": False}))
    H.maybe_reload()
    assert F.on(CID_A, "insult") is False

    bad = _cfg(features={"typo_feature": False})       # 未登记的功能名
    _write(cfgfile, bad)
    rep = H.maybe_reload()
    assert rep and rep.get("invalid") and not rep["ok"]
    assert "不是已登记" in "".join(rep["errors"])
    assert F.on(CID_A, "insult") is False, "坏配置时运行期必须保持原样（不是回到默认）"
    assert H.status()["error"], "要把原因记下来给页面看"
    # 同一个坏版本不要每轮重复报（mtime 已记住）
    assert H.maybe_reload() is None


def test_page_state_says_file_is_invalid(cfgfile):
    _boot(cfgfile)
    _write(cfgfile, _cfg(features={"typo_feature": False}))
    st = H.page_state()
    assert st["invalid"] is True and st["error"], st


def test_reload_now_reports_error_when_unconfigured(monkeypatch):
    """不是"应用配置的那个进程"（例如面板进程）⇒ 直接说不行，别假装成功。"""
    monkeypatch.setattr(H, "_OWNER", False)
    monkeypatch.setattr(H, "_LIVE_FN", None)
    monkeypatch.setattr(H, "_DEFAULTS", None)
    rep = H.reload_now()
    assert rep["ok"] is False and rep["errors"]
    assert H.page_state() is None, "没状态文件 ⇒ 页面退回旧的「要重启」提示"


def test_reload_now_forces_even_without_mtime_change(cfgfile):
    _boot(cfgfile)
    _write(cfgfile, _cfg(tuning={"heat.climax_threshold": 141}))
    assert T.get("heat.climax_threshold") != 141
    rep = H.reload_now()
    assert rep["ok"] and rep["source"] == "manual"
    assert T.get("heat.climax_threshold") == 141


def test_errors_in_apply_do_not_raise(cfgfile, monkeypatch):
    """应用阶段出异常也必须收进报告 —— 热加载不能把主循环拖垮。"""
    _boot(cfgfile)

    def boom(_m):
        raise RuntimeError("boom")

    monkeypatch.setattr(F, "set_overrides", boom)
    _write(cfgfile, _cfg(features={"insult": False}))
    rep = H.maybe_reload()
    assert rep is not None
    assert rep["ok"] is False and any("boom" in e for e in rep["errors"])


# ---------------------------------------------------------------- 4. 与页面的接口

def test_live_structural_is_empty_without_bot(monkeypatch):
    H._LIVE_FN = None
    assert H.live_structural() == {}
    assert H.structural_diff(live={}, cfg=cs._parse(_cfg(), DEFAULTS)) == []


def test_parts_diff_reports_human_readable_lines():
    old = {"features": {"a": True}, "tuning": {"p": 1}, "scopes": {"trend": [CID_A]},
           "personas": {"nuo": {"name_short": "旧", "phrases": {"mem_saved": "x"}}},
           "persona_map": {CID_B: "rose_butler"}}
    new = {"features": {"a": False}, "tuning": {"p": 2}, "scopes": {"trend": []},
           "personas": {"nuo": {"name_short": "新", "phrases": {"mem_saved": "y"}}},
           "persona_map": {CID_B: "nuo"}}
    d = H.parts_diff(old, new)
    assert d["features"] and "开 → 关" in d["features"][0]
    assert d["tuning"] and "1 → 2" in d["tuning"][0]
    assert d["scopes"] and "1 个群 → 0 个群" in d["scopes"][0]
    assert any("name_short" in x for x in d["personas"])
    assert any("话术" in x for x in d["personas"])
    assert any("的人设" in x for x in d["personas"])


def test_no_prompt_is_captured_as_a_default_argument():
    """热加载靠"调用时查模块全局"。

    如果有谁把 prompt 当**默认参数**捕获（`def f(p=CMD_PROMPT)`），
    那它就会永远用启动时那份 —— 热加载对它无效，而且完全看不出来。
    这条静态扫一遍源码钉死它。
    """
    import ast
    tree = ast.parse((ROOT / "brains.py").read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x]:
            if isinstance(d, ast.Name) and d.id.endswith("_PROMPT"):
                bad.append((node.name, d.id, getattr(d, "lineno", 0)))
    assert not bad, "这些函数把 prompt 捕获成了默认参数：{}".format(bad)


# ---------------------------------------------------------------- 5. 运维后台

def _make_server(monkeypatch, dbdir):
    """起一个真后台（**不配密码** ⇒ 走"未配密码"那条路，顺带回归 P12-0 修的 CSRF 键）。"""
    import threading
    from http.server import ThreadingHTTPServer
    import dashboard as dash
    monkeypatch.delenv("DZMM_ADMIN_USER", raising=False)
    monkeypatch.delenv("DZMM_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DZMM_BOT_TOKEN", "dummy")
    dash._sessions.clear()
    dash._csrf_tokens.clear()
    s = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:{}".format(s.server_address[1])


@pytest.fixture
def admin(monkeypatch, dbdir):
    s, base = _make_server(monkeypatch, dbdir)
    try:
        yield base
    finally:
        s.shutdown()
        s.server_close()


def _req(base, path, data=None):
    import urllib.error
    import urllib.parse
    import urllib.request
    body = urllib.parse.urlencode(data, doseq=True).encode("utf-8") if data else None
    r = urllib.request.Request(base + path, data=body)
    if body:
        r.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _csrf_of(page):
    import re
    return re.search(r"name='csrf' value='([^']*)'", page).group(1)


def test_reload_endpoint_applies_and_reports(cfgfile, admin):
    _boot(cfgfile)
    st, page = _req(admin, "/features")
    assert st == 200
    _write(cfgfile, _cfg(tuning={"heat.climax_threshold": 152}))
    assert T.get("heat.climax_threshold") != 152
    st, body = _req(admin, "/reload", {"csrf": _csrf_of(page)})
    assert st == 200, body[:200]
    assert "已热加载" in body and "没有重启" in body
    assert T.get("heat.climax_threshold") == 152, "点了按钮就得真生效"
    # 生效之后页面上要显示"已生效"，不能还挂着"待重启"
    st, page2 = _req(admin, "/features")
    assert "已生效" in page2 and "需要重启" not in page2


def test_reload_endpoint_needs_csrf(cfgfile, admin):
    _boot(cfgfile)
    _write(cfgfile, _cfg(tuning={"heat.climax_threshold": 153}))
    st, _b = _req(admin, "/reload", {"csrf": "wrong"})
    assert st == 400
    assert T.get("heat.climax_threshold") != 153, "没通过 CSRF 就一个字都不能动"


def test_pages_show_restart_hint_for_structural_changes(cfgfile, admin):
    """结构性改动：三个页面都要说"需要重启"，而不是含糊地说"已保存"。"""
    _boot(cfgfile)
    raw = _cfg()
    raw["groups"].append({"cid": CID_X, "alias": "新群", "enabled": True})
    _write(cfgfile, raw)
    for path in ("/edit", "/features", "/personas"):
        st, page = _req(admin, path)
        assert st == 200, path
        assert "需要重启" in page, path
        assert "群清单" in page, path


def test_api_summary_exposes_hotload_state(cfgfile, admin):
    _boot(cfgfile)
    st, body = _req(admin, "/api/summary")
    assert st == 200
    data = json.loads(body)
    assert data["hotload"]["configured"] is True
    assert data["hotload"]["enabled"] is True
    assert "hotload" in data["sections"], "只读面板上也要有这一段"


def test_invalid_file_shows_red_banner(cfgfile, admin):
    _boot(cfgfile)
    _write(cfgfile, _cfg(features={"no_such_feature": True}))
    st, page = _req(admin, "/features")
    assert st == 200
    assert "不合法" in page and "运行期保持原样" in page


# ---------------------------------------------------------------- 6. 跨进程
#
# ⚠️ 生产上 `bot.py` 与 `admin/dashboard.py` 是**两个进程**（同一个容器里，
# entrypoint.sh 各起一个）。面板读不到机器人的内存，所以靠两个小文件通信：
#   hotload_status.json（机器人写、面板读）
#   hotload_request    （面板写、机器人消费）
# 第一版漏了这件事：热加载其实生效了，但面板显示"没配置、0 次"，
# 按钮点了也没反应 —— 而且**看起来像是功能坏了**。这些用例钉住跨进程这条路。

def test_status_file_is_written_on_apply(cfgfile):
    _boot(cfgfile)
    _write(cfgfile, _cfg(tuning={"heat.climax_threshold": 161}))
    H.maybe_reload()
    data = json.loads(H.status_file().read_text(encoding="utf-8"))
    assert data["count"] >= 1
    assert data["mtime"] > 0
    assert data["n_changes"] >= 1
    assert data["live_structural"], "要带上运行期的结构性快照（面板拿它比）"
    assert data["pid"] == os.getpid()


def test_status_file_records_invalid_config(cfgfile):
    _boot(cfgfile)
    _write(cfgfile, _cfg(features={"nope": True}))
    H.maybe_reload()
    data = json.loads(H.status_file().read_text(encoding="utf-8"))
    assert data["invalid"] is True and data["error"]


def test_request_file_is_consumed_and_forces_reload(cfgfile):
    """面板点按钮 = 写请求文件；机器人下一轮必须**消费掉并强制重读**。"""
    _boot(cfgfile)
    _write(cfgfile, _cfg(tuning={"heat.climax_threshold": 162}))
    # 先让 mtime 检查"看过"这一版（模拟：文件是合法的新版本但还没被应用）
    H._LAST["mtime"] = H._config_mtime()
    assert H.maybe_reload() is None, "mtime 没变 ⇒ 自动路径不动手"
    assert T.get("heat.climax_threshold") != 162
    assert H.request_reload() is True
    assert H.request_file().is_file()
    rep = H.maybe_reload()
    assert rep and rep["source"] == "manual", rep
    assert T.get("heat.climax_threshold") == 162
    assert not H.request_file().is_file(), "请求文件必须被消费掉（否则每轮都重放）"
    assert H.maybe_reload() is None


def test_page_state_falls_back_to_status_file(cfgfile, monkeypatch):
    """面板进程：没有运行期内存 ⇒ 读状态文件；文件也没有 ⇒ None（保守）。"""
    _boot(cfgfile)
    _write(cfgfile, _cfg(features={"insult": False}))
    H.maybe_reload()
    # 模拟"面板进程"：同一个模块，但 configure 没被调用过
    monkeypatch.setattr(H, "_OWNER", False)   # 假装这是面板进程（不接管运行期）
    monkeypatch.setattr(H, "_LIVE_FN", None)
    assert H.configured() is False
    st = H.page_state()
    assert st is not None and st["out_of_process"] is True
    assert st["count"] == 1 and st["at_text"]
    assert st["pending"] is False, "机器人已经应用过 ⇒ 不该显示待生效"
    assert st["last_changes"], "面板要能看到改了什么"


def test_page_state_from_file_says_pending_when_file_newer(cfgfile, monkeypatch):
    _boot(cfgfile)
    H.maybe_reload()                      # 写一次状态文件
    monkeypatch.setattr(H, "_OWNER", False)   # 假装这是面板进程（不接管运行期）
    monkeypatch.setattr(H, "_LIVE_FN", None)
    _write(cfgfile, _cfg(features={"insult": False}))
    st = H.page_state()
    assert st["pending"] is True


def test_page_state_from_file_reports_structural_forever(cfgfile, monkeypatch):
    """结构性改动**永远不会**被应用 ⇒ 面板必须一直显示"要重启"。

    （它和"待生效"不同：待生效会自己消失，这个不会。用机器人写进状态文件的
    运行期快照来比，所以面板进程也能判断。）
    """
    _boot(cfgfile)
    raw = _cfg()
    raw["groups"].append({"cid": CID_X, "alias": "新群", "enabled": True})
    _write(cfgfile, raw)
    H.maybe_reload()                      # 报告一次"需要重启"，状态文件里带上快照
    monkeypatch.setattr(H, "_OWNER", False)   # 假装这是面板进程（不接管运行期）
    monkeypatch.setattr(H, "_LIVE_FN", None)
    st = H.page_state()
    assert st["pending"] is False
    assert st["structural"], "结构性差异必须一直显示"
    assert any("群清单" in x for x in st["structural"])
    # 页面上的话术也要对
    from admin import config_ui
    html = config_ui.status_block(st, csrf="tok")
    assert "需要重启" in html and "群清单" in html


def test_page_state_none_without_status_file(cfgfile, monkeypatch):
    _boot(cfgfile)
    monkeypatch.setattr(H, "_OWNER", False)   # 假装这是面板进程（不接管运行期）
    monkeypatch.setattr(H, "_LIVE_FN", None)
    try:
        H.status_file().unlink()
    except OSError:
        pass
    assert H.page_state() is None, "宁可退回旧的「要重启」提示，也不能谎报已生效"


def test_dashboard_reload_button_only_requests(cfgfile, admin, monkeypatch):
    """面板进程点按钮 ⇒ 只写请求文件（不试图在本进程应用）。"""
    _boot(cfgfile)
    monkeypatch.setattr(H, "_OWNER", False)   # 假装这是面板进程（不接管运行期）      # 假装这是面板进程
    monkeypatch.setattr(H, "_LIVE_FN", None)
    _write(cfgfile, _cfg(tuning={"heat.climax_threshold": 163}))
    st, page = _req(admin, "/features")
    st, body = _req(admin, "/reload", {"csrf": _csrf_of(page)})
    assert st == 200
    assert "已请求机器人进程立即热加载" in body
    assert H.request_file().is_file()
    assert T.get("heat.climax_threshold") != 163, "面板不该自己应用"
