"""功能注册表（P12-A）：**68 个功能的声明式清单** + 唯一的开关判定点 + 状态复位。

### 为什么要有这个模块

盘点（`docs/盘点-群友功能.md`）的结论是：**今天能关的功能只有 4 个布尔 + ROOMS +
`/吐槽开|关` + 9 个环境变量**，而骰子/发骚/调教/投票/剧情/公演/羞辱/写诗/算命/
趋势/回顾/注册/记忆**一层开关都没有**。要让运维在页面上按群开关它们，先得有
一份"功能到底有哪些"的**机器可读清单**，否则每加一个开关就要在 `bot.py` 里
再写一遍同样的判断，最后必然出现"关掉了但某个入口忘了判"。

### 五条硬性设计原则

1. **代码是默认值，配置只写覆盖。** 本模块的 `default` 就是"今天的行为"，
   没有配置文件时行为**逐字节不变**（P12-A 的验收标准）。
2. **开关判定只有一个入口**：`on(cid, key)`。各功能入口只调它，
   **不许**自己写 `if xxx_enabled`（否则判定点会散掉，无法审计也无法测试）。
3. **关掉必须能复位**：五个玩法状态机都用"`active` 为真就屏蔽其他指令"做互斥，
   而且**都没有 reset 接口**（公演连超时都没有）—— 所以"中途关掉功能"
   的真实后果是**把这个群弄哑**。`reset_feature()` 就是为这条准备的。
4. **只登记"有真实触发 + 真实效果"的功能**：死代码不许进表
   （盘点报告 §6：`defend_nuo`、`_get_perf_content_full`、`should_reply` 等）。
5. **接线进度要显式**：`stage="planned"` 表示"登记了但闸门还没接"。
   测试钉住"planned 的条目只许减少"（`test_feature_registry.py`），
   免得"以为配了、其实没接线"——这正是 P12-0 刚修过的那类 bug。

### 与配置的关系（P12-C 才接）

P12-A 只做"接线 + 默认值"，**不读配置文件**：`set_overrides()` 由 `bot.py`
在启动时注入（P12-C 起，值来自 `config.json`）。所以本期**零行为变化**，
与 P4 时期"接线但不上线"（`DZMM_BOT_ROUTING` 默认 off）是同一个套路。
"""
import os
import time
from dataclasses import dataclass, field, replace

__all__ = [
    "Feature", "Param", "FeatureError", "FEATURES", "CATEGORIES",
    "get", "keys", "by_category", "on", "why_off", "set_overrides",
    "overrides", "reset_feature", "reset_all", "summary", "registry",
    "WIRE_STAGES",
]


class FeatureError(ValueError):
    """功能表的用法错误（未知 key 等）。**要让它冒出来**，别静默退回默认。"""


# 接线阶段：wired = 闸门已接；planned = 登记了但还没接（本模块建立时先这样标）
WIRE_STAGES = ("wired", "planned")


@dataclass(frozen=True)
class Param:
    """一个可调参数（P12-B 才填值；这里先把**结构**定下来）。

    `tier`：必配（露在功能卡片上）/ 可配（折进"高级"）/ 内部（不上页面）。
    `lo`/`hi`：硬边界，页面与校验都用它拦危险值（例如冷却 0 会刷屏撞风控）。
    """
    key: str
    label: str
    default: object
    unit: str = ""
    lo: object = None
    hi: object = None
    tier: str = "必配"
    note: str = ""


@dataclass(frozen=True)
class Feature:
    """一个功能。`key` 是稳定标识（配置里写它），`label` 只给人看。"""
    key: str
    label: str
    category: str
    default: bool = True
    # 关掉它时要清理的**群状态键**（见 runtime/group_state.py 与 runtime/dice.py）。
    # "dice_game" 是**顶层键**（不按群），关掉骰子会影响所有群 —— 单独标注。
    resets: tuple = ()
    stage: str = "planned"
    note: str = ""
    params: tuple = field(default_factory=tuple)


# ---------------------------------------------------------------- 注册表（65 项）
#
# 顺序 = docs/功能与人设配置方案.md §9.1 的顺序，方便两边对照。
# default 一律取"今天实际的行为"：只有恶魔吐槽/冷场吐槽今天是关的
# （它们还额外被群状态里的 `devil_enabled` 管着，那是内层门，保持不变）。

