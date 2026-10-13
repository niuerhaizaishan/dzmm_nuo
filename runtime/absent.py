"""缺席名册（/寻人）：找出超过 N 天没发言的熟面孔 + 她们留下的最后痕迹。

数据面（2026-10-13 屋主需求："查询那些离开的人，超过半个月没有发言的群友，
以及她们之前的故事，和最后一句话。当然，是之前在群里活跃过的人"）：

  · 发言统计/最后一句话/语录/小传取样 → 认证群聊天归档（runtime.chat_archive，
    只读 SQL，零平台 API，带 sender 索引，按人聚合是一次 GROUP BY 的事）；
  · "已退群 vs 还在潜水" → 拉一次当前成员表（roster.reconcile_members 同款
    `chatroom.getMembers`），uid 不在表里 = ⚰️ 已离开；
  · "活跃过" 门槛 → 累计文本发言 ≥ absent.min_msgs（默认 5 条），
    过滤进来冒个泡就走的人；
  · 小传（单人卡）→ brains._call_llm_raw 一次调用；失败不拦卡片，
    本地数据照常出，小传位置给占位。

改名兼容：按 sender_id 聚合，昵称并入 profiles 当前名（find_senders 同款）。
"""
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

import brains
from runtime import tuning
from runtime.chat_archive import archive_path
from runtime.chat_archive_query import LOCAL_ZONE, find_senders
from runtime.ctx import PRIMARY_CID
from runtime.obs import log

__all__ = ["is_absent_cmd", "absent_reply"]

CMD_PREFIXES = ("/寻人", "/缺席")
CLEAN_SQL = ("message_type = 'text'", "length(trim(text)) > 0",
             "substr(trim(text), 1, 1) NOT IN ('/', '#', '✧')")
LIST_SNIPPET = 40          # 裸列表里最后一句话截断字数
QUOTE_MAX = 120            # 单条语录截断
MEMOIR_TIMEOUT = 45        # 小传 LLM 超时（秒）
MEMOIR_FALLBACK = "（小传没写出来，不过她留下的痕迹都在上面啦）"


def is_absent_cmd(cmd: str):
    """识别寻人指令：None=不是；""=裸 /寻人（列表）；其他=目标昵称."""
    for prefix in CMD_PREFIXES:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None


def absent_reply(cid: str, query: str) -> str:
    """dispatch 入口（同步，调用方 to_thread）：裸=列表，带昵称=单人卡."""
    if query:
        return _card(cid, query)
    return _list(cid)


# ---------------------------------------------------------------- 归档只读

def _connect(path=None):
    database = Path(path or archive_path())
    if not database.is_file():
        return None
    conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    return conn


def _per_person(conn):
    """每人聚合：names 并集（含 profiles 当前名）、总条数、首末发言。"""
    per = {}
    for uid, name, total, first_at, last_at in conn.execute(
            "SELECT sender_id, sender_name, count(*), min(sent_at), max(sent_at) "
            "FROM messages WHERE group_id = ? AND message_type = 'text' "
            "GROUP BY sender_id, sender_name", (PRIMARY_CID,)):
        p = per.setdefault(uid, {"uid": uid, "names": set(), "total": 0,
                                 "first_at": first_at or 0, "last_at": last_at or 0})
        if name:
            p["names"].add(name)
        p["total"] += total
        p["first_at"] = min(p["first_at"], first_at or 0)
        p["last_at"] = max(p["last_at"], last_at or 0)
    try:
        from profiles import load_profiles
        for uid, profile in (load_profiles() or {}).items():
            current = (profile or {}).get("name") or ""
            if current and uid in per:
                per[uid]["names"].add(current)
    except Exception:                                            # noqa: BLE001
        pass
    return per


def _last_words(conn, uid):
    """最近一条干净文本（跳过指令），没有则空串."""
    where = " AND ".join(("group_id = ?", "sender_id = ?") + CLEAN_SQL)
    row = conn.execute(
        "SELECT text FROM messages WHERE " + where +
        " ORDER BY sent_at DESC, message_id DESC LIMIT 1",
        (PRIMARY_CID, uid)).fetchone()
    return (row[0] or "").strip() if row else ""


