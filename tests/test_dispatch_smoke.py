"""dispatch 分派链**真调用**冒烟（P4-4C 补盲区，2026-10-02）。

背景：分派链（`runtime/dispatch.py::handle_slash`）的既有测试几乎全是
getsource/字符串断言 —— 线上 main_loop 改名事故与 screen 死闸门事故的
共同教训：**"源码长这样"不等于"行为对"**。

本文件真调 `handle_slash()`（main_loop `if cmd:` 块的真身，kw 注入全部
用桩），构造最小 group_state / state / msgs / 假 sender，走完整 async 链。

选的全是**不调 AI、不碰浏览器**的确定性路径：
  /帮助（personas 话术填充）/ /吐槽开|关（群状态翻转）/ 未知指令（静默）。
断言四件事：发送了正确文本、last_id 推进、_save_group 落盘、inflight 标记。
"""
import asyncio

from runtime import dispatch as D

CID = "cid-dispatch-smoke-01"
GUEST = "uid-guest-01"


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, text, cid=None, **kw):
        self.sent.append(str(text))
        return True


class _Record:
    def __init__(self):
        self.calls = []

    def __call__(self, *a, **k):
        self.calls.append(a)
        return None


def _slash(cmd, mid="m-1"):
    """真调 handle_slash，返回 (FakeSender, group_state, state, 注入桩)。"""
    env = {"save_group": _Record(), "mark_inflight": _Record(),
           "remember_turn": _Record()}
    sender = FakeSender()
    gs, st = {"last_id": ""}, {}
    msgs = [{"message_id": mid, "sender": GUEST}]
    asyncio.run(D.handle_slash(
        sender, CID, cmd, GUEST, False, gs, msgs, msgs[0], st,
        _save_group=env["save_group"], _mark_inflight=env["mark_inflight"],
        _remember_turn=env["remember_turn"],
        rank_summary=lambda top=10: "", sync_profiles=lambda: ""))
    return sender, gs, st, env


def test_help_replies_with_the_command_reference(dbdir):
    """/帮助：走 personas 话术填充（不调 AI），且链首协议要执行."""
    sender, gs, _st, env = _slash("/帮助")
    assert sender.sent and "指令说明" in sender.sent[-1], sender.sent
    assert gs["last_id"] == "m-1", "链首要推进 last_id（防同一条指令重复处理）"
    assert env["save_group"].calls, "last_id 要经 _save_group 落盘"
    assert env["mark_inflight"].calls, "处理中的指令要标 inflight（P12-BC）"


def test_devil_switch_flips_group_state(dbdir):
    """/吐槽开|关：群内开关翻转并落盘（feature devil_roast 默认开）."""
    sender, gs, _st, _env = _slash("/吐槽开")
    assert any("主动吐槽已开启" in t for t in sender.sent), sender.sent
    assert gs["devil_enabled"] is True
    sender2, gs2, _st2, _env2 = _slash("/吐槽关")
    assert any("主动吐槽已关闭" in t for t in sender2.sent), sender2.sent
    assert gs2["devil_enabled"] is False


def test_unknown_command_gets_the_polite_fallback_text(dbdir):
    """看不懂的指令：链内有专属文案（"还不会"+ 指引 /说明）——
    纯话术、不调 AI（真调用揭示的行为，就此钉住）。"""
    sender, _gs, _st, _env = _slash("/完全不存在xyz")
    assert len(sender.sent) == 1, sender.sent
    assert "还不会" in sender.sent[0] and "/说明" in sender.sent[0], sender.sent