CATEGORIES = (
    ("game", "互动游戏"),
    ("content", "公演与内容生成"),
    ("people", "人物与档案"),
    ("screen", "认证与筛查"),
    ("schedule", "定时与自动"),
    ("room", "房间玩法"),
    ("alert", "告警"),
)

_FEATURES_RAW = (
    # ---- A. 互动游戏（15）
    Feature("dice", "吹牛骰子", "game",
            resets=("dice_game",), note="群里所有人共享一局；状态是**顶层键**，"
            "关掉不清会让所有群都开不了新局", params=(
                Param("dice.timeout", "弃权超时", 180, "秒", 30, 3600,
                      note="无动作多久算弃权跳过"),
            )),
    Feature("dice_timeout", "骰子超时跳过", "game", note="180s 无动作自动跳过"),
    Feature("scene", "发骚/涩涩场景", "game", resets=("fasao", "last_action"),
            params=(
                Param("scene.timeout", "无互动超时", 900, "秒", 60, 7200,
                      note="多久没人接就自动结束（防状态卡死）"),
                Param("scene.max_tokens", "正文长度上限", 1800, "token", 300, 4000,
                      note="原先写死 700，实测会把正文掐在半句话上"
                           "（2026-09-27 屋主报「发骚有截断/内容太少」）；"
                           "被截断时还会自动续写接上"),
            )),
    Feature("scene_continue", "场景续写", "game"),
    Feature("scene_rewrite", "场景重写", "game"),
    Feature("scene_end", "场景结束", "game"),
    Feature("diaojiao", "调教", "game", resets=("diaojiao",),
            note="还兼着「未识别斜杠指令」的兜底路径，关掉后那些指令会落到兜底聊天",
            params=(
                Param("diaojiao.timeout", "无动静超时", 300, "秒", 30, 7200),
            )),
    Feature("diaojiao_idle", "调教冷场自续", "game", params=(
        Param("diaojiao.idle_seconds", "冷场多久自编一条", 30, "秒", 5, 600),
    )),
    Feature("vote", "投票", "game", resets=("vote",), params=(
        Param("vote.default_seconds", "默认时长", 120, "秒", 10, 86400),
    )),
    Feature("story", "剧情接龙", "game", resets=("story",), params=(
        Param("story.max_round", "最多轮数", 6, "轮", 1, 50),
        Param("story.vote_seconds", "每轮投票时长", 60, "秒", 5, 3600),
    )),
    Feature("heat", "发情值系统", "game", params=(
        Param("heat.scene_pick", "抽中发骚骰", 10, "分", 0, 100),
        Param("heat.scene_continue_target", "发骚续写·目标", 10, "分", 0, 100),
        Param("heat.scene_continue_sender", "发骚续写·发起人", 10, "分", 0, 100),
        Param("heat.insult_target", "被羞辱", 15, "分", 0, 100),
        Param("heat.insult_sender", "羞辱续写·发起人", 10, "分", 0, 100),
        Param("heat.fasao_pick", "被点名发骚", 15, "分", 0, 100),
        Param("heat.diaojiao_round", "被调教一轮", 20, "分", 0, 100),
        Param("heat.vote_starter", "投票发起人", 8, "分", 0, 100),
        Param("heat.vote_voter", "投票参与者", 5, "分", 0, 100),
        Param("heat.story_voter", "剧情投票者", 8, "分", 0, 100),
        Param("heat.story_protagonist", "剧情主角", 15, "分", 0, 100),
        Param("heat.climax_threshold", "高潮阈值", 100, "分", 1, 1000),
        Param("heat.climax_reset", "高潮后扣减", 100, "分", 0, 1000),
        Param("heat.judge_interval", "批量评定间隔", 1800, "秒", 60, 86400),
        Param("heat.judge_trigger", "≥此值立即重评", 80, "分", 1, 1000),
        Param("heat.pending_max", "待评缓冲条数", 10, "条", 1, 200),
    )),
    Feature("heat_climax", "高潮事件播报", "game"),
    Feature("devil_roast", "恶魔吐槽（主动冒话）", "game",
            note="**两层门**：这一层是「允不允许」（默认允许）；是否真的吐槽还由群状态 "
                 "`devil_enabled`（群内 `/吐槽开|关`）决定 —— 配置关了就是彻底关，"
                 "群内也打不开",
            params=(
                Param("devil.base_prob", "普通闲聊触发概率", 0.03, "", 0.0, 1.0),
                Param("devil.sensitive_prob", "命中敏感词概率", 0.10, "", 0.0, 1.0),
                Param("devil.hot_prob", "热闹时概率", 0.08, "", 0.0, 1.0),
                Param("devil.cooldown", "冷却", 600, "秒", 0, 86400),
                Param("devil.about_person_prob", "针对具体成员的概率", 0.5, "", 0.0, 1.0),
            )),
    Feature("devil_cold", "冷场吐槽", "game",
            note="同 `devil_roast`：这一层是「允不允许」，实际触发还看群状态里的 "
                 "`devil_enabled` 开关（两者共用同一个群内开关）",
            params=(
                Param("devil.cold_prob", "每次 roll 概率", 0.3, "", 0.0, 1.0),
                Param("devil.cold_threshold", "冷清多久后开始", 7200, "秒", 60, 86400),
                Param("devil.cold_cooldown", "重试周期", 1800, "秒", 60, 86400),
                Param("devil.cold_quiet_hour", "几点前静默", 8, "点", 0, 23),
            )),
    Feature("insult", "羞辱/毒舌", "game"),
    # P12-AC（2026-09-27 屋主）：`@` 时的问答回复。
    # 以前是"没有开关的隐式行为"，而且喂的上下文只有 4 条、还**不含机器人自己
    # 说过的话**（没有对话连续性）；回复长度也写死 400 token + 提示词要求"1-3 句"。
    # 现在：带多少条上下文、回复多长都是按群可调参数，并且可以整块关掉。
    Feature("chat", "问答回复（@ 时回答）", "people", params=(
        Param("reply.max_tokens", "回复长度上限", 800, "token", 100, 3000,
              note="太小会把话说到一半就断（400 时常见）；太大=话太长"),
        Param("reply.context_msgs", "带多少条上下文", 20, "条", 4, 60,
              note="含机器人自己说过的话，才有对话连续性；调大=更连贯但更慢/更贵"),
    )),
    # P12-AE（2026-09-27 屋主）：别人**右键点击回复**我那条消息时，也自动接话。
    # 平台把被引用的那条塞在入站消息的 `content.reference`
    # （`{id, sentBy, content:{text,type}}`，2026-09-27 线上实测 40 条里有 3 条），
    # 于是"他回复的是不是我"这件事**有确凿依据**，不用猜文本。
    # ⚠️ 只管"要不要接话"：官方 Bot API **没有**"把回复挂到某条上"的出站字段
    #    （`tools/probe_reply_capability.py` 探过 15 个候选字段，全无校验）。
    # 默认**关**＝今天的行为（只有 @ 才答）；要在哪个群用，按群把开关打开。
    Feature("quote_reply", "别人引用回复我也接话", "people", default=False,
            note="关＝只有 @ 才答（今天的行为）；开＝别人右键回复我的消息也答"),
    # P12-AK（2026-09-29 屋主）：顶层茶水间那种"群里还跑着别家 bot 的游戏"的群。
    # 群友发的 `/开始摸鱼躲藏`、`/记忆考核`、`/答案 xxx`、`/继续` 全是**别家 bot 的语法**；
    # 我们一接进这种群，主循环会把它们当成"给我的指令"，走到兜底就回一句"这个我不会" ——
    # 779 人的群里每条游戏指令都回一句，是灾难。
    # 关掉它 = 这个群**不接任何 `/` 指令**（只留 @ 问答那条路）。默认开＝今天的行为。
    Feature("slash_cmds", "接群内斜杠指令", "people",
            note="关＝这个群的 /xxx 一律不理（让给别家 bot）；开＝照旧处理"),
    # P12-AL（2026-09-29 屋主："最后，只有 @ 小小糯 的时候才触发回复"）：
    # 关掉之后，这个群里**任何没 @ 到她的消息都不处理**（连指令、房间触发、定时播报
    # 一并免掉）。顶层茶水间那种 779 人的群要的就是这个保证。
    # ⚠️ 别在认证群开：那边的骰子/发骚/排行/问候本来就靠非 @ 消息触发，开了等于全废。
    Feature("at_only", "只 @ 才回复", "people", default=False,
            note="开＝这个群只有被 @ 才说话；关＝照旧（今天的行为）"),

    # ---- B. 公演与内容生成（12）
    Feature("perf", "公演", "content", resets=("perf",),
            note="今天**没有超时**：中途打断会让该群永久哑掉（P12-A 补超时）"),
    Feature("perf_enrich", "公演实时互动", "content", params=(
        Param("perf.enrich_seconds", "互动间隔", 600, "秒", 30, 7200,
              note="两次实时互动之间至少隔多久"),
    )),
    Feature("perf_review", "公演小说审核", "content", note="只有 OWNER 能审"),
    Feature("perf_novel", "公演改写成小说", "content", params=(
        Param("perf.ending_wait", "收尾等待", 20, "秒", 0, 300,
              note="公演结束后等参与者补台词的时长"),
        Param("perf.novel_chunk", "每次生成条数", 5, "条", 1, 50),
        Param("perf.novel_max_chunks", "单次最多分块", 12, "块", 1, 24,
              note="调大堵死主循环（2026-09-14 堵过 37 分钟）"),
    )),
    Feature("poem", "写诗", "content"),
    Feature("fortune", "算命/今日运势", "content"),
    Feature("horoscope", "星座运势", "content"),
    Feature("trend", "7 天运势趋势", "content"),
    Feature("recap", "缺勤回顾/补课", "content", params=(
        Param("recap.cooldown", "同窗口冷却", 30, "秒", 0, 3600),
        Param("recap.max_msgs", "喂给 AI 的条数", 400, "条", 20, 1000),
        Param("recap.max_hours", "回溯窗口上限", 48, "小时", 1, 168),
        # P12-AK：问"我不在的时候发生了什么"却没指定时间段时的回退窗口。
        # 原来是写死的 6 小时（`runtime/recap.py`），现在按群可调 ——
        # 有的群一小时几百条，6 小时的转录能把提示词塞满。
        Param("recap.default_hours", "默认窗口", 6, "小时", 1, 48,
              note="没写时间段时回看多久；群越热闹越该调小"),
        # P12-AK：recap 要**忽略的内容**关键词（逗号分隔，空 = 不过滤）。
        # 用途：群里跑着别家 bot 的游戏（打卡 / 摸鱼躲藏 / 记忆考核…），那些
        # "XX，躲猫猫开始…""第 3 级通过…""恭喜 XX 完成第 5 级记忆考核" 全是噪音，
        # 实测能占到窗口内消息的 **39%**（2026-09-29 顶层茶水间取样）。
        # 按群配（`cid|recap.ignore_kw`），所以只影响配了的那个群。
        Param("recap.ignore_kw", "忽略的内容关键词", "", "",
              note="逗号分隔；命中即不进转录、也不算『有人找你』"),
    )),
    Feature("summary", "群聊总结", "content",
            note="判定是子串匹配且很宽（含「情况/记录/发生/讨论/整理」），"
                 "而且排在写诗/羞辱/算命之前"),
    Feature("rank_heat", "骚度排行（手动）", "content"),
    Feature("profile_card", "未注册者介绍", "content"),

    # ---- C. 人物与档案（11）
    Feature("register", "注册档案", "people"),
    Feature("query", "查人", "people"),
    # 注：盘点里曾把 `is_rewrite_cmd` 归成"改写档案"，实际上它**只服务场景重写**
    # （`/重写 /重新生成 /重来 /重新写`，见 bot.py:1492），已经由 `scene_rewrite` 覆盖。
    # 注册表的规矩是"只登记有真实触发 + 真实效果的功能"，所以这里**不设** `rewrite` 条目。
    Feature("archive_build", "档案自动构建", "people", params=(
        Param("profile.scan_max_api", "单次 get_user 上限", 150, "次", 1, 1000),
        Param("profile.notice_max", "单次通知上限", 10, "条", 1, 50),
        Param("profile.roll_msgs", "滚动总结条数", 30, "条", 5, 200),
    )),
    Feature("intro", "自我介绍", "people"),
    Feature("memory_remember", "记住", "people"),
    Feature("memory_recall", "回忆", "people"),
    Feature("memory_report", "每日记忆汇报", "people", params=(
        Param("memory.report_items", "汇报列几条", 8, "条", 1, 30),
        Param("memory.max", "群记忆上限", 100, "条", 10, 1000),
        Param("memory.inject", "问答注入记忆数", 10, "条", 0, 50),
    )),
    Feature("person_summary", "表现查询", "people"),
    Feature("sync", "增量同步", "people", note="手动只有 OWNER；定时 12:05/23:35"),
    Feature("help", "帮助/指南", "people"),

    # ---- D. 认证与筛查（7）
    Feature("screen", "入群筛查总开关", "screen"),
    Feature("screen_tourist", "踢游客", "screen"),
    Feature("screen_gender", "踢男性/未填性别", "screen"),
    Feature("screen_welcome", "入群欢迎语", "screen"),
    Feature("screen_captcha_notice", "撞验证码提示 + noVNC", "screen"),
    Feature("screen_rescreen", "风控解除后补筛查", "screen"),
    Feature("screen_pending", "待筛名单", "screen", resets=("pending_screen",)),

    # ---- E. 定时与自动（9）
    Feature("greet_morning", "早安（08:00）", "schedule", params=(
        Param("schedule.greet_window_min", "补发窗口", 90, "分钟", 0, 720),
    )),
    Feature("greet_noon", "午安（12:00）", "schedule"),
    Feature("greet_evening", "晚安（22:00）", "schedule"),
    Feature("rank_push", "定时骚度排行", "schedule", params=(
        Param("schedule.rank_hours", "整点（逗号分隔）", "12,18", ""),
    )),
    Feature("profile_scan", "每日档案扫描", "schedule", params=(
        Param("schedule.scan_hour", "几点后首扫", 6, "点", 0, 23),
    )),
    Feature("notice_rename", "改名/简介变化播报", "schedule"),
    Feature("heartbeat", "心跳", "schedule"),
    Feature("health_check", "健康巡检 + 续期告警", "schedule"),
    Feature("auto_recover", "cookie 失效自动恢复", "schedule"),

    # ---- F. 房间玩法（7，仅房间群）
    Feature("room_welcome", "新客欢迎（含屋主规矩）", "room"),
    Feature("room_refuse", "客人指令婉拒", "room"),
    Feature("room_order", "点单（酒水/雪茄）", "room",
            params=(
                Param("room.reply_max_tokens", "房间回复上限", 400, "token", 100, 2000,
                      tier="可配",
                      note="房间对话的 max_tokens。玫瑰房人设要求 4～8 句，"
                           "默认 400 容易说到一半被截断（线上给玫瑰房调高）"),
            )),
    Feature("room_flower", "摘花/花瓣", "room"),
    Feature("room_ghost", "红衣女鬼", "room", resets=("rose_ghost",),
            note="3h 兜底只在收到房间消息时检查，冷清房间不自愈"),
    Feature("room_lewd", "调戏三级递进", "room", resets=("rose_strikes",)),
    Feature("room_owner_cmd", "屋主指令", "room"),

    # ---- G. 告警（4）
    Feature("alert_send_fail", "连续发送失败告警", "alert"),
    Feature("alert_cookie", "cookie 失效告警", "alert"),
    Feature("alert_token", "token 过期告警", "alert"),
    Feature("alert_captcha", "撞验证码告警", "alert"),
)

