"""P8 第 4 步的迁移机制：把 bot.py 的模块级函数**整段搬**到新模块。

沿用 P4.2 / P8.3 的纪律：**程序化搬运 + 逐项比对 + 任何不符即中止**。

为什么不能靠手工剪切粘贴：
  搬走的函数如果在 bot.py 里偷偷引用了某个模块级全局（比如 `_ACTIVE_CID`），
  搬出去之后 **import 不会报错**，只在真正调用时 `NameError`。
  这意味着一半的错要等到线上有人发那条指令才炸 —— P8 第 3 步那种"抄错一个字"
  的风险，在这里会升级成"运行时才炸"。所以本工具在写盘**之前**做静态依赖检查。

三道闸门（任一不过就不写盘）：
  1. 源文件里每个函数名/常量名存在且**唯一**，要删的文本也唯一
  2. 组装出的新模块里，被搬函数引用的每个模块级名字都必须有来源
     （兄弟函数 / 搬过来的常量 / 头部 import / 内置）——否则中止
  3. 搬完重新 import bot，逐个用 `inspect.getsource` 比对源码文本与搬之前一致

用法：
    python tools/p8s4_move.py <slice> --dry    # 只看会发生什么
    python tools/p8s4_move.py <slice>          # 执行
"""
import ast
import builtins
import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot.py"
BOT_IMPORT_ANCHOR = "import state_store"      # bot.py 里新 import 插在它后面


class Slice:
    def __init__(self, module, header, funcs=(), consts=(), remove_texts=(),
                 rewrites=(), export_in_bot=True, append=False):
        self.module = module
        self.header = header
        self.funcs = list(funcs)
        self.consts = list(consts)
        self.remove_texts = list(remove_texts)
        # 搬运时必须一起改的文本（例如 `_ACTIVE_CID` -> `ctx.get_active_cid()`）。
        # 声明式：工具会逐条打印出来，而且"搬完比对"以**改写后**的文本为基准，
        # 所以它仍然能证明"除了这些声明过的改写，一个字符都没动"。
        self.rewrites = list(rewrites)
        self.export_in_bot = export_in_bot
        # append=True：目标是**已有模块**，把函数追加进去（header 只用于校验，
        # 不会重写文件头）。给"某几个函数天然属于已存在的模块"这种情况用 ——
        # 为它们单开一个模块反而把边界搞碎。
        self.append = append


SLICES = {}

# ---------------------------------------------------------------- 第 0 片：地基
SLICES["obs"] = Slice(
    module="runtime/obs.py",
    header='''"""日志（P8 第 4 步从 bot.py 拆出的叶子工具）。

`log` 被 bot.py 里几乎每个函数调用，是最典型的"叶子工具"：只依赖 `ROOT`
和 `datetime`，谁都不依赖。**必须先把它搬出来** —— 否则后面每拆一个模块，
新模块都要为了打一行日志回指 bot.py。
"""
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

__all__ = ["log", "ROOT"]
''',
    funcs=["log"],
)

SLICES["identity"] = Slice(
    module="runtime/identity.py",
    header='''"""双身份 + 昵称解析（P8 第 4 步从 bot.py 拆出的叶子工具）。

P4 起「我自己」有**两个**身份，全项目必须一致地认这两个：
  NUO_UID —— 侧车账号（登录态持有者）：私聊 / 图片 / 分享 / 历史数据里的旧发言
  BOT_UID —— 官方 Bot：群文本发送（P4.1 的 RoutingSender）

回声过滤与「排除自己」必须同时认这两个账号，否则 Bot 的发言会被当成群友 ——
轻则统计污染，重则自触发循环。

昵称解析（`get_full_name`/`get_name`）也放这里：判定「@的是不是我」需要它，
而它又是几乎所有模块都要用的东西。原始昵称来自成员表，查不到时兜底读档案。

`_name_cache` 是**可变对象**：bot.py 用 `from ... import _name_cache` 拿到同一个
dict 就地读写，所以留在 bot.py 的两个使用点（`_daily_profile_scan`、
`_find_uid_by_name`）不需要改任何代码。
"""
import os

from dzmm import get_members, get_user
from profiles import get_profile
from runtime import ctx

# ---------------------------------------------------------------- 身份
NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"  # 侧车账号（登录态持有者）
BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()
BOT_NAME = (os.environ.get("DZMM_BOT_NAME") or "小小糯").strip()
BOT_NAME_SHORT = (os.environ.get("DZMM_BOT_SHORT") or "小小糯").strip()

# （_name_cache 随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "NUO_UID", "BOT_UID", "BOT_NAME", "BOT_NAME_SHORT",
    "self_uids", "is_self", "self_names",
    "_name_cache", "_load_member_names", "get_full_name", "get_name", "is_at_me",
]
''',
    funcs=["self_uids", "is_self", "self_names",
           "_load_member_names", "get_full_name", "get_name", "is_at_me"],
    # 常量**不搬**，由头部定义（头部版本带注释、是权威定义处）；bot.py 里的原定义
    # 用 remove_texts 删掉。搬常量会与头部重复定义 —— 第一版就是这么写的，
    # 结果同一组常量在文件里出现两次。
    consts=[],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()")],
    remove_texts=[
        "# ---------------------------------------------------------------- 自身身份（P4.2）\n"
        "# 从 P4 起，「我自己」有**两个**身份，必须分清：\n"
        "#   NUO_UID  —— 侧车账号：私聊 / 图片 / 分享 / 其他群，以及历史数据里的旧发言\n"
        "#   BOT_UID  —— 官方 Bot：群文本发送（P4.1 的 RoutingSender）\n"
        "# 回声过滤与「排除自己」必须同时认这两个。否则 Bot 的发言会被当成群友 ——\n"
        "# 轻则统计污染，重则自触发循环。\n",
        "# 昵称缓存（uid -> 昵称），跨轮次复用\n",
        'NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"  # 侧车账号（登录态持有者）\n',
        'BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()\n',
        'BOT_NAME = (os.environ.get("DZMM_BOT_NAME") or "小小糯").strip()\n',
        'BOT_NAME_SHORT = (os.environ.get("DZMM_BOT_SHORT") or "小小糯").strip()\n',
        "_name_cache = {}\n",
    ],
)

