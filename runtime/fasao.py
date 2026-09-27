"""发骚骰子与场景扮演（P8 第 4 步从 bot.py 搬出）。

`/发骚` **同时**命中发骚与场景两个判定（靠分派顺序区分）、
`/发骚结束` 在没有进行中的发骚时会**开一场目标叫"结束"的场景** ——
两条都在 `docs/保全清单.md` 里登记为"现状行为"，顺序不得重排。

场景/体位/附加词库与 XP 偏好表跟着这一片走：只有这里用。
`_parse_scene_target` 也被调教用（调教要按场景目标开局），所以调教排在它后面。
"""
import random
import time
from datetime import datetime, timedelta

from brains import (generate_heat_fasao, generate_scene, generate_scene_continue,
                    generate_scene_end, generate_scene_rewrite)
from dzmm import get_messages_all
# P12-T：发骚文案走话术表（本群人设可换）。别名刻意叫 `_phrase` —— `fasao()` 里有一行
# `uid, _p = _find_in_profiles(...)`，别名若叫 `_p` 会被**影子覆盖**成 dict（实测报 TypeError）。
from personas import p as _phrase
import personas as _personas          # P12-T：取向（异性恋）要做代码级过滤
from profiles import get_profile
from runtime.tuning import get as _tune   # P12-B：可调参数（唯一读口）
from runtime import ctx
from runtime.group_state import (_get_fasao_state, _set_fasao_state,
                                 _set_last_action)
from runtime.heat import HEAT_FASAO_PICK, HEAT_SCENE_PICK, _add_heat
from runtime.identity import get_full_name, is_self
from runtime.members import (_find_in_profiles, _find_user_in_msgs,
                             _gender_label, _random_member_uid)
from runtime.memory import _retrieve_memory
from runtime.obs import log
from runtime.text import _is_stop_word, _strip_parenthesis


__all__ = [
    "is_fasao_cmd", "fasao", "is_scene_cmd", "_parse_scene_target", "scene",
    "is_fasao_end_cmd", "_check_fasao_timeout", "end_fasao", "_pick_combo_by_xp",
    "FASAO_TIMEOUT", "SCENES", "POSITIONS", "EXTRAS", "XP_BIAS",
]


FASAO_TIMEOUT = 15 * 60  # 发骚 active 超过 15 分钟无互动自动结束（防卡死）


SCENES = ["更衣室", "办公室", "教室", "浴室", "天台", "停车场", "公厕隔间", "深夜公园", "KTV包间", "楼梯间"]


POSITIONS = ["后入", "骑乘", "69", "口爆", "颜射", "捆绑", "spanking", "道具（跳蛋·振动棒·肛塞）", "项圈牵引", "露出", "强制高潮", "失禁play", "兽耳cos", "师生角色扮演", "主人与宠物", "双飞", "群P"]


EXTRAS = ["蒙眼", "手铐", "戴项圈", "不许出声", "公开play被围观", "边被玩边打电话", "塞着跳蛋出门", "让她求饶才停"]


XP_BIAS = {
    "小M": {"pos": ["后入", "口爆", "捆绑", "项圈牵引", "spanking", "失禁play"]},
    "被支配": {"pos": ["后入", "捆绑", "强制高潮", "项圈牵引"]},
    "小S": {"pos": ["骑乘", "颜射", "spanking", "强制高潮"]},
    "支配": {"pos": ["骑乘", "spanking", "强制高潮"]},
    "露出": {"scene": ["天台", "深夜公园", "停车场", "公厕隔间"], "extra": ["露出", "公开play被围观", "塞着跳蛋出门"]},
    "口交": {"pos": ["口爆", "69"]},
    "绳缚": {"pos": ["捆绑"], "extra": ["手铐", "戴项圈"]},
    "母狗化": {"extra": ["戴项圈", "让她求饶才停"]},
}


def is_fasao_cmd(cmd: str) -> bool:
    """识别发骚点名指令."""
    return cmd.startswith("/发骚") or cmd.startswith("/发情点名")


