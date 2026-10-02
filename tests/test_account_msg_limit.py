"""P12-AN：账号通道单条上限 **200 字** —— 超了静默失败，长回复全灭。

## 现场（2026-09-29 屋主："茶水间@你怎么没反应了？"）

顶层茶水间（没有 Bot，只走**账号**通道）的缺勤回顾 400+ 字，群里一条都没出现；
而出站队列里它们全是 `status=sent`。日志只有一行 `⚠️ 假成功检测：消息未投递到群里`
然后 `fallback(DZMMSocket): ✅`（那行 ✅ 也是假的，见下）。

## 夹逼出来的阈值（`bot测试` 群，真发真读回）

| 消息 | 结果 |
|---|---|
| 8 / 12 / 20 / 108 / **190** 字 | ✅ 落地 |
| **210** / 240 / 250 / 400 / 412 字 | ❌ 一条都没落地（1 行、4 行、6 行、12 行都一样） |

→ 上限是 **200 字**，与行数无关；**超了平台前端直接不收**（Enter 无效，不报错）。
官方 Bot API 那条路是 10000 字，所以"长回复"在 Bot 通道正常、在账号通道全灭。

## 两个真 bug

1. **账号通道不发长消息** → `_split_for_account()` 切成 ≤190 字的多条依次发（每条照常读回验证）。
2. **Socket.IO 兜底"假成功"** → `socketio.DZMMSocket._try_send` 只看 `status_code < 400`
   就返回成功，`_fallback_send` 直接采信 → 出站队列记 `sent`，群里什么都没有。
   现在兜底也要**读回验证**，读不到就判未投递。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                  # noqa: E402

import bot                                                     # noqa: E402
from runtime import sender as S                                # noqa: E402  P3-7：Sender 真身


# ---------------------------------------------------------------- ① 切分

def test_short_message_is_not_split():
    assert bot._split_for_account("姐姐好呀～") == ["姐姐好呀～"]


def test_cap_is_below_the_platform_limit():
    """实测 190 ✅ / 210 ❌ → 我们的阈值必须 ≤200，且留一点余量。"""
    assert 100 <= bot.ACCOUNT_MSG_MAX_CHARS <= 200


def test_long_text_is_split_into_parts_within_limit():
    text = "\n".join("· 第 {} 条：{}".format(i, "啊" * 40) for i in range(1, 8))
    parts = bot._split_for_account(text)
    assert len(parts) > 1, "400+ 字必须被切开"
    assert all(len(p) <= bot.ACCOUNT_MSG_MAX_CHARS for p in parts), \
        [len(p) for p in parts]
    # 不丢内容（拼回来必须与原文本一致：段间补回换行）
    assert "\n".join(parts) == text


def test_splitting_prefers_line_boundaries():
    """尽量在换行处断，别把一句话劈成两半。"""
    text = ("第一段说完这句话。\n"
            "第二段也说完这句话。\n"
            "第三段继续说这句话。")
    parts = bot._split_for_account(text, limit=12)
    assert parts[0] == "第一段说完这句话。", parts
    assert all(len(p) <= 12 for p in parts)
    assert "\n".join(parts) == text


def test_over_long_single_line_is_hard_split():
    text = "啊" * 500
    parts = bot._split_for_account(text, limit=190)
    assert len(parts) == 3
    assert all(len(p) <= 190 for p in parts)
    assert "".join(parts) == text


def test_empty_and_edge_cases():
    assert bot._split_for_account("") == [""]
    assert bot._split_for_account("短") == ["短"]
    assert bot._split_for_account("\n\n")  # 不抛异常


def test_the_real_recap_shape_gets_split():
    """复盘现场那条（400+ 字、14 行的回顾）必须被切成 ≤190 字的多条。"""
    text = ("🤖 📮 你不在的时候（最近 3 小时 · 群里 453 条，这里挑最近 155 条看）\n"
            "🔔 有人找你：囡囡、紫苑、御褚 提到你 3 次\n"
            "💬 大家在聊：\n"
            "· 柒夏说淫趴本要提前凑好人、分成几个1v1才能玩，不然根本玩不了\n"
            "· 紫苑吐槽夜魇偷她模板，夜魇回不要介意小事，还说古风不适合囡囡\n"
            "· 苏白和G_百戏聊彩票，G_百戏中奖转正了，苏白说正数就辞职\n"
            "🎭 群里的事：\n"
            "· 09:30 那场投票结果出来，演的是《公园——露出调教》\n"
            "· 黑瞳通过悦·大王的链接加入了群聊\n"
            "📌 等你回：\n"
            "· 紫苑在问总监事和小小糯的指令会不会重合，彻说应该不会\n"
            "· 玖欣在问怎么不能发视频动态了，说需要vip1，亦书说能发\n")
    assert len(text) > bot.ACCOUNT_MSG_MAX_CHARS, "前提：这条本来就超标"
    parts = bot._split_for_account(text)
    assert len(parts) >= 2, [len(p) for p in parts]
    assert all(len(p) <= bot.ACCOUNT_MSG_MAX_CHARS for p in parts)
    assert "\n".join(parts) == text


# ---------------------------------------------------------------- ② 接线

def test_send_path_splits_before_typing():
    # P3-7 起 _send_locked（发送主路径）住 runtime/sender.py
    src = (ROOT / "runtime" / "sender.py").read_text(encoding="utf-8")
    i = src.index("P12-AN：**账号通道单条上限 200 字**")
    seg = src[i:i + 2600]
    assert "_split_for_account(text, ACCOUNT_MSG_MAX_CHARS)" in seg
    assert "for _pi, _part in enumerate(parts)" in seg
    assert "await self._verify_sent(cid, _part)" in seg, "每一段都要读回验证"


def test_socket_fallback_must_verify_readback():
    """兜底那条路原来只看 HTTP 状态码 → 出站队列里的"sent"是假的。

    P3-7 起 `_fallback_send` 住 runtime/sender.py。
    """
    src = (ROOT / "runtime" / "sender.py").read_text(encoding="utf-8")
    i = src.index("P12-AN：Socket.IO 那条路只看 HTTP 状态码")
    seg = src[i:i + 700]
    assert "_verify_sent(cid, text)" in seg
    assert "判为未投递" in seg


def test_fallback_returns_false_when_readback_fails(monkeypatch):
    """行为级：兜底报成功但读回看不到 → 必须返回 False（别再骗出站队列）。

    这条不需要真浏览器：把 `socketio.DZMMSocket` 换成一个"永远说成功"的替身，
    再把 `_verify_sent` 换成"永远说没看到"，然后看 `_fallback_send` 返回什么。
    本项目没装 pytest-asyncio，所以用 `asyncio.run` 自己跑。
    """
    import asyncio
    import types

    sent = bot.Sender.__new__(bot.Sender)          # 不走 __init__（不需要浏览器）

    async def fake_verify(cid, text):
        return False

    monkeypatch.setattr(bot.Sender, "_verify_sent", staticmethod(fake_verify))

    class FakeSock:
        def __init__(self, *a, **kw):
            pass

        def send_message(self, *a, **kw):
            return True                            # 假的"HTTP 成功"

    fake_mod = types.SimpleNamespace(DZMMSocket=FakeSock)
    monkeypatch.setattr(S, "get_cached_cookie", lambda: "c", raising=False)  # P3-7

    import importlib.util
    real_spec = importlib.util.spec_from_file_location

    class FakeLoader:
        def exec_module(self, module):
            return None

    def fake_spec(*a, **kw):
        return types.SimpleNamespace(loader=FakeLoader())

    monkeypatch.setattr(importlib.util, "spec_from_file_location", fake_spec)
    monkeypatch.setattr(importlib.util, "module_from_spec", lambda spec: fake_mod)

    ok = asyncio.run(bot.Sender._fallback_send(sent, "很长的回顾内容" * 30, "cid-x"))
    assert ok is False, "读回看不到就必须判失败"

    # 反向：读回能看到 → 才算成功
    async def verify_ok(cid, text):
        return True

    monkeypatch.setattr(bot.Sender, "_verify_sent", staticmethod(verify_ok))
    assert asyncio.run(bot.Sender._fallback_send(sent, "短", "cid-x")) is True
