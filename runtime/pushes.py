"""定时问候（早/中/晚）+ 每日记忆汇报（P3-11 从 bot.py 拆出）。

相比早期 entrypoint「到点另起进程 → 冷启浏览器」：主循环里用主 sender 发
（页面常驻秒发），带 90 分钟补发窗口，每群独立打点（state[cid]["last_greet_xxx"]），
重启/部署不会重复发。午间顺带发每日记忆汇报（memory_report 闸门）。

**不持有 bot 侧状态**：群清单（push_cids）、状态读写（load_state/save_group）、
功能开关唯一入口（_feat_on —— bot 的包装器带"log+放行"回退，行为不能绕开）
全部 kw-only 注入 → monkeypatch `bot.PUSH_CIDS` / `bot.load_state` /
`bot._feat_on` 的既有接缝保持不变。generate_greeting / _tune /
_publish_memory_report / datetime 是叶子工具，本模块直接 import
（test_feature_registry 的行为测试相应 patch 本模块）。
⚠️ 闸门注入参数**必须沿用 `_feat_on` 名**：test_feature_registry 的静态扫描
只认 `_feat_on(` 字面量调用，换个名字 memory_report 就会"被判定未接线"。

P12-AA：这一支会**同时**给多个群打点，而它跑在主循环任务里 —— 整 scope 落盘
会用旧快照盖掉别的群这一轮的新数据，所以逐个群只写自己那一行（save_group）。
"""
import asyncio
from datetime import datetime

from brains import generate_greeting
from personas import set_cid as set_persona_cid
from runtime.climax import _publish_memory_report
from runtime.obs import log
from runtime.tuning import get as _tune


__all__ = ["scheduled_pushes"]


# 三个问候的功能开关 key：`period` 是拼出来的，**必须在这里显式登记** ——
# 否则静态扫描（tests/test_feature_registry.py）看不见它，而"_feat_on 遇到
# 不认识的 key 只记日志 + 放行"会把它变成一次静默失效。
_GREET_FEATURES = {"morning": "greet_morning", "noon": "greet_noon",
                   "evening": "greet_evening"}


async def scheduled_pushes(sender, dry: bool, *, push_cids,
                           load_state, save_group, _feat_on) -> None:
    """定时问候（早/中/晚）+ 记忆汇报：用主 sender 发（页面常驻，秒发）。

    相比原来的 entrypoint「到点另起进程 → 冷启浏览器」：
    - 不再有 30 秒整页加载超时的风险（认证群页面本来就常驻）
    - 带补发窗口：机器人在到点后 90 分钟内恢复，仍然会补发（原来错过就永远没了）
    - 每群独立打点（state[cid]["last_greet_xxx"]），重启/部署不会重复发
    """
    if dry:
        return
    set_persona_cid("")  # 广播文案（问候/记忆汇报）一律用默认人设，别串到房间群管家口吻
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    cur = now.hour * 60 + now.minute
    st = load_state()
    for period, hour in (("morning", 8), ("noon", 12), ("evening", 22)):
        # 窗口：[整点, 整点+90 分钟]；超出窗口就不补了（避免半夜恢复时补发早安）
        if not (hour * 60 <= cur <= hour * 60 + int(_tune("schedule.greet_window_min"))):
            continue
        # P12-A 闸门：三个问候各自可关（按群由 push_cids 决定，这里是功能级开关）。
        # 注意 todo 要按**群**过滤（同一个问候在不同群可以有不同开关）。
        key = "last_greet_" + period
        todo = [c for c in push_cids
                if (st.get(c) or {}).get(key) != today
                and _feat_on(c, _GREET_FEATURES[period])]
        if not todo:
            continue
        try:
            greeting = (await asyncio.to_thread(generate_greeting, period, today) or "").strip()
        except Exception as e:
            log("问候生成失败(" + period + "): " + str(e)[:60])
            greeting = ""
        if not greeting:
            greeting = "姐姐们好呀～"
        for c in todo:
            ok = await sender.send("🤖 " + greeting, c)
            if ok:
                gs = st.get(c) or {}
                gs[key] = today
                st[c] = gs
                # P12-AA：这一支会**同时**给多个群打点，而它跑在主循环任务里 ——
                # 整 scope 落盘会用这份旧快照盖掉别的群这一轮的新数据，
                # 所以逐个群只写自己那一行。
                save_group(c, gs)
            log("greet[" + period + "] " + c[:8] + ": " + ("✅" if ok else "❌"))
        if period == "noon":
            # 午间顺带发每日记忆汇报
            for c in push_cids:
                if (st.get(c) or {}).get("last_memreport") == today:
                    continue
                if not _feat_on(c, "memory_report"):
                    continue
                try:
                    await _publish_memory_report(sender, c, dry)
                except Exception as e:
                    log("记忆汇报失败: " + str(e)[:60])
                gs = st.get(c) or {}
                gs["last_memreport"] = today
                st[c] = gs
                save_group(c, gs)
