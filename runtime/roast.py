"""恶魔吐槽族（P3-8，2026-10-01 自 bot.py 原样抽出）。

`_maybe_devil_roast`（热闹/敏感词触发，只改内存 group_state）与
`_maybe_devil_cold_roast`（冷场主动毒舌，自包含 load/save）。

依赖注入（同 3-2/3-3 模式）：cold roast 的 `load_state` / `_save_group` 由
main_loop 调用点传真身名字 —— `monkeypatch bot.load_state` 接缝不变。
概率/阈值走 `_tune`（P12-0 读口唯一）；下面 4 个**非参数化**常量随函数搬入，
8 个被 tuning 对照钉住的 DEVIL_* 概率常量仍住 bot.py（test_tuning 读它们）。

纪律：逐字搬迁，只动「住在哪」；本模块不许 import bot。
"""
import random
import time
from datetime import datetime

from brains import generate_devil_roast
from profiles import get_profile
from runtime.group_state import (_get_dj_state, _get_fasao_state,
                                 _get_perf_state, _get_story_state,
                                 _get_vote_state)
from runtime.identity import get_full_name, get_name, is_self
from runtime.members import (_archive_text_from_profile, _member_profile_text,
                             _random_member_uid)
from runtime.obs import log
from runtime.tuning import get as _tune

# ---------------------------------------------------------------- roast 自有常量
DEVIL_DAILY_MAX = 30         # 每日上限 30 次
DEVIL_HOT_MIN_SPEAKERS = 3   # 热闹阈值：最近 12 条内 ≥3 人发言
DEVIL_SENSITIVE_WORDS = [
    "骚", "发情", "母狗", "肉便器", "欠操", "高潮", "调教", "骚穴",
    "发浪", "骚水", "下贱", "淫荡", "发骚", "公演",
]
DEVIL_HOT_WINDOW = 3 * 60        # 热闹窗口：3 分钟内发言算"同时活跃"


def _maybe_devil_roast(cid: str, msgs: list, group_state: dict) -> str:
    """恶魔吐槽：判断是否触发并生成文案（只改内存 group_state，不 save）.
    返回文案或空串(不触发)."""
    # 开关（默认关，需 /吐槽开 开启）
    if group_state.get("devil_enabled") is not True:
        return ""
    # 互斥：任何 active 状态都不吐
    if (_get_vote_state(cid).get("active") or _get_story_state(cid).get("active")
            or _get_dj_state(cid).get("active") or _get_perf_state(cid).get("active")
            or _get_fasao_state(cid).get("active")):
        return ""
    # 冷却
    now = time.time()
    if now - group_state.get("devil_last_roast", 0) < _tune("devil.cooldown", cid):
        return ""
    # 每日上限（跨天重置）
    today = datetime.now().strftime("%Y-%m-%d")
    if group_state.get("devil_daily_date") != today:
        group_state["devil_daily_count"] = 0
        group_state["devil_daily_date"] = today
    if group_state.get("devil_daily_count", 0) >= DEVIL_DAILY_MAX:
        return ""
    # 提取上下文（最近 12 条非 bot、非系统消息）
    ctx = []
    for m in msgs[-12:]:
        uid = m.get("sent_by", "")
        if is_self(uid):
            continue
        text = (m.get("content") or {}).get("text", "").strip()
        if text and "加入了群聊" not in text:
            ctx.append({"sender": get_name(uid) or "某人", "text": text})
    if not ctx:
        return ""
    # 概率判断（敏感词 > 热闹 > 基础）
    joined = " ".join(c["text"] for c in ctx)
    hit_sensitive = any(w in joined for w in DEVIL_SENSITIVE_WORDS)
    # 热闹：最近 DEVIL_HOT_WINDOW 秒内发言的独立人数 ≥ 阈值（不依赖本轮消息窗口）
    last_speak = group_state.get("last_speak") or {}
    hot_count = sum(1 for ts in last_speak.values() if now - ts <= DEVIL_HOT_WINDOW)
    hot = hot_count >= DEVIL_HOT_MIN_SPEAKERS
    if hit_sensitive:
        prob = _tune("devil.sensitive_prob", cid)
    elif hot:
        prob = _tune("devil.hot_prob", cid)
    else:
        prob = _tune("devil.base_prob", cid)
    if random.random() >= prob:
        return ""
    # 命中：决定对事/对人（50/50，对人随机抽成员注入画像）
    target_text = ""
    if random.random() < _tune("devil.about_person_prob", cid):
        uid = _random_member_uid()
        if uid:
            profile = get_profile(uid)
            if profile:
                target_text = _archive_text_from_profile(profile)
            else:
                target_text = _member_profile_text(uid, get_full_name(uid))
    # 生成
    context_text = "\n".join(c["sender"] + ": " + c["text"] for c in ctx)
    try:
        roast = generate_devil_roast(context_text, target_text)
    except Exception:
        return ""
    roast = (roast or "").strip()
    if not roast:
        return ""
    # 更新状态（内存，主循环触发后立即 save）
    group_state["devil_last_roast"] = now
    group_state["devil_daily_count"] = group_state.get("devil_daily_count", 0) + 1
    return roast


