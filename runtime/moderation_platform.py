"""Rose 管理动作：账号权限、原消息核验与明确回执。"""
import asyncio
from datetime import datetime, timedelta, timezone

import dzmm
from runtime import identity
from runtime import sender as account_sender


class ModerationUnavailable(RuntimeError):
    """权限或平台状态无法可靠核验。"""


class Platform:
    async def members(self, cid, *, limit=None):
        member_limit = dzmm.MEMBER_FETCH_LIMIT if limit is None else limit
        if isinstance(member_limit, bool) or not isinstance(member_limit, int) or member_limit <= 0:
            raise ModerationUnavailable("成员查询上限配置异常")
        def load():
            chats = dzmm.api("chat.listAll", {"json": None,
                            "meta": {"values": ["undefined"], "v": 1}}, timeout=5)
            room = next((chat for chat in chats if chat.get("id") == cid), None)
            owner = ((room or {}).get("data") or {}).get("creatorId")
            if not owner:
                raise ModerationUnavailable("无法核验群主")
            result = dzmm.api("chatroom.getMembers", {
                "json": {"chatroomId": cid, "limit": member_limit}}, timeout=5)
            rows = result.get("members")
            if (not isinstance(rows, list) or len(rows) >= member_limit
                    or result.get("hasMore") or result.get("nextCursor")):
                raise ModerationUnavailable("无法核验完整成员列表")
            members = {}
            for row in rows:
                if not isinstance(row, dict) or not row.get("id"):
                    raise ModerationUnavailable("成员列表结构异常")
                metadata = row.get("metadata") or {}
                if not isinstance(metadata, dict):
                    raise ModerationUnavailable("成员角色结构异常")
                role = metadata.get("role") or "member"
                if role not in ("member", "admin", "owner"):
                    raise ModerationUnavailable("成员角色未知")
                members[row["id"]] = {
                    "admin": row["id"] == owner or role in ("admin", "owner"),
                    "protected": bool(row.get("isBot")) or identity.is_self(row["id"]),
                    "name": row.get("fullName") or row["id"],
                }
            return members
        return await asyncio.to_thread(load)

    async def authorize(self, cid, actor, target=None):
        members = await self.members(cid)
        if not (members.get(actor) or {}).get("admin"):
            raise ModerationUnavailable("只有当前群管理员可以操作")
        if target is not None:
            if not (members.get(identity.NUO_UID) or {}).get("admin"):
                raise ModerationUnavailable("执行账号尚未获得本群管理权限")
            member = members.get(target)
            if member is None:
                raise ModerationUnavailable("目标已经不在群中")
            if member["admin"] or member["protected"]:
                raise ModerationUnavailable("不能处理群主、管理员或机器人")
            return member["name"]
        return ""

    async def message(self, cid, message_id, sent_at=""):
        def load():
            cursor = None
            if sent_at:
                try:
                    stamp = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    cursor = (stamp + timedelta(seconds=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                except ValueError:
                    raise ModerationUnavailable("原消息时间格式异常")
            for _ in range(1 if sent_at else 10):
                rows = dzmm.get_messages(cid, limit=50, before=cursor, timeout=5)
                for row in rows:
                    if row.get("message_id") == message_id:
                        if row.get("chatroom_id") not in (None, cid):
                            raise ModerationUnavailable("原消息不属于本群")
                        return row
                if not rows or not rows[0].get("sent_at") or rows[0]["sent_at"] == cursor:
                    break
                cursor = rows[0]["sent_at"].replace("+00:00", "Z")
            raise ModerationUnavailable("无法找到原消息，未执行操作")
        return await asyncio.to_thread(load)

    async def recall(self, cid, message_id):
        subscriber = account_sender._LIVE_SIO
        if subscriber is None:
            raise ModerationUnavailable("账号连接不可用，未执行撤回")
        await subscriber.recall_message(cid, message_id)

    async def kick(self, cid, uid):
        await asyncio.to_thread(dzmm.remove_member, cid, uid)
        if uid in await self.members(cid):
            raise ModerationUnavailable("踢出后仍在成员列表中，结果未确认")
