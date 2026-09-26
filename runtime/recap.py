"""缺勤复盘（P8 第 4 步从 bot.py 搬出）。

"我不在的时候群里发生了什么" —— 按时间窗回放消息、点名被 @ 的人和缺勤的人。
只在认证妹子群开放（`RECAP_CIDS`），且有冷却（同一时间窗重复问会被挡）。
"""
import asyncio
import re
import time
from datetime import datetime, timedelta

from brains import generate_recap
from dzmm import get_messages_since
from personas import me as _pme, p as _pp          # 自称 + 按群人设文案（小小糯/薇丝）
from runtime.ctx import PRIMARY_CID
from runtime.identity import _load_member_names, get_full_name, get_name, is_self
from runtime.obs import log
import state_store
from runtime.tuning import (get as _tune, in_scope as _tune_scope)  # P12-B：可调参数 + 按群集合

__all__ = [
    "is_recap_cmd", "_recap_intent", "_recap_window", "_day0", "_msg_ts",
    "_fmt_ts", "_recap_mentions", "_recap_transcript", "recap_absent",
]


RECAP_CIDS = {PRIMARY_CID}          # 只在认证妹子群开放


RECAP_MAX_MSGS = 400                # 窗口内最多取多少条喂给 AI（防刷屏撑爆）


RECAP_MAX_HOURS = 48                # 窗口最长 48 小时


RECAP_COOLDOWN = 30                 # 同一时间窗重复问的冷却（换个时间段问不受限）


def is_recap_cmd(cmd: str):
    """识别 /我不在 /补课 /错过 指令，返回窗口描述（可为空串）；None = 不是."""
    for p in ["/我不在", "/补课", "/错过了什么", "/错过"]:
        if cmd.startswith(p):
            return cmd[len(p):].strip()
    return None


def _recap_intent(text: str) -> bool:
    """自然语言里是不是在问"我不在的时候发生了什么"（配合 @小小糯 用）."""
    t = text or ""
    if "我不在" in t or "错过" in t:
        return True
    if "发生" in t and any(k in t for k in ("什么", "啥")):
        return True
    if any(k in t for k in ("有啥事", "有什么瓜", "有瓜吗", "群里咋了", "群里怎么了")):
        return True
    return False


