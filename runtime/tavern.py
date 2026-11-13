"""瑞雯の酒馆的魔法小剧场：三条文案咒语 + 真禁言/解禁（2026-11-02 屋主）。

- `tavern_magic`（纯文案，零 API 零模型）：/除你衣物 /荧光闪烁 /塔朗泰拉舞
- `tavern_mute`（真禁言）：/小嘴巴闭起来 [时长] [名字]、/恢复如初 [名字]
  走 `groupChat.setMemberSilence`（协议 2026-11-02 从平台前端 bundle 挖出并
  实测到权限层，封装见 `dzmm.set_member_silence`；5 档 + `None`=解禁）。

目标解析（双路，与 rose_moderation 的"回复原消息"同一习惯）：
  1. **回复**某人的消息 → 那条消息的发送者（先在本批消息窗口里找 sent_by，
     窗口太老找不到就提示改用名字，不再单独打平台接口）；
  2. **空格加对方全名** → 成员表解析（`_load_member_names` + `_find_uid_by_name`，
     与 `/查` 的 P12-CC 同一条路）。

真实禁言先核验发令者是本群管理员；服务端再校验执行账号和目标权限。
"""
import asyncio

from dzmm import set_member_silence
from personas.lambda_persona import RUIWEN_CID
from runtime import features
from runtime.members import _find_uid_by_name
from runtime.identity import _load_member_names, get_full_name
from runtime.obs import log
from runtime.moderation_platform import Platform, ModerationUnavailable

# kind → 功能开关（mainloop 闸门放行与 dispatch 分派共用同一张表）。
# ⚠️ 名字必须叫 `_FEATURES`：test_feature_registry 的静态扫描认这个形状
# （动态 key `features.on(cid, _FEATURES[kind])` 扫不出字面量，靠这张表登记）。
_FEATURES = {"magic": "tavern_magic", "mute": "tavern_mute", "unmute": "tavern_mute"}
_LAST_TARGET = {}  # (群 ID, 发令者 ID) → (目标 UID, 显示名)，不串用别人的目标。

# ---------------------------------------------------------------- 咒语文案（屋主原文，{name} 为目标昵称）
SPELLS = {
    "/除你衣物":
        "Λʌ：Take off your clothes.！掏出绿色震动棒开始挥舞，一阵绿色光束之后，"
        "{name}的衣物齐刷刷原地解散，就剩一条粉红色的小熊宝宝内裤！",
    "/荧光闪烁":
        "Λʌ：TLumos！掏出绿色震动棒开始挥舞，为{name}美妙的歌声打Call！",
    "/塔朗泰拉舞":
        "Λʌ：Tarantallegra！掏出绿色震动棒开始挥舞，一阵绿色光束之后，"
        "独自喝闷酒的{name}换上苏格兰裙子原地跳起踢踏舞，鞋底踩出的节奏正好卡上 BGM！",
}
MUTE_CMD = "/小嘴巴闭起来"
UNMUTE_CMD = "/恢复如初"
MUTE_OK = ("Λʌ：Quietus！掏出绿色震动棒开始挥舞，一阵绿色光束之后，"
           "{name}的小嘴巴已经被缝上了。（{name}已被禁言{dur}）")
UNMUTE_OK = ("Λʌ：Reparo！掏出绿色震动棒开始挥舞，一阵绿色光束之后，"
             "{name}的哑巴症已经治好啦！（{name}已解除禁言）")

# 时长词 → API 档位（先长后短匹配；"24小时"须排在含前缀关系的词前面）
DURATIONS = (("永久", "forever"), ("7天", "7d"), ("24小时", "24h"),
             ("1小时", "1h"), ("10分钟", "10m"))
DUR_LABEL = {"10m": "10分钟", "1h": "1小时", "24h": "24小时",
             "7d": "7天", "forever": "永久"}
ALL_PREFIXES = tuple(SPELLS) + (MUTE_CMD, UNMUTE_CMD)


def is_tavern_cmd(text: str):
    """纯前缀判定（不查 features）。命中返回 (kind, spell, arg)，否则 None。

    kind ∈ "magic" / "mute" / "unmute"；arg 为剥掉指令（和时长词）后的余文
    （名字，可能为空——回复场景）。mainloop 闸门放行和 dispatch 分派共用，
    两处前缀语义必须一致。
    """
    t = (text or "").strip()
    if not t:
        return None
    for spell, tpl in SPELLS.items():
        if t == spell or t.startswith(spell + " "):
            return ("magic", spell, t[len(spell):].strip())
    if t == MUTE_CMD or t.startswith(MUTE_CMD + " "):
        arg = t[len(MUTE_CMD):].strip()
        dur, name = _split_duration(arg)
        return ("mute", dur, name)
    if t == UNMUTE_CMD or t.startswith(UNMUTE_CMD + " "):
        return ("unmute", None, t[len(UNMUTE_CMD):].strip())
    return None


def _split_duration(arg: str):
    """'10分钟 张三' → ('10m', '张三')；'张三 10分钟' 也认；没写时长 → (None, 原文)。"""
    for word, code in DURATIONS:
        if arg.startswith(word):
            return code, arg[len(word):].strip()
        if arg.endswith(word):
            return code, arg[: len(arg) - len(word)].strip()
    return None, arg