# ---------------------------------------------------------------- 第 1 片：骰子局
SLICES["dice"] = Slice(
    module="runtime/dice.py",
    header='''"""吹牛骰子局的状态机（P8 第 4 步从 bot.py 整段搬出）。

为什么第一个拆它：侦察量出来的耦合最低 ——
  fan_in = 1（只有 main_loop 调它）
  fan_out = 2（只要 `get_name` / `log`）
  引用的模块级全局 **0 个**
拆完对 bot.py **零回指**，所以它同时也是"拆分机制"本身的验证。

纯牌面规则本来就在 `dice_game.py`（发牌 / 叫点 / 判定 / 开牌）；
这里只管**局面**：座位、轮次、看板、各方指令、私聊发骰。
"""
import asyncio
import time

from dice_game import count_point, dice_str, is_legal_raise, roll_dice, roll_wild
from dzmm import get_private_cid
from runtime.identity import get_name
from runtime.obs import log
import state_store

__all__ = [
    "_dice_state", "_dice_save", "_dice_current_player", "_dice_send_private",
    "_dice_board_text", "_dice_deal_round", "_dice_start_cmd", "_dice_join_cmd",
    "_dice_exit_cmd", "_dice_begin_cmd", "_dice_next_round_cmd", "_dice_call_cmd",
    "_dice_open_cmd", "_dice_quit_cmd", "_dice_skip_cmd", "_dice_status_text",
]
''',
    funcs=["_dice_state", "_dice_save", "_dice_current_player",
           "_dice_send_private", "_dice_board_text", "_dice_deal_round",
           "_dice_start_cmd", "_dice_join_cmd", "_dice_exit_cmd",
           "_dice_begin_cmd", "_dice_next_round_cmd", "_dice_call_cmd",
           "_dice_open_cmd", "_dice_quit_cmd", "_dice_skip_cmd",
           "_dice_status_text"],
    remove_texts=["# ============ 吹牛骰子游戏 ============"],
)


# ---------------------------------------------------------------- 第 2 片：文本工具
SLICES["text"] = Slice(
    module="runtime/text.py",
    header='''"""中文文本工具（P8 第 4 步从 bot.py 拆出的叶子工具）。

这一组是**纯函数**：不碰网络、不碰状态、不认识"群"。
之所以要单独成模块，是因为后面每一片（`members` / `fortune` / `vote` / `poem` …）
都要用昵称归一化和选项归一化；不抽出来，每片都得回指 `bot.py`。

`_normalize_cn` 是这里最要紧的一个：昵称比较必须走它 ——
繁简、异体字、全半角、空格、大小写任一不同都会被当成两个人，
而"认错人"在这套系统里等于把 A 的档案按到 B 头上。
"""
import re
import unicodedata

# （_OPENCC_T2S 随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "_get_opencc", "_normalize_cn", "_simplify", "_strip_parenthesis",
    "_is_stop_word", "_dedupe_options", "_normalize_choice",
    "_extract_deadline_seconds", "_squash", "_clean_name", "_norm_poem",
]
''',
    funcs=["_get_opencc", "_normalize_cn", "_simplify", "_strip_parenthesis",
           "_is_stop_word", "_dedupe_options", "_normalize_choice",
           "_extract_deadline_seconds", "_squash", "_clean_name", "_norm_poem"],
    consts=["_CJK_VARIANT", "STOP_WORDS", "_OPENCC_T2S"],
)

# ---------------------------------------------------------------- 第 3 片：成员/档案查询
SLICES["members"] = Slice(
    module="runtime/members.py",
    header='''"""群成员与档案查询（P8 第 4 步从 bot.py 拆出）。

一组"按昵称/uid 找人、把某人的资料与发言拼成文本"的helper。
它们被发骚、公演、投票、剧情、藏头诗、运势、周趋势**全都要用** ——
留在 bot.py 里的话，上面每一个模块都要回指它，拆分就白做了。

**找人的优先级是有讲究的**（`_find_uid_by_name`）：精确 → 简化昵称 → 前缀 → 子串，
且**单字不参与模糊匹配** —— 否则「裳」会命中「裳姐姐」，把指令打到别人身上。
"""
import random
import time

from dzmm import get_messages_all, get_user
from profiles import get_profile, load_profiles
from runtime import ctx
from runtime.identity import (_load_member_names, _name_cache, get_full_name,
                              get_name, is_self)
from runtime.text import _normalize_cn, _simplify, _squash

# （HISTORY_MAX 随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "_extract_msgs", "_get_msgs_by_uid", "_member_profile_text",
    "_find_in_profiles", "_find_user_in_msgs", "find_user_by_name",
    "_find_uid_by_name", "_gender_label", "_random_member_uid",
    "_archive_text_from_profile", "_history_lines", "_profile_history",
]
''',
    funcs=["_extract_msgs", "_get_msgs_by_uid", "_member_profile_text",
           "_find_in_profiles", "_find_user_in_msgs", "find_user_by_name",
           "_find_uid_by_name", "_gender_label", "_random_member_uid",
           "_archive_text_from_profile", "_history_lines", "_profile_history"],
    consts=["HISTORY_MAX"],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()")],
)