def fasao(cmd: str) -> str:
    """发骚点名：随机抽或指定某人发骚，加发情值."""
    cid = ctx.get_active_cid()
    target = ""
    for prefix in ["/发骚", "/发情点名"]:
        if cmd.startswith(prefix):
            target = cmd[len(prefix):].strip().lstrip("@").strip()
            break
    uid = None
    full = ""
    if target:
        uid, _p = _find_in_profiles(target)
        if uid:
            full = get_full_name(uid)
        else:
            msgs = get_messages_all(cid, max_count=200)
            uid, full = _find_user_in_msgs(target, msgs)
        if not uid:
            return _phrase("fasao_not_found", cid) or "没找到这个人。"
    else:
        uid = _random_member_uid()
        full = get_full_name(uid) if uid else ""
        if not uid:
            return _phrase("fasao_empty", cid) or "群里还没有可以点名的人。"
    # P12-T：异性恋人设（若瑾）不跟女性目标玩情欲玩法 —— 代码级过滤，
    # 不指望模型自己遵守（2026-09-27 实测：光写人设它照样照搬风格）。
    if _personas.is_straight(cid) and _gender_label(uid) == "女生":
        return _phrase("wrong_gender", cid) or "这个我不玩。"
    try:
        text = generate_heat_fasao(full)
    except Exception:
        text = ""
    text = (text or "").strip().replace("🤖", "")
    if not text:
        text = _phrase("fasao_fallback", cid, who=full) or (full + "，来。")
    _add_heat(cid, uid, _tune("heat.fasao_pick", cid))
    return (_phrase("fasao_pick", cid, who=full) or ("🎯 " + full)) + "\n" + text


def is_scene_cmd(cmd: str) -> bool:
    """识别涩涩骰子指令（/发骚 为主，旧的保留作别名）."""
    return cmd.startswith("/发骚") or cmd.startswith("/体位骰") or cmd.startswith("/场景骰") or cmd.startswith("/涩涩骰")


def _parse_scene_target(target):
    """解析 /发骚 的 target → (uid, full, keywords)。最长前缀匹配昵称，剩余为关键词."""
    tokens = target.split()
    # 档案本地匹配（零 API，可多次尝试）
    for i in range(len(tokens), 0, -1):
        name_part = " ".join(tokens[:i])
        uid, _ = _find_in_profiles(name_part)
        if uid:
            return uid, get_full_name(uid), tokens[i:]
    # 消息里匹配（一次 API）
    msgs = get_messages_all(ctx.get_active_cid())
    for i in range(len(tokens), 0, -1):
        name_part = " ".join(tokens[:i])
        uid2, full2 = _find_user_in_msgs(name_part, msgs)
        if uid2:
            return uid2, full2, tokens[i:]
    # 匹配不到，整个当昵称（无 uid，纯展示）
    return None, target, tokens


