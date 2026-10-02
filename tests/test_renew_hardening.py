"""P12-BL：续期链再检查一轮 —— 原子凭据写 + 原始响应留证 + 统一浏览器 UA。

屋主 2026-09-30 17:0x："**续期的问题再检查一下。**"

## 这一轮查到/改掉的三件事

1. **凭据文件是"非原子写"（真风险，已修）**
   `token_store.save_cookies()` 原来是 `TOKEN_FILE.write_text(...)`；而容器重建
   （`docker compose up -d`）= SIGKILL，正好可能落在这次写的中途 ⇒ 文件写坏
   ⇒ `load_cookies()` 返回 `[]` ⇒ **所有平台调用一起未授权**、`renew_token` 连
   cookie 都读不到，只能人工重登。凭据是单点，必须原子：**tmp → fsync → os.replace**。
2. **失败时没有原始响应（已补）**
   那几天 4~8 小时的"未授权"窗口，日志里**只有我们翻译过的词**（"未授权"/"refresh_token
   失效"），平台到底回了什么（403 挑战页？401？429？）无从得知。现在失败那一轮会把
   `HTTP <status> body <前 240 字>` 打进 `renew_token.log`。
3. **UA 不统一（已统一为浏览器 UA）**
   实测同一端点同一时刻：`Python-urllib/3.11` → **403**，Chrome / DZMM-Agent → 200。
   `requests` 默认 UA（`python-requests/x.y`）是典型爬虫签名，换成浏览器 UA 是零成本保险；
   `dzmm.api()` 与 `renew_token.get_new_token()` 都改了。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                    # noqa: E402

import token_store as TS                                         # noqa: E402
import renew_token as R                                          # noqa: E402
import dzmm                                                      # noqa: E402


# ---------------------------------------------------------------- ① 原子写

def test_save_cookies_is_atomic(tmp_path=None, monkeypatch=None):
    """写坏中途不能毁掉原文件：必须是 tmp + os.replace。"""
    src = (ROOT / "token_store.py").read_text(encoding="utf-8")
    i = src.index("def save_cookies")
    seg = src[i:i + 1400]
    assert "os.replace(" in seg, "必须原子替换"
    assert "os.fsync(" in seg, "要落盘再替换"
    body = seg.split('"""')[-1]              # 文档字符串**之后**的代码才算
    assert "write_text(" not in body, "不许再直接用 write_text 覆盖凭据文件"


def test_save_cookies_round_trip(monkeypatch):
    """行为级：写进去能读出来，而且不留 .tmp；失败时原文件不变。"""
    real = TS.TOKEN_FILE
    fake = ROOT / ".pytest-token.json"
    fake.unlink(missing_ok=True)
    monkeypatch.setattr(TS, "TOKEN_FILE", fake, raising=False)
    monkeypatch.setattr(TS, "_note_written", lambda: None, raising=False)
    try:
        assert TS.save_cookies([{"name": "a", "value": "1"}]) is True
        assert TS.get_cookie_dict() == {"a": "1"}
        assert not (ROOT / ".pytest-token.json.tmp").exists(), "临时文件要已被 rename 掉"
        # 再写一次，内容是新的；旧的临时文件不会残留
        assert TS.save_cookies([{"name": "a", "value": "2"}]) is True
        assert TS.get_cookie_dict() == {"a": "2"}
    finally:
        fake.unlink(missing_ok=True)
        (ROOT / ".pytest-token.json.tmp").unlink(missing_ok=True)
        monkeypatch.setattr(TS, "TOKEN_FILE", real, raising=False)


def test_update_cookie_keeps_other_cookies(monkeypatch):
    fake = ROOT / ".pytest-token2.json"
    fake.unlink(missing_ok=True)
    monkeypatch.setattr(TS, "TOKEN_FILE", fake, raising=False)
    monkeypatch.setattr(TS, "_note_written", lambda: None, raising=False)
    try:
        TS.save_cookies([{"name": "a", "value": "1"}, {"name": "b", "value": "2"}])
        assert TS.update_cookie("b", "22") is True
        d = TS.get_cookie_dict()
        assert d == {"a": "1", "b": "22"}
    finally:
        fake.unlink(missing_ok=True)
        (ROOT / ".pytest-token2.json.tmp").unlink(missing_ok=True)


# ------------------------------------------------- ①b 挂载点上必须退化（P12-BL-fix）