def _recap_window(cid: str, uid: str, text: str):
    """算出回顾起点，返回 (起点时间戳, 人类可读窗口描述)."""
    now = time.time()
    t = text or ""
    # 手动指定（支持阿拉伯数字和中文数字：3小时 / 两小时 / 半天）
    cn = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}
    m = re.search(r"(\d+)\s*(小时|钟头)", t)
    if m:
        h = min(int(m.group(1)), int(_tune("recap.max_hours", cid)))
        return (now - h * 3600, now, "最近 " + str(h) + " 小时")
    m = re.search(r"([一两二三四五六七八九十])\s*(小时|钟头)", t)
    if m:
        h = min(cn.get(m.group(1), 1), int(_tune("recap.max_hours", cid)))
        return (now - h * 3600, now, "最近 " + str(h) + " 小时")
    if "半天" in t:
        return (now - 12 * 3600, now, "最近半天")
    if "一天" in t or "24小时" in t:
        return (now - 24 * 3600, now, "最近一天")
    m = re.search(r"([一两二三四五六七八九十\d]+)\s*天(前|以前)", t)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else cn.get(raw, 1)
        d0 = _day0(datetime.now() - timedelta(days=n))
        return (d0, d0 + 86400, str(n) + " 天前")
    if "大前天" in t:
        d0 = _day0(datetime.now() - timedelta(days=3))
        return (d0, d0 + 86400, "大前天")
    if "前天" in t:
        d0 = _day0(datetime.now() - timedelta(days=2))
        return (d0, d0 + 86400, "前天")
    if "前几天" in t:
        return (_day0(datetime.now() - timedelta(days=3)), now, "前几天")
    # 周末（必须放在"上周"之前判断，否则"上周末"会被"上周"先吃掉）
    if any(k in t for k in ("周末", "礼拜六", "礼拜天", "礼拜日")):
        d = datetime.now()
        back = (d.weekday() - 5) % 7          # 最近的那个周六
        sat = _day0(d - timedelta(days=back))
        end = min(sat + 2 * 86400, now)       # 到周日 24:00（未结束则到现在）
        return (sat, end, "上周末" if back else "本周末")
    # 周X / 星期X / 上周X（"上周三"必须放在"上周"之前）
    m = re.search(r"(周|星期|礼拜)([一二三四五六日天])", t)
    if m:
        wmap = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        w = wmap.get(m.group(2))
        if w is not None:
            d = datetime.now()
            back = (d.weekday() - w) % 7
            d0 = _day0(d - timedelta(days=back))
            end = min(d0 + 86400, now)
            return (d0, end, "周" + m.group(2))
    if any(k in t for k in ("上周", "上个星期", "上一周", "上星期")):
        d = datetime.now()
        this_mon = _day0(d - timedelta(days=d.weekday()))
        return (this_mon - 7 * 86400, this_mon, "上周")
    if any(k in t for k in ("上个月", "上月")):
        first = _day0(datetime.now().replace(day=1))
        prev_month = datetime.fromtimestamp(first - 86400)  # 上个月最后一天
        return (_day0(prev_month.replace(day=1)), first, "上个月")
    if any(k in t for k in ("这个月", "本月")):
        return (_day0(datetime.now().replace(day=1)), now, "这个月")
    if "今天" in t or "今日" in t:
        d0 = _day0(datetime.now())
        if "早上" in t or "早晨" in t:
            return (d0, min(d0 + 9 * 3600, now), "今天早上")
        if "上午" in t:
            return (d0, min(d0 + 12 * 3600, now), "今天上午")
        if "中午" in t:
            return (d0 + 11 * 3600, min(d0 + 14 * 3600, now), "今天中午")
        if "下午" in t:
            return (d0 + 12 * 3600, min(d0 + 18 * 3600, now), "今天下午")
        if "晚上" in t:
            return (d0 + 18 * 3600, now, "今天晚上")
        return (d0, now, "今天")
    if "昨天" in t or "昨日" in t:
        d0 = _day0(datetime.now() - timedelta(days=1))
        return (d0, d0 + 86400, "昨天")
    if "上午" in t:
        return (_day0(datetime.now()), now, "今天上午起")
    if "下午" in t:
        return (_day0(datetime.now()) + 12 * 3600, now, "今天下午起")
    if "晚上" in t:
        return (_day0(datetime.now()) + 18 * 3600, now, "今晚起")
    # 默认：你上次发言之前的那一条
    gs = (state_store.load_state().get(cid) or {})
    prev = (gs.get("prev_speak") or {}).get(uid)
    last = (gs.get("last_speak") or {}).get(uid)
    since = prev or last
    if since and now - since <= _tune("recap.max_hours", cid) * 3600:
        return (since, now, "")
    return (now - 6 * 3600, now, "最近 6 小时")


