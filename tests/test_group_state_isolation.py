"""P12-AA：并行下"跨群丢更新"的回归守卫。

体检（2026-09-27）：`_run_all` 把**同一份** `state` 字典交给每个按群任务，
而主循环里 24 处 `save_state(state)` 是**整 scope 重写** —— 任务 A 手里那份
`state` 还带着群 B/C 的旧快照，写下去就把 B/C 刚更新的数据盖回旧值。

修法：`_save_group(cid, group_state)` 只 upsert 本群那一行。
这组用例钉住"再也不能退回整 scope 落盘"。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                     # noqa: E402
import state_store                                             # noqa: E402

CID_A = "aaaaaaaa-0000-0000-0000-000000000001"
CID_B = "bbbbbbbb-0000-0000-0000-000000000002"


def test_save_group_only_touches_its_own_row(isolated):
    """写 A 群不能影响 B 群 —— 整 scope 重写就会（并行下就是丢更新）。

    用 `isolated`（真 sqlite 后端）：`state` 那个内存夹具会把 save_state 换成
    no-op，测不出"谁覆盖了谁"。
    """
    state_store.save_state({CID_A: {"v": 1}, CID_B: {"v": 10}})
    # 模拟任务 A：手里那份 state 是**旧快照**（B 群还是旧值）
    stale_map = state_store.load_state()
    stale_map[CID_B] = {"v": 10}
    stale_map[CID_A] = {"v": 2}
    # B 群此刻被另一个任务更新成 99
    state_store.save_state({CID_B: {"v": 99}})
    # 任务 A 落盘（只写自己）
    bot._save_group(CID_A, stale_map[CID_A])
    assert state_store.group_get(CID_A, "v") == 2
    assert state_store.group_get(CID_B, "v") == 99, "B 群的新数据被覆盖了！"


def test_whole_scope_save_would_have_clobbered():
    """对照：旧的整 scope 写法**确实**会覆盖（证明这条回归有意义）。"""
    store = {}
    real_load, real_save = state_store.load_state, state_store.save_state
    try:
        state_store.load_state = lambda: {k: dict(v) for k, v in store.items()}
        state_store.save_state = lambda s: store.update(
            {k: dict(v) for k, v in s.items()})
        state_store.save_state({CID_A: {"v": 1}, CID_B: {"v": 10}})
        stale = state_store.load_state()             # 任务 A 的旧快照
        state_store.save_state({CID_B: {"v": 99}})   # 别的任务更新了 B
        stale[CID_A] = {"v": 2}
        state_store.save_state(stale)                # 旧的整 scope 落盘
        assert store[CID_B]["v"] == 10, "旧写法就该覆盖（对照用）"
    finally:
        state_store.load_state, state_store.save_state = real_load, real_save


def test_bot_never_saves_the_whole_scope_from_the_loop():
    """源码级：主循环里不许再出现 `save_state(state)`（注释除外）。"""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    hits = []
    for i, ln in enumerate(src.splitlines(), 1):
        if "save_state(state)" not in ln:
            continue
        if ln.lstrip().startswith("#") or "：" in ln or "`" in ln:
            continue                              # 注释/文档里的说明
        hits.append("{}: {}".format(i, ln.strip()))
    assert not hits, "主循环里又出现整 scope 落盘：{}".format(hits)
    # P3-3-3 起房间块（含其 5 处本群落盘）在 runtime/room.py，两边合起来数；
    # P3-8/P3-9 起 roast/climax 也持有本群落盘调用点，一并计入；
    # P3-10 起补筛在 runtime/roster.py、P3-14 起 inflight 族在 runtime/inflight.py
    # —— 注入参数无下划线，按 "save_group(cid," 计
    src_r = (ROOT / "runtime" / "room.py").read_text(encoding="utf-8")
    extra = sum(
        (ROOT / "runtime" / m).read_text(encoding="utf-8").count("_save_group(cid,")
        for m in ("roast.py", "climax.py"))
    injected = sum(
        (ROOT / "runtime" / m).read_text(encoding="utf-8").count("save_group(cid,")
        for m in ("roster.py", "inflight.py"))
    # P3-19 起主循环体在 runtime/mainloop.py（bot 桥 B 前缀，_save_group(cid, 原样）
    mainloop_n = (ROOT / "runtime" / "mainloop.py").read_text(
        encoding="utf-8").count("_save_group(cid,")
    assert src.count("_save_group(cid,") + src_r.count("_save_group(cid,") \
        + extra + injected + mainloop_n >= 20, "本群落盘的调用点不该少"