# ---------------------------------------------------------------- 接线进度
#
# P12-A 分批接线：第一批只做**定时/事件类入口**（它们各自有独立函数，
# 不与 49 步命令分派链耦合，风险最低）。命令分派链那一批见文档 §5 的说明 ——
# 那里有几处"落空转发/共享分支"（例如 `/发骚结束` 无进行中时会落到"发起场景"、
# `/选 N` 同时服务投票与剧情），所以闸门必须**逐分支**加，不能用一个前置判定糊过去。
#
# ⚠️ 这份名单与代码是**双向**钉住的：`tests/test_feature_registry.py` 会扫
# bot.py / runtime/*.py 里的 `_feat_on(cid, "key")`，两边不一致就报错。
# 加闸门忘了改这里、或改了这里忘了加闸门，都会当场红。
_WIRED = (
    # --- 第一批（定时/事件类入口，不与命令分派链耦合）---
    "greet_morning", "greet_noon", "greet_evening",   # 定时问候（按群过滤）
    "memory_report",                                  # 午间记忆汇报
    "rank_push",                                      # 12/18 点骚度排行
    "profile_scan",                                   # 每日档案扫描（按群）
    "notice_rename",                                  # 扫描后的改名/简介播报
    "screen_rescreen",                                # 风控解除后补筛查 + 群内提醒
    "alert_send_fail",                                # 连续发送失败告警
    # --- 第二批（命令分派链，逐分支加闸门）---
    "dice", "perf", "perf_review", "story", "vote", "scene", "scene_continue",
    "scene_rewrite", "scene_end", "diaojiao", "help", "memory_remember",
    "memory_recall", "devil_roast", "intro", "sync", "register", "query",
    "trend", "rank_heat", "recap", "summary", "fortune", "poem", "insult", "chat",
    "quote_reply", "slash_cmds", "at_only",
    # --- 第三批（房间玩法 / 入群筛查 / @消息子功能 / 告警 / 自动触发）---
    "room_welcome", "room_refuse", "room_order", "room_flower", "room_ghost",
    "room_lewd", "room_owner_cmd", "horoscope",
    "screen", "screen_tourist", "screen_gender", "screen_welcome",
    "screen_captcha_notice", "screen_pending",
    "person_summary", "profile_card", "archive_build",
    "dice_timeout", "diaojiao_idle", "heat", "heat_climax", "devil_cold",
    "perf_enrich", "perf_novel",
    "alert_cookie", "alert_token", "alert_captcha", "auto_recover",
    "heartbeat", "health_check",
)
FEATURES = tuple(replace(f, stage="wired") if f.key in _WIRED else f
                 for f in _FEATURES_RAW)
