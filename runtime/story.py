"""群剧情接龙（P8 第 4 步从 bot.py 搬出）。

**实测澄清**：`/剧情结束` 只命中结束判定（`is_story_start_cmd=False`、
`is_story_end_cmd=True`），**没有**投票/发骚那种"开始与结束同时命中"的重叠。
`docs/保全清单.md` 登记的重叠只有 `/投票结束`、`/发骚结束`、`/调教结束`。

`_record_story_vote` / `_story_vote_result` 是**剧情与投票共用**的两个函数
（剧情也用投票选下一步），所以放在这里、由 vote 侧引用 —— 不能两边各留一份。
"""
import time
from datetime import datetime, timedelta

import random

from brains import generate_story_end, generate_story_start, generate_story_step
from dzmm import get_messages_all
from runtime.group_state import _get_story_state, _set_story_state
from runtime.heat import HEAT_STORY_PROTAGONIST, HEAT_STORY_VOTER, _add_heat
from runtime.identity import get_full_name, is_self
from runtime.members import _find_in_profiles, _find_user_in_msgs, _random_member_uid
from runtime.obs import log
from runtime.text import _is_stop_word, _squash
import state_store


__all__ = [
    "is_story_start_cmd", "is_story_end_cmd", "start_story", "_check_story",
    "_finish_story", "_advance_story", "_record_story_vote",
    "_story_vote_result", "STORY_MAX_ROUND", "STORY_VOTE_SECONDS",
]


STORY_MAX_ROUND = 6


STORY_VOTE_SECONDS = 60


def is_story_start_cmd(cmd: str) -> bool:
    return cmd.startswith("/剧情开始") or cmd.strip() == "/剧情"


def is_story_end_cmd(cmd: str) -> bool:
    return cmd.startswith("/剧情结束") or _is_stop_word(cmd)


def start_story(cid: str, cmd: str, sender_uid: str) -> str:
    """发起剧情游戏：解析主角+自定义剧情，生成开局，设置状态，返回开局消息."""
    rest = ""
    if cmd.startswith("/剧情开始"):
        rest = cmd[len("/剧情开始"):].strip()
    protagonist_uid = None
    protagonist = ""
    custom = ""
    if rest:
        tokens = rest.split()
        # 档案本地匹配（零 API，可多次尝试）
        for i in range(len(tokens), 0, -1):
            name_part = " ".join(tokens[:i])
            uid, _ = _find_in_profiles(name_part)
            if uid and not is_self(uid):
                protagonist_uid = uid
                protagonist = get_full_name(uid)
                custom = " ".join(tokens[i:])
                break
        # 消息里匹配（一次 API）
        if not protagonist_uid:
            msgs = get_messages_all(cid, max_count=200)
            for i in range(len(tokens), 0, -1):
                name_part = " ".join(tokens[:i])
                uid2, full2 = _find_user_in_msgs(name_part, msgs)
                if uid2 and not is_self(uid2):
                    protagonist_uid = uid2
                    protagonist = full2
                    custom = " ".join(tokens[i:])
                    break
        # 匹配不到 → 整个当自定义剧情，主角随机
        if not protagonist_uid:
            custom = rest
    # 未指定主角 → 随机抽一个（排除发起者）
    if not protagonist_uid:
        protagonist_uid = _random_member_uid(exclude={sender_uid})
        protagonist = get_full_name(protagonist_uid) if protagonist_uid else ""
    if not protagonist_uid:
        return "群里还没有可参与的人呢，先聊几句再来开剧情～"
    opening = generate_story_start(protagonist, custom)
    opening = (opening or "").strip()
    _set_story_state(cid, {
        "active": True,
        "phase": "等投票",
        "round": 1,
        "max_round": STORY_MAX_ROUND,
        "protagonist_uid": protagonist_uid,
        "protagonist": protagonist,
        "custom": custom,
        "votes": {},
        "vote_deadline": time.time() + STORY_VOTE_SECONDS,
        "remind_30": False,
        "remind_10": False,
        "history": [opening],
    })
    header = "🎬 剧情开始！女主：" + protagonist
    footer = "（" + str(STORY_VOTE_SECONDS) + "秒内 /选 1/2/3 或 /1/2/3 投票，可改票～）"
    return header + "\n" + opening + "\n" + footer


