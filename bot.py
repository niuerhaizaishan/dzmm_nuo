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
    """启动时把热配置注入运行期（P3-17 起实现在 runtime/config_boot.py）。

    cfg=None 在壳层解析为 _CFG（原语义不变）。
    """
    _config_boot.run_apply_config_overrides(cfg if cfg is not None else _CFG)


def _live_structural() -> dict:
    """hotload 的结构性配置快照（P3-17 起实现在 runtime/config_boot.py）。

    只读 bot 运行期镜像（GROUPS/MUTE_CIDS/…）—— 全部调用时注入。
    """
    return _config_boot.live_structural(
        cfg=_CFG, groups=GROUPS, mute_cids=MUTE_CIDS,
        screen_cids=SCREEN_CIDS, push_cids=PUSH_CIDS)


async def hot_reload_check() -> None:
    """主循环每轮的热加载检查（P3-17 起实现在 runtime/config_boot.py）。

    壳保持 async（test_loop_blocking 钉 iscoroutinefunction + 调用点文本）。
    """
    await _config_boot.run_hot_reload_check()


def _log_hotload(rep) -> None:
    """热加载自证行（P3-17 起实现在 runtime/config_boot.py）。"""
    _config_boot.log_hotload(rep)




def migrate_config_file() -> None:
    """旧版配置升级落盘（P3-17 起实现在 runtime/config_boot.py）。

    _FEATURES_DEFAULT_KW 留在 bot（聚合 bot 默认常量），拷贝后注入。
    """
    _config_boot.run_migrate_config_file(default_kw=dict(_FEATURES_DEFAULT_KW))

GROUPS = list(_CFG.groups)
MUTE_CIDS = set(_CFG.mute)
SCREEN_CIDS = set(_CFG.screen)
PUSH_CIDS = list(_CFG.push)
_ACTIVE_CID = GROUPS[0]  # 当前处理的群，主循环每次迭代设置；真源在 runtime/ctx.py


# 涩涩骰子词库

# XP → 场景/体位/附加 偏好（发骚骰子按目标 XP 定制）

from dzmm import get_messages, get_messages_all, get_messages_since, get_user, get_chatroom_name, get_members, remove_member, get_verified, get_private_cid
from dice_game import roll_dice, roll_wild, dice_str, count_point, is_legal_raise, judge_open, parse_call
from brains import generate_reply, generate_summary, generate_profile_summary, generate_person_summary, generate_rank, generate_insult, generate_insult_continue, generate_poem, generate_poem_theme, generate_archive, generate_fortune, generate_greeting, generate_horoscope, generate_recap, generate_trend, generate_defend, generate_scene, generate_scene_continue, generate_scene_end, generate_scene_rewrite, generate_xp, generate_diaojiao, generate_diaojiao_end, generate_perf_enrich, generate_perf_insult, generate_perf_comment, generate_perf_ending, generate_perf_novel, generate_perf_novel_chunked, generate_heat_judge, generate_heat_climax, generate_heat_fasao, generate_story_start, generate_story_step, generate_story_end, generate_vote_options, generate_devil_roast, generate_memory_summary, POEM_STYLES, AT_FRAME_PROMPT, _call_llm
from profiles import get_profile, upsert_profile, load_profiles, save_profiles
from wx_alert import send_weixin
import token_store
import env_config
import state_store                                    # P7：状态存储后端（默认 SQLite）
from runtime import ctx                               # P8 第 4 步：当前群上下文的唯一真源
from runtime.ctx import PRIMARY_CID, captcha_active, set_captcha_active                   # 常驻群 cid 的唯一真源（Sender 默认参数要用）
from runtime import ctx as _ctx              # P3-7：撞验证码旗标真源（别名防局部遮蔽）
from runtime import sender as _sender_mod    # P3-7：发送引擎真身（Sender 类 + 账号通道族）
from runtime.sender import (ACCOUNT_MSG_MAX_CHARS, Sender, SIO_LONG_BUDGET_SEC,
                            SIO_LONG_MAX_TRIES, SIO_LONG_READBACK,
                            TOKEN_HARVEST_SEC, _split_for_account,
                            get_cached_cookie)  # P3-7：同名 re-export，调用点零改动
from runtime import roast as _roast_mod      # P3-8：恶魔吐槽族（main_loop 调）
from runtime.roast import (DEVIL_DAILY_MAX, DEVIL_HOT_MIN_SPEAKERS,
                           DEVIL_SENSITIVE_WORDS, DEVIL_HOT_WINDOW,
                           _maybe_devil_cold_roast, _maybe_devil_roast)  # P3-8：同名 re-export
from runtime import climax as _climax_mod    # P3-9：高潮结算族（main_loop 调）
from runtime.climax import (_build_climax_context, _build_climax_texts,
                            _climax_hits, _judge_pending, _parse_judge,
                            _publish_memory_report)  # P3-9：同名 re-export
from runtime import status as _status      # P3-10：启动状态自证（main_loop 调）
from runtime import roster as _roster      # P3-10：成员名册对账/补筛
from runtime import pushes as _pushes  # P3-11：定时问候/记忆汇报（main_loop 调）
from runtime import profile_sync as _profile_sync  # P3-13：档案同步/一次性模式（扫描、/同步、CLI --sync/--greet/--memory-report）
from runtime import inflight as _inflight    # P3-14：inflight 标记/重启重放
from runtime import turns as _turns         # P3-15：对话轮次上下文（remember/recent/seed）
from runtime import sender_chain as _chain  # P3-16：发送链组装（路由/outbox/放回队列）
from runtime import config_boot as _config_boot  # P3-17：配置生命周期（启动接线/热加载/迁移）
from runtime import reply_gate as _replygate    # P3-18：回复判定（引用/at_only/指令/lag）
from runtime import mainloop as _mainloop      # P3-19：主循环（体搬迁，B=bot 模块桥）
from runtime import rank as _rank        # P3-12：骚度排行（发布/斜杠指令用）
from runtime.obs import log  # P8 第 4 步：搬到 runtime/obs.py
from runtime.identity import self_uids, is_self, self_names, self_names_longest_first, _load_member_names, get_full_name, get_name, is_at_me, mentions_self, NUO_UID, BOT_UID, BOT_NAME, BOT_NAME_SHORT, _name_cache, register_bots, resolve_bot_uids as _resolve_bot_uids, learn_bot_uid, bot_uids  # P8 第 4 步：搬到 runtime/identity.py（P12-H 加多 Bot 身份）
from runtime import joins as _joins  # P3-3-1：入群链路（检测/筛查/迎客/欢迎语）
from runtime.joins import (detect_joins as _detect_joins,
                           is_join_msg as _is_join_msg,
                           room_greet_text as _room_greet_text)  # P3-3-1：同名 re-export，调用点零改动