def test_bind_mount_fallback(monkeypatch):
    """**这条是真事故**：`.dzmm_token.json` 是单文件 bind mount，
    在它上面 `os.replace` 会 EBUSY —— 2026-09-30 17:40 线上连续
    `FAIL: 写回 token JSON 失败` → EMERGENCY，续期写不回去。

    要求：`os.replace` 失败时**退化成就地写**（并先备份 .bak），而不是直接报失败。
    """
    fake = ROOT / ".pytest-token3.json"
    bak = ROOT / ".pytest-token3.json.bak"
    tmp = ROOT / ".pytest-token3.json.tmp"
    for f in (fake, bak, tmp):
        f.unlink(missing_ok=True)
    monkeypatch.setattr(TS, "TOKEN_FILE", fake, raising=False)
    monkeypatch.setattr(TS, "_note_written", lambda: None, raising=False)
    TS._WARNED.clear()
    monkeypatch.setattr(TS.os, "replace",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError(16, "busy")),
                        raising=False)
    try:
        assert TS.save_cookies([{"name": "a", "value": "old"}]) is True, "第一次要先成功"
        assert TS.get_cookie_dict() == {"a": "old"}
        assert TS.save_cookies([{"name": "a", "value": "new"}]) is True, "退化后要能写"
        assert TS.get_cookie_dict() == {"a": "new"}, "内容必须是新的"
        assert bak.exists(), "就地写之前要留备份"
        assert not tmp.exists(), "临时文件别残留"
    finally:
        for f in (fake, bak, tmp):
            f.unlink(missing_ok=True)


def test_load_falls_back_to_bak(monkeypatch):
    """就地写没有原子性：主文件被写坏时必须能从 `.bak` 捞回来（别让凭据彻底没了）。"""
    fake = ROOT / ".pytest-token4.json"
    bak = ROOT / ".pytest-token4.json.bak"
    for f in (fake, bak):
        f.unlink(missing_ok=True)
    monkeypatch.setattr(TS, "TOKEN_FILE", fake, raising=False)
    TS._WARNED.clear()
    try:
        bak.write_text(json.dumps({"cookies": [{"name": "a", "value": "keep"}]}),
                       encoding="utf-8")
        assert TS.get_cookie_dict() == {"a": "keep"}, "主文件不存在时用 .bak"
        fake.write_text("{半截坏 JSON", encoding="utf-8")
        assert TS.get_cookie_dict() == {"a": "keep"}, "主文件坏了也要回退 .bak"
    finally:
        for f in (fake, bak):
            f.unlink(missing_ok=True)


# ---------------------------------------------------------------- ② 原始响应留证

def test_failure_log_prints_the_raw_response():
    src = (ROOT / "renew_token.py").read_text(encoding="utf-8")
    assert 'LAST_HTTP["status"]' in src and 'LAST_HTTP["body"]' in src
    i = src.index("原始响应: HTTP")
    seg = src[max(0, i - 400):i]
    assert "if not new_at:" in seg, "只在'没拿到 access_token'那一轮打"


def test_get_new_token_records_status(monkeypatch):
    class _Resp:
        status_code = 403
        text = "<html>WAF</html>"

        def json(self):
            return {"error": "未授权"}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _Resp(), raising=False)
    monkeypatch.setattr(R, "PROXY", {}, raising=False)
    R.LAST_HTTP.update({"status": 0, "body": ""})
    out = R.get_new_token()
    assert out == {"error": "未授权"}
    assert R.LAST_HTTP["status"] == 403
    assert "WAF" in R.LAST_HTTP["body"]


# ---------------------------------------------------------------- ③ 统一 UA

def test_all_non_browser_calls_use_a_browser_ua():
    for mod, src_path in ((R, "renew_token.py"), (dzmm, "dzmm.py")):
        assert "Chrome/" in mod.BROWSER_UA, src_path
        assert "Mozilla/5.0" in mod.BROWSER_UA, src_path
    src_r = (ROOT / "renew_token.py").read_text(encoding="utf-8")
    src_d = (ROOT / "dzmm.py").read_text(encoding="utf-8")
    assert 'headers={"User-Agent": BROWSER_UA}' in src_r, "续期请求要带 UA"
    assert 'headers={"User-Agent": BROWSER_UA}' in src_d, "tRPC 请求要带 UA"


def test_ua_evidence_is_recorded_in_the_probe():
    """留个可复现的实验脚本（不然下一个人不知道这个 UA 结论从哪来）。"""
    p = ROOT / "tools" / "probes" / "probe_ua_wall.py"
    assert p.exists()
    txt = p.read_text(encoding="utf-8")
    assert "Python-urllib" in txt and "Chrome 126" in txt