def _quotes(conn, uid, limit=3):
    """经典语录：最长的几条干净发言（发言太短的人语录可能为空）."""
    where = " AND ".join(("group_id = ?", "sender_id = ?",
                          "length(trim(text)) >= 6") + CLEAN_SQL)
    rows = conn.execute(
        "SELECT text FROM messages WHERE " + where +
        " ORDER BY min(length(text), ?) DESC, sent_at DESC LIMIT ?",
        (PRIMARY_CID, uid, QUOTE_MAX, limit)).fetchall()
    return [(r[0] or "").strip()[:QUOTE_MAX] for r in rows]


def _months(conn, uid):
    """(首次月份, 最近月份, 最活跃月份) —— 东八区."""
    row = conn.execute(
        "SELECT min(sent_at), max(sent_at) FROM messages "
        "WHERE group_id = ? AND sender_id = ? AND message_type = 'text'",
        (PRIMARY_CID, uid)).fetchone()
    if not row or not row[0]:
        return "", "", ""
    fmt = lambda ts: datetime.fromtimestamp(ts, LOCAL_ZONE).strftime("%Y-%m")  # noqa: E731
    top = conn.execute(
        "SELECT strftime('%Y-%m', sent_at + 28800, 'unixepoch') AS m, count(*) AS c "
        "FROM messages WHERE group_id = ? AND sender_id = ? AND message_type = 'text' "
        "GROUP BY m ORDER BY c DESC LIMIT 1", (PRIMARY_CID, uid)).fetchone()
    return fmt(row[0]), fmt(row[1]), (top[0] if top else "")


def _memoir_samples(conn, uid, limit=30):
    """小传取样：最近的干净发言（老→新），给模型当证据."""
    where = " AND ".join(("group_id = ?", "sender_id = ?") + CLEAN_SQL)
    rows = conn.execute(
        "SELECT text FROM (SELECT text, sent_at FROM messages WHERE " + where +
        " ORDER BY sent_at DESC LIMIT ?) ORDER BY sent_at",
        (PRIMARY_CID, uid, limit)).fetchall()
    return [(r[0] or "").strip()[:160] for r in rows]


# ---------------------------------------------------------------- 成员表

def _current_uids(cid):
    """当前群成员 uid 集合 + 是否成功（roster.reconcile_members 同款 API）。"""
    try:
        from dzmm import api as _api
        raw = _api("chatroom.getMembers", {"json": {"chatroomId": cid, "limit": 1000}})
        members = (raw or {}).get("members") or []
        return {m.get("id") for m in members if m.get("id") and not m.get("isBot")}, True
    except Exception as e:                                       # noqa: BLE001
        log("寻人：成员表拉取失败 [{}] {}".format((cid or "")[:8], str(e)[:60]))
        return set(), False


# ---------------------------------------------------------------- 列表

def _list(cid: str) -> str:
    days = int(tuning.get("absent.days", cid))
    min_msgs = int(tuning.get("absent.min_msgs", cid))
    max_show = int(tuning.get("absent.max_show", cid))
    conn = _connect()
    if conn is None:
        return "糯糯还没找到认证群的聊天归档，暂时没法翻名册～"
    try:
        with conn:
            conn.execute("BEGIN")
            per = _per_person(conn)
    finally:
        conn.close()
    cutoff = time.time() - days * 86400
    gone = [p for p in per.values() if p["total"] >= min_msgs and p["last_at"] < cutoff]
    if not gone:
        return "最近 {} 天里，翻过档案的熟面孔们都还在说话呢～没有失踪人口．".format(days)
    uids, ok = _current_uids(cid)
    conn2 = _connect()
    try:
        with conn2:
            conn2.execute("BEGIN")
            for p in gone:
                p["last_text"] = _last_words(conn2, p["uid"])
    finally:
        conn2.close()
    gone.sort(key=lambda p: p["last_at"])
    for p in gone:
        p["name"] = sorted(p["names"], key=len)[0] if p["names"] else "未知昵称"
        p["gap"] = max(1, int((time.time() - p["last_at"]) / 86400))
    header = "🕯️ 缺席名册 · 超过 {} 天没说话的熟面孔（{} 人）".format(days, len(gone))
    if not ok:
        header += "\n（成员表没拉到，暂时分不出退群还是潜水）"
    left = [p for p in gone if ok and p["uid"] not in uids]
    stay = [p for p in gone if not ok or p["uid"] in uids]
    lines = [header]

    def block(title, items, mark):
        if not items:
            return
        lines.append("")
        lines.append("{} {}".format(mark, title))
        for p in items[:max_show]:
            snippet = p["last_text"][:LIST_SNIPPET]
            ell = "…" if len(p["last_text"]) > LIST_SNIPPET else ""
            line = "· {} — {} 天前".format(p["name"], p["gap"])
            if snippet:
                line += "：“{}{}”".format(snippet, ell)
            lines.append(line)
        rest = len(items) - max_show
        if rest > 0:
            lines.append("…还有 {} 人没列出来，想看谁就说 /寻人 昵称".format(rest))

    block("已离开群聊", left, "⚰️")
    block("还在群里潜水", stay, "💤")
    lines.append("")
    lines.append("想看谁留下的故事：/寻人 昵称")
    return "\n".join(lines)