# ---------------------------------------------------------------- 第 4 片：运势
SLICES["fortune"] = Slice(
    module="runtime/fortune.py",
    header='''"""每日运势（P8 第 4 步从 bot.py 搬出）。

五行/干支/生肖/关系那几张表（`_GAN` `_ZHI` `_WX_*` `_SHENG` `_KE` `_CHONG`
`_REL_SCORE` `_ZHI_SCORE` …）**跟着这一片一起搬**：它们只被这里用，
留在 bot.py 里就变成"模块外的隐式依赖"。

`_fortune_calc` 同时被周趋势（`_trend_chong`）用 —— 所以周趋势必须排在它后面，
从 `runtime.fortune` 拿，而不是回指 bot.py。
"""
import random
import time
from datetime import datetime

from brains import generate_fortune
from profiles import get_profile
from runtime import ctx
from runtime.identity import get_full_name
from runtime.text import _clean_name
import state_store

__all__ = [
    "is_fortune_cmd", "fortune", "_fortune_calc", "_fortune_score",
    "_fortune_level", "_record_fortune", "_get_fortune_history",
    "_char_wx", "_name_wx", "_ganzhi", "_relation",
]
''',
    funcs=["is_fortune_cmd", "fortune", "_fortune_calc", "_fortune_score",
           "_fortune_level", "_record_fortune", "_get_fortune_history",
           "_char_wx", "_name_wx", "_ganzhi", "_relation"],
    consts=["FORTUNE_APOLOGY", "_GAN", "_ZHI", "_WX_GAN", "_WX_ZHI", "_GZ_BASE",
            "_WX_ORDER", "_WX_BASE", "_WX_LUCK", "_WX_YI", "_WX_JI", "_SHENG",
            "_KE", "_CHONG", "_SHENGXIAO", "_CHAR_WX", "_REL_SCORE",
            "_REL_TEXT", "_LIUCHONG", "_LIUHE", "_ZHI_SCORE", "_ZHI_PENALTY"],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()"),
              ("load_state(", "state_store.load_state("),
              ("save_state(", "state_store.save_state(")],
)


# ---------------------------------------------------------------- 第 5 片：周趋势
SLICES["trend"] = Slice(
    module="runtime/trend.py",
    header='''"""骚度周趋势（P8 第 4 步从 bot.py 搬出）。

`_trend_chong` 要用运势的干支冲合（`_fortune_calc`），所以它排在 `fortune`
之后，从 `runtime.fortune` 拿 —— 这也是拆分顺序的由来：**先搬被依赖的**。
"""
import asyncio
import re
from datetime import datetime, timedelta

from brains import generate_trend
from profiles import get_profile
from runtime.ctx import PRIMARY_CID
from runtime.fortune import _fortune_calc
from runtime.identity import get_full_name, get_name
from runtime.obs import log
import state_store

__all__ = [
    "is_trend_cmd", "is_trend_board_cmd", "_trend_days", "_trend_spark",
    "_trend_chong", "_trend_stats", "_wd_cn", "_md_cn", "week_trend",
    "week_trend_board",
]
''',
    funcs=["is_trend_cmd", "is_trend_board_cmd", "_trend_days", "_trend_spark",
           "_trend_chong", "_trend_stats", "_wd_cn", "_md_cn", "week_trend",
           "week_trend_board"],
    consts=["TREND_CIDS", "TREND_DEFAULT_DAYS", "TREND_MAX_DAYS"],
    rewrites=[("load_state(", "state_store.load_state("),
              ("save_state(", "state_store.save_state(")],
)


# ---------------------------------------------------------------- 第 6 片：缺勤复盘
SLICES["recap"] = Slice(
    module="runtime/recap.py",
    header='''"""缺勤复盘（P8 第 4 步从 bot.py 搬出）。

"我不在的时候群里发生了什么" —— 按时间窗回放消息、点名被 @ 的人和缺勤的人。
只在认证妹子群开放（`RECAP_CIDS`），且有冷却（同一时间窗重复问会被挡）。
"""
import asyncio
import re
import time
from datetime import datetime, timedelta

from brains import generate_recap
from dzmm import get_messages_since
from runtime.ctx import PRIMARY_CID
from runtime.identity import _load_member_names, get_full_name, get_name, is_self
from runtime.obs import log
import state_store

__all__ = [
    "is_recap_cmd", "_recap_intent", "_recap_window", "_day0", "_msg_ts",
    "_fmt_ts", "_recap_mentions", "_recap_transcript", "recap_absent",
]
''',
    funcs=["is_recap_cmd", "_recap_intent", "_recap_window", "_day0", "_msg_ts",
           "_fmt_ts", "_recap_mentions", "_recap_transcript", "recap_absent"],
    consts=["RECAP_CIDS", "RECAP_MAX_MSGS", "RECAP_MAX_HOURS", "RECAP_COOLDOWN"],
    rewrites=[("load_state(", "state_store.load_state("),
              ("save_state(", "state_store.save_state(")],
)

# ---------------------------------------------------------------- 第 7 片：星座/房间
SLICES["sign"] = Slice(
    module="runtime/sign.py",
    header='''"""玫瑰房：星座运势与房间互动（P8 第 4 步从 bot.py 搬出）。

包含房间触发词、摘花判定，以及"我是什么星座"的整条链路
（解析生日 → 记住 → 算运势 → 出卡片）。
"""
import asyncio
import hashlib
import random
import re
import time
from datetime import datetime

from brains import generate_horoscope
from dzmm import get_user
from personas import (NUONUO_UID, ROOMS, ROSE_LUCKY_COLORS, ROSE_LUCKY_DIRS,
                      ROSE_LUCKY_ITEMS, ROSE_MOONS, ROSE_SIGNS, ROSE_SIGN_JI,
                      ROSE_SIGN_WORDS, ROSE_SIGN_YI, ROOM_FLOWER_NOUNS,
                      ROOM_FLOWER_VERBS, ROOM_FLOWER_WORDS, ROOM_MENU_WORDS,
                      ROOM_ORDER_VERBS)
from runtime.identity import _load_member_names, get_name, is_at_me, is_self
from runtime.members import _get_msgs_by_uid
from runtime.obs import log
import state_store

__all__ = [
    "_room_owner", "_is_room_trigger", "_is_flower_grab", "_sign_from_md",
    "_sign_intent", "_sign_find", "_sign_parse_birth", "_sign_stored",
    "_sign_remember", "_sign_calc", "_sign_recent", "_sign_card", "_sign_reply",
]
''',
    funcs=["_room_owner", "_is_room_trigger", "_is_flower_grab", "_sign_from_md",
           "_sign_intent", "_sign_find", "_sign_parse_birth", "_sign_stored",
           "_sign_remember", "_sign_calc", "_sign_recent", "_sign_card",
           "_sign_reply"],
    consts=["_ROOM_LAYOUT_WORDS", "_ROOM_CONJURE_WORDS", "_SIGN_FULL",
            "_SIGN_SHORT", "_SIGN_ME_RE", "_SIGN_NAMES"],
    rewrites=[("load_state(", "state_store.load_state("),
              ("save_state(", "state_store.save_state(")],
)


