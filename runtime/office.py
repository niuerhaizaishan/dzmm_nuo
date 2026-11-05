"""茶水间专用命令。模型在后台执行，允许拒绝/结束在生成期间立即生效。"""
import asyncio
import hashlib
import json
import re
import shlex

import personas
from runtime import features
from runtime.employee_api import Client, EmployeeUnavailable
from runtime.identity import get_full_name, is_self
from runtime.members import _gender_of
from runtime.moderation_platform import Platform, ModerationUnavailable
from runtime.obs import log
from runtime.office_scene import SceneGenerationError, generate
from runtime.office_store import RuleError, Store, TEA_CID
from runtime.reply_delivery import ReplyContentRejected, ReplyNotSent
from runtime.text import _normalize_command_prefix

COMMAND = re.compile(r"^#(允许潜规则|拒绝潜规则|潜规则状态|潜规则继续|潜规则结束|潜规则|继续|结束)(?=$|\s)(.*)$", re.S)
TASKS = set()
_feat_on = features.on
RANK_DENIED = "胆子挺大呀，领导都敢点？小小糯替你把申请撤回了，快去努力升职～"
EQUAL_DENIED = "工牌摆一起，谁也管不了谁呀～只能向职级比自己低的成员发起哦。"
USAGE = "发送 #潜规则 @目标 [剧情方向]，也可用工号，或引用对方消息发送 #潜规则 [剧情方向]；可跨部门，仅限职级低于自己的成员。"


def command(text):
    match = COMMAND.fullmatch(_normalize_command_prefix(text))
    return ({"继续": "潜规则继续", "结束": "潜规则结束"}.get(match[1], match[1]), match[2].strip()) if match else None


def enabled(cid):
    return cid == TEA_CID and personas.persona_key_for(cid) == "nuo" and _feat_on(cid, "office")


def reference_of(message):
    content = message.get("content") or {}
    return {"id": message.get("message_id", ""), "sentBy": message.get("sent_by", ""),
            "content": {"type": "text", "text": str(content.get("text") or "")[:2000]}}


def split_request(payload):
    try:
        tokens = shlex.split(payload, posix=True)
    except ValueError:
        raise RuleError("目标格式不完整，请使用 @目标 或工号；带空格的名字可加双引号。") from None
    if not tokens:
        raise RuleError(USAGE)
    return tokens[0], " ".join(tokens[1:])


async def quoted_target(cid, message):
    content = (message or {}).get("content") or {}
    quoted = next((content[key] for key in ("reference", "quote", "reply") if key in content), None)
    if quoted is None:
        return None
    if not isinstance(quoted, dict):
        raise RuleError("引用消息格式不完整，请重新引用对方的群消息再试。")
    mid = quoted.get("id") or quoted.get("message_id")
    if not isinstance(mid, str) or not mid.strip():
        raise RuleError("引用消息缺少编号，请重新引用对方的群消息再试。")
    stamp = quoted.get("sent_at") or quoted.get("sentAt") or ""
    try:
        original = await Platform().message(cid, mid, stamp if isinstance(stamp, str) else "")
    except ModerationUnavailable:
        raise EmployeeUnavailable("暂时无法核验引用的本群原消息，请重新引用后再试，不会扣次数。") from None
    # 引用中的 sentBy、原文及嵌套引用均不能作为身份依据，必须核验本群原消息作者。
    target = original.get("sent_by")
    if (original.get("message_id") != mid or original.get("chatroom_id") not in (None, cid)
            or not isinstance(target, str) or not target.strip()):
        raise RuleError("引用消息无法核验为本群成员的原消息，请重新引用后再试。")
    return target


def eligibility(actor, target):
    if actor["rank_level"] < target["rank_level"]:
        raise RuleError(RANK_DENIED)
    if actor["rank_level"] == target["rank_level"]:
        raise RuleError(EQUAL_DENIED)


