"""群投票（P8 第 4 步从 bot.py 搬出）。

⚠️ 分派顺序是行为，不是风格：`/投票结束` **同时**命中开始与结束两个判定，
而结束判定会被"没有进行中的投票"挡掉 → 落到开始判定 →
**开出一个标题叫"结束"的投票**。这条顺序见 `docs/保全清单.md`，不得重排。
"""
import time
from datetime import datetime, timedelta

from brains import generate_vote_options
from dzmm import get_messages_all
from runtime import features
from runtime.tuning import get as _tune   # P12-B：可调参数（唯一读口）
from runtime.group_state import _get_vote_state, _set_vote_state
from runtime.heat import HEAT_VOTE_STARTER, HEAT_VOTE_VOTER, _add_heat
from runtime.identity import get_name, is_self
from runtime.members import _extract_mentioned_members
from runtime.obs import log
from runtime.office_store import TEA_CID
from runtime.text import (_dedupe_options, _extract_deadline_seconds, _is_stop_word,
                          _normalize_choice, _normalize_command_prefix)
import state_store


__all__ = [
    "is_vote_start_cmd", "is_vote_end_cmd", "is_vote_choice_cmd",
    "_extract_vote_title", "_vote_members_text", "start_vote", "_record_vote",
    "_finish_vote", "_check_vote", "VOTE_DEFAULT_SECONDS",
]


VOTE_DEFAULT_SECONDS = 2 * 60  # 默认 2 分钟自动结束


def is_vote_start_cmd(cmd: str) -> bool:
    return cmd.startswith("/投票")


def is_vote_end_cmd(cmd: str) -> bool:
    return cmd.startswith("/投票结束") or _is_stop_word(cmd)


def is_vote_choice_cmd(cmd: str):
    """识别选择指令（/选 N 或 /N），返回归一化数字或 None. 投票与剧情共用."""
    c = cmd.strip().lstrip("/").strip()
    if not c:
        return None
    if c.startswith("选"):
        return _normalize_choice(c[1:].strip())
    return _normalize_choice(c)


def tea_command(cid: str, text: str) -> tuple[str, str] | None:
    if cid != TEA_CID:
        return None
    cmd = _normalize_command_prefix(text)
    if not cmd.startswith("#"):
        return None
    if cmd == "#投票结束":
        return "end", ""
    head, *rest = cmd.split(maxsplit=1)
    if head == "#投票":
        return "start", rest[0] if rest else ""
    if cmd.startswith("#选"):
        choice = _normalize_choice(cmd[2:])
    elif len(cmd) == 2 and cmd[1] in "123456789" and _get_vote_state(cid).get("active"):
        choice = cmd[1:]
    else:
        return None
    return ("choice", choice) if choice else None


def tea_reply(cid: str, uid: str, request: tuple[str, str]) -> str | None:
    action, payload = request
    vote = _get_vote_state(cid)
    if action == "start":
        if vote.get("active"):
            return "已经有进行中的投票啦～可以发 #投票结束 提前结算。"
        return start_vote(cid, "#投票 " + payload, uid)
    if not vote.get("active"):
        return "当前没有进行中的投票。"
    if action == "end" or vote.get("deadline", 0) <= time.time():
        return _finish_vote(cid)
    count = len(vote.get("options") or [])
    if not 1 <= int(payload) <= count:
        return f"请发送 #选 1 到 #选 {count} 之间的选项编号。"
    _record_vote(cid, uid, payload)
    return None


def _extract_vote_title(text: str, mentioned: list) -> str:
    """去掉点名成员后的剩余文本作为标题（简单清理）."""
    title = text
    for name in mentioned:
        title = title.replace(name, " ")
    for w in ["、", "，", ",", " ", "？", "?", "！", "!", "呢", "呀", "啊", "嘛", "～", "~"]:
        title = title.replace(w, "")
    title = title.strip()
    return title or "投票"


def _vote_members_text(cid: str) -> str:
    """当前群发言成员名单（供 AI 生成"选人"类选项）."""
    msgs = get_messages_all(cid, max_count=200)
    uids = []
    for m in msgs:
        uid = m.get("sent_by", "")
        if uid and not is_self(uid) and uid not in uids:
            uids.append(uid)
    names = [get_name(uid) for uid in uids if get_name(uid)]
    return "、".join(names[:30])