_BY_KEY = {f.key: f for f in FEATURES}
assert len(_BY_KEY) == len(FEATURES), "功能 key 重复了"


# ---------------------------------------------------------------- 开关判定

# (cid, key) -> bool。由 bot.py 在启动时注入（P12-C 起来自 config.json）。
_OVERRIDES = {}
# "cid|key" -> 谁把它关掉的（自证/排查用）
_WHY = {}


def _env_all_on() -> bool:
    """`DZMM_FEATURES=off` = 忽略所有开关（全开）。

    这是"配错了能秒退"的那条路：页面上一旦把某个功能关错、群里立刻有反应，
    运维必须能**不依赖页面**把它救回来。
    """
    return (os.environ.get("DZMM_FEATURES") or "on").strip().lower() in \
        ("off", "0", "no", "false")


def get(key: str) -> Feature:
    f = _BY_KEY.get(key)
    if f is None:
        raise FeatureError("未知功能 key={!r}（不在注册表里）".format(key))
    return f


def keys() -> tuple:
    return tuple(f.key for f in FEATURES)


def by_category() -> dict:
    """{category_key: [Feature, ...]}，供页面按分类画矩阵。"""
    out = {c: [] for c, _ in CATEGORIES}
    for f in FEATURES:
        out.setdefault(f.category, []).append(f)
    return out


