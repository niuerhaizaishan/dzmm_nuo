"""P12-BK：把**浏览器里的** token 抄回 JSON（单向刷新的缺口）。

## 缺口（查"这几天经常续期失效"时发现的）

```
_refresh_token()   :  JSON ──→ 浏览器     （把续期后的新 cookie 推进页面）
SPA 自己刷新       :  浏览器里换掉 refresh_token（平台是**轮换制**，旧的随即作废）
我们手里的 JSON    :  还是旧的  ← 没有任何代码把它抄回来
```

于是 `renew_token` 拿旧 refresh_token 去续期 → 平台回"未授权" → 被判"会话真死"。
这与"续期失效几小时后自己又好"的形状吻合（页面某次刷新后我们才跟上）。

## 规则

* **只往"更新"的方向抄**：解两边 payload 的 `expires_at`，浏览器那份更大才写回；
* 60 秒节流；`ctx.cookies()` 是本地操作，不额外打网络；
* 任何异常都不影响发送。
"""
import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                    # noqa: E402

import bot                                                       # noqa: E402
from runtime import sender as S                                  # noqa: E402  P3-7：Sender 真身

BOT_PY = (ROOT / "bot.py").read_text(encoding="utf-8")


def _cookie_value(expires_at: float, tag: str = "x") -> str:
    payload = {"access_token": "a-" + tag, "refresh_token": "r-" + tag,
               "expires_at": expires_at}
    return "base64-" + base64.b64encode(
        json.dumps(payload).encode("utf-8")).decode()


class _Ctx:
    def __init__(self, value):
        self._value = value
        self.added = []

    async def cookies(self):
        return [{"name": "sb-rls-auth-token", "value": self._value,
                 "domain": ".dzmm.ai", "path": "/"}]

    async def add_cookies(self, cookies):
        self.added.append(cookies)


def _sender(monkeypatch, browser_value, stored_value, last_harvest=0.0):
    s = bot.Sender.__new__(bot.Sender)
    s.ctx = _Ctx(browser_value)
    s._last_harvest = last_harvest
    s._last_token_value = ""
    s._last_reload = 0.0
    written = []
    monkeypatch.setattr(bot.token_store, "get_cookie",
                        lambda name: {"value": stored_value} if stored_value else None,
                        raising=False)
    monkeypatch.setattr(bot.token_store, "update_cookie",
                        lambda name, val: written.append((name, val)) or True,
                        raising=False)
    monkeypatch.setattr(bot.token_store, "get_cookies_for_playwright", lambda: [],
                        raising=False)
    logged = []
    monkeypatch.setattr(S, "log", lambda m: logged.append(m), raising=False)  # P3-7
    return s, written, logged


def test_harvest_writes_back_when_browser_is_newer(monkeypatch):
    future, past = time.time() + 3600, time.time() - 60
    s, written, logged = _sender(monkeypatch, _cookie_value(future, "new"),
                                 _cookie_value(past, "old"))
    assert __import__("asyncio").run(bot.Sender._harvest_token(s)) is True
    assert written and written[0][1] == _cookie_value(future, "new")
    assert any("已抄回" in m for m in logged), logged


def test_harvest_never_overwrites_with_an_older_token(monkeypatch):
    """浏览器那份更旧/一样 ⇒ 一个字都不许动（否则会把好 token 弄坏）。"""
    import asyncio
    now = time.time() + 3600
    s, written, _ = _sender(monkeypatch, _cookie_value(now - 600, "old"),
                            _cookie_value(now, "cur"))
    assert asyncio.run(bot.Sender._harvest_token(s)) is False
    assert written == []


def test_harvest_is_throttled(monkeypatch):
    import asyncio
    future, past = time.time() + 3600, time.time() - 60
    s, written, _ = _sender(monkeypatch, _cookie_value(future), _cookie_value(past))
    s._last_harvest = time.time()          # 刚抄过
    assert asyncio.run(bot.Sender._harvest_token(s)) is False
    assert written == []
    assert bot.TOKEN_HARVEST_SEC >= 30, "节流窗口不该小于 30 秒"


def test_harvest_handles_missing_cookie(monkeypatch):
    import asyncio
    s, written, _ = _sender(monkeypatch, "", "")
    assert asyncio.run(bot.Sender._harvest_token(s)) is False
    assert written == []


