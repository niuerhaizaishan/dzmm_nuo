"""P12-AA：事件循环不许被阻塞（2026-09-27 体检的 8 处里最要紧的几处）。

体检结论：主循环是单线程 asyncio，任何"同步 HTTP / 读 40 个文件 / 调模型"
跑在上面都会把**那个群**这一轮完全卡住（实测表现："发指令十几秒没反应"）。
这组用例钉住三件事：

1. `identity.get_full_name` 在事件循环上**不发 HTTP**（退化取档案名 + 记待补），
   在工作线程里行为不变；
2. `_handle_climax`（平台读 + 模型）整段在工作线程里跑；
3. `hot_reload_check` 是协程，且真的把 `maybe_reload` 丢进线程。
"""
import asyncio
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                     # noqa: E402
from runtime import identity as ident                          # noqa: E402

MAIN = threading.main_thread()


# ---------------------------------------------------------------- 1. 昵称

def test_get_full_name_never_does_http_on_the_event_loop(monkeypatch):
    """事件循环上缓存未命中：不发 HTTP，退化取档案名，并记进待补集合。"""
    ident._name_cache.clear()
    ident._pending_names.clear()
    calls = []

    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("事件循环上不该发 HTTP！")

    monkeypatch.setattr(ident, "get_user", _boom)
    monkeypatch.setattr(ident, "get_profile", lambda uid: {"name": "档案里的名字"})

    async def run():
        # 事件循环上调用（async 函数里直接调 get_full_name 就是这种情形）
        return ident.get_full_name("uid-new")

    assert asyncio.run(run()) == "档案里的名字"
    assert calls == [], "事件循环上发了 HTTP"
    assert "uid-new" in ident._pending_names, "要记下来等后台预载补真名"
    assert "uid-new" not in ident._name_cache, "别把降级值固化进缓存"


def test_get_full_name_falls_back_to_uid_prefix_without_profile(monkeypatch):
    ident._name_cache.clear()
    ident._pending_names.clear()
    monkeypatch.setattr(ident, "get_user", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("不该发 HTTP")))
    monkeypatch.setattr(ident, "get_profile", lambda uid: None)
    assert asyncio.run(_call_in_loop("uid-abcdef")) == "uid-abcd"


async def _call_in_loop(uid):
    return ident.get_full_name(uid)


def test_get_full_name_still_fetches_off_the_event_loop(monkeypatch):
    """工作线程（没有事件循环）里行为不变：该发就发、发完就缓存。"""
    ident._name_cache.clear()
    ident._pending_names.clear()
    monkeypatch.setattr(ident, "get_user", lambda cid, uid: {"fullName": "平台真名"})
    out = {}
    t = threading.Thread(target=lambda: out.update(n=ident.get_full_name("uid-x")))
    t.start()
    t.join()
    assert out["n"] == "平台真名"
    assert ident._name_cache["uid-x"] == "平台真名"
    assert "uid-x" not in ident._pending_names


def test_member_preload_clears_pending(monkeypatch):
    ident._name_cache.clear()
    ident._pending_names.clear()
    ident._pending_names.add("uid-y")
    monkeypatch.setattr(ident, "get_members", lambda cid: {"uid-y": "补上的真名"})
    ident._load_member_names("cid-1")
    assert ident._name_cache["uid-y"] == "补上的真名"
    assert "uid-y" not in ident._pending_names


def test_loop_preloads_member_names_in_a_worker_thread():
    """主循环里要有"每 10 分钟在工作线程里批量刷昵称"的那一段。"""
    # P3-19 起主循环体在 runtime/mainloop.py（bot 桥 B 前缀，子串兼容）
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    assert "_load_member_names, cid" in src, "没接预载"
    i = src.index("_load_member_names, cid")
    assert "asyncio.to_thread(" in src[max(0, i - 120):i], "预载必须走 to_thread"


# ---------------------------------------------------------------- 2. 高潮事件

def test_handle_climax_generates_off_the_loop(monkeypatch):
    """`_handle_climax` 的生成段（平台读 + 模型）必须在工作线程里。"""
    seen = {}

    def fake_build(cid, hits, gs):
        seen["thread"] = threading.current_thread()
        return []

    monkeypatch.setattr(bot, "_build_climax_texts", fake_build)
    monkeypatch.setattr(bot, "load_state", lambda: {"c1": {}})

    class _S:
        async def send(self, *a, **kw):
            return True

    asyncio.run(bot._handle_climax(_S(), "c1", True, []))
    assert seen.get("thread") is not None, "没调到生成段"
    assert seen["thread"] is not MAIN, "生成段还在事件循环线程上！"


def test_build_climax_texts_is_plain_sync():
    """生成段必须是**同步函数**（否则没法整体 to_thread）。"""
    import inspect
    assert not inspect.iscoroutinefunction(bot._build_climax_texts)


# ---------------------------------------------------------------- 3. 热加载

def test_hot_reload_check_is_async_and_offloads(monkeypatch):
    import inspect
    assert inspect.iscoroutinefunction(bot.hot_reload_check)
    from runtime import hotload
    seen = {}

    def fake_maybe_reload():
        seen["thread"] = threading.current_thread()
        return None

    monkeypatch.setattr(hotload, "maybe_reload", fake_maybe_reload)
    asyncio.run(bot.hot_reload_check())
    assert seen["thread"] is not MAIN, "热加载还在事件循环线程上跑"


# ---------------------------------------------------------------- 4. 回归守卫

def test_no_blocking_calls_left_in_async_handlers():
    """源码级守卫：体检点名的这几处不许再直接同步调用。

    注意 `_build_climax_context` 现在是在**同步助手** `_build_climax_texts` 里调的，
    那个助手整体走 `to_thread`（上面有行为用例钉住），所以这里不禁止它出现。
    """
    # P3-19：主循环体搬进 runtime/mainloop.py；高潮生成段仍在 bot.py 的
    # `_handle_climax`（P3-9 留守）—— 两边都检查。
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    src_ml = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    assert "= _retrieve_memory(" not in src, "记忆检索又在事件循环里同步调了"
    assert "= _retrieve_memory(" not in src_ml, "记忆检索又在事件循环里同步调了"
    i = src.index("items = await asyncio.to_thread")
    assert "_build_climax_texts" in src[i:i + 120], "高潮生成段没走 to_thread"
    # P3-3-4 起 @ 分支的记忆检索搬进了 runtime/at_chat.py —— 一样必须 to_thread
    src_a = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    assert "= _retrieve_memory(" not in src_a, "记忆检索又在事件循环里同步调了"
    assert "= await asyncio.to_thread(\n" in src_a and "_retrieve_memory, cid, q," in src_a
    perf = (ROOT / "runtime" / "perf.py").read_text(encoding="utf-8")
    # 写盘只允许出现在同步助手 `_write_novel` 里，且调用点必须走 to_thread
    assert perf.count('with open(path, "w"') == 1, "写盘点变多了，检查是不是又回到 async 函数里"
    i = perf.index('with open(path, "w"')
    assert "def _write_novel" in perf[max(0, i - 200):i], "写盘必须关在同步助手里"
    assert "await asyncio.to_thread(_write_novel" in perf
    assert "to_thread(\n        send_weixin" in perf, "perf 的告警也要走 to_thread"