def _reference_id(message) -> str:
    """被回复消息的 id（与 rose_moderation.reference 同一形状探测）。"""
    content = message.get("content") or {}
    quoted = content.get("reference") or content.get("quote") or content.get("reply")
    if not isinstance(quoted, dict):
        return ""
    return str(quoted.get("id") or quoted.get("message_id") or "")


def _resolve_target(cid: str, name: str, message, msgs: list):
    """目标 → (uid, 显示昵称)。回复优先、名字兜底；都落空返回 ("", "")。

    含阻塞 HTTP（成员表 / 补拉历史）——调用方放 `asyncio.to_thread` 里。

    ⚠️ 被回复的原消息**多半不在本轮 pending**（那是 last_id 之后的新消息，
    而被引用的往往是几分钟前的旧消息——2026-11-02 实测 Vv 引用别人发咒语
    全部落空）。本轮窗口找不到就再拉最近 30 条历史补找，别急着判失败。
    """
    quoted_id = _reference_id(message)
    if quoted_id:
        for m in msgs or []:
            if str(m.get("message_id") or "") == quoted_id:
                uid = str(m.get("sent_by") or "")
                if uid:
                    return uid, get_full_name(uid)
        try:
            from dzmm import get_messages
            for m in (get_messages(cid, 30) or []):
                if str(m.get("message_id") or "") == quoted_id:
                    uid = str(m.get("sent_by") or "")
                    if uid:
                        return uid, get_full_name(uid)
        except Exception as e:                                 # noqa: BLE001
            log("🎭 [" + cid[:8] + "] 补拉历史找被回复消息失败：" + type(e).__name__)
        log("🎭 [" + cid[:8] + "] 酒馆指令的被回复消息不在窗口，改走名字解析")
    if name:
        _load_member_names(cid)
        uid = _find_uid_by_name(name)
        if uid:
            return uid, get_full_name(uid)
    return "", ""


async def handle(sender, cid, cmd_sender, cmd, message, msgs, dry=False):
    """酒馆指令总入口（dispatch 接线点；闸门放行与功能开关都在前面做过了）。"""
    parsed = is_tavern_cmd(cmd)
    if parsed is None:
        return False
    kind, a, b = parsed
    try:
        if not features.on(cid, _FEATURES[kind]):
            return True                     # 功能关着：闸门放行过但这里不执行
    except Exception:                                       # noqa: BLE001
        return True
    if dry:
        log("[dry] 酒馆指令：" + cmd[:40])
        return True

    if kind == "magic":
        uid, name = await asyncio.to_thread(_resolve_target, cid, b, message, msgs)
        name = name or b
        if not name:
            # 2026-11-02 屋主实测反馈：刚对 kk0 用过 `/塔朗泰拉舞 kk0`，紧接着
            # 只发 `/荧光闪烁` —— 期望延续上一个目标，不用每次重写名字。
            last = _LAST_TARGET.get((cid, cmd_sender))
            if last:
                uid, name = last
            else:
                await sender.send("🤖 对谁施法？回复 TA 的消息，或空格加上对方全名。", cid)
                return True
        if not await sender.send(SPELLS[a].replace("{name}", name), cid):
            raise RuntimeError("酒馆魔法文案发送失败")
        _LAST_TARGET[(cid, cmd_sender)] = (uid, name)
        return True

    # mute / unmute
    try:
        await Platform().authorize(cid, cmd_sender)
    except ModerationUnavailable as error:
        await sender.send("🤖 " + str(error), cid)
        return True
    except Exception:
        await sender.send("🤖 暂时无法核验管理员权限，本次未执行。", cid)
        return True
    duration = a if kind == "mute" else None
    if kind == "mute" and not duration:
        await sender.send("🤖 要缝多久？10分钟 / 1小时 / 24小时 / 7天 / 永久。", cid)
        return True
    uid, full = await asyncio.to_thread(_resolve_target, cid, b, message, msgs)
    if not uid:
        last = _LAST_TARGET.get((cid, cmd_sender)) if not b and not _reference_id(message) else None
        if last and last[0]:
            uid, full = last
        else:
            await sender.send("🤖 没找到目标：回复 TA 的消息，或空格加上对方全名。", cid)
            return True
    try:
        await asyncio.to_thread(set_member_silence, cid, uid, duration)
    except Exception as e:                                     # noqa: BLE001
        msg = str(e)
        for prefix in ("setMemberSilence: ", "removeMember: "):
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
        log("🎭 [" + cid[:8] + "] 禁言失败：" + msg[:80])
        await sender.send("🤖 魔法失败了：" + msg, cid)
        return True
    name = full or get_full_name(uid)
    tpl = MUTE_OK if kind == "mute" else UNMUTE_OK
    text = tpl.replace("{name}", name).replace(
        "{dur}", DUR_LABEL.get(duration, "") if duration else "")
    if not await sender.send(text, cid):
        raise RuntimeError("酒馆禁言回执发送失败")
    _LAST_TARGET[(cid, cmd_sender)] = (uid, name)
    return True
