"""P12-AZ：账号的长消息能**一条**发完（走 Socket.IO，不是那个 200 字的打字框）。

## 现场（屋主 2026-09-30："为啥茶水间的运势分两段发啊"）

P12-AN 夹逼出的 200 字上限是**打字那条路**的（Playwright `insert_text` + Enter，
平台前端超限直接不收）。而账号身份还有**第二条投递路**：
`socketio.DZMMSocket`（Socket.IO `message:send`，`sent_by` 同样是账号 uid）。

实测（自检群 `bot测试`，2026-09-30）：

| 形状 | 结果 |
|---|---|
| 273 字 / 9 行的整张运势卡 | ✅ **一条完整落地**（第 1 次握手就成） |
| 278 字 / 9 行 | ✅ **一条完整落地**（第 4 次握手才成） |

失败长这样：`connect=400 {"code":1,"message":"Session ID unknown"}`
（这条路"时好时坏"）；而且**`connect` 失败时 `send` 有时也回 `200 ok`、群里什么都没有**
—— 所以**只有读回算数**，回执只用来判断"要不要重新握手"。

于是策略是：长消息**先试 Socket.IO**（成了就是一条，和认证群走 Bot API 的观感一致），
每轮读回、读不到就重新握手，预算用完退回 P12-AN 的"切 ≤190 字 + 打字"。
最坏情况只是退回两条，不会变成没有回复。
"""
import asyncio
import importlib.util
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

import bot                                                       # noqa: E402
from runtime import sender as S                                  # noqa: E402  P3-7：Sender 真身


# ------------------------------------------------------------------ 工具

def _bare_sender():
    """不走 `__init__`（不需要浏览器）的 Sender。"""
    s = bot.Sender.__new__(bot.Sender)
    s._fail_streak = 0
    s._consecutive_send_fail = 0
    s._send_alerted = False
    s._send_locks = {}
    s.page = None
    s.pages = {}
    s.ctx = None
    s.current_cid = None
    s._pw = None
    return s


def _install_fake_socket_module(monkeypatch, sock_cls):
    """把 `_sio_send_long` 里那次 `spec_from_file_location` 换成我们的替身。"""
    fake_mod = types.SimpleNamespace(DZMMSocket=sock_cls)
    monkeypatch.setattr(
        importlib.util, "spec_from_file_location",
        lambda *a, **kw: types.SimpleNamespace(
            loader=types.SimpleNamespace(exec_module=lambda m: None)))
    monkeypatch.setattr(importlib.util, "module_from_spec", lambda spec: fake_mod)
    monkeypatch.setattr(S, "get_cached_cookie", lambda: "c", raising=False)  # P3-7：真身在 runtime/sender


def _patch_async(monkeypatch):
    """把 `bot.asyncio` 换成"不真的等"的替身（测试里 2.0s/1.5s 的等待是浪费）。"""
    slept = []

    async def _sleep(sec):
        slept.append(sec)

    async def _to_thread(fn, *a, **kw):
        return fn(*a, **kw)

    fake = types.SimpleNamespace(sleep=_sleep, to_thread=_to_thread)
    monkeypatch.setattr(S, "asyncio", fake)   # P3-7：Sender 真身读 runtime.sender 的 asyncio
    return slept


