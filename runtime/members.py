"""群成员与档案查询（P8 第 4 步从 bot.py 拆出）。

一组"按昵称/uid 找人、把某人的资料与发言拼成文本"的helper。
它们被发骚、公演、投票、剧情、藏头诗、运势、周趋势**全都要用** ——
留在 bot.py 里的话，上面每一个模块都要回指它，拆分就白做了。

**找人的优先级是有讲究的**（`_find_uid_by_name`）：精确 → 简化昵称 → 前缀 → 子串，
且**单字不参与模糊匹配** —— 否则「裳」会命中「裳姐姐」，把指令打到别人身上。
"""
import random
import time

from dzmm import get_messages_all, get_user
from profiles import get_profile, load_profiles, upsert_profile
from runtime import ctx
from runtime.identity import (_load_member_names, _name_cache, get_full_name,
                              get_name, is_self)
from runtime.text import _normalize_cn, _simplify, _squash


__all__ = [
    "_extract_msgs", "_get_msgs_by_uid", "_member_profile_text",
    "_find_in_profiles", "_find_user_in_msgs", "find_user_by_name",
    "_find_uid_by_name", "_gender_label", "_gender_of", "_random_member_uid",
    "_archive_text_from_profile", "_history_lines", "_profile_history",
]


HISTORY_MAX = 5             # 每人保留最近 5 版变化轨迹（姓名/签名/简介一起）


def _extract_msgs(uid: str, msgs: list) -> list:
    """从已有消息列表过滤某成员的发言文本."""
    their_msgs = []
    for m in msgs:
        if m.get("sent_by") == uid:
            text = (m.get("content") or {}).get("text", "").strip()
            if text and "加入了群聊" not in text:
                their_msgs.append(text)
    return their_msgs


def _get_msgs_by_uid(uid: str) -> list:
    """拉取某成员的所有发言文本."""
    return _extract_msgs(uid, get_messages_all(ctx.get_active_cid()))


def _member_profile_text(uid: str, full: str, msgs: list = None) -> str:
    """组装某成员资料+发言文本."""
    u = get_user(ctx.get_active_cid(), uid)
    their_msgs = _extract_msgs(uid, msgs) if msgs is not None else _get_msgs_by_uid(uid)
    lines = ["昵称：" + full]
    if u:
        if u.get("gender"):
            lines.append("性别：" + str(u["gender"]))
        if u.get("quirk"):
            lines.append("签名：" + str(u["quirk"]))
        if u.get("bio"):
            lines.append("简介：" + str(u["bio"]))
    if their_msgs:
        lines.append("最近发言（" + str(len(their_msgs)) + "条）：")
        for t in their_msgs[-30:]:
            lines.append("  - " + t[:80])
    return "\n".join(lines)


def _find_in_profiles(query: str):
    """在档案里按昵称匹配，返回 (uid, profile) 或 (None, None)."""
    profiles = load_profiles()
    q = _normalize_cn(query)
    for uid, p in profiles.items():
        name = p.get("name", "")
        if _normalize_cn(name) == q or _normalize_cn(_simplify(name)) == q:
            return uid, p
    for uid, p in profiles.items():
        if q in _normalize_cn(p.get("name", "")):
            return uid, p
    return None, None


def _find_user_in_msgs(query: str, msgs: list):
    """在已有消息列表中按昵称匹配，返回 (uid, 完整昵称)."""
    _load_member_names()  # 批量加载昵称，避免逐个 get_user
    uids = set(m.get("sent_by", "") for m in msgs)
    q = _normalize_cn(query)
    # 精确匹配优先（归一化后比较，兼容异体字/繁体/空格差异）
    for uid in uids:
        full = get_full_name(uid)
        if _normalize_cn(full) == q or _normalize_cn(get_name(uid)) == q:
            return uid, full
    # 子串匹配
    for uid in uids:
        full = get_full_name(uid)
        if q in _normalize_cn(full) or q in _normalize_cn(get_name(uid)):
            return uid, full
    return None, None


def find_user_by_name(query: str):
    """根据昵称模糊匹配，返回 (uid, 完整昵称)."""
    return _find_user_in_msgs(query, get_messages_all(ctx.get_active_cid()))


def _find_uid_by_name(name: str) -> str:
    """昵称→uid。精确匹配优先，单字不模糊匹配（避免「裳」误中「裳姐姐」）。"""
    name = (name or "").strip()
    if not name:
        return ""
    # 1. 完整昵称精确匹配
    for uid, full in _name_cache.items():
        if full == name:
            return uid
    # 2. 简化昵称精确匹配（去括号/波浪号）
    for uid in _name_cache:
        if get_name(uid) == name:
            return uid
    # 3. 前缀匹配（name 是完整昵称前缀，如「糯糯」匹配「糯糯～（...）」）
    for uid, full in _name_cache.items():
        if full.startswith(name):
            return uid
    # 4. 子串匹配（仅 name 长度 ≥2，单字「裳」不参与，避免误中「裳姐姐」）
    if len(name) >= 2:
        for uid, full in _name_cache.items():
            if name in full or full in name:
                return uid
    # 档案同样精确优先
    profiles = load_profiles()
    for uid, p in profiles.items():
        if p.get("name", "") == name:
            return uid
    for uid, p in profiles.items():
        full = p.get("name", "")
        if full and full.startswith(name):
            return uid
    if len(name) >= 2:
        for uid, p in profiles.items():
            full = p.get("name", "")
            if full and (name in full or full in name):
                return uid
    return ""


