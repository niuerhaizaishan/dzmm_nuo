# -*- coding: utf-8 -*-
"""官方 Bot Webhook 接收端（P12-J）：平台把群消息**直推**给我们。

## 为什么要有它

官方收消息**只有 Webhook 一条路**（文档里没有 `getUpdates` 那种长轮询）。
现在收消息走的是侧车账号的 Socket.IO —— 那条路是浏览器会话的长连接：
会断线重连、会受验证码/风控影响，实测投递延迟出现过 **10～20 秒**。
Webhook 是平台服务端直推，秒级，且与账号会话**完全解耦**。

（侧车账号退不掉：档案/成员/筛查踢人/图片/私聊仍靠它。Webhook 只替换"读群消息"。）

## 平台侧约定（来自官方文档）

- Webhook URL 在 **Bot 详情页**配置；平台硬要求**公网 HTTPS**（阻断 localhost/内网）。
- 校验头：`X-Telegram-Bot-Api-Secret-Token`（不是 `X-Bot-Secret`，文档特别点名）。
- payload 是 Telegram 风格：`update.message.chat.id` / `update.message.text`。
- Bot 被移出群后不再收到该群 Webhook。

## 本实现的四个要点

1. **先校验 Secret**：不在白名单里一律 401（并记一行日志，便于发现有人在扫）。
2. **绝不让平台等我们**：验证通过立刻 200，再入队 —— 平台超时会重推。
3. **按 message_id 去重**：平台会重推（我们 200 晚了、或它自己重试）。
4. **形状防御式解析 + 头几条打原始 payload**：字段名以实测为准，不靠猜。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from aiohttp import web

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class Deduper:
    """按 message_id 去重（平台会重推同一个 update）。

    只留最近 `cap` 个 id：重推窗口是分钟级，不需要无限记。
    """

    def __init__(self, cap: int = 500):
        self.cap = cap
        self._seen = []
        self._set = set()

    def seen(self, mid) -> bool:
        """True = 这条已经处理过（重复推送）。"""
        if not mid:
            return False
        mid = str(mid)
        if mid in self._set:
            return True
        self._seen.append(mid)
        self._set.add(mid)
        while len(self._seen) > self.cap:
            self._set.discard(self._seen.pop(0))
        return False


def _first(d: dict, *names, default=None):
    for n in names:
        v = d.get(n)
        if v not in (None, ""):
            return v
    return default


def to_internal(update: dict) -> dict | None:
    """Telegram 风格 update → 本项目内部消息形状。

    内部形状（主循环 `drain(cid)` 拿到的就是它）：
        {message_id, chatroom_id, sent_by, sent_at, content:{type, text}}

    `sent_at` 用 **UTC** 的 "%Y-%m-%d %H:%M:%S"（与 tRPC `getMessages` 一致）——
    `bot.message_lag()` 就是拿它算"投递延迟"的。
    解析不出来就返回 None（调用方会 200 丢掉并记日志，**不**让平台重推）。

    **入群/退群事件**：Telegram 风格是 `message.new_chat_member(s)` /
    `left_chat_member`。本项目筛查靠 `_is_join_msg()` —— 它只认
    `content.text` 里含「加入了群聊」且 `sent_by` 是新成员 uid。
    所以这里把入群事件**合成成那个形状**，于是"收消息全走 Webhook"之后
    入群筛查照样能用（不必再依赖侧车账号的 Socket.IO）。
    """
    if not isinstance(update, dict):
        return None
    msg = update.get("message") or update.get("edited_message") or {}
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    frm = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    content = msg.get("content") if isinstance(msg.get("content"), dict) else {}

    cid = _first(msg, "chatroom_id", "chat_id") or _first(chat, "id") \
        or _first(update, "chatroomId", "chatroom_id")
    if not cid:
        return None

    # ---- 入群 / 退群：合成成 _is_join_msg() 认得的系统消息 ----
    join = _first(msg, "new_chat_member", "new_chat_participant")
    join_list = _first(msg, "new_chat_members", "new_chat_participants")
    left = _first(msg, "left_chat_member", "left_chat_participant")
    sys_text = ""
    sys_uid = ""
    if join_list and isinstance(join_list, (list, tuple)):
        parts, uids = [], []
        for j in join_list:
            if isinstance(j, dict):
                parts.append(str(_first(j, "first_name", "username", "name") or "新成员"))
                uids.append(str(_first(j, "id") or ""))
        sys_text = "、".join(parts) + " 加入了群聊"
        sys_uid = uids[0] if uids else ""
    elif isinstance(join, dict):
        sys_text = str(_first(join, "first_name", "username", "name") or "新成员") \
            + " 加入了群聊"
        sys_uid = str(_first(join, "id") or "")
    elif isinstance(left, dict):
        sys_text = str(_first(left, "first_name", "username", "name") or "成员") \
            + " 退出了群聊"
        sys_uid = str(_first(left, "id") or "")

    sender = sys_uid or _first(msg, "sent_by", "sender_id") or _first(frm, "id") \
        or _first(update, "sent_by")
    if not sender:
        return None

    text = sys_text or _first(msg, "text") or _first(content, "text") or ""
    ctype = "system" if sys_text else (_first(msg, "type")
                                       or _first(content, "type") or "text")

    sent_at = ""
    raw_date = _first(msg, "date", "sent_at")
    if isinstance(raw_date, (int, float)):
        try:
            sent_at = datetime.fromtimestamp(
                float(raw_date), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:                                          # noqa: BLE001
            sent_at = ""
    elif isinstance(raw_date, str) and len(raw_date) >= 19:
        sent_at = raw_date[:19].replace("T", " ")
    if not sent_at:
        sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # P12-AE：别人"右键点击回复"时，平台把被引用的那条放在 `content.reference`
    # （`{id, sentBy, content:{text,type}}`）。以前这里只取 type/text，**reference
    # 被整个丢掉** ⇒ 走 webhook 那条路的消息永远看不出"他在回复我"。
    # 原样透传（不做形状转换），下游 `bot._ref_of()` 两层形状都认。
    ref = content.get("reference") or content.get("quote")
    if not isinstance(ref, dict):
        ref = None

    return {
        "message_id": str(_first(msg, "message_id", "id") or update.get("update_id") or ""),
        "chatroom_id": str(cid),
        "sent_by": str(sender),
        "sent_at": sent_at,
        "content": {"type": str(ctype), "text": str(text),
                    "from_name": str(_first(frm, "first_name", "username", "name") or ""),
                    "reference": ref},
        "_source": "webhook",
    }


def secret_env_for(token_env: str) -> str:
    """Bot 的 Webhook Secret 环境变量名 —— 由 token 变量名推出来。

    `DZMM_BOT_TOKEN_ROSE` → `DZMM_BOT_SECRET_ROSE`；`DZMM_BOT_TOKEN` → `DZMM_BOT_SECRET`。
    （配置 schema 里没有 secret 字段，也不打算加：Secret 只在服务器 .env 里。）
    """
    t = (token_env or "").strip()
    if not t:
        return ""
    return t.replace("TOKEN", "SECRET") if "TOKEN" in t else t + "_SECRET"


def _field(b, name: str, default=""):
    """从 dict 或 dataclass 里取字段（两种形状都要认）。"""
    if isinstance(b, dict):
        return b.get(name, default)
    return getattr(b, name, default)


def collect_secrets(bots) -> dict:
    """{secret 值: (bot_key, cid)} —— 只收非空的。

    `bots` 需要**带 `token_env`** 的形状：`config_store.Config.bots`（BotSpec）✓。
    ⚠️ 别传 `bots_for_identity()`：那是给 identity 用的 dict（只有 key/name/short/
    cid/uid，**没有 token_env**）—— P12-J 踩过这个坑（结果是一个 Secret 都收不到，
    日志只说"没有配置 Secret"，看起来像 .env 没配）。
    """
    out = {}
    for b in bots or ():
        tenv = str(_field(b, "token_env") or "")
        senv = secret_env_for(tenv)
        val = (os.environ.get(senv) or "").strip() if senv else ""
        if val:
            out[val] = (str(_field(b, "key") or ""), str(_field(b, "cid") or ""))
    return out


def build_app(*, secrets: dict, push, log=None, dedup: Deduper = None,
              raw_log_budget: int = 3) -> web.Application:
    """建 aiohttp 应用。

    `secrets`: {secret: (bot_key, cid)} —— 只有白名单里的 Secret 才认。
    `push`: `push(cid, msg) -> int`（入队，返回该群待处理条数）。
    `raw_log_budget`: 头几条把**原始 payload** 打进日志（确认字段名用），够了就停。
    """
    seen = dedup or Deduper()
    budget = {"n": raw_log_budget}

    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:                                      # noqa: BLE001
                pass

    async def handle(request: web.Request) -> web.Response:
        secret = request.headers.get(SECRET_HEADER, "")
        if not secret or secret not in secrets:
            _log("P12-J: ❌ Webhook 校验失败（来源 {}，header={}）".format(
                request.remote or "?", "有" if secret else "无"))
            return web.json_response({"ok": False, "error": "unauthorized"},
                                     status=401)
        try:
            raw = await request.text()
            update = json.loads(raw or "{}")
        except Exception as e:                                     # noqa: BLE001
            _log("P12-J: ⚠️ Webhook payload 不是 JSON：{}".format(str(e)[:60]))
            return web.json_response({"ok": True, "ignored": "bad json"})

        if budget["n"] > 0:
            budget["n"] -= 1
            _log("P12-J: 原始 payload（前 {} 条之一，用于核对字段名）：{}".format(
                raw_log_budget, raw[:400]))

        bot_key, bot_cid = secrets[secret]
        msg = to_internal(update)
        if not msg:
            _log("P12-J: ⚠️ 解析不出消息（bot={}）：{}".format(bot_key, raw[:160]))
            return web.json_response({"ok": True, "ignored": "unparsed"})
        if seen.seen(msg.get("message_id")):
            return web.json_response({"ok": True, "ignored": "duplicate"})
        if bot_cid and msg["chatroom_id"] != bot_cid:
            _log("P12-J: ⚠️ 消息群 {} 与 Bot {} 绑定的群不一致，忽略".format(
                msg["chatroom_id"][:8], bot_key))
            return web.json_response({"ok": True, "ignored": "wrong chatroom"})
        try:
            n = push(msg["chatroom_id"], msg)
        except Exception as e:                                     # noqa: BLE001
            _log("P12-J: ❌ 入队失败：{}".format(str(e)[:70]))
            return web.json_response({"ok": False, "error": "enqueue failed"},
                                     status=500)
        _log("📥 P12-J: webhook 收到 [{}] {}: {}（待处理 {} 条）".format(
            msg["chatroom_id"][:8], msg["sent_by"][:8],
            (msg["content"].get("text") or "")[:30], n))
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/bot/webhook", handle)

    async def _probe(_request: web.Request) -> web.Response:
        # 给浏览器/排查用：能 200 说明 TLS 与反代通了（真实验证靠 POST + Secret）
        return web.json_response({"ok": True, "hint": "POST only"})

    app.router.add_get("/bot/webhook", _probe)
    return app


async def start(app: web.Application, host: str, port: int):
    """起服务，返回 runner（调用方负责在退出时 `await runner.cleanup()`）。"""
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