from runtime import dispatch as _dispatch  # P3-3-2：斜杠指令分派链（main_loop 调 handle_slash）
from runtime.dispatch import (HELP_TEXT, INTRO_TEXT,
                              get_group_name, is_summary_cmd, is_help_cmd,
                              is_devil_cmd, is_intro_cmd, is_guide_cmd, is_sync_cmd,
                              is_register_cmd, _extract_xp, _ph, register_user,
                              is_profile_cmd, _insult_prefix, is_rank_cmd,
                              _live_insult_text, _get_insult_history, _record_insult,
                              archive_query, _insult_resolve,
                              is_insult_cmd, is_continue_cmd,
                              _extract_continue_custom, continue_last, is_rewrite_cmd,
                              rewrite_last, is_dj_command_cmd, is_review_cmd,
                              split_novel, parse_time_range, summarize_chat,
                              _write_text_file, _read_text_file, _handle_review,
                              _continue_allowed, _DICE_ALWAYS_CMDS, _gate_dice,
                              _gate_perf, _gate_story, _gate_vote, _gate_fasao,
                              _CMD_FEATURE_ORDER, _cmd_owned_by_disabled)  # P3-3-2：同名 re-export，调用点零改动
from runtime.dice import _dice_state, _dice_save, _dice_current_player, _dice_send_private, _dice_board_text, _dice_deal_round, _dice_start_cmd, _dice_join_cmd, _dice_exit_cmd, _dice_begin_cmd, _dice_next_round_cmd, _dice_call_cmd, _dice_open_cmd, _dice_quit_cmd, _dice_skip_cmd, _dice_status_text  # P8 第 4 步：搬到 runtime/dice.py
from runtime.text import _get_opencc, _normalize_cn, _simplify, _strip_parenthesis, _is_stop_word, _dedupe_options, _normalize_choice, _extract_deadline_seconds, _squash, _clean_name, _norm_poem, _spice_emoji, _ensure_decor, _humanize_chat, _solicit_tail, _strip_solicit_tail, _CJK_VARIANT, STOP_WORDS, _OPENCC_T2S  # P8 第 4 步：搬到 runtime/text.py
from runtime.members import _extract_msgs, _get_msgs_by_uid, _member_profile_text, _find_in_profiles, _find_user_in_msgs, find_user_by_name, _find_uid_by_name, _gender_label, _gender_of, _random_member_uid, _archive_text_from_profile, _history_lines, _profile_history, HISTORY_MAX  # P8 第 4 步：搬到 runtime/members.py
from runtime.fortune import is_fortune_cmd, is_fortune_ask, fortune_reply, fortune, _fortune_calc, _fortune_score, _fortune_level, _record_fortune, _get_fortune_history, _char_wx, _name_wx, _ganzhi, _relation, FORTUNE_APOLOGY, _GAN, _ZHI, _WX_GAN, _WX_ZHI, _GZ_BASE, _WX_ORDER, _WX_BASE, _WX_LUCK, _WX_YI, _WX_JI, _SHENG, _KE, _CHONG, _SHENGXIAO, _CHAR_WX, _REL_SCORE, _REL_TEXT, _LIUCHONG, _LIUHE, _ZHI_SCORE, _ZHI_PENALTY  # P8 第 4 步：搬到 runtime/fortune.py
from runtime.trend import is_trend_cmd, is_trend_board_cmd, _trend_days, _trend_spark, _trend_chong, _trend_stats, _wd_cn, _md_cn, week_trend, week_trend_board, TREND_CIDS, TREND_DEFAULT_DAYS, TREND_MAX_DAYS  # P8 第 4 步：搬到 runtime/trend.py
from runtime.recap import is_recap_cmd, _recap_intent, _recap_window, _day0, _msg_ts, _fmt_ts, _recap_mentions, _recap_transcript, recap_absent, RECAP_CIDS, RECAP_MAX_MSGS, RECAP_MAX_HOURS, RECAP_COOLDOWN, _is_noise as _recap_is_noise, _ignore_kw as _recap_ignore_kw  # P8 第 4 步：搬到 runtime/recap.py
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
from runtime.diaojiao import is_dj_end_cmd, _dj_target_text, _dj_end_text, is_diaojiao_cmd, start_diaojiao, _check_diaojiao  # P8 第 4 步：搬到 runtime/diaojiao.py（P12-BD a：`DJ_PARTICIPATE_HINT` 不再直接用，参与提示统一由 `start_diaojiao`/`_dj_hint` 走话术表）
from runtime import intro_photo as _intro_photo  # P12-L：自我介绍配图（只发图，不带字）
from runtime import room_layout as _room_layout  # P12-N：改布局/复原 → 发对应的房间图
from runtime import ghost_photo as _ghost_photo  # P12-O：女鬼场景配图
from runtime import room as _room_mod                 # P3-3-3：房间管家回应（main_loop 调 handle_room）
from runtime.room import (ROOM_OPEN_CMDS, is_open_room_cmd, _room_male,
                          _strike_get, _strike_set)  # P3-3-3：同名 re-export，调用点零改动
