"""P12-BJ：墙窗口证据的**落盘**与**决策统计**（"要不要自动换域名"靠它）。

屋主 2026-09-30："嗯，听你的吧" = 按建议**先攒数据再决定**。
所以这一轮不加自动切换，而是：

1. 每个坏窗口第一轮探一遍镜像域名时，把结果**落成 JSONL**
   （`data/wall_probes.jsonl`，一行一个窗口）；
2. 给出决策工具 `tools/analyze_wall_windows.py`，攒够后一眼看出"换了有没有用"。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import pytest                                                    # noqa: E402

import renew_token as R                                          # noqa: E402
import analyze_wall_windows as A                                 # noqa: E402

SRC = (ROOT / "renew_token.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 落盘

def test_probe_nodes_writes_a_structured_record(monkeypatch):
    f = ROOT / ".pytest-wall-probes.jsonl"
    f.unlink(missing_ok=True)
    monkeypatch.setattr(R, "WALL_PROBE_FILE", f, raising=False)
    monkeypatch.setattr(R, "PROXY", {}, raising=False)
    monkeypatch.setattr(R, "_home_status", lambda: "wall", raising=False)
    monkeypatch.setattr(R, "log", lambda m, to_stdout=False: None, raising=False)

    class _Resp:
        status_code = 418
        text = ""

        def json(self):
            return {}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _Resp(), raising=False)
    out = R._probe_nodes(timeout=1)
    assert out and f.exists(), "探测结果要落盘"
    rec = json.loads(f.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["origin_home"] == "wall"
    assert len(rec["nodes"]) == len(R.NODE_CANDIDATES)
    assert all(set(n) == {"host", "home", "token"} for n in rec["nodes"])
    assert "elapsed" in rec
    f.unlink(missing_ok=True)


def test_probe_record_failure_does_not_break_the_probe(monkeypatch):
    monkeypatch.setattr(R, "WALL_PROBE_FILE", Path("/proc/nope/x.jsonl"), raising=False)
    monkeypatch.setattr(R, "PROXY", {}, raising=False)
    monkeypatch.setattr(R, "_home_status", lambda: "wall", raising=False)
    logged = []
    monkeypatch.setattr(R, "log", lambda m, to_stdout=False: logged.append(m), raising=False)

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"access_token": "t"}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _Resp(), raising=False)
    out = R._probe_nodes(timeout=1)
    assert len(out) == len(R.NODE_CANDIDATES), "写盘失败也要把探测结果返回"


def test_timeout_is_bounded_and_evidence_persists():
    """超时必须压住（这段在持有会话锁时跑），而且证据要落在**挂载卷**里（重建不丢）。"""
    assert "_probe_nodes(timeout: float = 4)" in SRC
    i = SRC.index("WALL_PROBE_FILE = ")
    assert "env_config.data_dir()" in SRC[i:i + 200], \
        "JSONL 要落在 data/（挂载卷），否则容器一重建证据就没了"


# ---------------------------------------------------------------- ② 决策统计

def test_summarize_counts_usable_mirrors():
    rows = [
        {"origin_home": "wall",
         "nodes": [{"host": "a", "home": "200", "token": "token-ok"},
                   {"host": "b", "home": "418", "token": "401"}]},
        {"origin_home": "wall",
         "nodes": [{"host": "a", "home": "418", "token": "401"},
                   {"host": "b", "home": "418", "token": "401"}]},
        {"origin_home": "ok",
         "nodes": [{"host": "a", "home": "200", "token": "token-ok"}]},
    ]
    s = A.summarize(rows)
    assert s["windows"] == 3
    assert s["origin_bad"] == 2, "只有 origin_home 不是 ok/200 的才算墙里"
    assert s["alt_ok"] == 2
    assert s["all_alt_bad"] == 1
    assert s["per_host"]["a"] == 2 and s["per_host"]["b"] == 0


def test_summarize_handles_empty():
    s = A.summarize([])
    assert s["windows"] == 0 and s["alt_ok"] == 0 and not s["per_host"]


def test_load_probes_skips_broken_lines(tmp_path=None):
    p = ROOT / ".pytest-wall-load.jsonl"
    p.write_text('{"at": "x", "nodes": []}\n不是 json\n\n', encoding="utf-8")
    rows = A.load_probes(str(p))
    assert len(rows) == 1 and rows[0]["at"] == "x"
    p.unlink(missing_ok=True)


def test_decision_tool_is_documented_in_the_doc():
    doc = (ROOT / "docs" / "P12-BJ-恢复收口与告警去噪.md").read_text(encoding="utf-8")
    assert "wall_probes.jsonl" in doc
    assert "analyze_wall_windows" in doc