def _gender_label(uid: str) -> str:
    """返回性别标签：女生/男生/未知（用于 AI 称呼判断）."""
    p = get_profile(uid)
    g = (p.get("gender") or "").strip().lower() if p else ""
    if g in ("male", "man", "男", "m"):
        return "男生"
    if g in ("female", "lesbian", "女", "f", "百合"):
        return "女生"
    return "未知"


def _gender_of(uid: str, cid: str = None, write_back: bool = True) -> str:
    """这人什么性别：「女生」/「男生」/「未知」—— **档案优先，档案没写就实时查一次平台**.

    与 `_gender_label` 的区别：那个只看本地档案（查不到就是"未知"）；这个会补一次
    `getChatroomUser` 并顺手写回档案。

    P12-T（屋主："先识别性别，然后再根据性别生成"）：羞辱/发骚要用它**先定性别再分路**。
    ⚠️ 会发一次平台请求：调用方要么在 sync 上下文（本来就跑在线程里），
    要么自己套 `asyncio.to_thread`。
    """
    lab = _gender_label(uid)
    if lab != "未知" or not uid:
        return lab
    try:
        u = get_user(cid or ctx.get_active_cid(), uid) or {}
    except Exception:                                        # noqa: BLE001
        return "未知"
    g = str(u.get("gender") or "").strip().lower()
    if g in ("male", "man", "男", "m"):
        out = "男生"
    elif g in ("female", "lesbian", "女", "f", "百合"):
        out = "女生"
    else:
        return "未知"
    if write_back:
        try:                       # 写回档案，免得每次都查（与 `_room_male` 同一套）
            p = get_profile(uid) or {}
            if p:
                p["gender"] = "male" if out == "男生" else "female"
                upsert_profile(uid, p)
        except Exception:                                    # noqa: BLE001
            pass
    return out


def _random_member_uid(exclude: set = None):
    """随机选当前群一个发言成员（排除 agent 自己和 exclude 里的 uid）."""
    msgs = get_messages_all(ctx.get_active_cid(), max_count=200)
    uids = [m.get("sent_by", "") for m in msgs if m.get("sent_by") and not is_self(m.get("sent_by"))]
    if exclude:
        uids = [u for u in uids if u not in exclude]
    if not uids:
        return None
    return random.choice(list(set(uids)))


def _archive_text_from_profile(profile: dict) -> str:
    """从档案构建查询文本（零 API）."""
    lines = ["【注册档案】", "昵称：" + profile.get("name", "")]
    if profile.get("gender"):
        lines.append("性别：" + profile["gender"])
    if profile.get("signature"):
        lines.append("签名：" + profile["signature"])
    if profile.get("bio"):
        lines.append("简介：" + profile["bio"])
    if profile.get("xp"):
        lines.append("XP性癖：" + "、".join(profile["xp"]))
    lines.append("注册时间：" + profile.get("registered_at", ""))
    hl = _history_lines(profile, n=3, bio_n=70)
    if hl:
        lines.append("【变化轨迹（姓名/签名/简介；越靠前越早，可挑一处做对比反差）】")
        lines.extend(hl)
    lines.append("注册时画像：" + profile.get("register_summary", ""))
    if profile.get("summary"):
        lines.append("滚动总结：" + profile["summary"])
    msgs = profile.get("messages", [])
    if msgs:
        lines.append("【最新发言】")
        for m in msgs[-10:]:
            lines.append("  - [" + m.get("at", "") + "] " + m.get("text", "")[:60])
    return "\n".join(lines)


def _history_lines(profile: dict, n: int = 3, bio_n: int = 70) -> list:
    """变化轨迹 → 给 AI 看的文本行（越靠前越早，可用于对比反差）."""
    hist = [h for h in _profile_history(profile) if isinstance(h, dict)][-n:]
    lines = []
    for h in hist:
        bits = []
        if h.get("name"):
            bits.append("叫「" + str(h["name"]) + "」")
        if h.get("sig"):
            bits.append("签名「" + _squash(h["sig"], 30) + "」")
        if h.get("bio"):
            bits.append("简介：" + _squash(h["bio"], bio_n))
        if bits:
            lines.append("  - " + (str(h.get("at")) or "?") + " 前，" + "｜".join(bits))
    return lines


def _profile_history(profile: dict) -> list:
    """统一变化轨迹 [{at, name, sig, bio}]（从旧到新）；老 name_history 自动并入。"""
    hist = profile.get("history")
    if not isinstance(hist, list):
        hist = []
        for h in (profile.get("name_history") or []):
            if isinstance(h, dict) and h.get("from"):
                hist.append({"at": str(h.get("at") or ""), "name": str(h["from"]),
                             "sig": "", "bio": ""})
        profile["history"] = hist
    return hist


def _extract_mentioned_members(text: str, cid: str) -> list:
    """从自然语言提取点名的群成员（按用户顺序），返回简化昵称列表."""
    cleaned = text
    for sep in ["、", "，", ","]:
        cleaned = cleaned.replace(sep, " ")
    tokens = cleaned.split()
    profiles = load_profiles()
    mentioned = []
    for tok in tokens:
        if len(tok) < 2:
            continue
        uid = None
        for u, p in profiles.items():
            name = p.get("name", "")
            if name == tok or _simplify(name) == tok:
                uid = u
                break
        if not uid:
            for u, p in profiles.items():
                simp = _simplify(p.get("name", ""))
                if simp and len(simp) >= 2 and tok.startswith(simp):
                    uid = u
                    break
        if uid:
            label = get_name(uid) or _simplify(get_full_name(uid))
            if label and label not in mentioned:
                mentioned.append(label)
    return mentioned