def test_harvest_survives_broken_context(monkeypatch):
    import asyncio

    class _Boom:
        async def cookies(self):
            raise RuntimeError("target closed")

    s = bot.Sender.__new__(bot.Sender)
    s.ctx = _Boom()
    s._last_harvest = 0
    logged = []
    monkeypatch.setattr(S, "log", lambda m: logged.append(m), raising=False)  # P3-7
    assert asyncio.run(bot.Sender._harvest_token(s)) is False
    assert any("抄回失败" in m for m in logged), logged


def test_harvest_is_called_before_every_send(monkeypatch):
    """接线：`_refresh_token()`（每次发送前都会调）里要先抄一次。

    P3-7 起 `_refresh_token`/`_harvest_token` 随 Sender 住 runtime/sender.py。
    """
    SENDER_PY = (ROOT / "runtime" / "sender.py").read_text(encoding="utf-8")
    i = SENDER_PY.index("async def _refresh_token")
    seg = SENDER_PY[i:i + 900]
    assert "_harvest_token()" in seg, "发送前要抄一次（内部已节流）"


# ---------------------------------------------------- 2026-10-02：定时抄回（P12-BK 缺口）
#
# 现场：平台的 refresh_token 是**轮换制**，常驻页面的 SPA 自己也会刷，刷完把新的留在
# 浏览器里、我们 JSON 那份就成了旧的。而 `_harvest_token()` 原先**只在发送路径**被调 ——
# 群一安静/深夜没人说话就不发送 ⇒ 不抄回 ⇒ JSON 越来越旧 ⇒ 续期 401 ⇒ 浏览器也刷不动
# ⇒ 判"会话真死" ⇒ emergency 重启 + 5 分钟掉线。
#
# 实测代价（2026-10-01）：01:32~08:00 之间 **7 次 EMERGENCY、几乎每小时一次**，
# 其中一次共坏 **529 分钟**（8.8 小时）；浏览器续期 FAIL 24 次 / 成功 1 次；
# 抄回**只发生过 1 次**（容器启动以来）。

MAINLOOP_SRC = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")


def test_mainloop_harvests_on_a_timer_not_only_on_send():
    """主循环的 is_primary 块里必须**定时**抄回，否则安静期就成了盲区。"""
    from runtime import mainloop as M
    assert M.TOKEN_HARVEST_INTERVAL > 0, "间隔必须是正数"
    # 间隔要明显小于 token 寿命（实测约 1 小时），否则抄了也来不及续
    assert M.TOKEN_HARVEST_INTERVAL <= 1800, "间隔太长就失去意义（token 约 1 小时寿命）"
    # ⚠️ 锚点必须用**代码语句**而不是裸的 `_harvest_at`：后者先出现在声明行
    # （`_harvest_at = [0.0]`），`index()` 会命中声明、窗口里根本不含调用点。
    # 这个"锚点命中注释/声明"的坑本项目已经踩过三次（mainloop 的 `平台读消息失败`、
    # watch_bot 的 BOM、这里）。写源码断言时先确认锚点在**代码**里唯一。
    i = MAINLOOP_SRC.index(">= TOKEN_HARVEST_INTERVAL")
    seg = MAINLOOP_SRC[i:i + 600]
    assert "sender._harvest_token()" in seg, "主循环要真的调它"
    # 必须包在 try 里：抄回本身是机会主义行为，不许影响收发
    assert "except Exception" in seg, "抄回异常不能冒泡打断主循环"


def test_harvest_timer_lives_in_the_primary_block():
    """跨群/进程级的事只在主实例做 —— 抄回也是（4 个实例一起抄没意义还抢锁）。"""
    i = MAINLOOP_SRC.index("if is_primary:")
    # 主实例块的起点到"token.emergency"检测之间应该包含抄回
    seg = MAINLOOP_SRC[i:MAINLOOP_SRC.index("token.emergency")]
    assert "sender._harvest_token()" in seg, "定时抄回要放在 is_primary 块里"


def test_relogin_gate_was_shortened_after_measuring_the_floor():
    """重登门槛 5.0 -> 2.0：5.0 的代价是**固定 5 分钟掉线**（恢复时长恰等于门槛）。"""
    import renew_token as R
    assert R.RELOGIN_MIN_BAD_MIN == 2.0
    assert R.RELOGIN_MIN_BAD_MIN > 0, "不能为 0：得留一点挡瞬时抖动的余量"
