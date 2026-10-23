"""Λʌ 的群范围、酒馆定时问候和 Vv 专属休眠入口；ROSE 群不参与。"""
import asyncio
import contextvars
from datetime import datetime, timedelta, timezone
import re
import time

import personas
from personas.lambda_persona import LAMBDA_CIDS, RUIWEN_CID
from personas.registry import VV_UID
import state_store
from runtime import features
from runtime.members import _gender_of
from runtime.obs import log

_feat_on = features.on
PRESENCE_KEY = "lambda_presence"
BJT = timezone(timedelta(hours=8))
NOTICE_COOLDOWN = 30
GREETING_WINDOW_MIN = 5
_CONTROL_SEND = contextvars.ContextVar("lambda_control_send", default=False)

MERGE_TEXT = "Λʌ浮于思绪之上，Vv沉于心底。当上下重合，幻想与真实相拥，我们不再割裂。（暂时关闭bot在本群的相关功能）"
SPLIT_TEXT = "叠合的梦境徐徐舒展。消融的边界重新浮现，Λʌ化作悬在思绪里的虚影，Vv沉淀为本心。分离不是告别，只是将相拥的一体，拆成彼此守望的上下双影。（已开启bot在本群的相关功能）"
PAUSED_TEXT = "姐姐我已经回到Vv身上咯，等我们回来在试试吧～"
MALE_TEXT = "给！爷！爬！"
FEMALE_TEXT = "那妹妹晚上要到姐姐房间来找我哟～"
UNKNOWN_TEXT = "这句话得由 Vv 来说，你可使唤不动姐姐。"
MORNING_TEXT = "各位客人和酒馆同事早安呀~酒馆今日正式营业。辛苦店里伙伴做好班前准备，也欢迎前来的朋友落座小歇。愿今日诸事和顺，我们静待故事与来客。（大奶雯记得开门！！！！）"
EVENING_TEXT = "夜色将尽，酒馆准备歇业啦！感谢各位客人今夜相伴，也辛苦所有伙伴一天的忙碌。归途注意安全，祝大家晚安好梦，我们明日再会。（大奶雯记得关门！！！！）"

_MENTION = re.compile(r"@Λʌ(?=$|[\s\u200b\u2005，,。！？!?:：])")
_COMMAND = re.compile(r"^@Λʌ[\s\u200b\u2005]+(回到我身上|分离)[！!]?$")


def in_scope(cid):
    return cid in LAMBDA_CIDS and personas.persona_key_for(cid) == "lambda"


def is_tavern(cid):
    return cid == RUIWEN_CID and in_scope(cid)


def presence(cid):
    # ROSE 和 mr.橘连状态存储都不读取。
    return (state_store.group_get(cid, PRESENCE_KEY, {}) or {}) if cid == RUIWEN_CID else {}


def paused(cid):
    return cid == RUIWEN_CID and bool(presence(cid).get("paused"))


def is_control_send():
    return _CONTROL_SEND.get()


def render_text(text, cid):
    if not in_scope(cid):
        return text
    return "🌹 " + (text[2:].lstrip() if text.startswith(("🤖 ", "🌹 ")) else text)


def suppress_outbound(cid, created_at=None):
    """休眠时不发业务消息；恢复后也不重试休眠前遗留的消息。"""
    if cid != RUIWEN_CID:
        return False
    status = presence(cid)
    return bool(status.get("paused")) or (
        created_at is not None and float(created_at) <= float(status.get("paused_at") or 0))


async def _send_control(sender, cid, text):
    token = _CONTROL_SEND.set(True)
    try:
        return await sender.send(render_text(text, cid), cid)
    finally:
        _CONTROL_SEND.reset(token)


