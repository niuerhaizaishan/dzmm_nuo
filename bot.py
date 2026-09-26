#!/usr/bin/env python3
"""DZMM Agent — c5269e31 专用。全异步 + Playwright 常驻复用，低延迟."""
# build-marker: dsh-link-check-2026-08-31  # dsh 部署链路验证标记（无害，可删）
import asyncio, json, os, sys, time, random, hashlib, re, subprocess, unicodedata
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HERMES_PY = sys.executable  # auto_recover 重启 bot 用（当前解释器，容器/裸机通用）
PAGE_CACHE_MAX = 3  # 常驻页面池上限（LRU 淘汰）；1.8G 的机器上别开太多 chromium 页

# ---------------------------------------------------------------- 群清单（P11 第 A 期）
# 下面这几个**字面量现在只是默认值**：真正的来源是 config.json
# （`runtime/config_store.py`）。文件不存在时行为与改动前**逐字一致**，
# 所以"删掉 config.json"就是最简单的回滚。
_GROUPS_DEFAULT = [
    "bd7fe6a4-0065-4551-ad3d-94116dba8f9c",  # 认证妹子群（主群，常驻）
    "93cc6c7b-ba9e-48f3-a106-3074483aebbd",  # 裳姐姐和她的骚贱小母狗
    "38698306-4252-438a-aa56-7dbab89250eb",  # 被dzmm团本折磨的三人
    "007bfd75-2efb-491c-8233-20046af69b24",  # 机电驱动与智能算法研讨群
    "4a56be71-9ca6-4785-b45f-18725d187f1a",  # Rose（505）
]
# 静默监控群：只读不回（不回复、不接指令、不筛查、不发欢迎语、不推送、不同步档案）。
# 想恢复功能，把 cid 从这张表里删掉即可。
_MUTE_CIDS_DEFAULT = set()
# 入群筛查生效的群（踢游客 / 踢男性 / 提醒未认证 + 「妹子认证群」文案）。
# 不在这张表里的群：新人入群只发中性欢迎语，不踢人、不发认证群文案。
_SCREEN_CIDS_DEFAULT = {
    "bd7fe6a4-0065-4551-ad3d-94116dba8f9c",  # 认证妹子群
    "93cc6c7b-ba9e-48f3-a106-3074483aebbd",  # 裳姐姐和她的骚贱小母狗
    "38698306-4252-438a-aa56-7dbab89250eb",  # 被dzmm团本折磨的三人
}
# 定时推送生效的群（早/中/晚问候、12·18 点骚度排行、每日记忆汇报）
_PUSH_CIDS_DEFAULT = [
    "bd7fe6a4-0065-4551-ad3d-94116dba8f9c",  # 认证妹子群
    "007bfd75-2efb-491c-8233-20046af69b24",  # 机电驱动与智能算法研讨群
]
# 群别名：只用于**显示**（日志/运维后台），不参与任何判定
_GROUP_ALIASES = {
    "bd7fe6a4-0065-4551-ad3d-94116dba8f9c": "认证妹子群",
    "93cc6c7b-ba9e-48f3-a106-3074483aebbd": "裳姐姐和她的骚贱小母狗",
    "38698306-4252-438a-aa56-7dbab89250eb": "被dzmm团本折磨的三人",
    "007bfd75-2efb-491c-8233-20046af69b24": "机电驱动与智能算法研讨群",
    "4a56be71-9ca6-4785-b45f-18725d187f1a": "Rose（505）玫瑰房",
}

# 房间群（独立人设 + 屋主权限）：配置见 personas.ROOMS
import personas as _personas           # P12-E：人设文本/话术自证
from personas import (ROOMS, PERSONAS, apply_persona, set_cid as set_persona_cid,
                      set_config_personas, config_personas,
                      set_content_overrides as set_persona_texts,
                      ROOM_GREET_TEMPLATE, ROOM_GREET_ITEMS, ROOM_GREET_LINES,
                      ROOM_GUEST_REFUSE, ROOM_ORDER_VERBS, ROOM_MENU_WORDS,
                      ROOM_LEWD_WORDS, ROOM_FLOWER_WORDS, ROOM_FLOWER_NOUNS,
                      ROOM_FLOWER_VERBS, ROOM_GHOST_TEXT, ROOM_GHOST_WARN,
                      ROOM_GHOST_BACK, ROOM_GHOST_WARN_COOLDOWN,
                      ROOM_GHOST_MAX_H, ROOM_GHOST_COOLDOWN, ROSE_CID, VV_UID, NUONUO_UID,
                      ROSE_SIGNS, ROSE_SIGN_WORDS,
                      ROSE_LUCKY_ITEMS, ROSE_LUCKY_DIRS, ROSE_LUCKY_COLORS,
                      ROSE_SIGN_YI, ROSE_SIGN_JI, ROSE_MOONS, p)
ROOM_CIDS = set(ROOMS.keys())

from runtime.tuning import get as _tune   # P12-B：可调参数（唯一读口）
from runtime import tuning as _tuning     # P12-B：参数层自证/注入
from runtime import config_store  # P11 A 期：群清单/Bot 注册表的外部配置

# Bot 注册表的默认值：与现状等价（单个 Bot，绑认证群）。
# 构造逻辑放在 config_store 里，**运维后台共用同一份** —— 否则面板显示的
# Bot 列表和实际生效的可能不一致，那是运维后台最不该犯的错。
_BOTS_DEFAULT = config_store.default_bots_from_env(_GROUPS_DEFAULT[0])

try:
    _CFG = config_store.load({
        "groups": _GROUPS_DEFAULT,
        "mute": _MUTE_CIDS_DEFAULT,
        "screen": _SCREEN_CIDS_DEFAULT,
        "push": _PUSH_CIDS_DEFAULT,
        "aliases": _GROUP_ALIASES,
        "bots": _BOTS_DEFAULT,
        # 人设白名单取自 personas.PERSONAS 本身（唯一真源）。
        # 原先是硬编码的 ("nuo", "rose_butler")：加一个人设要改两处，
        # 而运维后台读不到那份硬编码 ⇒ 页面上能存进一个不存在的人设名，
        # 运行期又静默回落默认（"看起来配了、其实没生效"）。
        "personas": tuple(PERSONAS),
    })
except config_store.ConfigError as _e:
    raise SystemExit("配置不合法，拒绝启动：{}".format(_e))

# 把配置里指定的按群人设注入 personas 层（覆盖 ROOMS 硬编码）。
# 必须在下面任何一次 apply_persona / p() 之前完成。
set_config_personas({s.cid: s.persona for s in _CFG.group_specs.values()})
# P12-E：人设**文本/话术**的覆盖（schema v3 的 personas 节）。
# 与上面那行是两件事：上面选"用哪个人设"，这里改"那个人设说什么话"。
set_persona_texts(_CFG.personas)

# P12-C：schema v2 的三块覆盖**从这里进运行期**。
# 这是"页面上配了 → 运行期真的生效"的唯一一条通路：
#   features  → runtime/features.py 的覆盖表（"cid|key" / "key"）
#   tuning    → runtime/tuning.py 的覆盖表
#   scopes    → 按群集合（trend / recap）
# 没写 = 用代码默认值（= 今天的行为），所以旧配置（v1）升级后行为不变。
#
# ⚠️ 必须放在**运行时导入之后**（`_features` 在下面那批 import 里才出现），
# 而且要在任何一次 `_feat_on()` / `_tune()` 调用之前。
_FEATURES_DEFAULT_KW = {
    "groups": _GROUPS_DEFAULT, "mute": _MUTE_CIDS_DEFAULT,
    "screen": _SCREEN_CIDS_DEFAULT, "push": _PUSH_CIDS_DEFAULT,
    "aliases": _GROUP_ALIASES, "bots": _BOTS_DEFAULT,
    "personas": tuple(PERSONAS),
}


def apply_config_overrides(cfg=None) -> None:
    """把配置里的热配置（功能/参数/集合/人设）注入运行期。

    P12-F 起这里**只有一份实现**：`runtime/hotload.py`（热加载与启动走同一条路，
    免得"启动时生效的"和"热加载生效的"慢慢长成两套）。

    `_features` / `_tuning` 在**运行时导入**那块才出现，所以这个函数
    必须在那边之后调用。
    """
    c = cfg if cfg is not None else _CFG
    try:
        from runtime import hotload as _hotload
        _hotload.apply_cfg(c, source="startup")
        return
    except Exception as e:                                     # noqa: BLE001
        # hotload 不可用（老版本/异常）时退回逐项注入：**行为不变优先**
        log("P12: hotload 不可用（{}），退回逐项注入".format(str(e)[:60]))
    _features.set_overrides(c.feature_map())
    _tuning.set_overrides(c.tuning_map())
    _tuning.set_scopes(c.scopes)
    set_persona_texts(c.personas)          # P12-E：人设文本/话术覆盖
    set_config_personas({s.cid: s.persona for s in c.group_specs.values()})


def _live_structural() -> dict:
    """交给 `hotload`：**启动时定下来**的结构性配置（用来判断要不要重启）。

    只读 bot 里的运行期镜像（`GROUPS`/`MUTE_CIDS`/…），不是文件 —— 这正是要点：
    文件里改了群/Bot，运行期是不知道的，页面要如实说"需要重启"。
    """
    return {
        "groups": tuple(GROUPS),
        "mute": tuple(sorted(MUTE_CIDS)),
        "screen": tuple(sorted(SCREEN_CIDS)),
        "push": tuple(PUSH_CIDS),
        "bots": tuple((b.key, b.cid) for b in _CFG.bots if b.enabled),
    }


def hot_reload_check() -> None:
    """主循环每轮调一次（P12-F）：配置/提示词改了就地生效，不用重启。

    出错不抛（热加载的问题绝不能让机器人停摆），只在日志里说清楚。
    """
    try:
        from runtime import hotload as _hotload
        rep = _hotload.maybe_reload()
    except Exception as e:                                     # noqa: BLE001
        log("P12: 热加载检查异常（不影响运行）：" + str(e)[:100])
        return
    if not rep:
        return
    _log_hotload(rep)


def _log_hotload(rep) -> None:
    """热加载自证：**改了什么、什么时候、要不要重启**，一行说清。"""
    ch = rep.get("changes") or {}
    n = sum(len(v or []) for v in ch.values())
    if rep.get("invalid") or not rep.get("ok", True):
        log("P12: 热加载 **未生效**（配置文件不合法，运行期保持原样）：{}".format(
            "；".join(rep.get("errors") or [])[:200]))
        return
    if n:
        for cat, items in ch.items():
            for line in (items or []):
                log("P12: 热加载 {} · {}".format(_HOT_CAT.get(cat, cat), line))
    _c = rep.get("counts") or {}
    log("P12: 热加载完成 来源={} 改动={} 项 生效 功能={} 参数={} 集合={} 人设={} prompts={}".format(
        rep.get("source"), n,
        _c.get("features", "-"), _c.get("tuning", "-"), _c.get("scopes", "-"),
        _c.get("personas", "-"), len(rep.get("prompts") or [])))
    for line in (rep.get("structural") or []):
        log("P12: ⚠️ 需要重启才生效 · " + line)
    for line in (rep.get("errors") or []):
        log("P12: 热加载有异常（已跳过）· " + line)


_HOT_CAT = {"features": "功能", "tuning": "参数", "scopes": "按群集合",
            "personas": "人设", "prompts": "提示词"}


def migrate_config_file() -> None:
    """旧版配置在**启动时**升级落盘（读路径不写文件，见 config_store.migrate_file）。

    放在运行时导入之后（这里才拿得到 `log`）。不需要 reload `_CFG` ——
    `_parse` 内部已把旧版在**内存里**升级过了，这里只是让磁盘上的版本跟上。
    """
    try:
        m = config_store.migrate_file(dict(_FEATURES_DEFAULT_KW))
        if m.get("migrated"):
            log("P11: 配置已从 v{} 升级到 v{}（已备份 {}）".format(
                m["from"], m["to"], m.get("backup") or "无"))
        elif m.get("error"):
            log("P11: 配置未升级（{}）".format(m["error"]))
    except Exception as e:                                     # noqa: BLE001
        log("P11: 配置升级检查异常（不影响启动）：" + str(e)[:80])

GROUPS = list(_CFG.groups)
MUTE_CIDS = set(_CFG.mute)
SCREEN_CIDS = set(_CFG.screen)
PUSH_CIDS = list(_CFG.push)
_ACTIVE_CID = GROUPS[0]  # 当前处理的群，主循环每次迭代设置；真源在 runtime/ctx.py


# 涩涩骰子词库

# XP → 场景/体位/附加 偏好（发骚骰子按目标 XP 定制）

from dzmm import get_messages, get_messages_all, get_messages_since, get_user, get_chatroom_name, get_members, remove_member, get_verified, get_private_cid
from dice_game import roll_dice, roll_wild, dice_str, count_point, is_legal_raise, judge_open, parse_call
from brains import generate_reply, generate_summary, generate_profile_summary, generate_person_summary, generate_rank, generate_insult, generate_insult_continue, generate_poem, generate_poem_theme, generate_archive, generate_fortune, generate_greeting, generate_horoscope, generate_recap, generate_trend, generate_defend, generate_scene, generate_scene_continue, generate_scene_end, generate_scene_rewrite, generate_xp, generate_diaojiao, generate_diaojiao_end, generate_perf_enrich, generate_perf_insult, generate_perf_comment, generate_perf_ending, generate_perf_novel, generate_perf_novel_chunked, generate_heat_judge, generate_heat_climax, generate_heat_fasao, generate_story_start, generate_story_step, generate_story_end, generate_vote_options, generate_devil_roast, generate_memory_summary, POEM_STYLES, _call_llm
from profiles import get_profile, upsert_profile, load_profiles, save_profiles
from wx_alert import send_weixin
import token_store
import env_config
import state_store                                    # P7：状态存储后端（默认 SQLite）
from runtime import ctx                               # P8 第 4 步：当前群上下文的唯一真源
from runtime.ctx import PRIMARY_CID                   # 常驻群 cid 的唯一真源（Sender 默认参数要用）
from runtime.obs import log  # P8 第 4 步：搬到 runtime/obs.py
from runtime.identity import self_uids, is_self, self_names, self_names_longest_first, _load_member_names, get_full_name, get_name, is_at_me, mentions_self, NUO_UID, BOT_UID, BOT_NAME, BOT_NAME_SHORT, _name_cache, register_bots, resolve_bot_uids as _resolve_bot_uids, learn_bot_uid, bot_uids  # P8 第 4 步：搬到 runtime/identity.py（P12-H 加多 Bot 身份）
from runtime.dice import _dice_state, _dice_save, _dice_current_player, _dice_send_private, _dice_board_text, _dice_deal_round, _dice_start_cmd, _dice_join_cmd, _dice_exit_cmd, _dice_begin_cmd, _dice_next_round_cmd, _dice_call_cmd, _dice_open_cmd, _dice_quit_cmd, _dice_skip_cmd, _dice_status_text  # P8 第 4 步：搬到 runtime/dice.py
from runtime.text import _get_opencc, _normalize_cn, _simplify, _strip_parenthesis, _is_stop_word, _dedupe_options, _normalize_choice, _extract_deadline_seconds, _squash, _clean_name, _norm_poem, _CJK_VARIANT, STOP_WORDS, _OPENCC_T2S  # P8 第 4 步：搬到 runtime/text.py
from runtime.members import _extract_msgs, _get_msgs_by_uid, _member_profile_text, _find_in_profiles, _find_user_in_msgs, find_user_by_name, _find_uid_by_name, _gender_label, _random_member_uid, _archive_text_from_profile, _history_lines, _profile_history, HISTORY_MAX  # P8 第 4 步：搬到 runtime/members.py
from runtime.fortune import is_fortune_cmd, fortune, _fortune_calc, _fortune_score, _fortune_level, _record_fortune, _get_fortune_history, _char_wx, _name_wx, _ganzhi, _relation, FORTUNE_APOLOGY, _GAN, _ZHI, _WX_GAN, _WX_ZHI, _GZ_BASE, _WX_ORDER, _WX_BASE, _WX_LUCK, _WX_YI, _WX_JI, _SHENG, _KE, _CHONG, _SHENGXIAO, _CHAR_WX, _REL_SCORE, _REL_TEXT, _LIUCHONG, _LIUHE, _ZHI_SCORE, _ZHI_PENALTY  # P8 第 4 步：搬到 runtime/fortune.py
from runtime.trend import is_trend_cmd, is_trend_board_cmd, _trend_days, _trend_spark, _trend_chong, _trend_stats, _wd_cn, _md_cn, week_trend, week_trend_board, TREND_CIDS, TREND_DEFAULT_DAYS, TREND_MAX_DAYS  # P8 第 4 步：搬到 runtime/trend.py
from runtime.recap import is_recap_cmd, _recap_intent, _recap_window, _day0, _msg_ts, _fmt_ts, _recap_mentions, _recap_transcript, recap_absent, RECAP_CIDS, RECAP_MAX_MSGS, RECAP_MAX_HOURS, RECAP_COOLDOWN  # P8 第 4 步：搬到 runtime/recap.py
from runtime.sign import _room_owner, _is_room_trigger, _is_flower_grab, _sign_from_md, _sign_intent, _sign_find, _sign_parse_birth, _sign_stored, _sign_remember, _sign_calc, _sign_recent, _sign_card, _sign_reply, _ROOM_LAYOUT_WORDS, _ROOM_CONJURE_WORDS, _SIGN_FULL, _SIGN_SHORT, _SIGN_ME_RE, _SIGN_NAMES  # P8 第 4 步：搬到 runtime/sign.py
from runtime.group_state import _get_fasao_state, _set_fasao_state, _get_dj_state, _set_dj_state, _get_perf_state, _set_perf_state, _get_vote_state, _set_vote_state, _get_story_state, _set_story_state, _get_memory, _remember, _set_last_action, _get_last_action, MEMORY_MAX  # P8 第 4 步：搬到 runtime/group_state.py
from runtime import features as _features   # P12-A：功能注册表（唯一开关判定点）

# P12-C：配置里的三块覆盖，在这里（运行时导入之后、任何 _feat_on/_tune 之前）
# 正式进运行期。
apply_config_overrides()

# P12-H：把配置里的**所有** Bot 注册成"我自己"。
# 少了这一步，新 Bot 在自己群里发的消息会被当成群友 —— 轻则统计污染，
# 重则自问自答（它是"回声过滤"的唯一真源，见 runtime/identity.py）。
# ⚠️ 必须在 identity 的 import 之后（第 233 行那批）。
try:
    _BOT_REG = register_bots(_CFG.bots_for_identity())
    log("P11: 我的身份数={}（账号 1 + Bot {}）".format(
        1 + len(_BOT_REG), len(_BOT_REG)))
    for _b in _CFG.bots_for_identity():
        log("P11: 我的身份 Bot key={} name={} cid={} uid={}".format(
            _b["key"], _b["name"] or "-", (_b["cid"] or "")[:8],
            (_b["uid"] or "**未配置**（启动时按成员表里的名字认）")))
except Exception as _e:                                        # noqa: BLE001
    log("P11: 多 Bot 身份注册失败（只认账号）：" + str(_e)[:80])

# 旧版配置升级落盘（必须在运行时导入之后 —— 这里才拿得到 log）
migrate_config_file()

# P12-F：热加载的初始化 —— 记下"启动时那份配置/提示词"的指纹，
# 主循环靠它判断有没有人改过（改了就就地生效，不用重启）。
# ⚠️ 必须在 `migrate_config_file()` **之后**：升级会重写 config.json（mtime 变了），
# 先注册指纹的话，主循环第一轮就会把"刚才那次升级"当成一次新改动重放一遍。
try:
    from runtime import hotload as _hotload
    _hotload.configure(dict(_FEATURES_DEFAULT_KW), _live_structural)
    log("P12: 热加载={}（改完配置/提示词就地生效；群与 Bot 仍需重启）".format(
        "启用" if _hotload.status()["enabled"] else "已关闭"))
except Exception as _e:                                        # noqa: BLE001
    log("P12: 热加载未启用（" + str(_e)[:80] + "）")
from runtime.heat import _add_heat, HEAT_SCENE_PICK, HEAT_SCENE_CONTINUE_TARGET, HEAT_SCENE_CONTINUE_SENDER, HEAT_INSULT_TARGET, HEAT_INSULT_SENDER, HEAT_FASAO_PICK, HEAT_DIAOJIAO_ROUND, HEAT_VOTE_STARTER, HEAT_VOTE_VOTER, HEAT_STORY_VOTER, HEAT_STORY_PROTAGONIST  # P8 第 4 步：搬到 runtime/heat.py
from runtime.memory import _extract_names, _extract_keywords, _fast_rank, _ai_filter, _retrieve_memory, _append_memory_ref, _extract_remember, is_remember_cmd, is_recall_cmd, _record_history, _on_rename, _refresh_profile_latest, MEMORY_TRIGGERS, MEMORY_FILTER_PROMPT  # P8 第 4 步：搬到 runtime/memory.py
from runtime.poem import is_poem_cmd, poem, _do_poem, _poem_text, _poem_resolve, _record_poem, _get_poem_history, _parse_poem_style, POEM_STYLE_KEYWORDS  # P8 第 4 步：搬到 runtime/poem.py
from runtime.members import _extract_mentioned_members  # P8 第 4 步：搬到 runtime/members.py
from runtime.novel import _extract_novel_title, _novel_filename, _novel_save_path, _group_name_cache  # P8 第 4 步：搬到 runtime/novel.py
from runtime.vote import is_vote_start_cmd, is_vote_end_cmd, is_vote_choice_cmd, _extract_vote_title, _vote_members_text, start_vote, _record_vote, _finish_vote, _check_vote, VOTE_DEFAULT_SECONDS  # P8 第 4 步：搬到 runtime/vote.py
from runtime.story import is_story_start_cmd, is_story_end_cmd, start_story, _check_story, _finish_story, _advance_story, _record_story_vote, _story_vote_result, STORY_MAX_ROUND, STORY_VOTE_SECONDS  # P8 第 4 步：搬到 runtime/story.py
from runtime.perf import _detect_perf_query, summarize_person_perf, is_perf_start_cmd, is_perf_end_cmd, _get_perf_content, _get_perf_content_full, _perf_heat_target, _perf_m_name, _perf_send, _handle_perf_start, _perf_end_bg, _handle_perf_end, _check_perf_interact, OWNER_UID  # P8 第 4 步：搬到 runtime/perf.py
from runtime.fasao import is_fasao_cmd, fasao, is_scene_cmd, _parse_scene_target, scene, is_fasao_end_cmd, _check_fasao_timeout, end_fasao, _pick_combo_by_xp, FASAO_TIMEOUT, SCENES, POSITIONS, EXTRAS, XP_BIAS  # P8 第 4 步：搬到 runtime/fasao.py
from runtime.perf import _check_perf_timeout  # P12-A：公演超时解锁（原先没有超时）
from runtime.diaojiao import is_dj_end_cmd, _dj_target_text, _dj_end_text, is_diaojiao_cmd, start_diaojiao, _check_diaojiao, DJ_PARTICIPATE_HINT  # P8 第 4 步：搬到 runtime/diaojiao.py