def set_overrides(mapping) -> None:
    """注入开关覆盖：{"cid|key": bool}。空/None 忽略。"""
    global _OVERRIDES, _WHY
    ov, why = {}, {}
    for k, v in (mapping or {}).items():
        if v is None:
            continue
        ov[str(k)] = bool(v)
        why[str(k)] = "config"
    _OVERRIDES, _WHY = ov, why


def overrides() -> dict:
    return dict(_OVERRIDES)


def on(cid: str, key: str) -> bool:
    """**唯一的开关判定点**。未配置 = 用注册表里的默认值（= 今天的行为）。

    覆盖表的键形状（与 `runtime/tuning.py` **保持一致**）：
      - 按群：`"<cid>|<key>"`
      - 全局：`"<key>"`（配置文件里就是这么写的）
      - 另外兼容 `"*|<key>"` 这种显式全局写法。
    ⚠️ 曾经 features 只认 `"*|<key>"` 而 config 写的是裸 `"<key>"`，
    于是"全局关了某个功能"看起来配了却不生效 —— 端到端测试抓到的。
    """
    f = get(key)                       # 未知 key 必须炸，不许静默放行
    if _env_all_on():
        return True                    # DZMM_FEATURES=off → 全开
    v = None
    if cid:
        v = _OVERRIDES.get("{}|{}".format(cid, key))
    if v is None:
        v = _OVERRIDES.get(key)            # 全局（配置文件的形状）
    if v is None:
        v = _OVERRIDES.get("*|" + key)     # 兼容：显式全局
    return f.default if v is None else bool(v)