def _maybe_devil_cold_roast(cid: str, *, _save_group, load_state) -> str:
    """冷场吐槽：群里冷清时主动毒舌（自包含 load/save，时间驱动）.
    冷清满 20 分钟后首次 roll，之后每 30 分钟重试一次（无需有人说话），深夜 0-8 点静默."""
    state = load_state()
    group = state.get(cid) or {}
    # 开关（默认关，需 /吐槽开 开启）
    if group.get("devil_enabled") is not True:
        return ""
    # 深夜静默：0-8 点不触发
    if datetime.now().hour < _tune("devil.cold_quiet_hour", cid):
        return ""
    # 互斥：任何 active 状态都不吐
    if (_get_vote_state(cid).get("active") or _get_story_state(cid).get("active")
            or _get_dj_state(cid).get("active") or _get_perf_state(cid).get("active")
            or _get_fasao_state(cid).get("active")):
        return ""
    # 独立冷却（冷场吐槽专用，30 分钟）
    now = time.time()
    if now - group.get("devil_cold_last", 0) < _tune("devil.cold_cooldown", cid):
        return ""
    # 每日上限（跨天重置，与恶魔吐槽共享）
    today = datetime.now().strftime("%Y-%m-%d")
    if group.get("devil_daily_date") != today:
        group["devil_daily_count"] = 0
        group["devil_daily_date"] = today
    if group.get("devil_daily_count", 0) >= DEVIL_DAILY_MAX:
        return ""
    # 冷清判断
    last_msg = group.get("last_msg_time", 0)
    if last_msg == 0:
        return ""  # 还没见过消息（刚启动），不算冷场
    idle = now - last_msg
    if idle < _tune("devil.cold_threshold", cid):
        return ""
    # 冷清后 roll 一次概率：不中则更新周期标记（30 分钟后再试），这轮安静
    if random.random() >= _tune("devil.cold_prob", cid):
        group["devil_cold_last"] = now
        state[cid] = group
        _save_group(cid, group)
        log("🎲 [冷场] [" + cid[:8] + "] roll 不中（" + str(int(_tune("devil.cold_prob", cid) * 100)) + "%），30 分钟后再试")
        return ""
    # 命中：生成（吐槽冷清）
    minutes = int(idle // 60)
    context = "（群里已经 " + str(minutes) + " 分钟没人说话了，冷场中，请你用可爱的语气冒个泡，撒娇问问大家怎么都不说话呀）"
    try:
        roast = generate_devil_roast(context, "", force_cute=True)
    except Exception:
        roast = ""
    roast = (roast or "").strip()
    if not roast:
        # 生成失败也更新周期标记（30 分钟后再试，避免反复重试刷屏）
        group["devil_cold_last"] = now
        state[cid] = group
        _save_group(cid, group)
        return ""
    # 更新 + save
    group["devil_cold_last"] = now
    group["devil_daily_count"] = group.get("devil_daily_count", 0) + 1
    state[cid] = group
    _save_group(cid, group)
    return roast