from runtime import at_chat as _at_chat               # P3-3-4：@ 闲聊全流程（main_loop 调 handle_at_chat）
from runtime.at_chat import (_strip_at, _pending_line, _apply_frame_length,
                             _ref_of, _CHAT_LAST_REPLY, _chat_cooling,
                             _chat_mark_replied, _ctx_mode, _ctx_only_uid,
                             _active_play_hint)  # P3-3-4：同名 re-export，调用点零改动

# DZMM 主站 origin（多线路切换：env_config 从 DZMM_HOST 环境变量读）
ORIGIN = env_config.get_dzmm_origin()


_captcha_notified = {}  # cid -> bool，撞验证码群里说明是否已发（每群一次防刷屏）

# 撞验证码相关文案
# ⚠️ 自称一律写 `{me}` —— 这里是**代码默认值**（默认人设 = 小小糯），发出去之前
# 会过 `personas.fill(text, cid)`：认证群填「小小糯」、玫瑰房填「薇丝」。
# 直接写死名字的话，玫瑰房会冒出一个客人不认识的「小小糯」。
CAPTCHA_NOTICE = ("⚠️ {me}撞上平台验证码啦～查资料、踢人、新人筛查暂时受限，"
                  "聊天收发不受影响哦～风控解除会自动恢复，稍安勿躁～♡")
# P3-3-1：CAPTCHA_JOIN_NOTICE（入群撞码延迟筛查文案）搬去 runtime/joins.py
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


def _log_outbox_status(sender) -> None:
    """周期性把出站队列的状态写进日志（P12-AB）。

    为什么要有：2026-09-26 有一条星象卡发送失败（`sender 返回 False`）后
    就永远躺在 `failed` 里没人知道 —— 日志里只有当时一个 `❌`。
    现在每半小时一行，写明 pending/failed 与**最近一条失败**是什么。
    """
    try:
        failed = int(getattr(sender, "failed_count", lambda: 0)() or 0)
        pending = int(getattr(sender, "pending_count", lambda: 0)() or 0)
    except Exception:                                          # noqa: BLE001
        return
    line = "📤 出站队列：pending={} failed={}".format(pending, failed)
    if failed:
        try:
            last = (getattr(sender, "recent_failed", lambda n=1: [])(1) or [None])[0]
        except Exception:                                      # noqa: BLE001
            last = None
        if last:
            line += "；最近失败 {} {} {}「{}」— {}".format(
                time.strftime("%m-%d %H:%M", time.localtime(last.get("created_at") or 0)),
                str(last.get("chatroom_id") or "")[:8],
                len(str(last.get("text") or "")),
                str(last.get("text") or "")[:30].replace("\n", " "),
                str(last.get("last_error") or "")[:40])
    log(line)


# P3-3-2：_write_text_file 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_read_text_file 已搬 runtime/dispatch.py（import 区同名 re-export）
# ---- 本群滚动上下文（P12-AD） -------------------------------------------------
# 为什么需要：收到的那"一批消息"里**只有新消息**（webhook/sio 推来的），
# 机器人自己发出去的话根本不会回到这条队列里（批处理扫描还显式 `is_self → continue`）。
# 所以 `msgs[-N:]` 拿不到对话历史 —— 2026-09-27 实测的后果是：
# 用户 @ 两句，她答"说两遍，是想让我记住"（当前问题被喂了两遍）、
# 又说"我不太确定你在回应哪句话，那话我还没说"（她看不到自己上一句）。
# 这里维护一份**进程内**的滚动上下文：进来的每句话 + 我们自己发出去的每句话。
# ---------------------------------------------------------------- P12-BE：双边上下文
# P3-15：对话轮次状态真源搬 runtime/turns.py；可变对象**同一对象** re-export ——
# tests 直操 bot._TURNS / bot._GROUP_BOTS 与 runtime 状态是同一份。
_TURNS = _turns._TURNS
_TURN_MAX = _turns._TURN_MAX
_TURN_SEEN = _turns._TURN_SEEN
_TURN_SEEDED = _turns._TURN_SEEDED
_GROUP_BOTS = _turns._GROUP_BOTS


def _remember_turn(cid: str, who: str, text: str, mid: str = None, uid: str = None) -> None:
    """记滚动上下文（P3-15 起实现在 runtime/turns.py）。

    dispatch / at_chat 经这里注入（调用点 `_remember_turn=_remember_turn` 不变）。
    """
    _turns.remember_turn(cid, who, text, mid, uid)


def _platform_api(method: str, params: dict):
    """平台 API 薄封装（P3-15 起实现在 runtime/turns.py）。"""
    return _turns.platform_api(method, params)


def _other_bot_uids(cid: str, ttl: float = 600.0) -> set:
    """本群里别家 bot 的 uid（P3-15 起实现在 runtime/turns.py）。"""
    return _turns.other_bot_uids(cid, ttl)


def _recent_turns(cid: str, n: int = 20, exclude_mid: str = None,
                  only_uid: str = None, drop_bots: bool = True) -> list:
    """取最近 N 条上下文（P3-15 起实现在 runtime/turns.py：注入壳）。

    other_bot_uids/is_self 经壳注入 —— test_ctx_bilateral patch 的是
    `bot._other_bot_uids`，runtime 直调模块内函数会绕开接缝。
    """
    return _turns.recent_turns(cid, n, exclude_mid, only_uid, drop_bots,
                               other_bot_uids=_other_bot_uids, is_self=is_self)


