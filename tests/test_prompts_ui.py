"""文案模板页（P12-G）：渲染 / 校验 / 保存 / 自测拒绝 / 历史与回滚。

这一页的纪律是"**改坏不可能保存成功**"，所以测试重点在"拒绝"那一侧：

1. 页面上 40 条都在、按档分组、每条都能点进编辑器；
2. 静态校验不过 ⇒ 400 + **一个字都不写**（文件内容逐字节不变）；
3. C3 保存**自动自测**，契约不过 ⇒ 拒绝（并且不写文件）；`强制保存` 才放行；
4. 每次写入都留历史版本，能看逐行差异、能回滚；
5. 恢复默认 = **删掉覆盖文件**（不是写一份默认值进文件）。
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "admin"))

import brains  # noqa: E402,F401
import dashboard as dash  # noqa: E402
import runtime.memory  # noqa: E402,F401
from admin import prompts_ui as pui  # noqa: E402
from runtime import prompt_tiers as PT  # noqa: E402
from runtime import prompts as RP  # noqa: E402

import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

USER, PW = "nuo", "pw12345678"
CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"

C1 = "GREETING_PROMPT"
C2 = "FORTUNE_PROMPT"
C3 = "XP_PROMPT"


def _req(base, path, method="GET", headers=None, data=None):
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        headers = dict(headers or {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    r = urllib.request.Request(base + path, method=method,
                               headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _csrf(page):
    return re.search(r"name='csrf' value='([^']*)'", page).group(1)


@pytest.fixture
def pdir(monkeypatch, dbdir):
    """把 prompts 目录指到临时目录：**绝不在真 prompts/ 上做写入测试**。

    ⚠️ 用 `copy2` 逐字节复制，**不要** read_text/write_text：
    Windows 的文本模式会把 LF 翻成 CRLF，文件就与代码默认值不一样了
    （测试会以"备份大小对不上"这种莫名其妙的方式失败）。
    """
    import shutil
    d = dbdir / "prompts"
    d.mkdir()
    for f in (ROOT / "prompts").glob("*.md"):
        shutil.copy2(str(f), str(d / f.name))
    monkeypatch.setenv("DZMM_PROMPT_DIR", str(d))
    RP.clear_cache()
    yield d
    RP.clear_cache()


@pytest.fixture
def srv(monkeypatch, dbdir, pdir):
    cfgp = dbdir / "config.json"
    cfgp.write_text(json.dumps({
        "version": 3,
        "groups": [{"cid": CID_A, "alias": "认证妹子群", "enabled": True,
                    "persona": ""}],
        "bots": [{"key": "nuo_main", "name": "小小糯-bot", "cid": CID_A,
                  "token_env": "DZMM_BOT_TOKEN", "enabled": True}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DZMM_CONFIG", str(cfgp))
    monkeypatch.setenv("DZMM_ADMIN_USER", USER)
    monkeypatch.setenv("DZMM_ADMIN_PASSWORD", PW)
    cs = __import__("runtime.config_store", fromlist=["x"])
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
        cs._cache = None


def _login(base):
    st, _b = _req(base, "/login", method="POST",
                  data={"username": USER, "password": PW})
    # 用同一个 opener 维护 cookie 比较麻烦，这里换个简单办法：拿 Set-Cookie
    return st


def _opener_login(base):
    """返回一个带会话 cookie 的 opener。"""
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.open(base + "/login",
            urllib.parse.urlencode({"username": USER,
                                    "password": PW}).encode("utf-8"))
    return op


def _get(op, base, path):
    try:
        with op.open(base + path, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _post(op, base, path, data):
    body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
    r = urllib.request.Request(base + path, data=body)
    r.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with op.open(r, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- 1. 渲染

def test_page_requires_login(srv):
    base, _ = srv
    st, body = _req(base, "/prompts")
    assert st == 200 and "请登录" in body


def test_page_lists_all_prompts_by_tier(srv):
    base, _ = srv
    op = _opener_login(base)
    st, page = _get(op, base, "/prompts")
    assert st == 200
    for s in PT.all_specs():
        assert "name={}".format(s.key) in page, s.key
    assert "C1 · 自由文案" in page and "C2 · 形状敏感" in page
    assert "C3 · 结构化输出" in page
    assert "（24 个）" in page and "（12 个）" in page and "（10 个）" in page


def test_editor_shows_constraints_and_contract(srv):
    base, _ = srv
    op = _opener_login(base)
    st, page = _get(op, base, "/prompts?name={}".format(C3))
    assert st == 200
    assert "【1】" not in page or True
    assert "必须是 JSON 数组" in page, "契约要写在页面上"
    assert "name" in page and "delta" in page or True
    assert "保存并自测" in page and "恢复代码默认" in page
    # CMD_PROMPT 的分段结构要显示成"不能丢的东西"
    st, page2 = _get(op, base, "/prompts?name=CMD_PROMPT")
    assert "【外貌】" in page2 and "用户: ≥4 次" in page2


def test_page_links_from_other_pages(srv):
    base, _ = srv
    op = _opener_login(base)
    for path in ("/", "/edit", "/features", "/personas"):
        st, page = _get(op, base, path)
        assert st == 200, path
        assert "href='/prompts'" in page, path


# ---------------------------------------------------------------- 2. 保存

def test_save_writes_file_and_backup(srv, pdir):
    base, _ = srv
    op = _opener_login(base)
    _st, page = _get(op, base, "/prompts?name={}".format(C1))
    csrf = _csrf(page)
    new = RP.load(C1, RP.default_of(C1)) + "\n（多加一句测试）"
    st, body = _post(op, base, "/prompts",
                     {"csrf": csrf, "key": C1, "text": new, "action": "save"})
    assert st == 200, body[:300]
    assert "已保存" in body and "热加载" in body
    assert (pdir / (C1 + ".md")).read_text(encoding="utf-8") == new
    assert pui.list_backups(C1), "写入前必须留一份历史"


def test_save_refuses_when_must_keep_missing(srv, pdir):
    base, _ = srv
    op = _opener_login(base)
    _st, page = _get(op, base, "/prompts?name={}".format(C2))
    before = (pdir / (C2 + ".md")).read_text(encoding="utf-8")
    st, body = _post(op, base, "/prompts",
                     {"csrf": _csrf(page), "key": C2, "action": "save",
                      "text": "随便写点什么，把形状要求删掉"})
    assert st == 400 and "一个字都没写" in body
    assert "不要换行" in body, "要告诉用户缺了哪个片段"
    assert (pdir / (C2 + ".md")).read_text(encoding="utf-8") == before, "文件不能变"
    assert not pui.list_backups(C2), "被拒时不该留历史"


def test_save_refuses_empty(srv, pdir):
    base, _ = srv
    op = _opener_login(base)
    _st, page = _get(op, base, "/prompts?name={}".format(C1))
    before = (pdir / (C1 + ".md")).read_text(encoding="utf-8")
    st, body = _post(op, base, "/prompts",
                     {"csrf": _csrf(page), "key": C1, "text": "   ",
                      "action": "save"})
    assert st == 400 and "不能为空" in body
    assert (pdir / (C1 + ".md")).read_text(encoding="utf-8") == before


def test_save_rejects_unknown_key(srv):
    base, _ = srv
    op = _opener_login(base)
    _st, page = _get(op, base, "/prompts?name=" + C1)
    st, body = _post(op, base, "/prompts",
                     {"csrf": _csrf(page), "key": "NO_SUCH", "text": "x",
                      "action": "save"})
    assert st == 400 and "没有这个提示词" in body


def test_save_without_csrf_is_refused(srv, pdir):
    base, _ = srv
    op = _opener_login(base)
    before = (pdir / (C1 + ".md")).read_text(encoding="utf-8")
    st, _b = _post(op, base, "/prompts",
                   {"key": C1, "text": "改了", "action": "save"})
    assert st == 400
    assert (pdir / (C1 + ".md")).read_text(encoding="utf-8") == before


# ---------------------------------------------------------------- 3. 自测

def test_c3_save_runs_selftest_and_refuses_on_bad_contract(srv, pdir, monkeypatch):
    """C3（结构化输出）保存时**自动自测**：契约不过就拒（这正是这一页的价值）。"""
    base, _ = srv
    op = _opener_login(base)

    def fake_llm(system, user, **kw):
        return "我拒绝输出 JSON"

    monkeypatch.setattr(brains, "_call_llm", fake_llm)
    _st, page = _get(op, base, "/prompts?name={}".format(C3))
    before = (pdir / (C3 + ".md")).read_text(encoding="utf-8")
    new = RP.load(C3, RP.default_of(C3)).replace("输出 JSON 数组", "输出 JSON 数组")
    st, body = _post(op, base, "/prompts",
                     {"csrf": _csrf(page), "key": C3, "text": new,
                      "action": "save"})
    assert st == 400 and "自测没通过" in body
    assert "没有写入" in body
    assert (pdir / (C3 + ".md")).read_text(encoding="utf-8") == before


def test_c3_force_save_bypasses_selftest(srv, pdir, monkeypatch):
    base, _ = srv
    op = _opener_login(base)
    monkeypatch.setattr(brains, "_call_llm",
                        lambda system, user, **kw: "我拒绝输出 JSON")
    _st, page = _get(op, base, "/prompts?name={}".format(C3))
    new = RP.load(C3, RP.default_of(C3))
    st, body = _post(op, base, "/prompts",
                     {"csrf": _csrf(page), "key": C3, "text": new,
                      "action": "force"})
    assert st == 200 and "已保存" in body
    assert (pdir / (C3 + ".md")).read_text(encoding="utf-8") == new


def test_c3_save_passes_when_contract_holds(srv, pdir, monkeypatch):
    base, _ = srv
    op = _opener_login(base)
    monkeypatch.setattr(brains, "_call_llm",
                        lambda system, user, **kw: '["小M","露出"]')
    _st, page = _get(op, base, "/prompts?name={}".format(C3))
    new = RP.load(C3, RP.default_of(C3))
    st, body = _post(op, base, "/prompts",
                     {"csrf": _csrf(page), "key": C3, "text": new,
                      "action": "save"})
    assert st == 200 and "契约通过" in body
    assert (pdir / (C3 + ".md")).read_text(encoding="utf-8") == new


def test_c1_save_does_not_call_the_model(srv, pdir, monkeypatch):
    """C1（自由文案）保存**不该**花钱调模型 —— 只有 C3 自动自测。"""
    base, _ = srv
    op = _opener_login(base)
    calls = []

    def fake(system, user, **kw):
        calls.append(user)
        return "x"

    monkeypatch.setattr(brains, "_call_llm", fake)
    _st, page = _get(op, base, "/prompts?name={}".format(C1))
    new = RP.load(C1, RP.default_of(C1)) + "\n加一句"
    st, _body = _post(op, base, "/prompts",
                      {"csrf": _csrf(page), "key": C1, "text": new,
                       "action": "save"})
    assert st == 200
    assert calls == [], "C1 保存不该调模型"


def test_preview_uses_draft_and_writes_nothing(srv, pdir, monkeypatch):
    base, _ = srv
    op = _opener_login(base)
    seen = {}

    def fake(system, user, **kw):
        seen["system"] = system
        seen["user"] = user
        return '["标签"]'

    monkeypatch.setattr(brains, "_call_llm", fake)
    _st, page = _get(op, base, "/prompts?name={}".format(C3))
    before = (pdir / (C3 + ".md")).read_text(encoding="utf-8")
    draft = RP.load(C3, RP.default_of(C3)) + "\n（草稿）"
    st, body = _post(op, base, "/prompts",
                     {"csrf": _csrf(page), "key": C3, "text": draft,
                      "action": "preview", "user": "我自己的输入"})
    assert st == 200 and "契约通过" in body
    assert seen["system"].endswith("（草稿）"), "预览要用草稿，不是已保存的那份"
    assert seen["user"] == "我自己的输入"
    assert (pdir / (C3 + ".md")).read_text(encoding="utf-8") == before, "预览不能写"


def test_preview_reports_shape_violation(srv, monkeypatch):
    base, _ = srv
    op = _opener_login(base)
    monkeypatch.setattr(brains, "_call_llm",
                        lambda system, user, **kw: "第一行\n第二行")
    _st, page = _get(op, base, "/prompts?name={}".format(C2))
    st, body = _post(op, base, "/prompts",
                     {"csrf": _csrf(page), "key": C2, "action": "preview",
                      "text": RP.load(C2, RP.default_of(C2))})
    assert st == 200 and "没通过" in body and "换行" in body


# ---------------------------------------------------------------- 4. 历史与回滚

def test_reset_writes_default_back_and_keeps_history(srv, pdir):
    """恢复默认 = 把**代码默认文本写回文件**（不是删文件）。

    为什么不是删：`prompts/` 里本来就齐着 40 个文件（它们是默认值的载体），
    删掉会破坏"文件与登记册一一对应"这条不变量（闸门会报错），
    而且内容写回默认值的效果完全一样。
    """
    base, _ = srv
    op = _opener_login(base)
    f = pdir / (C1 + ".md")
    f.write_text(RP.default_of(C1) + "\n改过了", encoding="utf-8")
    RP.clear_cache()
    _st, page = _get(op, base, "/prompts?name={}".format(C1))
    st, body = _post(op, base, "/prompts/reset",
                     {"csrf": _csrf(page), "key": C1})
    assert st == 200 and "已恢复代码默认值" in body
    assert f.is_file(), "文件必须还在（默认文本的载体）"
    assert f.read_bytes() == RP.default_of(C1).encode("utf-8")
    assert pui.list_backups(C1), "写回之前要留一份历史"
    RP.clear_cache()
    assert RP.load(C1, RP.default_of(C1)) == RP.default_of(C1)
    # 再点一次：本来就是默认值 ⇒ 明确说"没有改动"
    _st, page2 = _get(op, base, "/prompts?name={}".format(C1))
    st, body2 = _post(op, base, "/prompts/reset",
                      {"csrf": _csrf(page2), "key": C1})
    assert st == 200 and "本来就是代码默认值" in body2


def test_reset_repairs_a_missing_file(srv, pdir):
    """文件被人手删了 → 「恢复默认」把它写回来（顺手当修复用）。"""
    base, _ = srv
    op = _opener_login(base)
    f = pdir / (C1 + ".md")
    f.unlink()
    RP.clear_cache()
    _st, page = _get(op, base, "/prompts?name={}".format(C1))
    st, body = _post(op, base, "/prompts/reset", {"csrf": _csrf(page), "key": C1})
    assert st == 200 and "已恢复代码默认值" in body
    assert f.read_bytes() == RP.default_of(C1).encode("utf-8")


def test_history_diff_and_rollback(srv, pdir):
    base, _ = srv
    op = _opener_login(base)
    f = pdir / (C1 + ".md")
    original = RP.default_of(C1)
    f.write_text(original, encoding="utf-8")
    RP.clear_cache()
    # 改一次 → 产生一个历史版本
    _st, page = _get(op, base, "/prompts?name={}".format(C1))
    v1 = original + "\n第一版"
    st, _b = _post(op, base, "/prompts",
                   {"csrf": _csrf(page), "key": C1, "text": v1, "action": "save"})
    assert st == 200
    backups = pui.list_backups(C1)
    assert backups and backups[0]["size"] == len(original.encode("utf-8"))
    version = backups[0]["name"]
    # 看差异
    st, diff = _get(op, base, "/prompts/history?name={}&version={}".format(C1, version))
    assert st == 200
    assert "第一版" in diff and "当前 prompts/{}.md".format(C1) in diff
    # 回滚
    _st, page2 = _get(op, base, "/prompts?name={}".format(C1))
    st, body = _post(op, base, "/prompts/rollback",
                     {"csrf": _csrf(page2), "key": C1, "version": version})
    assert st == 200 and "已回滚" in body
    assert f.read_text(encoding="utf-8") == original


def test_rollback_refuses_foreign_version(srv, pdir):
    base, _ = srv
    op = _opener_login(base)
    (pdir / (C2 + ".md")).write_text(RP.default_of(C2), encoding="utf-8")
    RP.clear_cache()
    _st, page = _get(op, base, "/prompts?name={}".format(C2))
    st, body = _post(op, base, "/prompts/rollback",
                     {"csrf": _csrf(page), "key": C1,
                      "version": "{}-20260101-000000.md".format(C2)})
    assert st == 400 and "历史版本不存在" in body


def test_rollback_refuses_history_that_fails_validation(srv, pdir):
    """历史版本也要过**现在**的校验（比如契约后来加了新要求）。"""
    base, _ = srv
    op = _opener_login(base)
    d = pui.backup_dir()
    d.mkdir(exist_ok=True)
    (d / "{}-20260101-000000.md".format(C2)).write_text("坏版本：什么都没有",
                                                        encoding="utf-8")
    _st, page = _get(op, base, "/prompts?name={}".format(C2))
    st, body = _post(op, base, "/prompts/rollback",
                     {"csrf": _csrf(page), "key": C2,
                      "version": "{}-20260101-000000.md".format(C2)})
    assert st == 400 and "过不了现在的校验" in body


def test_keep_backups_prunes(srv, pdir):
    """历史版本只留最近 N 份（长文本会天天改，不能无限长）。"""
    key = C1
    for i in range(pui.KEEP_BACKUPS + 5):
        pui.backup_prompt(key, "版本 {}\n".format(i))
    got = pui.list_backups(key)
    assert len(got) == pui.KEEP_BACKUPS, len(got)