class _SockRecorder:
    """可编排的 DZMMSocket 替身：`script` 里每项是 send 的返回，最后一次重复。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, origin):                    # DZMMSocket(ORIGIN)
        return self

    def send_message_ex(self, cid, uid, text, cookie, retries=1, message_id=""):
        self.calls.append({"cid": cid, "uid": uid, "text": text, "cookie": cookie,
                           "retries": retries})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        ok, note = item
        return ok, note, "mid-{}".format(len(self.calls))


# ------------------------------------------------------------------ ① 接线

def test_long_path_runs_before_the_split_typing_path():
    # P3-7 起 _send_locked（发送主路径）住 runtime/sender.py
    src = (ROOT / "runtime" / "sender.py").read_text(encoding="utf-8")
    i_sio = src.index("if allow_fallback and len(text) > ACCOUNT_MSG_MAX_CHARS:")
    i_an = src.index("P12-AN：**账号通道单条上限 200 字**")
    assert i_sio < i_an, "Socket.IO 一条发那条路必须在「切分打字」之前"
    seg = src[i_sio:i_sio + 400]
    assert "_sio_send_long(text, cid)" in seg
    assert "return True" in seg


def test_long_path_only_for_over_limit_text():
    src = (ROOT / "runtime" / "sender.py").read_text(encoding="utf-8")
    i = src.index("P12-AZ：长消息先试 Socket.IO")
    assert "len(text) > ACCOUNT_MSG_MAX_CHARS" in src[i:i + 400]
    assert "allow_fallback" in src[i:i + 400], "私聊发骰那种不走兜底的发送也要绕开这条路"


def test_verify_landed_looks_at_freshness_not_just_text():
    """冷却期重发同一张卡时，窗口里躺着上一次那张（逐字相同）——不卡时间就会静默丢。"""
    src = (ROOT / "runtime" / "sender.py").read_text(encoding="utf-8")
    i = src.index("async def _verify_landed")
    seg = src[i:i + 2200]
    assert "cutoff" in seg and "sent_at" in seg
    assert "time.gmtime" in seg, "平台 sent_at 是 UTC（茶水间 09:43 发的存 01:43）"
    assert "SIO_LONG_READBACK" in seg


# ------------------------------------------------------------------ ② _sio_send_long

def test_sio_long_sends_exactly_one_message(monkeypatch):
    """一次成功 = 只发一次（不可能重复）。"""
    sock = _SockRecorder([(True, "ok")])
    _install_fake_socket_module(monkeypatch, sock)
    _patch_async(monkeypatch)
    calls = []

    async def fake_verify(self, cid, text, message_id=""):
        calls.append(message_id)
        return True

    monkeypatch.setattr(bot.Sender, "_verify_landed", fake_verify)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._sio_send_long(s, "长" * 300, "cid-1")) is True
    assert len(sock.calls) == 1, [c["text"] for c in sock.calls]
    assert sock.calls[0]["cid"] == "cid-1"
    assert sock.calls[0]["retries"] == 1, "自己控制重试节奏，别让底层再套一层"
    assert calls == ["mid-1"]


def test_sio_long_retries_until_it_lands(monkeypatch):
    """时好时坏：第 3 次才落地 —— 必须重新握手，且**成功前不重复发已落地的内容**。"""
    sock = _SockRecorder([(False, "connect=400"), (False, "connect=400"), (True, "ok")])
    _install_fake_socket_module(monkeypatch, sock)
    _patch_async(monkeypatch)
    seen = []

    async def fake_verify(self, cid, text, message_id=""):
        seen.append(len(sock.calls))
        return len(sock.calls) >= 3

    monkeypatch.setattr(bot.Sender, "_verify_landed", fake_verify)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._sio_send_long(s, "长" * 300, "cid-1")) is True
    assert len(sock.calls) == 3
    assert seen == [1, 2, 3], "每次都读回"


def test_sio_long_returns_false_when_it_never_lands(monkeypatch):
    """一直没成 → 返回 False（调用方接着切分打字），别抛异常。"""
    sock = _SockRecorder([(False, "connect=400")])
    _install_fake_socket_module(monkeypatch, sock)
    _patch_async(monkeypatch)
    monkeypatch.setattr(S, "SIO_LONG_MAX_TRIES", 3, raising=False)  # P3-7

    async def fake_verify(self, cid, text, message_id=""):
        return False

    monkeypatch.setattr(bot.Sender, "_verify_landed", fake_verify)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._sio_send_long(s, "长" * 300, "cid-1")) is False
    assert len(sock.calls) == 3


def test_sio_long_survives_socket_module_failure(monkeypatch):
    """socketio.py 加载失败也不能把发送打挂（退回打字那条路）。"""
    def boom(*a, **kw):
        raise RuntimeError("no module")

    monkeypatch.setattr(importlib.util, "spec_from_file_location", boom)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._sio_send_long(s, "长" * 300, "cid-1")) is False


# --------------------------------------------------------- ②b 读消息那条长连接优先

class _FakeLiveSio:
    def __init__(self, connected=True, mid="mid-live"):
        self.is_connected = connected
        self.mid = mid
        self.sent = []

    async def send_text(self, cid, user_id, text):
        self.sent.append((cid, user_id, text))
        return self.mid


def test_long_message_prefers_the_live_subscription_socket(monkeypatch):
    """读消息那条长连接已经连着了 ⇒ 长消息优先用它（不用握手、不会撞 Session ID unknown）。"""
    live = _FakeLiveSio()
    monkeypatch.setattr(S, "_LIVE_SIO", live, raising=False)  # P3-7：真身住 runtime/sender
    sock = _SockRecorder([(True, "ok")])
    _install_fake_socket_module(monkeypatch, sock)
    _patch_async(monkeypatch)

    async def fake_verify(self, cid, text, message_id=""):
        return message_id == "mid-live"

    monkeypatch.setattr(bot.Sender, "_verify_landed", fake_verify)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._sio_send_long(s, "长" * 300, "cid-1")) is True
    assert len(live.sent) == 1 and live.sent[0][0] == "cid-1"
    assert sock.calls == [], "长连接成了就不该再去 HTTP 握手"


def test_live_socket_miss_falls_through_to_http_handshake(monkeypatch):
    """长连接没落地（或没连上）→ 接着走 HTTP 握手那条老路。"""
    live = _FakeLiveSio(mid="mid-live")
    monkeypatch.setattr(S, "_LIVE_SIO", live, raising=False)  # P3-7：真身住 runtime/sender
    sock = _SockRecorder([(True, "ok")])
    _install_fake_socket_module(monkeypatch, sock)
    _patch_async(monkeypatch)

    async def fake_verify(self, cid, text, message_id=""):
        return message_id != "mid-live"          # 只认 HTTP 那条的 mid

    monkeypatch.setattr(bot.Sender, "_verify_landed", fake_verify)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._sio_send_long(s, "长" * 300, "cid-1")) is True
    assert len(live.sent) == 1, "先试长连接"
    assert len(sock.calls) == 1, "没成再握着 HTTP 握手"


def test_disconnected_live_socket_is_skipped(monkeypatch):
    live = _FakeLiveSio(connected=False)
    monkeypatch.setattr(S, "_LIVE_SIO", live, raising=False)  # P3-7：真身住 runtime/sender
    sock = _SockRecorder([(True, "ok")])
    _install_fake_socket_module(monkeypatch, sock)
    _patch_async(monkeypatch)

    async def fake_verify(self, cid, text, message_id=""):
        return True

    monkeypatch.setattr(bot.Sender, "_verify_landed", fake_verify)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._sio_send_long(s, "长" * 300, "cid-1")) is True
    assert live.sent == [], "没连上就别去 emit"
    assert len(sock.calls) == 1


def test_live_socket_send_exception_is_swallowed(monkeypatch):
    class Boom(_FakeLiveSio):
        async def send_text(self, cid, user_id, text):
            raise RuntimeError("emit 炸了")

    monkeypatch.setattr(S, "_LIVE_SIO", Boom(), raising=False)  # P3-7
    sock = _SockRecorder([(True, "ok")])
    _install_fake_socket_module(monkeypatch, sock)
    _patch_async(monkeypatch)

    async def fake_verify(self, cid, text, message_id=""):
        return True

    monkeypatch.setattr(bot.Sender, "_verify_landed", fake_verify)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._sio_send_long(s, "长" * 300, "cid-1")) is True
    assert len(sock.calls) == 1, "长连接抛异常也要继续走 HTTP"


# --------------------------------------------------------- ②c SioSubscriber.send_text

class _EmitRecorder:
    def __init__(self, awaitable=True):
        self.events = []
        self.awaitable = awaitable

    def emit(self, event, data):
        self.events.append((event, data))
        if not self.awaitable:
            return None

        async def _noop():
            return None

        return _noop()


def _sub_with(connected=True, awaitable=True):
    import sio_subscribe
    sub = sio_subscribe.SioSubscriber(["cid-1"])
    sub._sio = _EmitRecorder(awaitable=awaitable)
    if connected:
        sub._connected.set()
    return sub


def test_send_text_emits_message_send_on_the_live_socket():
    sub = _sub_with()
    mid = asyncio.run(sub.send_text("cid-1", "uid-1", "晚安"))
    assert mid, "连上了就该给出 message_id"
    event, payload = sub._sio.events[0]
    assert event == "message:send"
    assert payload["chatroomId"] == "cid-1"
    msg = payload["message"]
    assert msg["message_id"] == mid and msg["sent_by"] == "uid-1"
    assert msg["content"] == {"type": "text", "text": "晚安"}
    assert msg["sent_at"].endswith("Z"), "平台存 UTC"


def test_send_text_works_with_a_sync_emit():
    """万一 emit 不是协程（版本差异）也不能炸。"""
    sub = _sub_with(awaitable=False)
    assert asyncio.run(sub.send_text("cid-1", "uid-1", "晚安"))
    assert len(sub._sio.events) == 1


def test_send_text_refuses_when_disconnected():
    sub = _sub_with(connected=False)
    assert asyncio.run(sub.send_text("cid-1", "uid-1", "晚安")) == ""
    assert sub._sio.events == []


def test_send_text_swallows_emit_errors():
    sub = _sub_with()

    def boom(event, data):
        raise RuntimeError("连接没了")

    sub._sio.emit = boom
    assert asyncio.run(sub.send_text("cid-1", "uid-1", "晚安")) == ""


def test_main_hands_the_live_socket_to_the_sender():
    """P3-7 起 `_LIVE_SIO` 真身住 runtime/sender.py；装配点经模块引用写它。

    P3-19 起装配点随 _run_all 体在 runtime/mainloop.py（bot 桥 B 前缀，
    `B._sender_mod._LIVE_SIO = subscriber` 与原写法语义一致）。
    """
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    assert "_sender_mod._LIVE_SIO = subscriber" in src, "写点必须经 runtime.sender 模块引用"
    src_b = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "global _LIVE_SIO" not in src_b, "bot 侧不许再留镜像全局（会分叉）"
    assert "global _LIVE_SIO" not in src, "mainloop 侧也不许留镜像全局（会分叉）"
    src_s = (ROOT / "runtime" / "sender.py").read_text(encoding="utf-8")
    assert "_LIVE_SIO = None" in src_s, "真身（初值 None）必须住 sender 模块"


# ------------------------------------------------------------------ ③ _send_locked 接线

def test_send_locked_uses_sio_for_long_text(monkeypatch):
    """长消息：SIO 成了就直接返回，**根本不碰浏览器**。"""
    used = []

    async def fake_sio(self, text, cid):
        used.append((text, cid))
        return True

    async def fake_token(self):
        return True

    monkeypatch.setattr(bot.Sender, "_sio_send_long", fake_sio)
    monkeypatch.setattr(bot.Sender, "_refresh_token", fake_token)
    s = _bare_sender()
    long_text = "🤖 " + "长" * 400
    assert asyncio.run(bot.Sender._send_locked(s, long_text, "cid-1")) is True
    assert used == [(long_text, "cid-1")]


def test_send_locked_skips_sio_when_fallback_disabled(monkeypatch):
    """`allow_fallback=False`（私聊发骰那类）必须绕开 Socket.IO —— 那是"假成功"的老家。"""
    used = []

    async def fake_sio(self, text, cid):
        used.append(text)
        return True

    async def fake_token(self):
        return True

    async def fake_navigate(self, cid):
        raise RuntimeError("no browser")

    monkeypatch.setattr(bot.Sender, "_sio_send_long", fake_sio)
    monkeypatch.setattr(bot.Sender, "_refresh_token", fake_token)
    monkeypatch.setattr(bot.Sender, "navigate", fake_navigate)
    monkeypatch.setattr(bot.Sender, "_record_fail", lambda self: None)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._send_locked(
        s, "🤖 " + "长" * 400, "cid-1", allow_fallback=False)) is False
    assert used == []


# ------------------------------------------------------------------ ④ _verify_landed

def _msg(text, sent_by="u-self", sent_at=None, mid="m1"):
    if sent_at is None:
        sent_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    return {"message_id": mid, "sent_by": sent_by, "sent_at": sent_at,
            "content": {"type": "text", "text": text}}


def _patch_reads(monkeypatch, msgs):
    monkeypatch.setattr(S, "get_messages", lambda cid, limit=20: msgs)  # P3-7
    monkeypatch.setattr(S, "is_self", lambda uid: True)  # P3-7
    _patch_async(monkeypatch)


def test_verify_landed_accepts_a_fresh_message(monkeypatch):
    _patch_reads(monkeypatch, [_msg("别的消息"), _msg("🤖 🔮 墨莲 今日运势 83分（吉）\n…")])
    s = _bare_sender()
    assert asyncio.run(bot.Sender._verify_landed(
        s, "cid-1", "🤖 🔮 墨莲 今日运势 83分（吉）\n————\n…")) is True


def test_verify_landed_rejects_a_stale_identical_message(monkeypatch):
    """冷却期重发同一张卡：窗口里那条是**上一次**的，不能算这一条发出去了。"""
    old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3600))
    _patch_reads(monkeypatch, [_msg("🤖 🔮 墨莲 今日运势 83分（吉）\n————", sent_at=old)])
    s = _bare_sender()
    assert asyncio.run(bot.Sender._verify_landed(
        s, "cid-1", "🤖 🔮 墨莲 今日运势 83分（吉）\n————")) is False


def test_verify_landed_rejects_other_peoples_messages(monkeypatch):
    monkeypatch.setattr(S, "get_messages", lambda cid, limit=20: [_msg("同一句话")])  # P3-7
    monkeypatch.setattr(S, "is_self", lambda uid: False)  # P3-7
    _patch_async(monkeypatch)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._verify_landed(s, "cid-1", "同一句话")) is False


def test_verify_landed_accepts_our_own_message_id(monkeypatch):
    """message_id 是我们自己生成的 uuid —— 它匹配上就一定是我们刚发的那条。"""
    old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3600))
    _patch_reads(monkeypatch, [_msg("完全不同的文本", sent_at=old, mid="mid-exact")])
    s = _bare_sender()
    assert asyncio.run(bot.Sender._verify_landed(
        s, "cid-1", "要不要紧的文本", "mid-exact")) is True


def test_verify_landed_returns_false_when_reads_keep_failing(monkeypatch):
    """读回一直失败 → False（宁可重复一条，也别像 P12-AN 那样整条消失）。"""
    def boom(cid, limit=20):
        raise RuntimeError("http 502")

    monkeypatch.setattr(S, "get_messages", boom)  # P3-7
    monkeypatch.setattr(S, "is_self", lambda uid: True)  # P3-7
    slept = _patch_async(monkeypatch)
    s = _bare_sender()
    assert asyncio.run(bot.Sender._verify_landed(s, "cid-1", "同一句话")) is False
    assert slept.count(1.5) == 3, "三次机会都试过了"


def test_verify_landed_ignores_empty_timestamp(monkeypatch):
    """时间戳读不出来 = 没法证明是新鲜的 ⇒ 不算落地（宁可重复，不可静默丢）。"""
    _patch_reads(monkeypatch, [_msg("同一句话", sent_at="")])
    s = _bare_sender()
    assert asyncio.run(bot.Sender._verify_landed(s, "cid-1", "同一句话")) is False


# ------------------------------------------------------------------ ⑤ socketio.py 的回执

def _load_socketio():
    spec = importlib.util.spec_from_file_location(
        "dzmm_socketio_under_test", str(ROOT / "socketio.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http " + str(self.status_code))


class _FakeSession:
    def __init__(self, connect_code=200, send_code=200):
        self.headers = {}
        self.proxies = {}
        self.connect_code = connect_code
        self.send_code = send_code
        self.posts = []

    def get(self, url, timeout=None, **kw):
        if "sid=" not in url:
            return _Resp(200, '0{"sid":"S1","upgrades":["websocket"]}')
        return _Resp(200, "")

    def post(self, url, data=None, headers=None, timeout=None, **kw):
        self.posts.append(bytes(data or b""))
        if data == b"40":
            return _Resp(self.connect_code,
                         '{"code":1,"message":"Session ID unknown"}'
                         if self.connect_code >= 400 else "")
        return _Resp(self.send_code, "ok")

    def close(self):
        pass


def _with_fake_session(monkeypatch, mod, **kw):
    fake = _FakeSession(**kw)
    monkeypatch.setattr(mod.requests, "Session", lambda: fake)
    return fake


def test_try_send_reports_connect_failure(monkeypatch):
    """`connect` 400（Session ID unknown）就是这条路最常见的失败 —— 要能说出来。"""
    mod = _load_socketio()
    _with_fake_session(monkeypatch, mod, connect_code=400, send_code=200)
    note = mod.DZMMSocket("https://x")._try_send("cid", "uid", "文本", "c")
    assert note.startswith("connect=400"), note


def test_send_message_ex_never_trusts_the_200(monkeypatch):
    """**connect 失败时 send 也会回 `200 ok`** —— 内部必须判失败，让上层重新握手。"""
    mod = _load_socketio()
    _with_fake_session(monkeypatch, mod, connect_code=400, send_code=200)
    ok, note, mid = mod.DZMMSocket("https://x").send_message_ex(
        "cid", "uid", "文本", "c", retries=1, message_id="mid-1")
    assert ok is False and note.startswith("connect=400") and mid == "mid-1"


def test_send_message_ex_ok_carries_the_message_id(monkeypatch):
    mod = _load_socketio()
    fake = _with_fake_session(monkeypatch, mod, connect_code=200, send_code=200)
    ok, note, mid = mod.DZMMSocket("https://x").send_message_ex(
        "cid", "uid", "文本", "c", retries=1, message_id="mid-9")
    assert (ok, note, mid) == (True, "ok", "mid-9")
    batch = fake.posts[-1].decode("utf-8")
    assert "mid-9" in batch and "message:send" in batch


def test_send_message_still_returns_a_bool(monkeypatch):
    """老调用方（`send_missed_recap.py`）要的还是一个 bool。"""
    mod = _load_socketio()
    _with_fake_session(monkeypatch, mod, connect_code=400, send_code=400)
    assert mod.DZMMSocket("https://x").send_message("cid", "uid", "文本", "c",
                                                    retries=1) is False


def test_no_password_or_cookie_in_the_logged_note(monkeypatch):
    """回执里不许带 cookie（那条信息会进日志）。"""
    mod = _load_socketio()
    _with_fake_session(monkeypatch, mod, connect_code=400, send_code=200)
    _ok, note, _mid = mod.DZMMSocket("https://x").send_message_ex(
        "cid", "uid", "文本", "SECRET-COOKIE", retries=1)
    assert "SECRET-COOKIE" not in note


if __name__ == "__main__":                                      # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
