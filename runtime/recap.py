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
from runtime.features import on as _feat_on   # P12-AK：本群接不接 `/` 指令（决定 /xxx 算不算噪音）
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


MIN_WINDOW_SEC = 300                # 起点距现在不足 5 分钟 ⇒ 认为起点算错了，退回默认窗口
                                    # （2026-09-29 现场：窗口塌成 0 分钟，"群里 1 条"
                                    #   就是提问自己 —— 见 `_recap_window` 里的说明）


# 三段的固定标题（解析时重建，不让模型自由发挥标题）
_SECTION_TITLE = {"💬": "大家在聊", "🎭": "群里的事", "📌": "等你回"}


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
    # 默认：你上次发言**之前**的那一条
    #
    # ⚠️ P12-AK 修（2026-09-29 屋主现场报的 bug）：
    #   在顶层茶水间 @ 她问"我不在的时候发生了什么"，回的是
    #   「你不在的时候（09-29 10:46 ~ 09-29 10:46，群里 1 条）」—— 窗口塌成 0 分钟。
    #   原因：他**在这个群没发过言**。`bot.py` 的发言统计在处理这条提问时刚把它写进
    #   `last_speak[uid]`（`prev_speak[uid]` 还是空的）⇒ `since = last_speak` =
    #   **提问那一刻**，窗口里只剩这条提问本身（"1 条"就是它）。
    #   修法：起点必须**明显早于现在**才算数（`MIN_WINDOW_SEC`），否则退回默认窗口。
    gs = (state_store.load_state().get(cid) or {})
    prev = (gs.get("prev_speak") or {}).get(uid)
    last = (gs.get("last_speak") or {}).get(uid)
    since = prev or last
    cap = _tune("recap.max_hours", cid) * 3600
    if since and MIN_WINDOW_SEC <= (now - since) <= cap:
        return (since, now, "")
    # P12-AK：回退窗口按群可调（原来写死 6 小时 —— 热闹的群一小时几百条，
    # 6 小时的转录能把提示词塞满；顶层茶水间先设 2-3 小时）。
    h = max(1, int(_tune("recap.default_hours", cid)))
    return (now - h * 3600, now, "最近 " + str(h) + " 小时")


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


def _ignore_kw(cid: str = None) -> tuple:
    """本群 recap 要忽略的内容关键词（逗号/顿号/空格分隔，空 = 不过滤）。

    P12-AK：群里常跑着**别家 bot 的游戏** —— 顶层茶水间实测（2026-09-29 取样 100 条）
    `总监事「bot」` 一人发了 38 条，"XX，躲猫猫开始…""【巡查】…""第 3 级通过…"
    "恭喜 XX 完成第 5 级记忆考核" 这类占了窗口的 **39%**；群友自己发的
    `/开始摸鱼躲藏`、`/记忆考核`、`/答案 xxx`、`/继续` 也是同一套噪音。
    这些内容进了转录只会让 recap 变成"游戏播报"，所以要按群过滤掉。
    """
    raw = str(_tune("recap.ignore_kw", cid) or "").strip()
    if not raw:
        return ()
    parts = re.split(r"[,，、;；\s]+", raw)
    return tuple(p for p in (x.strip() for x in parts) if p)


def _is_noise(text: str, kw: tuple, cid: str = None) -> bool:
    """这条消息算不算要忽略的噪音。

    两类（P12-AK）：
      1. 命中 `recap.ignore_kw` 关键词（游戏播报："躲猫猫开始…""第 3 级通过…"）；
      2. **本群不接 `/` 指令**（`slash_cmds` 关）时，`/xxx` 属于**别家 bot 的协议** ——
         `/躲 6`、`/答案 D4BfI`、`/继续`、`/记忆考核` 这些既不是聊天也不是给我们的指令，
         留着只会把转录塞满（2026-09-29 实测：过滤后的前 8 行里有 7 行是它们）。
    """
    t = text or ""
    if kw and any(k in t for k in kw):
        return True
    if t.startswith("/") and cid and not _feat_on(cid, "slash_cmds"):
        return True
    return False


