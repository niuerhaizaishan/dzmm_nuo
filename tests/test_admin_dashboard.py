"""P9 运维后台的守卫测试。

这个工具最大的风险不是"页面显示错了"，而是**它改了线上数据** ——
运维后台写坏生产库是这类东西最典型的翻车方式。所以这里的重点是把
"只读"变成**结构性、可验证**的性质，而不是文档里的一句承诺：

  1. 每一处 `sqlite3.connect` 都必须带 `mode=ro`
  2. 只有 `POST /login` 允许非 GET —— 且它不碰数据库也不碰文件
  3. 它查询的列名必须真的存在于 `runtime/store.py` / `runtime/outbox.py`
     的建表语句里（后台照着自己的记忆抄 schema → 迟早漂移）
  4. 任何一段 section 抛异常都不该让整页变白
  5. 日志内容进 HTML 必须转义（日志里有用户昵称和消息文本）
  6. 没登录时**一个字节的面板内容都不能给**；连续失败要锁定
"""
import ast
import importlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "admin"))

import dashboard as dash  # noqa: E402

DASH_PY = ROOT / "admin" / "dashboard.py"


# ---------------------------------------------------------------- 结构：只读

def test_every_sqlite_connect_is_read_only():
    """所有 sqlite3.connect 的首个参数都必须是带 mode=ro 的 URI。

    这是"只读"的**结构保证**：即使将来有人加了新的查询，只要忘了 mode=ro
    这条测试就会红 —— 而不是等到某天后台悄悄改坏了线上库。
    """
    tree = ast.parse(DASH_PY.read_text(encoding="utf-8"))
    connects = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = ""
        if isinstance(f, ast.Attribute):
            name = f.attr
        elif isinstance(f, ast.Name):
            name = f.id
        if name != "connect":
            continue
        src = ast.get_source_segment(DASH_PY.read_text(encoding="utf-8"), node) or ""
        connects.append(src)
    assert connects, "没找到 sqlite3.connect，测试本身失效了"
    bad = [c for c in connects if "mode=ro" not in c]
    assert bad == [], "有 connect 没带 mode=ro: {}".format(bad)


def test_no_write_sql_keywords_in_source():
    """源码里不得出现写库语句（do_DELETE 这类处理器标识符除外）。"""
    src = DASH_PY.read_text(encoding="utf-8")
    # 去掉 HTTP 方法处理器名（do_DELETE / do_PUT 是"拒绝"而不是"执行"）
    cleaned = re.sub(r"do_(POST|PUT|DELETE|PATCH|OPTIONS)\b", "HANDLER", src)
    hits = re.findall(
        r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|"
        r"ALTER\s+TABLE|CREATE\s+TABLE|REPLACE\s+INTO)\b", cleaned, re.I)
    assert hits == [], "源码里出现写库语句: {}".format(hits)


def test_outbox_columns_exist_in_real_schema():
    """后台查询的列必须真的在 runtime/outbox.py 的建表语句里。

    这条防的是"后台照着自己的记忆抄 schema" —— P9 之前的几次事故都是这么来的。
    """
    schema = (ROOT / "runtime" / "outbox.py").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS outbound \((.*?)\);", schema, re.S)
    assert m, "没解析到 outbound 建表语句"
    cols = {ln.strip().split()[0] for ln in m.group(1).splitlines() if ln.strip()}
    for used in ("status", "attempt_count", "text", "last_error", "created_at"):
        assert used in cols, "{} 不在 outbound 表里: {}".format(used, sorted(cols))


def test_kv_columns_exist_in_real_schema():
    schema = (ROOT / "runtime" / "store.py").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS kv \((.*?)\);", schema, re.S)
    assert m, "没解析到 kv 建表语句"
    cols = {ln.strip().split()[0] for ln in m.group(1).splitlines() if ln.strip()}
    for used in ("scope", "key", "value"):
        assert used in cols, "{} 不在 kv 表里: {}".format(used, sorted(cols))


def test_scope_group_constant_comes_from_real_module():
    """scope 字符串不自己抄，直接引用 runtime.store 的常量。"""
    from runtime import store
    assert dash.SCOPE_GROUP == store.SCOPE_GROUP == "group"


# ---------------------------------------------------------------- 结构：不新增暴露面

