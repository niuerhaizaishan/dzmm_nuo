"""骚度周趋势（P8 第 4 步从 bot.py 搬出）。

`_trend_chong` 要用运势的干支冲合（`_fortune_calc`），所以它排在 `fortune`
之后，从 `runtime.fortune` 拿 —— 这也是拆分顺序的由来：**先搬被依赖的**。
"""
import asyncio
import re
from datetime import datetime, timedelta

from brains import generate_trend
from personas import me as _pme, p as _pp          # 自称 + 按群人设文案（小小糯/薇丝）
from profiles import get_profile
from runtime.ctx import PRIMARY_CID
from runtime.fortune import _fortune_calc
from runtime.identity import get_full_name, get_name
from runtime.obs import log
import state_store
from runtime.tuning import (get as _tune, in_scope as _tune_scope)  # P12-B：可调参数 + 按群集合

__all__ = [
    "is_trend_cmd", "is_trend_board_cmd", "_trend_days", "_trend_spark",
    "_trend_chong", "_trend_stats", "_wd_cn", "_md_cn", "week_trend",
    "week_trend_board",
]


TREND_CIDS = {PRIMARY_CID}      # 只在认证妹子群开放（那边才有 fortune_history）


TREND_DEFAULT_DAYS = 7


TREND_MAX_DAYS = 30             # 历史只从 2026-09-04 开始，再长也没数据


def is_trend_cmd(cmd: str):
    """识别趋势指令，返回天数；None = 不是."""
    for p in ["/周运", "/过去一周", "/最近七天", "/周趋势", "/运势趋势", "/走势",
              "/过去7天", "/过去"]:
        if cmd.startswith(p):
            rest = cmd[len(p):].strip()
            if rest.startswith("榜"):   # /周运榜 归榜单分支，不算趋势
                return None
            m = re.search(r"(\d+)", rest)
            n = int(m.group(1)) if m else TREND_DEFAULT_DAYS
            if "半" in rest:
                n = 15
            elif "月" in rest:
                n = 30
            return max(1, min(n, TREND_MAX_DAYS))
    return None


def is_trend_board_cmd(cmd: str) -> bool:
    """识别「群运势榜」指令（必须放在 is_rank_cmd 之前判断，否则会被"排行"吃掉）."""
    return any(k in cmd for k in ("周运榜", "运势排行", "运势榜", "谁运势最好", "谁运气最好"))