# ---------------------------------------------------------------- 第 6 片：按群状态存取器
SLICES["group_state"] = Slice(
    module="runtime/group_state.py",
    header='''"""按群持久化状态的**薄存取器**（P8 第 4 步从 bot.py 搬出）。

这是 P8 第 1 步那批"读全部群 → 改 → 写全部群"样板的最终归宿：
它们全部只做一件事 —— 从 `state_store` 按群读/写一个键。
单独成模块是因为**五个状态机、记忆、上一段动作全都要用**，
留在 bot.py 里，后面每个切片都得回指它。

⚠️ 骰子局的 `_dice_state`/`_dice_save` 不在这里，而在 `runtime/dice.py`：
它用的是**顶层键**（`dice_game` 不按 cid 存），不是"按群"的。
"""
import time

import state_store

# （MEMORY_MAX 等常量随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "_get_fasao_state", "_set_fasao_state", "_get_dj_state", "_set_dj_state",
    "_get_perf_state", "_set_perf_state", "_get_vote_state", "_set_vote_state",
    "_get_story_state", "_set_story_state",
    "_get_memory", "_remember", "_set_last_action", "_get_last_action",
]
''',
    funcs=["_get_fasao_state", "_set_fasao_state", "_get_dj_state", "_set_dj_state",
           "_get_perf_state", "_set_perf_state", "_get_vote_state",
           "_set_vote_state", "_get_story_state", "_set_story_state",
           "_get_memory", "_remember", "_set_last_action", "_get_last_action"],
    consts=["MEMORY_MAX"],
)

# ---------------------------------------------------------------- 第 7 片：发情值
SLICES["heat"] = Slice(
    module="runtime/heat.py",
    header='''"""发情值（P8 第 4 步从 bot.py 搬出）。

`_add_heat` 是**最热的写路径**（几乎每个玩法得分都要调它），
所以 P8 第 1 步就先把它改成了单键写入（只写该群那一行）。

11 个 `HEAT_*` 取值与 P8 之前的字面量逐个相同，由
`tests/test_p8_refactor.py` 钉死 —— 调平衡只改常量，改错了测试会说话。
搬到这里之后，调用方从 `runtime.heat` 取（bot.py 仍 re-export，老调用点不用改）。
"""
import state_store

from runtime.identity import is_self

__all__ = [
    "_add_heat",
    "HEAT_SCENE_PICK", "HEAT_SCENE_CONTINUE_TARGET", "HEAT_SCENE_CONTINUE_SENDER",
    "HEAT_INSULT_TARGET", "HEAT_INSULT_SENDER", "HEAT_FASAO_PICK",
    "HEAT_DIAOJIAO_ROUND", "HEAT_VOTE_STARTER", "HEAT_VOTE_VOTER",
    "HEAT_STORY_VOTER", "HEAT_STORY_PROTAGONIST",
]
''',
    funcs=["_add_heat"],
    consts=["HEAT_SCENE_PICK", "HEAT_SCENE_CONTINUE_TARGET",
            "HEAT_SCENE_CONTINUE_SENDER", "HEAT_INSULT_TARGET",
            "HEAT_INSULT_SENDER", "HEAT_FASAO_PICK", "HEAT_DIAOJIAO_ROUND",
            "HEAT_VOTE_STARTER", "HEAT_VOTE_VOTER", "HEAT_STORY_VOTER",
            "HEAT_STORY_PROTAGONIST"],
)


# ---------------------------------------------------------------- 第 8 片：记忆与档案变更
SLICES["memory"] = Slice(
    module="runtime/memory.py",
    header='''"""长期记忆 + 档案变更轨迹（P8 第 4 步从 bot.py 搬出）。

两件事放在一起是因为它们是同一条链：
/记住 写入的记忆要能被 /回忆 找回来（先关键词粗排、再交给 AI 精筛），
而"人变了"（改名/改签名/改简介）也要留痕，查人时才能拿新旧对比做反差。

`_retrieve_memory` 的两级筛选是刻意的：先用纯本地的词面匹配把候选压到几条，
再花一次 LLM 调用精筛 —— 直接全量喂 LLM 又慢又贵。
"""
import re
import time

from brains import _call_llm
from dzmm import get_user
from profiles import get_profile, load_profiles, upsert_profile
from runtime import ctx
from runtime.group_state import _get_memory, _remember
from runtime.identity import get_full_name
from runtime.members import HISTORY_MAX, _profile_history
from runtime.obs import log
import state_store

__all__ = [
    "_extract_names", "_extract_keywords", "_fast_rank", "_ai_filter",
    "_retrieve_memory", "_append_memory_ref", "_extract_remember",
    "is_remember_cmd", "is_recall_cmd",
    "_record_history", "_on_rename", "_refresh_profile_latest",
]
''',
    funcs=["_extract_names", "_extract_keywords", "_fast_rank", "_ai_filter",
           "_retrieve_memory", "_append_memory_ref", "_extract_remember",
           "is_remember_cmd", "is_recall_cmd",
           "_record_history", "_on_rename", "_refresh_profile_latest"],
    consts=["MEMORY_TRIGGERS", "MEMORY_FILTER_PROMPT"],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()"),
              ("load_state(", "state_store.load_state("),
              ("save_state(", "state_store.save_state(")],
)