async def reply(sender, cid, text, reference, *, guard=None, strict=False):
    # 原生引用区分作者；正文也区分指令，避免多人收到同一提示被平台判为重复内容。
    name = " ".join(get_full_name(reference.get("sentBy", "")).split())[:24]
    request_id = hashlib.sha256(reference["id"].encode()).hexdigest()[:8]
    payload = "🤖 【回复 {} · 指令 {}】 {}".format(name, request_id, " ".join(text.split()))
    try:
        # 无引用能力时不退回普通文本，更不能拆分。
        return await sender.send_reply_with_id(payload, cid, reference, guard=guard)
    except Exception as error:
        log("office_reply 未确认 [{}] {}".format(cid[:8], type(error).__name__))
        if strict:
            raise
        return ""


async def _profiles(cid, actor, target, client):
    try:
        members = await Platform().members(cid, limit=2000)
    except ModerationUnavailable:
        raise EmployeeUnavailable("暂时无法核验本群成员，请稍后再试，不会扣次数。") from None
    if (actor not in members or target not in members or actor == target
            or members[actor]["protected"] or members[target]["protected"] or is_self(target)):
        raise RuleError("请指定本群另一位已入职成员，不能选择自己或机器人。")
    first = await asyncio.to_thread(client.profile, actor)
    second = await asyncio.to_thread(client.profile, target)
    eligibility(first, second)
    # 集成接口未提供性别时，沿用原场景使用的档案/平台资料识别；不从昵称猜测。
    for person in (first, second):
        if person["gender"] == "未知":
            gender = await asyncio.to_thread(_gender_of, person["platform_id"], cid, False)
            person["gender"] = {"男生": "男", "女生": "女"}.get(gender, "未知")
    return [first, second]


async def _run(sender, cid, uid, action, payload, reference, store, client, *, message=None):
    sid = operation = None
    try:
        if action == "潜规则":
            target = await quoted_target(cid, message)
            if target is not None:
                direction = payload  # 引用模式的全部参数都是剧情方向；不再从中猜目标。
            else:
                target_text, direction = split_request(payload)
                # 平台稳定 UID 可直接定位；显示名必须走唯一匹配再核验本群成员。
                raw_uid = target_text.lstrip("@")
                if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", raw_uid):
                    target = raw_uid
                else:
                    target = await asyncio.to_thread(client.match, target_text)
            profiles = await _profiles(cid, uid, target, client)
            if not enabled(cid):
                return
            sid, operation = store.start(cid, uid, target, reference, profiles)
        else:
            row = store.current(cid, uid)
            if not row or row["status"] != "active":
                raise RuleError("当前没有可以继续的会话，可能已经结束或正在处理上一段。")
            try:
                profiles = await _profiles(cid, row["actor"], row["target"], client)
            except RuleError:
                store.end(cid, uid, "ineligible", expected_sid=row["id"])
                raise
            if not enabled(cid):
                return
            reservation = store.begin_continue(cid, uid, reference, profiles, expected_sid=row["id"])
            if reservation is None:
                return
            sid, operation = reservation
            direction = payload
        row = store.get(sid)
        names = [" ".join(person["name"].split())[:24] for person in profiles]
        session_title = "【会话 {}｜{} × {}】".format(sid[:8], *names)
        text = await asyncio.wait_for(asyncio.to_thread(
            generate, profiles, json.loads(row["history"]), direction), timeout=50)
        if not enabled(cid):
            store.end(cid, uid, "disabled", expected_sid=sid)
            return
        if not store.ready(sid, operation, text):
            return

        def guard():
            return enabled(cid) and bool(store.dispatch(sid, operation))

        mid = ""
        for attempt in range(3):
            try:
                mid = await reply(sender, cid, session_title + "\n" + text, reference, guard=guard, strict=True)
                break
            except ReplyContentRejected:
                # 明确拒绝内容时不改文案重发、不重复撞同一拒绝；释放尚未投递的首段额度。
                if store.retry_delivery(sid, operation) and store.fail_generation(sid, operation):
                    await reply(sender, cid, "平台拒绝了本段内容，本次未发送；未成功发送的首段不扣次数。", reference)
                return
            except ReplyNotSent:
                if not store.retry_delivery(sid, operation):
                    return
                if attempt == 2:
                    if store.fail_generation(sid, operation):
                        await reply(sender, cid, "本次消息未能投递，未成功发送的首段不扣次数，请稍后再试。", reference)
                    return
                await asyncio.sleep(attempt + 1)
            except Exception:
                store.ambiguous(sid, operation)
                return  # 不确定是否落地，绝不重新生成、改通道或盲目重发。
        if mid:
            store.delivered(sid, operation, mid)
        else:
            store.ambiguous(sid, operation)
    except asyncio.CancelledError:
        if sid:
            store.fail_generation(sid, operation)
        raise
    except (RuleError, EmployeeUnavailable) as error:
        if sid:
            store.fail_generation(sid, operation)
        await reply(sender, cid, str(error), reference)
    except SceneGenerationError as error:
        log("office_model 失败 [{}] 指令={} reason={} chars={} finish={} attempts={}".format(
            cid[:8], reference["id"][:8], error.reason, error.length, error.finish, error.attempts))
        if sid and not store.fail_generation(sid, operation):
            return
        hint = ("当前剧情生成繁忙，请稍后再试；未成功发送的首段不扣次数。"
                if error.reason == "office_model_busy" else
                "本段内容过短、过长或未完整结束，已取消发送；未成功发送的首段不扣次数。")
        await reply(sender, cid, hint, reference)
    except Exception as error:
        log("office_task 失败 [{}] {}".format(cid[:8], type(error).__name__))
        if sid and not store.fail_generation(sid, operation):
            return  # 已拒绝或结束：不再冒出旧任务回复。
        await reply(sender, cid, "这次剧情没能生成，未成功发送的首段不会扣次数，请稍后再试。", reference)