def _trend_days(cid: str, uid: str, n: int):
    """取某人最近 n 天的真实运势记录 → [(date_str, score|None)]（从旧到新，缺的为 None）."""
    hist = ((state_store.load_state().get(cid) or {}).get("fortune_history") or {}).get(uid) or {}
    today = datetime.now()
    out = []
    for i in range(n - 1, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        sc = hist.get(d)
        out.append((d, int(sc) if isinstance(sc, (int, float)) else None))
    return out


def _trend_spark(scores: list) -> str:
    """迷你走势条：分数 → 方块字符（缺记录用 ·）."""
    blocks = "▁▂▃▄▅▆▇█"
    cells = []
    for sc in scores:
        cells.append("·" if sc is None else blocks[max(0, min(7, sc // 13))])
    return "".join(cells)


def _trend_chong(uid: str, dates: list) -> list:
    """这几天里哪些是「日冲本命」（日期可确定性推算，所以补不出来也能标）."""
    out = []
    try:
        p = get_profile(uid) or {}
        full = p.get("name") or get_full_name(uid)
        bio = str(p.get("bio") or "")
        for d in dates:
            try:
                info = _fortune_calc(uid, full, d, bio)
                if info.get("chong_ben"):
                    out.append(d)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _trend_stats(pairs: list):
    """(日期, 分数) → 统计量（只按有记录的天算）."""
    have = [(d, s) for d, s in pairs if s is not None]
    st = {"n": len(have), "total": len(pairs)}
    if not have:
        return st
    st["avg"] = round(sum(s for _, s in have) / float(len(have)), 1)
    hi = max(have, key=lambda kv: kv[1])
    lo = min(have, key=lambda kv: kv[1])
    st["hi"], st["hi_d"] = hi[1], hi[0]
    st["lo"], st["lo_d"] = lo[1], lo[0]
    # 最长连跌（只看有记录的天，按时间顺序）
    run = best = 0
    best_span = None
    cur_start = None
    for i in range(1, len(have)):
        if have[i][1] < have[i - 1][1]:
            if run == 0:
                cur_start = have[i - 1][0]
            run += 1
            if run > best:
                best, best_span = run, (cur_start, have[i][0])
        else:
            run = 0
    st["drop"] = best
    st["drop_span"] = best_span
    return st


def _wd_cn(date_str: str) -> str:
    """日期 → 周几（中文）."""
    try:
        w = datetime.strptime(date_str, "%Y-%m-%d").weekday()
        return "一二三四五六日"[w]
    except Exception:
        return "?"


def _md_cn(date_str: str) -> str:
    return date_str[5:].replace("-", "/")


async def week_trend(cid: str, uid: str, days: int = TREND_DEFAULT_DAYS) -> str:
    """过去 N 天的真实运势趋势卡（只显示真实记录，没算的留空）."""
    if not _tune_scope("trend", cid):
        return ""
    pairs = _trend_days(cid, uid, days)
    st = _trend_stats(pairs)
    name = get_name(uid)
    if st["n"] == 0:
        _me = _pme(cid)
        return (_pp("trend_empty", cid, days=days) or (
            "（翻了翻小本子）过去 " + str(days) + " 天你一次都没找" + _me + "算过运势呢～"
            "发 /算命 算一次，" + _me + "就开始给你记了哦～"))
    scores = [s for _, s in pairs]
    # 上周同期（用于对比）
    prev = _trend_days(cid, uid, days * 2)[:days]
    pst = _trend_stats(prev)
    lines = ["📈 " + name + " · 过去 " + str(days) + " 天运势（"
             + _md_cn(pairs[0][0]) + " ~ " + _md_cn(pairs[-1][0]) + "）",
             "日期  " + " ".join(_wd_cn(d).rjust(2) for d, _ in pairs),
             "分数  " + " ".join(("—" if s is None else str(s)).rjust(2) for _, s in pairs),
             "走势  " + " ".join(("·" if s is None else "▁▂▃▄▅▆▇█"[max(0, min(7, s // 13))])
                                 .rjust(2) for _, s in pairs),
             "有记录 " + str(st["n"]) + "/" + str(st["total"]) + " 天 · "
             + (("唯一记录 " + str(st["hi"]) + "(" + _wd_cn(st["hi_d"]) + ")")
                if st["n"] == 1 else
                ("均值 " + str(st["avg"]) + " · 最高 " + str(st["hi"])
                 + "(" + _wd_cn(st["hi_d"]) + ")"
                 + " · 最低 " + str(st["lo"]) + "(" + _wd_cn(st["lo_d"]) + ")"))]
    if pst.get("n"):
        diff = round(st["avg"] - pst["avg"], 1)
        arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "=")
        cmp_txt = ("环比上期 " + str(pst["avg"]) + "（" + arrow + str(abs(diff)) + "）")
    else:
        cmp_txt = "上期没记录，没法对比"
    if st.get("drop"):
        sp = st["drop_span"]
        cmp_txt += (" · 最长连跌 " + str(st["drop"]) + " 天（"
                    + _wd_cn(sp[0]) + "→" + _wd_cn(sp[1]) + "）" if sp else "")
    lines.append(cmp_txt)
    chongs = _trend_chong(uid, [d for d, s in pairs if s is not None])
    if chongs:
        lines.append("⚠️ 凶日（日冲本命）：" + "、".join(_wd_cn(d) for d in chongs))
    lines.append("————————————")
    data_txt = ("统计窗口：" + pairs[0][0] + " ~ " + pairs[-1][0] + "\n"
                + "每天分数（— = 那天没算）："
                + "、".join(_wd_cn(d) + " " + ("没算" if s is None else str(s))
                            for d, s in pairs) + "\n"
                + "有记录 " + str(st["n"]) + "/" + str(st["total"]) + " 天，均值 " + str(st["avg"])
                + "，最高 " + str(st["hi"]) + "（周" + _wd_cn(st["hi_d"]) + "）"
                + "，最低 " + str(st["lo"]) + "（周" + _wd_cn(st["lo_d"]) + "）\n"
                + "上期均值：" + (str(pst.get("avg")) if pst.get("n") else "无记录") + "\n"
                + "最长连跌：" + (str(st.get("drop")) + " 天" if st.get("drop") else "无") + "\n"
                + "日冲本命的日子：" + ("、".join(_wd_cn(d) for d in chongs) if chongs else "无"))
    try:
        para = await asyncio.to_thread(generate_trend, data_txt, name)
    except Exception as e:
        log("趋势点评生成失败: " + str(e)[:60])
        para = ""
    para = " ".join(str(para or "").split())
    if not para:
        para = "（翻了翻小本子）这 " + str(days) + " 天里你有记录的有 " + str(st["n"]) + " 天，均值 " + str(st["avg"]) + "～"
    lines.append(para)
    log("📈 [" + cid[:8] + "] 趋势: " + name[:12] + " " + str(days) + " 天，有记录 "
        + str(st["n"]) + " 天，均值 " + str(st["avg"]))
    return "\n".join(lines[:10])


async def week_trend_board(cid: str, days: int = TREND_DEFAULT_DAYS) -> str:
    """群运势榜：过去 N 天谁的平均分最高（只统计有记录的天，至少 2 天才上榜）."""
    if not _tune_scope("trend", cid):
        return ""
    fh = (state_store.load_state().get(cid) or {}).get("fortune_history") or {}
    today = datetime.now()
    win = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    rows = []
    for uid, uh in fh.items():
        sc = [int(uh[d]) for d in win if isinstance(uh.get(d), (int, float))]
        if len(sc) >= 2:
            rows.append((round(sum(sc) / float(len(sc)), 1), get_name(uid), len(sc), max(sc)))
    if not rows:
        return "过去 " + str(days) + " 天还没有人有 2 天以上的记录呢～多算几次就能上榜啦～"
    rows.sort(reverse=True)
    lines = ["🏆 群运势榜 · 过去 " + str(days) + " 天（按平均分）"]
    for i, (avg, nm, n, hi) in enumerate(rows[:8], 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, str(i) + ".")
        lines.append(medal + " " + nm + " · 均值 " + str(avg) + "（算过 " + str(n) + "/"
                     + str(days) + " 天，最高 " + str(hi) + "）")
    lines.append("（至少算过 2 天才上榜）")
    return "\n".join(lines[:10])