def why_off(cid: str, key: str) -> str:
    """它为什么是关的（自证行/日志用）：default / config / env。"""
    if _env_all_on():
        return "env(DZMM_FEATURES=off)"
    if cid and _OVERRIDES.get("{}|{}".format(cid, key)) is not None:
        return "config(本群)"
    if _OVERRIDES.get(key) is not None or _OVERRIDES.get("*|" + key) is not None:
        return "config(全局)"
    return "default"


# ---------------------------------------------------------------- 状态复位

# 顶层状态键（不按群）——关掉时影响所有群，必须单独对待
TOP_LEVEL_KEYS = ("dice_game",)


def reset_feature(cid: str, key: str) -> list:
    """清掉某个功能在**这个群**的进行中状态，返回清掉了什么（人话）。

    为什么必须有：五个玩法状态机都用"`active` 为真就屏蔽其他指令"做互斥，
    而且都没有 reset 接口 —— 所以"中途关掉功能"的真实后果是**把群弄哑**
    （骰子局更狠：状态是顶层键，会让**所有群**都开不了新局）。
    关开关时顺手复位，才等于"把这个功能关掉"而不是"把这个群弄坏"。

    只清进行中的中间态，**不删数据**（档案/记忆/发情值一律不碰）。
    """
    f = get(key)
    done = []
    if not f.resets:
        return done
    import state_store
    for skey in f.resets:
        try:
            if skey in TOP_LEVEL_KEYS:
                cur = state_store.top_get(skey)
                if cur:
                    state_store.top_set(skey, {})
                    done.append("{}（全群共享）".format(skey))
                continue
            cur = state_store.group_get(cid, skey, None)
            if cur:
                state_store.group_set(cid, skey, {})
                done.append(skey)
        except Exception as e:                                 # noqa: BLE001
            done.append("{}（清理失败：{}）".format(skey, str(e)[:40]))
    return done