async def handle(sender, cid, uid, text, message, *, dry=False, store=None, client=None):
    parsed = command(text)
    if parsed is None or not enabled(cid) or dry:
        return
    store = store or Store()
    ref = reference_of(message)
    if (not uid or message.get("sent_by") != uid or message.get("chatroom_id") not in (None, cid)
            or not store.claim_command(cid, ref["id"])):
        return
    action, payload = parsed
    if len(payload) > 1000:
        await reply(sender, cid, "剧情方向最多 1000 字，请精简后再发。", ref)
        return
    if action != "潜规则" and action != "潜规则继续" and payload:
        await reply(sender, cid, "这条命令不需要额外参数哦。", ref)
        return
    if action in ("允许潜规则", "拒绝潜规则"):
        allow = action == "允许潜规则"
        store.preference(cid, uid, allow)
        if not allow:
            for task in tuple(TASKS):
                if getattr(task, "office_owner", None) == (cid, uid):
                    task.cancel()
        await reply(sender, cid, "已允许职级更高的成员向你发起，可跨部门。" if allow else
                    "已拒绝潜规则；你参与的会话已结束，尚未发送的内容已取消。", ref)
    elif action == "潜规则状态":
        status = store.status(cid, uid)
        await reply(sender, cid, "当前{}；今日剩余 {} 次；{}。".format(
            "允许" if status["allowed"] else "拒绝", status["remaining"],
            "有进行中的会话" if status["active"] else "没有进行中的会话"), ref)
    elif action == "潜规则结束":
        ended = store.end(cid, uid)
        for task in tuple(TASKS):
            if getattr(task, "office_owner", None) == (cid, uid):
                task.cancel()
                ended = True
        await reply(sender, cid, "本场结束。" if ended else "你当前没有进行中的会话。", ref)
    else:
        if len(TASKS) >= 4:
            await reply(sender, cid, "小小糯正在处理其他剧情，稍后再试，不会扣次数。", ref)
            return
        task = asyncio.create_task(_run(sender, cid, uid, action, payload, ref, store, client or Client(),
                                        message=message))
        task.office_owner = (cid, uid)
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)


async def tick(sender, cid, *, dry=False, store=None, now=None):
    if cid != TEA_CID or dry:
        return
    store = store or Store()
    # 默认关闭且从未开启时不创建新的运行数据。
    if not enabled(cid) and not store.path.exists():
        return
    for notice in store.sweep(cid, now=now, enabled=enabled(cid)):
        mid = await reply(sender, cid, notice["text"], json.loads(notice["reference"]))
        store.notice_result(notice["id"], bool(mid))
