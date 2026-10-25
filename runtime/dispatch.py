# -*- coding: utf-8 -*-
"""斜杠指令分派链（P3 Step 3-2，2026-10-01 自 bot.py 原样抽出）。

main_loop 收到一条 `/` 指令之后的所有事都在这：从「吹牛骰子命令（优先）」到
兜底聊天（generate_reply），再到发送尾（strip、🤖 前缀、_remember_turn、
/自我介绍 补立绘、空回复 raise）。分支顺序就是行为（关掉的功能靠
`_CMD_FEATURE_ORDER` 归属表静默），**不许调整 elif 顺序**。

**原样搬入**的纪律与 P8 第 4 步 / P3-3-1 一致：逻辑逐字不变，只动「住在哪」。
两处不得不动的：
    `old_last_id` 那行移回调用方 —— 回滚仍由 main_loop 外层 except 做（P12-BC）；
    `_feat_on` → `_gate` + `_feat_on = _gate`
              （调用点保持 `_feat_on(群, "键")` 的字面形态 ——
               test_feature_registry 按这个前缀静态扫描接线，赋值别名即可命中）。

bot 侧依赖按参数注入（本模块**不许 import bot**，见 p8s4 守卫）：
    _save_group / _mark_inflight / _remember_turn / rank_summary / sync_profiles
    以及 main_loop 的形参 state —— 调用点读的是当前全局，既有 monkeypatch
    bot.X 的测试接缝不受影响。

结构（自上而下）：常量 → 匹配器 → 业务函数（注册/羞辱/续写/总结/公演审核）→
归属表 → `handle_slash`（**必须放最后一个函数**：test_feature_registry 用
「吹牛骰子命令」锚点提取到文件尾来核对归属表顺序）。
"""
import asyncio
import json
import re
import time
from datetime import datetime

import personas as _personas
from personas import p

from runtime import ctx
from runtime import features as _features
from runtime.dice import (_dice_begin_cmd, _dice_call_cmd, _dice_exit_cmd,
                          _dice_join_cmd, _dice_next_round_cmd, _dice_open_cmd,
                          _dice_quit_cmd, _dice_send_private, _dice_start_cmd,
                          _dice_state, _dice_status_text)
from runtime.diaojiao import (_dj_end_text, _dj_target_text, is_diaojiao_cmd,
                              is_dj_end_cmd, start_diaojiao)
from runtime.fasao import end_fasao, is_fasao_cmd, is_fasao_end_cmd, is_scene_cmd, scene
from runtime.fortune import fortune, is_fortune_cmd
from runtime.group_state import (_get_dj_state, _get_fasao_state, _get_last_action,
                                 _get_memory, _get_perf_state, _get_story_state,
                                 _get_vote_state, _remember, _set_dj_state,
                                 _set_fasao_state, _set_last_action, _set_perf_state)
from runtime.heat import _add_heat
from runtime.identity import _load_member_names, get_full_name, get_name, is_self
from runtime import intro_photo as _intro_photo
from runtime.members import (_archive_text_from_profile, _find_in_profiles,
                             _find_uid_by_name, _find_user_in_msgs, _gender_of,
                             _get_msgs_by_uid, _history_lines,
                             _member_profile_text, _random_member_uid)
from runtime.memory import (_append_memory_ref, _refresh_profile_latest,
                            is_recall_cmd, is_remember_cmd)
from runtime.novel import _extract_novel_title, _group_name_cache, _novel_save_path
from runtime.obs import log
from runtime import dream as _dream
from runtime import office as _office
from runtime import personal_story as _personal_story
from runtime import personal_portrait as _personal_portrait
from runtime import personal_letter as _personal_letter
from runtime import rose_moderation as _rose_moderation
from runtime import gallery as _gallery
from runtime.haptics import bot_commands as _haptics_bot
from runtime.perf import (OWNER_UID, _get_perf_content, _handle_perf_start,
                          _perf_end_bg, _perf_m_name, _perf_send,
                          is_perf_end_cmd, is_perf_start_cmd)
from runtime.poem import is_poem_cmd, poem
from runtime.recap import is_recap_cmd, recap_absent
from runtime.absent import absent_reply, is_absent_cmd
from runtime.story import (_finish_story, _record_story_vote,
                           is_story_end_cmd, is_story_start_cmd, start_story)
from runtime.text import _is_stop_word, _spice_emoji, _strip_parenthesis
from runtime.trend import is_trend_board_cmd, is_trend_cmd, week_trend, week_trend_board
from runtime.tuning import get as _tune
from runtime.vote import (_finish_vote, _record_vote, is_vote_choice_cmd,
                          is_vote_end_cmd, is_vote_start_cmd, start_vote)

from brains import (generate_archive, generate_diaojiao, generate_insult,
                    generate_insult_continue, generate_perf_novel_chunked,
                    generate_profile_summary, generate_reply, generate_scene_continue,
                    generate_scene_rewrite, generate_summary, generate_xp)
from dice_game import dice_str, parse_call
from dzmm import get_chatroom_name, get_messages_all, get_user
from profiles import get_profile, load_profiles, upsert_profile
from wx_alert import send_weixin


# ---------------------------------------------------------------- 基础件