def reset_all(cid: str) -> list:
    """把这个群所有"需要复位"的功能状态清一遍（页面上的"强制复位"按钮）。"""
    done = []
    for f in FEATURES:
        if f.resets:
            for item in reset_feature(cid, f.key):
                done.append("{}: {}".format(f.key, item))
    return done


def active_states(cid: str) -> list:
    """这个群当前有哪些"进行中"的状态（页面/自证行用）。"""
    out = []
    try:
        import state_store
        for f in FEATURES:
            for skey in f.resets:
                if skey in TOP_LEVEL_KEYS:
                    cur = state_store.top_get(skey)
                else:
                    cur = state_store.group_get(cid, skey, None)
                if isinstance(cur, dict) and cur.get("active"):
                    out.append((f.key, skey))
    except Exception:                                           # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------- 自证/面板

def summary() -> dict:
    """一句话自证：共几个、接线了几个、关掉了几个。"""
    wired = [f for f in FEATURES if f.stage == "wired"]
    off = sorted(f.key for f in FEATURES
                 if not on("", f.key))
    return {"total": len(FEATURES), "wired": len(wired),
            "planned": len(FEATURES) - len(wired),
            "off_by_default": sorted(f.key for f in FEATURES if not f.default),
            "off_now": off, "env_all_on": _env_all_on()}


def registry() -> list:
    """给页面/测试用的完整清单（纯数据，无副作用）。"""
    return [{"key": f.key, "label": f.label, "category": f.category,
             "default": f.default, "stage": f.stage, "resets": list(f.resets),
             "note": f.note,
             "params": [{"key": p.key, "label": p.label, "default": p.default,
                         "unit": p.unit, "lo": p.lo, "hi": p.hi, "tier": p.tier}
                        for p in f.params]}
            for f in FEATURES]