def _seed_turns(cid: str) -> None:
    """第一次用到某群上下文时补平台历史（P3-15 起实现在 runtime/turns.py）。

    get_messages/is_self/get_name 经壳注入（test_chat_transcript patch `bot.*`）；
    read_timeout 是 bot 侧常量（main_loop 也用），同样 kw-only 传入。
    """
    _turns.seed_turns(cid, get_messages=get_messages, is_self=is_self,
                      get_name=get_name, read_timeout=PLATFORM_READ_TIMEOUT)


# P3-3-4：_pending_line 已搬 runtime/at_chat.py

# P3-3-4：_FRAME_LEN_RE / _apply_frame_length 已搬 runtime/at_chat.py

def _welcome_of(cid: str, plain: bool = False) -> str:
    """P12-BD：欢迎语可按群覆盖（话术 `welcome_text` / `welcome_text_plain`）。

    P3-20 起体在 runtime/joins.py：常量经壳**传值**注入 —— test_recovery_ownership
    patch `bot.WELCOME_TEXT` 时壳读 bot 全局，接缝保持。
    """
    return _joins.build_welcome(cid, plain, welcome_text=WELCOME_TEXT,
                                welcome_text_plain=WELCOME_TEXT_PLAIN, phrase_p=p)


# P3-3-4：_ref_of 已搬 runtime/at_chat.py（_quotes_self 走 re-export）

def _quotes_self(m: dict) -> bool:
    """这条消息是不是"右键回复了我说过的那条"（P3-18 起实现在 runtime/reply_gate.py）。

    is_self 经壳注入 —— test_quote_reply patch 的是 `bot.is_self`。
    """
    return _replygate.quotes_self(m, is_self=is_self)


def _quote_reply_on(cid: str) -> bool:
    """"引用她的消息算不算触发"（P3-18 起实现在 runtime/reply_gate.py：注入壳）。

    _feat_on 是闸门唯一入口，经壳注入（main_loop 调用点文本被源码钉点钉住）。
    """
    return _replygate.quote_reply_on(cid, _feat_on=_feat_on)


# P3-3-4：@ 闲聊冷却族（_CHAT_LAST_REPLY/_chat_cooling/_chat_mark_replied）已搬 runtime/at_chat.py

# P3-3-4：_SOLICIT_REDO 已搬 runtime/at_chat.py

def _save_group(cid: str, group_state: dict) -> None:
    """只落**本群**那一行（P12-AA，2026-09-27 体检）。

    ⚠️ 为什么不能再用 `save_state(state)`：`state_store.save_state()` 走的是
    `save_scope("group", 整份映射)` —— **整 scope 重写**。按群并行（P12-I）下
    三个任务共用同一份 `state` 字典，任务 A 手里那份还带着群 B/C 的旧快照，
    写下去就把 B/C 这一轮的新数据盖回旧值（丢更新）。

    `save_scope` 本身只 upsert 传进去的键、不删别的键，所以传 `{cid: 本群}` 就
    恰好等于"只写本群"。语义与原实现一致，只是不再碰其它群。
    """
    try:
        state_store.save_state({cid: group_state or {}})
    except Exception:                                          # noqa: BLE001
        pass




def _set_active_cid(cid: str) -> None:
    """设置「当前正在处理的群」（**当前上下文**的那一份）。

    P8 第 4 步起 `_ACTIVE_CID` 有两个消费者：bot.py 自己和 `runtime/ctx`
    （搬出去的模块从那里读）。**唯一**允许写它的地方就是这里 ——
    镜像与真源一起更新，写点分散就会出现"搬出去的模块读到旧的群"这种隐性错。
    一致性由 tests/test_p8s4_split.py 与部署闸门钉住。

    P12-I（按群并行）：真源是 `runtime/ctx` 里的 ContextVar；这里的
    `_ACTIVE_CID` 只是给外部脚本/测试看的**遗留镜像**（并行时它只反映
    最近一次设置，不可作为判断依据）。读点一律走 `_active_cid()`。
    """
    global _ACTIVE_CID
    _ACTIVE_CID = cid
    ctx.set_active_cid(cid)


def _active_cid() -> str:
    """当前正在处理的群 —— **上下文局部**，按群并行时每个任务看自己那份。

    P12-I 起所有读点都走这里，不要再直接读模块全局 `_ACTIVE_CID`：
    并行后那个全局会被另一个群的任务覆盖（Rose 的房间逻辑读到认证群的 cid）。
    """
    return ctx.get_active_cid() or _ACTIVE_CID


# P12-I：平台读的"这一跳"超时上限。
# `dzmm.api()` 自己是 timeout=15 且**不重试** —— 一次卡住就是 15 秒，
# 而它是在主循环里 await 的 ⇒ 那个群整整 15 秒不干活（日志里几小时就 40 次）。
# 并行之后它只拖累自己那个群，但还是要有个更短的上限：卡住就放弃这一轮，下轮重试。
PLATFORM_READ_TIMEOUT = 8.0


def _load_root_module(name: str):
    """按文件路径加载根目录模块（P3-17 起实现在 runtime/config_boot.py）。"""
    return _config_boot.load_root_module(name)


def message_lag(msgs) -> float:
    """最新群友消息的投递延迟秒数（P3-18 起实现在 runtime/reply_gate.py）。

    main_loop 的调用点被 test_chat_transcript 源码钉住（注意：壳 docstring 里
    不能复述调用点文本，否则源码窗口钉点会先命中这里）。
    """
    return _replygate.calc_message_lag(msgs)




def should_reply(context: list[dict]) -> str:
    """上下文里最近 3 条有没有没处理的 `/` 指令（P3-18 起实现在 runtime/reply_gate.py）。"""
    return _replygate.find_reply_command(context)




# P3-3-4：_strip_at 已搬 runtime/at_chat.py

# P3-3-2：get_group_name 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：is_summary_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：HELP_TEXT 已搬 runtime/dispatch.py（import 区同名 re-export）