# ---------------------------------------------------------------- 第 9 片：藏头诗/夸夸诗
SLICES["poem"] = Slice(
    module="runtime/poem.py",
    header='''"""藏头诗与夸夸诗（P8 第 4 步从 bot.py 搬出）。

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
''',
    funcs=["is_poem_cmd", "poem", "_do_poem", "_poem_text", "_poem_resolve",
           "_record_poem", "_get_poem_history", "_parse_poem_style"],
    consts=["POEM_STYLE_KEYWORDS"],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()")],
)


# ------------------------------------------------------- 追加：点名成员提取 -> members
SLICES["mentioned_members"] = Slice(
    module="runtime/members.py",
    header="（追加，不使用）",
    funcs=["_extract_mentioned_members"],
    append=True,
)


# ---------------------------------------------------------------- 第 10 片：小说落盘
SLICES["novel"] = Slice(
    module="runtime/novel.py",
    header='''"""公演小说落盘（P8 第 4 步从 bot.py 搬出）。

标题提取 / 文件名生成 / 目录创建。放在公演之前，因为公演结束时要存小说。

`_group_name_cache` 是**可变对象**：`bot.py` 的 `get_group_name` 也用同一个 dict
（群名 5 分钟缓存），所以搬出来之后两边共享，不需要改 `get_group_name`。
"""
import time
from datetime import datetime
from pathlib import Path

from dzmm import get_chatroom_name

ROOT = Path(__file__).resolve().parent.parent

# （_group_name_cache 随搬运一起进来，见本 slice 的 consts）

__all__ = ["_extract_novel_title", "_novel_filename", "_novel_save_path"]
''',
    funcs=["_extract_novel_title", "_novel_filename", "_novel_save_path"],
    consts=["_group_name_cache"],
)


# ---------------------------------------------------------------- 第 11 片：投票
SLICES["vote"] = Slice(
    module="runtime/vote.py",
    header='''"""群投票（P8 第 4 步从 bot.py 搬出）。

⚠️ 分派顺序是行为，不是风格：`/投票结束` **同时**命中开始与结束两个判定，
而结束判定会被"没有进行中的投票"挡掉 → 落到开始判定 →
**开出一个标题叫"结束"的投票**。这条顺序见 `docs/保全清单.md`，不得重排。
"""
import time
from datetime import datetime, timedelta

from brains import generate_vote_options
from dzmm import get_messages_all
from runtime.group_state import _get_vote_state, _set_vote_state
from runtime.heat import HEAT_VOTE_STARTER, HEAT_VOTE_VOTER, _add_heat
from runtime.identity import get_name, is_self
from runtime.members import _extract_mentioned_members
from runtime.obs import log
from runtime.text import _dedupe_options, _extract_deadline_seconds, _is_stop_word, _normalize_choice
import state_store

# （VOTE_DEFAULT_SECONDS 随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "is_vote_start_cmd", "is_vote_end_cmd", "is_vote_choice_cmd",
    "_extract_vote_title", "_vote_members_text", "start_vote", "_record_vote",
    "_finish_vote", "_check_vote", "VOTE_DEFAULT_SECONDS",
]
''',
    funcs=["is_vote_start_cmd", "is_vote_end_cmd", "is_vote_choice_cmd",
           "_extract_vote_title", "_vote_members_text", "start_vote",
           "_record_vote", "_finish_vote", "_check_vote"],
    consts=["VOTE_DEFAULT_SECONDS"],
)

# ---------------------------------------------------------------- 第 12 片：剧情
SLICES["story"] = Slice(
    module="runtime/story.py",
    header='''"""群剧情接龙（P8 第 4 步从 bot.py 搬出）。

`/剧情结束` **实测**只命中结束判定（`is_story_start_cmd=False`、
`is_story_end_cmd=True`），**没有**投票/发骚那种"开始与结束同时命中"的重叠。
`docs/保全清单.md` 登记的重叠只有 `/投票结束`、`/发骚结束`、`/调教结束`。

`_record_story_vote` / `_story_vote_result` 是**剧情与投票共用**的两个函数
（剧情也用投票选下一步），所以放在这里、由 vote 侧引用 —— 不能两边各留一份。
"""
import time
from datetime import datetime, timedelta

import random

from brains import generate_story_end, generate_story_start, generate_story_step
from dzmm import get_messages_all
from runtime.group_state import _get_story_state, _set_story_state
from runtime.heat import HEAT_STORY_PROTAGONIST, HEAT_STORY_VOTER, _add_heat
from runtime.identity import get_full_name, is_self
from runtime.members import _find_in_profiles, _find_user_in_msgs, _random_member_uid
from runtime.obs import log
from runtime.text import _is_stop_word, _squash
import state_store

# （STORY_MAX_ROUND / STORY_VOTE_SECONDS 随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "is_story_start_cmd", "is_story_end_cmd", "start_story", "_check_story",
    "_finish_story", "_advance_story", "_record_story_vote",
    "_story_vote_result", "STORY_MAX_ROUND", "STORY_VOTE_SECONDS",
]
''',
    funcs=["is_story_start_cmd", "is_story_end_cmd", "start_story",
           "_check_story", "_finish_story", "_advance_story",
           "_record_story_vote", "_story_vote_result"],
    consts=["STORY_MAX_ROUND", "STORY_VOTE_SECONDS"],
)