def start_vote(cid: str, cmd: str, sender_uid: str) -> str:
    """发起投票：解析三种方式，返回投票消息."""
    prefix = "#" if cid == TEA_CID else "/"
    start = prefix + "投票"
    rest = cmd[len("/投票"):].strip() if cmd.startswith(("/投票", start)) else cmd
    if not rest:
        return f"格式：{start} 主题 | 选项1 | 选项2（或 {start} 主题 让我生成选项，可加 5分钟 定时）"
    rest, deadline_secs = _extract_deadline_seconds(rest)
    title = rest
    options = []
    if "|" in rest:
        parts = [p.strip() for p in rest.split("|") if p.strip()]
        title = parts[0] if parts else ""
        options = parts[1:]
    else:
        mentioned = _extract_mentioned_members(rest, cid)
        if len(mentioned) >= 2:
            title = _extract_vote_title(rest, mentioned)
            options = mentioned
        else:
            try:
                options = generate_vote_options(rest, _vote_members_text(cid))
            except Exception:
                options = []
            if len(options) < 2:
                return f"没想好选项呢，你自己列一下吧：{start} 主题 | 选项1 | 选项2"
    options = _dedupe_options(options)[:6]
    if len(options) < 2:
        return f"选项太少啦，至少 2 个哦～格式 {start} 主题 | 选项1 | 选项2"
    _set_vote_state(cid, {
        "active": True,
        "title": title,
        "options": options,
        "votes": {},
        "starter": sender_uid,
        "deadline": time.time() + (deadline_secs
                                       or _tune("vote.default_seconds", cid)),
        "last_remind": time.time(),
    })
    lines = ["📊 投票：" + title]
    for i, opt in enumerate(options, 1):
        lines.append("【" + str(i) + "】" + opt)
    if cid == TEA_CID:
        seconds = deadline_secs or _tune("vote.default_seconds", cid)
        duration = f"{seconds // 60}分钟" if seconds % 60 == 0 else f"{seconds}秒"
        footer = f"（发 #选 X 或 #X 投票，可改票，{duration}倒计时，#投票结束 可提前结算～）"
    elif deadline_secs:
        if deadline_secs >= 60:
            footer = "（发 /选 X 或 /X 投票（X=序号），可改票，" + str(deadline_secs // 60) + "分钟倒计时～）"
        else:
            footer = "（发 /选 X 或 /X 投票（X=序号），可改票，" + str(deadline_secs) + "秒倒计时～）"
    else:
        footer = "（发 /选 X 或 /X 投票（X=序号），可改票，2分钟倒计时，/投票结束 可提前结束～）"
    lines.append(footer)
    return "\n".join(lines)


def _record_vote(cid: str, uid: str, choice: str) -> None:
    """记录投票（以最后一次为准），静默不回复."""
    vote = _get_vote_state(cid)
    if not vote.get("active"):
        return
    votes = vote.get("votes") or {}
    votes[uid] = choice
    vote["votes"] = votes
    _set_vote_state(cid, vote)


def _finish_vote(cid: str) -> str:
    """结束投票：统计 + 结算加分 + 清空，返回结果消息."""
    vote = _get_vote_state(cid)
    if not vote.get("active"):
        return ""
    title = vote.get("title", "投票")
    options = vote.get("options") or []
    votes = vote.get("votes") or {}
    counts = {}
    for v in votes.values():
        counts[v] = counts.get(v, 0) + 1
    total = len(votes)
    lines = ["📊 投票结果：" + title]
    for i, opt in enumerate(options, 1):
        key = str(i)
        n = counts.get(key, 0)
        pct = (n * 100 // total) if total else 0
        voters = [get_name(uid) for uid, v in votes.items() if v == key]
        voter_str = "、".join([x for x in voters if x]) if voters else "无"
        lines.append("【" + str(i) + "】" + opt + "　" + str(n) + "票(" + str(pct) + "%)　" + voter_str)
    starter = vote.get("starter", "")
    if starter and not is_self(starter):
        _add_heat(cid, starter, _tune("heat.vote_starter", cid))
    for uid in votes.keys():
        if uid and not is_self(uid) and uid != starter:
            _add_heat(cid, uid, _tune("heat.vote_voter", cid))
    _set_vote_state(cid, {})
    if cid != TEA_CID:
        lines.append("🎉 结算：发起人 +8，投票的人各 +5")
    elif features.on(cid, "heat"):
        lines.append(f"🎉 结算：发起人 +{_tune('heat.vote_starter', cid)}，"
                     f"其他投票人各 +{_tune('heat.vote_voter', cid)}")
    return "\n".join(lines)


def _check_vote(cid: str) -> str:
    """投票定时检查：每 30 秒提醒剩余时间，截止到 → 自动结束."""
    vote = _get_vote_state(cid)
    if not vote.get("active"):
        return ""
    now = time.time()
    remaining = vote.get("deadline", 0) - now
    if remaining <= 0:
        return _finish_vote(cid)
    if now - vote.get("last_remind", 0) >= 30:
        vote["last_remind"] = now
        _set_vote_state(cid, vote)
        prefix = "#" if cid == TEA_CID else "/"
        return f"⏰ 还有 {int(remaining)} 秒，想投的快 {prefix}选 X 或 {prefix}X 哦～"
    return ""