# P3-20：欢迎语常量真源搬 runtime/joins.py（re-export 保 bot.WELCOME_TEXT 接缝——
# test_recovery_ownership 会 patch 它、test_rose_weisi_switch 做值对照）
WELCOME_TEXT = _joins.WELCOME_TEXT
WELCOME_TEXT_PLAIN = _joins.WELCOME_TEXT_PLAIN

# P3-3-1：筛查话术常量（TOURIST_KEYWORDS / JOIN_WARN_* / JOIN_DONE_*）搬去 runtime/joins.py

# 运势磕头道歉文案（2026-09-05 赔罪日追加在运势分析后）

# 记忆功能：触发词 + 上限

# P3-3-2：INTRO_TEXT 已搬 runtime/dispatch.py（import 区同名 re-export）

# P3-3-2：is_help_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：is_devil_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：is_intro_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：is_guide_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：is_sync_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：is_register_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_extract_xp 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_ph 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：register_user 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：is_profile_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_insult_prefix 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：is_rank_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# 玫瑰房里**对所有人开放**的指令（客人也能用，不参与"只有 Vv姐姐 能吩咐我"的婉拒）。
# 2026-09-27 屋主要求「开放 /查 指令给所有人」：查档案是"看资料"，不是"吩咐管家做事"，
# 所以它不该被那句婉拒挡住。目前只有查档案一族（`is_profile_cmd` 覆盖的前缀）。
#
# P3-3-3：ROOM_OPEN_CMDS / is_open_room_cmd 已搬 runtime/room.py（import 区同名 re-export）


def _room_cmd_refused(cid: str, cmd: str, sender: str) -> bool:
    """房间群里这条 `/` 指令要不要变成一次"婉拒"（只有主人能下命令）.

    这是房间群对指令的**唯一闸门**（`main_loop` 里调它）：开放的指令（`/查`）放行，
    其余非主人发的一律改成管家的婉拒话术。
    """
    if not cmd or cid not in ROOM_CIDS:
        return False
    if _room_owner(cid, sender):
        return False                        # 主人下命令照常执行
    return not is_open_room_cmd(cmd)


def rank_summary(top: int = 10) -> str:
    """骚度排行（P3-12 起实现在 runtime/rank.py：注入壳）。

    load_state 是 bot 侧状态，调用时 kw-only 传入 → monkeypatch `bot.load_state`
    接缝（conftest.state）保持不变；handle_slash 也经这里注入
    （bot.py 调用点 `rank_summary=rank_summary` 零改动）。
    """
    return _rank.build_rank_summary(top, load_state=load_state)


# ============ 发情值增量（P8 集中管理）============
# 原先 5/8/10/15/20 散落在十余个函数里，改规则要到处找。括号内是原行号，便于对照。


def _rollback_last_id(cid: str, old_last_id: str) -> None:
    """生成失败时回滚 last_id（P3-14 起实现在 runtime/inflight.py：注入壳）。"""
    _inflight.rollback_last_id(cid, old_last_id,
                               load_state=load_state, save_group=_save_group,
                               log=log)


def _mark_inflight(cid: str, mid: str) -> None:
    """记下"这条消息正在处理"（P3-14 起实现在 runtime/inflight.py：注入壳）。

    dispatch / at_chat / room 经这里注入（调用点 `_mark_inflight=_mark_inflight`
    零改动），main_loop 的调用文本也被 test_inflight_recovery 源码钉住。
    """
    _inflight.mark_inflight(cid, mid,
                            load_state=load_state, save_group=_save_group,
                            log=log)


def _clear_inflight(cid: str) -> None:
    """这一轮跑完了，标记清掉（P3-14 起实现在 runtime/inflight.py：注入壳）。"""
    _inflight.clear_inflight(cid, load_state=load_state, save_group=_save_group,
                             log=log)


def _recover_inflight(state: dict, groups: list) -> int:
    """启动时收拾上次被杀掉时正在处理的消息（P3-14 起实现在 runtime/inflight.py）。

    get_messages/is_self/log 经壳注入 —— test_inflight_recovery patch 的是
    `bot.get_messages` / `bot.is_self` / `bot.log`，直连 runtime 会绕开接缝。
    """
    return _inflight.recover_inflight(
        state, groups, load_state=load_state, save_group=_save_group,
        get_messages=get_messages, is_self=is_self, log=log)


# P3-14：INFLIGHT_* 真身搬 runtime/inflight.py（re-export 保 bot.INFLIGHT_KEY 接缝）
INFLIGHT_KEY = _inflight.INFLIGHT_KEY
INFLIGHT_STALE_SEC = _inflight.INFLIGHT_STALE_SEC
INFLIGHT_PROBE_MSGS = _inflight.INFLIGHT_PROBE_MSGS












async def _handle_climax(sender, cid: str, dry: bool, hits: list):
    """处理高潮事件：为每个发情值≥100的成员生成专属色色文字并 @当事人（标注今日/总高潮次数）."""
    gs = (load_state() or {}).get(cid) or {}
    # ⚠️ P12-AA：生成（平台读 + 模型）整段放工作线程；发送留在循环里。
    items = await asyncio.to_thread(_build_climax_texts, cid, hits, gs)
    for _uid, _name, msg in items:
        if dry:
            log("[dry] " + msg[:60])
        else:
            ok = await sender.send("🤖 " + msg, cid)
            log("✅" if ok else "❌")
        await asyncio.sleep(1.0)


async def _publish_rank(sender, cid: str, dry: bool):
    """发布骚度排行（P3-18 起实现在 runtime/rank.py：rank_summary 经壳注入）。"""
    await _rank.publish_rank(sender, cid, dry, rank_summary=rank_summary)




DICE_TIMEOUT_SEC = 180  # 3 分钟超时弃权（代码默认值；运行期读 tuning）


def _rank_hour_set():
    """定时排行发布的整点（P3-18 起实现在 runtime/rank.py）。"""
    return _rank.rank_hour_set()