def scene(cmd: str) -> str:
    """涩涩骰子：生成 人物×场景×体位×附加玩法（支持自定义关键词 + 按 XP 定制）."""
    target = ""
    for prefix in ["/发骚", "/体位骰", "/场景骰", "/涩涩骰"]:
        if cmd.startswith(prefix):
            target = cmd[len(prefix):].strip()
            break
    selected_uid = None
    full = ""
    keywords = []
    if target:
        selected_uid, full, keywords = _parse_scene_target(target)
    else:
        selected_uid = _random_member_uid()
        full = get_full_name(selected_uid) if selected_uid else (
            _phrase("scene_default_who", ctx.get_active_cid()) or "姐姐")
    if selected_uid:
        _cid = ctx.get_active_cid()
        glabel = _gender_label(selected_uid)
        # P12-T：异性恋人设（若瑾）不跟女性目标玩情欲玩法 —— 放在**加发情值之前**：
        # 被回绝的目标不该涨分。
        if _personas.is_straight(_cid) and glabel == "女生":
            return _phrase("wrong_gender", _cid) or "这个我不玩。"
        _add_heat(_cid, selected_uid, _tune("heat.scene_pick", _cid))
    glabel = _gender_label(selected_uid) if selected_uid else "未知"
    # 读取目标 XP + 资料（签名/简介/画像，供增加上下文）
    xp = []
    profile_ctx = []
    if selected_uid:
        p = get_profile(selected_uid)
        if p:
            xp = p.get("xp") or []
            if p.get("signature"):
                profile_ctx.append("签名：" + str(p["signature"]))
            if p.get("bio"):
                profile_ctx.append("简介：" + str(p["bio"])[:150])
            if p.get("register_summary"):
                profile_ctx.append("画像：" + str(p["register_summary"])[:150])
    # 组装：昵称后面只要有内容，整体就是自定义玩法；否则按 XP/随机
    custom = " ".join(keywords)
    scene_name, pos, extra = _pick_combo_by_xp(xp)
    # 生成 prompt 用「去括号」昵称：括号里的签名（如「裳姐姐的骚贱小母狗」）语义太强，
    # LLM 会把「裳姐姐」当成独立角色引入，导致内容串角色。展示（combo）仍用完整昵称。
    gen_name = _strip_parenthesis(full)
    if custom:
        combo = full + " × 【自定义】" + custom
        # 自定义：只给主角 + 用户描述，不塞随机场景/体位/附加（只润色不加料）
        ai_input = "主角：" + gen_name + "（性别：" + glabel + "）\n用户描述（务必据此描写润色，不要增加其他人物/场景/道具/体位）：" + custom
    else:
        combo = full + " × " + scene_name + " × " + pos + " × " + extra
        ai_input = "主角：" + gen_name + "（性别：" + glabel + "）\n场景：" + scene_name + "\n体位：" + pos + "\n附加：" + extra
    if xp:
        ai_input += "\n主角XP：" + "、".join(xp)
    if profile_ctx and not custom:
        ai_input += "\n主角资料（贴合人设描写，只描写主角本人，不增加其他人物）：\n" + "\n".join(profile_ctx)
    try:
        text = generate_scene(ai_input)
    except Exception:
        text = ""
    text = (text or "").strip()
    # 记录上一段上下文（供 /继续 续写）
    _set_last_action(ctx.get_active_cid(), {
        "type": "scene",
        "uid": selected_uid,
        "full": full,
        "combo": combo,
        "output": text,
    })
    # 设置发骚状态（active + history），供 /继续 续写和指令屏蔽
    _set_fasao_state(ctx.get_active_cid(), {
        "active": True,
        "uid": selected_uid,
        "full": full,
        "combo": combo,
        "history": [text] if text else [],
        "last_ts": time.time(),
    })
    if text:
        return "🎲 " + combo + "\n" + text
    return "🎲 " + combo


def is_fasao_end_cmd(cmd: str) -> bool:
    """识别发骚结束：/发骚结束、停止、停下、结束 等."""
    return cmd.startswith("/发骚结束") or _is_stop_word(cmd)


def _check_fasao_timeout(cid: str):
    """发骚超时自动结束：active 超过 FASAO_TIMEOUT 自动结束，返回要发的消息或 None。"""
    fs = _get_fasao_state(cid)
    if not fs.get("active"):
        return None
    last_ts = fs.get("last_ts") or 0
    if last_ts and time.time() - last_ts > _tune("scene.timeout", cid):
        log("⏱️ [" + cid[:8] + "] 发骚超时自动结束")
        return end_fasao(cid)
    return None


def end_fasao(cid: str) -> str:
    """结束发骚，返回收尾小结."""
    fs = _get_fasao_state(cid)
    full = fs.get("full", "")
    history = fs.get("history") or []
    end_text = ""
    if history:
        try:
            end_text = generate_scene_end(_strip_parenthesis(full), history)
        except Exception:
            end_text = ""
    end_text = (end_text or "").strip()
    _set_fasao_state(cid, {"active": False})
    head = _phrase("fasao_end", cid) or "🎲 发骚结束。"
    if end_text:
        return head + "\n" + end_text
    return head


def _pick_combo_by_xp(xp):
    """按 XP 偏好 > 随机 选场景/体位/附加（作为背景，自定义玩法是核心）."""
    bias_scene, bias_pos, bias_extra = [], [], []
    for tag in (xp or []):
        b = XP_BIAS.get(tag)
        if b:
            bias_scene += b.get("scene", [])
            bias_pos += b.get("pos", [])
            bias_extra += b.get("extra", [])
    scene_name = random.choice(bias_scene or SCENES)
    pos = random.choice(bias_pos or POSITIONS)
    extra = random.choice(bias_extra or EXTRAS)
    return scene_name, pos, extra
