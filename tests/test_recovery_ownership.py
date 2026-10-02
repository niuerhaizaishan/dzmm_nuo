"""P12-BJ：三条收尾 —— ①告警按窗口去噪 ②墙期间探镜像域名 ③成员快照对账补筛查。

屋主 2026-09-30："可以，你干吧！"（批准了 P12-BI 文末的四条建议）

## 背景（都在 `docs/P12-BI-风控墙与会话真死分开.md` 里）

1. **告警刷屏**：一个"坏窗口"4~8 小时，每 5 分钟一条日志、每小时一条 EMERGENCY 微信。
   → 改成"进窗一条、出窗一条"（`renew_bad_window.json`）。
2. **换域名**：`switch_node.py` 有 13 个镜像域名的清单，但它**自己排除了 418**，从没触发过。
   → 墙期间主动探一遍（只读、不切换），把"哪个域名还能用"变成日志里的证据。
3. **入群盲区**：会话死/墙期间读不到消息，连"X 加入了群聊"都看不见 ⇒ `pending_screen`
   里也不会有它 ⇒ 永久漏掉（09-30 13:12 那位是恢复后靠补拉才补上的）。
   → 用**成员快照**对账：`当前成员 − 见过的成员` = 新增，交给原有 `_handle_joins` 筛查。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                    # noqa: E402

import bot                                                       # noqa: E402
import renew_token as R                                          # noqa: E402

SRC_R = (ROOT / "renew_token.py").read_text(encoding="utf-8")
# P3-19 起主循环体在 runtime/mainloop.py（对账接线点随之搬迁）
SRC_B = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 告警按窗口

@pytest.fixture
def win(monkeypatch):
    f = ROOT / ".pytest-bad-window.json"
    f.unlink(missing_ok=True)
    monkeypatch.setattr(R, "BAD_WINDOW_FILE", f, raising=False)
    logged = []
    monkeypatch.setattr(R, "log", lambda m, to_stdout=False: logged.append((m, to_stdout)),
                        raising=False)
    return {"file": f, "logged": logged}


def test_first_bad_round_opens_the_window(win):
    assert R._open_bad_window() is True, "窗口第一轮该告警"
    assert win["file"].exists()


def test_following_bad_rounds_do_not_alert_again(win):
    R._open_bad_window()
    for _ in range(5):
        assert R._open_bad_window() is False, "同一窗口内不许重复告警"
    assert json.loads(win["file"].read_text(encoding="utf-8"))["started"] > 0


def test_recovery_closes_the_window_and_reports_minutes(win):
    win["file"].write_text(json.dumps({"started": time.time() - 3600}), encoding="utf-8")
    mins = R._close_bad_window()
    assert 59 <= mins <= 61, mins
    assert not win["file"].exists()
    assert R._close_bad_window() == 0.0, "没坏过就别报"


def test_unauth_branch_alerts_once_per_window():
    """unauth 分支里那两处告警都要走 `_open_bad_window()` 判据。

    （P12-BO 之后 `if probe == "unauth":` 有两处，主分支是**最后一处**。）
    """
    i = SRC_R.rindex('if probe == "unauth":')
    seg = SRC_R[i:i + 2600]
    assert seg.count("_open_bad_window()") >= 2, "墙分支与真死分支都要去噪"
    assert "窗口内不重复告警" in seg


def test_recovery_alert_is_wired_in_main():
    i = SRC_R.index("def main()")
    seg = SRC_R[i:]
    assert "_close_bad_window()" in seg
    assert "续期恢复正常" in seg


# ---------------------------------------------------------------- ② 墙期间探节点

def test_node_probe_is_read_only_and_reports_each_host(monkeypatch):
    calls = []

    class _Resp:
        status_code = 418
        text = ""

        def json(self):
            return {}

    import requests

    def _get(url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(requests, "get", _get, raising=False)
    monkeypatch.setattr(R, "PROXY", {}, raising=False)
    out = R._probe_nodes(timeout=1)
    assert len(out) == len(R.NODE_CANDIDATES) >= 10
    assert all(len(t) == 3 for t in out)
    assert calls, "要真的探一遍（首页 + /api/auth/token）"


def test_wall_branch_probes_nodes_only_on_first_round():
    i = SRC_R.index('if wall == "wall":')
    seg = SRC_R[i:i + 1400]
    assert "_probe_nodes()" in seg
    assert "_open_bad_window()" in seg
    assert seg.index("_open_bad_window()") < seg.index("_probe_nodes()"), \
        "探节点只在窗口第一轮做"


def test_candidate_list_matches_switch_node():
    """清单要和宿主上那个 `switch_node.py` 一致（它才是这份清单的来源）。"""
    sw = (ROOT / "switch_node.py")
    if not sw.exists():
        pytest.skip("没有 switch_node.py")
    src = sw.read_text(encoding="utf-8")
    for host in R.NODE_CANDIDATES[:4]:
        assert host in src, host + " 应该在 switch_node.py 的候选里"


# ---------------------------------------------------------------- ③ 成员快照对账

def _fake_members(rows):
    return {"members": rows}


def test_first_run_only_snapshots(monkeypatch):
    """首次跑只建快照 —— 否则整群都会被当新人踢掉。"""
    saved = {}
    monkeypatch.setattr(bot.state_store, "group_get",
                        lambda cid, key, default=None: saved.get(key, default))
    monkeypatch.setattr(bot.state_store, "group_set",
                        lambda cid, key, val: saved.__setitem__(key, val))
    monkeypatch.setattr(bot, "is_self", lambda uid: False)
    monkeypatch.setattr(bot, "log", lambda m: None)

    class _Dzmm:
        @staticmethod
        def api(*a, **kw):
            return _fake_members([{"id": "u1", "isBot": False},
                                  {"id": "u2", "isBot": False},
                                  {"id": "b1", "isBot": True}])
    import types
    monkeypatch.setitem(sys.modules, "dzmm", types.SimpleNamespace(
        api=_Dzmm.api, get_members=lambda *a, **kw: {}))
    called = []

    async def _handle(sender, cid, uids, dry):
        called.append(list(uids))
        return []

    monkeypatch.setattr(bot, "_handle_joins", _handle)

    class _Sender:
        pass

    out = asyncio.run(bot._reconcile_members(_Sender(), "c1", True))
    assert out == [], "首次不筛查"
    assert called == []
    assert saved[bot.KNOWN_MEMBERS_KEY] == ["u1", "u2"], "Bot 不进快照"


def test_second_run_screens_only_the_newcomers(monkeypatch):
    saved = {bot.KNOWN_MEMBERS_KEY: ["u1", "u2"]}
    monkeypatch.setattr(bot.state_store, "group_get",
                        lambda cid, key, default=None: saved.get(key, default))
    monkeypatch.setattr(bot.state_store, "group_set",
                        lambda cid, key, val: saved.__setitem__(key, val))
    monkeypatch.setattr(bot, "is_self", lambda uid: False)
    monkeypatch.setattr(bot, "log", lambda m: None)
    monkeypatch.setattr(bot, "get_full_name", lambda uid: {"u3": "新人"}.get(uid, uid))
    monkeypatch.setattr(bot, "_personas",
                        type("P", (), {"fill": staticmethod(lambda t, cid: t)}), raising=False)
    monkeypatch.setattr(bot, "WELCOME_TEXT", "{name}", raising=False)

    async def _send(sender, cid, text, dry):
        return True

    monkeypatch.setattr(bot, "_send", _send, raising=False)
    import types
    monkeypatch.setitem(sys.modules, "dzmm", types.SimpleNamespace(
        api=lambda *a, **kw: _fake_members([{"id": "u1", "isBot": False},
                                            {"id": "u2", "isBot": False},
                                            {"id": "u3", "isBot": False}]),
        get_members=lambda *a, **kw: {}))
    seen = []

    async def _handle(sender, cid, uids, dry):
        seen.append(list(uids))
        return []

    monkeypatch.setattr(bot, "_handle_joins", _handle)
    out = asyncio.run(bot._reconcile_members(object(), "c1", True))
    assert out == ["u3"]
    assert seen == [["u3"]], "只筛新增的那一个"
    assert "u3" in saved[bot.KNOWN_MEMBERS_KEY], "快照要更新"


def test_member_read_failure_is_not_fatal(monkeypatch):
    monkeypatch.setattr(bot, "log", lambda m: None)
    import types

    def _boom(*a, **kw):
        raise RuntimeError("http 502")

    monkeypatch.setitem(sys.modules, "dzmm", types.SimpleNamespace(
        api=_boom, get_members=lambda *a, **kw: {}))
    assert asyncio.run(bot._reconcile_members(object(), "c1", True)) == []


def test_reconcile_is_wired_at_startup_behind_the_screen_switch():
    i = SRC_B.index("_reconcile_members(sender,")
    seg = SRC_B[max(0, i - 500):i + 200]
    assert '_feat_on(_cid, "screen")' in seg, "只在开了筛查的群里做"
    assert "state = B.load_state()" in SRC_B[:i], "要在拿到 state 之后"


# ---------------------------------------------------------------- ④ 宿主看护脚本

def test_monitor_recover_no_longer_restarts_for_session_issues():
    src = (ROOT / "monitor_recover.py").read_text(encoding="utf-8")
    assert "session_heartbeat_stale_min" in src, "要看容器里的续期心跳"
    assert "续期心跳" in src
    # 旧的"首页 OK + token invalid → 重启"必须已经不在了
    assert 'hp and ts == "invalid" and up' not in src