# ---------------------------------------------------------------- 第 13 片：公演
SLICES["perf"] = Slice(
    module="runtime/perf.py",
    header='''"""公演（P8 第 4 步从 bot.py 搬出）。

公演是这一整套里**唯一会用后台任务续跑**的玩法：`_perf_end_bg` 在结束广播之后
继续生成小说，所以它跨了"主循环结束"这个边界 —— 搬的时候要保证
后台任务里引用的名字全都还在（这正是迁移工具的静态依赖检查在防的事）。
"""
import asyncio
import random
import time
from datetime import datetime, timedelta

from brains import (generate_perf_comment, generate_perf_ending,
                    generate_perf_enrich, generate_perf_insult,
                    generate_perf_novel, generate_perf_novel_chunked,
                    generate_person_summary)
from dzmm import get_messages_all, get_messages_since, get_user
from wx_alert import send_weixin
from runtime import ctx
from runtime.group_state import _get_perf_state, _set_perf_state
from runtime.heat import _add_heat
from runtime.identity import get_full_name, get_name, is_self, self_names
from runtime.members import (_extract_msgs, _find_uid_by_name,
                             _find_user_in_msgs, _random_member_uid)
from runtime.novel import _extract_novel_title, _novel_save_path
from runtime.obs import log
from runtime.text import _is_stop_word, _squash
import state_store

# （OWNER_UID 随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "_detect_perf_query", "summarize_person_perf", "is_perf_start_cmd",
    "is_perf_end_cmd", "_get_perf_content", "_get_perf_content_full",
    "_perf_heat_target", "_perf_m_name", "_perf_send", "_handle_perf_start",
    "_perf_end_bg", "_handle_perf_end", "_check_perf_interact", "OWNER_UID",
]
''',
    funcs=["_detect_perf_query", "summarize_person_perf", "is_perf_start_cmd",
           "is_perf_end_cmd", "_get_perf_content", "_get_perf_content_full",
           "_perf_heat_target", "_perf_m_name", "_perf_send",
           "_handle_perf_start", "_perf_end_bg", "_handle_perf_end",
           "_check_perf_interact"],
    consts=["OWNER_UID"],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()")],
)

# ---------------------------------------------------------------- 第 14 片：发骚 / 场景
SLICES["fasao"] = Slice(
    module="runtime/fasao.py",
    header='''"""发骚骰子与场景扮演（P8 第 4 步从 bot.py 搬出）。

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
from profiles import get_profile
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

# （FASAO_TIMEOUT / SCENES / POSITIONS / EXTRAS / XP_BIAS 随搬运一起进来，
#   见本 slice 的 consts —— 在 header 里再写一遍会变成重复定义）

__all__ = [
    "is_fasao_cmd", "fasao", "is_scene_cmd", "_parse_scene_target", "scene",
    "is_fasao_end_cmd", "_check_fasao_timeout", "end_fasao", "_pick_combo_by_xp",
    "FASAO_TIMEOUT", "SCENES", "POSITIONS", "EXTRAS", "XP_BIAS",
]
''',
    funcs=["is_fasao_cmd", "fasao", "is_scene_cmd", "_parse_scene_target", "scene",
           "is_fasao_end_cmd", "_check_fasao_timeout", "end_fasao",
           "_pick_combo_by_xp"],
    consts=["FASAO_TIMEOUT", "SCENES", "POSITIONS", "EXTRAS", "XP_BIAS"],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()")],
)

# ---------------------------------------------------------------- 第 15 片：调教
SLICES["diaojiao"] = Slice(
    module="runtime/diaojiao.py",
    header='''"""调教（P8 第 4 步从 bot.py 搬出）。

**这是 5 个状态机里最后一个、也是最少动的一个。**

`is_dj_command_cmd` 刻意**不搬**：它不是调教的一部分，而是**总分发口** ——
它调用另外 4 个状态机的 20 个谓词函数（`is_fasao_cmd` / `is_perf_start_cmd` /
`is_vote_start_cmd` / `is_poem_cmd` / `is_recall_cmd` …）。
把它一起搬出去会制造 31 条回指，只是把文字换个位置，不产生任何解耦。
"""
import time
from datetime import datetime, timedelta

from brains import generate_diaojiao, generate_diaojiao_end
from profiles import get_profile
from runtime import ctx
from runtime.fasao import _parse_scene_target
from runtime.group_state import _get_dj_state, _set_dj_state
from runtime.heat import HEAT_DIAOJIAO_ROUND, _add_heat
from runtime.identity import get_full_name
from runtime.members import _archive_text_from_profile, _member_profile_text
from runtime.memory import _retrieve_memory
from runtime.obs import log
from runtime.text import _is_stop_word

# （DJ_PARTICIPATE_HINT 随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "is_dj_end_cmd", "_dj_target_text", "_dj_end_text", "is_diaojiao_cmd",
    "start_diaojiao", "_check_diaojiao", "DJ_PARTICIPATE_HINT",
]
''',
    funcs=["is_dj_end_cmd", "_dj_target_text", "_dj_end_text", "is_diaojiao_cmd",
           "start_diaojiao", "_check_diaojiao"],
    consts=["DJ_PARTICIPATE_HINT"],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()")],
)


# ---------------------------------------------------------------- 工具
def read_raw(p: Path) -> str:
    return p.read_bytes().decode("utf-8")


def to_lf(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def norm_src(s: str) -> str:
    return to_lf(s).strip()


def span(node) -> tuple:
    """节点的**完整**行范围（含装饰器）。"""
    start = node.lineno
    for d in getattr(node, "decorator_list", []) or []:
        start = min(start, d.lineno)
    return start, node.end_lineno


def block_of(lines, node) -> str:
    s, e = span(node)
    return "".join(lines[s - 1:e])


def bound_names(module_src: str) -> set:
    tree = ast.parse(module_src)
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
    return out


def needed_names(src: str) -> set:
    """一段源码（函数**或常量赋值**）引用的、**非局部**的名字。

    `global X` 要把 X 从局部里摘掉 —— 否则 `global _T; _T = ...` 会被当成
    局部赋值，一个"搬走了别人还在用的全局"的错就漏过去了。

    ⚠️ 常量也必须走这里：`TREND_CIDS = {PRIMARY_CID}` 的值里引用了另一个
    模块级常量。只查函数不查常量，第一版就把 `PRIMARY_CID` 漏过去了，
    搬到新模块后 **import 期** 直接 NameError（比函数体里的更早、更响）。
    """
    node = ast.parse(src).body[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # 常量赋值：没有局部作用域，所有 Load 都是模块级引用
        return {n.id for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    local = set()
    for a in (node.args.args + node.args.kwonlyargs + node.args.posonlyargs):
        local.add(a.arg)
    if node.args.vararg:
        local.add(node.args.vararg.arg)
    if node.args.kwarg:
        local.add(node.args.kwarg.arg)
    globals_declared = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            local.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)) and n is not node:
            local.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            local.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                local.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.comprehension) and isinstance(n.target, ast.Name):
            local.add(n.target.id)
        elif isinstance(n, ast.Lambda):
            # lambda 的参数是它自己的局部名。不摘掉的话
            # `sorted(..., key=lambda kv: kv[1])` 里的 kv 会被误报成"缺名字"。
            for a in (n.args.args + n.args.kwonlyargs + n.args.posonlyargs):
                local.add(a.arg)
            if n.args.vararg:
                local.add(n.args.vararg.arg)
            if n.args.kwarg:
                local.add(n.args.kwarg.arg)
        elif isinstance(n, ast.Global):
            globals_declared |= set(n.names)
    local -= globals_declared
    return {n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id not in local} | globals_declared