def _check_story(cid: str) -> str:
    """剧情状态流转：提醒投票 / 推进剧情 / 结局。返回要发送的文本（空=无需发送）."""
    story = _get_story_state(cid)
    if not story.get("active") or story.get("phase") != "等投票":
        return ""
    now = time.time()
    remaining = story.get("vote_deadline", 0) - now
    if 10 < remaining <= 30 and not story.get("remind_30"):
        story["remind_30"] = True
        _set_story_state(cid, story)
        return "⏰ 还有 30 秒，想投的快 /选 1/2/3 哦～"
    if 0 < remaining <= 10 and not story.get("remind_10"):
        story["remind_10"] = True
        _set_story_state(cid, story)
        return "⏰ 还有 10 秒，最后机会啦～"
    if remaining <= 0:
        return _advance_story(cid)
    return ""


def _finish_story(cid: str) -> str:
    """生成结局 + 结算 + 清空状态，返回结局消息."""
    story = _get_story_state(cid)
    if not story.get("active"):
        return ""
    history = story.get("history") or []
    history_text = "\n\n".join(history) if history else ""
    ending = generate_story_end(history_text)
    ending = (ending or "").strip()
    votes = story.get("votes") or {}
    protagonist_uid = story.get("protagonist_uid", "")
    protagonist = story.get("protagonist", "")
    for uid in votes.keys():
        if uid and not is_self(uid):
            _add_heat(cid, uid, HEAT_STORY_VOTER)
    if protagonist_uid and not is_self(protagonist_uid):
        _add_heat(cid, protagonist_uid, HEAT_STORY_PROTAGONIST)
    _set_story_state(cid, {})
    lines = ["🎬 剧情结束！女主：" + protagonist]
    if ending:
        lines.append(ending)
    if votes:
        lines.append("🎉 结算：参与投票的各 +8，女主 +15")
    return "\n".join(lines)


def _advance_story(cid: str) -> str:
    """推进剧情：统计投票，生成下一段或结局。返回要发送的文本."""
    story = _get_story_state(cid)
    if not story.get("active"):
        return ""
    round_no = story.get("round", 1)
    max_round = story.get("max_round", STORY_MAX_ROUND)
    history = story.get("history") or []
    choice = _story_vote_result(cid)
    choice_label = "选项" + choice
    history_text = "\n\n".join(history) if history else ""
    if round_no >= max_round:
        return _finish_story(cid)
    # 推进：生成下一段（结尾必须带【1】【2】【3】选项，缺了就重试）
    next_part = generate_story_step(history_text, choice_label)
    next_part = (next_part or "").strip()
    for _ in range(2):
        if "【1】" in next_part and "【2】" in next_part and "【3】" in next_part:
            break
        retry = generate_story_step(history_text, choice_label)
        retry = (retry or "").strip()
        if retry:
            next_part = retry
    story["history"] = history + [next_part]
    story["round"] = round_no + 1
    story["votes"] = {}
    story["vote_deadline"] = time.time() + STORY_VOTE_SECONDS
    story["remind_30"] = False
    story["remind_10"] = False
    _set_story_state(cid, story)
    footer = "（" + str(STORY_VOTE_SECONDS) + "秒内 /选 1/2/3 或 /1/2/3 投票，可改票～）"
    return next_part + "\n" + footer


def _record_story_vote(cid: str, uid: str, choice: str) -> None:
    """记录投票（以最后一次为准），静默不回复."""
    story = _get_story_state(cid)
    if not story.get("active"):
        return
    votes = story.get("votes") or {}
    votes[uid] = choice
    story["votes"] = votes
    _set_story_state(cid, story)


def _story_vote_result(cid: str) -> str:
    """统计投票，返回本轮选中选项(1/2/3)。平票随机、无人投随机兜底."""
    story = _get_story_state(cid)
    votes = story.get("votes") or {}
    if not votes:
        return random.choice(["1", "2", "3"])
    counts = {}
    for v in votes.values():
        key = "1" if v in ("1", "A") else ("2" if v in ("2", "B") else "3")
        counts[key] = counts.get(key, 0) + 1
    max_n = max(counts.values())
    top = [k for k, n in counts.items() if n == max_n]
    return random.choice(top)