def _msg_kind(m: dict) -> str:
    """非文字消息也给个标记，别在转录里凭空消失（图片/语音/表情/撤回…）。"""
    c = m.get("content") or {}
    t = str(c.get("type") or "text")
    if t == "sticker":
        return "[表情]"
    if t in ("image", "photo"):
        return "[图片]"
    if t == "audio":
        return "[语音]"
    if t == "video":
        return "[视频]"
    if t in ("recalled", "recall"):
        return "[撤回]"
    if t == "system":
        return "[系统]"
    return ""


def _recap_mentions(msgs: list, uid: str, kw: tuple = (), cid: str = None):
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
        if _is_noise(t, kw, cid):
            continue  # P12-AK：游戏播报里的"提到"不算有人找他
        if any(("@" + n) in t or (n in t) or (n[:4] in t and len(n) >= 4) for n in names):
            hits += 1
            nm = get_name(m.get("sent_by", "")) or "有人"
            if nm not in who:
                who.append(nm)
            if not sample:
                sample = t[:60]
    return (hits, who[:3], sample)


def _recap_transcript(msgs: list, cid: str = None) -> tuple:
    """消息列表 → 给 AI 的转录文本 + 条数。

    P12-AK（屋主 2026-09-29："小小糯的**输入内容要丰富完整一些**"）—— 原来这段很瘦，
    三种信息都在丢：每条**截断到 80/100 字**、**非文字消息直接丢**（图片/语音/表情
    全没了）、**没有时间**（跨天时模型不知道哪句是几点说的）。现在：
      · 文本基本不截断（单条封顶 300 字，防一条长文吃掉全部预算）；
      · 非文字按类型补标记（[图片]/[语音]/[表情]/[撤回]）；
      · 每条前带时间 `HH:MM`，跨天处插一行日期分隔；
      · 命中 `recap.ignore_kw` 的噪音（游戏播报/打卡等）不进转录。
    """
    lines = []
    n_msg = 0
    _me = _pme(cid)
    kw = _ignore_kw(cid)
    last_day = ""
    for m in msgs:
        u = m.get("sent_by", "")
        c = m.get("content") or {}
        t = (c.get("text") or "").strip()
        if _is_noise(t, kw, cid) or (not t and not _msg_kind(m)):
            continue
        ts = _msg_ts(m)
        if ts:
            day = datetime.fromtimestamp(ts).strftime("%m-%d")
            if day != last_day:
                lines.append("— " + day + " —")      # 日期分隔行（不算消息条数）
                last_day = day
            stamp = datetime.fromtimestamp(ts).strftime("%H:%M")
        else:
            stamp = ""
        body = t[:300] if t else _msg_kind(m)
        who = _me if is_self(u) else get_name(u)
        lines.append("[{}] {}: {}".format(stamp or "--:--", who, body))
        n_msg += 1
    return ("\n".join(lines), n_msg)


def _parse_recap_sections(raw: str) -> dict:
    """把模型输出解析成 `{💬: [条, …], 🎭: […], 📌: […]}`（P12-AM）。

    为什么要单独一个函数：老实现是 `body[k] = s` —— **每段只留最后一行**，
    而新提示词要求"段内每条一行、每段 2~3 条"，多写会被静默丢掉（屋主反馈"内容单薄"
    就是这么来的）。抽成函数后可以直接测。

    两种行都认：
      · 标题行 `💬 大家在聊：` —— 只认它的 emoji，**标题本身不算内容**（下面会重建标题）
      · 内容行 `· 糯糯和橘先生在接龙唱山歌`（`·`/`-`/`•`/`*` 都认，没有前缀也认）
    """
    body, cur = {}, None
    for ln in str(raw or "").split("\n"):
        s = ln.strip()
        if not s:
            continue
        hit = next((k for k in ("💬", "🎭", "📌") if s.startswith(k)), None)
        if hit:
            cur = hit
            body.setdefault(cur, [])
            rest = s[len(hit):].strip().lstrip("：:").strip()
            if not rest:
                continue
            head = re.split(r"[：:]", rest)[0].strip()
            if ("：" in rest or ":" in rest or head == _SECTION_TITLE.get(hit)) \
                    and len(head) <= 8:
                # 「💬 大家在聊：」这类**只有标题**的行 → 冒号后面的才算内容
                after = rest[len(head):].lstrip("：: ").strip()
                if after:
                    body[cur].append(after)
                continue
            body[cur].append(rest)
        elif cur:
            rest = s.lstrip("·-•*・ ").strip()
            if rest:
                body[cur].append(rest)
    return body


