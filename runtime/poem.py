"""藏头诗与夸夸诗（P8 第 4 步从 bot.py 搬出）。

它是第 4 步里**最后被解锁**的一片：原来卡在 `_retrieve_memory` 与
`_refresh_profile_latest` 上 —— 这两个属于"记忆与档案变更"那一簇。
先搬那一簇，这一片才能做到**零回指**，而不是回指 bot.py 假装解耦。
"""
import time

from brains import POEM_STYLES, generate_poem, generate_poem_theme
from dzmm import get_messages_all, get_user
from profiles import get_profile, load_profiles, upsert_profile
from runtime import ctx
from runtime.identity import get_full_name, get_name
from runtime.members import (_archive_text_from_profile, _find_in_profiles,
                             _find_user_in_msgs, _get_msgs_by_uid,
                             _member_profile_text)
from runtime.memory import _refresh_profile_latest, _retrieve_memory
from runtime.obs import log
from runtime.text import _norm_poem
import state_store

__all__ = [
    "is_poem_cmd", "poem", "_do_poem", "_poem_text", "_poem_resolve",
    "_record_poem", "_get_poem_history", "_parse_poem_style",
]


POEM_STYLE_KEYWORDS = {
    "七言绝句": ["七言绝句", "七绝", "绝句", "七言"],
    "五言绝句": ["五言绝句", "五绝", "五言"],
    "七言律诗": ["七言律诗", "七律", "律诗"],
    "五言律诗": ["五言律诗", "五律"],
    "宋词": ["宋词", "填词", "写词", "如梦令", "菩萨蛮", "浣溪沙", "卜算子", "鹧鸪天",
             "蝶恋花", "清平乐", "忆江南", "点绛唇", "西江月", "虞美人", "长相思", "采桑子", "醉花阴", "满江红"],
}