def _gate(cid: str, key: str) -> bool:
    """功能开关的安全封装 —— 与 bot._feat_on 同一份语义（fail-open + 记日志）。

    本模块的调用点统一写 `_feat_on(群, "键")`（下面有赋值别名），
    让 test_feature_registry 的静态扫描把这里的接线也数进去。
    """
    try:
        return _features.on(cid or "", key)
    except Exception as e:                                     # noqa: BLE001
        log("features: 判定失败 key=" + str(key) + " " + str(e)[:60])
        return True


_feat_on = _gate  # 字面别名：调用点写 `_feat_on(...)`（原因见模块 docstring）


def _current_cid() -> str:
    """当前正在处理的群 —— 与 bot._active_cid 同一份语义（真源在 runtime/ctx.py）。

    ctx.get_active_cid() 的 ContextVar 带 PRIMARY_CID 默认值、永不返回空，
    所以不需要 bot 侧那个 `or _ACTIVE_CID` 的遗留兜底。
    （名字不用 `_active_cid`：p8s4 守卫禁止 bot 与 runtime def 同名 ——
    与 joins.py 的 `_gate`/`_feat_on` 同一先例，下面有赋值别名。）
    """
    return ctx.get_active_cid()


_active_cid = _current_cid  # 字面别名：搬入的函数体逐字引用 `_active_cid()`
# ---------------------------------------------------------------- 同步小工具（P12-AA）

def _write_text_file(path: str, text: str) -> None:
    """写文本文件（P12-AA：给 `asyncio.to_thread` 用的同步小工具）。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read_text_file(path: str) -> str:
    """读文本文件，读不到返回空串（P12-AA：同上，别在事件循环里读盘）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:                                          # noqa: BLE001
        return ""


def get_group_name() -> str:
    """查询群名（5分钟缓存，按群隔离）."""
    cid = _active_cid()
    cache = _group_name_cache.get(cid, {"name": "", "ts": 0.0})
    if time.time() - cache["ts"] < 300 and cache["name"]:
        return cache["name"]
    cache["name"] = get_chatroom_name(cid)
    cache["ts"] = time.time()
    _group_name_cache[cid] = cache
    return cache["name"]


# ---------------------------------------------------------------- 匹配器与常量

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


# ---------------------------------------------------------------- 注册 / 查档案

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


def _ph(cid: str, key: str, fallback: str = "", **kw) -> str:
    """取本群人设的固定话术；没配就退回代码里的兜底句。

    P12-Y：发骚/调教/注册这些**提示语**原来硬编码在流程里，全是小小糯的软萌口吻
    （"…哦～""…啦～"），注册那几句甚至写着"糯糯姐"——落到若瑾身上名字和口吻都不对。
    统一走这里之后，人设话术表能一句话换掉（页面可改、热加载 1 秒生效）。
    """
    try:
        return p(key, cid, **kw) or fallback
    except Exception:                                          # noqa: BLE001
        return fallback


def register_user(query: str, sender_uid: str) -> str:
    """注册用户，返回结果文本."""
    msgs = get_messages_all(_active_cid())  # 拉一次，找用户+发言都复用
    if query:
        # 代注册：只允许糯糯（群主）
        if sender_uid != OWNER_UID:
            return _ph(_active_cid(), "reg_self_only", "只有糯糯姐才能帮别人注册哦～你自己发 /注册 就可以啦～")
        uid, full = _find_user_in_msgs(query, msgs)
        if not uid:
            return _ph(_active_cid(), "reg_not_found", "没找到这个人呢，注册不了～")
    else:
        uid = sender_uid
        full = get_full_name(uid)

    # 已注册则幂等：不重复建档，直接提示
    existing = get_profile(uid)
    if existing:
        return _ph(_active_cid(), "reg_exists",
                   "✅ " + full + " 已经注册过啦～不用重复注册哦，"
                   "发 /查 " + full + " 就能看档案啦～", who=full)

    u = get_user(_active_cid(), uid)
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
        return _ph(_active_cid(), "reg_ok",
                   "✅ " + full + " 注册成功啦～已经记进档案，"
                   "以后查" + full + "会更丰富哦～", who=full)
    return _ph(_active_cid(), "reg_fail", "注册失败，存档出了点问题～")


