# -*- coding: utf-8 -*-
"""P12-I：按群并行（`DZMM_PARALLEL_GROUPS=on`）。

背景：主循环是 `while True → for cid in GROUPS → await 生成`，**一个群的长任务
（发骚/写诗/星象/回顾/入群筛查翻页）会把另一个群一起按住**。实测（平台时间戳）：
12 小时里 >10 秒的占 33%，最慢 76 秒，而且**同一个 76 秒同时出现在两个群**上。

做法（不去搬那 900 行循环体）：把 `main_loop` 参数化成"只管一个群"，
`_run_all` 建**唯一一份**共享资源后，按群起 N 个实例：
  · sender      —— 共用一个常驻页面（1.8G 主机跑不了两个 Playwright）
  · state       —— 共用同一份 dict（各自 load 会互相覆盖）
  · subscriber  —— 共用一个订阅器（按群分队列）
主实例（`is_primary`）额外负责跨群/进程级工作（热加载、验证码恢复、每日档案扫描、
定时问候、骰子超时），否则会被跑 N 遍。

本文件钉住四件事：① 当前群是**上下文局部**；② 参数化后的 main_loop 只碰自己的群；
③ 全局块只在主实例跑；④ 开关默认关（= 老行为）。
"""
import asyncio
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot                     # noqa: E402
import runtime.ctx as ctx      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
BD = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


# ---------------------------------------------------------------- ① 上下文局部

def test_active_cid_is_context_local():
    """两个并发任务各自设置"我在处理哪个群"，互不覆盖（这是并行的前提）。"""
    seen = {}

    async def worker(cid, tag):
        bot._set_active_cid(cid)
        await asyncio.sleep(0.01)          # 让另一个任务插进来设置它自己的
        seen[tag] = (ctx.get_active_cid(), bot._active_cid())

    async def main():
        await asyncio.gather(worker(BD, "a"), worker(ROSE, "b"))

    asyncio.run(main())
    assert seen["a"] == (BD, BD), "认证群任务读到了别人的 cid：{}".format(seen["a"])
    assert seen["b"] == (ROSE, ROSE), "玫瑰房任务读到了别人的 cid：{}".format(seen["b"])


def test_active_cid_reader_goes_through_ctx():
    """`_active_cid()` 必须读 contextvars 真源，而不是那个遗留镜像。"""
    ctx.reset_active_cid(ROSE)
    try:
        assert bot._active_cid() == ROSE
    finally:
        ctx.reset_active_cid(BD)


def test_bot_no_longer_reads_the_global_mirror():
    """读点不许再直接读模块全局 `_ACTIVE_CID`（并行后它会被别的群覆盖）。

    只允许三处：定义、setter 里的赋值、`_active_cid()` 里的兜底。
    """
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    bad = []
    for i, ln in enumerate(src.splitlines(), 1):
        if "_ACTIVE_CID" not in ln:
            continue
        s = ln.strip()
        if s.startswith("#") or s.startswith("_ACTIVE_CID =") \
                or s.startswith("global ") or s.startswith("def _set_active_cid") \
                or "or _ACTIVE_CID" in s or "`_ACTIVE_CID`" in s \
                or "镜像" in s or "遗留" in s:
            continue
        bad.append("{}: {}".format(i, s[:70]))
    assert not bad, "还有直接读全局镜像的地方：\n" + "\n".join(bad)


# ---------------------------------------------------------------- ②/③ 参数化

def test_main_loop_takes_a_group_subset_and_shared_resources():
    sig = inspect.signature(bot.main_loop)
    for name in ("groups", "sender", "state", "subscriber", "is_primary"):
        assert name in sig.parameters, name
    # 共享资源不给默认值 → 不可能"忘了传、于是各自建一份"
    for name in ("sender", "state", "subscriber"):
        assert sig.parameters[name].default is inspect.Parameter.empty, name


def test_run_all_shares_one_sender_and_one_state():
    """`_run_all` 必须**只建一份** sender / state / subscriber（并行时才成立）。"""
    src = inspect.getsource(bot._run_all)
    assert src.count("_wrap_with_outbox(") == 1
    assert src.count("load_state()") == 1
    assert src.count("SioSubscriber(") == 1
    assert "state=state" in src and "sender=sender" in src


def test_global_blocks_are_primary_only():
    """跨群/进程级的块必须在 `is_primary` 里（否则被跑 N 遍）。"""
    src = inspect.getsource(bot.main_loop)
    for marker in ("hot_reload_check()", "_daily_profile_scan",
                   "_scheduled_pushes(sender, dry)", "_dice_current_player(_dg)"):
        idx = src.index(marker)
        # 往前找最近的 if is_primary / 该行的条件
        line_start = src.rfind("\n", 0, idx) + 1
        cond = src.rfind("is_primary", max(0, idx - 400), idx)
        assert cond != -1, "{} 没被 is_primary 包住".format(marker)
        assert line_start > cond or "is_primary" in src[line_start:idx + 1], marker


def test_parallel_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("DZMM_PARALLEL_GROUPS", raising=False)
    assert bot._parallel_groups_on() is False
    for on in ("on", "1", "true", "YES", " y "):
        monkeypatch.setenv("DZMM_PARALLEL_GROUPS", on)
        assert bot._parallel_groups_on() is True
    monkeypatch.setenv("DZMM_PARALLEL_GROUPS", "off")
    assert bot._parallel_groups_on() is False


def test_sequential_path_is_still_one_loop(monkeypatch):
    """开关关着时，仍然只起一个实例、管全部群（= P12-I 之前的行为）。"""
    src = inspect.getsource(bot._run_all)
    assert "groups=GROUPS" in src and "is_primary=True" in src
