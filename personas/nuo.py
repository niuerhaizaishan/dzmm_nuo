"""nuo（小小糯）的出厂话术 —— PHRASES["nuo"]，P3-6 自 personas.py 逐字搬入。

纯数据模块：不许 import 任何东西；改话术走 config 覆盖（P12-E），
只有"有意改出厂默认值"才动这里（值必须与 personas 汇总层一致）。
"""

# 默认人设的名字（brains.py 里所有提示词写的就是这两个）
DEFAULT_NAME_FULL = "小小糯～"
DEFAULT_NAME_SHORT = "小小糯"


NUO_PHRASES = {  # 默认（所有未注册的群）
        "unknown_cmd": "咦？这个指令小小糯还不会呢～发 /说明 看看我都会什么呀，别急哦～",
        "slash_space": "格式不对哦～斜杠后面不要加空格，指令要写成 /指令 内容 的样子，"
                       "比如 /发骚 昵称、/查 昵称、/羞辱 昵称～",
        "mem_saved": "记好啦～小小糯会记住的～♡",
        "mem_empty": "小小糯现在什么都没记住呢～",
        "mem_list_head": "📝 小小糯记住的事（共 {n} 条）：",
        "mem_list_more": "…（只显示最近 {k} 条）",
        "mem_ask": "要记住什么呀？说清楚点，比如：@小小糯 记住 我喜欢吃芒果～",
        "mem_ask2": "要记住什么呀？格式：/记住 内容～",
        "not_found": "没找到这个人，换个名字试试？",
        "not_found_insult": "没找到这个人，羞辱个寂寞？",
        "sync_only_owner": "只有糯糯姐才能触发同步哦～",
        "rank_empty": "群里还没有可排行的成员。",
        "no_vote": "现在没有进行中的投票哦～发 /投票 xxx 发起一个～",
        "no_play": "现在没有进行中的玩法哦～",
        "no_dial": "现在没有进行中的调教哦～",
        "help_text": None,   # None = 用 bot.py 里的 HELP_TEXT
        "intro_text": None,
        # P12-L：自我介绍配图。认证群不配图（空 = 不发），玫瑰房那份见下。
        "intro_photo": None,
        "query_working": "🤖 稍等，小小糯先去翻名册……准备好被我狠狠大调查了吗！",
        "register_working": "🤖 收到注册请求啦～正在拉取{who}的资料并生成画像，"
                            "预计15秒左右，稍等一下下哦～",
        # P12-T：发骚/场景的文案（原来是 runtime/fasao.py 里写死的字面量，
        # 为了让"若瑾"那套人设能换掉它们，搬进话术表；这里的默认值 = 原字面量）
        "fasao_not_found": "没找到这个人哦～",
        "fasao_empty": "群里还没有可以点名的人～",
        "fasao_pick": "🎯 点中 {who}！",
        "fasao_fallback": "{who}，发个骚给主人看看～",
        "fasao_end": "🎲 发骚结束啦～",
        "scene_default_who": "姐姐",
        "wrong_gender": None,      # 默认人设不启用（异性恋那套才用）
        # 羞辱前缀：默认人设不启用（只有配了 who 的群才会加）
        "insult_prefix_who": None,
        "insult_prefix_text": None,
        # P12-X：认证群的发骚/羞辱正文撒点表情（屋主 2026-09-27 要求；
        # 玫瑰房那份写成空串 = 不撒，保持管家口吻）
        "spice_emoji": "💗 💦 🥵",
        # P12-Y：这些提示语的**默认值逐字等于**原来硬编码在 bot.py 里的句子，
        # 所以认证群与其它默认人设的群行为一个字不变。
        "cont_no_content": "还没有发骚内容可以继续哦～先 /发骚 试试～",
        "cont_no_action": "还没有上一段可以继续哦～先 /发骚 或 /羞辱 某人试试～",
        "cont_bad_type": "上一段不是可以继续的内容哦～",
        "cont_fail": "续写失败啦，换个指令试试～",
        "rewrite_idle": "现在没有进行中的发骚哦～先 /发骚 试试～",
        "rewrite_no_content": "还没有发骚内容可以重写哦～先 /发骚 试试～",
        "rewrite_fail": "重写失败啦，再试一次～",
        "play_busy_fasao": "🎲 发骚进行中哦～暂无法响应其他指令，想继续发 /继续，想结束发 /结束～",
        "play_busy_diaojiao": "🔗 调教进行中哦～暂无法响应其他指令，想参与发 /命令 xxx，想结束发 /调教结束～",
        "dj_end": "🔗 调教结束啦～",
        "dj_cmd_empty": "命令不能为空哦～格式 /命令 xxx",
        "dj_busy": "调教进行中哦～先 /调教结束 再发起新的～",
        "dj_block": "🔗 调教进行中哦～想参与就发 /命令 xxx，或 /调教结束 收尾～",
        "reg_self_only": "只有糯糯姐才能帮别人注册哦～你自己发 /注册 就可以啦～",
        "reg_not_found": "没找到这个人呢，注册不了～",
        "reg_exists": "✅ {who} 已经注册过啦～不用重复注册哦，发 /查 {who} 就能看档案啦～",
        "reg_ok": "✅ {who} 注册成功啦～已经记进档案，以后查{who}会更丰富哦～",
        "reg_fail": "注册失败，存档出了点问题～",
        "sync_working": "🤖 收到同步指令～正在拉取增量消息，稍等一下下哦～",
        # P12-V：调教那一族的文案（原来是 runtime/diaojiao.py 里写死的字面量）。
        # 这里的默认值 = 原字面量**逐字**，所以不配覆盖的群行为一个字不变。
        "dj_format": "格式：/调教 目标 命令（例如 /调教 糯糯 学狗叫）",
        "dj_no_target": "没找到调教目标哦～换个人试试？",
        "dj_start": "🔗 调教开始！目标：{who}～",
        "dj_timeout": "⏱️ 调教结束啦（5分钟没动静）～",
        "dj_replied": "目标回应啦～谁想发下一条命令？发 /命令 xxx 或直接 /xxx 都行"
                      "（目标自己也能发哦～）30秒没人发我就自己来～",
        "dj_idle": "30秒没人发命令呀～那我继续啦～",
        "dj_hint": "想参与的姐妹也来玩呀～发 /命令 xxx 或直接 /xxx 都行"
                   "（加不加「命令」都可以）～30秒没人我就自己继续啦～",
}