def is_poem_cmd(cmd: str) -> str:
    """识别写诗指令，返回目标（空串=给自己），None=不是写诗。"""
    for prefix in ["/写诗", "/作诗", "/赋诗"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None


def poem(cmd: str, sender_uid: str) -> str:
    """写诗：不填=给自己，指定人=给那人，指定主题=按主题写。
    支持 /写诗 宋词 xxx、/写诗 七言绝句 xxx 指定形式。"""
    target = is_poem_cmd(cmd)
    target = target if target is not None else ""
    # 解析开头指定的形式（如"宋词""七言绝句"）
    style, target = _parse_poem_style(target)
    if not target:
        # 给自己写
        profile = get_profile(sender_uid)
        text = _poem_text(sender_uid, profile) if profile else "昵称：" + get_full_name(sender_uid)
        return _do_poem(sender_uid, text, get_full_name(sender_uid), style)
    # 尝试匹配人
    uid, text, found = _poem_resolve(target)
    if found and uid:
        return _do_poem(uid, text, get_full_name(uid), style)
    # 匹配不到人 → 按主题写
    poem_text, st = generate_poem_theme(target, style=style)
    poem_text = _norm_poem(poem_text)
    if not poem_text:
        return "写诗失败啦，再试一次～"
    return "📜 关于「" + target + "」的诗词：\n" + poem_text


def _do_poem(uid: str, text: str, full: str, style: str = None) -> str:
    """给人写诗（带历史避让，保证每次不一样）。style 指定形式则用指定。"""
    history = _get_poem_history(uid)
    poem_text, st = generate_poem(text, history, style=style)
    _record_poem(uid, st, poem_text)
    poem_text = _norm_poem(poem_text)
    if not poem_text:
        return "写诗失败啦，再试一次～"
    return "📜 " + full + " 的诗词：\n" + poem_text


def _poem_text(uid: str, profile: dict) -> str:
    """写诗素材：实时简介/签名 + 档案画像 + 近期发言（化入意象，不机械翻译）。"""
    lines = ["【写诗素材（只作参考，不要机械翻译成诗句）】", "昵称：" + profile.get("name", "")]
    # 1. 实时简介/签名（DZMM 当前资料，会变，比档案里注册时缓存的旧值准）
    try:
        u = get_user(ctx.get_active_cid(), uid)
        if u:
            if u.get("quirk"):
                lines.append("签名：" + str(u["quirk"]))
            if u.get("bio"):
                lines.append("简介：" + str(u["bio"]))
            if u.get("gender"):
                lines.append("性别：" + str(u["gender"]))
    except Exception:
        pass
    # 2. 档案沉淀（XP/画像，实时拿不到，注册时生成）
    if profile.get("xp"):
        lines.append("XP性癖：" + "、".join(profile["xp"]))
    if profile.get("register_summary"):
        lines.append("画像：" + profile["register_summary"][:150])
    # 3. 近期发言（找气质/故事点/意象，即兴发挥）
    try:
        their = _get_msgs_by_uid(uid)
        if their:
            lines.append("【近期发言（找气质/故事点/意象，即兴发挥）】")
            for t in their[-10:]:
                lines.append("  - " + t[:50])
    except Exception:
        pass
    # 4. 与该人相关的记忆（可提炼意象）
    try:
        mem = _retrieve_memory(ctx.get_active_cid(), profile.get("name", ""), 5)
        if mem:
            lines.append("【TA 相关的记忆（可提炼意象）】")
            for m in mem:
                lines.append("  - " + m["text"])
    except Exception:
        pass
    return "\n".join(lines)


def _poem_resolve(query: str):
    """解析写诗目标（非空 query），返回 (uid, 素材文本, 是否找到)。匹配不到人返回 found=False。"""
    uid, profile = _find_in_profiles(query)
    if uid:
        _refresh_profile_latest(uid, profile)
        return uid, _poem_text(uid, profile), True
    msgs = get_messages_all(ctx.get_active_cid())
    uid2, full = _find_user_in_msgs(query, msgs)
    if uid2:
        return uid2, _member_profile_text(uid2, full, msgs), True
    tokens = query.split()
    if len(tokens) > 1:
        for i in range(len(tokens) - 1, 0, -1):
            name_part = " ".join(tokens[:i])
            uid3, profile3 = _find_in_profiles(name_part)
            if uid3:
                _refresh_profile_latest(uid3, profile3)
                return uid3, _poem_text(uid3, profile3), True
    return None, "", False


def _record_poem(uid: str, style: str, text: str) -> None:
    """记录一次写诗（方向 + 内容），供下次避免重复。保留最近 3 首。没档案也建最小档案记录。"""
    try:
        p = get_profile(uid)
        if not p:
            p = {"name": get_full_name(uid), "poem_history": []}
        hist = (p.get("poem_history") or [])[-2:]
        hist.append({"style": style, "text": (text or "")[:200], "at": time.strftime("%m-%d %H:%M")})
        p["poem_history"] = hist
        upsert_profile(uid, p)
    except Exception:
        pass


def _get_poem_history(uid: str) -> list:
    """读某人的写诗历史（避免重复用），profile 全局共享。"""
    p = get_profile(uid)
    return (p or {}).get("poem_history") or []


def _parse_poem_style(rest: str):
    """识别指定形式（开头或结尾均可，如"宋词 林九鹓"或"林九鹓 宋词"）。
    返回 (POEM_STYLES 匹配项 or None, 剩余内容)。"""
    candidates = []
    for style_key, kws in POEM_STYLE_KEYWORDS.items():
        for kw in kws:
            candidates.append((len(kw), style_key, kw))
    candidates.sort(key=lambda x: -x[0])  # 长关键词优先
    # 1. 开头匹配：宋词 林九鹓 / 七言绝句 苏苏
    for _, style_key, kw in candidates:
        if rest.startswith(kw):
            for s in POEM_STYLES:
                if s.startswith(style_key):
                    return s, rest[len(kw):].strip()
    # 2. 结尾匹配：林九鹓 宋词 / 御褚 如梦令（最后一个空格分隔的词是形式）
    tokens = rest.split()
    if len(tokens) >= 2:
        last = tokens[-1]
        for _, style_key, kw in candidates:
            if last == kw or last.startswith(kw):
                for s in POEM_STYLES:
                    if s.startswith(style_key):
                        return s, rest[: -len(last)].strip()
    return None, rest