# ---------------------------------------------------------------- 单人卡

def _card(cid: str, query: str) -> str:
    days = int(tuning.get("absent.days", cid))
    min_msgs = int(tuning.get("absent.min_msgs", cid))
    hits = find_senders(query)
    if not hits:
        return "名册里翻不到「{}」这个人，换个叫法试试？".format(query)
    if len(hits) > 1:
        opts = "、".join("{}（{}条）".format(h["name"], h["messages"]) for h in hits[:5])
        return "叫「{}」的有多位：{}。说得更准一点，糯糯再帮你翻～".format(query, opts)
    hit = hits[0]
    uid, name = hit["uid"], hit["name"]
    conn = _connect()
    if conn is None:
        return "糯糯还没找到认证群的聊天归档，暂时没法翻名册～"
    try:
        with conn:
            conn.execute("BEGIN")
            total = conn.execute(
                "SELECT count(*) FROM messages WHERE group_id = ? AND sender_id = ? "
                "AND message_type = 'text'", (PRIMARY_CID, uid)).fetchone()[0]
            if total < min_msgs:
                return "{} 只在群里冒过 {} 个泡，还没到名册门槛（{} 条），算不上失踪人口～".format(
                    name, total, min_msgs)
            last_at = conn.execute(
                "SELECT max(sent_at) FROM messages WHERE group_id = ? AND sender_id = ? "
                "AND message_type = 'text'", (PRIMARY_CID, uid)).fetchone()[0] or 0
            last_text = _last_words(conn, uid)
            quotes = _quotes(conn, uid)
            first_m, last_m, top_m = _months(conn, uid)
            samples = _memoir_samples(conn, uid)
    finally:
        conn.close()
    gap = max(1, int((time.time() - last_at) / 86400))
    uids, ok = _current_uids(cid)
    if ok:
        where = "⚰️ 已离开群聊" if uid not in uids else "💤 还在群里，就是很久没说话"
    else:
        where = "❓ 好久没说话了"
    lines = ["📮 寻人启事 · {}".format(name),
             "{}（最后发言 {} 天前）".format(where, gap)]
    if last_text:
        lines.append("💬 最后一句话：“{}”".format(last_text[:LIST_SNIPPET * 2]))
    span = "、".join(dict.fromkeys(x for x in (first_m, last_m) if x))
    meta = "🗣️ 留言 {} 条".format(total)
    if span:
        meta += " · 活跃于 {}".format(span)
        if top_m and top_m != last_m:
            meta += "（{} 最活跃）".format(top_m)
    lines.append(meta)
    if quotes:
        lines.append("📜 留下来的话：")
        lines.extend("· {}".format(q) for q in quotes[:3])
    lines.append("")
    lines.append(_memoir(cid, name, gap, last_text, total, span, quotes, samples))
    return "\n".join(lines)


def _memoir(cid, name, gap, last_text, total, span, quotes, samples):
    """AI 小传：一次调用，失败不拦卡片."""
    try:
        payload = json.dumps({
            "name": name, "days_absent": gap, "total_messages": total,
            "active_span": span, "last_text": last_text,
            "quotes": quotes[:3], "messages": samples}, ensure_ascii=False)
        reply, finish = brains._call_llm_raw(
            brains.ABSENT_MEMOIR_PROMPT, payload,
            max_tokens=int(tuning.get("absent.max_tokens", cid)),
            timeout=MEMOIR_TIMEOUT)
        text = (reply or "").strip()
        if not text or finish == "length":
            raise ValueError("memoir empty/truncated")
        return text
    except Exception as e:                                       # noqa: BLE001
        log("寻人小传失败 [{}] {}".format((cid or "")[:8], type(e).__name__))
        return MEMOIR_FALLBACK