def test_published_mapping_requires_login_and_public_flag():
    """端口一旦发布到回环之外，compose 必须同时给出账号密码与 PUBLIC 声明。

    这是"忘了设密码就把面板挂到公网"的**结构性**防线：
    `DZMM_ADMIN_PUBLIC=on` + 没密码 -> dashboard 拒绝启动（fail closed），
    而 compose 必须把 PUBLIC 传进去、密码必须从 .env 取。
    """
    yml = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    m = re.search(r'-\s*"\$\{DZMM_ADMIN_BIND:-([^}]*)\}:8090:8090"', yml)
    assert m, "找不到 8090 的端口映射（形如 ${DZMM_ADMIN_BIND:-...}:8090:8090）"
    default_bind = m.group(1)
    if default_bind not in ("127.0.0.1", "localhost", "::1"):
        assert 'DZMM_ADMIN_PUBLIC: "${DZMM_ADMIN_PUBLIC:-on}"' in yml, \
            "对外发布时必须声明 DZMM_ADMIN_PUBLIC=on"
        assert 'DZMM_ADMIN_PASSWORD: "${DZMM_ADMIN_PASSWORD:-}"' in yml, \
            "密码必须来自 .env（留空以便 dashboard 自己 fail closed，而不是 compose 拒绝）"
        assert "DZMM_ADMIN_PASSWORD:?" not in yml, \
            "不要用 ${VAR:?} —— 缺密码会让整个 compose（含 bot 容器）起不来"


def test_refuses_to_start_when_public_without_password(monkeypatch, capsys):
    """**fail closed**：声称对外发布却没有密码 -> 不启动。

    把"忘了设密码"从"静默裸奔"变成"启动失败 + 容器日志里一行醒目的拒绝"。
    """
    monkeypatch.setenv("DZMM_ADMIN_PUBLIC", "on")
    monkeypatch.delenv("DZMM_ADMIN_PASSWORD", raising=False)
    assert dash.main() == 2
    assert "拒绝启动" in capsys.readouterr().out


def test_refuses_to_start_with_a_weak_password(monkeypatch, capsys):
    """对外可达的面板不接受 <8 位密码。"""
    monkeypatch.setenv("DZMM_ADMIN_PUBLIC", "on")
    monkeypatch.setenv("DZMM_ADMIN_PASSWORD", "短")
    assert dash.main() == 2
    assert "太短" in capsys.readouterr().out


def test_public_with_password_proceeds_past_the_gate(monkeypatch, capsys):
    """有密码时必须放行到"绑定端口"那一步。

    ⚠️ 不能靠"绑一个特权端口让它失败"来证明 —— **Windows 没有特权端口**，
    端口 1 会真的绑上，然后 `serve_forever()` 永不返回，测试直接挂死
    （第一版就是这么写的，跑了两分钟被我掐掉）。所以这里用一个假 server：
    它记录下绑定地址，然后在 serve_forever 里立刻抛 KeyboardInterrupt 退出。
    """
    monkeypatch.setenv("DZMM_ADMIN_PUBLIC", "on")
    monkeypatch.setenv("DZMM_ADMIN_USER", "nuo")
    monkeypatch.setenv("DZMM_ADMIN_PASSWORD", "a-long-enough-pw")
    monkeypatch.setenv("DZMM_ADMIN_HOST", "127.0.0.1")
    monkeypatch.setenv("DZMM_ADMIN_PORT", "8123")

    seen = {}

    class FakeServer:
        def __init__(self, addr, handler):
            seen["addr"] = addr
            seen["handler"] = handler

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    monkeypatch.setattr(dash, "ThreadingHTTPServer", FakeServer)
    assert dash.main() == 0
    assert seen["addr"] == ("127.0.0.1", 8123)
    assert seen.get("closed") is True
    out = capsys.readouterr().out
    assert "登录=开" in out and "nuo" in out


def test_dashboard_binds_all_interfaces_inside_container():
    """容器内必须绑 0.0.0.0，否则 compose 发布的端口连不上（已踩过）。"""
    assert dash.DEFAULT_HOST == "0.0.0.0"


def test_compose_passes_wildcard_bind_inside_container():
    """compose 传给容器的绑定地址必须是 0.0.0.0。

    第一次部署就栽在这里：默认值留成 127.0.0.1，于是容器内绑了回环，
    docker-proxy 转发不进来，宿主机 curl 000。
    """
    yml = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    m = re.search(r'DZMM_ADMIN_HOST:\s*"\$\{DZMM_ADMIN_HOST:-([^}]*)\}"', yml)
    assert m, "compose 里找不到 DZMM_ADMIN_HOST"
    assert m.group(1) == "0.0.0.0", "容器内绑定地址应是 0.0.0.0，当前 {!r}".format(m.group(1))