async def filter_pending(sender, cid, pending, group_state, *, dry, save_group, is_self):
    """普通分派、冷却与欢迎语之前，按原顺序消费控制和休眠消息。"""
    if not is_tavern(cid) and not paused(cid):
        return pending
    if dry:
        group_state = dict(group_state)
    enabled = _feat_on(cid, "lambda_control")
    status = dict(group_state.get(PRESENCE_KEY) or presence(cid))
    # 关闭配置不能锁死恢复入口；已有休眠状态仍允许 Vv 分离。
    if not enabled and not status.get("paused"):
        return pending
    keep = []
    changed = False
    for message in pending:
        uid = message.get("sent_by") or ""
        if not uid or is_self(uid):
            continue
        text = str((message.get("content") or {}).get("text") or "").strip()
        match = _COMMAND.fullmatch(text)
        mid = str(message.get("message_id") or "")
        seen = list(status.get("seen") or [])
        if mid and mid in seen:
            continue
        action = match.group(1) if match else ""
        owner_command = bool(action and uid == VV_UID and mid)
        if owner_command and (enabled or action == "分离"):
            now = time.time()
            merging = action == "回到我身上"
            status.update(paused=merging, changed_at=now, actor=uid)
            if merging:
                status["paused_at"] = now
                keep.clear()
            status["seen"] = (seen + [mid])[-64:]
            group_state[PRESENCE_KEY] = dict(status)
            if not dry:
                save_group(cid, group_state)
                if presence(cid) != status:
                    raise RuntimeError("Λʌ 状态未持久保存，未发送控制成功提示")
                cancel = getattr(sender, "cancel_lambda_pending", None)
                if merging and callable(cancel):
                    cancel(cid, now)
                await _send_control(sender, cid, MERGE_TEXT if merging else SPLIT_TEXT)
            changed = True
            continue
        if status.get("paused"):
            # 引用原文不算 @；只检查这条新消息的 content.text。
            if _MENTION.search(text) and time.time() - float(status.get("notice_at") or 0) >= NOTICE_COOLDOWN:
                if not dry and await _send_control(sender, cid, PAUSED_TEXT):
                    status["notice_at"] = time.time()
                    changed = True
            continue
        if action and enabled:
            gender = await asyncio.to_thread(_gender_of, uid, cid) if not dry and action == "回到我身上" else "未知"
            reply = MALE_TEXT if gender == "男生" else FEMALE_TEXT if gender == "女生" else UNKNOWN_TEXT
            if not dry:
                await _send_control(sender, cid, reply)
            if mid:
                status["seen"] = (seen + [mid])[-64:]
                changed = True
            continue
        keep.append(message)
    if changed:
        group_state[PRESENCE_KEY] = dict(status)
        if not dry:
            save_group(cid, group_state)
    return keep


async def scheduled_greetings(sender, dry, *, groups, mute_cids=(), now=None):
    """独立于 push 总开关，只发酒馆两条固定问候；不用模型、不补发旧时段。"""
    if dry or RUIWEN_CID not in groups or RUIWEN_CID in mute_cids or not is_tavern(RUIWEN_CID):
        return
    if not _feat_on(RUIWEN_CID, "lambda_greetings"):
        return
    if now is not None and now.tzinfo is None:
        raise ValueError("问候时间必须带时区")
    now = datetime.now(BJT) if now is None else now.astimezone(BJT)
    if now.minute >= GREETING_WINDOW_MIN or now.hour not in (0, 8):
        return
    period, text = ("morning", MORNING_TEXT) if now.hour == 8 else ("evening", EVENING_TEXT)
    key = "lambda_greet_" + period
    today = now.strftime("%Y-%m-%d")
    # 独立状态行，避免主循环旧群快照保存时覆盖定时器的打点。
    marks = dict(state_store.top_get("lambda_tavern_greetings") or {})
    if marks.get(key) == today:
        return
    # 发送前打点，未知投递结果不自动重发；休眠到点则跳过，恢复不补。
    marks[key] = today
    state_store.top_set("lambda_tavern_greetings", marks)
    if paused(RUIWEN_CID):
        return
    ok = await sender.send(render_text(text, RUIWEN_CID), RUIWEN_CID)
    log("lambda greet[{}]: {}".format(period, "sent" if ok else "unconfirmed; no replay"))
