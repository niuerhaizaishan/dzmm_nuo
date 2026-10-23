"""人设注册表 —— P3-6 自 personas.py 逐字搬入后**汇总**。

只做三件事：按原名 re-export 各人设模块的数据、组装 PERSONAS/PHRASES、
持有群→人设配置（ROOMS/CID_PERSONA）与话术标签（PHRASE_LABELS）。
值与拆分前逐字一致；运行期读口在 personas/__init__.py（re-export）。
"""

from .nuo import DEFAULT_NAME_FULL, DEFAULT_NAME_SHORT, NUO_PHRASES
from .rose_butler import ROSE_BUTLER_EXTRA, ROSE_PHRASES
from .ruojin import RUOJIN_EXTRA, RUOJIN_PHRASES
from .lambda_persona import LAMBDA_CIDS, LAMBDA_EXTRA, LAMBDA_PHRASES


PERSONAS = {
    "lambda": {
        "name_full": "Λʌ",
        "name_short": "Λʌ",
        "extra": LAMBDA_EXTRA,
    },
    "nuo": {  # 默认（所有未注册的群）
        "name_full": DEFAULT_NAME_FULL,
        "name_short": DEFAULT_NAME_SHORT,
        "extra": "",
    },
    "rose_butler": {
        # P12-H：玫瑰房的管家 2026-09-26 改名「薇丝」（群里发消息的 Bot 也叫薇丝 ——
        # 2026-09-27 起平台上的 Bot 名从「薇丝-bot」改成「薇丝」，与这里彻底对齐）。
        # 代码默认值也跟着改 —— 否则配置一旦丢了，房间里自称
        # 「小小糯」、发消息的却是另一个名字，又变回半吊子状态。
        "name_full": "薇丝",
        "name_short": "薇丝",
        "extra": ROSE_BUTLER_EXTRA,
    },
    # P12-T（2026-09-27）：第三个 Bot「若瑾」—— 群里发消息的是她，自称也得是她
    # （否则又是"自称小小糯、发言者是若瑾"的半吊子状态）。
    # 人设基本参考小小糯（基础提示词不动），用 `RUOJIN_EXTRA` 覆盖成**纯 S + 异性恋**。
    "ruojin": {
        "name_full": "若瑾",
        "name_short": "若瑾",
        "extra": RUOJIN_EXTRA,
        # 取向：异性恋。代码靠它做**硬过滤**（光靠提示词压不住"色气挑逗"那种风格，
        # 2026-09-27 实测：对女性目标仍然输出了性羞辱）。
        "orientation": "straight",
    },
}


ROSE_CID = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV_UID = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"       # 屋主
NUONUO_UID = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"  # 操作者（半个主人）

ROOMS = {
    ROSE_CID: {
        "alias": "Rose（505）玫瑰房",
        "persona": "rose_butler",
        # 能下命令的人（屋主 + 操作者）
        "owners": {VV_UID, NUONUO_UID},
        "greet_on_join": True,
        "strike_limit": 3,
        "strike_reset_h": 12,   # 12 小时未再犯 → 计数清零
    },
}

# 群 → 人设
CID_PERSONA = {cid: cfg["persona"] for cid, cfg in ROOMS.items()}
CID_PERSONA.update({cid: "lambda" for cid in LAMBDA_CIDS})