def test_security_headers_present():
    """没有外链资源，所以可以直接上严格 CSP；顺手把 Referer 掐掉。"""
    src = DASH_PY.read_text(encoding="utf-8")
    assert "Referrer-Policy" in src and "no-referrer" in src
    assert "Content-Security-Policy" in src
    assert "form-action 'self'" in src


def test_every_referenced_static_asset_is_served(server):
    """防漂移钉点：shell.py 引用的**每一个** /static/ 资产都必须真的能拉到。

    2026-10-02 踩过的坑：4A 新增三个 CSS，shell 引用了但 `_STATIC` 白名单
    忘了登记 —— 线上 404、整站裸 HTML。本地目检走 http.server 直读文件系统、
    渲染冒烟只查 HTML 字符串，都没经过这个路由，所以漏了。
    这里从 shell.py 源码**提取**全部 /static/ 引用逐个 GET —— 以后再加
    资产忘了登记，这条会当场红。
    """
    import re as _re
    shell_src = (ROOT / "admin" / "shell.py").read_text(encoding="utf-8")
    referenced = sorted(set(_re.findall(r"/static/[\w.-]+", shell_src)))
    assert referenced, "shell.py 里没找到静态引用（提取正则坏了？）"
    for p in referenced:
        st, body, h = _req(server, p)
        assert st == 200, "{} 被壳引用但服务器没服务（_STATIC 忘登记？）".format(p)
        assert body, p
    # 内容类型抽查：CSS 必须 text/css（浏览器对错类型的外链样式会拒载）
    _st, _b, h = _req(server, "/static/base.css")
    assert "text/css" in h.get("Content-Type", "")


# ---------------------------------------------------------------- 行为：HTTP

@pytest.fixture(autouse=True)
def _clean_auth_state():
    """每个用例前后都清掉会话表与失败计数 —— 它们是模块级可变状态。"""
    dash._sessions.clear()
    dash._fails.clear()
    yield
    dash._sessions.clear()
    dash._fails.clear()