def _collect_bindings(stmts, bound: set) -> None:
    """收集**模块级**绑定；进 if/try/for/with，但不进 def/class（那是局部）。"""
    for st in stmts:
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            for al in st.names:
                bound.add((al.asname or al.name).split(".")[0])
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(st.name)
        elif isinstance(st, ast.Assign):
            for t in st.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        bound.add(n.id)
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            bound.add(st.target.id)
        elif isinstance(st, (ast.If, ast.For, ast.AsyncFor, ast.While,
                             ast.With, ast.AsyncWith)):
            _collect_bindings(st.body, bound)
            _collect_bindings(getattr(st, "orelse", []), bound)
        elif isinstance(st, ast.Try):
            _collect_bindings(st.body, bound)
            _collect_bindings(st.orelse, bound)
            _collect_bindings(st.finalbody, bound)
            for h in st.handlers:
                if h.name:
                    bound.add(h.name)
                _collect_bindings(h.body, bound)


def unresolved_in_bot(module_src: str) -> dict:
    """反向闸门：搬完之后，**剩下的 bot.py 自己**还缺不缺模块级名字。

    没有这道检查，"把一个别人还在用的常量搬走"会一路绿灯写盘，
    然后在线上第一次调用时报 NameError。
    """
    tree = ast.parse(module_src)
    bound = set(dir(builtins))
    _collect_bindings(tree.body, bound)
    bad = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        miss = sorted(needed_names(ast.get_source_segment(module_src, node)
                                   or ast.unparse(node)) - bound)
        if miss:
            bad[node.name] = miss
    return bad



def apply_rewrites(src: str, rewrites) -> str:
    for old, new in rewrites:
        src = src.replace(old, new)
    return src


