"""调教（P8 第 4 步从 bot.py 搬出）。

**这是 5 个状态机里最后一个、也是最少动的一个。**

`is_dj_command_cmd` 刻意**不搬**：它不是调教的一部分，而是**总分发口** ——
它调用另外 4 个状态机的 20 个谓词函数（`is_fasao_cmd` / `is_perf_start_cmd` /
`is_vote_start_cmd` / `is_poem_cmd` / `is_recall_cmd` …）。
把它一起搬出去会制造 31 条回指，只是把文字换个位置，不产生任何解耦。
"""
import time
from datetime import datetime, timedelta

from brains import generate_diaojiao, generate_diaojiao_end
from personas import me as _pme, p as _phrase     # 自称/固定话术：按群人设
                                                # ⚠️ 别名别用 `_p`（本文件里
                                                # 有 `x, y = ...` 的解包，容易撞）
from profiles import get_profile
from runtime.tuning import get as _tune   # P12-B：可调参数（唯一读口）
from runtime import ctx
from runtime.fasao import _parse_scene_target
from runtime.features import on as _feat_on     # P12-A：功能开关（唯一判定点）
from runtime.group_state import _get_dj_state, _set_dj_state
from runtime.heat import HEAT_DIAOJIAO_ROUND, _add_heat
from runtime.identity import get_full_name
from runtime.members import _archive_text_from_profile, _member_profile_text
from runtime.memory import _retrieve_memory
from runtime.obs import log
from runtime.text import _is_stop_word


__all__ = [
    "is_dj_end_cmd", "_dj_target_text", "_dj_end_text", "is_diaojiao_cmd",
    "start_diaojiao", "_check_diaojiao", "DJ_PARTICIPATE_HINT",
]


DJ_PARTICIPATE_HINT = "想参与的姐妹也来玩呀～发 /命令 xxx 或直接 /xxx 都行（加不加「命令」都可以）～30秒没人我就自己继续啦～"


# P12-V：调教这一族文案原来**写死在代码里**（"哦～""姐妹""30秒没人命令呀"），
# 换个人设（例如纯 S 的若瑾）就会把小小糯那套软糯口吻漏出来。
# 现在全部走话术表：`dj_*` 这几个 key 的**代码默认值就是上面原文**，
# 所以没配覆盖的群行为一个字不变；要换口吻就在人设话术里覆盖。
def _dj_phrase(cid: str, key: str, fallback: str, **kw) -> str:
    try:
        return _phrase(key, cid, **kw) or fallback
    except Exception:                                          # noqa: BLE001
        return fallback


def _dj_hint(cid: str) -> str:
    return _dj_phrase(cid, "dj_hint", DJ_PARTICIPATE_HINT)


def is_dj_end_cmd(cmd: str) -> bool:
    """识别调教结束：/调教结束、停止、停下、结束 等."""
    return cmd.startswith("/调教结束") or _is_stop_word(cmd)


def _dj_target_text(uid: str) -> str:
    """调教目标资料文本（档案优先，零API）."""
    profile = get_profile(uid)
    if profile:
        return _archive_text_from_profile(profile)
    return _member_profile_text(uid, get_full_name(uid))


def _dj_end_text(target_uid: str, dj: dict) -> str:
    """调教收尾小结文本."""
    try:
        text = generate_diaojiao_end(_dj_target_text(target_uid), dj.get("round", 0))
        return (text or "").strip()
    except Exception:
        return ""


def is_diaojiao_cmd(cmd: str) -> str:
    """识别 /调教 指令，返回 目标+命令 文本（None 表示不是）."""
    for prefix in ["/调教", "/调教开始"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None


def start_diaojiao(cid: str, cmd: str, sender_uid: str) -> str:
    """发起调教，返回第一轮命令消息."""
    rest = is_diaojiao_cmd(cmd)
    if not rest:
        return _dj_phrase(cid, "dj_format",
                          "格式：/调教 目标 命令（例如 /调教 糯糯 学狗叫）")
    target_uid, full, keywords = _parse_scene_target(rest)
    if not target_uid:
        return _dj_phrase(cid, "dj_no_target", "没找到调教目标哦～换个人试试？")
    command = " ".join(keywords)
    if not command:
        # 没指定命令 → 根据发起人 + 目标 XP 自编
        sender_xp = (get_profile(sender_uid) or {}).get("xp") or []
        command = "自编一条命令（结合发起人的性癖：" + ("、".join(sender_xp) if sender_xp else "未知") + "，目标XP见目标资料）"
    _set_dj_state(cid, {
        "target_uid": target_uid,
        "active": True,
        "phase": "等回应",
        "round": 1,
        "last_activity": time.time(),
    })
    try:
        text = generate_diaojiao(_dj_target_text(target_uid), command)
    except Exception:
        text = ""
    text = (text or "").strip()
    if not text:
        text = full + "，" + _pme(cid) + "命令你：" + command + "～"
    head = _dj_phrase(cid, "dj_start", "🔗 调教开始！目标：{who}～", who=full)
    return head + "\n" + text + "\n\n" + _dj_hint(cid)


def _check_diaojiao(cid: str, pending: list) -> str:
    """调教状态流转，返回要发的消息（无则 None）."""
    dj = _get_dj_state(cid)
    if not dj.get("active"):
        return None
    target_uid = dj.get("target_uid")
    now = time.time()
    # 5分钟超时结束
    if now - dj.get("last_activity", now) > _tune("diaojiao.timeout", cid):
        end_text = _dj_end_text(target_uid, dj)
        _set_dj_state(cid, {"active": False})
        msg = _dj_phrase(cid, "dj_timeout", "⏱️ 调教结束啦（5分钟没动静）～")
        if end_text:
            msg += "\n" + end_text
        return msg
    phase = dj.get("phase")
    if phase == "等回应":
        # 检测目标回应（非指令消息）
        for m in pending:
            if m.get("sent_by") == target_uid:
                t = (m.get("content") or {}).get("text", "").strip()
                if t and not t.startswith("/"):
                    dj["phase"] = "等命令"
                    dj["ask_at"] = now
                    dj["last_activity"] = now
                    dj["round"] = dj.get("round", 0) + 1
                    _set_dj_state(cid, dj)
                    _add_heat(cid, target_uid, _tune("heat.diaojiao_round", cid))  # 被调教一轮
                    return _dj_phrase(
                        cid, "dj_replied",
                        "目标回应啦～谁想发下一条命令？发 /命令 xxx 或直接 /xxx 都行"
                        "（目标自己也能发哦～）30秒没人发我就自己来～")
        return None
    if phase == "等命令":
        # 30秒冷场 → 催一句 + 自编命令
        # P12-A：`diaojiao_idle` 关掉时**不做自编**：状态推进到"等回应"照旧
        # （否则这一局会永远卡在"等命令"），只是不再自动生成新命令。
        if now - dj.get("ask_at", now) > _tune("diaojiao.idle_seconds", cid):
            dj["phase"] = "等回应"
            dj["last_activity"] = now
            dj["round"] = dj.get("round", 0) + 1
            _set_dj_state(cid, dj)
            if not _feat_on(cid, "diaojiao_idle"):
                return None
            try:
                text = generate_diaojiao(_dj_target_text(target_uid), "自编一条新命令")
            except Exception:
                text = ""
            text = (text or "").strip()
            return (_dj_phrase(cid, "dj_idle", "30秒没人发命令呀～那我继续啦～")
                    + "\n" + text + "\n\n" + _dj_hint(cid))
        return None
    return None
