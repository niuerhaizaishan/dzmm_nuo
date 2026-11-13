"""Rose 专用的管理员确认工作流，不调用模型、不自动重放管理动作。"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from personas.lambda_persona import RUIWEN_CID
from runtime import features
from runtime.moderation_platform import ModerationUnavailable, Platform
from runtime.moderation_store import Store
from runtime.obs import log

ROSE_CID = "4a56be71-9ca6-4785-b45f-18725d187f1a"
CREATE = "/阿瓦达啃大瓜"
CONFIRM = "/确认"
CANCEL = "/取消"
SUCCESS = "Avada Kedavra！掏出绿色震动棒开始挥舞，一阵绿色光束之后，中者当场消失不留下任何痕迹！（已删除其消息并且踢出群聊)"
KICK_SUCCESS = "Avada Kedavra！{name}已被踢出群聊。"
_feat_on = features.on
_service = None


def _parse(cid, text):
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    head, name = parts[0], parts[1] if len(parts) == 2 else ""
    if cid == RUIWEN_CID:
        if head.startswith(("#", "＃")):
            action = "/" + head[1:]
            if action == CREATE or (action in (CONFIRM, CANCEL) and not name):
                return action, name
    elif head in (CREATE, CONFIRM, CANCEL) and not name:
        return head, ""
    return None


def _syntax(cid, text):
    return text.replace("/", "#") if cid == RUIWEN_CID else text


def is_command(cid, text):
    """哪个群开了 `rose_moderation` 功能，哪个群的这三条指令就生效
    （2026-10-12 起**不再硬编码单群**：Rose 之外，屋主给 Mr.橘的淫窝、瑞雯の酒馆
    也开了这个功能）。功能关着时指令按普通斜杠指令走原有分派。"""
    if not cid or _parse(cid, text) is None:
        return False
    try:
        return bool(_feat_on(cid, "rose_moderation"))
    except Exception:                                           # noqa: BLE001
        return False


def reference(message):
    content = message.get("content") or {}
    quoted = content.get("reference") or content.get("quote") or content.get("reply")
    if not isinstance(quoted, dict):
        return ""
    return str(quoted.get("id") or quoted.get("message_id") or "")


def reference_time(message):
    content = message.get("content") or {}
    quoted = content.get("reference") or content.get("quote") or content.get("reply")
    if not isinstance(quoted, dict):
        return ""
    return str(quoted.get("sent_at") or quoted.get("sentAt") or "")


def reason(error):
    return str(error) if isinstance(error, ModerationUnavailable) else type(error).__name__


def brief(task):
    created = datetime.fromtimestamp(task["created"], timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    name = " ".join(task["name"].split())[:60]
    return f"{name}（{task['uid'][:8]}，{created}）：{task['excerpt']}"


class Service:
    def __init__(self, store, platform):
        self.store = store
        self.platform = platform
        self.lock = asyncio.Lock()

    async def notify(self, sender, cid, text):
        """提示/结果一律走账号通道（小小糯～），与撤回/踢出同一执行者 ——
        见 `RoutingSender.send_with_id` 的说明（屋主 2026-10-12）。"""
        try:
            message_id = await sender.send_with_id(text, cid)
            return message_id if isinstance(message_id, str) else ""
        except Exception as error:
            log("Rose 管理提示发送未确认：" + type(error).__name__)
            return ""

    async def handle(self, sender, cid, message, dry=False):
        text = str((message.get("content") or {}).get("text") or "").strip()
        if not is_command(cid, text):
            return False
        if dry:
            log("[dry] Rose 管理指令：" + text)
            return True
        actor = message.get("sent_by") or ""
        message_id = message.get("message_id") or ""
        if not actor or not message_id:
            return True
        async with self.lock:
            try:
                await self.platform.authorize(cid, actor)
            except Exception as error:
                await self.notify(sender, cid, "未执行：" + reason(error))
                return True
            if not self.store.command(cid, message_id, actor):
                return True
            try:
                action, name = _parse(cid, text)
                await self.process(sender, cid, actor, action, reference(message), reference_time(message),
                                   name=name, command_id=message_id)
            except ModerationUnavailable as error:
                await self.notify(sender, cid, "未执行：" + reason(error))
            except Exception as error:
                log("Rose 管理流程中断：" + type(error).__name__)
                await self.notify(sender, cid, "操作未完成，已开始的任务不会自动重试，请人工核查。")
        return True

    async def process(self, sender, cid, actor, command, quoted_id, quoted_time="", *, name="", command_id=""):
        if command == CREATE:
            if name:
                if quoted_id:
                    raise ModerationUnavailable("请只选一种方式：回复原消息，或空格加对方全名")
                members = await self.platform.members(cid)
                matches = [uid for uid, row in members.items() if row["name"] == name and uid != name]
                if len(matches) != 1:
                    raise ModerationUnavailable("有同名成员，请回复目标原消息" if matches else "未找到全名完全一致的当前群成员")
                uid = matches[0]
                current_name = await self.platform.authorize(cid, actor, uid)
                if current_name != name:
                    raise ModerationUnavailable("目标名字已变更，请重新核对")
                task, _ = self.store.create_kick(cid, command_id, uid, name, actor)
                if task["status"] != "prepared":
                    await self.notify(sender, cid, "该成员已有待处理任务，请回复原确认提示处理。")
                    return
                prompt = "确认将以下成员踢出群聊？\n" + brief(task) + _syntax(cid, "\n回复本条消息发送 /确认 或 /取消。")
                prompt_id = await self.notify(sender, cid, prompt)
                if prompt_id:
                    self.store.bind("task", task["id"], prompt_id)
                return
            if not quoted_id:
                usage = "请回复要处理的原消息，再发送 /阿瓦达啃大瓜。"
                if cid == RUIWEN_CID:
                    usage += "也可发送 /阿瓦达啃大瓜 全名，只踢人。"
                await self.notify(sender, cid, _syntax(cid, usage))
                return
            existing = self.store.read("SELECT * FROM tasks WHERE cid=? AND message_id=? AND status != 'cancelled'",
                                       (cid, quoted_id))
            if existing and existing[0]["status"] != "prepared":
                await self.notify(sender, cid, "这条消息已有任务，状态：" + existing[0]["status"] +
                                  "。请回复原确认提示处理。")
                return
            sent_at = existing[0]["sent_at"] if existing else quoted_time
            original = await self.platform.message(cid, quoted_id, sent_at)
            uid = original.get("sent_by")
            if not uid or original.get("message_id") != quoted_id:
                raise ModerationUnavailable("原消息身份无法核验")
            name = await self.platform.authorize(cid, actor, uid)
            task, created = self.store.create(cid, original, name, actor)
            if not created and task["status"] != "prepared":
                await self.notify(sender, cid, "这条消息已有任务，状态：" + task["status"] +
                                  "。请回复原确认提示处理。")
                return
            prompt = "识别到食死徒，是否对其使用杀戮咒？\n" + brief(task) + \
                     _syntax(cid, "\n回复本条消息发送 /确认 或 /取消。")
            prompt_id = await self.notify(sender, cid, prompt)
            if prompt_id:
                self.store.bind("task", task["id"], prompt_id)
            return

        kind, item = self.store.reference(cid, quoted_id) if quoted_id else (None, None)
        if quoted_id and item is None:
            await self.notify(sender, cid, "这条消息不是管理确认提示，未执行操作。")
            return
        if command == CANCEL and not quoted_id:
            count = self.store.cancel_all(cid, actor)
            await self.notify(sender, cid, f"已取消 {count} 条待处理任务；正在执行的任务不会被取消。")
            return
        if item is not None:
            if kind == "task" and item["status"] != "pending":
                await self.notify(sender, cid, "任务状态：" + item["status"] + "。" +
                                  (item.get("result") or "") + "不会重复执行。")
                return
            task_ids = [item["id"]] if kind == "task" else json.loads(item["task_ids"])
            selected = self.store.select(cid, task_ids, actor, command == CANCEL,
                                         item["id"] if kind == "batch" else None)
        else:
            batch = self.store.active_batch(cid)
            if batch:
                selected = self.store.select(cid, json.loads(batch["task_ids"]), actor,
                                             batch_id=batch["id"])
            else:
                pending = self.store.pending(cid)
                if len(pending) > 1:
                    await self.preview(sender, cid, pending)
                    return
                selected = self.store.select(cid, [task["id"] for task in pending], actor)
        if not selected:
            await self.notify(sender, cid, "没有可处理的任务；已执行、已取消或执行中任务不会重复处理。")
        elif command == CANCEL:
            await self.notify(sender, cid, f"已取消 {len(selected)} 条待处理任务。")
        else:
            await self.execute(sender, cid, actor, selected)

    async def preview(self, sender, cid, tasks):
        batch_id = self.store.batch(cid, tasks)
        lines = [f"{number}. {brief(task)}" for number, task in enumerate(tasks, 1)]
        chunks = []
        current = ""
        for line in lines:
            if len(current) + len(line) > 7000:
                chunks.append(current)
                current = ""
            current += line + "\n"
        chunks.append(current)
        prompt_ids = []
        for number, chunk in enumerate(chunks, 1):
            prompt = f"待确认汇总：共 {len(tasks)} 条（{number}/{len(chunks)}）\n" + chunk + \
                     _syntax(cid, "再次直接发送 /确认 执行本次汇总；/取消 取消全部。也可回复单条提示分别处理。")
            sent_id = await self.notify(sender, cid, prompt)
            if not sent_id:
                return
            prompt_ids.append(sent_id)
        self.store.bind("batch", batch_id, prompt_ids[-1], prompt_ids[:-1])

    async def execute(self, sender, cid, actor, tasks):
        users = {}
        for task in tasks:
            users.setdefault(task["uid"], []).append(task)
        succeeded = 0
        recalled_and_kicked = False
        failures = []
        for uid, user_tasks in users.items():
            recalled = []
            for task in user_tasks:
                if task.get("action") == "kick":
                    continue
                stage = "撤回前"
                try:
                    await self.platform.authorize(cid, actor, uid)
                    original = await self.platform.message(cid, task["message_id"], task["sent_at"])
                    if original.get("sent_by") != uid or original.get("message_id") != task["message_id"]:
                        raise ModerationUnavailable("原消息身份发生变化")
                    stage = "撤回结果未确认"
                    self.store.progress(task["id"], stage)
                    await self.platform.recall(cid, task["message_id"])
                    self.store.progress(task["id"], "撤回已确认，尚未完成踢出")
                    recalled.append(task)
                except Exception as error:
                    detail = stage + "：" + reason(error)
                    self.store.finish(task["id"], "failed" if stage == "撤回前" else "uncertain", detail)
                    failures.append(brief(task) + "\n" + detail)
            message_tasks = [task for task in user_tasks if task.get("action") != "kick"]
            if len(recalled) != len(message_tasks):
                for task in recalled + [task for task in user_tasks if task.get("action") == "kick"]:
                    did_recall = task in recalled
                    detail = "已撤回；同一用户另有消息撤回失败，未执行踢出" if did_recall else "同一用户有消息撤回失败，未执行踢出"
                    self.store.finish(task["id"], "partial" if did_recall else "failed", detail)
                    failures.append(brief(task) + "\n" + detail)
                continue
            stage = "踢出前"
            try:
                current_name = await self.platform.authorize(cid, actor, uid)
                stage = "踢出结果未确认"
                for task in user_tasks:
                    self.store.progress(task["id"], ("已撤回，" if task in recalled else "") + stage)
                await self.platform.kick(cid, uid)
                for task in user_tasks:
                    self.store.finish(task["id"], "done", "撤回与踢出均已确认" if task in recalled else "踢出已确认")
                succeeded += len(user_tasks)
                recalled_and_kicked |= bool(recalled)
                if len(recalled) != len(user_tasks):
                    await self.notify(sender, cid, KICK_SUCCESS.format(name=current_name))
            except Exception as error:
                for task in user_tasks:
                    did_recall = task in recalled
                    detail = ("已撤回，" if did_recall else "") + stage + "：" + reason(error)
                    status = ("partial" if did_recall else "failed") if stage == "踢出前" else "uncertain"
                    self.store.finish(task["id"], status, detail)
                    failures.append(brief(task) + "\n" + detail)
        if recalled_and_kicked:
            await self.notify(sender, cid, SUCCESS)
        if len(tasks) > 1:
            await self.notify(sender, cid, f"本次汇总：{succeeded} 条完成，{len(tasks) - succeeded} 条未完成。")
        for failure in failures:
            await self.notify(sender, cid, "未全部完成：" + failure + "\n不会自动重试，请人工核查。")


async def handle(sender, cid, message, dry=False):
    global _service
    if not is_command(cid, str((message.get("content") or {}).get("text") or "")):
        return False
    try:
        enabled = _feat_on(cid, "rose_moderation")
    except Exception:
        enabled = False
    if not enabled:
        return True
    if dry:
        log("[dry] Rose 管理指令（无副作用）")
        return True
    if _service is None:
        path = Path(__file__).resolve().parent.parent / "data" / "rose_moderation.db"
        _service = Service(Store(path), Platform())
    return await _service.handle(sender, cid, message)