async def recap_absent(cid: str, uid: str, text: str = "",
                       exclude_mid: str = None) -> str:
    """「我不在的时候群里发生了什么」——返回 ≤10 行的回顾.

    `exclude_mid`：**当前这条提问**的消息 id。不排掉的话它会被当成"群里发生的事"
    算进条数与转录（2026-09-29 现场："群里 1 条"就是提问自己）。
    """
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
    # 排掉当前这条提问本身（它是"问题"，不是"你不在的时候发生的事"）
    if exclude_mid:
        msgs = [m for m in msgs if str(m.get("message_id") or "") != str(exclude_mid)]
    # 按窗口终点过滤（"昨天"/"上周末"这类有明确结束时间，不能把今天的消息也算进来）
    if until and until < time.time() - 60:
        msgs = [m for m in msgs if _msg_ts(m) <= until]
    total = len(msgs)
    if total == 0:
        return "（翻了翻小本子）" + (wlabel or "你不在的这段时间") + "群里一条消息都没有，安静得像没人～"
    kept = msgs[-int(_tune("recap.max_msgs", cid)):]
    _kw = _ignore_kw(cid)
    transcript, n_shown = _recap_transcript(kept, cid)
    hits, who, sample = _recap_mentions(msgs, uid, _kw, cid)
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
    # P12-AM（屋主 2026-09-29："内容要再丰富完善一些，有些单薄"）：
    # 原来是"每段只保留**最后一行**"（`body[k] = s`）—— 而提示词又硬性要求"只输出 3 行、
    # 每行 35/25/20 字"，加起来才 80~140 字，内容自然单薄。
    # 现在：提示词改成"三段、段内每条一行以 `· ` 开头（每段 2~3 条）"，
    # 解析交给 `_parse_recap_sections()` **按段累积**。
    body = _parse_recap_sections(raw)
    # 📌 那段**不准缺席**：模型偶尔会漏写（2026-09-29 实测：只给了 💬/🎭 两段，
    # 于是"等你回"整段消失，用户看到的就是残缺）。缺了就用**代码已知的事实**补一条
    # —— 内容来自 `hits`（谁提到过他），不让模型编。
    if not [r for r in (body.get("📌") or []) if r]:
        if hits:
            body["📌"] = ["、".join(who) + " 提到你 " + str(hits) + " 次"
                          + ("，比如「" + sample + "」" if sample else "")]
        else:
            body["📌"] = ["没人等你，安心潜水吧"]
    lines = ["📮 你不在的时候（" + win_txt + "，群里 " + str(total) + " 条"
             + ("，这里挑最近 " + str(n_shown) + " 条看" if total > n_shown else "") + "）",
             bell]
    for k in ("💬", "🎭", "📌"):
        rows = [r for r in (body.get(k) or []) if r][:5]
        if not rows:
            continue
        lines.append("{} {}：".format(k, _SECTION_TITLE[k]))
        for r in rows:
            lines.append("· " + r.replace("\n", " "))
    if not any(body.get(k) for k in ("💬", "🎭", "📌")):
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
    # ⚠️ P12-AM：这里原来是 `lines[:10]`（旧格式总共才 5 行，10 行上限没问题）。
    # 改成"三段、段内 2~3 条"之后，正常输出就是 2 + 3×(1+3) = 14 行 ——
    # **最后一段被这行沉默截掉**（2026-09-29 实测：📌 等你回 整段消失，
    # 工具还报"段落不全"）。上限按新格式的极端情况给足：
    # 头 2 行 + 3 段 × (1 标题 + 5 条) = 20 行。
    return "\n".join(lines[:20])