def profile_summary(query: str) -> str:
    """查资料+发言，返回给 AI 的文本."""
    uid, full = find_user_by_name(query)
    if not uid:
        return ""
    return _member_profile_text(uid, full)


# ---- 中文昵称归一化（繁→简 + 异体字→标准字 + 去空白）----
# 用于昵称模糊匹配，解决"寜/寧"异体字、"繁简混用"、空格差异导致的匹配失败


# opencc t2s 不覆盖的生僻异体字 → 简体标准字


# P3-3-2：_live_insult_text 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_get_insult_history 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_record_insult 已搬 runtime/dispatch.py（import 区同名 re-export）
# 指定诗词形式的关键词 → POEM_STYLES 匹配（长词优先）


# P3-13：档案扫描常量真身搬 runtime/profile_sync.py；这 3 个被
# tests/test_tuning.py 的 tuning 默认对照钉住（`bot.PROFILE_SCAN_*`），re-export 保接缝。
PROFILE_SCAN_HOUR = _profile_sync.PROFILE_SCAN_HOUR
PROFILE_SCAN_MAX_API = _profile_sync.PROFILE_SCAN_MAX_API
PROFILE_NOTICE_MAX = _profile_sync.PROFILE_NOTICE_MAX


def _daily_profile_scan() -> list:
    """每天一次全群档案扫描（P3-13 起实现在 runtime/profile_sync.py：注入壳）。

    只留**注入壳**：群集合是 bot 侧状态、_feat_on 是唯一入口（log+放行回退），
    调用时 kw-only 传入 → monkeypatch `bot._feat_on` 等既有接缝保持不变。
    """
    return _profile_sync.daily_profile_scan(
        groups=GROUPS, mute_cids=MUTE_CIDS, room_cids=ROOM_CIDS,
        primary_cid=PRIMARY_CID, _feat_on=_feat_on)


# P3-3-2：archive_query 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_insult_resolve 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_insult_material / _insult_target_uid 已随搬迁判定为死代码删除（2026-10-01）
def defend_nuo(attacker_uid: str) -> str:
    """维护糯糯权威，痛斥发指令者."""
    full = get_full_name(attacker_uid)
    profile = get_profile(attacker_uid)
    attacker_text = _archive_text_from_profile(profile) if profile else "昵称：" + full
    return generate_defend(attacker_text)


# P3-3-2：is_insult_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# ---------- 运势：五行命理算分（确定性，非随机）----------

# 五行 → 命基底分（天生禀赋）
# 五行 → 幸运色/数字/方位
# 五行 → 当日宜/忌
# 字 → 五行（常见字，按偏旁字义；查不到用编码兜底）


# 本命地支 × 当日地支：六冲 = 明确的凶日信号，六合 = 顺遂（让「凶」真的算得出来）




# P3-3-2：is_continue_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_extract_continue_custom 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：continue_last 已搬 runtime/dispatch.py（import 区同名 re-export）
# ===== 发骚状态机（多轮互动 + 屏蔽 + 上下文） =====


# P3-3-2：is_rewrite_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：rewrite_last 已搬 runtime/dispatch.py（import 区同名 re-export）
# ===== 调教功能 =====


# P3-3-2：is_dj_command_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# 调教参与提醒（每轮命令后附带，群友可 /命令 xxx 或直接 /xxx 参与）


# ===== 公演功能 =====


# P3-3-2：is_review_cmd 已搬 runtime/dispatch.py（import 区同名 re-export）
# ============ 投票功能（/投票）============

# 恶魔吐槽配置（自主毒舌，对事也对人）
DEVIL_BASE_PROB = 0.03       # 普通闲聊基础概率
DEVIL_SENSITIVE_PROB = 0.10  # 命中敏感词概率（封顶）
DEVIL_HOT_PROB = 0.08        # 多人热闹概率
DEVIL_COOLDOWN = 10 * 60     # 冷却 10 分钟
DEVIL_COLD_THRESHOLD = 2 * 60 * 60   # 冷清阈值：2 小时没人说话后首次 roll
DEVIL_COLD_PROB = 0.3            # 冷场吐槽概率：每次 roll 30%（不中也更新周期标记）
DEVIL_COLD_COOLDOWN = 30 * 60    # 冷场重试周期：30 分钟（无论吐没吐都重新计，无需有人说话）
DEVIL_COLD_QUIET_HOUR = 8        # 深夜静默：0-8 点不触发冷场吐槽


# ============ 剧情游戏（/剧情）============


# P3-3-4：_active_play_hint 已搬 runtime/at_chat.py

# P3-3-2：split_novel 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：parse_time_range 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：summarize_chat 已搬 runtime/dispatch.py（import 区同名 re-export）
# ===== 公演 handler =====


async def _scheduled_pushes(sender, dry: bool) -> None:
    """定时问候（早/中/晚）+ 记忆汇报（P3-11 起实现在 runtime/pushes.py：注入壳）。

    只留**注入壳**：PUSH_CIDS / load_state / _save_group / _feat_on 都是 bot 侧
    状态与唯一入口（包装器带"log+放行"回退），调用时 kw-only 传入
    → monkeypatch `bot.*` 的既有接缝（test_feature_registry、conftest.state）不变。
    """
    await _pushes.scheduled_pushes(
        sender, dry,
        push_cids=PUSH_CIDS, load_state=load_state, save_group=_save_group,
        _feat_on=_feat_on)


# P3-3-2：_handle_review 已搬 runtime/dispatch.py（import 区同名 re-export）
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
    """env 真值判定（P3-16 起实现在 runtime/sender_chain.py）。"""
    return _chain.truthy_env(name, default)




def _outbox_enabled() -> bool:
    """DZMM_OUTBOX=off/0/no/false 可退回直发（回滚开关）。默认开。
    （P3-16 起实现在 runtime/sender_chain.py。）"""
    return _chain.outbox_enabled()