# DZMM 主站 origin（多线路切换：env_config 从 DZMM_HOST 环境变量读）
ORIGIN = env_config.get_dzmm_origin()

# Cookie 缓存（30s 刷新一次，避免每次读 token JSON）
_cookie_cache = {"cookie_str": "", "ts": 0.0}

# 撞验证码状态（进程内全局）：撞验证码时置 True，HTTP 恢复后置 False
_captcha_active = False
_captcha_notified = {}  # cid -> bool，撞验证码群里说明是否已发（每群一次防刷屏）

# 撞验证码相关文案
# ⚠️ 自称一律写 `{me}` —— 这里是**代码默认值**（默认人设 = 小小糯），发出去之前
# 会过 `personas.fill(text, cid)`：认证群填「小小糯」、玫瑰房填「薇丝」。
# 直接写死名字的话，玫瑰房会冒出一个客人不认识的「小小糯」。
CAPTCHA_NOTICE = ("⚠️ {me}撞上平台验证码啦～查资料、踢人、新人筛查暂时受限，"
                  "聊天收发不受影响哦～风控解除会自动恢复，稍安勿躁～♡")
CAPTCHA_JOIN_NOTICE = ("⚠️ 检测到新成员「{name}」加入，但当前撞平台验证码、暂时核实不了身份～"
                       "恢复后男生和游客会被自动踢出，其他情况会提醒管理员处理哦～♡")
CAPTCHA_RECOVERED_NOTICE = ("✅ 验证码风控解除啦～查资料、踢人、新人筛查都恢复正常了哦～"
                            "之前延迟的新成员会陆续补筛查，稍等片刻～♡")

# 页面会话刷新：页面存活超 2 小时强制 reload（兜底防会话老化；token 变化也会触发 reload）
PAGE_MAX_AGE_SEC = 7200


# 状态存储：P7 起默认走 SQLite（`state_store`），`DZMM_STORE=json` 可回退。
# 函数名与签名保持不变 —— 下面约 45 个调用点一处都不用改。
# 旧实现的问题（已核实）：写是**非原子**的 write_text（文件 bind mount 上 os.replace
# 会 EBUSY），写到一半被 kill 会留下截断 JSON，而解析失败时**静默返回 {}**，
# 导致全群发情值/高潮/状态机/记忆一起无声清零。
def load_state() -> dict:
    """读全部群状态 `{cid: {...}}`。"""
    return state_store.load_state()


def save_state(s: dict) -> None:
    state_store.save_state(s)


def get_cached_cookie() -> str:
    """缓存 cookie 字符串，30s 刷新一次."""
    if time.time() - _cookie_cache["ts"] < 30 and _cookie_cache["cookie_str"]:
        return _cookie_cache["cookie_str"]
    _cookie_cache["cookie_str"] = token_store.get_cookie_string()
    _cookie_cache["ts"] = time.time()
    return _cookie_cache["cookie_str"]


def _set_active_cid(cid: str) -> None:
    """设置「当前正在处理的群」。

    P8 第 4 步起 `_ACTIVE_CID` 有两个消费者：bot.py 自己（24 个读点，保持不变）
    和 `runtime/ctx`（搬出去的模块从那里读）。**唯一**允许写它的地方就是这里 ——
    镜像与真源一起更新，写点分散就会出现"搬出去的模块读到旧的群"这种隐性错。
    一致性由 tests/test_p8s4_split.py 与部署闸门钉住。
    """
    global _ACTIVE_CID
    _ACTIVE_CID = cid
    ctx.set_active_cid(cid)


