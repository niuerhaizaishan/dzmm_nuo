"""骚度排行（P3-12 从 bot.py 拆出）。

纯读 helper：按「今日高潮数 > 总高潮数 > 发情值 > 活跃度」排序（活跃度只展示
不参与排名，排除 bot 自己）。`top` 只列前 N 名 —— 认证群 65 人全列出来是
2000+ 字 / 66 行的刷屏长消息（屋主 2026-09-27："只展示前 10 名就可以"）。

**不持有 bot 侧状态**：load_state kw-only 注入（conftest.state 夹具
patch `bot.load_state` 的接缝保持不变）；当前群 cid **直读 runtime/ctx 真源**
（contextvars —— 按群并行下 bot 镜像本就不可靠，与 3-9 的 `_active_cid` 同理）；
昵称族（self_uids / _load_member_names / get_name）是 runtime.identity 叶子
工具，直接 import。
"""
from runtime import ctx
from runtime.identity import _load_member_names, get_name, self_uids
from runtime.obs import log
from runtime.tuning import get as _tune


__all__ = ["build_rank_summary", "rank_hour_set", "publish_rank"]


def build_rank_summary(top: int = 10, *, load_state) -> str:
    """生成骚度排行（高潮次数为主，发情值为辅，活跃度不参与排名，排除小小糯）。

    `top`：只列前 N 名（屋主 2026-09-27："发骚排行只展示前 10 名就可以"）。
    认证群 65 个人全列出来是 2000+ 字 / 66 行的刷屏长消息，没必要。
    """
    cid = ctx.get_active_cid()
    group_state = (load_state() or {}).get(cid) or {}
    heat = group_state.get("heat_value") or {}
    counts = group_state.get("msg_count") or {}
    climax = group_state.get("heat_climax") or {}
    today_climax = group_state.get("heat_climax_today") or {}
    uids = set(heat) | set(counts) | set(climax)
    uids.difference_update(self_uids())
    if not uids:
        return ""
    _load_member_names()
    items = sorted(uids, key=lambda u: (-today_climax.get(u, 0), -climax.get(u, 0), -heat.get(u, 0), -counts.get(u, 0)))
    total = len(items)
    shown = items[:max(1, int(top))]
    lines = ["🔥 骚度排行"]
    for i, uid in enumerate(shown, 1):
        name = get_name(uid)  # 简化名字（去掉括号/波浪号后缀）
        h = heat.get(uid, 0)
        c = counts.get(uid, 0)
        cl = climax.get(uid, 0)
        ct = today_climax.get(uid, 0)
        line = name + " · 高潮今" + str(ct) + "/总" + str(cl) + " · 发情" + str(h) + " · 活跃" + str(c)
        if i == 1:
            lines.append("🥇 " + line)
        elif i == 2:
            lines.append("🥈 " + line)
        elif i == 3:
            lines.append("🥉 " + line)
        else:
            lines.append(str(i) + ". " + line)
    if total > len(shown):
        lines.append("…（共 " + str(total) + " 人有记录，只列前 " + str(len(shown)) + " 名）")
    return "\n".join(lines)


def rank_hour_set():
    """定时排行发布的整点（默认 12、18；`schedule.rank_hours` 可配，逗号分隔）.

    配错了（空/非法）就退回默认的 12、18 —— 定时任务不该因为一个笔误彻底不发。
    """
    raw = str(_tune("schedule.rank_hours") or "")
    out = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            out.add(int(part))
    return out or {12, 18}


async def publish_rank(sender, cid: str, dry: bool, *, rank_summary):
    """发布骚度排行."""
    rank = rank_summary()
    if not rank:
        return
    if dry:
        log("[dry] " + rank[:60])
    else:
        ok = await sender.send("🤖 " + rank, cid)
        log("✅" if ok else "❌")