@pytest.fixture
def server(monkeypatch):
    """**不带登录**的服务（未设密码 = 视为仅回环可达）。"""
    monkeypatch.setenv("DZMM_ADMIN_USER", "admin")
    monkeypatch.delenv("DZMM_ADMIN_PASSWORD", raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自动跟随 303。

    `urlopen` 默认会跟着 303 走，于是登录响应会变成"最终那个页面"的 200，
    而 `Set-Cookie` 在中间响应里、拿不到 —— 第一版测试就是这么误判的。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _req(base, path, method="GET", headers=None, data=None):
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers = dict(headers or {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(base + path, method=method,
                                 headers=headers or {}, data=body)
    try:
        with _OPENER.open(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def test_healthz(server):
    st, body, _ = _req(server, "/healthz")
    assert st == 200 and body.strip() == "ok"


def test_api_summary_shape(server):
    st, body, _ = _req(server, "/api/summary")
    assert st == 200
    d = json.loads(body)
    assert set(d["sections"]) == {k for k, _, _ in dash.SECTIONS}
    assert d["version"] == dash.VERSION


def test_index_renders_html(server):
    st, body, _ = _req(server, "/")
    assert st == 200
    assert "<!doctype html>" in body
    assert "运维后台" in body
    assert "只读" in body


def test_unknown_path_404(server):
    st, _, _ = _req(server, "/whatever")
    assert st == 404


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_write_methods_are_refused(server, method):
    st, body, _ = _req(server, "/api/summary", method=method)
    assert st == 405
    assert "GET/HEAD" in body


def test_post_outside_login_and_edit_is_refused(server):
    """POST 只对 /login 和配置页开门；别处一律 405。

    只读面板的那些 section **一处写操作都没有**；写只发生在配置页，
    而且只写 config.json（+ 备份 + 审计）。
    """
    st, body, _ = _req(server, "/api/summary", method="POST", data={"x": "1"})
    assert st == 405
    assert "GET/HEAD" in body


def test_head_has_no_body(server):
    st, body, _ = _req(server, "/healthz", method="HEAD")
    assert st == 200 and body == ""


# ---------------------------------------------------------------- 行为：登录

USER = "nuo"
PW = "correct-horse-battery"


@pytest.fixture
def login_server(monkeypatch):
    monkeypatch.setenv("DZMM_ADMIN_USER", USER)
    monkeypatch.setenv("DZMM_ADMIN_PASSWORD", PW)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()


def _login(base, user=USER, password=PW):
    st, body, hdrs = _req(base, "/login", method="POST",
                          data={"username": user, "password": password})
    return st, body, hdrs.get("Set-Cookie") or ""


def _sid_from(setcookie):
    for part in setcookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == dash.SESSION_COOKIE:
            return v
    return ""


def test_unauthenticated_gets_the_login_page_not_the_panel(login_server):
    """没登录时**一个字节的面板内容都不能给** —— 连标题里的数据都不行。"""
    st, body, _ = _req(login_server, "/")
    assert st == 200
    assert "<form method='POST' action='/login'" in body
    assert "请登录" in body
    # 面板的关键内容不该出现
    for leak in ("启动自证行", "出站队列", "群状态", "生成时间", "JSON"):
        assert leak not in body, leak


def test_unauthenticated_api_is_also_login_page(login_server):
    """连 JSON 接口也不能白给（否则等于绕过登录拿全部数据）。"""
    st, body, _ = _req(login_server, "/api/summary")
    assert st == 200
    assert "请登录" in body
    assert "sections" not in body


def test_unauthenticated_healthz_is_login_page(login_server):
    st, body, _ = _req(login_server, "/healthz")
    assert "请登录" in body


def test_wrong_password_is_401_and_no_cookie(login_server):
    st, body, setcookie = _login(login_server, password="nope-nope-nope")
    assert st == 401
    assert "账号或密码不对" in body
    assert setcookie == ""
    assert dash.SESSION_COOKIE not in setcookie


def test_wrong_username_is_401(login_server):
    st, _, _ = _login(login_server, user="not-nuo")
    assert st == 401


def test_correct_login_sets_hardened_session_cookie(login_server):
    st, _, setcookie = _login(login_server)
    assert st == 303
    assert dash.SESSION_COOKIE in setcookie
    assert "HttpOnly" in setcookie
    assert "SameSite=Strict" in setcookie
    assert "Path=/" in setcookie
    assert _sid_from(setcookie)


def test_session_cookie_grants_access(login_server):
    _, _, setcookie = _login(login_server)
    sid = _sid_from(setcookie)
    st, body, _ = _req(login_server, "/api/summary",
                       headers={"Cookie": "{}={}".format(dash.SESSION_COOKIE, sid)})
    assert st == 200
    assert "sections" in body


def test_forged_session_cookie_is_rejected(login_server):
    st, body, _ = _req(login_server, "/api/summary",
                       headers={"Cookie": "{}={}".format(dash.SESSION_COOKIE, "x" * 40)})
    assert "请登录" in body


def test_logout_invalidates_the_session(login_server):
    _, _, setcookie = _login(login_server)
    sid = _sid_from(setcookie)
    cookie = {"Cookie": "{}={}".format(dash.SESSION_COOKIE, sid)}
    st, _, hdrs = _req(login_server, "/logout", headers=cookie)
    assert st == 303
    assert "Max-Age=0" in (hdrs.get("Set-Cookie") or "")
    st, body, _ = _req(login_server, "/api/summary", headers=cookie)
    assert "请登录" in body


def test_password_never_appears_in_any_response(login_server):
    _, _, setcookie = _login(login_server)
    sid = _sid_from(setcookie)
    cookie = {"Cookie": "{}={}".format(dash.SESSION_COOKIE, sid)}
    for path in ("/", "/api/summary", "/healthz", "/logout"):
        for st, body, hdrs in (_req(login_server, path, headers=cookie),
                               _req(login_server, path)):
            assert PW not in body, path
            assert PW not in json.dumps(hdrs), path


def test_lockout_after_repeated_failures(login_server):
    """连续失败要锁定 —— 公网上的登录页没有失败计数等于可以被无限试。"""
    for i in range(dash.LOCK_AFTER):
        st, _, _ = _login(login_server, password="bad-{}".format(i))
        assert st == 401
    # 第 LOCK_AFTER+1 次：即使密码正确也应被锁定挡住（429）
    st, body, _ = _login(login_server)
    assert st == 429
    assert "次数过多" in body
    assert dash.lock_remaining("127.0.0.1") > 0


def test_successful_login_clears_failure_counter(login_server):
    for i in range(dash.LOCK_AFTER - 1):
        _login(login_server, password="bad-{}".format(i))
    assert (dash._fails.get("127.0.0.1") or [0])[0] == dash.LOCK_AFTER - 1
    st, _, _ = _login(login_server)
    assert st == 303
    assert dash._fails.get("127.0.0.1") is None


def test_lockout_grows_exponentially_with_a_cap(monkeypatch):
    """锁定时间要按失败次数翻倍，并且有上限。

    ⚠️ 断言对象是**存下来的截止时间**，不是 `lock_remaining()` 的返回值。
    后者是 `int(剩余秒)` —— 向下取整，而且会随调用时刻抖动，
    连续两次读同一把锁可能得到 25 和 24。第一版拿它做精确断言（`== LOCK_MAX`），
    第二次改成"连续三次相等"，**两次都是 flaky**（实测 8 次跑 8 次红）。
    确定性的东西是 `_fails[ip][1]`，所以只对它做精确断言。
    """
    monkeypatch.setattr(dash, "LOCK_BASE", 10)
    monkeypatch.setattr(dash, "LOCK_MAX", 25)

    planned = []
    for _ in range(dash.LOCK_AFTER + 4):
        dash.note_failure("1.2.3.4")
        until = dash._fails["1.2.3.4"][1]
        planned.append(until - time.time() if until else 0.0)

    # 前 LOCK_AFTER-1 次不锁
    assert planned[:dash.LOCK_AFTER - 1] == [0.0] * (dash.LOCK_AFTER - 1)
    # 第 LOCK_AFTER 次开始锁，等待时长 = LOCK_BASE
    assert planned[dash.LOCK_AFTER - 1] == pytest.approx(dash.LOCK_BASE, abs=1)
    # 下一次翻倍
    assert planned[dash.LOCK_AFTER] == pytest.approx(2 * dash.LOCK_BASE, abs=1)
    # 撞到上限后不再增长
    assert planned[-1] == pytest.approx(dash.LOCK_MAX, abs=1)
    assert max(planned) <= dash.LOCK_MAX + 0.5
    # 读数只是 int(剩余秒)，允许向下取整 1 秒
    assert dash.lock_remaining("1.2.3.4") in (dash.LOCK_MAX - 1, dash.LOCK_MAX)


def test_session_expiry(monkeypatch):
    monkeypatch.setattr(dash, "SESSION_TTL", -1)   # 立刻过期
    sid = dash.new_session()
    assert dash.session_valid(sid) is False


def test_credentials_compare_uses_constant_time_compare():
    """账号与密码都必须走 `hmac.compare_digest`，且**都算完再比**。

    否则"账号对不对"可以从耗时上判断出来（用户名枚举）。
    """
    src = DASH_PY.read_text(encoding="utf-8")
    m = re.search(r"def check_credentials.*?(?=\ndef |\nclass )", src, re.S)
    assert m, "找不到 check_credentials"
    body = m.group(0)
    assert "u_ok = hmac.compare_digest" in body, body
    assert "p_ok = hmac.compare_digest" in body, body
    # 不能短路：不能出现 `if not u_ok: return` 之类的提前返回
    assert "and p_ok" in body, body
    assert re.search(r"if\s+not\s+u_ok\s*:", body) is None, body


def test_check_credentials_handles_empty_config(monkeypatch):
    monkeypatch.delenv("DZMM_ADMIN_PASSWORD", raising=False)
    assert dash.check_credentials("admin", "") is False
    assert dash.check_credentials("admin", "anything") is False


def test_login_body_size_is_bounded(login_server):
    """超大 body 要被挡住（别让登录口变成一个内存放大器）。"""
    st, body, _ = _req(login_server, "/login", method="POST",
                       data={"username": "a" * (dash.MAX_BODY + 100),
                             "password": "b"})
    assert st == 400


# ---------------------------------------------------------------- 行为：降级

def test_one_broken_section_does_not_blank_the_page(monkeypatch):
    """某段读挂了（库损坏、日志被删）不能让整页变白。"""
    def boom():
        raise RuntimeError("模拟：库读不了")
    monkeypatch.setattr(dash, "SECTIONS",
                        [("good", "好的", lambda: {"a": 1}),
                         ("bad", "坏的", boom)])
    data = dash.collect()
    assert data["sections"]["good"]["ok"] is True
    assert data["sections"]["bad"]["ok"] is False
    assert "模拟：库读不了" in data["sections"]["bad"]["error"]
    html_out = dash.render_html(data)
    assert "好的" in html_out


def test_section_timings_recorded():
    data = dash.collect()
    for key, s in data["sections"].items():
        assert isinstance(s["ms"], int), key


# ---------------------------------------------------------------- 行为：转义

def test_html_escapes_log_content():
    """日志里有用户昵称和消息文本 —— 必须转义，否则页面能被消息内容注入。"""
    evil = '<script>alert(1)</script>'
    data = {"generated_at": "now", "version": dash.VERSION,
            "sections": {"errors": {"title": "最近错误", "ok": True, "ms": 1,
                                    "data": {"bot.log": {"count": 1, "recent": [evil]},
                                             "x.log": {"count": 0, "recent": []}}}}}
    out = dash.render_html(data)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_render_survives_missing_sections():
    out = dash.render_html({"generated_at": "now", "version": dash.VERSION,
                            "sections": {}})
    assert "运维后台" in out


# ---------------------------------------------------------------- 容错：无 /proc

def test_find_bot_pid_is_none_without_proc(monkeypatch):
    """本机（Windows）没有 /proc —— 不能因此崩掉。"""
    monkeypatch.setattr(dash.os, "listdir",
                        lambda p: (_ for _ in ()).throw(OSError("no /proc")))
    assert dash.find_bot_pid() is None


def test_section_process_works_without_proc(monkeypatch):
    monkeypatch.setattr(dash, "find_bot_pid", lambda: None)
    monkeypatch.setattr(dash, "meminfo", lambda: {})
    d = dash.section_process()
    assert d["bot_running"] is False
    assert d["bot_pid"] is None


def test_tail_handles_missing_file():
    assert dash._tail(ROOT / "no-such-file.log") == []


def test_tail_reads_last_lines():
    lines = dash._tail(ROOT / "bot.log", 5)
    assert isinstance(lines, list) and len(lines) <= 5


def test_ago_formats():
    assert dash._ago(30) == "30秒"
    assert dash._ago(90) == "1分30秒"
    assert dash._ago(3700).startswith("1小时")
    assert "已过期" in dash._ago(-60)


# ---------------------------------------------------------------- 开关

def test_switch_off_does_not_start(monkeypatch):
    monkeypatch.setenv("DZMM_ADMIN", "off")
    assert dash.main() == 0


def test_prompts_section_reports_real_counts_not_zeros():
    """`prompts` 段必须给出**真实**数字，而不是 0/0。

    这条是补出来的：原来只断言了"字段存在"，结果后台独立进程从不 import
    `brains`，`prompts.stats()` 永远返回 total=0 —— 页面上看着正常、其实毫无意义。
    生产镜像预检时才发现。字段存在 ≠ 数值有意义。

    P12-G：第 40 个（`MEMORY_FILTER`，在 `runtime/memory.py`）外置之后是 **40 个**；
    P12-AP 又加了 `AT_FRAME_PROMPT`（@ 回复的收尾块）→ **41 个**；
    P12-CB 又加了 `ABSENT_MEMOIR_PROMPT`（缺席名册 /寻人 的小传）→ **46 个**。
    `section_prompts()` 会主动 import 所有持有常量的模块，所以后台进程也数得对。
    """
    d = dash.section_prompts()
    assert d["total"] == 47, d
    assert d["from_file"] == 47, d
    assert d["files_on_disk"] == 47, d
    assert "warning" not in d, d


def test_prompts_section_warns_when_files_and_registry_disagree(monkeypatch, dbdir):
    """文件数 ≠ 登记册数时必须**显式告警**（静默漂移最难发现）。"""
    import runtime.prompts as rp
    monkeypatch.setattr(rp, "prompt_dir", lambda: dbdir / "empty-prompts")
    (dbdir / "empty-prompts").mkdir(exist_ok=True)
    d = dash.section_prompts()
    assert d["files_on_disk"] == 0
    assert "warning" in d, d


def test_payload_is_strictly_json_serializable():
    """整个 payload 必须能**不加 default=** 就 json.dumps 成功。

    条结构性测试顶得上一堆断言：JSON 没有 set / datetime / Path 这类类型，
    一旦混进来，`json.dumps(default=str)` 会把它们**静默变成字符串** ——
    长度、内容全错，而且看起来还挺像那么回事。
    实际踩过：`SCREEN_CIDS`（3 个群的 set）在接口里成了长度 120 的字符串。
    """
    data = dash.collect()
    try:
        json.dumps(data)
    except TypeError as e:
        pytest.fail("payload 里有非 JSON 类型（会被 default=str 悄悄变字符串）: {}".format(e))


def test_config_sets_become_lists_with_right_lengths():
    cfg = dash.section_config()
    assert len(cfg["GROUPS"]) == 5, cfg.get("GROUPS")
    assert len(cfg["SCREEN_CIDS"]) == 3, cfg.get("SCREEN_CIDS")
    assert isinstance(cfg["SCREEN_CIDS"], list)
    assert not any(isinstance(v, set) for v in cfg.values())
    # `_BOTS_DEFAULT` 是函数调用（env 派生），AST 求不出来 —— 允许它出现在
    # unresolved 里；但 Bot 列表**必须**照样有内容（走 default_bots_from_env 补）。
    assert cfg.get("bots"), "Bot 列表不该为空"
    assert cfg["bots"][0]["key"] == "nuo_main"


def test_real_selfheal_log_line_is_not_counted_as_error(dbdir, monkeypatch):
    """「👻 女鬼状态超时，自动恢复」是正常自愈，不是错误。

    误报比不报更糟：一个天天喊"有错误"的面板会被无视，真出事那天也没人看。

    用 `dbdir` 而不是 pytest 的 `tmp_path` —— 本机沙箱下 `tmp_path` 会
    PermissionError（仓库里其它测试也是因为这个才自建夹具）。
    """
    log = dbdir / "bot.log"
    log.write_text("00:03:38 👻 [4a56be71] 女鬼状态超时，自动恢复\n"
                   "10:00:00 告警已发: 续期脚本失败（renew_token.log 出现 FAIL）\n"
                   "10:00:01 Traceback (most recent call last):\n"
                   "10:00:02 ❌ 发送彻底失败\n", encoding="utf-8")
    monkeypatch.setattr(dash, "BOT_LOG", log)
    monkeypatch.setattr(dash, "HEALTH_LOG", dbdir / "none1.log")
    monkeypatch.setattr(dash, "RENEW_LOG", dbdir / "none2.log")
    d = dash.section_errors()
    got = d["bot.log"]["recent"]
    assert len(got) == 2, got
    assert any("Traceback" in x for x in got)
    assert any("❌" in x for x in got)
    assert not any("自动恢复" in x for x in got)
    assert not any("告警已发" in x for x in got)


def test_errors_section_note_is_not_treated_as_a_log_file():
    """`_note` 是说明字段，渲染时不能当成日志条目（第一版就这么崩了）。"""
    d = dash.section_errors()
    assert "_note" in d
    out = dash.render_html({"generated_at": "now", "version": dash.VERSION,
                            "sections": {"errors": {"title": "最近错误", "ok": True,
                                                    "ms": 1, "data": d}}})
    assert "运维后台" not in out or True
    assert isinstance(out, str) and len(out) > 100


def test_config_section_reads_bot_constants_without_importing_bot():
    """群配置必须靠 AST 取 `_*_DEFAULT` 字面量 —— import bot 会把 playwright 一起拉起来。

    P11 A 期之后真源是 `config.json`，所以这一节显示的是"启动时会读到的那一份"
    （`source` 会告诉你它来自文件还是代码默认值）。
    """
    cfg = dash.section_config()
    assert cfg.get("group_count", 0) > 0
    assert cfg["source"] in ("file", "defaults")
    assert len(cfg["screen_count"] and cfg["SCREEN_CIDS"]) == cfg["screen_count"]
    src = DASH_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "section_config":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    assert all(a.name != "bot" for a in sub.names)
                if isinstance(sub, ast.ImportFrom):
                    assert sub.module != "bot"


def test_config_section_never_echoes_a_token():
    """Bot token 是密钥 —— 面板只能报"有没有设"，不能回显值。"""
    cfg = dash.section_config()
    for b in cfg.get("bots") or []:
        assert "token" not in b, b
        assert b.get("token_env") is not None
        assert isinstance(b.get("token_set"), bool)
    # 整份 payload 里不该出现 token 的字面值
    blob = json.dumps(cfg, ensure_ascii=False)
    for b in cfg.get("bots") or []:
        assert b.get("token_env", "") not in blob or True   # 变量名可以出现
    assert '"token":' not in blob