class Sender:
    """常驻 Playwright 浏览器：每个群一个常驻 page，发送时不再来回 goto。

    （2026-09-14 提速）原来每次给非主群发消息都要 goto 目标群 → 发完再 goto 回主群，
    两次整页导航约 4~6 秒，占一次回复耗时的大头。改成每群一个常驻页面后，
    发送只剩「输入 + 回车 + 读回验证」，从 8~17 秒降到 2~4 秒。
    """

    def __init__(self, profile_dir: str = "pw_profile", primary_cid: str = PRIMARY_CID):
        self.ctx = None
        self.page = None
        self.current_cid = None
        self.profile_dir = profile_dir
        self.primary_cid = primary_cid
        self.pages = {}  # cid -> {"page":..., "ts":..., "loaded":..., "cid":...}（常驻页面池，LRU）
        self._send_locks = {}  # cid -> asyncio.Lock（同群发送串行化，防并发打字串行错乱）
        self._token_dirty = False  # token 变化 → 下次用到某页时按需 reload（不再无条件重载主群页）
        self._fail_streak = 0  # 浏览器连续失败计数（连续失败触发自动重启）
        self._consecutive_send_fail = 0  # 连续发送失败计数（含兜底失败，自愈无效告警用）
        self._send_alerted = False  # 发送失败告警防重（恢复后重置）
        self._last_token_value = ""  # 上次记录的 token 值（检测变化触发页面 reload）
        self._last_reload = 0.0      # 上次页面 reload 时间（兜底防会话老化）

    async def start(self):
        from playwright.async_api import async_playwright
        cookies = token_store.get_cookies_for_playwright()
        self._last_token_value = next((c.get("value", "") for c in cookies
                                       if c.get("name") == "sb-rls-auth-token"), "")
        self._pw = await async_playwright().start()
        proxy_url = env_config.get_proxy_url()
        self.ctx = await self._pw.chromium.launch_persistent_context(
            str(ROOT / self.profile_dir), headless=True, channel=env_config.get_channel(),
            viewport={"width": 1280, "height": 800},
            proxy={"server": proxy_url} if proxy_url else None,
            # 消 headless 指纹（2026-08-22 实测）：去掉 navigator.webdriver + 伪装真实 Chrome UA
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
        await self.ctx.add_cookies(cookies)
        # 预加载常驻群（每个群一张常驻页），之后发送不再 goto
        await self.navigate(self.primary_cid)
        log("sender ready（常驻页面 " + str(len(self.pages)) + " 张）")

    async def _page_for(self, cid: str):
        """取该群的常驻页面（懒创建 + LRU 淘汰）；私聊共用一个 scratch 页。返回页面条目."""
        key = cid if cid in GROUPS else "__scratch__"
        ent = self.pages.get(key)
        if ent is not None and ent["page"].is_closed():
            self.pages.pop(key, None)
            ent = None
        if ent is None:
            if len(self.pages) >= PAGE_CACHE_MAX:
                # 淘汰最久未用的页；主群页永不淘汰（它最忙，被淘汰后每次问候/排行都要重载）
                cands = [(k, v) for k, v in self.pages.items() if k != self.primary_cid]
                if not cands:
                    cands = list(self.pages.items())
                oldest = min(cands, key=lambda kv: kv[1]["ts"])
                try:
                    await oldest[1]["page"].close()
                except Exception:
                    pass
                self.pages.pop(oldest[0], None)
            ent = {"page": await self.ctx.new_page(), "ts": time.time(),
                   "loaded": False, "cid": None}
            self.pages[key] = ent
        ent["ts"] = time.time()
        self.page = ent["page"]
        return ent

    async def _load_group(self, page, cid: str) -> bool:
        """把某个页面加载到指定群的聊天页并等输入框就绪."""
        try:
            await page.goto(ORIGIN + "/chat?c=" + cid, wait_until="commit", timeout=30000)
        except Exception as e:
            log("  goto 失败: " + str(e)[:50])
            return False
        for _ in range(80):  # 80 × 0.25s = 20s
            await self._dismiss_overlay(page)
            try:
                ta = await page.query_selector('textarea[placeholder*="输入消息"]')
                if ta and await ta.is_visible():
                    await asyncio.sleep(0.4)  # 预热：等 React 受控组件就绪
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        return False

    async def navigate(self, cid: str) -> bool:
        """切到指定群的页面（常驻页直接复用，只有新建/掉线/需重载时才 goto）."""
        ent = await self._page_for(cid)
        page = ent["page"]
        self.current_cid = cid
        dirty = self._token_dirty or (time.time() - self._last_reload > PAGE_MAX_AGE_SEC)
        if ent["loaded"] and ent["cid"] == cid and not dirty:
            try:
                await self._dismiss_overlay(page)
                ta = await page.query_selector('textarea[placeholder*="输入消息"]')
                if ta and await ta.is_visible():
                    return True
            except Exception:
                pass
        ok = await self._load_group(page, cid)
        ent["loaded"], ent["cid"] = ok, cid
        if ok:
            self._token_dirty = False
            self._last_reload = time.time()
        return ok

    async def _goto_primary(self) -> None:
        """切到常驻群（兼容旧调用；现在是复用常驻页面，不再 goto）."""
        await self.navigate(self.primary_cid)

    async def _dismiss_overlay(self, page=None) -> None:
        """关闭可能遮挡输入框的公告/Modal 弹窗。无遮罩时零副作用、零按钮查询。"""
        page = page or self.page
        if page is None:
            return
        try:
            # 先检测是否有全屏遮罩（公告弹窗特征：fixed + inset-0 覆盖全屏 + 可见）。
            # 无遮罩直接返回——避免误点聊天页其他 [aria-label="Close"] 按钮（图片预览/面板等）。
            has_overlay = await page.evaluate(
                """() => {
                    for (const el of document.querySelectorAll('div.fixed.inset-0')) {
                        const st = getComputedStyle(el);
                        if (st.display !== 'none' && st.visibility !== 'hidden') return true;
                    }
                    return false;
                }"""
            )
            if not has_overlay:
                return
            # 有遮罩才找关闭按钮：优先「知道了」类确认按钮，× 图标兜底
            for sel in ('button:has-text("知道了")', 'button:has-text("我知道了")',
                        '[aria-label="Close"]'):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=3000)
                        log("  dismiss overlay: 关闭弹窗 [" + sel + "]")
                        await asyncio.sleep(0.5)
                        return
                except Exception:
                    continue
        except Exception:
            pass

    async def _refresh_token(self) -> bool:
        """发消息前刷新 cookie（从 JSON 读最新值覆盖，防 Playwright 旧 cookie 过期）。

        为什么无条件：renew_token 每 5 分钟续期，JSON 里 token 永远新鲜（剩余 >600s），
        但 Playwright context 里的 cookie 只在 add_cookies 时更新。若长时间不发消息，
        Playwright 旧 cookie 已过期，用「剩余时间」判断会误判为新鲜而不刷新。
        add_cookies 是本地操作（<50ms），无条件刷新代价可忽略。

        token 变化检测：DZMM 前端页面加载时把 cookie 会话读入内存，renew 续期更新 cookie
        后页面内存会话仍是旧的 → 发送「假成功」（Playwright 操作成功但消息未投递）。
        因此检测到 token 值变化（或页面存活超 2 小时兜底）时 reload 页面，让 JS 重读新 cookie。
        返回是否成功。
        """
        try:
            if not self.ctx:
                return True  # dry 模式无浏览器上下文，跳过
            cookies = token_store.get_cookies_for_playwright()
            new_token = next((c.get("value", "") for c in cookies
                              if c.get("name") == "sb-rls-auth-token"), "")
            token_changed = bool(new_token) and new_token != self._last_token_value
            page_old = time.time() - self._last_reload > PAGE_MAX_AGE_SEC
            token_expired = self._access_expired()
            await self.ctx.add_cookies(cookies)
            # token 变了才标记「需重载」（防假成功）；改成惰性：下次用到哪张页面就只重载那一张，
            # 不再无条件重载主群页（多群常驻后，无条件重载会白等一次整页导航）。
            # access_token 过期时跳过 page_old 强制重载（撞验证码+过期时重载会重新认证失败，
            # 保持旧会话反而能继续收发）
            if token_changed or (page_old and not token_expired):
                self._token_dirty = True
                self._last_token_value = new_token
            return True
        except Exception as e:
            log("  token 刷新失败: " + str(e)[:60])
            return False

    def _access_expired(self) -> bool:
        """读 token JSON 判断 access_token 是否已过期（本地判断，不打网络）。"""
        try:
            import base64
            c = token_store.get_cookie("sb-rls-auth-token")
            if not c:
                return True
            v = c.get("value", "")
            payload = v[len("base64-"):] if v.startswith("base64-") else v
            payload += "=" * (-len(payload) % 4)
            d = json.loads(base64.b64decode(payload))
            return d.get("expires_at", 0) - time.time() < 0
        except Exception:
            return True

    def _record_fail(self) -> None:
        """发送失败计数：撞验证码时不累积（navigate 失败是风控拦截，重启也救不回来，只空转）。"""
        if not _captcha_active:
            self._fail_streak = self._fail_streak + 1

    async def reload_page(self) -> None:
        """重新加载当前页面（让页面 JS 重新从 cookie 读会话，防发送假成功）."""
        try:
            log("  ♻️ token 已更新，reload 页面刷新会话...")
            cid = self.current_cid or self.primary_cid
            ent = await self._page_for(cid)
            ent["loaded"] = await self._load_group(ent["page"], cid)
            ent["cid"] = cid
            if ent["loaded"]:
                self._token_dirty = False
                self._last_reload = time.time()
            log("  ♻️ reload 完成（已预热）")
        except Exception as e:
            log("  ♻️ reload 失败: " + str(e)[:60])
            self._last_reload = time.time()

    async def send(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        """按群串行化发送（后台任务和主循环可能同时往同一个群发，共用一张页面，
        并发打字会串行错乱）."""
        lock = self._send_locks.get(cid)
        if lock is None:
            lock = self._send_locks[cid] = asyncio.Lock()
        async with lock:
            return await self._send_locked(text, cid, allow_fallback)

    async def _send_locked(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        """发送消息；navigate 间歇失败/浏览器卡顿自动重试一次.
        allow_fallback=False 时纯 Playwright 发送，不走 Socket.IO 兜底（私聊发骰用，避免假成功）。"""
        # 热更新 token：发消息前确保 cookie 新鲜（renew_token 续期后零中断生效）
        if not await self._refresh_token():
            log("⚠️ token 热更新失败，主动退出交给 entrypoint 重启")
            sys.exit(1)
        for attempt in range(2):
            if attempt > 0:
                await asyncio.sleep(2)
            _t0 = time.time()
            try:
                if not await self.navigate(cid):
                    log("  send fail: navigate 失败 cid=" + cid[:8])
                    self._record_fail()
                    continue
                _t1 = time.time()
                ta = await self.page.query_selector('textarea[placeholder*="输入消息"]')
                if not ta:
                    log("  send fail: 找不到 textarea, URL=" + self.page.url[:60])
                    self._record_fail()
                    continue
                if not await ta.is_visible():
                    log("  send fail: textarea 不可见")
                    self._record_fail()
                    continue
                await ta.click(timeout=5000)
                # 等 React 受控组件响应 click（focus + 事件绑定），避免 insert_text 空输入
                await asyncio.sleep(0.3)
                # 逐行输入：行间用 Shift+Enter 换行（不发送），最后 Enter 发送。
                # insert_text 一次性插入（已实测 React 正确接收，比逐字符 type 快 ~500 倍）。
                lines = [ln for ln in text.split("\n")]
                if len(lines) > 10:
                    lines = lines[:10]
                for i, line in enumerate(lines):
                    await self.page.keyboard.insert_text(line)
                    if i < len(lines) - 1:
                        await self.page.keyboard.press("Shift+Enter")
                await self.page.keyboard.press("Enter")
                await asyncio.sleep(0.3)
                _t2 = time.time()
                # 读回验证（防假成功）：确认消息真的投递到群里
                if await self._verify_sent(cid, text):
                    self._fail_streak = 0
                    self._consecutive_send_fail = 0
                    self._send_alerted = False
                    _tot = time.time() - _t0
                    if _tot >= 3.0:  # 慢发送留痕，便于定位卡顿
                        log("  ⏱️ send 耗时 " + str(round(_tot, 1)) + "s"
                            + "（切页 " + str(round(_t1 - _t0, 1))
                            + "s / 输入 " + str(round(_t2 - _t1, 1))
                            + "s / 读回 " + str(round(_tot - (_t2 - _t0), 1)) + "s）")
                    return True
                log("  ⚠️ 假成功检测：消息未投递到群里，重试")
                self._record_fail()
                continue
            except Exception as e:
                log("  pw: " + str(e)[:60])
                self._record_fail()
                continue
        # Playwright 两次都失败，降级用 DZMMSocket（HTTP 快速发送）兜底
        if allow_fallback:
            ok = await self._fallback_send(text, cid)
        else:
            ok = False  # 纯 Playwright，不用 Socket.IO 兜底（避免假成功）
        # 浏览器连续失败达阈值，自动重启自愈（防止页面卡死/进程崩溃长期无响应）
        if self._fail_streak >= 3 and not _captcha_active:
            await self._restart()
        # 发送失败计数 + 持续失败告警（自愈/兜底都救不回才报，避免偶发抖动刷屏）
        if ok:
            self._consecutive_send_fail = 0
            self._send_alerted = False
        else:
            self._consecutive_send_fail += 1
            if self._consecutive_send_fail >= 3 and not self._send_alerted:
                self._send_alerted = True
                if _feat_on("", "alert_send_fail"):
                    try:
                        await asyncio.to_thread(send_weixin,
                            "🚨【小小糯bot】发送持续失败（连续 3 条，自愈无效），请检查代理/cookie")
                    except Exception:
                        pass
        return ok

    async def _verify_sent(self, cid: str, text: str) -> bool:
        """读回验证：确认消息真的投递到群里（防 Playwright 假成功）。
        返回 True=已投递（或读回失败保守放行）；False=确认未投递。
        注意：这里的等待不能压太短——太短会读不到刚发的消息 → 误判假成功 → 重发 → 群内重复。
        （2026-09-14 曾试过压到 0.7s，风险大于收益，退回 1.2s）"""
        await asyncio.sleep(1.2)
        try:
            msgs = await asyncio.to_thread(get_messages, cid, 5)
            probe = (text.split("\n")[0] or text)[:20].strip()
            for m in msgs:
                if not is_self(m.get("sent_by")):
                    continue
                mt = (m.get("content") or {}).get("text", "")
                if probe and probe in mt:
                    return True
            return False  # 最近 5 条里没有 bot 刚发的 → 假成功
        except Exception:
            return True  # 读回失败（网络抖动）保守放行，避免误判重发

    async def _fallback_send(self, text: str, cid: str) -> bool:
        """Playwright 发送失败后的兜底：DZMMSocket HTTP 快速发送（静默降级）。"""
        try:
            import importlib.util
            # 用独立模块名加载项目自己的 socketio.py，避免污染 sys.modules['socketio']
            # （sio_subscribe import 后会从 sys.path 移除项目目录，且 socketio 已被 python-socketio 占用）
            spec = importlib.util.spec_from_file_location("dzmm_socketio", str(ROOT / "socketio.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d = mod.DZMMSocket(ORIGIN)
            ok = await asyncio.to_thread(d.send_message, cid, NUO_UID, text, get_cached_cookie())
            log("  fallback(DZMMSocket): " + ("✅" if ok else "❌") + " cid=" + cid[:8])
            return ok
        except Exception as e:
            log("  fallback err: " + str(e)[:60])
            return False

    async def _restart(self):
        """浏览器连续失败，自动重启自愈（close 后重新 start）。"""
        log("  ⚠️ 浏览器连续失败 " + str(self._fail_streak) + " 次，自动重启自愈...")
        try:
            await self.close()
        except Exception:
            pass
        self._fail_streak = 0
        try:
            await self.start()
            log("  ✅ 浏览器自动重启完成")
        except Exception as e:
            log("  ❌ 浏览器自动重启失败: " + str(e)[:80])

    async def close(self):
        try:
            if self.ctx:
                await self.ctx.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self.ctx = None
        self.page = None
        self.pages = {}
        self.current_cid = None
        self._pw = None


def should_reply(context: list[dict]) -> str:
    last_three = [m["text"] for m in context[-3:]]
    for t in last_three:
        t = t.strip()
        if t.startswith("/"):
            return t
    return ""


def _strip_at(text: str, cid: str = None) -> str:
    """去掉 @我 的前缀，返回纯提问（`cid` 决定本群我可能被叫的名字）.

    ⚠️ 必须**长名优先**（`self_names_longest_first`）：名字之间可能互为前缀，
    短名先剥会把长名的残余留进 prompt（`@小小糯-bot` → `-bot`）。
    见 runtime/identity.py 里那个函数的说明。
    """
    for name in self_names_longest_first(cid):
        if name:
            text = text.replace("@" + name, "", 1)
    return text.strip()


def get_group_name() -> str:
    """查询群名（5分钟缓存，按群隔离）."""
    cid = _ACTIVE_CID
    cache = _group_name_cache.get(cid, {"name": "", "ts": 0.0})
    if time.time() - cache["ts"] < 300 and cache["name"]:
        return cache["name"]
    cache["name"] = get_chatroom_name(cid)
    cache["ts"] = time.time()
    _group_name_cache[cid] = cache
    return cache["name"]


def is_summary_cmd(cmd: str) -> bool:
    """判断是否是要总结群聊记录的指令."""
    keywords = ["总结", "回顾", "昨天", "今天聊", "最近聊", "记录",
                "聊了什么", "情况", "复盘", "发生", "讨论", "整理"]
    return any(k in cmd for k in keywords)


HELP_TEXT = """{me}的指令说明～：
@{me_full} - 直接@我聊天
/骚度排行 - 发情值排行
/发骚 /体位骰 - 涩涩骰子
/羞辱 昵称 - 恶魔羞辱 /吐槽开/关 - 主动吐槽
/继续 - 续写  /算命 /占卜 - 运势
/注册 - 建档案  /查 昵称 - 查档案
/公演开始 /公演结束 - 公演+小说
/剧情开始 /投票 - 游戏&投票(/选 N)
🔥 发情值满100触发高潮"""

# 认证群欢迎语（含"妹子认证群"字样）—— 别的人群不用它，见 WELCOME_TEXT_PLAIN
WELCOME_TEXT = """🎉 欢迎 {name} 姐姐加入～♡ 我是{me_full}，群里的小秘书～

💬 新人先冒个泡说句话，让姐姐们认识认识你呀～还没注册的话发 /注册 建个档案哦～📝

⚠️ 本群是妹子认证群，不是妹子的小哥哥会被踢出去哦～🚫（没认证的可以私聊糯糯姐发照片认证，哈哈开玩笑的～）

【群简介】姐妹们发骚聊天的地方～可以 🎲发骚、🔗玩调教、😈被羞辱、🎭开公演，还有 🎬剧情游戏、📊投票、🔮算命、📇查档案、🔥发情值排行～发 /说明 看全部功能～♡"""

# 非认证群的欢迎语（中性版）：不写"会被踢""认证"等字样，只介绍功能
WELCOME_TEXT_PLAIN = """🎉 欢迎 {name} 加入～♡ 我是{me_full}，群里的小秘书～

💬 冒个泡说句话，让大家认识认识你呀～还没注册的话发 /注册 建个档案哦～📝

【群简介】{me}会 🔮算命、📇查档案、📝写诗、🎬剧情游戏、📊投票、🎲骰子、🔥发言排行，也能陪你聊天～发 /说明 看全部功能～♡"""

# 新成员筛查（妹子认证群）：踢游客 / 踢男性 / 提醒未填性别
TOURIST_KEYWORDS = ["游客"]
JOIN_WARN_TOURIST = "⚠️ @{name} 你是游客账号，本群是妹子认证群，稍后会将你移出哦～"
JOIN_DONE_TOURIST = "🚫 已移出游客账号「{name}」～（请用正式账号加入）"
JOIN_WARN_MALE = "⚠️ @{name} 检测到你的性别是男，本群是妹子认证群，稍后会将你移出哦～"
JOIN_DONE_MALE = "🚫 已移出「{name}」（原因：男性），本群是妹子认证群～"
JOIN_WARN_NULL = "⚠️ 新成员「{name}」加入但未填性别资料，请管理员留意认证～ @{name} 也请尽快完善资料哦～"
JOIN_WARN_NOT_VERIFIED = "⚠️ 新成员「{name}」加入但未通过妹子认证，请管理员留意～ @{name} 也请尽快完成认证哦～"

# 运势磕头道歉文案（2026-09-05 赔罪日追加在运势分析后）

# 记忆功能：触发词 + 上限

INTRO_TEXT = """我是{me_full}19岁，糯糯姐的私人秘书，银白双马尾配红蓝异色瞳，软软糯糯的小奶猫一只～♡
我会查档案、排骚度、羞辱人、总结群聊，还能陪姐姐们聊天～
还没注册的姐姐，发 /注册 建立档案，以后查你会更丰富哦～发 /说明 看详细功能～"""

def is_help_cmd(cmd: str) -> bool:
    """识别帮助指令."""
    return any(cmd.startswith(p) for p in ["/帮助", "/help", "/指令", "/菜单", "/命令"])


def is_devil_cmd(cmd: str):
    """识别主动吐槽开关指令，返回 'on'/'off'/None."""
    c = cmd.strip()
    if c.startswith("/吐槽开"):
        return "on"
    if c.startswith("/吐槽关"):
        return "off"
    return None


def is_intro_cmd(cmd: str) -> bool:
    """识别自我介绍指令."""
    return any(cmd.startswith(p) for p in ["/自我介绍", "/介绍", "/你是谁", "/whoami"])


def is_guide_cmd(cmd: str) -> bool:
    """识别使用说明指令."""
    return any(cmd.startswith(p) for p in ["/说明", "/使用说明", "/功能", "/指南"])


def is_sync_cmd(cmd: str) -> bool:
    """识别同步指令."""
    return cmd.startswith("/同步") or cmd.startswith("/sync")


def is_register_cmd(cmd: str) -> str:
    """识别注册指令，返回目标昵称（空串=注册自己，None=不是）."""
    for prefix in ["/注册", "/register"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None


def _extract_xp(profile_text: str) -> list:
    """提炼并解析 XP 标签（返回字符串列表）."""
    try:
        raw = (generate_xp(profile_text) or "").strip()
        if not raw:
            return []
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            arr = json.loads(raw[start:end + 1])
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        pass
    return []


def register_user(query: str, sender_uid: str) -> str:
    """注册用户，返回结果文本."""
    msgs = get_messages_all(_ACTIVE_CID)  # 拉一次，找用户+发言都复用
    if query:
        # 代注册：只允许糯糯（群主）
        if sender_uid != OWNER_UID:
            return "只有糯糯姐才能帮别人注册哦～你自己发 /注册 就可以啦～"
        uid, full = _find_user_in_msgs(query, msgs)
        if not uid:
            return "没找到这个人呢，注册不了～"
    else:
        uid = sender_uid
        full = get_full_name(uid)

    # 已注册则幂等：不重复建档，直接提示
    existing = get_profile(uid)
    if existing:
        return "✅ " + full + " 已经注册过啦～不用重复注册哦，发 /查 " + full + " 就能看档案啦～"

    u = get_user(_ACTIVE_CID, uid)
    profile_text = _member_profile_text(uid, full, msgs)
    summary = generate_profile_summary(profile_text)
    xp = _extract_xp(profile_text)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profile = {
        "name": full,
        "gender": str(u.get("gender")) if u and u.get("gender") else "",
        "signature": (u.get("quirk") or "") if u else "",
        "bio": (u.get("bio") or "") if u else "",
        "registered_at": now,
        "register_summary": summary,
        "xp": xp,
        "last_seen": now,
        "query_count": 0,
    }
    if upsert_profile(uid, profile):
        return "✅ " + full + " 注册成功啦～已经记进档案，以后查" + full + "会更丰富哦～"
    return "注册失败，存档出了点问题～"


def is_profile_cmd(cmd: str) -> str:
    """识别查人指令，返回目标昵称（空串表示不是）."""
    for prefix in ["/查", "/whois", "/资料"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return ""


def is_rank_cmd(cmd: str) -> bool:
    """识别骚度排行指令."""
    keywords = ["骚度", "排行", "骚榜", "骚排名", "骚气", "谁最骚", "骚王"]
    return any(k in cmd for k in keywords)


def rank_summary() -> str:
    """生成骚度排行（高潮次数为主，发情值为辅，活跃度不参与排名，排除小小糯）."""
    cid = _ACTIVE_CID
    group_state = (load_state() or {}).get(cid) or {}
    heat = group_state.get("heat_value") or {}
    counts = group_state.get("msg_count") or {}
    climax = group_state.get("heat_climax") or {}
    today_climax = group_state.get("heat_climax_today") or {}
    uids = set(heat) | set(counts) | set(climax)
    uids.difference_update(self_uids())
    if not uids:
        return ""
    _load_member_names()
    items = sorted(uids, key=lambda u: (-today_climax.get(u, 0), -climax.get(u, 0), -heat.get(u, 0), -counts.get(u, 0)))
    lines = ["🔥 骚度排行"]
    for i, uid in enumerate(items, 1):
        name = get_name(uid)  # 简化名字（去掉括号/波浪号后缀）
        h = heat.get(uid, 0)
        c = counts.get(uid, 0)
        cl = climax.get(uid, 0)
        ct = today_climax.get(uid, 0)
        line = name + " · 高潮今" + str(ct) + "/总" + str(cl) + " · 发情" + str(h) + " · 活跃" + str(c)
        if i == 1:
            lines.append("🥇 " + line)
        elif i == 2:
            lines.append("🥈 " + line)
        elif i == 3:
            lines.append("🥉 " + line)
        else:
            lines.append(str(i) + ". " + line)
    return "\n".join(lines)


# ============ 发情值增量（P8 集中管理）============
# 原先 5/8/10/15/20 散落在十余个函数里，改规则要到处找。括号内是原行号，便于对照。


def _rollback_last_id(cid: str, old_last_id: str) -> None:
    """生成失败（网络超时/空回复）时回滚 last_id，让这条消息下轮重试."""
    state = load_state()
    group_state = state.get(cid) or {}
    group_state["last_id"] = old_last_id
    state[cid] = group_state
    save_state(state)


def _climax_hits(group_state: dict, cid: str = None):
    """检测发情值≥阈值，返回 (hits, changed)。changed=有跨天重置或高潮（需写回磁盘）.

    `cid` 用来取**按群**的参数（阈值/扣减可调）；不传就用当前处理中的群
    （调用点在主循环里，`_ACTIVE_CID` 一定已设置）。
    """
    cid = cid or _ACTIVE_CID
    heat = group_state.get("heat_value") or {}
    climax = group_state.get("heat_climax") or {}
    today_climax = group_state.get("heat_climax_today") or {}
    today = datetime.now().strftime("%Y-%m-%d")
    changed = False
    # 跨天重置今日高潮次数
    if group_state.get("climax_date") != today:
        today_climax = {}
        group_state["climax_date"] = today
        changed = True
    hits = []
    for uid, h in list(heat.items()):
        if h >= _tune("heat.climax_threshold", cid):
            hits.append((uid, h))
            heat[uid] = h - _tune("heat.climax_reset", cid)
            climax[uid] = climax.get(uid, 0) + 1
            today_climax[uid] = today_climax.get(uid, 0) + 1
            changed = True
    if changed:
        group_state["heat_value"] = heat
        group_state["heat_climax"] = climax
        group_state["heat_climax_today"] = today_climax
    return hits, changed


def _parse_judge(result: str) -> dict:
    """解析 AI 评定结果 JSON，返回 {name: delta}."""
    import json as _json
    import re as _re
    result = (result or "").strip()
    result = _re.sub(r"```(?:json)?", "", result).strip()
    data = None
    try:
        data = _json.loads(result)
    except Exception:
        m = _re.search(r"\[.*\]", result, _re.DOTALL)
        if m:
            try:
                data = _json.loads(m.group(0))
            except Exception:
                data = None
    out = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                try:
                    delta = int(item.get("delta", 0))
                except Exception:
                    delta = 0
                if name and delta > 0:
                    out[name] = delta
    return out


def _judge_pending(cid: str) -> str:
    """批量评定待评发言的发情值（每2小时调用），返回评定结果文本."""
    state = load_state()
    group_state = state.get(cid) or {}
    pending = group_state.get("heat_pending") or {}
    if not pending:
        return ""
    _load_member_names()
    lines = []
    for uid, texts in pending.items():
        name = get_full_name(uid)
        lines.append(name + ": " + " | ".join(texts))
    chat_text = "\n".join(lines)
    try:
        result = generate_heat_judge(chat_text)
    except Exception:
        result = ""
    result = (result or "").strip()
    delta = _parse_judge(result)
    heat = group_state.get("heat_value") or {}
    for name, d in delta.items():
        uid = _find_uid_by_name(name)
        if uid and not is_self(uid):
            heat[uid] = heat.get(uid, 0) + d
    group_state["heat_value"] = heat
    group_state["heat_pending"] = {}
    group_state["heat_last_judge"] = time.time()
    state[cid] = group_state
    save_state(state)
    return result


def _build_climax_context(cid: str, max_count: int = 60) -> str:
    """拉最近群聊内容，组装成「昵称: 内容」上下文，供高潮生成判断场景/施动者。"""
    _load_member_names()
    try:
        msgs = get_messages_all(cid, max_count=max_count)
    except Exception:
        return ""
    lines = []
    for m in msgs[-max_count:]:
        uid = m.get("sent_by", "")
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text or text.startswith("/"):
            continue
        name = get_full_name(uid) if uid else ""
        if not name or name == uid[:8]:
            continue  # 拿不到昵称的跳过，避免 uid 干扰
        lines.append(name + ": " + text[:60])
    return "\n".join(lines)


async def _handle_climax(sender, cid: str, dry: bool, hits: list):
    """处理高潮事件：为每个发情值≥100的成员生成专属色色文字并 @当事人（标注今日/总高潮次数）."""
    gs = (load_state() or {}).get(cid) or {}
    total_map = gs.get("heat_climax") or {}
    today_map = gs.get("heat_climax_today") or {}
    context = _build_climax_context(cid)  # 拉一次上下文，循环复用
    for uid, _h in hits:
        name = get_full_name(uid)
        total = total_map.get(uid, 0)
        today = today_map.get(uid, 0)
        profile = get_profile(uid)
        user_text = _archive_text_from_profile(profile) if profile else "昵称：" + name
        if context:
            user_text += "\n\n【最近群聊上下文】\n" + context
        try:
            text = generate_heat_climax(user_text)
        except Exception:
            text = ""
        text = (text or "").strip().replace("🤖", "")
        tag = "🔥 发情值爆表！今天第" + str(today) + "次高潮，总共第" + str(total) + "次～"
        if text:
            msg = "@" + name + " " + tag + "\n\n" + text
        else:
            msg = "@" + name + " " + tag
        if dry:
            log("[dry] " + msg[:60])
        else:
            ok = await sender.send("🤖 " + msg, cid)
            log("✅" if ok else "❌")
        await asyncio.sleep(1.0)


async def _publish_rank(sender, cid: str, dry: bool):
    """发布骚度排行."""
    rank = rank_summary()
    if not rank:
        return
    if dry:
        log("[dry] " + rank[:60])
    else:
        ok = await sender.send("🤖 " + rank, cid)
        log("✅" if ok else "❌")


async def _publish_memory_report(sender, cid: str, dry: bool):
    """每日记忆汇报：列出具体记忆内容（原文列表）。"""
    mem = _get_memory(cid)
    if not mem:
        return
    total = len(mem)
    shown = mem[-_tune("memory.report_items", cid):]
    lines = [p("mem_list_head", cid).format(n=total)]
    for i, m in enumerate(shown, 1):
        at_short = (m.get('at') or '')
        if len(at_short) >= 11:
            at_short = at_short[5:]
        lines.append(f"{i}. {m['text']}（{m.get('from', '?')}，{at_short}）")
    if total > len(shown):
        lines.append(p("mem_list_more", cid).format(k=len(shown)))
    text = "\n".join(lines)
    if dry:
        log("[dry] 记忆汇报")
    else:
        ok = await sender.send("🤖 " + text, cid)
        log("✅" if ok else "❌")


DICE_TIMEOUT_SEC = 180  # 3 分钟超时弃权（代码默认值；运行期读 tuning）


def _rank_hour_set():
    """定时排行发布的整点（默认 12、18；`schedule.rank_hours` 可配，逗号分隔）.

    配错了（空/非法）就退回默认的 12、18 —— 定时任务不该因为一个笔误彻底不发。
    """
    raw = str(_tune("schedule.rank_hours") or "")
    out = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            out.add(int(part))
    return out or {12, 18}


def profile_summary(query: str) -> str:
    """查资料+发言，返回给 AI 的文本."""
    uid, full = find_user_by_name(query)
    if not uid:
        return ""
    return _member_profile_text(uid, full)


# ---- 中文昵称归一化（繁→简 + 异体字→标准字 + 去空白）----
# 用于昵称模糊匹配，解决"寜/寧"异体字、"繁简混用"、空格差异导致的匹配失败


# opencc t2s 不覆盖的生僻异体字 → 简体标准字


def _live_insult_text(uid: str, profile: dict) -> str:
    """羞辱素材：档案背景（只作参考）+ 实时最新发言（羞辱点主要从这里找）。

    区别于 _archive_text_from_profile：不用 profile["messages"] 的滞后同步快照，
    而是实时 get_messages_all 拉最新发言，保证羞辱点跟着当下互动走、不重复。
    """
    lines = ["【档案背景（只作参考，别死扣简介/XP）】", "昵称：" + profile.get("name", "")]
    if profile.get("gender"):
        lines.append("性别：" + profile["gender"])
    if profile.get("signature"):
        lines.append("签名：" + profile["signature"])
    if profile.get("xp"):
        lines.append("XP性癖：" + "、".join(profile["xp"]))
    hl = _history_lines(profile, n=2, bio_n=50)
    if hl:
        lines.append("【以前的样子（前后不一就是最好的反差素材）】")
        lines.extend(hl)
    try:
        their = _get_msgs_by_uid(uid)  # 实时拉（API）
        if their:
            lines.append("【实时最新发言（羞辱点主要从这里找，以子之矛攻子之盾）】")
            for t in their[-12:]:
                lines.append("  - " + t[:60])
    except Exception:
        pass
    return "\n".join(lines)


def _get_insult_history(uid: str) -> list:
    """读某人的羞辱历史（角度避让用），profile 全局共享。"""
    p = get_profile(uid)
    return (p or {}).get("insult_history") or []


def _record_insult(uid: str, style: str, text: str) -> None:
    """记录一次羞辱（风格 + 内容），供下次角度避让。保留最近 3 条。"""
    try:
        p = get_profile(uid)
        if not p:
            return
        hist = (p.get("insult_history") or [])[-2:]
        hist.append({"style": style, "text": (text or "")[:200], "at": time.strftime("%m-%d %H:%M")})
        p["insult_history"] = hist
        upsert_profile(uid, p)
    except Exception:
        pass


# 指定诗词形式的关键词 → POEM_STYLES 匹配（长词优先）


PROFILE_SCAN_FILE = ROOT / "profile_scan.json"  # 全群档案扫描基线（跨重启保留）
PROFILE_SCAN_HOUR = 6      # 每天 06:00 后首次循环执行一次
PROFILE_SCAN_MAX_API = 150  # 单次扫描 get_user 上限（防限流）
PROFILE_NOTICE_MAX = 10     # 单次扫描最多发几条群通知（防刷屏）


def _scan_load() -> dict:
    try:
        d = json.loads(PROFILE_SCAN_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _scan_save(d: dict) -> None:
    try:
        PROFILE_SCAN_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _daily_profile_scan() -> list:
    """每天一次：全群扫昵称 + 已建档成员的简介/签名/性别。
    返回 [(cid, 通知文本)]；首次运行（无基线）只建基线，不发通知。"""
    snap = _scan_load()
    prev_all = snap.get("members") or {}
    first_run = not prev_all
    cur = {}
    notices = []
    seen = set()
    api_used = 0
    for cid in GROUPS:
        if cid in MUTE_CIDS:  # 静默监控群：不扫档案、不播报
            continue
        if not _feat_on(cid, "profile_scan"):   # P12-A 闸门：这个群今天不扫
            continue
        try:
            members = get_members(cid)
        except Exception:
            continue
        for uid, name in members.items():
            if not name or uid in seen:
                continue
            seen.add(uid)
            prev = prev_all.get(uid) or {}
            info = {"name": name}
            prof = get_profile(uid)
            if prof:
                old = {"name": str(prof.get("name") or ""),
                       "sig": str(prof.get("signature") or ""),
                       "bio": str(prof.get("bio") or "")}
                if api_used < _tune("profile.scan_max_api", cid):
                    api_used += 1
                    u = get_user(cid, uid)
                    if u:
                        info["bio"] = str(u.get("bio") or "")
                        info["sig"] = str(u.get("quirk") or "")
                        info["gender"] = str(u.get("gender") or "")
                # 姓名/签名/简介一起比：有变化 → 记轨迹 + 同步档案（首日静默，不播报历史变化）
                newv = {"name": name,
                        "sig": info.get("sig", old["sig"]),
                        "bio": info.get("bio", old["bio"])}
                if newv != old:
                    _record_history(prof, old, time.strftime("%m-%d %H:%M"))
                    prof["name"] = newv["name"]
                    prof["signature"] = newv["sig"]
                    prof["bio"] = newv["bio"]
                    if info.get("gender"):
                        prof["gender"] = info["gender"]
                    upsert_profile(uid, prof)
            cur[uid] = info
            _name_cache[uid] = name
            if first_run or not prev:
                continue
            old_name = str(prev.get("name") or "")
            rename = bool(old_name) and old_name != name
            bits = []
            if info.get("bio") is not None and str(prev.get("bio") or "") != info["bio"]:
                bits.append("简介换啦：" + (_squash(info["bio"], 36) or "（清空）"))
            if info.get("sig") is not None and str(prev.get("sig") or "") != info["sig"]:
                bits.append("签名换啦：" + (_squash(info["sig"], 24) or "（清空）"))
            g_old = str(prev.get("gender") or "")
            if info.get("gender") and g_old and g_old != info["gender"]:
                bits.append("性别改成「" + info["gender"] + "」咯")
            if rename:
                _on_rename(uid, old_name, name)
                if cid in ROOM_CIDS:
                    continue  # 房间群照常扫档案，但不播报（客人不该看到后台改名播报）
                txt = "🤖 咦～" + old_name + " 改名成「" + name + "」啦"
                if bits:
                    txt += "，" + "，".join(bits)
                txt += ("，" + _personas.me(cid) + "记住咯～以后就叫你 "
                        + (_clean_name(name) or name) + " 姐姐啦～♡")
                notices.append((cid, txt))
            elif bits:
                if cid in ROOM_CIDS:
                    continue  # 同上：房间群不播报简介/签名变化
                notices.append((cid, "🤖 咦～" + name + " 姐姐" + "，".join(bits)
                                + "，" + _personas.me(cid) + "偷偷更新档案咯～♡"))
    snap["day"] = time.strftime("%Y-%m-%d")
    snap["members"] = cur
    _scan_save(snap)
    log("📇 档案扫描完成：成员 " + str(len(cur)) + "，查简介 " + str(api_used)
        + "，变化 " + str(len(notices))
        + ("（首次运行，只建基线）" if first_run else ""))
    return notices[:max(1, int(_tune("profile.notice_max", PRIMARY_CID)))]


def archive_query(query: str):
    """查询某人，返回 (文本, 是否已注册). 已注册读档案（零 API），未注册实时查."""
    uid, profile = _find_in_profiles(query)
    if uid:
        _refresh_profile_latest(uid, profile)
        return _archive_text_from_profile(profile), True
    # 未注册：实时查（fallback）
    msgs = get_messages_all(_ACTIVE_CID)
    uid2, full = _find_user_in_msgs(query, msgs)
    if not uid2:
        return "", False
    return _member_profile_text(uid2, full, msgs), False


def _insult_resolve(query: str, sender_uid: str):
    """解析羞辱目标 + 素材 + 自定义方向，返回 (uid, 素材文本, 自定义方向, 是否找到)。query 为空时随机抽人羞辱."""
    if not query:
        # 不带昵称：随机抽一个成员（排除小小糯 + 发指令的人自己）
        uid = _random_member_uid(exclude={sender_uid})
        if not uid:
            return None, "", "", False
        profile = get_profile(uid)
        if profile:
            _refresh_profile_latest(uid, profile)
            return uid, _live_insult_text(uid, profile), "", True
        return uid, _member_profile_text(uid, get_full_name(uid)), "", True
    # 先精确匹配完整 query（快速路径）
    uid, profile = _find_in_profiles(query)
    if uid:
        _refresh_profile_latest(uid, profile)
        return uid, _live_insult_text(uid, profile), "", True
    msgs = get_messages_all(_ACTIVE_CID)
    uid2, full = _find_user_in_msgs(query, msgs)
    if uid2:
        return uid2, _member_profile_text(uid2, full, msgs), "", True
    # 完整匹配失败 → 最长前缀匹配拆「昵称 + 自定义方向」
    tokens = query.split()
    if len(tokens) > 1:
        for i in range(len(tokens) - 1, 0, -1):
            name_part = " ".join(tokens[:i])
            uid3, profile3 = _find_in_profiles(name_part)
            if uid3:
                _refresh_profile_latest(uid3, profile3)
                return uid3, _live_insult_text(uid3, profile3), " ".join(tokens[i:]), True
        for i in range(len(tokens) - 1, 0, -1):
            name_part = " ".join(tokens[:i])
            uid4, full4 = _find_user_in_msgs(name_part, msgs)
            if uid4:
                return uid4, _member_profile_text(uid4, full4, msgs), " ".join(tokens[i:]), True
    return None, "", "", False


def _insult_material(query: str, sender_uid: str):
    """获取羞辱素材文本，返回 (文本, 是否找到). 优先档案（零 API）."""
    if query:
        uid, profile = _find_in_profiles(query)
        if uid:
            return _live_insult_text(uid, profile), True
        msgs = get_messages_all(_ACTIVE_CID)
        uid2, full = _find_user_in_msgs(query, msgs)
        if not uid2:
            return "", False
        return _member_profile_text(uid2, full, msgs), True
    # 羞辱发指令的人自己
    profiles = load_profiles()
    profile = profiles.get(sender_uid)
    if profile:
        return _live_insult_text(sender_uid, profile), True
    return _member_profile_text(sender_uid, get_full_name(sender_uid)), True


def _insult_target_uid(query: str):
    """解析羞辱目标的 uid。返回 None 表示羞辱自己（不带昵称）。"""
    if not query:
        return None  # 羞辱发指令的人自己
    uid, _ = _find_in_profiles(query)
    if uid:
        return uid
    msgs = get_messages_all(_ACTIVE_CID)
    uid2, _ = _find_user_in_msgs(query, msgs)
    return uid2


def defend_nuo(attacker_uid: str) -> str:
    """维护糯糯权威，痛斥发指令者."""
    full = get_full_name(attacker_uid)
    profile = get_profile(attacker_uid)
    attacker_text = _archive_text_from_profile(profile) if profile else "昵称：" + full
    return generate_defend(attacker_text)


def is_insult_cmd(cmd: str) -> str:
    """识别羞辱指令，返回目标昵称（空串表示羞辱发指令的人自己）."""
    for prefix in ["/羞辱", "/骂", "/diss", "/损", "/吐槽"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None  # None 表示不是羞辱指令


# ---------- 运势：五行命理算分（确定性，非随机）----------

# 五行 → 命基底分（天生禀赋）
# 五行 → 幸运色/数字/方位
# 五行 → 当日宜/忌
# 字 → 五行（常见字，按偏旁字义；查不到用编码兜底）


# 本命地支 × 当日地支：六冲 = 明确的凶日信号，六合 = 顺遂（让「凶」真的算得出来）


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


def _maybe_devil_cold_roast(cid: str) -> str:
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
        save_state(state)
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
        save_state(state)
        return ""
    # 更新 + save
    group["devil_cold_last"] = now
    group["devil_daily_count"] = group.get("devil_daily_count", 0) + 1
    state[cid] = group
    save_state(state)
    return roast


def is_continue_cmd(cmd: str) -> bool:
    """识别续写指令（继续/续写/接着/再来 等同义词都触发）."""
    c = cmd.strip().lstrip("/").strip()
    if not c:
        return False
    for kw in ("继续", "续写", "接着", "再来", "再写", "往下写"):
        if c.startswith(kw):
            return True
    return False


def _extract_continue_custom(cmd: str) -> str:
    """从续写指令里提取自定义方向（继续/续写 后面的内容）."""
    c = cmd.strip().lstrip("/").strip()
    for kw in ("继续羞辱", "继续发骚", "继续写", "接着写", "往下写", "继续", "续写", "接着", "再来", "再写"):
        if c.startswith(kw):
            return c[len(kw):].strip()
    return ""


def continue_last(cid: str, cmd: str = "", sender_uid: str = "") -> str:
    """续写上一段羞辱/发骚（可带自定义方向）. 发骚续写走 fasao 状态（多段上下文），羞辱续写走 last_action."""
    custom = _extract_continue_custom(cmd)
    # 发骚续写优先（发骚 active）
    fs = _get_fasao_state(cid)
    if fs.get("active"):
        history = fs.get("history") or []
        if not history:
            _set_fasao_state(cid, {"active": False})
            return "还没有发骚内容可以继续哦～先 /发骚 试试～"
        try:
            # combo 去括号再传给续写 prompt，避免签名里的「裳姐姐」串角色
            combo_clean = _strip_parenthesis(fs.get("combo", ""))
            text = generate_scene_continue(combo_clean, history, custom)
        except Exception:
            text = ""
        if not (text or "").strip():
            # 续写失败（超时/空），重试一次
            try:
                combo_clean = _strip_parenthesis(fs.get("combo", ""))
                text = generate_scene_continue(combo_clean, history, custom)
            except Exception:
                text = ""
        text = (text or "").strip()
        if not text:
            return "续写失败啦，换个指令试试～"
        target_uid = fs.get("uid")
        if target_uid:
            _add_heat(cid, target_uid, _tune("heat.scene_continue_target", cid))
        if sender_uid and sender_uid != target_uid:
            _add_heat(cid, sender_uid, _tune("heat.scene_continue_sender", cid))
        history.append(text)
        fs["history"] = history
        fs["last_ts"] = time.time()
        _set_fasao_state(cid, fs)
        # 同步 last_action（供兼容）
        _set_last_action(cid, {"type": "scene", "uid": target_uid, "full": fs.get("full", ""), "combo": fs.get("combo", ""), "output": text})
        return text
    # 羞辱续写
    action = _get_last_action(cid)
    if not action:
        return "还没有上一段可以继续哦～先 /发骚 或 /羞辱 某人试试～"
    atype = action.get("type")
    try:
        if atype == "insult":
            text = generate_insult_continue(action.get("material", ""), action.get("output", ""), custom)
        else:
            return "上一段不是可以继续的内容哦～"
    except Exception:
        return "续写失败啦，换个指令试试～"
    text = (text or "").strip()
    if not text:
        return "续写失败啦，换个指令试试～"
    # 续写加分（和目标原指令一致：羞辱 +15）
    target_uid = action.get("uid")
    if target_uid:
        _add_heat(cid, target_uid, _tune("heat.insult_target", cid))
    if sender_uid and sender_uid != target_uid:
        _add_heat(cid, sender_uid, _tune("heat.insult_sender", cid))
    action["output"] = text
    _set_last_action(cid, action)
    return text


# ===== 发骚状态机（多轮互动 + 屏蔽 + 上下文） =====


def is_rewrite_cmd(cmd: str) -> str:
    """识别重新生成指令：/重写 /重新生成 /重来 /重新写。返回调整方向（空串=无方向），None=不是."""
    c = cmd.strip().lstrip("/").strip()
    if not c:
        return None
    for kw in ("重新生成", "重新写", "重写", "重来"):
        if c == kw:
            return ""
        if c.startswith(kw):
            return c[len(kw):].strip()
    return None


def rewrite_last(cid: str, cmd: str = "") -> str:
    """重新生成发骚的上一段（可带调整方向）."""
    fs = _get_fasao_state(cid)
    if not fs.get("active"):
        return "现在没有进行中的发骚哦～先 /发骚 试试～"
    history = fs.get("history") or []
    if not history:
        _set_fasao_state(cid, {"active": False})
        return "还没有发骚内容可以重写哦～先 /发骚 试试～"
    direction = is_rewrite_cmd(cmd) or ""
    try:
        text = generate_scene_rewrite(_strip_parenthesis(fs.get("combo", "")), history[:-1], history[-1], direction)
    except Exception:
        text = ""
    text = (text or "").strip()
    if not text:
        return "重写失败啦，再试一次～"
    history[-1] = text
    fs["history"] = history
    _set_fasao_state(cid, fs)
    _set_last_action(cid, {"type": "scene", "uid": fs.get("uid"), "full": fs.get("full", ""), "combo": fs.get("combo", ""), "output": text})
    return text


# ===== 调教功能 =====


def is_dj_command_cmd(cmd: str) -> str:
    """识别调教命令：/命令 xxx 或 /xxx（直接斜杠，排除已知系统指令）。返回命令文本或 None."""
    if cmd.startswith("/命令"):
        return cmd[len("/命令"):].strip()
    # /xxx 直接斜杠命令（排除已知系统指令）
    if not cmd.startswith("/") or cmd.startswith("//"):
        return None
    body = cmd.lstrip("/").strip()
    if not body:
        return None
    if (is_help_cmd(cmd) or is_intro_cmd(cmd) or is_guide_cmd(cmd)
            or is_sync_cmd(cmd) or is_register_cmd(cmd) is not None
            or is_profile_cmd(cmd) != "" or is_rank_cmd(cmd)
            or is_fasao_cmd(cmd) or is_insult_cmd(cmd) is not None
            or is_fortune_cmd(cmd) or is_scene_cmd(cmd)
            or is_poem_cmd(cmd) is not None
            or is_continue_cmd(cmd) or is_rewrite_cmd(cmd) is not None or is_perf_start_cmd(cmd)
            or is_perf_end_cmd(cmd) or is_dj_end_cmd(cmd) or is_fasao_end_cmd(cmd)
            or is_diaojiao_cmd(cmd) is not None
            or is_story_start_cmd(cmd) or is_story_end_cmd(cmd)
            or is_vote_choice_cmd(cmd) is not None
            or is_vote_start_cmd(cmd)
            or is_summary_cmd(cmd) or is_devil_cmd(cmd) is not None
            or is_remember_cmd(cmd) is not None or is_recall_cmd(cmd)):
        return None
    return body


# 调教参与提醒（每轮命令后附带，群友可 /命令 xxx 或直接 /xxx 参与）


# ===== 公演功能 =====


def is_review_cmd(cmd: str):
    """审核命令：返回 'approve'/'reject'/'ignore' 或 None."""
    if cmd.startswith("/同意"):
        return "approve"
    if cmd.startswith("/退回"):
        return "reject"
    if cmd.startswith("/忽略"):
        return "ignore"
    return None


# ============ 投票功能（/投票）============

# 恶魔吐槽配置（自主毒舌，对事也对人）
DEVIL_BASE_PROB = 0.03       # 普通闲聊基础概率
DEVIL_SENSITIVE_PROB = 0.10  # 命中敏感词概率（封顶）
DEVIL_HOT_PROB = 0.08        # 多人热闹概率
DEVIL_COOLDOWN = 10 * 60     # 冷却 10 分钟
DEVIL_DAILY_MAX = 30         # 每日上限 30 次
DEVIL_HOT_MIN_SPEAKERS = 3   # 热闹阈值：最近 12 条内 ≥3 人发言
DEVIL_SENSITIVE_WORDS = [
    "骚", "发情", "母狗", "肉便器", "欠操", "高潮", "调教", "骚穴",
    "发浪", "骚水", "下贱", "淫荡", "发骚", "公演",
]
DEVIL_COLD_THRESHOLD = 2 * 60 * 60   # 冷清阈值：2 小时没人说话后首次 roll
DEVIL_COLD_PROB = 0.3            # 冷场吐槽概率：每次 roll 30%（不中也更新周期标记）
DEVIL_COLD_COOLDOWN = 30 * 60    # 冷场重试周期：30 分钟（无论吐没吐都重新计，无需有人说话）
DEVIL_COLD_QUIET_HOUR = 8        # 深夜静默：0-8 点不触发冷场吐槽
DEVIL_HOT_WINDOW = 3 * 60        # 热闹窗口：3 分钟内发言算"同时活跃"


# ============ 剧情游戏（/剧情）============


def _active_play_hint(cid: str) -> str:
    """返回当前独占型玩法进行中的「打断保护」提示，无则返回 None."""
    if _get_perf_state(cid).get("active"):
        return "🎭 公演进行中哦～暂无法响应，想参与就直接发言，等公演结束后再试～"
    if _get_fasao_state(cid).get("active"):
        return "🎲 发骚进行中哦～暂无法响应其他指令，想继续发 /继续，想结束发 /结束～"
    if _get_dj_state(cid).get("active"):
        return "🔗 调教进行中哦～暂无法响应其他指令，想参与发 /命令 xxx，想结束发 /调教结束～"
    if _get_story_state(cid).get("active"):
        return "🎬 剧情进行中哦～暂无法响应其他指令，想投票发 /选 X，想结束发 /剧情结束～"
    return None


def split_novel(text: str, max_lines: int = 10, max_bytes: int = 1000):
    """分段美化：优先在 ## 章节标题处断，每段 ≤10行 且 ≤1000字节."""
    lines = text.split("\n")
    chunks, cur_lines, cur_bytes = [], [], 0
    for line in lines:
        lb = len((line + "\n").encode('utf-8'))
        is_heading = line.strip().startswith("## ")
        if is_heading and cur_lines:
            chunks.append("\n".join(cur_lines))
            cur_lines, cur_bytes = [], 0
        if (len(cur_lines) >= max_lines or cur_bytes + lb > max_bytes) and cur_lines:
            chunks.append("\n".join(cur_lines))
            cur_lines, cur_bytes = [], 0
        cur_lines.append(line)
        cur_bytes += lb
    if cur_lines:
        chunks.append("\n".join(cur_lines))
    return chunks


def parse_time_range(cmd: str):
    """从指令解析时间范围，返回 (start_utc, end_utc) 两个 datetime（UTC）."""
    from datetime import timedelta, timezone
    now_utc = datetime.now(timezone.utc)
    # 北京时间 = UTC + 8
    beijing = timezone(timedelta(hours=8))
    now_bj = now_utc.astimezone(beijing)

    if "昨天" in cmd:
        start_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
    elif "今天" in cmd:
        start_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
        end_bj = now_bj
    else:
        # 默认最近 24 小时
        start_bj = now_bj - timedelta(hours=24)
        end_bj = now_bj

    return start_bj.astimezone(timezone.utc), end_bj.astimezone(timezone.utc)


def summarize_chat(cmd: str) -> str:
    """拉取群聊记录并总结."""
    from datetime import timezone, timedelta
    msgs = get_messages_all(_ACTIVE_CID)
    if not msgs:
        return "群聊记录为空，没有可总结的内容。"

    start_utc, end_utc = parse_time_range(cmd)

    lines = []
    for m in msgs:
        # 过滤 agent 自己的消息，避免自我引用循环
        if is_self(m.get("sent_by")):
            continue
        # 解析时间
        try:
            s = m.get("sent_at", "")
            s = s.split(".")[0]  # 去毫秒
            if s.endswith("+00"):
                s = s[:-3]
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if not (start_utc <= dt <= end_utc):
            continue
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text:
            continue
        name = get_name(m.get("sent_by", ""))
        t = dt.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M")
        lines.append(f"[{t}] {name}: {text}")

    if not lines:
        return "这段时间群里没有聊天记录。"

    # 截断，避免太长
    if len(lines) > 200:
        lines = lines[-200:]
    return "\n".join(lines)


# ===== 公演 handler =====


async def _scheduled_pushes(sender, dry: bool) -> None:
    """定时问候（早/中/晚）+ 记忆汇报：用主 sender 发（页面常驻，秒发）。

    相比原来的 entrypoint「到点另起进程 → 冷启浏览器」：
    - 不再有 30 秒整页加载超时的风险（认证群页面本来就常驻）
    - 带补发窗口：机器人在到点后 90 分钟内恢复，仍然会补发（原来错过就永远没了）
    - 每群独立打点（state[cid]["last_greet_xxx"]），重启/部署不会重复发
    """
    if dry:
        return
    set_persona_cid("")  # 广播文案（问候/记忆汇报）一律用默认人设，别串到房间群管家口吻
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    cur = now.hour * 60 + now.minute
    st = load_state()
    changed = False
    for period, hour in (("morning", 8), ("noon", 12), ("evening", 22)):
        # 窗口：[整点, 整点+90 分钟]；超出窗口就不补了（避免半夜恢复时补发早安）
        if not (hour * 60 <= cur <= hour * 60 + int(_tune("schedule.greet_window_min"))):
            continue
        # P12-A 闸门：三个问候各自可关（按群由 PUSH_CIDS 决定，这里是功能级开关）。
        # 注意 todo 要按**群**过滤（同一个问候在不同群可以有不同开关）。
        key = "last_greet_" + period
        todo = [c for c in PUSH_CIDS
                if (st.get(c) or {}).get(key) != today
                and _feat_on(c, _GREET_FEATURES[period])]
        if not todo:
            continue
        try:
            greeting = (await asyncio.to_thread(generate_greeting, period, today) or "").strip()
        except Exception as e:
            log("问候生成失败(" + period + "): " + str(e)[:60])
            greeting = ""
        if not greeting:
            greeting = "姐姐们好呀～"
        for c in todo:
            ok = await sender.send("🤖 " + greeting, c)
            if ok:
                gs = st.get(c) or {}
                gs[key] = today
                st[c] = gs
                changed = True
            log("greet[" + period + "] " + c[:8] + ": " + ("✅" if ok else "❌"))
        if changed:
            save_state(st)
        if period == "noon":
            # 午间顺带发每日记忆汇报
            for c in PUSH_CIDS:
                if (st.get(c) or {}).get("last_memreport") == today:
                    continue
                if not _feat_on(c, "memory_report"):
                    continue
                try:
                    await _publish_memory_report(sender, c, dry)
                except Exception as e:
                    log("记忆汇报失败: " + str(e)[:60])
                gs = st.get(c) or {}
                gs["last_memreport"] = today
                st[c] = gs
                changed = True
            if changed:
                save_state(st)


async def _handle_review(sender, cid: str, action: str, dry: bool):
    perf = _get_perf_state(cid)
    if action == "ignore":
        perf["review_status"] = "ignored"
        _set_perf_state(cid, perf)
        await _perf_send(sender, "好哦～不提醒啦，小说先存着～", cid, dry)
        return
    if action == "reject":
        start_id = perf.get("start_id", "")
        await _perf_send(sender, "收到退回～重新生成中...", cid, dry)
        content = await asyncio.to_thread(_get_perf_content, cid, start_id)
        m_name = await asyncio.to_thread(_perf_m_name, content)
        novel_content = content
        if m_name:
            novel_content = "【被调教方（M）】：" + m_name + "\n\n" + content
        novel = (await asyncio.to_thread(generate_perf_novel_chunked, novel_content)).strip().replace("🤖", "")
        if not novel:
            await _perf_send(sender, "重写失败了，再试一次 /退回 吧～", cid, dry)
            return
        path = _novel_save_path(cid, novel)
        with open(path, "w", encoding="utf-8") as f:
            f.write(novel)
        perf.update({"novel_path": path, "novel_title": _extract_novel_title(novel),
                     "review_status": "pending", "review_notified_ts": time.time()})
        _set_perf_state(cid, perf)
        await _perf_send(sender, "已重新生成《" + perf["novel_title"] + "》，请糯糯姐再审核～", cid, dry)
        send_weixin("📝【小小糯】公演小说《" + perf["novel_title"] + "》已重新生成，待审核～\n文件：" + path +
                    "\n请在群里回复：/同意 发送｜/退回 重写｜/忽略 不再提醒")
        return
    if action == "approve":
        path = perf.get("novel_path", "")
        try:
            novel = open(path, encoding="utf-8").read()
        except Exception:
            novel = ""
        if not novel:
            await _perf_send(sender, "没找到小说文件，重新 /公演结束 试试吧～", cid, dry)
            return
        perf["review_status"] = "approved"
        _set_perf_state(cid, perf)
        chunks = split_novel(novel)
        await _perf_send(sender, "✅ 审核通过～开始发送《" + perf.get("novel_title", "") + "》～", cid, dry)
        for i, chunk in enumerate(chunks):
            decorated = "📖【" + str(i + 1) + "/" + str(len(chunks)) + "】\n\n" + chunk
            await _perf_send(sender, decorated, cid, dry)
            await asyncio.sleep(1.5)
        await _perf_send(sender, "（完）", cid, dry)


FAST_INTERVAL = 0.8  # 最快轮询间隔（秒）


def _max_interval() -> float:
    """当前时段的退避封顶间隔：早8~凌晨2=5s，凌晨2~早8=10s."""
    hour = datetime.now().hour  # 本地时间（北京时间）
    if 2 <= hour < 8:
        return 10.0
    return 5.0


def _next_interval(cur: float, had_new: bool) -> float:
    """退避一步：有新消息重置最快，没消息翻倍封顶，全程 jitter."""
    if had_new:
        return FAST_INTERVAL * random.uniform(0.9, 1.1)
    max_i = _max_interval()
    return min(cur * 2 * random.uniform(0.9, 1.1), max_i * random.uniform(0.95, 1.05))


# ---------------------------------------------------------------- 出站队列（P3）+ Bot 路由（P4）
# 防御性导入：deploy.ps1 需递归上传才能带上 runtime/ 子包。
# 缺包时静默退回直发，绝不让 bot 起不来。
_RUNTIME_IMPORT_ERR = ""                                   # 无条件定义，避免条件定义隐患
try:
    from runtime.outbox import INTERRUPTED_NOTE, OutboxSender, OutboxStore
    from runtime.routing import RoutingSender
    from runtime.transport_bot import DzmmBotSender
    _RUNTIME_IMPORT_OK = True
except Exception as _e:                                    # noqa: BLE001
    _RUNTIME_IMPORT_OK = False
    _RUNTIME_IMPORT_ERR = str(_e)[:120]


def _truthy_env(name: str, default: str = "off") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("on", "1", "yes", "true")


# 三个问候的功能开关 key：`period` 是拼出来的，**必须在这里显式登记** ——
# 否则静态扫描（tests/test_feature_registry.py）看不见它，而"_feat_on 遇到
# 不认识的 key 只记日志 + 放行"会把它变成一次静默失效。
_GREET_FEATURES = {"morning": "greet_morning", "noon": "greet_noon",
                   "evening": "greet_evening"}


def _outbox_enabled() -> bool:
    """DZMM_OUTBOX=off/0/no/false 可退回直发（回滚开关）。默认开。"""
    v = (os.environ.get("DZMM_OUTBOX") or "on").strip().lower()
    return v not in ("off", "0", "no", "false")


def _feat_on(cid: str, key: str) -> bool:
    """功能开关的**唯一入口**（P12-A）。各功能只调它，不许自己写判断。

    本期"只接线不上线"：没有任何覆盖时 `features.on()` 返回注册表里的默认值
    （= 今天的行为），所以这一版**零行为变化**；配置覆盖在 P12-C 才接上。

    未知 key 在这里**不抛**：这条路在消息处理热路径上，抛了会中断整个群的处理。
    折中做法是记日志 + 放行（放行 = 保持今天的行为），
    并由 `tests/test_feature_registry.py` 静态扫描 bot.py 里用到的 key
    是否都在注册表里 —— 开发期就拦住，而不是线上。
    """
    try:
        return _features.on(cid or "", key)
    except Exception as e:                                     # noqa: BLE001
        log("features: 判定失败 key=" + str(key) + " " + str(e)[:60])
        return True


def _continue_allowed(cid: str) -> bool:
    """`/继续` 归哪个功能管：看**上一次动作的类型**（羞辱 / 场景续写）。

    "共享分支"之一：同一条命令，归属取决于上一步做了什么，
    所以闸门不能只看命令本身。
    """
    try:
        last = _get_last_action(cid) or {}
    except Exception:                                          # noqa: BLE001
        last = {}
    if str(last.get("type") or "") == "insult":
        return _feat_on(cid, "insult")
    return _feat_on(cid, "scene_continue")


# 命令 → 功能 的归属表（**顺序必须与 cmd 分派链一致**）。
# 只用于兜底分支的一个判断：这条命令是不是属于某个**被关掉**的功能？
# 是 ⇒ 静默（不回、也不交给兜底聊天），否则"关掉投票"会变成
# "投票指令被当成闲聊回答" —— 功能没关干净。
#
# 每一项是 `((key, ...), 谓词)`，**键是元组**：
# 共享分支/落空转发的命令会挂在多个功能上，只要还有**任意一个**开着，
# 就不算"被关掉"。例如：
#   - `/选 N` 一个分支同时服务投票与剧情 ⇒ ("vote", "story")
#   - `/发骚结束` 没有进行中的场景时会**落空转发**到"发起场景" ⇒ ("scene_end", "scene")
#   - 停止词 `/结束` 在骰子局进行中归骰子 ⇒ 骰子那一项里也含这些词
_CMD_FEATURE_ORDER = (
    (("dice",), lambda c: c in ("/吹牛骰子", "/加入", "/牌局", "/看骰", "/开始",
                                "/下一轮", "/开", "/退出", "/结束")
     or parse_call(c.lstrip("/")) is not None),
    (("perf",), is_perf_end_cmd),
    (("perf_review",), lambda c: is_review_cmd(c) is not None),
    (("perf",), is_perf_start_cmd),
    (("story",), is_story_end_cmd),
    (("vote",), is_vote_end_cmd),
    (("vote", "story"), lambda c: is_vote_choice_cmd(c) is not None),
    (("scene_end", "scene"), is_fasao_end_cmd),
    (("diaojiao",), is_dj_end_cmd),
    (("help",), lambda c: c.startswith("/ ")),
    (("diaojiao",), lambda c: is_dj_command_cmd(c) is not None),
    (("diaojiao",), lambda c: is_diaojiao_cmd(c) is not None),
    (("scene_continue", "insult"), is_continue_cmd),
    (("scene_rewrite",), lambda c: is_rewrite_cmd(c) is not None),
    (("story",), is_story_start_cmd),
    (("vote",), is_vote_start_cmd),
    (("help",), lambda c: is_help_cmd(c) or is_guide_cmd(c)),
    (("memory_remember",), lambda c: is_remember_cmd(c) is not None),
    (("memory_recall",), is_recall_cmd),
    (("devil_roast",), lambda c: is_devil_cmd(c) is not None),
    (("intro",), is_intro_cmd),
    (("sync",), is_sync_cmd),
    (("register",), lambda c: is_register_cmd(c) is not None),
    (("query",), is_profile_cmd),
    (("trend",), is_trend_board_cmd),
    (("rank_heat",), is_rank_cmd),
    (("recap",), lambda c: is_recap_cmd(c) is not None),
    (("trend",), lambda c: is_trend_cmd(c) is not None),
    (("summary",), is_summary_cmd),
    (("fortune",), is_fortune_cmd),
    (("scene",), is_scene_cmd),
    (("poem",), lambda c: is_poem_cmd(c) is not None),
    (("insult",), lambda c: is_insult_cmd(c) is not None),
)


def _cmd_owned_by_disabled(cid: str, cmd: str) -> bool:
    """这条命令属于某个被关掉的功能吗？（只看**第一个**匹配的归属）

    一个归属可能挂多个功能（共享分支/落空转发）：**只要还有一个开着**，
    就返回 False（不静默）—— 否则关掉 A 会把还在用的 B 的命令一起吞掉。
    """
    for keys, pred in _CMD_FEATURE_ORDER:
        try:
            if pred(cmd):
                return not any(_feat_on(cid, k) for k in keys)
        except Exception:                                      # noqa: BLE001
            continue
    return False


def _log_route(event: str, cid: str, detail: str = "") -> None:
    """路由审计。account 太频繁不记；bot_* 事件都记（切换期观察用）。"""
    if event == "account":
        return
    log("bot路由: {} cid={} {}".format(event, (cid or "")[:8], str(detail)[:80]))


def _bot_senders() -> dict:
    """按**配置里的 Bot 注册表**造发送器：`{cid: DzmmBotSender}`。

    P12-H 之前这里只读 `DZMM_BOT_TOKEN`/`DZMM_BOT_CID` 两个环境变量，
    于是一个 Bot 都加不了 —— 配置里写 `bots[]` 也不会有人送（P11 留下的缺口：
    页面上能加 Bot，运行期没人读）。现在唯一真源是 `_CFG.bots`：
    每个 `enabled` 且 token 能解析到（`token_env` → `.env`，或内联）的 Bot
    各拿一个发送器，绑到它自己的 `cid` 上。

    一个群只能有一个 Bot（`config_store` 校验过），所以 cid 就是路由键。
    """
    senders = {}
    origin = (os.environ.get("DZMM_BOT_ORIGIN") or ORIGIN).rstrip("/")
    proxy = os.environ.get("DZMM_PROXY") or None
    for b in _CFG.bots:
        if not b.enabled:
            log("bot路由: 跳过未启用的 Bot {}（{}）".format(b.key, (b.cid or "")[:8]))
            continue
        token = b.resolve_token()
        if not token:
            log("bot路由: ⚠️ Bot {} 的 token 没解析到（token_env={!r}）—— 这个群退回账号".format(
                b.key, b.token_env))
            continue
        try:
            senders[b.cid] = DzmmBotSender(origin, token, proxy=proxy)
            log("bot路由: Bot {}({}) -> 群 {} 已就绪（uid={}）".format(
                b.key, b.name or "-", (b.cid or "")[:8], (b.uid or "未配置")[:8]))
        except Exception as e:                                 # noqa: BLE001
            log("bot路由: Bot {} 初始化失败（该群退回账号）：{}".format(
                b.key, str(e)[:80]))
    return senders


def _wrap_with_bot_routing(account):
    """把装了官方 Bot 的群路由到 Bot。**默认关闭** —— 接线但不上线。

    `DZMM_BOT_ROUTING=on` 才启用；缺 token/cid 或初始化异常一律退回账号。
    切换时机由人决定，不由代码替人决定。

    配置里一个可用 Bot 都没有时，退回"按环境变量造一个"的老路（P4 的行为），
    免得没挂 config.json 的旧部署突然没了 Bot 通道。
    """
    if not _RUNTIME_IMPORT_OK:
        return account
    if not _truthy_env("DZMM_BOT_ROUTING", "off"):
        log("bot路由: 未启用（DZMM_BOT_ROUTING=off），全部走账号")
        return account
    try:
        senders = _bot_senders()
        if not senders:
            token = (os.environ.get("DZMM_BOT_TOKEN") or "").strip()
            cid = (os.environ.get("DZMM_BOT_CID") or "").strip()
            if token and cid:
                origin = (os.environ.get("DZMM_BOT_ORIGIN") or ORIGIN).rstrip("/")
                senders = {cid: DzmmBotSender(
                    origin, token, proxy=(os.environ.get("DZMM_PROXY") or None))}
                log("bot路由: 配置里没有可用 Bot，退回 env 单 Bot cid={}".format(cid[:8]))
            else:
                log("bot路由: 没有任何可用 Bot（配置与 env 都没有），退回账号")
                return account
        log("bot路由: 已启用 origin={} 覆盖群={}".format(
            (os.environ.get("DZMM_BOT_ORIGIN") or ORIGIN).rstrip("/"),
            ",".join(c[:8] for c in senders)))
        return RoutingSender(account, senders, on_route=_log_route)
    except Exception as e:                                     # noqa: BLE001
        log("bot路由: 初始化失败，退回账号：" + str(e)[:100])
        return account


def _log_runtime_status() -> None:
    """启动时打印 P5 / P7 的运行时状态。

    没有这几行，这些能力生效与否在日志里看不出来（它们默认不输出任何东西）。
    """
    try:
        from session_guard import version as _sver
        from throttle_guard import stats as _tstats
        s = _tstats() or {}
        b = s.get("bucket") or {}
        log("P5: 会话锁=on(version={}) 限流={} rate={}/min burst={}".format(
            _sver(), "on" if s.get("enabled") else "off",
            b.get("rate_per_min"), b.get("capacity")))
    except Exception as e:                                 # noqa: BLE001
        log("P5: 状态未知（" + str(e)[:60] + "）")
    try:
        import state_store
        st = state_store.stats()
        log("P7: 存储={} db={} counts={}".format(
            st.get("backend"), st.get("db") or "-", st.get("counts") or {}))
    except Exception as e:                                 # noqa: BLE001
        log("P7: 状态未知（" + str(e)[:60] + "）")
    try:
        import runtime.prompts as _prompts
        ps = _prompts.stats()
        # 这行是 P8 第 3 步的自证：prompt 到底来自文件还是代码里的默认值。
        # 外置最常见的坑是"文件没挂上 → 悄悄退回默认值"，不打印就看不出来。
        log("P8: prompt dir={} 来自文件={}/{} 磁盘文件={}".format(
            ps.get("dir"), ps.get("from_file"), ps.get("total"),
            ps.get("files_on_disk")))
    except Exception as e:                                 # noqa: BLE001
        log("P8: 无 runtime.prompts，prompt 全部用代码默认值（"
            + str(e)[:50] + "）")
    try:
        # P11 A 期自证：群清单到底来自 config.json 还是代码里的默认值。
        # 与 P8 同一个道理 —— "悄悄退回默认值"必须能一眼看出来。
        cfg = _CFG
        log("P11: 配置={} 群={} 静默={} 筛查={} 推送={} Bot={} path={}".format(
            cfg.source, len(GROUPS), len(MUTE_CIDS), len(SCREEN_CIDS),
            len(PUSH_CIDS), len(cfg.bots), cfg.path))
        for b in cfg.bots:
            log("P11: Bot key={} name={} cid={} token={} uid={}".format(
                b.key, b.name, (b.cid or "")[:8],
                "已设置" if b.resolve_token() else "**未设置**",
                (b.uid or "未配置")[:8]))
        log("P11: 我是谁={}".format(
            ",".join(sorted(u[:8] for u in bot_uids())) or "（只有侧车账号）"))
        # 人设自证：配置里指定的按群人设到底生效了没有。
        # （这个字段曾经"画在页面上但运行期没人读"，不打印就不出来）
        _cp = config_personas()
        log("P11: 人设 配置指定={} 生效群数={}".format(len(_cp), len(GROUPS)))
        for _cid, _key in sorted(_cp.items()):
            log("P11: 人设 cid={} -> {}".format(_cid[:8], _key))
        # P12-A 自证：功能注册表的接线进度与"现在关掉了哪些"。
        # 与 P8/P11 同一个道理 —— "悄悄退回默认值/悄悄没接线"必须能一眼看出来。
        _fs = _features.summary()
        log("P12: 功能 共={} 已接线={} 待接线={} 默认关={} 秒退={}".format(
            _fs["total"], _fs["wired"], _fs["planned"],
            ",".join(_fs["off_by_default"]) or "无",
            "开" if _fs["env_all_on"] else "关"))
        # P12-B 自证：参数总共几个、被覆盖了几个、按群集合各几个群。
        # 与功能自证同一个道理 —— "页面上改了、这里没生效"要能一眼看出来。
        _ts = _tuning.summary()
        log("P12: 参数 共={} 已覆盖={} 集合={} 秒退={}".format(
            _ts["params"], _ts["overridden"],
            ",".join("{}:{}".format(k, v) for k, v in sorted(_ts["scopes"].items())),
            "开" if _ts["env_off"] else "关"))
        # P12-E 自证：人被改过人设文本/话术的**有几个人设、改了几处**。
        # 与上面同一个道理 —— "页面上改了、运行期没生效"要能一眼看出来。
        _ps = _personas.summary()
        log("P12: 人设文本 覆盖人设={} 文本段={} 话术={} 体检={}".format(
            _ps["personas"], _ps["fields"], _ps["phrases"],
            "全部通过" if not _ps["issues"] else "有 {} 条提示".format(
                len(_ps["issues"]))))
        for _line in _ps["issues"]:
            log("P12: 人设体检 " + _line)
    except Exception as e:                                 # noqa: BLE001
        log("P11: 配置状态未知（" + str(e)[:60] + "）")


def _wrap_with_outbox(sender, dry: bool):
    """组装发送链：`OutboxSender( RoutingSender( 账号 ) )`。

    出站队列放**最外层** —— 无论最终走 Bot 还是账号，都要落库、串行、可审计。
    任何一层异常都退回更简单的形态，不阻断 bot 启动。
    """
    if dry:
        return sender
    if not _RUNTIME_IMPORT_OK:
        log("outbox: runtime 包不可用，直发（" + _RUNTIME_IMPORT_ERR + "）")
        return sender

    inner = _wrap_with_bot_routing(sender)

    if not _outbox_enabled():
        log("outbox: 已禁用（DZMM_OUTBOX=off），直发")
        return inner
    try:
        db = os.environ.get("DZMM_OUTBOX_DB") or str(ROOT / "data" / "outbox.db")
        store = OutboxStore(db)
        # 启动时清理上次进程留下的未终态记录：发送是同步内联的，
        # 所以此刻任何 pending/leased 都是"上次发送途中被杀"留下的孤儿，
        # 没有任何代码会再去处理它（会让队列深度永远看着像有积压）。
        # 只标 failed 记录原因，**不重发**（不知道平台收没收到，重发就是重复打扰）。
        try:
            n = store.abandon_interrupted(INTERRUPTED_NOTE)
            if n:
                log("outbox: 清理上次中断的出站记录 {} 条（已标 failed，不重发）".format(n))
        except Exception as e:                             # noqa: BLE001
            log("outbox: 清理中断记录失败（不影响发送）：" + str(e)[:60])
        wrapped = OutboxSender(inner, store)
        log("outbox: 已启用 db=" + db + " worker=" + wrapped.worker_id)
        return wrapped
    except Exception as e:                                 # noqa: BLE001
        log("outbox: 初始化失败，退回直发：" + str(e)[:100])
        return inner


async def main_loop(interval: float, dry: bool, once: bool):
    global _ACTIVE_CID, _captcha_active, _captcha_notified
    sender = _wrap_with_outbox(Sender(), dry)
    if not dry:
        _log_runtime_status()
        try:
            await sender.start()
        except Exception as e:
            log("sender init failed: " + str(e)[:80])
        # P12-H：给"配置里没写 uid"的 Bot 从群成员表里认一次身份。
        # 放在 sender.start() **之后**：这一步要用登录态调 getMembers，
        # 启动最初那一刻 cookie 还没准备好，太早查只会白失败一次。
        try:
            _resolved = await asyncio.to_thread(_resolve_bot_uids)
            for _k, _uid in (_resolved or {}).items():
                log("P11: Bot {} 的身份 = {}".format(
                    _k, ("已认到 " + _uid[:8]) if _uid else
                    "**没认到**（Bot 可能还没被拉进群；进群后发出第一条消息时会自动认）"))
        except Exception as e:                                 # noqa: BLE001
            log("P11: Bot 身份识别失败（不影响运行）：" + str(e)[:80])

    state = load_state()
    log("start  dry=" + str(dry) + " interval=" + str(interval) + " groups=" + str(len(GROUPS)))

    captcha_cooldown_until = 0.0  # captcha 冷却截止时间戳
    http_backoff_until = 0.0  # 撞验证码后 HTTP 兜底退避截止时间戳（收发不受影响，继续）
    captcha_streak = 0  # 连续 captcha 次数（指数退避）
    _alerted_unauthorized = False  # unauthorized 告警防重（cookie 恢复后重置）
    _unauth_streak = 0  # 连续 unauthorized 次数（第 1 次即触发自动恢复）
    _recovering = False  # 是否正在自动恢复中
    group_meta = {cid: {"interval": FAST_INTERVAL, "last_poll": 0.0} for cid in GROUPS}

    # Socket.IO 订阅器（读消息主通道）：后台长连接监听 message:new，实时推送
    from sio_subscribe import SioSubscriber
    subscriber = SioSubscriber(GROUPS)
    sub_task = asyncio.create_task(subscriber.start())
    HTTP_FALLBACK_INTERVAL = 600  # HTTP 兜底对账间隔（秒）；断线补拉不受此限
    last_http_fallback = {cid: 0.0 for cid in GROUPS}
    _was_sio_connected = False  # 上一次 Socket.IO 连接状态（恢复检测用边沿：断→连）
    _last_scan_day = str(_scan_load().get("day") or "")  # 上次档案扫描日期（每天 06:00 一次）

    while True:
        # 本轮开始先清空人设：分组循环之外的所有 AI 调用都必须用默认人设。
        # 必须清！否则 contextvars 会残留上一轮最后一个群的人设（GROUPS 最后一个是 Rose 房间群），
        # 广播文案（问候/记忆汇报）就会被写成房间管家口吻。
        # —— 2026-09-17 事故：研讨群的早安变成了"（推开玫瑰房的窗…）Vv姐姐、糯糯姐，早上好"
        set_persona_cid("")
        # P12-F：热加载检查。便宜的那一段（一次 stat）每轮都做，
        # 只有真改了文件才会做完整解析 + 应用（见 runtime/hotload.py）。
        hot_reload_check()
        # 检测 renew_token 连续失败信号，主动退出交给 entrypoint 重启
        if (ROOT / "token.emergency").exists():
            try:
                (ROOT / "token.emergency").unlink()
            except Exception:
                pass
            log("⚠️ 检测到 token.emergency 信号，主动退出交给 entrypoint 重启")
            return
        # 撞验证码恢复检测：Socket.IO 从「断→连」的边沿 = 风控解除，触发恢复
        # （用边沿而非单次检查，避免 Socket.IO 短暂波动导致时序竞态漏掉恢复）
        is_conn = subscriber.is_connected
        if _captcha_active and is_conn and not _was_sio_connected:
            _captcha_active = False
            _captcha_notified.clear()
            for _cid in GROUPS:
                if _cid in MUTE_CIDS:
                    continue
                if _feat_on(_cid, "screen_rescreen"):
                    await _send(sender, _cid, "🤖 " + _personas.fill(CAPTCHA_RECOVERED_NOTICE, _cid), dry)
                    await _rescreen_pending(sender, _cid, dry)
            log("✅ 风控解除（Socket.IO 已重连），已群内提醒 + 补筛查")
        _was_sio_connected = is_conn
        # 每天 06:00 后首次循环：全群扫描昵称/简介/签名变化，有变化就在群里说一声
        _today = time.strftime("%Y-%m-%d")
        if time.localtime().tm_hour >= _tune("schedule.scan_hour") and _last_scan_day != _today:
            _last_scan_day = _today  # 先占位，避免扫描期间循环重入
            try:
                _notices = await asyncio.to_thread(_daily_profile_scan)
                for _ncid, _ntxt in _notices:
                    # 「档案自动构建」与「改名播报」是两个开关：扫描仍在做，
                    # 只是可以选择"不发群消息"（数据照样更新）
                    if _feat_on(_ncid, "notice_rename"):
                        await _send(sender, _ncid, _ntxt, dry)
            except Exception as _e:
                log("档案扫描异常: " + str(_e)[:60])
        # 定时问候 / 记忆汇报：用主 sender（页面常驻、秒发）发出，不再靠独立进程的冷浏览器
        # （2026-09-16 事故：22:00 的晚安因独立进程冷启浏览器 + 平台抖动，整页加载超时全失败，
        #   认证群整晚没收到问候）
        try:
            await _scheduled_pushes(sender, dry)
        except Exception as _e:
            log("定时推送异常: " + str(_e)[:80])
        # 吹牛骰子超时跳过（3 分钟无动作 → 移到顺序最后，游戏继续）
        _dg = _dice_state()
        _dice_cid = str(_dg.get("cid") or "")
        if (_dg.get("active") and _dg.get("phase") == "calling"
                and _feat_on(_dice_cid, "dice_timeout")
                and time.time() - _dg.get("last_action_ts", 0) > _tune("dice.timeout", _dice_cid)):
            _dcur = _dice_current_player(_dg)
            if _dcur:
                _dmsg = _dice_skip_cmd(_dcur)
                _dcid = _dg.get("cid") or GROUPS[0]  # 广播只发发起群
                if _dmsg:
                    await _send(sender, _dcid, "🤖 " + _dmsg, dry)
                    log("⏱️ 骰子局超时跳过：" + get_name(_dcur))
        # 处理私聊消息（吹牛骰子 /看骰）
        try:
            _pmsgs = subscriber.drain_private()
            for _pcid, _plist in _pmsgs.items():
                for _m in _plist:
                    if is_self(_m.get("sent_by")):
                        continue
                    _ptext = ((_m.get("content") or {}).get("text") or "").strip()
                    if _ptext == "/看骰":
                        _puid = _m.get("sent_by", "")
                        _pg = _dice_state()
                        if _pg.get("active") and _puid in _pg.get("dice", {}):
                            await _dice_send_private(sender, _puid, "你的骰子：" + dice_str(_pg["dice"][_puid]))
                        else:
                            await _dice_send_private(sender, _puid, "你现在没有进行中的骰子局哦～发 /吹牛骰子 发起～")
        except Exception as _pe:
            log("私聊消息处理异常: " + str(_pe)[:80])
        captcha_hit = False
        for cid in GROUPS:
            _set_active_cid(cid)  # 同时更新 bot.py 镜像与 runtime/ctx（搬出去的模块读它）
            set_persona_cid(cid)  # 分群人设：本轮所有 AI 调用都按这个群的人设走
            meta = group_meta[cid]
            # 该群没到轮询时间，跳过（每群独立退避）
            if time.time() - meta["last_poll"] < meta["interval"]:
                continue
            meta["last_poll"] = time.time()
            had_new = False  # 本轮该群有没有新的用户消息
            old_last_id = None  # 本轮指令/@ 消息处理前的 last_id（生成失败回滚用）
            try:
                # 读消息：Socket.IO 推送优先，HTTP 低频兜底（对账补漏）
                sio_msgs = subscriber.drain(cid)
                need_resync = subscriber.need_resync(cid)
                now_ts = time.time()
                from_sio = False
                if sio_msgs:
                    msgs = sio_msgs
                    from_sio = True
                elif (need_resync or (now_ts - last_http_fallback.get(cid, 0) >= HTTP_FALLBACK_INTERVAL)) and now_ts >= http_backoff_until:
                    last_http_fallback[cid] = now_ts
                    if need_resync:
                        subscriber.clear_resync(cid)
                    msgs = await asyncio.to_thread(get_messages, cid, 20)
                    # HTTP 兜底成功 = 风控解除：清撞验证码状态 + 所有群提醒 + 补筛查延迟的新成员
                    if _captcha_active:
                        _captcha_active = False
                        _captcha_notified.clear()
                        for _cid in GROUPS:
                            if _cid in MUTE_CIDS:
                                continue
                            await _send(sender, _cid, "🤖 " + _personas.fill(CAPTCHA_RECOVERED_NOTICE, _cid), dry)
                            await _rescreen_pending(sender, _cid, dry)
                        log("✅ 风控解除，已群内提醒 + 补筛查")
                else:
                    msgs = []
                if msgs:
                    group_state = state.get(cid) or {}
                    last_id = group_state.get("last_id")
                    # 定位 pending：Socket.IO 推送直接去重，HTTP 兜底用 last_id 定位
                    if from_sio:
                        pending = [m for m in msgs if m.get("message_id") != last_id]
                    else:
                        if last_id:
                            idx = -1
                            for i, m in enumerate(msgs):
                                if m.get("message_id") == last_id:
                                    idx = i
                                    break
                            if idx >= 0:
                                pending = msgs[idx + 1:]
                            else:
                                # last_id 已滚出窗口，只处理最新一条
                                pending = [msgs[-1]]
                        else:
                            # 首次启动，只处理最新一条，避免扫历史
                            pending = [msgs[-1]]
                    if not pending:
                        continue

                    # 静默监控群：只推进游标、不做任何处理（读得到但一声不吭）
                    if cid in MUTE_CIDS:
                        group_state["last_id"] = pending[-1]["message_id"]
                        state[cid] = group_state
                        save_state(state)
                        continue

                    # P12-H：新 Bot 的 uid 可能还不知道（配置里没写 / 平台没给）。
                    # 用"成员名 == 配置里的 Bot 名"把它认出来，之后 is_self() 就准了。
                    # ⚠️ 必须在下面**所有** is_self(...) 之前跑：否则新 Bot 在群里
                    # 发的第一条消息会被当成群友（可能自问自答）。
                    # 只查昵称缓存，不发请求，所以放进每轮循环是安全的。
                    for _m in pending:
                        if learn_bot_uid(_m.get("sent_by")):
                            log("P11: 认出一个 Bot 身份（按名字）：{} -> {}".format(
                                (_m.get("sent_by") or "")[:8],
                                ((_m.get("content") or {}).get("text") or "")[:20]))

                    # 本轮是否有真正的用户新消息（非 agent）
                    had_new = any(not is_self(m.get("sent_by")) for m in pending)

                    # 发言统计（活跃度 + 待评定发情缓冲 + 最近发言时间，每群独立持久化）
                    counts = group_state.get("msg_count") or {}
                    pending_buf = group_state.get("heat_pending") or {}
                    last_speak = group_state.get("last_speak") or {}
                    prev_speak = group_state.get("prev_speak") or {}  # 「上一条发言」时间（回顾功能的起点）
                    _now = time.time()
                    for m in pending:
                        uid = m.get("sent_by", "")
                        text = (m.get("content") or {}).get("text", "").strip()
                        if uid and not is_self(uid) and text and "加入了群聊" not in text and not text.startswith("/"):
                            if last_speak.get(uid):  # 先把「现在这条之前」的发言时间存下来
                                prev_speak[uid] = last_speak[uid]
                            counts[uid] = counts.get(uid, 0) + 1
                            last_speak[uid] = _now
                            if cid not in ROOM_CIDS:  # 房间群不攒发情值
                                buf = pending_buf.get(uid, [])
                                buf.append(text)
                                if len(buf) > _tune("heat.pending_max", cid):
                                    buf = buf[-10:]
                                pending_buf[uid] = buf
                    group_state["msg_count"] = counts
                    group_state["heat_pending"] = pending_buf
                    group_state["last_speak"] = last_speak
                    group_state["prev_speak"] = prev_speak
                    if had_new:
                        # 记录最后一条用户消息时间，立即落盘避免末尾 load_state 覆盖
                        group_state["last_msg_time"] = time.time()
                        state[cid] = group_state
                        save_state(state)

                    # 检测新成员加入：认证群做筛查（踢游客/踢男/提醒未填性别），
                    # 其他群只发中性欢迎语，不踢人、不发认证群文案
                    if not _get_story_state(cid).get("active"):
                        joins = _detect_joins(pending)
                        if joins:
                            if cid in SCREEN_CIDS and _feat_on(cid, "screen"):
                                log("🎉 [" + cid[:8] + "] 新成员加入 " + str(len(joins)) + " 人，开始筛查")
                                passed = await _handle_joins(sender, cid, joins, dry)
                                wtxt = _personas.fill(WELCOME_TEXT, cid)
                            elif cid in SCREEN_CIDS:
                                # 「入群筛查」关了：**不再踢人**，但仍要欢迎 ——
                                # 所以这里退化成"普通群"的处理方式（中性欢迎语）。
                                # ⚠️ 闸门只包住筛查这一支，不能包住整个入群块，
                                # 否则关掉筛查会把欢迎语、房间迎客一起关掉。
                                log("🎉 [" + cid[:8] + "] 新成员加入 " + str(len(joins)) + " 人（筛查已关闭，只欢迎）")
                                passed = joins
                                wtxt = _personas.fill(WELCOME_TEXT_PLAIN, cid)
                            elif cid in ROOM_CIDS:
                                # 房间群：主动问好 + 屋主规矩 + 花瓣变出招待品
                                # （女鬼期间屋子没人，不迎客）
                                # P12-A：`screen` 是入群筛查总开关（只管认证群那套踢人）；
                                # 房间迎客另有 `room_welcome`，两者互不影响。
                                if not _feat_on(cid, "room_welcome"):
                                    passed, wtxt = [], ""
                                elif (group_state.get("rose_ghost") or {}).get("active") \
                                        and _feat_on(cid, "room_ghost"):
                                    log("👻 [" + cid[:8] + "] 有客人进房，但管家不在（女鬼期间），不迎客")
                                    passed, wtxt = [], ""
                                else:
                                    log("🌹 [" + cid[:8] + "] 有客人进房 " + str(len(joins)) + " 人，上茶")
                                    for jid in joins:
                                        await _send(sender, cid,
                                                    "🤖 " + _room_greet_text(get_full_name(jid), cid), dry)
                                    passed, wtxt = [], ""
                            else:
                                log("🎉 [" + cid[:8] + "] 新成员加入 " + str(len(joins)) + " 人，只发欢迎语")
                                passed = joins
                                wtxt = _personas.fill(WELCOME_TEXT_PLAIN, cid)
                            for jid in passed:
                                jname = get_full_name(jid)
                                if not _feat_on(cid, "screen_welcome"):
                                    continue
                                await _send(sender, cid, "🤖 " + wtxt.replace("{name}", jname), dry)

                    # 检查 pending 里有没有 / 指令或 @ 消息
                    cmd = ""
                    cmd_sender = ""
                    at_msg = ""
                    at_sender = ""
                    room_hit = None  # (uid, 原文) 需要管家回应的房间消息
                    for m in pending:
                        if is_self(m.get("sent_by")):
                            continue  # 跳过 bot 自己的消息，避免 self-echo
                        t = (m.get("content") or {}).get("text", "").strip()
                        if t.startswith("/"):
                            cmd = t
                            cmd_sender = m.get("sent_by", "")
                            break
                        if not at_msg and is_at_me(t, cid):
                            at_msg = t
                            at_sender = m.get("sent_by", "")
                        if not room_hit and _is_room_trigger(cid, m.get("sent_by", ""), t):
                            room_hit = (m.get("sent_by", ""), t)

                    # 房间群：客人发 / 指令 → 变成一次"婉拒"的房间回应（只有主人能下命令）
                    if cmd and cid in ROOM_CIDS and not _room_owner(cid, cmd_sender):
                        room_hit = (cmd_sender, cmd)
                        cmd = ""
                        at_msg = ""

                    if cmd:
                        reply = ""
                        log("⚡ [" + cid[:8] + "] " + cmd[:40])
                        old_last_id = group_state.get("last_id", "")  # 记录旧值，生成失败时回滚
                        # 先推进 last_id，避免处理中异常导致同一条指令被重复处理
                        group_state["last_id"] = msgs[-1]["message_id"]
                        state[cid] = group_state
                        save_state(state)

                        perf = _get_perf_state(cid)
                        perf_active = perf.get("active")

                        # 吹牛骰子命令（优先）
                        # P12-A：整族命令共用一个功能开关 `dice`。
                        # 关了它，这些分支全部落空 → 最后落到 else 的"被关功能"判定 → 静默。
                        if cmd == "/吹牛骰子" and _feat_on(cid, "dice"):
                            reply = await _dice_start_cmd(cid, cmd_sender)
                        elif cmd == "/加入" and _feat_on(cid, "dice"):
                            reply = await _dice_join_cmd(cmd_sender)
                        elif cmd == "/牌局" and _feat_on(cid, "dice"):
                            reply = _dice_status_text(_dice_state())
                        elif cmd == "/看骰" and _feat_on(cid, "dice"):
                            # 群里发 /看骰，bot 私聊回骰子（不暴露给群里其他人）
                            _dg = _dice_state()
                            if _dg.get("active") and cmd_sender in _dg.get("dice", {}):
                                await _dice_send_private(sender, cmd_sender, "你的骰子：" + dice_str(_dg["dice"][cmd_sender]))
                                reply = "🎲 骰子已私聊发你啦～去看私聊哦～"
                            else:
                                reply = "你现在没有进行中的骰子局哦～"
                        elif cmd == "/开始" and _feat_on(cid, "dice"):
                            reply = await _dice_begin_cmd(sender, cmd_sender)
                        elif cmd == "/下一轮" and _feat_on(cid, "dice") and _dice_state().get("phase") == "round_end":
                            reply = await _dice_next_round_cmd(sender)
                        elif cmd == "/继续" and _feat_on(cid, "dice") and _dice_state().get("phase") == "round_end" and not _get_fasao_state(cid).get("active"):
                            reply = await _dice_next_round_cmd(sender)
                        elif cmd == "/开" and _feat_on(cid, "dice"):
                            reply = await _dice_open_cmd(cmd_sender)
                        elif cmd == "/退出" and _feat_on(cid, "dice") and _dice_state().get("active"):
                            reply = _dice_exit_cmd(cmd_sender)
                        elif cmd == "/结束" and _feat_on(cid, "dice") and _dice_state().get("active"):
                            reply = await _dice_quit_cmd(cmd_sender)
                        elif _feat_on(cid, "dice") and (dc := parse_call(cmd.lstrip("/"))) is not None:
                            reply = await _dice_call_cmd(cmd_sender, dc)
                        elif (_feat_on(cid, "dice") and _dice_state().get("active") and _dice_state().get("phase") == "calling"
                              and cmd.startswith("/") and re.match(r"^/\d", cmd)):
                            reply = "叫数格式要写成 /N个X 哦～比如 /3个3、/5个6（N 是数量，X 是 1~6 的点数）～"
                        # 公演结束
                        elif is_perf_end_cmd(cmd) and perf_active and _feat_on(cid, "perf"):
                            # 生成小说要跑十几次 pro 模型调用（很慢），放后台跑——
                            # 主循环是单协程串行处理所有群的，堵在这里会让 5 个群一起失联
                            # （2026-09-14 实测被堵 37 分钟以上）
                            asyncio.create_task(_perf_end_bg(sender, cid, dry))
                            reply = None
                        # 审核命令（仅糯糯，且待审核状态）
                        elif (rv := is_review_cmd(cmd)) is not None and cmd_sender == OWNER_UID and perf.get("review_status") == "pending" and _feat_on(cid, "perf_review"):
                            await _handle_review(sender, cid, rv, dry)
                        # 公演开始
                        elif is_perf_start_cmd(cmd) and _feat_on(cid, "perf"):
                            if _get_dj_state(cid).get("active"):
                                await _perf_send(sender, "调教进行中哦～先 /调教结束 再开公演～", cid, dry)
                            elif perf_active:
                                await _perf_send(sender, "公演已经进行中啦～", cid, dry)
                            else:
                                await _handle_perf_start(sender, cid, msgs[-1]["message_id"], dry)
                            reply = None  # 已自己发过消息了；空串会被当成"生成失败"回滚重试（2026-09-21 修）
                        # 公演进行中，其他指令不响应（交给互动逻辑）
                        # ⚠️ 这是**互斥分支**不是功能分支：但它也必须跟功能开关走 ——
                        # 否则把"公演"关掉后，残留的 active 会继续把这个群屏蔽住
                        # （运维在页面上关了功能，群里却更哑了 —— 那是最糟的结果）。
                        elif perf_active and _feat_on(cid, "perf"):
                            reply = "🎭 公演进行中哦～暂无法响应其他指令，想参与就直接发言，等公演结束后再试～"
                        # 剧情结束（提前结束 → 直接进结局）
                        elif is_story_end_cmd(cmd) and _get_story_state(cid).get("active") and _feat_on(cid, "story"):
                            reply = await asyncio.to_thread(_finish_story, cid)
                        # 投票结束（/投票结束 或停止词，投票进行中）
                        elif is_vote_end_cmd(cmd) and _get_vote_state(cid).get("active") and _feat_on(cid, "vote"):
                            reply = await asyncio.to_thread(_finish_vote, cid)
                        # 选择指令（投票优先，剧情次之；静默记录）
                        # ★ 共享分支：`/选 N` 同时服务投票与剧情，所以闸门必须**分两臂**加，
                        #   不能只看命令属于哪个功能（命令 → 功能不是一对一）。
                        elif (choice := is_vote_choice_cmd(cmd)) is not None and (
                                _feat_on(cid, "vote") or _feat_on(cid, "story")):
                            if _get_vote_state(cid).get("active") and _feat_on(cid, "vote"):
                                _record_vote(cid, cmd_sender, choice)
                                reply = None
                            elif _get_story_state(cid).get("active") and _feat_on(cid, "story"):
                                if choice in ("1", "2", "3"):
                                    _record_story_vote(cid, cmd_sender, choice)
                                reply = None
                            else:
                                reply = p("no_vote", cid)
                        elif is_fasao_end_cmd(cmd) and _get_fasao_state(cid).get("active") and _feat_on(cid, "scene_end"):
                            # 发骚结束（停止词，发骚进行中）
                            reply = await asyncio.to_thread(end_fasao, cid)
                        elif is_dj_end_cmd(cmd) and _feat_on(cid, "diaojiao"):
                            # 调教结束
                            dj = _get_dj_state(cid)
                            if dj.get("active"):
                                end_text = _dj_end_text(dj.get("target_uid"), dj)
                                reply = "🔗 调教结束啦～"
                                if end_text:
                                    reply += "\n" + end_text
                                _set_dj_state(cid, {"active": False})
                            else:
                                # 通用停止词（/结束 /停止 /停下 /终止）用通用提示；/调教结束 明确是调教
                                if cmd.startswith("/调教结束"):
                                    reply = p("no_dial", cid)
                                else:
                                    reply = p("no_play", cid)
                        elif cmd.startswith("/ ") and _feat_on(cid, "help"):
                            # 斜杠后紧跟空格（如 / 清歌），格式错误
                            reply = p("slash_space", cid)
                        elif (dj_cmd := is_dj_command_cmd(cmd)) is not None and _feat_on(cid, "diaojiao"):
                            # 调教命令（调教中才有效）
                            dj = _get_dj_state(cid)
                            if dj.get("active"):
                                if not dj_cmd:
                                    reply = "命令不能为空哦～格式 /命令 xxx"
                                else:
                                    dj["phase"] = "等回应"
                                    dj["last_activity"] = time.time()
                                    dj["round"] = dj.get("round", 0) + 1
                                    _set_dj_state(cid, dj)
                                    reply = await asyncio.to_thread(generate_diaojiao, _dj_target_text(dj["target_uid"]), dj_cmd)
                                    reply = (reply or "").strip()
                                    if reply:
                                        reply += "\n\n" + DJ_PARTICIPATE_HINT
                            else:
                                reply = p("unknown_cmd", cid)
                        elif is_diaojiao_cmd(cmd) is not None and _feat_on(cid, "diaojiao"):
                            # 发起调教
                            if _get_dj_state(cid).get("active"):
                                reply = "调教进行中哦～先 /调教结束 再发起新的～"
                            else:
                                reply = await asyncio.to_thread(start_diaojiao, cid, cmd, cmd_sender)
                        elif _get_dj_state(cid).get("active") and _feat_on(cid, "diaojiao"):
                            # 调教进行中，其他指令屏蔽（同样必须跟开关走，理由见公演那处）
                            reply = "🔗 调教进行中哦～想参与就发 /命令 xxx，或 /调教结束 收尾～"
                        elif is_continue_cmd(cmd) and _continue_allowed(cid):
                            # 续写（发骚续写优先，continue_last 内部分发发骚/羞辱）
                            # ★ 共享分支：按上一次动作的**类型**决定归哪个功能管
                            #   （insult 的上一段 → 羞辱；其余 → 场景续写）
                            reply = await asyncio.to_thread(continue_last, cid, cmd, cmd_sender)
                        elif is_rewrite_cmd(cmd) is not None and _feat_on(cid, "scene_rewrite"):
                            # 重写上一段（发骚 active 时有效）
                            reply = await asyncio.to_thread(rewrite_last, cid, cmd)
                        elif _get_fasao_state(cid).get("active") and _feat_on(cid, "scene"):
                            # 发骚进行中，其他指令屏蔽
                            reply = "🎲 发骚过程中暂时无法响应其他指令哦～请等结束后再试～想继续就发 /继续，想结束就发 /结束～"
                        elif is_story_start_cmd(cmd) and _feat_on(cid, "story"):
                            # 发起剧情游戏
                            if _get_story_state(cid).get("active"):
                                reply = "剧情已经进行中啦～"
                            else:
                                reply = await asyncio.to_thread(start_story, cid, cmd, cmd_sender)
                        elif is_vote_start_cmd(cmd) and _feat_on(cid, "vote"):
                            # 发起投票（轻量，可与其他功能并存）
                            if _get_vote_state(cid).get("active"):
                                reply = "已经有进行中的投票啦～想结束发 /投票结束～"
                            else:
                                reply = await asyncio.to_thread(start_vote, cid, cmd, cmd_sender)
                        elif _get_story_state(cid).get("active") and _feat_on(cid, "story"):
                            # 剧情进行中，其他指令屏蔽
                            reply = "🎬 剧情进行中哦～想投票就发 /选 1/2/3 或 /1/2/3，或 /剧情结束 收尾～"
                        elif _get_vote_state(cid).get("active") and _feat_on(cid, "vote"):
                            # 投票进行中，其他指令屏蔽
                            reply = "📊 投票进行中哦～想参与就发 /选 X 或 /X（X=序号），想结束发 /投票结束～"
                        elif (is_help_cmd(cmd) or is_guide_cmd(cmd)) and _feat_on(cid, "help"):
                            reply = p("help_text", cid) or _personas.fill(HELP_TEXT, cid)
                        elif (mc := is_remember_cmd(cmd)) is not None and _feat_on(cid, "memory_remember"):
                            # 记忆：/记住 内容
                            if not mc:
                                reply = p("mem_ask2", cid)
                            else:
                                _remember(cid, mc, get_name(cmd_sender))
                                reply = p("mem_saved", cid)
                        elif is_recall_cmd(cmd) and _feat_on(cid, "memory_recall"):
                            # 回忆：直接列出具体记忆内容（原文列表）
                            mem = _get_memory(cid)
                            if not mem:
                                reply = p("mem_empty", cid)
                            else:
                                total = len(mem)
                                shown = mem[-_tune("memory.report_items", cid):]
                                lines = [p("mem_list_head", cid).format(n=total)]
                                for i, m in enumerate(shown, 1):
                                    at_short = (m.get('at') or '')
                                    if len(at_short) >= 11:
                                        at_short = at_short[5:]
                                    lines.append(f"{i}. {m['text']}（{m.get('from', '?')}，{at_short}）")
                                if total > len(shown):
                                    lines.append(p("mem_list_more", cid).format(k=len(shown)))
                                reply = "\n".join(lines)
                        elif (devil_sw := is_devil_cmd(cmd)) is not None and _feat_on(cid, "devil_roast"):
                            # 主动吐槽开关（恶魔+冷场共用，全局配置）
                            # 跟功能开关走：配置里把它关了，群内就**不许**再打开
                            # （否则会出现"页面上关着、群里却是开的"这种自相矛盾）
                            group_state["devil_enabled"] = (devil_sw == "on")
                            state[cid] = group_state
                            save_state(state)
                            reply = ("💬 主动吐槽已开启～我会偶尔冒个泡哦～"
                                     if devil_sw == "on" else "💬 主动吐槽已关闭～我先安静啦")
                        elif is_intro_cmd(cmd) and _feat_on(cid, "intro"):
                            reply = p("intro_text", cid) or _personas.fill(INTRO_TEXT, cid)
                        elif is_sync_cmd(cmd) and _feat_on(cid, "sync"):
                            # 手动同步（仅糯糯）
                            if cmd_sender != OWNER_UID:
                                reply = p("sync_only_owner", cid)
                            else:
                                if not dry:
                                    await sender.send("🤖 收到同步指令～正在拉取增量消息，稍等一下下哦～", cid)
                                reply = await asyncio.to_thread(sync_profiles)
                        elif (reg_query := is_register_cmd(cmd)) is not None and _feat_on(cid, "register"):
                            # 注册：先发提醒，再执行，最后发结果
                            if not dry:
                                target = reg_query or get_name(cmd_sender)
                                await sender.send(p("register_working", cid, who=target), cid)
                            reply = await asyncio.to_thread(register_user, reg_query, cmd_sender)
                        elif (query := is_profile_cmd(cmd)) and _feat_on(cid, "query"):
                            # 查人：先提醒，再执行
                            if not dry:
                                await sender.send(p("query_working", cid, who=query), cid)
                            text, registered = await asyncio.to_thread(archive_query, query)
                            if not text:
                                reply = p("not_found", cid)
                            elif registered:
                                text = _append_memory_ref(text, cid, query)
                                reply = await asyncio.to_thread(generate_archive, text)
                            else:
                                text = _append_memory_ref(text, cid, query)
                                if not _feat_on(cid, "profile_card"):
                                    reply = p("not_found", cid)
                                else:
                                    reply = await asyncio.to_thread(generate_profile_summary, text)
                                    reply += "\n（这个人还没注册哦，发 /注册 可以建立档案～）"
                        elif is_trend_board_cmd(cmd) and _feat_on(cid, "trend"):
                            # 群运势榜（必须在 is_rank_cmd 之前，否则"运势排行"被骚度榜的"排行"吃掉）
                            reply = await week_trend_board(cid)
                            if not reply:
                                reply = p("unknown_cmd", cid)  # 非认证群：当成"不认识这个指令"
                        elif is_rank_cmd(cmd) and _feat_on(cid, "rank_heat"):
                            # 发情值排行
                            reply = await asyncio.to_thread(rank_summary)
                            if not reply:
                                reply = p("rank_empty", cid)
                        elif (rc_win := is_recap_cmd(cmd)) is not None and _feat_on(cid, "recap"):
                            # 「我不在的时候群里发生了什么」（仅认证群）
                            reply = await recap_absent(cid, cmd_sender, rc_win)
                            if not reply:
                                reply = p("unknown_cmd", cid)  # 非认证群：当成"不认识这个指令"
                        elif (tr_days := is_trend_cmd(cmd)) is not None and _feat_on(cid, "trend"):
                            # 过去 N 天运势趋势（必须排在 is_fortune_cmd 之前，否则 /运势趋势 会被 /运势 吃掉）
                            reply = await week_trend(cid, cmd_sender, tr_days)
                            if not reply:
                                reply = p("unknown_cmd", cid)  # 非认证群：当成"不认识这个指令"
                        elif is_summary_cmd(cmd) and _feat_on(cid, "summary"):
                            # 总结类指令：先拉群聊记录，再让 AI 总结
                            chat_text = await asyncio.to_thread(summarize_chat, cmd)
                            group_name = get_group_name()
                            full_text = ("群名：" + group_name + "\n" + chat_text) if group_name else chat_text
                            reply = await asyncio.to_thread(generate_summary, full_text)
                        elif is_fortune_cmd(cmd) and _feat_on(cid, "fortune"):
                            # 算命：给发指令的人算今天运势
                            reply = await asyncio.to_thread(fortune, cmd_sender, cid)
                        elif is_scene_cmd(cmd) and _feat_on(cid, "scene"):
                            # 涩涩骰子：人物×场景×体位×附加玩法
                            reply = await asyncio.to_thread(scene, cmd)
                        elif is_poem_cmd(cmd) is not None and _feat_on(cid, "poem"):
                            # 写诗：随机文学方向，根据档案+发言写短诗
                            reply = await asyncio.to_thread(poem, cmd, cmd_sender)
                        else:
                            # 羞辱指令（返回 None 表示不是羞辱）
                            insult_target = is_insult_cmd(cmd)
                            if insult_target is not None and _feat_on(cid, "insult"):
                                target_uid, text, custom, found = await asyncio.to_thread(_insult_resolve, insult_target, cmd_sender)
                                if not found:
                                    reply = p("not_found_insult", cid)
                                else:
                                    # 被羞辱 → 发情值 +15
                                    _add_heat(cid, target_uid, _tune("heat.insult_target", cid))
                                    history = _get_insult_history(target_uid)
                                    text = _append_memory_ref(text, cid, get_full_name(target_uid))
                                    reply, style = await asyncio.to_thread(generate_insult, text, custom, history)
                                    _record_insult(target_uid, style, reply)
                                    # 记录上一段（供 /继续 续写）
                                    _set_last_action(cid, {
                                        "type": "insult",
                                        "uid": target_uid,
                                        "full": get_full_name(target_uid),
                                        "material": text,
                                        "output": reply,
                                    })
                            elif _cmd_owned_by_disabled(cid, cmd):
                                # 属于被关掉的功能 ⇒ 静默（不回、也不交给兜底聊天）。
                                # 不这么做的话，"关掉投票"会变成"投票指令被当成闲聊回答"。
                                log("🔇 [" + cid[:8] + "] 功能已关闭，静默: " + cmd[:30])
                                reply = None
                            else:
                                reply = await asyncio.to_thread(generate_reply, [{"sender": "user", "text": cmd}])
                        if reply is not None:
                            reply = (reply or "").strip()
                            if reply:
                                reply = "🤖 " + reply
                                log("💬 " + reply[:200])
                                if dry:
                                    log("[dry] skip")
                                else:
                                    ok = await sender.send(reply, cid)
                                    log("✅" if ok else "❌")
                            else:
                                # 生成失败（空回复），抛异常让 except 统一回滚 last_id
                                raise RuntimeError("生成失败(空回复): " + cmd[:20])
                        # reply is None：静默成功（投票/剧情记录、公演屏蔽），不回复不报错

                    elif at_msg:
                        # 独占玩法进行中时，@ 提示进行中（打断保护）
                        active_hint = _active_play_hint(cid)
                        if active_hint:
                            reply = "🤖 " + active_hint
                            log("🚫 [" + cid[:8] + "] @消息被打断保护")
                            if not dry:
                                ok = await sender.send(reply, cid)
                                log("✅" if ok else "❌")
                        else:
                            # 被 @ 了（非公演中）
                            reply = ""
                            log("👋 [" + cid[:8] + "] @消息: " + at_msg[:40])
                            old_last_id = group_state.get("last_id", "")  # 记录旧值，生成失败时回滚
                            group_state["last_id"] = msgs[-1]["message_id"]
                            state[cid] = group_state
                            save_state(state)
                            q = _strip_at(at_msg, cid)  # 去 @前缀，得到纯提问
                            mem_content = _extract_remember(q) \
                                if _feat_on(cid, "memory_remember") else None
                            if mem_content is not None:
                                # 自然语言记忆请求：@小小糯 记住 XXX
                                if not mem_content:
                                    reply = p("mem_ask", cid)
                                else:
                                    _remember(cid, mem_content, get_name(at_sender))
                                    reply = p("mem_saved", cid)
                            elif _recap_intent(q) and _feat_on(cid, "recap"):
                                # 大白话入口：@小小糯 我不在的时候群里发生了什么
                                reply = await recap_absent(cid, at_sender, q)
                                if not reply:
                                    reply = ""
                            else:
                                perf_q = _detect_perf_query(at_msg) \
                                    if _feat_on(cid, "person_summary") else None
                                if perf_q:
                                    reply = await asyncio.to_thread(summarize_person_perf, perf_q[0], perf_q[1])
                                else:
                                    # 普通问答：精简上下文 + 注入记忆
                                    ctx = []
                                    for m in msgs[-4:]:
                                        muid = m.get("sent_by", "")
                                        if is_self(muid):
                                            continue
                                        mtxt = (m.get("content") or {}).get("text", "").strip()
                                        if mtxt:
                                            ctx.append({"sender": get_name(muid), "text": mtxt})
                                    glabel = _gender_label(at_sender)  # 女生/男生/未知
                                    ctx.append({"sender": get_name(at_sender) + ("（" + glabel + "）" if glabel != "未知" else ""), "text": q})
                                    mem = _retrieve_memory(cid, q, _tune("memory.inject", cid))
                                    if mem:
                                        mem_lines = "\n".join("- " + m["text"] for m in mem)
                                        ctx.insert(0, {"sender": "记忆", "text": "你之前记住的相关的事：\n" + mem_lines})
                                    reply = await asyncio.to_thread(generate_reply, ctx)
                            reply = (reply or "").strip()
                            if reply:
                                reply = "🤖 " + reply
                                log("💬 " + reply[:200])
                                if dry:
                                    log("[dry] skip")
                                else:
                                    ok = await sender.send(reply, cid)
                                    log("✅" if ok else "❌")
                            else:
                                # 生成失败（空回复），抛异常让 except 统一回滚 last_id
                                raise RuntimeError("生成失败(空回复): @消息")

                    elif room_hit:
                        # ============ 房间群：管家回应（被点名 / 主人指令 / 客人点单）============
                        r_uid, r_text = room_hit
                        cfg = ROOMS.get(cid) or {}
                        log("🌹 [" + cid[:8] + "] 房间消息: " + r_text[:40])
                        old_last_id = group_state.get("last_id", "")
                        group_state["last_id"] = msgs[-1]["message_id"]
                        state[cid] = group_state
                        save_state(state)
                        # ---- 女鬼状态：屋子里没人了（门和窗被带走），直到屋主回来 ----
                        ghost = group_state.get("rose_ghost") or {}
                        ghost_on = bool(ghost.get("active"))
                        if ghost_on and \
                                time.time() - float(ghost.get("ts") or 0) > ROOM_GHOST_MAX_H * 3600:
                            ghost_on = False  # 安全兜底：屋主太久没回来，自动收拾好（防房间永久哑掉）
                            group_state["rose_ghost"] = {"active": False, "ts": time.time()}
                            state[cid] = group_state
                            save_state(state)
                            log("👻 [" + cid[:8] + "] 女鬼状态超时，自动恢复")
                        if ghost_on and _room_owner(cid, r_uid) and _feat_on(cid, "room_ghost"):
                            # 屋主回来了 → 屋子复原
                            group_state["rose_ghost"] = {"active": False, "ts": time.time()}
                            state[cid] = group_state
                            save_state(state)
                            log("🌹 [" + cid[:8] + "] " + get_name(r_uid)[:12] + " 回来，女鬼退场")
                            reply = _personas.fill(ROOM_GHOST_BACK, cid, who=get_name(r_uid))
                        elif ghost_on and _feat_on(cid, "room_ghost"):
                            # 女鬼还在：再摘花 → 警告+恐吓；其他话一律不接（小小糯在门外）
                            if _is_flower_grab(r_text) and \
                                    time.time() - float(group_state.get("rose_ghost_warn_ts") or 0) \
                                    >= ROOM_GHOST_WARN_COOLDOWN:
                                group_state["rose_ghost_warn_ts"] = time.time()
                                state[cid] = group_state
                                save_state(state)
                                log("👻 [" + cid[:8] + "] 女鬼期间又摘花，警告+恐吓")
                                reply = _personas.fill(ROOM_GHOST_WARN, cid)
                            else:
                                reply = None  # 静默：管家不在，门也没有
                        elif r_text.startswith("/") and not _room_owner(cid, r_uid) \
                                and _feat_on(cid, "room_refuse"):
                            reply = ROOM_GUEST_REFUSE  # 客人用指令 → 固定话术婉拒，不惊动 AI
                        elif (_is_flower_grab(r_text)
                              and _feat_on(cid, "room_flower")
                              and _feat_on(cid, "room_ghost")
                              and await asyncio.to_thread(_room_male, cid, r_uid)
                              and time.time() - float(group_state.get("rose_ghost_ts") or 0)
                              >= ROOM_GHOST_COOLDOWN):
                            # 男生摘花 → 放出红衣女鬼（固定剧本，不走 AI）
                            group_state["rose_ghost_ts"] = time.time()
                            group_state["rose_ghost"] = {"active": True, "ts": time.time()}
                            state[cid] = group_state
                            save_state(state)
                            log("👻 [" + cid[:8] + "] " + get_name(r_uid)[:12] + " 摘花，放出红衣女鬼")
                            reply = _personas.fill(ROOM_GHOST_TEXT, cid)
                        elif (_feat_on(cid, "horoscope")
                              and (_sr := await _sign_reply(
                                  cid, r_uid, r_text,
                                  "Vv姐姐" if r_uid == VV_UID else
                                  ("糯糯姐" if r_uid == NUONUO_UID else "客人")))):
                            # 星座运势（待客服务）：确定性算分 + 管家口吻点评
                            log("🔮 [" + cid[:8] + "] 星座: " + r_text[:30])
                            reply = _sr
                        else:
                            n = _strike_get(group_state, r_uid, int(cfg.get("strike_reset_h") or 12))
                            ctx = []
                            for m in msgs[-5:]:
                                muid = m.get("sent_by", "")
                                if is_self(muid):
                                    continue
                                mtxt = (m.get("content") or {}).get("text", "").strip()
                                if mtxt:
                                    ctx.append({"sender": get_name(muid), "text": mtxt})
                            ctx.insert(0, {"sender": "系统", "text":
                                           "（正在跟你说话的这位是「" + get_name(r_uid)
                                           + "」，"
                                           + ("是这间屋子的主人，要听 TA 的吩咐"
                                              if _room_owner(cid, r_uid) else "是一位来串门的客人")
                                           + "）"})
                            ctx.insert(0, {"sender": "系统", "text":
                                           "（如果这句话是在调戏你，就是 TA 的第 " + str(n + 1)
                                           + " 次，按第 " + str(n + 1)
                                           + " 档演出，并在回复最末尾加上 [[STRIKE]]）"})
                            reply = await asyncio.to_thread(generate_reply, ctx)
                            # 调戏计数：AI 的 [[STRIKE]] 标记为主，关键词兜底（必须点名了管家）
                            # P12-A：`room_lewd` 只管"三级递进"这套计数/送出机制；
                            # 关掉它，管家照常回话、只是不再数次数（不是把房间关了）
                            _hit = _feat_on(cid, "room_lewd") and "[[STRIKE]]" in (reply or "")
                            if not _hit:
                                _named = mentions_self(r_text, cid)
                                _hit = _named and any(w in r_text for w in ROOM_LEWD_WORDS)
                                if _hit:
                                    log("🌹 [" + cid[:8] + "] 调戏（关键词兜底命中）")
                            if _hit:
                                nn = n + 1
                                if nn >= int(cfg.get("strike_limit") or 3):
                                    nn = 0  # 已送出门口，重新计数
                                    log("🌹 [" + cid[:8] + "] " + get_name(r_uid)[:12]
                                        + " 第三次调戏，送出去（纯台词）")
                                _strike_set(group_state, r_uid, nn)
                                state[cid] = group_state
                                save_state(state)
                        if reply is None:
                            pass  # 静默（女鬼期间管家不在，不接话）
                        else:
                            reply = reply.replace("[[STRIKE]]", "").strip()
                            if reply:
                                reply = "🤖 " + reply
                                log("💬 " + reply[:200])
                                if dry:
                                    log("[dry] skip")
                                else:
                                    ok = await sender.send(reply, cid)
                                    log("✅" if ok else "❌")
                            else:
                                raise RuntimeError("生成失败(空回复): 房间消息")

                    # 恶魔吐槽（无人@无人指令，普通聊天自主毒舌）
                    if not cmd and not at_msg and not room_hit:
                        roast = await asyncio.to_thread(_maybe_devil_roast, cid, msgs, group_state)
                        roast = (roast or "").strip()
                        if roast:
                            log("😈 [" + cid[:8] + "] 吐槽")
                            if dry:
                                log("[dry] " + roast[:50])
                            else:
                                ok = await sender.send("🤖 " + roast, cid)
                                log("✅" if ok else "❌")
                            # 立即落盘（devil 字段 + 本轮统计，避免末尾 load_state 覆盖）
                            state[cid] = group_state
                            save_state(state)

                    # 调教状态流转（目标回应 / 冷场接管 / 超时结束）
                    if _get_dj_state(cid).get("active"):
                        dj_reply = _check_diaojiao(cid, pending)
                        dj_reply = (dj_reply or "").strip()
                        if dj_reply:
                            log("🔗 [" + cid[:8] + "] 调教")
                            if dry:
                                log("[dry] " + dj_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + dj_reply, cid)
                                log("✅" if ok else "❌")

                    # 公演进行中的互动（丰富/羞辱/点评）
                    if _get_perf_state(cid).get("active") and _feat_on(cid, "perf_enrich"):
                        interact_reply = _check_perf_interact(cid, msgs)
                        interact_reply = (interact_reply or "").strip()
                        if interact_reply:
                            log("🎭 [" + cid[:8] + "] 互动")
                            if dry:
                                log("[dry] " + interact_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + interact_reply, cid)
                                log("✅" if ok else "❌")

                    # 剧情游戏状态流转（提醒投票 / 推进剧情 / 结局）
                    if _get_story_state(cid).get("active"):
                        story_reply = _check_story(cid)
                        story_reply = (story_reply or "").strip()
                        if story_reply:
                            log("🎬 [" + cid[:8] + "] 剧情")
                            if dry:
                                log("[dry] " + story_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + story_reply, cid)
                                log("✅" if ok else "❌")

                    # 重新加载 state（scene/羞辱的 _add_heat 内部已改磁盘），避免内存旧 group_state 覆盖加分
                    state = load_state()
                    group_state = state.get(cid) or {}

                    # 推进 last_id 到最新（"只处理之后"的逻辑保证不会重复）
                    group_state["last_id"] = msgs[-1]["message_id"]
                    state[cid] = group_state
                    save_state(state)

                    # 批量评定发情值：30分钟周期；有发情值≥80的成员刚发言则立即评定（高潮卡在发言时刻）
                    hot_speaker = any(
                        (group_state.get("heat_value") or {}).get(m.get("sent_by", ""), 0) >= _tune("heat.judge_trigger", cid)
                        for m in pending
                        if m.get("sent_by") and not is_self(m.get("sent_by"))
                        and (m.get("content") or {}).get("text", "").strip()
                        and "加入了群聊" not in (m.get("content") or {}).get("text", "")
                    )
                    if (group_state.get("heat_pending") or {}) and cid not in ROOM_CIDS and \
                            _feat_on(cid, "heat") and \
                            (time.time() - group_state.get("heat_last_judge", 0)
                             >= _tune("heat.judge_interval", cid) or hot_speaker):
                        try:
                            await asyncio.to_thread(_judge_pending, cid)
                        except Exception as e:
                            log("judge err: " + str(e)[:60])
                        # 重新加载（_judge_pending 内部已写回 state.json）
                        state = load_state()
                        group_state = state.get(cid) or {}

                    # 高潮检测：发情值≥100 → 高潮事件 + 发布排名（剧情进行中跳过，避免刷屏）
                    if _get_story_state(cid).get("active") or cid in ROOM_CIDS \
                            or not _feat_on(cid, "heat_climax"):
                        hits, climax_changed = [], False
                    else:
                        hits, climax_changed = _climax_hits(group_state)
                    if climax_changed:
                        state[cid] = group_state
                        save_state(state)
                    if hits:
                        await _handle_climax(sender, cid, dry, hits)
                        await _publish_rank(sender, cid, dry)
                    else:
                        # 定时发布：每天 12:00 和 18:00
                        now = datetime.now()
                        today = now.strftime("%Y-%m-%d")
                        has_data = bool((group_state.get("heat_value") or {}) or (group_state.get("msg_count") or {}))
                        if (now.hour in _rank_hour_set() and cid in PUSH_CIDS
                                and group_state.get("last_daily_rank_date") != today
                                and _feat_on(cid, "rank_push") and has_data):
                            group_state["last_daily_rank_date"] = today
                            state[cid] = group_state
                            save_state(state)
                            await _publish_rank(sender, cid, dry)

                # 冷场吐槽（群里冷清，时间驱动，无新消息也执行）——房间群不毒舌
                cold_reply = "" if cid in ROOM_CIDS or not _feat_on(cid, "devil_cold") \
                    else await asyncio.to_thread(_maybe_devil_cold_roast, cid)
                cold_reply = (cold_reply or "").strip()
                if cold_reply:
                    log("😈 [冷场] [" + cid[:8] + "] 吐槽")
                    if dry:
                        log("[dry] " + cold_reply[:50])
                    else:
                        ok = await sender.send("🤖 " + cold_reply, cid)
                        log("✅" if ok else "❌")

                # 发骚超时自动结束（防卡死：发骚 15 分钟无互动自动解锁）
                if _get_fasao_state(cid).get("active"):
                    fasao_timeout = _check_fasao_timeout(cid)
                    fasao_timeout = (fasao_timeout or "").strip()
                    if fasao_timeout:
                        log("⏱️ [" + cid[:8] + "] 发骚超时自动结束")
                        if dry:
                            log("[dry] " + fasao_timeout[:50])
                        else:
                            ok = await sender.send("🤖 " + fasao_timeout, cid)
                            log("✅" if ok else "❌")

                # 公演超时自动解锁（P12-A 补）。原先**没有超时**：
                # 人散了没人发 /公演结束 ⇒ 这个群被永久屏蔽，只有停止词那条分支可达。
                # 这里只解锁、不发消息、不生成小说（待审的 review_status 保留）。
                if _get_perf_state(cid).get("active"):
                    _check_perf_timeout(cid)

                # 投票定时检查（截止到→自动结束；时间驱动，无新消息也执行）
                if _get_vote_state(cid).get("active"):
                    vote_reply = _check_vote(cid)
                    vote_reply = (vote_reply or "").strip()
                    if vote_reply:
                        log("📊 [" + cid[:8] + "] 投票")
                        if dry:
                            log("[dry] " + vote_reply[:50])
                        else:
                            ok = await sender.send("🤖 " + vote_reply, cid)
                            log("✅" if ok else "❌")
            except Exception as e:
                msg = str(e)
                if "captcha" in msg.lower():
                    # 撞验证码：只降级 HTTP 功能，不退出（收发走 /ws + Playwright 不受影响）
                    http_backoff_until = time.time() + 3600  # HTTP 兜底退避 1 小时
                    _captcha_active = True
                    if not _captcha_notified.get(cid):
                        _captcha_notified[cid] = True
                        if _feat_on(cid, "screen_captcha_notice"):
                            await _send(sender, cid, "🤖 " + _personas.fill(CAPTCHA_NOTICE, cid), dry)
                        # 拉 noVNC 人工验证窗口（撞验证码时提供人工过滑块入口，只拉一次防刷屏）
                        try:
                            subprocess.Popen(
                                [HERMES_PY, "captcha_novnc.py"],
                                cwd=str(ROOT), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
                            log("已拉起 captcha_novnc.py（noVNC 人工验证）")
                        except Exception as _e:
                            log("拉起 captcha_novnc 失败: " + str(_e)[:60])
                    log("⚠️ 撞验证码，降级 HTTP 功能（收发继续），HTTP 兜底退避 1 小时")
                    # 不写 bot.paused、不 return，继续循环处理 Socket.IO 实时推送
                if "unauthorized" in msg.lower() or "invalid authentication" in msg.lower():
                    captcha_hit = True
                    captcha_cooldown_until = time.time() + 1800  # cookie 失效，冷却 30 分钟（避免频繁失败触发 IP 风控）
                    _unauth_streak += 1
                    log("⚠️ UNAUTHORIZED（cookie 可能失效），冷却 30 分钟（连续第 " + str(_unauth_streak) + " 次）")
                    # 事件驱动告警：cookie 失效发微信（防重，cookie 恢复后重置）
                    if not _alerted_unauthorized and _feat_on("", "alert_cookie"):
                        _alerted_unauthorized = True
                        try:
                            ok = await asyncio.to_thread(send_weixin, "🚨【小小糯bot】DZMM cookie 失效（unauthorized），已自动触发恢复，无需手动操作")
                            log("📱 unauthorized 告警: " + ("✅ 已发微信" if ok else "❌ 发送失败（代理可能不稳）"))
                        except Exception as _e:
                            log("📱 unauthorized 告警异常: " + str(_e)[:60])
                    # 第 1 次 unauthorized 就触发自动登录恢复（headless 兜底锁屏也能成功，不必等 2 次）
                    if _unauth_streak >= 1 and not _recovering and _feat_on("", "auto_recover"):
                        _recovering = True
                        log("🔄 连续 " + str(_unauth_streak) + " 次 unauthorized，触发自动登录恢复")
                        try:
                            subprocess.Popen(
                                [HERMES_PY, "auto_recover.py"],
                                cwd=str(ROOT),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True,
                            )
                        except Exception as _e:
                            log("🔄 自动恢复触发失败: " + str(_e)[:60])
                    break
                # 生成失败（网络超时等），回滚 last_id，下轮重试
                if old_last_id is not None:
                    try:
                        _rollback_last_id(cid, old_last_id)
                        log("⚠️ [" + cid[:8] + "] 生成异常，回滚 last_id，下轮重试")
                    except Exception:
                        pass
                log("loop[" + cid[:8] + "]: " + msg[:100])

            # Socket.IO 订阅是实时推送，drain 为纯内存操作，固定最快间隔（不退避）；
            # HTTP 兜底已由 HTTP_FALLBACK_INTERVAL 独立低频控制，无需在此退避
            meta["interval"] = FAST_INTERVAL
        if once:
            break
        # captcha 降频：触发后长冷却，冷却结束且恢复后才重置计数
        if captcha_hit or time.time() < captcha_cooldown_until:
            wait = max(captcha_cooldown_until - time.time(), 1.0)
        else:
            captcha_streak = 0  # 恢复正常轮询，重置连续 captcha 计数
            _alerted_unauthorized = False  # 恢复正常，重置告警防重标志
            _unauth_streak = 0  # 恢复正常，重置连续 unauthorized 计数
            _recovering = False  # 重置自动恢复标志
            wait = 0.2  # 小睡，每群按自己的 interval 判断轮询时间
        await asyncio.sleep(wait)

    await sender.close()



# ---------------------------------------------------------------- 房间群（独立人设 + 屋主权限）


# ---------------------------------------------------------------- 星座运势（Rose 专属）

# 一级：全称「X座」—— 任何时候都认（天平座是常见错写，带"座"所以安全）
# 二级：简称 —— 只有同句出现星座意图词时才认
# （已删掉"牧羊/室女/人马/山羊/宝瓶"这些非标准别名：牧羊犬、人马、山羊本身就有歧义）
# 句首「我+座名」也算自报星座（"我天秤"、"我水瓶的"）
# 注意必须锚定句首：否则"我今天买了个水瓶"会被误判


# ---------------------------------------------------------------- 「我不在的时候」回顾（仅认证群）


# ---------------------------------------------------------------- 过去 N 天运势趋势（认证群）


def _room_male(cid: str, uid: str) -> bool:
    """这人是不是男生（档案优先；档案没写就实时查一次并写回档案）。阻塞调用，放线程里跑."""
    def g(v):
        v = str(v or "").strip().lower()
        if v in ("male", "man", "男", "m"):
            return "male"
        if v in ("female", "lesbian", "女", "f", "百合"):
            return "female"
        return ""
    p = get_profile(uid) or {}
    r = g(p.get("gender"))
    if r:
        return r == "male"
    try:
        u = get_user(cid, uid) or {}
    except Exception:
        u = {}
    r = g(u.get("gender"))
    if r and p:
        try:  # 顺手写回档案，避免每次都查
            p["gender"] = r
            upsert_profile(uid, p)
        except Exception:
            pass
    return r == "male"


def _room_greet_text(name: str, cid: str = None) -> str:
    """房间群的新客欢迎语（固定模板 + 随机招待品，不消耗 AI 调用）.

    模板和候选句里都可能有 `{me}`（本群管家的名字，见 personas.fill）——
    房间管家改过名，欢迎语里必须跟着改，不能写死。
    """
    return _personas.fill(
        ROOM_GREET_TEMPLATE
        .replace("{name}", name)
        .replace("{item}", random.choice(ROOM_GREET_ITEMS))
        .replace("{line}", random.choice(ROOM_GREET_LINES)),
        cid)


def _strike_get(group_state: dict, uid: str, reset_h: int) -> int:
    """该客人当前累计调戏次数（超过 reset_h 小时未犯 → 视为 0）."""
    d = (group_state.get("rose_strikes") or {}).get(uid) or {}
    n = int(d.get("n") or 0)
    if n and time.time() - float(d.get("at") or 0) > reset_h * 3600:
        return 0
    return n


def _strike_set(group_state: dict, uid: str, n: int) -> None:
    d = group_state.get("rose_strikes") or {}
    if n <= 0:
        d.pop(uid, None)
    else:
        d[uid] = {"n": n, "at": time.time()}
    group_state["rose_strikes"] = d


def _is_join_msg(m: dict) -> bool:
    """判断是否'加入群聊'系统消息."""
    text = (m.get("content") or {}).get("text", "")
    return "加入了群聊" in text


def _detect_joins(pending: list) -> list:
    """检测 pending 里的新成员加入，返回新成员 uid 列表（去重，排除 bot）."""
    joins = []
    for m in pending:
        if is_self(m.get("sent_by")):
            continue
        if _is_join_msg(m):
            uid = m.get("sent_by", "")
            if uid and uid not in joins:
                joins.append(uid)
    return joins


async def _send(sender, cid: str, text: str, dry: bool) -> bool:
    """发送消息（dry 模式只 log）。"""
    if dry:
        log("[dry] " + text[:60])
        return True
    ok = await sender.send(text, cid)
    log("✅" if ok else "❌")
    return ok


async def _kick_and_notify(sender, cid: str, uid: str, full: str,
                           warn_msg: str, done_msg: str, dry: bool) -> None:
    """三段式移出：提醒本人 → 移出 → 通知群里。移出失败发飞书告警不阻断。"""
    # ① 提醒本人（还在群里，能看到）
    await _send(sender, cid, "🤖 " + warn_msg.format(name=full), dry)
    await asyncio.sleep(2)  # 给被移出者看到提醒的缓冲
    # ② 移出
    try:
        await asyncio.to_thread(remove_member, cid, uid)
        log("🚫 已移出 " + full + " (" + uid[:8] + ")")
        try:
            await asyncio.to_thread(send_weixin, "🚫【小小糯bot】已移出「" + full + "」")
        except Exception:
            pass
    except Exception as e:
        log("踢人失败 " + full + ": " + str(e)[:80])
        try:
            await asyncio.to_thread(send_weixin, "🚨【小小糯bot】移出失败（" + full + "）: " + str(e)[:80])
        except Exception:
            pass
        return
    # ③ 通知群里
    await _send(sender, cid, "🤖 " + done_msg.format(name=full), dry)


def _add_pending_screen(cid: str, uid: str) -> None:
    """撞验证码期间的新成员记入待筛查队列（持久化到 state.json）。"""
    try:
        state = load_state()
        gs = state.get(cid) or {}
        pending = gs.get("pending_screen") or []
        if uid not in pending:
            pending.append(uid)
            gs["pending_screen"] = pending
            state[cid] = gs
            save_state(state)
    except Exception:
        pass


async def _notify_join_delayed(full: str, cid: str) -> None:
    """撞验证码期间新成员延迟筛查的飞书告警。"""
    if not _feat_on(cid, "alert_captcha"):
        return
    try:
        await asyncio.to_thread(send_weixin, "🚨【小小糯bot】撞验证码期间新成员「" + full
                                + "」加入（" + cid[:8] + "），已延迟筛查，恢复后自动补筛")
    except Exception:
        pass


async def _rescreen_pending(sender, cid: str, dry: bool) -> None:
    """风控解除后补筛查撞验证码期间延迟的新成员。"""
    state = load_state()
    gs = state.get(cid) or {}
    pending = list(gs.get("pending_screen") or [])
    if not pending:
        return
    gs["pending_screen"] = []
    state[cid] = gs
    save_state(state)
    log("🔄 [" + cid[:8] + "] 风控解除，补筛查 " + str(len(pending)) + " 名新成员")
    passed = await _handle_joins(sender, cid, pending, dry)
    for jid in passed:
        jname = get_full_name(jid)
        await _send(sender, cid, "🤖 " + _personas.fill(WELCOME_TEXT, cid).replace("{name}", jname), dry)


async def _handle_joins(sender, cid: str, new_uids: list, dry: bool) -> list:
    """新成员筛查：踢游客 / 踢男 / 提醒未填性别 / 提醒未认证女生。返回放行（需发欢迎语）的 uid 列表。
    撞验证码（get_user/get_verified 返回 None）时延迟筛查：群里说明 + 飞书告警 + 记入 pending_screen。"""
    passed = []
    for uid in new_uids:
        full = get_full_name(uid)
        # 1. 游客账号名
        # P12-A：`screen` 是总开关（在调用点），这里三个是**分项**：
        # 关掉 `screen_tourist` 就不再踢游客（也不记待筛），其余筛查照常。
        if any(k in full for k in TOURIST_KEYWORDS) and _feat_on(cid, "screen_tourist"):
            if _captcha_active:
                # 撞验证码：延迟踢游客
                if _feat_on(cid, "screen_pending"):
                    _add_pending_screen(cid, uid)
                await _send(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
                await _notify_join_delayed(full, cid)
                continue
            await _kick_and_notify(sender, cid, uid, full, JOIN_WARN_TOURIST, JOIN_DONE_TOURIST, dry)
            continue
        if any(k in full for k in TOURIST_KEYWORDS):
            # 游客但"踢游客"已关：放行去发欢迎语，不做任何筛查动作
            passed.append(uid)
            continue
        # 2/3/4. 按性别
        try:
            u = await asyncio.to_thread(get_user, cid, uid)
        except Exception:
            u = None
        if u is None:
            # get_user 失败（撞验证码/网络）→ 延迟筛查
            if _feat_on(cid, "screen_pending"):
                _add_pending_screen(cid, uid)
            await _send(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
            await _notify_join_delayed(full, cid)
            continue
        g = u.get("gender")
        if g == "male" and _feat_on(cid, "screen_gender"):
            await _kick_and_notify(sender, cid, uid, full, JOIN_WARN_MALE, JOIN_DONE_MALE, dry)
        elif g in (None, "", "unknown"):
            # 未填性别：先欢迎，再请管理员留意 + @本人提醒（不踢）
            await _send(sender, cid, "🤖 " + _personas.fill(WELCOME_TEXT, cid).replace("{name}", full), dry)
            await _send(sender, cid, "🤖 " + JOIN_WARN_NULL.format(name=full), dry)
        else:
            # female（或已关掉"踢男性"时的 male）：再查妹子认证状态
            try:
                v = await asyncio.to_thread(get_verified, uid)
            except Exception:
                v = None
            if v is None:
                # 查认证失败（撞验证码）→ 延迟筛查
                if _feat_on(cid, "screen_pending"):
                    _add_pending_screen(cid, uid)
                await _send(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
                await _notify_join_delayed(full, cid)
                continue
            if bool((v or {}).get("verified")):
                # 认证妹子，放行交给主循环发欢迎语
                passed.append(uid)
            else:
                # 女生但未认证：先欢迎，再请管理员留意 + @本人提醒（不踢）
                await _send(sender, cid, "🤖 " + _personas.fill(WELCOME_TEXT, cid).replace("{name}", full), dry)
                await _send(sender, cid, "🤖 " + JOIN_WARN_NOT_VERIFIED.format(name=full), dry)
    return passed


def _to_local(sent_at: str) -> str:
    """sent_at(UTC) → 'YYYY-MM-DD HH:MM' 北京时间."""
    from datetime import timedelta, timezone
    try:
        dt = datetime.fromisoformat(sent_at.replace("+00", "+00:00"))
        dt = dt.astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return sent_at[:16]


def _build_profile_text(profile: dict) -> str:
    """从档案构建给 AI 的文本（生成滚动总结用）."""
    lines = ["昵称：" + profile.get("name", "")]
    if profile.get("gender"):
        lines.append("性别：" + profile["gender"])
    if profile.get("signature"):
        lines.append("签名：" + profile["signature"])
    if profile.get("bio"):
        lines.append("简介：" + profile["bio"])
    msgs = profile.get("messages", [])
    if msgs:
        lines.append("发言（" + str(len(msgs)) + "条）：")
        for m in msgs[-30:]:
            lines.append("  - " + m.get("text", "")[:80])
    return "\n".join(lines)


def sync_profiles() -> str:
    """增量同步：拉新消息 → 提取注册人发言 → 更新档案 → 推进游标."""
    cid = _ACTIVE_CID
    state = load_state()
    group_state = state.get(cid) or {}
    since = group_state.get("last_sync_at")

    # 1. 拉增量（或首次全量）
    if since:
        msgs = get_messages_since(cid, since)
    else:
        msgs = get_messages_all(cid, max_count=1000)

    if not msgs:
        return "没有新消息，无需同步～"

    profiles = load_profiles()
    if not profiles:
        return "还没有人注册，无需同步～"

    # 2. 提取注册人发言，追加原文（去重：按 at+text 避免重复追加）+ 更新 last_seen
    updated = 0
    active_uids = []  # 本次有新发言的 uid（用于刷新 bio/last_seen）
    for uid, profile in profiles.items():
        their = [m for m in msgs if m.get("sent_by") == uid and not _is_join_msg(m)]
        if not their:
            continue
        active_uids.append(uid)
        msgs_list = profile.get("messages", [])
        seen = {(x.get("at"), x.get("text")) for x in msgs_list}
        for m in their:
            text = (m.get("content") or {}).get("text", "").strip()
            if not text:
                continue
            at = _to_local(m["sent_at"])
            key = (at, text)
            if key in seen:
                continue
            msgs_list.append({"text": text, "at": at})
            seen.add(key)
        msgs_list.sort(key=lambda x: x.get("at", ""))
        profile["messages"] = msgs_list[-200:]
        profile["last_seen"] = _to_local(their[-1]["sent_at"])  # 最新活跃时间
        updated += 1

    # 2.5 刷新 bio/signature/gender（有发言的人，降频防 captcha）
    for uid in active_uids:
        try:
            u = get_user(cid, uid)
            if u:
                profile = profiles.get(uid)
                if not profile:
                    continue
                if u.get("bio"):
                    profile["bio"] = u["bio"]
                if u.get("quirk"):
                    profile["signature"] = u["quirk"]
                if u.get("gender"):
                    profile["gender"] = str(u["gender"])
        except Exception:
            pass
        time.sleep(0.4)  # 降频

    # 3. 更新滚动总结
    # P12-A：`archive_build` 管的是"档案内容（画像/滚动总结）的自动构建"。
    # 关掉它：**发言记录照常落库**（数据不丢），只是不再自动生成画像/总结
    # —— 省掉每个档案一次 LLM 调用，也不会因为画像写得不好而影响体验。
    for uid, profile in profiles.items():
        if profile.get("messages") and _feat_on(cid, "archive_build"):
            try:
                profile["summary"] = generate_profile_summary(_build_profile_text(profile))
            except Exception:
                pass

    # 4. 保存档案（全局整合，跨群发言都归到同一人的档案）
    save_profiles(profiles)

    # 5. 推进游标
    group_state["last_sync_at"] = msgs[-1]["sent_at"]
    state[cid] = group_state
    save_state(state)

    return "同步完成～更新了 " + str(updated) + " 人的发言档案～"


def sync_all() -> str:
    """对所有群执行增量同步（供 cron --sync 调用）."""
    results = []
    for cid in GROUPS:
        if cid in MUTE_CIDS:  # 静默监控群：不同步档案
            continue
        _set_active_cid(cid)
        try:
            results.append("[" + cid[:8] + "] " + sync_profiles())
        except Exception as e:
            results.append("[" + cid[:8] + "] 同步失败: " + str(e)[:60])
    return "\n".join(results)


async def greet_once(period: str) -> None:
    """发送一次问候到所有群（早/中/晚），用独立 profile 避免和主循环抢锁."""
    sender = Sender(profile_dir="pw_profile_greet")
    try:
        await sender.start()
    except Exception as e:
        log("greet sender init failed: " + str(e)[:80])
        return
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        greeting = generate_greeting(period, today)
    except Exception:
        greeting = ""
    greeting = (greeting or "").strip()
    if not greeting:
        greeting = "姐姐们好呀～"
    for cid in PUSH_CIDS:  # 只在配置了定时推送的群发问候（裳姐姐群、团本群不发）
        ok = await sender.send("🤖 " + greeting, cid)
        if ok:
            # 读回验证：看最近 5 条里有没有 bot 自己刚发的这条问候。
            # 不能只看最后一条——12:00 午安和「骚度排行」定时撞车，排行会插在中间，
            # 若只看 msgs[-1] 会误判「漏发」→ 补发 → 群里收到两遍午安（2026-08-24 实测）。
            await asyncio.sleep(2)
            try:
                msgs = await asyncio.to_thread(get_messages, cid, 5)
                sent = any(
                    is_self(m.get("sent_by"))
                    and greeting[:8] in (m.get("content") or {}).get("text", "")
                    for m in msgs
                )
                if not sent:
                    ok = await sender.send("🤖 " + greeting, cid)
            except Exception:
                pass
        log("greet[" + period + "] " + cid[:8] + ": " + ("✅" if ok else "❌"))
    await sender.close()


async def memory_report_once() -> None:
    """定时记忆汇报（独立 sender，准点执行，机制同问候）."""
    sender = Sender(profile_dir="pw_profile_greet")
    try:
        await sender.start()
    except Exception as e:
        log("memory-report sender init failed: " + str(e)[:80])
        return
    try:
        for cid in PUSH_CIDS:
            await _publish_memory_report(sender, cid, dry=False)
    finally:
        await sender.close()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--interval", type=float, default=0.8)
    ap.add_argument("--sync", action="store_true", help="执行一次增量同步后退出")
    ap.add_argument("--greet", type=str, choices=["morning", "noon", "evening"], help="发送一次问候（morning/noon/evening）")
    ap.add_argument("--memory-report", action="store_true", help="发送一次记忆汇报后退出（定时用）")
    args = ap.parse_args()
    if args.sync:
        print(sync_all())
        return 0
    if args.greet:
        asyncio.run(greet_once(args.greet))
        return 0
    if args.memory_report:
        asyncio.run(memory_report_once())
        return 0
    try:
        asyncio.run(main_loop(args.interval, args.dry, args.once))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