def main(argv) -> int:
    if len(argv) < 2 or argv[1] not in SLICES:
        print("用法: python tools/p8s4_move.py {{{}}} [--dry]".format(
            "|".join(sorted(SLICES))))
        return 2
    name, dry = argv[1], "--dry" in argv
    sl = SLICES[name]

    raw = read_raw(BOT)
    lines = raw.splitlines(keepends=True)
    tree = ast.parse(raw)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            consts[n.targets[0].id] = n
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            consts[n.target.id] = n

    # ---------- 闸门 0：存在性 / 防重复执行 ----------
    target = ROOT / sl.module
    if target.exists():
        if not sl.append:
            print("[中止] {} 已存在（要往里追加请设 append=True）".format(sl.module))
            return 1
        if "P8 第 4 步" not in read_raw(target):
            print("[中止] {} 存在但不是本步生成的，不敢追加".format(sl.module))
            return 1
    elif sl.append:
        print("[中止] append=True 但 {} 不存在".format(sl.module))
        return 1
    for label, want, pool in (("函数", sl.funcs, funcs), ("常量", sl.consts, consts)):
        miss = [x for x in want if x not in pool]
        if miss:
            print("[中止] bot.py 里找不到这些{}: {}".format(label, miss))
            return 1

    # ---------- 组装新模块 ----------
    blocks = {f: norm_src(apply_rewrites(block_of(lines, funcs[f]), sl.rewrites))
              for f in sl.funcs}
    cblocks = {c: norm_src(apply_rewrites(block_of(lines, consts[c]), sl.rewrites))
               for c in sl.consts}
    if sl.append:
        base = to_lf(read_raw(target)).rstrip("\n")
        module_src = base + "\n\n\n" + "\n\n\n".join(
            [cblocks[c] for c in sl.consts] + [blocks[f] for f in sl.funcs]) + "\n"
    else:
        parts = [to_lf(sl.header).rstrip("\n")]
        for c in sl.consts:
            parts.append(cblocks[c])
        for f in sl.funcs:
            parts.append(blocks[f])
        module_src = "\n\n\n".join(parts) + "\n"

    # ---------- 闸门 1：新模块里的未解析依赖 ----------
    have = bound_names(module_src) | set(dir(builtins))
    # 头部自己又定义了一遍 consts 里的常量 = 同一个常量在模块里出现两次。
    # 文本相同时看不出问题（后者覆盖前者，值也对），但"改了一边不生效"这类坑
    # 就埋下了 —— `DJ_PARTICIPATE_HINT` 抄漏几个字，正是被审计的逐字比对抓出来的。
    if sl.consts and not sl.append:
        dup = sorted(set(sl.consts) & bound_names(sl.header))
        if dup:
            print("[中止] header 里已经定义了这些常量，别再列进 consts（会重复定义）: {}"
                  .format(dup))
            return 1
    unresolved = {}
    for f in sl.funcs:
        bad = sorted(needed_names(blocks[f]) - have)
        if bad:
            unresolved[f] = bad
    for c in sl.consts:
        bad = sorted(needed_names(cblocks[c]) - have)
        if bad:
            unresolved["{}（常量值）".format(c)] = bad
    print("目标模块      : {}（{} 行，{} 函数，{} 常量）".format(
        sl.module, len(module_src.splitlines()), len(sl.funcs), len(sl.consts)))
    if sl.rewrites:
        for old, new in sl.rewrites:
            hit = [f for f in sl.funcs if old in block_of(lines, funcs[f])]
            # 防二次应用：新文本已经在了就别再替一遍（否则 `ctx.ctx.get_...`）
            dup = [f for f in sl.funcs if new in block_of(lines, funcs[f])]
            if dup:
                print("[中止] 改写 {!r} -> {!r} 已经应用过了: {}".format(old, new, dup))
                return 1
            print("声明式改写    : {!r} -> {!r}（命中 {}）".format(
                old, new, ", ".join(hit) or "无"))
    print("未解析依赖    : {}".format("无" if not unresolved else unresolved))
    if unresolved:
        print("[中止] 搬出去会 NameError（只在调用时才炸）。先补 import，或先搬它的依赖。")
        return 1

    # ---------- 计算删除集 ----------
    drop = set()
    for f in sl.funcs:
        s, e = span(funcs[f])
        drop.update(range(s - 1, e))
    for c in sl.consts:
        s, e = span(consts[c])
        drop.update(range(s - 1, e))
    hay = to_lf(raw)
    for text in sl.remove_texts:
        t = to_lf(text).strip("\n")
        n = hay.count(t)
        if n != 1:
            print("[中止] 待删文本出现 {} 次（要求 1）: {!r}".format(n, t[:60]))
            return 1
        start_line = hay.count("\n", 0, hay.index(t))
        drop.update(range(start_line, start_line + t.count("\n") + 1))

    kept = [(i, ln) for i, ln in enumerate(lines) if i not in drop]
    boundary = {p for p, (i, _) in enumerate(kept) if (i - 1) in drop or (i + 1) in drop}

    # 只把**被删出来的** >=3 连续空行收成 2 行（不碰原文里的空行）
    out = [ln for _, ln in kept]
    run = []
    for pos in range(len(out) - 1, -1, -1):
        if out[pos].strip() == "":
            run.append(pos)
            continue
        if len(run) >= 3 and any(r in boundary for r in run):
            for r in run[:-2]:
                out[r] = None
        run = []
    if len(run) >= 3 and any(r in boundary for r in run):
        for r in run[:-2]:
            out[r] = None
    bot_src = "".join(ln for ln in out if ln is not None)

    # ---------- bot.py 补 import（re-export：45 个调用点一处不改）----------
    if sl.export_in_bot:
        modname = sl.module[:-3].replace("/", ".") if sl.module.endswith(".py") \
            else sl.module.replace("/", ".")
        blines = bot_src.splitlines(keepends=True)
        # 锚点用"最后一个顶层 import 行"，不用名字匹配：
        # `import state_store` 在 bot.py 里出现两次（模块级 + _log_runtime_status 里），
        # 按名字匹配曾把 import 插进了那个函数体，直接语法错误。
        hits = [i for i, ln in enumerate(blines)
                if ln[:1] not in (" ", "\t")
                and (ln.startswith("import ") or ln.startswith("from "))]
        if not hits:
            print("[中止] bot.py 里找不到任何顶层 import 行")
            return 1
        eol = "\r\n" if blines[0].endswith("\r\n") else "\n"
        blines.insert(hits[-1] + 1, "from {} import {}  # P8 第 4 步：搬到 {}{}".format(
            modname, ", ".join(sl.funcs + sl.consts), sl.module, eol))
        bot_src = "".join(blines)

    # ---------- 闸门 1.5：改完的 bot.py 必须还能解析 + 自己不缺名字 ----------
    # 便宜且高价值：import 插错位置这类错误在这里就挡住，不会写进文件。
    try:
        ast.parse(bot_src)
    except SyntaxError as e:
        print("[中止] 改写后的 bot.py 语法错误（第 {} 行: {}）".format(e.lineno, e.msg))
        print("       出问题的行: {!r}".format(
            bot_src.splitlines()[e.lineno - 1].strip()[:80] if e.lineno else ""))
        return 1
    # 反向闸门：搬走的东西别人还在用吗？
    orphan = unresolved_in_bot(bot_src)
    if orphan:
        print("[中止] 搬完之后 bot.py 自己有名字解析不了（线上首次调用即 NameError）:")
        for fn, miss in list(orphan.items())[:8]:
            print("    {} 缺 {}".format(fn, miss))
        return 1

    print("bot.py        : {} 行 -> {} 行（-{}）".format(
        len(lines), len(bot_src.splitlines()),
        len(lines) - len(bot_src.splitlines())))
    if dry:
        print("\n--- 新模块前 34 行 ---")
        print("\n".join(module_src.splitlines()[:34]))
        return 0

    # ---------- 写盘 ----------
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(module_src.encode("utf-8"))
    BOT.write_bytes(bot_src.encode("utf-8"))
    print("已写入        : {}{}".format(
        sl.module, "（追加）" if sl.append else ""))

    # ---------- 闸门 2：搬完逐个比对源码文本 ----------
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import bot
    importlib.reload(bot)
    mod = importlib.import_module(sl.module[:-3].replace("/", "."))

    bad = []
    for f in sl.funcs:
        want = blocks[f]
        obj = getattr(bot, f, None)
        if obj is None:
            bad.append((f, "bot 上没有这个名字（re-export 失败）"))
        elif getattr(mod, f, None) is not obj:
            bad.append((f, "bot 和 {} 上不是同一个对象".format(sl.module)))
        elif norm_src(inspect.getsource(obj)) != want:
            bad.append((f, "源码文本与搬之前不一致"))
    print()
    print("比对 {} 个函数：{}".format(
        len(sl.funcs), "全部一致" if not bad else "**不一致**"))
    for f, why in bad[:8]:
        print("    {} -> {}".format(f, why))
    if bad:
        print("[失败] 用 git checkout bot.py 重来")
        return 1

    refs = [ln.strip() for ln in module_src.splitlines()
            if ln.strip().startswith(("import bot", "from bot import"))]
    print("回指 bot.py   : {} 处{}".format(
        len(refs), "" if not refs else " -> " + str(refs)))
    print()
    print("OK 搬完")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