def _is_other_bot_noise(cid: str, text: str) -> bool:
    """这条消息是不是"别家 bot 的游戏播报"（**触发层**用，2026-10-02）。

    P12-AK 那套过滤（`recap.ignore_kw` 关键词 + "本群不接 `/` 指令时 `/xxx` 也算噪音"）
    原先**只保护 recap 的输入** —— 触发层没有它。代价（茶水间实测 09:21:56）：

        👋 [4fc4a2fd] @消息: 【记忆考核·第 1 级】请记住：mTrOE
        💬 🤖 记好啦～小小糯会记住的～♡

    别家 bot 的开场白里那句"**请记住**"命中了 `memory.MEMORY_TRIGGERS` 里的裸"记住"，
    于是被当成一条 `/记住` 指令，`mTrOE` 被存进了记忆库。

    判定失败一律返回 False（= 不是噪音）：宁可多回一句，也不能把正常消息吃掉。
    """
    try:
        return _recap_is_noise(text or "", _recap_ignore_kw(cid), cid)
    except Exception:                                          # noqa: BLE001
        return False


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


# P3-3-2：_continue_allowed 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_DICE_ALWAYS_CMDS 已搬 runtime/dispatch.py（import 区同名 re-export）


# P3-3-2：_gate_dice 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_gate_perf 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_gate_story 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_gate_vote 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_gate_fasao 已搬 runtime/dispatch.py（import 区同名 re-export）
# P3-3-2：_CMD_FEATURE_ORDER 已搬 runtime/dispatch.py（import 区同名 re-export）


# P3-3-2：_cmd_owned_by_disabled 已搬 runtime/dispatch.py（import 区同名 re-export）
def _log_route(event: str, cid: str, detail: str = "") -> None:
    """路由审计（P3-16 起实现在 runtime/sender_chain.py）。"""
    _chain.log_route(event, cid, detail)


def _bot_senders() -> dict:
    """按配置里的 Bot 注册表造发送器（P3-16 起实现在 runtime/sender_chain.py）。

    cfg/origin/DzmmBotSender 类经壳注入 —— test_bot_routing_chain patch 的是
    `bot.DzmmBotSender`。
    """
    return _chain.bot_senders(cfg=_CFG, origin=ORIGIN,
                              dzmm_bot_sender_cls=DzmmBotSender)


def _wrap_with_bot_routing(account):
    """官方 Bot 群路由（P3-16 起实现在 runtime/sender_chain.py：注入壳）。"""
    return _chain.wrap_with_bot_routing(
        account, cfg=_CFG, origin=ORIGIN,
        dzmm_bot_sender_cls=DzmmBotSender,
        runtime_import_ok=_RUNTIME_IMPORT_OK)


def _log_runtime_status() -> None:
    """启动时打印各层运行时状态（P3-10 起实现在 runtime/status.py）。

    只留**注入壳**：群集合 / 配置 / 各注册表都是 bot 侧状态，调用时 kw-only 传入
    → monkeypatch `bot.*` 的既有接缝保持不变。
    """
    _status.log_runtime_status(
        cfg=_CFG, groups=GROUPS, mute_cids=MUTE_CIDS,
        screen_cids=SCREEN_CIDS, push_cids=PUSH_CIDS,
        features=_features, personas=_personas, tuning=_tuning,
        bot_uids=bot_uids, config_personas=config_personas)


def _wrap_with_outbox(sender, dry: bool):
    """组装发送链：OutboxSender( RoutingSender( 账号 ) )（P3-16 起实现在
    runtime/sender_chain.py：注入壳）。

    dzmm_bot_sender_cls / runtime_import_ok 经壳注入（test_bot_routing_chain
    patch `bot.DzmmBotSender` / `bot._RUNTIME_IMPORT_OK`）；_run_all 的调用点
    被 test_parallel_groups 源码钉住，文本不变。
    """
    return _chain.wrap_with_outbox(
        sender, dry, cfg=_CFG, origin=ORIGIN,
        dzmm_bot_sender_cls=DzmmBotSender,
        runtime_import_ok=_RUNTIME_IMPORT_OK,
        runtime_import_err=_RUNTIME_IMPORT_ERR)




def _pick_leftover(cid, cmd, cmd_msg, at_msg, at_msg_obj, room_obj, already=None):
    """这一轮没轮到、放回队列的消息（P3-16 起实现在 runtime/sender_chain.py）。

    main_loop 的调用点被 test_leftover_requeue/test_quote_reply 源码钉住。
    """
    return _chain.pick_leftover(cid, cmd, cmd_msg, at_msg, at_msg_obj,
                                room_obj, already)


# P3-16：REQUEUE_MAX 真身搬 runtime/sender_chain.py（re-export 保 bot.REQUEUE_MAX 接缝）
REQUEUE_MAX = _chain.REQUEUE_MAX


async def main_loop(interval: float, dry: bool, once: bool, *,
                    groups: list = None, sender, state: dict,
                    subscriber, is_primary: bool = True):
    """主循环（P3-19 起体在 runtime/mainloop.py：bot 模块桥 B 逐字搬迁）。

    体里所有 bot 侧名字经 `B.` 运行期属性查找 → monkeypatch `bot.X` 的
    既有接缝与搬迁前完全一致；签名保持（test_parallel_groups 钉
    sender/state/subscriber 无默认值、test_loop_blocking 钉协程 + to_thread）。
    """
    import sys
    # ⚠️ 真身是 run_main_loop（test_p8s4 禁止与 bot 壳同名）——线上曾因写成
    # _mainloop.main_loop 崩溃循环（本地测试只 import/getsource 没真跑主循环，
    # 2026-10-01 部署事故）。改完必须跑一次真调用冒烟再上线。
    return await _mainloop.run_main_loop(
        interval, dry, once, groups=groups, sender=sender, state=state,
        subscriber=subscriber, is_primary=is_primary, B=sys.modules[__name__])