def is_profile_cmd(cmd: str) -> str:
    """识别查人指令，返回目标昵称（空串表示不是）."""
    for prefix in ["/查", "/whois", "/资料"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return ""


def _insult_prefix(cid: str, target_name: str) -> str:
    """羞辱这个目标时，要在消息**最最前面**加的内容（没配就返回空串）.

    屋主 2026-09-27 指定：羞辱「工具熊」时消息最前面要加 `/talk`。
    做成**话术**（人设页可改、热加载生效），而不是写死在代码里：
      - `insult_prefix_who`  ：目标昵称关键词，逗号/顿号分隔，命中即加；空 = 不启用
      - `insult_prefix_text` ：要加的内容（如 `/talk`）
    """
    who = str(p("insult_prefix_who", cid) or "").strip()
    txt = str(p("insult_prefix_text", cid) or "").strip()
    if not who or not txt:
        return ""
    names = [x.strip() for x in who.replace("，", ",").replace("、", ",").split(",") if x.strip()]
    name = str(target_name or "")
    return txt if any(n in name for n in names) else ""


def is_rank_cmd(cmd: str) -> bool:
    """识别骚度排行指令."""
    keywords = ["骚度", "排行", "骚榜", "骚排名", "骚气", "谁最骚", "骚王"]
    return any(k in cmd for k in keywords)


# ---------------------------------------------------------------- 羞辱家族

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


def archive_query(query: str):
    """查询某人，返回 (文本, 是否已注册). 已注册读档案（零 API），未注册实时查.

    P12-CC（2026-10-13）：未注册路径原先**只在最近 200 条消息的发送者里找人**——
    rose 两个群 295/445 人、消息窗口只够到最近一小撮人（实测 200 条里才 14 个
    发送者），潜水几天的人必然"查不到这个人"。现在先拉**全群成员表**按名字匹配
    （`_find_uid_by_name`：精确→简化→前缀→子串，单字不模糊），命中后照样取
    最近发言拼卡片；消息窗口匹配降级为成员表拉取失败时的兜底。
    """
    uid, profile = _find_in_profiles(query)
    if uid:
        _refresh_profile_latest(uid, profile)
        return _archive_text_from_profile(profile), True
    # 未注册：先刷本群成员昵称缓存，再按成员表找（覆盖全群，含最近没发言的人）
    msgs = get_messages_all(_active_cid())
    roster_n = _load_member_names(_active_cid())
    uid2 = _find_uid_by_name(query)
    if uid2:
        return _member_profile_text(uid2, get_full_name(uid2), msgs), False
    # 成员表没找到（API 抖动 / 名字对不上）→ 退回消息窗口匹配
    uid3, full = _find_user_in_msgs(query, msgs)
    if not uid3:
        # P12-CC：miss 要留痕 —— 一次日志分辨全部假设：
        #   active_cid 错（= bd7fe6a4）/ 消息窗口空（msgs=0）/
        #   成员表没拉全（roster 很小）/ 名字对不上（roster 正常仍 miss）
        from runtime.identity import _name_cache as _nc
        log("🔍 [" + _active_cid()[:8] + "] /查 未命中: " + str(query)[:24]
            + " | msgs=" + str(len(msgs)) + " roster=" + str(roster_n)
            + " cache=" + str(len(_nc)))
        return "", False
    return _member_profile_text(uid3, full, msgs), False


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
    msgs = get_messages_all(_active_cid())
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


def is_insult_cmd(cmd: str) -> str:
    """识别羞辱指令，返回目标昵称（空串表示羞辱发指令的人自己）."""
    for prefix in ["/羞辱", "/骂", "/diss", "/损", "/吐槽"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None  # None 表示不是羞辱指令


# ---------------------------------------------------------------- 续写 / 重写

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
            return _ph(cid, "cont_no_content", "还没有发骚内容可以继续哦～先 /发骚 试试～")
        try:
            # combo 去括号再传给续写 prompt，避免签名里的「裳姐姐」串角色
            combo_clean = _strip_parenthesis(fs.get("combo", ""))
            text = generate_scene_continue(combo_clean, history, custom,
                                           max_tokens=int(_tune("scene.continue_max_tokens", cid)))
        except Exception:
            text = ""
        if not (text or "").strip():
            # 续写失败（超时/空），重试一次
            try:
                combo_clean = _strip_parenthesis(fs.get("combo", ""))
                text = generate_scene_continue(combo_clean, history, custom,
                                               max_tokens=int(_tune("scene.continue_max_tokens", cid)))
            except Exception:
                text = ""
        text = (text or "").strip()
        if not text:
            return _ph(cid, "cont_fail", "续写失败啦，换个指令试试～")
        text = _spice_emoji(text, p("spice_emoji", cid))      # P12-X：续写也撒表情
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
        return _ph(cid, "cont_no_action", "还没有上一段可以继续哦～先 /发骚 或 /羞辱 某人试试～")
    atype = action.get("type")
    try:
        if atype == "insult":
            text = generate_insult_continue(action.get("material", ""), action.get("output", ""), custom)
        else:
            return _ph(cid, "cont_bad_type", "上一段不是可以继续的内容哦～")
    except Exception:
        return _ph(cid, "cont_fail", "续写失败啦，换个指令试试～")
    text = (text or "").strip()
    if not text:
        return _ph(cid, "cont_fail", "续写失败啦，换个指令试试～")
    text = _spice_emoji(text, p("spice_emoji", cid))          # P12-X：羞辱续写也撒表情
    # 续写加分（和目标原指令一致：羞辱 +15）
    target_uid = action.get("uid")
    if target_uid:
        _add_heat(cid, target_uid, _tune("heat.insult_target", cid))
    if sender_uid and sender_uid != target_uid:
        _add_heat(cid, sender_uid, _tune("heat.insult_sender", cid))
    action["output"] = text
    _set_last_action(cid, action)
    return text


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
        return _ph(cid, "rewrite_idle", "现在没有进行中的发骚哦～先 /发骚 试试～")
    history = fs.get("history") or []
    if not history:
        _set_fasao_state(cid, {"active": False})
        return _ph(cid, "rewrite_no_content", "还没有发骚内容可以重写哦～先 /发骚 试试～")
    direction = is_rewrite_cmd(cmd) or ""
    try:
        text = generate_scene_rewrite(_strip_parenthesis(fs.get("combo", "")), history[:-1], history[-1], direction,
                                      max_tokens=int(_tune("scene.max_tokens", cid)))
    except Exception:
        text = ""
    text = (text or "").strip()
    if not text:
        return _ph(cid, "rewrite_fail", "重写失败啦，再试一次～")
    text = _spice_emoji(text, p("spice_emoji", cid))          # P12-X：重写也撒表情
    history[-1] = text
    fs["history"] = history
    _set_fasao_state(cid, fs)
    _set_last_action(cid, {"type": "scene", "uid": fs.get("uid"), "full": fs.get("full", ""), "combo": fs.get("combo", ""), "output": text})
    return text


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
            or _dream.command_payload(cmd) is not None
            or _personal_story.command_payload(cmd) is not None
            or _personal_portrait.command_payload(cmd) is not None
            or _personal_letter.command_payload(cmd) is not None
            or is_poem_cmd(cmd) is not None
            or is_continue_cmd(cmd) or is_rewrite_cmd(cmd) is not None or is_perf_start_cmd(cmd)
            or is_perf_end_cmd(cmd) or is_dj_end_cmd(cmd) or is_fasao_end_cmd(cmd)
            or is_diaojiao_cmd(cmd) is not None
            or is_story_start_cmd(cmd) or is_story_end_cmd(cmd)
            or is_vote_choice_cmd(cmd) is not None
            or is_vote_start_cmd(cmd)
            or is_summary_cmd(cmd) or is_devil_cmd(cmd) is not None
            or is_remember_cmd(cmd) is not None or is_recall_cmd(cmd)
            or is_absent_cmd(cmd) is not None):
        return None
    return body


def is_review_cmd(cmd: str):
    """审核命令：返回 'approve'/'reject'/'ignore' 或 None."""
    if cmd.startswith("/同意"):
        return "approve"
    if cmd.startswith("/退回"):
        return "reject"
    if cmd.startswith("/忽略"):
        return "ignore"
    return None


# ---------------------------------------------------------------- 总结 / 小说分段

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
    msgs = get_messages_all(_active_cid())
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


# ---------------------------------------------------------------- 公演审核

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
        # P12-AA：写文件与告警 webhook 都是**阻塞**的（send_weixin 最长 ~45 秒：
        # requests timeout=15 × retries=2），原先直接跑在事件循环上，会把整个群卡住。
        await asyncio.to_thread(_write_text_file, path, novel)
        perf.update({"novel_path": path, "novel_title": _extract_novel_title(novel),
                     "review_status": "pending", "review_notified_ts": time.time()})
        _set_perf_state(cid, perf)
        await _perf_send(sender, "已重新生成《" + perf["novel_title"] + "》，请糯糯姐再审核～", cid, dry)
        await asyncio.to_thread(
            send_weixin,
            "📝【小小糯】公演小说《" + perf["novel_title"] + "》已重新生成，待审核～\n文件：" + path +
            "\n请在群里回复：/同意 发送｜/退回 重写｜/忽略 不再提醒")
        return
    if action == "approve":
        path = perf.get("novel_path", "")
        novel = await asyncio.to_thread(_read_text_file, path)
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


# ---------------------------------------------------------------- /继续 的共享归属

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


# ---------------------------------------------------------------- 命令→功能归属表

# ---- 归属表用的"此刻这一支会不会接手"闸门 -------------------------------
# 只开局的骰子命令不用等局开始；收局/续局/叫数要局在进行。


_DICE_ALWAYS_CMDS = ("/吹牛骰子", "/加入", "/牌局", "/看骰", "/开始", "/开")


def _gate_dice(cid: str, cmd: str) -> bool:
    if cmd in _DICE_ALWAYS_CMDS or parse_call(cmd.lstrip("/")) is not None:
        return True
    return bool(_dice_state().get("active"))


def _gate_perf(cid: str, cmd: str) -> bool:
    return bool(_get_perf_state(cid).get("active"))


def _gate_story(cid: str, cmd: str) -> bool:
    return bool(_get_story_state(cid).get("active"))


def _gate_vote(cid: str, cmd: str) -> bool:
    return bool(_get_vote_state(cid).get("active"))


def _gate_fasao(cid: str, cmd: str) -> bool:
    return bool(_get_fasao_state(cid).get("active"))

# 命令 → 功能 的归属表（**顺序必须与 cmd 分派链一致**）。
# 只用于兜底分支的一个判断：这条命令是不是属于某个**被关掉**的功能？
# 是 ⇒ 静默（不回、也不交给兜底聊天），否则"关掉投票"会变成
# "投票指令被当成闲聊回答" —— 功能没关干净。
#
# 每一项是 `((key, ...), 谓词, 闸门)`：
#   - **键是元组**：共享分支/落空转发的命令挂在多个功能上，任意一个开着就算"还开着"：
#       - `/选 N` 一个分支同时服务投票与剧情 ⇒ ("vote", "story")
#       - `/发骚结束` 没有进行中的场景时会**落空转发**到"发起场景" ⇒ 由后面的
#         ("scene",) 那一项兜住（不是靠元组第二个键）
#   - **闸门** `gate(cid, cmd) -> bool`：分派链里那一支**此刻**会不会接手（None = 一定会）。
#     它回答的是"被关掉的功能，是不是这条命令没被处理的**原因**"。
#
# ⚠️ 只看命令、不看状态会错杀（2026-09-27 实测事故）：
#   `/结束` 既是骰子的收局、也是发骚/调教的收尾。若瑾那个群只开 `scene`+`insult`，
#   用户发 `/结束` 想收尾发骚 → 旧实现只认第一个匹配（骰子）且骰子关着 → 静默，
#   发骚根本轮不到收尾。可骰子那一支**根本没在等这条命令**（群里没有骰子局），
#   所以"骰子关着"不是它没人接的原因 —— 不能据此静默。


_CMD_FEATURE_ORDER = (
    (("dice",), lambda c: c in ("/吹牛骰子", "/加入", "/牌局", "/看骰", "/开始",
                                "/下一轮", "/开", "/退出", "/结束")
     or parse_call(c.lstrip("/")) is not None, _gate_dice),
    (("perf",), is_perf_end_cmd, _gate_perf),
    (("perf_review",), lambda c: is_review_cmd(c) is not None, None),
    (("perf",), is_perf_start_cmd, None),
    (("story",), is_story_end_cmd, _gate_story),
    (("vote",), is_vote_end_cmd, _gate_vote),
    (("vote", "story"), lambda c: is_vote_choice_cmd(c) is not None, None),
    (("scene_end",), is_fasao_end_cmd, _gate_fasao),
    (("diaojiao",), is_dj_end_cmd, None),
    (("help",), lambda c: c.startswith("/ "), None),
    (("diaojiao",), lambda c: is_dj_command_cmd(c) is not None, None),
    (("diaojiao",), lambda c: is_diaojiao_cmd(c) is not None, None),
    (("scene_continue", "insult"), is_continue_cmd, None),
    (("scene_rewrite",), lambda c: is_rewrite_cmd(c) is not None, None),
    (("story",), is_story_start_cmd, None),
    (("vote",), is_vote_start_cmd, None),
    (("help",), lambda c: is_help_cmd(c) or is_guide_cmd(c), None),
    (("dream",), lambda c: _dream.command_payload(c) is not None, None),
    (("personal_story",), lambda c: _personal_story.command_payload(c) is not None, None),
    (("personal_portrait",), lambda c: _personal_portrait.command_payload(c) is not None, None),
    (("personal_letter",), lambda c: _personal_letter.command_payload(c) is not None, None),
    (("memory_remember",), lambda c: is_remember_cmd(c) is not None, None),
    (("memory_recall",), is_recall_cmd, None),
    (("devil_roast",), lambda c: is_devil_cmd(c) is not None, None),
    (("intro",), is_intro_cmd, None),
    (("sync",), is_sync_cmd, None),
    (("register",), lambda c: is_register_cmd(c) is not None, None),
    (("query",), is_profile_cmd, None),
    (("trend",), is_trend_board_cmd, None),
    (("rank_heat",), is_rank_cmd, None),
    (("recap",), lambda c: is_recap_cmd(c) is not None, None),
    (("absent",), lambda c: is_absent_cmd(c) is not None, None),
    (("trend",), lambda c: is_trend_cmd(c) is not None, None),
    (("summary",), is_summary_cmd, None),
    (("fortune",), is_fortune_cmd, None),
    (("scene",), is_scene_cmd, None),
    (("poem",), lambda c: is_poem_cmd(c) is not None, None),
    (("insult",), lambda c: is_insult_cmd(c) is not None, None),
)


def _cmd_owned_by_disabled(cid: str, cmd: str) -> bool:
    """这条命令是不是"被关掉的功能本来会接手"？（⇒ 静默，不交给兜底聊天）

    按分派链顺序找**第一支此刻真会接手**的分支（谓词命中 + 闸门开着）：
      - 它的功能开着 → False（派发链自己会处理）；
      - 它的功能关着 → True（这就是"功能关了、命令还在用"）。
    一支都不会接手（或压根没人认它） → False（走兜底聊天，与以前一致）。
    """
    for keys, pred, gate in _CMD_FEATURE_ORDER:
        try:
            if not pred(cmd):
                continue
            if gate is not None and not gate(cid, cmd):
                continue
        except Exception:                                      # noqa: BLE001
            continue
        return not any(_feat_on(cid, k) for k in keys)
    return False


# ---------------------------------------------------------------- 分派链本体

async def handle_slash(sender, cid, cmd, cmd_sender, dry, group_state, msgs,
                       _cmd_msg, state,
                       _save_group, _mark_inflight, _remember_turn,
                       rank_summary, sync_profiles):
    """斜杠指令分派链（P3-3-2 自 bot.py main_loop 原样抽出，逐字搬迁）。

    原来是 main_loop 里的 `if cmd:` 块（~394 行）。搬出来时只动了两处：
      - `old_last_id = ...` 那行移回调用方（回滚仍由外层 except 做，协议不变）；
      - `_feat_on` → 本模块 `_gate` 的赋值别名（字面形态不变，静态扫描照常命中）。
    其余逻辑、分支顺序、日志文案逐字一致。

    bot 侧依赖以参数注入（本模块不许 import bot，见 p8s4 守卫）：
      _save_group / _mark_inflight / _remember_turn / rank_summary / sync_profiles
    / state（main_loop 的形参）。调用点读的是当前全局，既有 monkeypatch bot.X
    的测试接缝不受影响。
    """
    reply = ""
    _no_prefix = False   # P12-T：羞辱特定目标时要在最前面加前缀（见发送点）
    log("⚡ [" + cid[:8] + "] " + cmd[:40])
    # 先推进 last_id，避免处理中异常导致同一条指令被重复处理
    group_state["last_id"] = msgs[-1]["message_id"]
    state[cid] = group_state
    _save_group(cid, group_state)
    _mark_inflight(cid, (_cmd_msg or msgs[-1]).get("message_id"))   # P12-BC：被硬杀也能补

    if _office.command(cmd) is not None:
        await _office.handle(sender, cid, cmd_sender, cmd, _cmd_msg or msgs[-1], dry=dry)
        return

    if _rose_moderation.is_command(cid, cmd):
        await _rose_moderation.handle(sender, cid, _cmd_msg or msgs[-1], dry)
        return

    if _gallery.command(cmd) is not None:
        if not _feat_on(cid, "slash_cmds"):
            return
        reply = await _gallery.command_reply(cid, cmd_sender, cmd,
                                              _cmd_msg or msgs[-1], dry=dry)
        if reply and not dry:
            if not await sender.send("🤖 " + reply, cid):
                raise RuntimeError("图集指令回执发送失败")
            if reply == _gallery.CARD_INTRO:
                message_id = await sender.send_character_with_id(_gallery.CARD_ID, cid)
                if not message_id:
                    raise RuntimeError("图集分享卡发送未确认")
        return

    if _haptics_bot.command(cmd) is not None:
        if not _feat_on(cid, "slash_cmds"):
            return
        reply = await _haptics_bot.handle(cmd, owner=(cmd_sender == OWNER_UID))
        if reply and not dry:
            if not await sender.send("🤖 " + reply, cid):
                raise RuntimeError("触觉指令回执发送失败")
        return

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
            await _perf_send(sender, _ph(cid, "perf_busy_dj",
                                         "调教进行中哦～先 /调教结束 再开公演～"), cid, dry)
        elif perf_active:
            await _perf_send(sender, _ph(cid, "perf_already",
                                         "公演已经进行中啦～"), cid, dry)
        else:
            await _handle_perf_start(sender, cid, msgs[-1]["message_id"], dry)
        reply = None  # 已自己发过消息了；空串会被当成"生成失败"回滚重试（2026-09-21 修）
    # 公演进行中，其他指令不响应（交给互动逻辑）
    # ⚠️ 这是**互斥分支**不是功能分支：但它也必须跟功能开关走 ——
    # 否则把"公演"关掉后，残留的 active 会继续把这个群屏蔽住
    # （运维在页面上关了功能，群里却更哑了 —— 那是最糟的结果）。
    elif perf_active and _feat_on(cid, "perf"):
        reply = _ph(cid, "play_busy_perf",
                    "🎭 公演进行中哦～暂无法响应其他指令，想参与就直接发言，等公演结束后再试～")
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
            end_text = await asyncio.to_thread(
                _dj_end_text, dj.get("target_uid"), dj)
            reply = _ph(cid, "dj_end", "🔗 调教结束啦～")
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
                reply = _ph(cid, "dj_cmd_empty", "命令不能为空哦～格式 /命令 xxx")
            else:
                dj["phase"] = "等回应"
                dj["last_activity"] = time.time()
                dj["round"] = dj.get("round", 0) + 1
                _set_dj_state(cid, dj)
                reply = await asyncio.to_thread(generate_diaojiao, _dj_target_text(dj["target_uid"]), dj_cmd,
                                               max_tokens=int(_tune("diaojiao.max_tokens", cid)))
                reply = (reply or "").strip()
                # P12-BD a（2026-09-30）：这里原来**每一轮**都拼一遍
                # `DJ_PARTICIPATE_HINT`（软糯原版："想参与的姐妹也来玩呀～
                # …30秒没人我就自己继续啦～"）。两个毛病：
                #   ① 那句话是 `runtime/diaojiao.py` 里的**硬编码原文**，
                #      绕过话术表 ⇒ 若瑾的 `dj_hint` 覆盖在这条路上不生效，
                #      每轮都漏出"姐妹们…～"的旧人设；
                #   ② 同一条参与提示每轮重复刷 + 带倒计时
                #      （"30秒没人我就…" = 明摆着是机器人在等输入）——
                #      这正是屋主说的"工具熊每次都要问要不要调酒"。
                # 参与提示现在**每局只在开头出现一次**：
                # `start_diaojiao()` 已经在开场消息末尾带了 `_dj_hint(cid)`
                # （走话术表，若瑾那版是"想接话的跟着发…"），
                # 目标回应时 `dj_replied` 还会再给一次。这里不再粘。
        else:
            # 没在局里：
            #   · `/命令 xxx` 是我们的参与语法 → 明确提示"没有进行中的调教"
            #   · 其它不认识的斜杠指令（/talk、/ask 这类别的 bot 的语法）
            #     → **不理**（屋主 2026-09-27 要求）。话术
            #     `dj_idle_unknown` 留空 = 静默；没写过这个 key 的
            #     人设（认证群）仍旧回 unknown_cmd，行为不变。
            if cmd.startswith("/命令"):
                reply = p("no_dial", cid)
            elif _personas.phrase_is_blank("dj_idle_unknown", cid):
                reply = None
            else:
                reply = p("dj_idle_unknown", cid) or p("unknown_cmd", cid)
    elif is_diaojiao_cmd(cmd) is not None and _feat_on(cid, "diaojiao"):
        # 发起调教
        if _get_dj_state(cid).get("active"):
            reply = _ph(cid, "dj_busy", "调教进行中哦～先 /调教结束 再发起新的～")
        else:
            reply = await asyncio.to_thread(start_diaojiao, cid, cmd, cmd_sender)
    elif _get_dj_state(cid).get("active") and _feat_on(cid, "diaojiao"):
        # 调教进行中，其他指令屏蔽（同样必须跟开关走，理由见公演那处）
        reply = _ph(cid, "dj_block", "🔗 调教进行中哦～想参与就发 /命令 xxx，或 /调教结束 收尾～")
    elif is_continue_cmd(cmd) and _continue_allowed(cid):
        # 续写（发骚续写优先，continue_last 内部分发发骚/羞辱）
        # ★ 共享分支：按上一次动作的**类型**决定归哪个功能管
        #   （insult 的上一段 → 羞辱；其余 → 场景续写）
        reply = await asyncio.to_thread(continue_last, cid, cmd, cmd_sender)
    elif is_rewrite_cmd(cmd) is not None and _feat_on(cid, "scene_rewrite"):
        # 重写上一段（发骚 active 时有效）
        reply = await asyncio.to_thread(rewrite_last, cid, cmd)
    elif _get_fasao_state(cid).get("active") and _feat_on(cid, "scene"):
        # 发骚进行中，其他指令屏蔽（P12-BP：走话术表，别再硬编码软糯口吻）
        reply = _ph(cid, "play_busy_fasao",
                    "🎲 发骚过程中暂时无法响应其他指令哦～请等结束后再试～想继续就发 /继续，想结束就发 /结束～")
    elif is_story_start_cmd(cmd) and _feat_on(cid, "story"):
        # 发起剧情游戏
        if _get_story_state(cid).get("active"):
            reply = _ph(cid, "story_already", "剧情已经进行中啦～")
        else:
            reply = await asyncio.to_thread(start_story, cid, cmd, cmd_sender)
    elif is_vote_start_cmd(cmd) and _feat_on(cid, "vote"):
        # 发起投票（轻量，可与其他功能并存）
        if _get_vote_state(cid).get("active"):
            reply = _ph(cid, "vote_already", "已经有进行中的投票啦～想结束发 /投票结束～")
        else:
            reply = await asyncio.to_thread(start_vote, cid, cmd, cmd_sender)
    elif _get_story_state(cid).get("active") and _feat_on(cid, "story"):
        # 剧情进行中，其他指令屏蔽
        reply = _ph(cid, "play_busy_story",
                    "🎬 剧情进行中哦～想投票就发 /选 1/2/3 或 /1/2/3，或 /剧情结束 收尾～")
    elif _get_vote_state(cid).get("active") and _feat_on(cid, "vote"):
        # 投票进行中，其他指令屏蔽
        reply = _ph(cid, "play_busy_vote",
                    "📊 投票进行中哦～想参与就发 /选 X 或 /X（X=序号），想结束发 /投票结束～")
    elif (is_help_cmd(cmd) or is_guide_cmd(cmd)) and _feat_on(cid, "help"):
        reply = p("help_text", cid) or _personas.fill(HELP_TEXT, cid)
        if _gallery.in_scope(cid):
            reply += "\n/漫画 - 打开姐妹图集（所有人可查看）\n回复图片 /保存 个人立绘 图片名称 - 名称可不填，仅妹子群可收图"
        if _dream.enabled(cid):
            if cid == _dream.TEA_CID:
                dream_command = "#解梦" if _dream.is_hash_request(cid, "#解梦") else "@" + _personas.me_full(cid) + " 解梦"
            else:
                dream_command = "/解梦"
            reply += "\n" + dream_command + " 梦境 - 糯糯看梦（也可 @ 求解）"
        if _personal_story.enabled(cid):
            reply += "\n/我的故事 - 按日期回顾自己的聊天（也可 @ 小小糯 我的故事）"
        if _personal_portrait.enabled(cid):
            reply += "\n/我的画像 - 从多个维度看看自己（也可 @ 小小糯 我的画像）"
        if _personal_letter.enabled(cid):
            reply += "\n/致 某某 - 给群里某个人写一封信（也可 @ 小小糯 给某某写封信）"
    elif _dream.command_payload(cmd) is not None:
        reply = await _dream.dream_reply(cid, cmd_sender, cmd) if _feat_on(cid, "dream") else None
    elif _personal_story.command_payload(cmd) is not None:
        reply = await _personal_story.story_reply(cid, cmd_sender, cmd) if _feat_on(cid, "personal_story") else None
    elif _personal_portrait.command_payload(cmd) is not None:
        reply = await _personal_portrait.portrait_reply(cid, cmd_sender, cmd) if _feat_on(cid, "personal_portrait") else None
    elif _personal_letter.command_payload(cmd) is not None:
        reply = await _personal_letter.letter_reply(cid, cmd_sender, cmd) if _feat_on(cid, "personal_letter") else None
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
        _save_group(cid, group_state)
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
                await sender.send(_ph(cid, "sync_working",
                          "🤖 收到同步指令～正在拉取增量消息，稍等一下下哦～"), cid)
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
        # 发情值排行（P12-BR a：前几名按群可调，默认 10）
        reply = await asyncio.to_thread(
            rank_summary, top=int(_tune("rank.top_n", cid)))
        if not reply:
            reply = p("rank_empty", cid)
    elif (rc_win := is_recap_cmd(cmd)) is not None and _feat_on(cid, "recap"):
        # 「我不在的时候群里发生了什么」（按群范围开放，见 scopes.recap）
        reply = await recap_absent(
            cid, cmd_sender, rc_win,
            (_cmd_msg or {}).get("message_id"))
        if not reply:
            reply = p("unknown_cmd", cid)  # 非认证群：当成"不认识这个指令"
    elif (absent_q := is_absent_cmd(cmd)) is not None and _feat_on(cid, "absent"):
        # P12-CB：缺席名册（裸=列表零 API；带昵称=单人卡+一次 LLM 小传）。
        # 数据全走认证群归档只读 SQL；成员表一次调用判退群/潜水。见 runtime/absent.py
        reply = await asyncio.to_thread(absent_reply, cid, absent_q)
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
        # 算命：给发指令的人算今天运势。
        # P12-BR e（2026-09-30）：`is_fortune_cmd` 是 `startswith` 语义，
        # 所以 `#运势趋势`/`/运势趋势` 这一族也会落到这里 —— 在**没开
        # `trend`**的群里（如茶水间）trend 分支被闸门跳过，于是给出一张
        # **个人卡**：指令名说"趋势"、给的是个人运势，属于答错。
        # 现在先把趋势族挑出来：能给群榜就给群榜，给不了就明说没开
        # （话术 `trend_off`）—— 宁可说"没开"，也不给一张错的卡。
        if is_trend_cmd(cmd) is not None or is_trend_board_cmd(cmd):
            reply = await week_trend_board(cid)
            if not reply:
                reply = p("trend_off", cid) or \
                    "运势趋势只在本群算过运势之后才有。这个群没开，先发 /算命 看今天的。"
        else:
            reply = await asyncio.to_thread(fortune, cmd_sender, cid)
    elif is_scene_cmd(cmd) and _feat_on(cid, "scene"):
        # 涩涩骰子：人物×场景×体位×附加玩法
        reply = await asyncio.to_thread(scene, cmd)
    elif is_poem_cmd(cmd) is not None and _feat_on(cid, "poem"):
        # 写诗：随机文学方向，根据档案+发言写短诗
        reply = await asyncio.to_thread(poem, cmd, cmd_sender)
    elif _is_stop_word(cmd):
        # 通用停止词（/结束 /停止 /停下 /终止），但上面那些**真正的收尾
        # 分支一个都没接**（没有进行中的玩法）⇒ 回一句提示。
        # 屋主 2026-09-27 定的口径：这里别静默 —— 实测"/结束 没反应"
        # 有一半就是"本来就没有在进行的玩法"，静默会让人以为机器人坏了。
        # ⚠️ 只认**通用**停止词：`/剧情结束`、`/调教结束` 这类点名的
        #    不算（它们归各自功能，功能关了就照旧静默）。
        reply = p("no_play", cid)
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
                # P12-T：**先识别性别**（档案没有就查一次平台），
                # 再按取向决定走"只毒舌"还是"拉满"那条。
                _g = await asyncio.to_thread(_gender_of, target_uid, cid)
                reply, style = await asyncio.to_thread(
                    generate_insult, text, custom, history,
                    gender=_g,
                    straight=_personas.is_straight(cid),
                    max_tokens=int(_tune("insult.max_tokens", cid)))
                _record_insult(target_uid, style, reply)
                # P12-T：某些目标要在消息**最最前面**加前缀
                # （屋主指定：羞辱「工具熊」→ /talk；话术可配）
                _pre = _insult_prefix(cid, get_full_name(target_uid))
                if _pre:
                    reply = _pre + " " + reply
                    _no_prefix = True
                # P12-X：羞辱正文撒点表情（💗💦🥵…），开关是话术
                # `spice_emoji`（空 = 不撒）。前缀加在最前面，不受影响。
                reply = _spice_emoji(reply, p("spice_emoji", cid))
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
            # P12-T：某些羞辱目标要求在**最最前面**加前缀（如 /talk）——
            # 那种消息不能再加"🤖 "，否则前缀就不在最前面了。
            reply = reply if _no_prefix else ("🤖 " + reply)
            log("💬 " + reply[:200])
            _remember_turn(cid, "我", reply[2:].strip()
                           if reply.startswith("🤖 ") else reply)
            if dry:
                log("[dry] skip")
            else:
                ok = await sender.send(reply, cid)
                log("✅" if ok else "❌")
                # P12-L：/自我介绍 之后补一张立绘（只发图）
                if ok and is_intro_cmd(cmd or "") \
                        and _feat_on(cid, "intro"):
                    await _intro_photo.send(sender, cid, reason="指令")
        else:
            # 生成失败（空回复），抛异常让 except 统一回滚 last_id
            raise RuntimeError("生成失败(空回复): " + cmd[:20])
    # reply is None：静默成功（投票/剧情记录、公演屏蔽），不回复不报错