def _day0(dt) -> float:
    """某天 00:00 的时间戳."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _msg_ts(m: dict) -> float:
    """消息时间戳（sent_at 是 UTC 字符串）."""
    try:
        return datetime.fromisoformat(
            str(m.get("sent_at") or "").replace("+00", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _recap_mentions(msgs: list, uid: str):
    """扫窗口内有没有人提到/@他。返回 (次数, 提到他的人名列表, 一句样例)."""
    names = {get_full_name(uid), get_name(uid)}
    names = {n for n in names if n and len(n) >= 2}
    if not names:
        return (0, [], "")
    hits, who, sample = 0, [], ""
    for m in msgs:
        if is_self(m.get("sent_by")) or m.get("sent_by") == uid:
            continue
        t = (m.get("content") or {}).get("text", "")
        if not t or "加入了群聊" in t:
            continue  # 入群提示里的名字不算"有人找你"（但仍保留在转录里给 AI 归纳）
        if any(("@" + n) in t or (n in t) or (n[:4] in t and len(n) >= 4) for n in names):
            hits += 1
            nm = get_name(m.get("sent_by", "")) or "有人"
            if nm not in who:
                who.append(nm)
            if not sample:
                sample = t[:60]
    return (hits, who[:3], sample)


def _recap_transcript(msgs: list, cid: str = None) -> tuple:
    """消息列表 → 给 AI 的转录文本 + 条数（bot 消息标成本群自称，长文截断）."""
    lines = []
    _me = _pme(cid)
    for m in msgs:
        u = m.get("sent_by", "")
        t = ((m.get("content") or {}).get("text") or "").strip()
        if not t:
            continue
        if is_self(u):
            lines.append(_me + ": " + t[:100])
        else:
            lines.append(get_name(u) + ": " + t[:80])
    return ("\n".join(lines), len(lines))


async def recap_absent(cid: str, uid: str, text: str = "") -> str:
    """「我不在的时候群里发生了什么」——返回 ≤10 行的回顾."""
    if not _tune_scope("recap", cid):
        return ""
    gs = (state_store.load_state().get(cid) or {})
    since, until, wlabel = _recap_window(cid, uid, text)
    wkey = str(int(since) // 60)  # 窗口指纹（起点取整到分钟）
    # 冷却只拦"同一时间窗重复问"；换个时间段问（昨天/上午/两小时）直接放行
    cd = gs.get("recap_ts") or {}
    last = cd.get(uid) or {}
    if isinstance(last, dict) and last.get("key") == wkey \
            and time.time() - float(last.get("ts") or 0) \
            < _tune("recap.cooldown", cid):
        return "这一段刚给你说完呢～换个时间段问（比如「昨天」「上午」），或者过一会儿再问嘛～"
    try:
        since_utc = datetime.utcfromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S")
        msgs = await asyncio.to_thread(get_messages_since, cid, since_utc,
                                       max_count=600)
    except Exception as e:
        log("回顾拉取失败: " + str(e)[:60])
        return (_pp("recap_fail", cid)
                or "哎呀，" + _pme(cid) + "翻记录的时候卡了一下，等会儿再问一次好不好～")
    # 先把成员昵称一次性load进来：否则转录里每个陌生 uid 都会单独发一次 HTTP 查名字
    # （实测 6 小时窗口因此多花 2.9 秒）
    try:
        await asyncio.to_thread(_load_member_names, cid)
    except Exception:
        pass
    msgs = [m for m in msgs if (m.get("content") or {}).get("text", "").strip()]
    # 按窗口终点过滤（"昨天"/"上周末"这类有明确结束时间，不能把今天的消息也算进来）
    if until and until < time.time() - 60:
        msgs = [m for m in msgs if _msg_ts(m) <= until]
    total = len(msgs)
    if total == 0:
        return "（翻了翻小本子）" + (wlabel or "你不在的这段时间") + "群里一条消息都没有，安静得像没人～"
    kept = msgs[-int(_tune("recap.max_msgs", cid)):]
    transcript, n_shown = _recap_transcript(kept, cid)
    hits, who, sample = _recap_mentions(msgs, uid)
    end_show = until if (until and until < time.time() - 60) else time.time()
    if wlabel:
        win_txt = wlabel + " · " + _fmt_ts(since) + " ~ " + _fmt_ts(end_show)
    else:
        win_txt = _fmt_ts(since) + " ~ " + _fmt_ts(end_show)
    # 🔔 有人找你这行由代码确定，不让 AI 编
    if hits:
        bell = ("🔔 有人找你：" + "、".join(who) + " 提到你 " + str(hits) + " 次"
                + ("（比如「" + sample + "」）" if sample else ""))
    else:
        bell = "🔔 有人找你：没人提到你，安心～"
    mention_txt = ("提到他 " + str(hits) + " 次，提到的人是 " + "、".join(who)) if hits else "无"
    try:
        raw = await asyncio.to_thread(generate_recap, transcript, get_name(uid),
                                      win_txt, mention_txt)
    except Exception as e:
        log("回顾生成失败: " + str(e)[:60])
        raw = ""
    body = {}
    for ln in str(raw or "").split("\n"):
        s = ln.strip()
        for k in ("💬", "🎭", "📌"):
            if s.startswith(k):
                body[k] = s
    lines = ["📮 你不在的时候（" + win_txt + "，群里 " + str(total) + " 条"
             + ("，这里挑最近 " + str(n_shown) + " 条看" if total > n_shown else "") + "）",
             bell]
    for k in ("💬", "🎭", "📌"):
        if body.get(k):
            lines.append(body[k])
    if len(lines) < 3:
        lines.append("（群里挺热闹的，但" + _pme(cid) + "没提炼出什么重点，要不你再问细一点？）")
    # 记录冷却
    try:
        st = state_store.load_state()
        g2 = st.get(cid) or {}
        cd2 = g2.get("recap_ts") or {}
        cd2[uid] = {"ts": time.time(), "key": wkey}
        g2["recap_ts"] = cd2
        st[cid] = g2
        state_store.save_state(st)
    except Exception:
        pass
    log("📮 [" + cid[:8] + "] 回顾: " + get_name(uid)[:12] + " 窗口 " + win_txt
        + " 共 " + str(total) + " 条，提及 " + str(hits) + " 次")
    return "\n".join(lines[:10])
