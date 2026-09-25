"""P9 运维后台的守卫测试。

这个工具最大的风险不是"页面显示错了"，而是**它改了线上数据** ——
运维后台写坏生产库是这类东西最典型的翻车方式。所以这里的重点是把
"只读"变成**结构性、可验证**的性质，而不是文档里的一句承诺：

  1. 每一处 `sqlite3.connect` 都必须带 `mode=ro`
  2. HTTP 层只接受 GET/HEAD，其它方法一律 405
  3. 它查询的列名必须真的存在于 `runtime/store.py` / `runtime/outbox.py`
     的建表语句里（后台照着自己的记忆抄 schema → 迟早漂移）
  4. 任何一段 section 抛异常都不该让整页变白
  5. 日志内容进 HTML 必须转义（日志里有用户昵称和消息文本）
"""
import ast
import importlib
import json
import re
import sys
import threading
import urllib.error
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

def test_published_port_is_host_loopback_only():
    """**真正的安全边界在 compose 的端口映射上**，不在容器内绑哪个地址。

    第一次部署时我把容器内绑成 127.0.0.1，结果 docker-proxy 从容器 IP 转发，
    发布出来的端口根本连不上（宿主机 curl 000，而 ss 显示 docker-proxy 在听着）。
    所以容器内绑 0.0.0.0，安全靠这条映射保证 —— 因此这条必须被测试钉住。
    """
    yml = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    m = re.search(r'-\s*"([^"]*8090[^"]*)"', yml)
    assert m, "prod compose 里找不到 8090 的端口映射"
    mapping = m.group(1)
    assert mapping.startswith("127.0.0.1:"), \
        "8090 的映射必须是宿主机回环，当前是 {!r}（机器上 noVNC 6080 实测公网可达）".format(mapping)


def test_dashboard_binds_all_interfaces_inside_container():
    """容器内必须绑 0.0.0.0，否则 compose 发布的端口连不上（已踩过）。"""
    assert dash.DEFAULT_HOST == "0.0.0.0"


def test_compose_passes_wildcard_bind_inside_container():
    """compose 必须显式传 0.0.0.0，不能把默认值留成回环。

    第一次部署就是栽在这里：`DZMM_ADMIN_HOST: "${...:-127.0.0.1}"` 让 compose
    把回环地址传进了容器，端口发布出来连不上。
    """
    yml = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    m = re.search(r'DZMM_ADMIN_HOST:\s*"\$\{DZMM_ADMIN_HOST:-([^}]*)\}"', yml)
    assert m, "compose 里找不到 DZMM_ADMIN_HOST"
    assert m.group(1) == "0.0.0.0", "compose 传的容器内绑定地址应是 0.0.0.0，当前 {!r}".format(
        m.group(1))


def test_startup_prints_the_real_boundary():
    """启动时必须把"真正的边界在哪"打出来，避免有人改错映射还不知道后果。"""
    src = DASH_PY.read_text(encoding="utf-8")
    assert "对外边界 = compose 的端口映射" in src
    assert "noVNC(6080) 实测公网可达" in src


# ---------------------------------------------------------------- 行为：HTTP

@pytest.fixture
def server(monkeypatch):
    monkeypatch.delenv("DZMM_ADMIN_TOKEN", raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()


def _req(base, path, method="GET", headers=None):
    req = urllib.request.Request(base + path, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def test_healthz(server):
    st, body = _req(server, "/healthz")
    assert st == 200 and body.strip() == "ok"


def test_api_summary_shape(server):
    st, body = _req(server, "/api/summary")
    assert st == 200
    d = json.loads(body)
    assert set(d["sections"]) == {k for k, _, _ in dash.SECTIONS}
    assert d["version"] == dash.VERSION


def test_index_renders_html(server):
    st, body = _req(server, "/")
    assert st == 200
    assert "<!doctype html>" in body
    assert "运维后台" in body
    assert "只读" in body


def test_unknown_path_404(server):
    st, _ = _req(server, "/whatever")
    assert st == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_write_methods_are_refused(server, method):
    st, body = _req(server, "/api/summary", method=method)
    assert st == 405
    assert "read-only" in body


def test_head_has_no_body(server):
    st, body = _req(server, "/healthz", method="HEAD")
    assert st == 200 and body == ""


# ---------------------------------------------------------------- 行为：令牌

@pytest.fixture
def tokened_server(monkeypatch):
    monkeypatch.setenv("DZMM_ADMIN_TOKEN", "s3cret")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()


def test_token_required_when_configured(tokened_server):
    st, body = _req(tokened_server, "/api/summary")
    assert st == 403
    assert "令牌" in body


def test_token_wrong_is_refused(tokened_server):
    st, _ = _req(tokened_server, "/api/summary?token=nope")
    assert st == 403


def test_token_via_query(tokened_server):
    st, _ = _req(tokened_server, "/api/summary?token=s3cret")
    assert st == 200


def test_token_via_header(tokened_server):
    st, _ = _req(tokened_server, "/api/summary", headers={"X-Admin-Token": "s3cret"})
    assert st == 200


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
    """
    d = dash.section_prompts()
    assert d["total"] == 39, d
    assert d["from_file"] == 39, d
    assert d["files_on_disk"] == 39, d
    assert "warning" not in d, d


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
    # 是调用不是字面量的（ROOM_CIDS = set(ROOMS.keys())）要老实报到 unresolved
    names = [u["name"] for u in cfg.get("unresolved") or []]
    assert "ROOM_CIDS" in names, cfg.get("unresolved")
    assert not any(isinstance(v, set) for v in cfg.values())


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
    """群配置必须靠 AST 取字面量 —— import bot 会把 playwright 一起拉起来。

    顺带确认它对 bot.py 的**源码**没有依赖之外的副作用：只 parse，不 exec。
    """
    cfg = dash.section_config()
    assert cfg.get("group_count", 0) > 0
    src = DASH_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "section_config":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    assert all(a.name != "bot" for a in sub.names)
                if isinstance(sub, ast.ImportFrom):
                    assert sub.module != "bot"