def _run_all(interval: float, dry: bool, once: bool):
    """建唯一一份共享资源再起主循环（P3-19 起体在 runtime/mainloop.py）。"""
    import sys
    return _mainloop.run_all(interval, dry, once, B=sys.modules[__name__])


def _parallel_groups_on() -> bool:
    """P12-I：按群并行（进程级开关，改它要重启；默认关 = 老行为）。

    为什么不用 tuning：它决定**进程结构**（起几个主循环实例），
    运行期改不了；放 env 里更诚实（改了就重启）。
    """
    return str(os.environ.get("DZMM_PARALLEL_GROUPS") or "").strip().lower() \
        in ("1", "on", "true", "yes", "y")



# ---------------------------------------------------------------- 房间群（独立人设 + 屋主权限）


# ---------------------------------------------------------------- 星座运势（Rose 专属）

# 一级：全称「X座」—— 任何时候都认（天平座是常见错写，带"座"所以安全）
# 二级：简称 —— 只有同句出现星座意图词时才认
# （已删掉"牧羊/室女/人马/山羊/宝瓶"这些非标准别名：牧羊犬、人马、山羊本身就有歧义）
# 句首「我+座名」也算自报星座（"我天秤"、"我水瓶的"）
# 注意必须锚定句首：否则"我今天买了个水瓶"会被误判


# ---------------------------------------------------------------- 「我不在的时候」回顾（仅认证群）


# ---------------------------------------------------------------- 过去 N 天运势趋势（认证群）


# P3-3-3：_room_male 已搬 runtime/room.py（import 区同名 re-export）


# P3-3-1：room_greet_text 搬去 runtime/joins.py（上面已 re-export 为 _room_greet_text）


# P3-3-3：_strike_get / _strike_set 已搬 runtime/room.py（import 区同名 re-export）


# P3-3-1：is_join_msg / detect_joins 自 runtime/joins.py re-export（见文件头 import）


async def _send(sender, cid: str, text: str, dry: bool) -> bool:
    """发送消息（dry 模式只 log）。"""
    if dry:
        log("[dry] " + text[:60])
        return True
    ok = await sender.send(text, cid)
    log("✅" if ok else "❌")
    return ok


# P3-3-1：kick_and_notify / add_pending_screen / notify_join_delayed 搬去 runtime/joins.py


# P3-10：KNOWN_MEMBERS_KEY 真身住 runtime/roster.py（re-export 保 bot.KNOWN_MEMBERS_KEY 接缝）
KNOWN_MEMBERS_KEY = _roster.KNOWN_MEMBERS_KEY


async def _reconcile_members(sender, cid: str, dry: bool) -> list:
    """P12-BJ 成员快照对账（P3-10 起实现在 runtime/roster.py：注入壳）。

    筛查链 / 发送 / 欢迎话术 / 昵称全部调用时注入 → monkeypatch `bot._handle_joins` /
    `bot._send` / `bot.is_self` 等接缝（test_recovery_ownership）保持不变。
    """
    return await _roster.reconcile_members(
        sender, cid, dry,
        handle_joins=_handle_joins, send=_send, welcome_of=_welcome_of,
        personas=_personas, get_full_name=get_full_name, is_self=is_self)


async def _rescreen_pending(sender, cid: str, dry: bool) -> None:
    """风控解除后补筛查延迟新成员（P3-10 起实现在 runtime/roster.py：注入壳）。"""
    await _roster.rescreen_pending(
        sender, cid, dry,
        handle_joins=_handle_joins, send=_send, welcome_of=_welcome_of,
        personas=_personas, get_full_name=get_full_name,
        load_state=load_state, save_group=_save_group)


async def _handle_joins(sender, cid: str, new_uids: list, dry: bool) -> list:
    """新成员筛查（P3-3-1 起实现在 runtime/joins.py：`joins.screen_members`）。

    这里**只留注入壳**，不搬的两样都是 bot 侧状态：
      · welcome_of     —— 话术表读口（WELCOME_TEXT 常量与话术覆盖都在本文件）；
      · captcha_active —— 撞验证码旗标（P3-7 起真源在 runtime/ctx.py，写点走 set_captcha_active）。
    main_loop / 成员对账 / 补筛都经此调用 —— monkeypatch `bot._handle_joins`
    的既有接缝（test_recovery_ownership）保持不变。
    """
    return await _joins.screen_members(sender, cid, new_uids, dry,
                                       welcome_of=_welcome_of,
                                       captcha_active=_ctx.captcha_active)


def sync_profiles() -> str:
    """增量同步档案（P3-13 起实现在 runtime/profile_sync.py：注入壳）。

    dispatch 的 `/同步` 经这里注入（调用点 `sync_profiles=sync_profiles` 零改动）。
    """
    return _profile_sync.run_sync_profiles(
        _feat_on=_feat_on, _save_group=_save_group, load_state=load_state)


def sync_all() -> str:
    """对所有群执行增量同步（P3-13 起实现在 runtime/profile_sync.py：注入壳）。"""
    return _profile_sync.run_sync_all(
        groups=GROUPS, mute_cids=MUTE_CIDS, sync_profiles=sync_profiles)


async def greet_once(period: str) -> None:
    """发送一次问候到所有群（P3-13 起实现在 runtime/profile_sync.py：注入壳）。"""
    await _profile_sync.run_greet_once(period, groups=GROUPS, push_cids=PUSH_CIDS)


async def memory_report_once() -> None:
    """定时记忆汇报（P3-13 起实现在 runtime/profile_sync.py：注入壳）。"""
    await _profile_sync.run_memory_report_once(groups=GROUPS, push_cids=PUSH_CIDS)


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
        asyncio.run(_run_all(args.interval, args.dry, args.once))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