# 话术的中文标签（页面用；新增 key 必须同时补这里，测试会拦住）
PHRASE_LABELS = {
    "unknown_cmd": "看不懂的指令",
    "slash_space": "斜杠后带了空格",
    "mem_saved": "记忆 · 已记住",
    "mem_empty": "记忆 · 一条都没有",
    "mem_list_head": "记忆 · 列表开头",
    "mem_list_more": "记忆 · 列表省略行",
    "mem_ask": "记忆 · 问要记什么（@版）",
    "mem_ask2": "记忆 · 问要记什么（/记住版）",
    "not_found": "查不到这个人",
    "not_found_insult": "查不到这个人（羞辱指令）",
    "chat_decor": "@ 对话补足的装饰符号（空=不补，P12-AH）",
    "sync_only_owner": "非房主触发同步",
    "rank_empty": "排行榜里没人",
    "no_vote": "没有进行中的投票",
    "no_play": "没有进行中的玩法",
    "no_dial": "没有进行中的调教",
    "help_text": "/说明 的正文",
    "intro_text": "新人进群欢迎语",
    "welcome_text": "进群欢迎语 · 认证群版（{name} / {me_full}）",
    "welcome_text_plain": "进群欢迎语 · 中性版（{name} / {me_full}）",
    "intro_photo": "自我介绍配图（图片 URL，空=不发）",
    "query_working": "查档案 · 等待提示",
    "register_working": "注册 · 等待提示",
    "captcha_notice": "撞验证码的群内提示",
    "captcha_recovered": "风控解除的群内提示",
    "perf_start": "公演开场",
    "fortune_tail": "算命 · 兜底那句（{full}/{level}/{concl}）",
    "trend_empty": "运势趋势 · 还没算过（{days}）",
    "recap_fail": "回顾 · 拉取失败",
    # P12-BR e：在没开 `trend` 的群里收到"运势趋势"族指令时的话术
    # （以前会静默给出一张**个人运势卡** —— 指令说的是趋势，给的是个人，属于答错）
    "trend_off": "运势趋势 · 这个群没开（P12-BR）",
    "fasao_not_found": "发骚 · 找不到这个人",
    "fasao_empty": "发骚 · 群里没人可点",
    "fasao_pick": "发骚 · 点中某人（{who}）",
    "fasao_fallback": "发骚 · 生成失败兜底（{who}）",
    "fasao_end": "发骚 · 结束语",
    "scene_default_who": "场景 · 没指定人时的默认称呼",
    "wrong_gender": "异性恋人设 · 目标性别不符时的回绝",
    "insult_prefix_who": "羞辱前缀 · 目标昵称关键词（逗号分隔）",
    "insult_prefix_text": "羞辱前缀 · 要加在最前面的内容",
    "dj_format": "调教 · 格式提示",
    "dj_no_target": "调教 · 找不到目标",
    "dj_start": "调教 · 开场（{who}）",
    "dj_timeout": "调教 · 超时收尾",
    "dj_replied": "调教 · 目标回应后催下一条命令",
    "dj_idle": "调教 · 冷场自续开场白",
    "dj_hint": "调教 · 参与方式提醒",
    "dj_idle_unknown": "调教 · 没在局里时不认识的斜杠指令（留空=不理）",
    "spice_emoji": "正文表情（空格分隔，如 💗 💦 🥵；留空=不撒）",
    "cont_no_content": "续写 · 发骚没有内容可接",
    "cont_no_action": "续写 · 没有上一段",
    "cont_bad_type": "续写 · 上一段类型不对",
    "cont_fail": "续写 · 生成失败",
    "rewrite_idle": "重写 · 没有进行中的发骚",
    "rewrite_no_content": "重写 · 没有可重写的段落",
    "rewrite_fail": "重写 · 生成失败",
    "play_busy_fasao": "@消息 · 发骚进行中的打断提示",
    "play_busy_diaojiao": "@消息 · 调教进行中的打断提示",
    "play_busy_perf": "打断 · 公演进行中（P12-BP）",
    "play_busy_story": "打断 · 剧情进行中（P12-BP）",
    "play_busy_vote": "打断 · 投票进行中（P12-BP）",
    "perf_busy_dj": "公演 · 调教还没结束时想开公演",
    "perf_already": "公演 · 已经在跑",
    "story_already": "剧情 · 已经开着",
    "vote_already": "投票 · 已经在进行",
    "dj_end": "调教 · 结束语",
    "dj_cmd_empty": "调教 · 命令为空",
    "dj_busy": "调教 · 已有进行中的调教",
    "dj_block": "调教 · 进行中屏蔽其他指令",
    "reg_self_only": "注册 · 非房主代注册被拒",
    "reg_not_found": "注册 · 找不到人",
    "reg_exists": "注册 · 已经有档案（{who}）",
    "reg_ok": "注册 · 成功（{who}）",
    "reg_fail": "注册 · 存档失败",
    "sync_working": "同步 · 等待提示",
}


# 这些是不走 AI 的群内文案（错误提示/记忆回执等），原来硬编码在 bot.py 里，
# 在房间群会漏出"小秘书"口吻。这里按人设给一套；缺的 key 自动回落默认人设。
# （P3-6：三份正文各住 nuo.py / rose_butler.py / ruojin.py，这里只做汇总 —— 值逐字不变。）

PHRASES = {
    "lambda": LAMBDA_PHRASES,
    "nuo": NUO_PHRASES,
    "rose_butler": ROSE_PHRASES,
    # P12-T：若瑾那份（纯 S 口吻；只覆盖看得见的那几条，其余沿用默认）
    "ruojin": RUOJIN_PHRASES,
}
