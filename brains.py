"""AI reply generation via DeepSeek API."""
import json, os, random, requests
from runtime.tuning import get as _tune          # P12-B：可调参数（唯一读口）
from prompt_defaults import PD               # P3-5：提示词默认值住 prompt_defaults.py（覆盖安全网语义不变）

# P8 第 3 步：prompt 可被 prompts/<NAME>.md 覆盖（见 runtime/prompts.py）。
# runtime 包缺失时退化为"永远用代码里的默认值"，绝不让 prompt 变空。
#
# P12-G：登记册搬到了 `runtime/prompts.py`（`load()` 自动记下"调用方模块 + 默认值"），
# 所以 `runtime/memory.py` 里那个第 40 个提示词也能一起热加载 ——
# 每个模块自己维护一份"默认值清单"是没有意义的（那份清单一定会漂移）。
def _p(name, default):
    try:
        return _p_load(name, default)
    except Exception:                                      # noqa: BLE001
        return default


def refresh_prompts() -> list:
    """按磁盘上的 prompt 文件重算所有 prompt 常量（P12-F/G 热加载）。

    返回**内容变了的** prompt 名列表（自证/日志用）。真正的登记册与重算逻辑
    在 `runtime.prompts.refresh()`（它知道每个常量在哪个模块里）。

    ⚠️ 只改 prompt 常量，不动任何逻辑。所有读取点都是"调用时查模块全局"，
    所以下一次生成就会用新文案（没有谁把 prompt 当默认参数捕获过 ——
    这一点由 `test_hotload.py` 静态扫源码钉住）。
    """
    try:
        from runtime.prompts import refresh as _refresh
        return list(_refresh() or [])
    except Exception:                                      # noqa: BLE001
        return []


try:
    from runtime.prompts import declare as _p_load
except Exception:                                          # noqa: BLE001
    def _p_load(name, default):
        return default

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
PERF_NOVEL_MODEL = "deepseek-v4-pro"  # 公演小说用更强模型保证忠实度/一致性


def _get_deepseek_key() -> str:
    """优先环境变量 DEEPSEEK_API_KEY（容器/云端），fallback 读 ~/.hermes/.env（本地）。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    try:
        env_path = os.path.expanduser("~/.hermes/.env")
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1]
    except Exception:
        pass
    return ""


DEFAULT_KEY = _get_deepseek_key()

CMD_PROMPT = _p("CMD_PROMPT", PD["CMD_PROMPT"])
DREAM_PROMPT = _p("DREAM_PROMPT", PD["DREAM_PROMPT"])
PERSONAL_STORY_PROMPT = _p("PERSONAL_STORY_PROMPT", PD["PERSONAL_STORY_PROMPT"])
PERSONAL_LETTER_PROMPT = _p("PERSONAL_LETTER_PROMPT", PD["PERSONAL_LETTER_PROMPT"])
PERSONAL_PORTRAIT_PROMPT = _p("PERSONAL_PORTRAIT_PROMPT", PD["PERSONAL_PORTRAIT_PROMPT"])
ABSENT_MEMOIR_PROMPT = _p("ABSENT_MEMOIR_PROMPT", PD["ABSENT_MEMOIR_PROMPT"])

# P12-AP（2026-09-29）：@ 回复的**收尾块**。
# 为什么需要结构化的一块，而不是把当前这句混在历史里：屋主现场 ——
#   11:51:22 @消息: @小小糯 摸摸头～公主抱抱你（说话人是**柳无央**）
#   11:51:23 💬 糯糯姐！你终于冒头了～…囡囡也在问接模型的事，紫苑那边分工我还没回…
# 上下文（含她自己"等糯糯姐冒头就转告"的承诺 + 记忆里给糯糯姐留的待办）把一句亲昵的
# 空话读成了"糯糯来了"，顺手把待办念了一遍。实测：只加提示词规则治不住
# （`tools/probe_at_focus.py`，before/after 各 3 次都还会"认亲"），
# 把"现在说话的人 + 他这一句 + 只回这一句"摆成独立收尾块之后 A 场景 3/3 干净。
# 占位符 `{who}` / `{q}` / `{pending}`：用 `str.replace` 填，不用 `format`
# （正文里有花括号就会炸）。
#
# P12-AQ（2026-09-29）加 `{pending}` 那一行：屋主问"治一下汇报内容真实性"。
# 现场是另一面 —— 没人托付她任何事时，被问"有什么事要跟我说吗"，她会**编**：
#   "还有一件要问你的——你上次让我记着的那件事，要不要现在说呀？"
#   "我这边都整理好啦：今天的日程和待办我都记着呢，姐姐你想先听哪一件？"
#   "糯米团子新口味配方 / 供应商报价单放在你桌上了"（群里根本没这些事）
# 探针实测（中性历史 + 不注入记忆）：3/3 出问题。
# 治法不是再加一句"别编"（规则打不过"秘书被问事就该报事"这个惯性），而是把
# **她手上真实记着的事**摆到她眼前（`_pending_line()`：记忆条目，空就是「（空）」），
# 并规定汇报只能讲这一行或聊天记录里真出现过的。
# P12-AU（2026-09-29 屋主："茶水间@小小糯 闲聊回复变短了！"）——
# 收尾块里原来写的是"**只回他这一句**…没有实质内容就只接住这一句"，本意是**话题锚点**
# （别接着上一轮讲、别把人认错），但她读成了"少说话"。实测（同一批闲聊问题各 3 次）：
#     旧提示词+无收尾块 均值 122 字 → 新提示词+无收尾块 89 → 新提示词+收尾块 **55 字**
#     （15/15 条短于 100 字；线上 18:43/18:44/18:45 三条只有 32/43/60 字）
# 现在把两件事拆开写：`【怎么回】` 只管**话题**，`【写多长】` 单独讲**篇幅**，
# 并且把"哪些算有内容"点名（今天怎么样/在干嘛/吃了没 都算），
# 只有"纯贴贴/纯招呼"才允许短回。
AT_FRAME_PROMPT = _p("AT_FRAME_PROMPT", PD["AT_FRAME_PROMPT"])

SUMMARY_PROMPT = _p("SUMMARY_PROMPT", PD["SUMMARY_PROMPT"])

PERSON_SUMMARY_PROMPT = _p("PERSON_SUMMARY_PROMPT", PD["PERSON_SUMMARY_PROMPT"])

PROFILE_PROMPT = _p("PROFILE_PROMPT", PD["PROFILE_PROMPT"])

PROFILE_STYLES = [
    "客观档案：分点列基本信息 / 个人特点 / 最近动态，客观清晰",
    "可爱介绍：用小小糯软糯可爱的口吻介绍，亲切友好",
    "趣味段子：用轻松幽默的方式介绍，有梗但不毒舌",
    "暖心侧写：从性格闪光点和有趣的地方切入，友好介绍",
]

RANK_PROMPT = _p("RANK_PROMPT", PD["RANK_PROMPT"])

INSULT_PROMPT = _p("INSULT_PROMPT", PD["INSULT_PROMPT"])

INSULT_STYLES = [
    "恶魔毒舌：居高临下、直白辱骂，用「母狗/淫荡/骚穴/下贱/发情/欠操/骚水/肉便器」等词狂轰，怎么狠怎么露骨怎么来",
    "阴阳怪气：明褒实贬、反讽，嘴上夸她清纯矜持，实际句句戳她发骚淫荡的老底，用词照样露骨淫荡",
    "优雅羞辱：用文明矜持的措辞包装羞辱，但句句都是下流的性羞辱，反差拉满，骨子里照样淫荡",
    "色气挑逗：不直接骂，用挑逗撩拨的方式羞辱她发情，露骨描述她身体的淫荡反应（骚穴、湿、发浪）",
    "记仇翻旧账：专挑她最近发言里的原话和骚点打脸，以子之矛攻子之盾，骂得她无地自容",
]

# P12-T：**异性恋人设 + 女性目标** 时改用的风格库 —— 只毒舌、不涉情欲
# （屋主口径："纯 S 属性、异性恋"；2026-09-27 实测：光靠人设提示词压不住
#  "色气挑逗"那种写死"露骨描写身体"的风格，所以在这里做**硬过滤**）。
INSULT_STYLES_PLAIN = [
    "记仇翻旧账：专挑她最近发言里的原话打脸，以子之矛攻子之盾，就事论事怼到无话可说",
    "阴阳怪气：明褒实贬、反讽，句句戳她端着、装样子的部分，措辞文明但刀刀见血",
    "戳破人设：把她签名/简介里的自我标榜与最近言行摆在一起，点出反差",
    "居高临下：以上位者口吻点评她的幼稚没分寸，冷淡、不给台阶",
    "揭穿逻辑：挑她话里的自相矛盾穷追猛打，问到她自己圆不回来",
]

# 女性目标（异性恋人设）时的硬约束：不许出现情欲/身体描写
PLAIN_INSULT_RULE = ("【本群口径 · 优先级最高】目标是女性、而你是异性恋：**只毒舌，不涉情欲** —— "
                     "不许写身体部位、性器官、性行为，也不许用「发情/湿/淫荡/骚/母狗」这类字眼。"
                     "骂态度、骂逻辑、骂丢人的事，一样可以狠。")

# 男性目标（S 人设）时的"拉满"约束：屋主 2026-09-27「再淫贱、露骨一些」
HARD_INSULT_RULE = ("【本群口径 · 优先级最高】目标是男性、而你是纯 S 的支配方：**拉到最满，不许留台阶** —— "
                    "直接写身体与器官（鸡巴/后穴/屁股/喉咙…）、写他的生理反应（硬、流水、发抖）、"
                    "写你要他做的动作（跪好、撅起来、张嘴、自己数着）；用「母狗/公狗/肉便器/废物」这类称谓，"
                    "怎么下贱怎么写。你是动手的那一个：命令句多于感叹句，结尾留一个必须回答的钩子。")

# 男性目标（S 人设）时掺随机词库的概率比默认更高（默认 0.35 是给小糯那套的）
HARD_INSULT_PROB = 0.75

# 异性恋人设**要不要对女性目标收敛**（只毒舌、不涉情欲）。
# 屋主 2026-09-27：「对女生也调整为露骨、淫贱」→ **False**：男女一视同仁，都走"拉满"那条。
# （机制留着：哪天想重新只对男性放开，把这个常量改回 True 就行，测试也跟着有覆盖。）
INSULT_PLAIN_FOR_FEMALE = False

# 随机羞辱词库：脱离档案/发言的直白露骨下贱羞辱素材（概率混合用，增加多样性）
RANDOM_INSULT_PROB = 0.35  # 每次羞辱约 35% 概率掺入随机羞辱词库
RANDOM_INSULT_WORDS = {
    "称谓": ["母狗", "骚母狗", "下贱母狗", "肉便器", "公共厕所", "发情母畜", "欠操的骚货", "下贱婊子", "精盆", "贱婢", "浪货"],
    "身体": ["骚穴", "淫穴", "贱穴", "奶子", "骚奶", "翘臀", "腿心", "小嘴", "喉咙", "后穴"],
    "状态": ["发情", "发浪", "流水", "湿透", "夹紧", "扭屁股", "摇尾巴", "跪下来", "张开腿", "撅起屁股", "翻白眼"],
    "动作": ["给我爬过来", "跪好别动", "自己掰开", "张嘴接着", "蹭我的脚", "舔干净", "自己扇自己"],
}

def _sample_random_insult_block() -> str:
    """从词库随机抽词，拼成随机羞辱素材块。"""
    import random as _r
    def pick(key, n):
        return "、".join(_r.sample(RANDOM_INSULT_WORDS[key], min(n, len(RANDOM_INSULT_WORDS[key]))))
    return "【随机羞辱词库（可随机选用，脱离档案/发言即兴发挥，直白露骨下贱）】\n" \
           "称谓：" + pick("称谓", 4) + "\n" \
           "身体词：" + pick("身体", 4) + "\n" \
           "状态：" + pick("状态", 4) + "\n" \
           "动作：" + pick("动作", 3)

DEVIL_ROAST_PROMPT = _p("DEVIL_ROAST_PROMPT", PD["DEVIL_ROAST_PROMPT"])

CUTE_ROAST_PROMPT = _p("CUTE_ROAST_PROMPT", PD["CUTE_ROAST_PROMPT"])

DEFEND_PROMPT = _p("DEFEND_PROMPT", PD["DEFEND_PROMPT"])

ARCHIVE_PROMPT = _p("ARCHIVE_PROMPT", PD["ARCHIVE_PROMPT"])

VOTE_OPTIONS_PROMPT = _p("VOTE_OPTIONS_PROMPT", PD["VOTE_OPTIONS_PROMPT"])

FORTUNE_PROMPT = _p("FORTUNE_PROMPT", PD["FORTUNE_PROMPT"])

GREETING_PROMPT = _p("GREETING_PROMPT", PD["GREETING_PROMPT"])

SCENE_PROMPT = _p("SCENE_PROMPT", PD["SCENE_PROMPT"])

XP_PROMPT = _p("XP_PROMPT", PD["XP_PROMPT"])

INSULT_CONTINUE_PROMPT = _p("INSULT_CONTINUE_PROMPT", PD["INSULT_CONTINUE_PROMPT"])

SCENE_CONTINUE_PROMPT = _p("SCENE_CONTINUE_PROMPT", PD["SCENE_CONTINUE_PROMPT"])

SCENE_END_PROMPT = _p("SCENE_END_PROMPT", PD["SCENE_END_PROMPT"])

SCENE_REWRITE_PROMPT = _p("SCENE_REWRITE_PROMPT", PD["SCENE_REWRITE_PROMPT"])

DIAOJIAO_PROMPT = _p("DIAOJIAO_PROMPT", PD["DIAOJIAO_PROMPT"])

DIAOJIAO_END_PROMPT = _p("DIAOJIAO_END_PROMPT", PD["DIAOJIAO_END_PROMPT"])

PERF_ENRICH_PROMPT = _p("PERF_ENRICH_PROMPT", PD["PERF_ENRICH_PROMPT"])

PERF_INSULT_PROMPT = _p("PERF_INSULT_PROMPT", PD["PERF_INSULT_PROMPT"])

PERF_COMMENT_PROMPT = _p("PERF_COMMENT_PROMPT", PD["PERF_COMMENT_PROMPT"])

PERF_ENDING_PROMPT = _p("PERF_ENDING_PROMPT", PD["PERF_ENDING_PROMPT"])

PERF_NOVEL_PROMPT = _p("PERF_NOVEL_PROMPT", PD["PERF_NOVEL_PROMPT"])

HEAT_JUDGE_PROMPT = _p("HEAT_JUDGE_PROMPT", PD["HEAT_JUDGE_PROMPT"])

HEAT_CLIMAX_PROMPT = _p("HEAT_CLIMAX_PROMPT", PD["HEAT_CLIMAX_PROMPT"])

HEAT_FASAO_PROMPT = _p("HEAT_FASAO_PROMPT", PD["HEAT_FASAO_PROMPT"])


def _call_llm_raw(system_prompt: str, user_content: str,
                  api_key: str = DEFAULT_KEY, max_tokens: int = 100,
                  timeout: int = 30, model: str = None):
    """**一次**底层调用 → `(text, finish_reason)`。

    `finish_reason == "length"` 就是被 `max_tokens` 掐断了。
    2026-09-27 实测（屋主："妹子群发骚有截断"）：认证群一段发骚正文停在
    「屁股却不受控制地往裳手心里」—— 700 token 的额度用完了，
    而模型本来还要往下写。`_call_llm(extend=True)` 会据此自动续写。
    """
    payload = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    resp = requests.post(DEEPSEEK_URL, json={
        "model": model or DEEPSEEK_MODEL,
        "messages": payload,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "thinking": {"type": "disabled"},
    }, headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }, timeout=timeout)
    data = resp.json()
    try:
        reply = data["choices"][0]["message"]["content"]
        finish = str(data["choices"][0].get("finish_reason") or "")
    except Exception:
        reply, finish = "", ""
    reply = (reply or "").strip()
    # 清掉所有 🤖，bot.py 会统一加一个
    reply = reply.replace("🤖", "").strip()
    return reply, finish


# 续写指令：把"被截断"变成"接着写完"。措辞要压住模型重抄一遍的冲动。
CONTINUE_HINT = ("\n\n【紧接着往下写】上面那段被长度上限截断了。"
                 "请**直接从断掉的地方接着写下一句**："
                 "不要重复已经写过的内容、不要重写开头、不要任何解释/标题/序号，"
                 "人称、语气、标点风格保持一致。")


def _join_continuation(head: str, more: str, window: int = 80) -> str:
    """把续写接到上一段后面。

    模型经常把上一段的尾巴**重抄一遍**再往下写，所以先剥掉重复的前缀；
    剥不掉（重复得比 window 还长）也照样接上 —— 宁可多一句重复，也不要丢内容。
    """
    more = (more or "").lstrip()
    if not more:
        return head
    for n in range(min(window, len(more)), 7, -1):
        if head.endswith(more[:n]):
            more = more[n:].lstrip()
            break
    return head + more


def _call_llm(system_prompt: str, user_content: str,
              api_key: str = DEFAULT_KEY, max_tokens: int = 100,
              timeout: int = 30, model: str = None,
              extend: bool = False, max_rounds: int = 3) -> str:
    """全部提示词的唯一出口：分群人设在这里注入 + （可选）**截断自动续写**。

    `extend=True`：被 `max_tokens` 掐断时自动再要一段接上，最多 `max_rounds` 段。
    长文（发骚/场景这类"写正文"的）都该开；短句（算命/问候/巡查）不用。
    """
    try:
        import personas
        system_prompt = personas.apply_persona(system_prompt)
    except Exception:
        pass
    text, finish = _call_llm_raw(system_prompt, user_content, api_key,
                                 max_tokens, timeout, model)
    if not (extend and text and finish == "length"):
        return text
    for _ in range(max(0, int(max_rounds) - 1)):
        cont = (user_content + "\n\n【已经写好的部分（不要重复，接着它往下写）】\n"
                + text[-1200:] + CONTINUE_HINT)
        more, finish = _call_llm_raw(system_prompt, cont, api_key,
                                     max_tokens, timeout, model)
        if not more:
            break
        text = _join_continuation(text, more)
        if finish != "length":
            break
    return text


def generate_reply(messages: list[dict], api_key: str = DEFAULT_KEY,
                   max_tokens: int = None, context_msgs: int = None) -> str:
    """群内闲聊/房间对话的回复。

    `max_tokens` 可覆盖（默认 400）：玫瑰房的人设要求"4～10 句"，400 token 容易
    把话说到一半就截断 —— 由调用方按群传 `tuning: room.reply_max_tokens`。
    `context_msgs`：带多少条历史（默认 20）。原先写死 `[-10:]`，而 `@` 那条
    分支只喂了 4 条、**还不含机器人自己说过的话** ⇒ 它没有对话连续性。
    现在由调用方按群传 `tuning: reply.context_msgs`（P12-AC）。
    """
    limit = int(context_msgs or 20)
    chat = []
    for m in messages[-max(1, limit):]:
        name = m.get("sender", "")
        text = m.get("text", "")
        if name:
            chat.append(name[:8] + ": " + text)
        else:
            chat.append(text)
    context = "\n".join(chat)
    return _call_llm(CMD_PROMPT, context, api_key,
                     max_tokens=int(max_tokens or 400))


MEMORY_SUMMARY_PROMPT = _p("MEMORY_SUMMARY_PROMPT", PD["MEMORY_SUMMARY_PROMPT"])


def generate_memory_summary(mem_list: list, api_key: str = DEFAULT_KEY) -> str:
    """把记忆整理成总结（合并重复、滤噪音）。"""
    content = "\n".join(f"- {m.get('text', '')}（{m.get('from', '?')} {m.get('at', '?')}）" for m in mem_list)
    return _call_llm(MEMORY_SUMMARY_PROMPT, content, api_key, max_tokens=400)


def generate_summary(messages_text: str, api_key: str = DEFAULT_KEY) -> str:
    """总结群聊记录."""
    return _call_llm(SUMMARY_PROMPT, messages_text, api_key, max_tokens=200)


def generate_profile_summary(profile_text: str, api_key: str = DEFAULT_KEY) -> str:
    """查人（未注册）：随机角度 + 资料发言."""
    angle = random.choice(PROFILE_STYLES)
    content = "本次介绍角度（务必照此角度写）：" + angle + "\n\n" + profile_text
    return _call_llm(PROFILE_PROMPT, content, api_key, max_tokens=400)


def generate_person_summary(full_name: str, their_text: str, today_label: str, api_key: str = DEFAULT_KEY) -> str:
    """按人总结表现（正常小小糯人设，不毒舌）."""
    content = "目标：" + full_name + "\n\n时间范围：" + today_label + "\n\nTA 最近的发言：\n" + their_text
    return _call_llm(PERSON_SUMMARY_PROMPT, content, api_key, max_tokens=400)


def generate_rank(rank_text: str, api_key: str = DEFAULT_KEY) -> str:
    """骚度排行."""
    return _call_llm(RANK_PROMPT, rank_text, api_key, max_tokens=600)


def generate_insult(target_text: str, custom: str = "", history: list = None, api_key: str = DEFAULT_KEY,
                    gender: str = "", straight: bool = False, max_tokens: int = None):
    """一键羞辱：随机风格 + 角度避让 + 概率混合随机羞辱。

    history: 上次羞辱记录 [{style, text, at}, ...]，用于角度避让（排除上次风格 + 提示换角度）。
    返回 (text, style)，style 供调用方记录历史做下次避让。

    P12-T：`straight=True`（异性恋人设）且 `gender="女"` 时走**只毒舌不涉情欲**的一套
    （换风格库 + 不掺露骨词库 + 显式约束）。默认参数 = 老行为，一个字不变。
    """
    history = history or []
    # 性别标签是「女生/男生/未知」（见 runtime.members._gender_label），
    # 所以用前缀判断，别写死等于"女"。
    gl = str(gender or "").strip()
    # 未知性别 + 异性恋人设 + 开着"对女性收敛"时 → 走**保守**那条（只毒舌）：
    # 宁可骂轻，也不要押错取向。
    female_side = gl.startswith("女") or (straight and not gl.startswith("男"))
    plain = bool(straight and INSULT_PLAIN_FOR_FEMALE and female_side)
    # S 人设 → 男女都"拉满"（屋主 2026-09-27：对女生也调整成露骨、淫贱）
    hard = bool(straight and not plain)
    pool = INSULT_STYLES_PLAIN if plain else INSULT_STYLES
    # 选风格：随机，但角度避让——排除上次风格（避免连续同风格）
    style = random.choice(pool)
    if history:
        last_style = history[-1].get("style", "")
        others = [s for s in pool if s != last_style]
        if others:
            style = random.choice(others)
    content = ("本次羞辱风格（务必照此风格写" + ("，但保持不带情欲" if plain else "，保持露骨淫荡")
               + "）：" + style + "\n\n" + target_text)
    if plain:
        content += "\n\n" + PLAIN_INSULT_RULE
    elif hard:
        content += "\n\n" + HARD_INSULT_RULE
    # 概率混合：默认约 35% 概率掺入随机羞辱词库（脱离档案/发言，增加多样性）
    # ⚠️ 女目标 + 异性恋人设时**不掺**（那个词库全是性词）；男性目标是 S 人设时掺得更勤。
    prob = 0.0 if plain else (HARD_INSULT_PROB if hard else RANDOM_INSULT_PROB)
    if random.random() < prob:
        content += "\n\n" + _sample_random_insult_block()
    # 角度避让：注入上次羞辱内容，要求换全新角度
    if history:
        parts = []
        for h in history[-3:]:
            parts.append("- [" + h.get("at", "") + "] 风格：" + h.get("style", "")
                         + "；内容：" + str(h.get("text", ""))[:120])
        content += "\n\n【角度避让】你之前羞辱 ta 的内容如下，务必全部避开这些角度/用词/句式，换一个全新的羞辱角度：\n" + "\n".join(parts)
    if custom:
        content += "\n\n用户指定羞辱方向（务必照此羞辱，这是核心）：" + custom
    text = _call_llm(INSULT_PROMPT, content, api_key,
                     max_tokens=int(max_tokens or 600))   # P12-BR b：可按群传
    return text, style


POEM_PROMPT = _p("POEM_PROMPT", PD["POEM_PROMPT"])

POEM_STYLES = [
    "五言绝句：五言 4 句，严守绝句格律（起承转合），符合平仄与押韵",
    "七言绝句：七言 4 句，严守绝句格律（起承转合），符合平仄与押韵",
    "五言律诗：五言 8 句（四联：起承转合），颔联颈联对仗，符合平仄押韵",
    "七言律诗：七言 8 句（四联：起承转合），颔联颈联对仗，符合平仄押韵",
    "宋词：任选一个常用词牌（如梦令/菩萨蛮/浣溪沙/卜算子/鹧鸪天/蝶恋花/清平乐/虞美人/采桑子等），严格按该词牌的句式/字数/韵脚填一阕，标题标「词牌·题」",
]


def generate_poem(target_text: str, history: list = None, api_key: str = DEFAULT_KEY, style: str = None):
    """写诗：指定形式或随机 + 历史避让（保证每次不一样）。

    history: 之前写过的诗 [{style, text, at}, ...]，用于避开重复的意象/角度/用词。
    style: 指定形式（POEM_STYLES 的一项），None=随机。
    返回 (text, style)，style 供调用方记录历史做下次避让。
    """
    history = history or []
    if style is None:
        # 随机选，但避让——排除上次形式（避免连续同形式）
        style = random.choice(POEM_STYLES)
        if history:
            last_style = history[-1].get("style", "")
            others = [s for s in POEM_STYLES if s != last_style]
            if others:
                style = random.choice(others)
    content = "本次诗词形式（务必严格照此形式写）：" + style + "\n\n" + target_text
    if history:
        parts = []
        for h in history[-3:]:
            parts.append("- [" + h.get("at", "") + "] 方向：" + h.get("style", "")
                         + "；内容：" + str(h.get("text", ""))[:150])
        content += "\n\n【避免重复】你之前给 ta 写过的诗如下，务必全部避开这些意象/切入角度/用词，换一个全新的意象和角度：\n" + "\n".join(parts)
    text = _call_llm(POEM_PROMPT, content, api_key, max_tokens=400)
    return text, style


POEM_THEME_PROMPT = _p("POEM_THEME_PROMPT", PD["POEM_THEME_PROMPT"])


def generate_poem_theme(theme: str, api_key: str = DEFAULT_KEY, style: str = None):
    """给主题写诗（不绑定人）。返回 (text, style)。style 指定则用指定形式。"""
    if style is None:
        style = random.choice(POEM_STYLES)
    content = "本次诗词形式（务必严格照此形式写）：" + style + "\n\n主题：" + theme
    text = _call_llm(POEM_THEME_PROMPT, content, api_key, max_tokens=400)
    return text, style


def generate_defend(attacker_text: str, api_key: str = DEFAULT_KEY) -> str:
    """维护糯糯权威，痛斥发指令者."""
    return _call_llm(DEFEND_PROMPT, attacker_text, api_key, max_tokens=300)


def generate_devil_roast(context_text: str, target_text: str = "", api_key: str = DEFAULT_KEY, force_cute: bool = False) -> str:
    """主动吐槽：根据群聊上下文冒一句（对事/对人）. 默认 50/50 随机恶魔/可爱，force_cute=True 固定可爱."""
    content = "最近群聊：\n" + context_text
    if target_text:
        content += "\n\n目标成员资料（点名吐槽这个成员）：\n" + target_text
    else:
        content += "\n\n（没有指定目标，就事论事吐槽话题/氛围，不点名）"
    if force_cute:
        prompt = CUTE_ROAST_PROMPT
    else:
        prompt = DEVIL_ROAST_PROMPT if random.random() < 0.5 else CUTE_ROAST_PROMPT
    return _call_llm(prompt, content, api_key, max_tokens=200)


# P12-W（2026-09-27，屋主："妹子群发骚有截断""上班群发骚内容太少"）：
# 发骚/场景这一族的正文长度上限从 700 提到 1800，并且**被截断就自动续写**
# （`extend=True`）。700 的实测后果：认证群有一段正文停在「往裳手心里」。
# 上限本身可调（tuning `scene.max_tokens`），调用方按群传进来。
SCENE_MAX_TOKENS = 1800
# 2026-10-07 屋主：「发骚的过程中，/继续 后输出的字数调整为现在的一半」。
# 首段（/发骚）保持 1800，**续写单独减半** —— 走 tuning `scene.continue_max_tokens`。
SCENE_CONTINUE_MAX_TOKENS = 900
SCENE_TIMEOUT = 90


def generate_scene(combo_text: str, api_key: str = DEFAULT_KEY,
                   max_tokens: int = SCENE_MAX_TOKENS) -> str:
    """涩涩骰子：生成情境描写."""
    return _call_llm(SCENE_PROMPT, combo_text, api_key,
                     max_tokens=max_tokens, timeout=SCENE_TIMEOUT, extend=True)


def generate_xp(profile_text: str, api_key: str = DEFAULT_KEY) -> str:
    """提炼性癖（XP）标签，返回 JSON 数组文本."""
    return _call_llm(XP_PROMPT, profile_text, api_key, max_tokens=100)


def generate_insult_continue(material_text: str, last_output: str, custom: str = "", api_key: str = DEFAULT_KEY) -> str:
    """继续羞辱：目标资料 + 上一段输出 + 可选自定义方向."""
    content = "目标资料：\n" + material_text + "\n\n上一段羞辱：\n" + last_output
    if custom:
        content += "\n\n用户指定续写方向（务必照此羞辱）：" + custom
    return _call_llm(INSULT_CONTINUE_PROMPT, content, api_key, max_tokens=600)


def generate_scene_continue(combo_text: str, history: list, custom: str = "",
                            api_key: str = DEFAULT_KEY,
                            max_tokens: int = SCENE_CONTINUE_MAX_TOKENS) -> str:
    """继续涩涩场景：场景信息 + 最近几段已写内容 + 可选自定义方向."""
    hist_text = "\n\n".join(history[-8:])  # 最近 8 段，长剧情更连贯
    content = "场景信息：\n" + combo_text + "\n\n已写内容（最近几段，接着写）：\n" + hist_text
    if custom:
        content += "\n\n用户指定续写方向（务必照此描写）：" + custom
    return _call_llm(SCENE_CONTINUE_PROMPT, content, api_key,
                     max_tokens=max_tokens, timeout=SCENE_TIMEOUT, extend=True)


def generate_scene_end(full_name: str, history: list, api_key: str = DEFAULT_KEY) -> str:
    """发骚结束收尾小结."""
    hist_text = "\n\n".join(history[-3:])
    content = "主角：\n" + full_name + "\n\n已写内容：\n" + hist_text
    return _call_llm(SCENE_END_PROMPT, content, api_key,
                     max_tokens=400, timeout=SCENE_TIMEOUT, extend=True)


def generate_scene_rewrite(combo_text: str, prev_history: list, last_output: str,
                           direction: str = "", api_key: str = DEFAULT_KEY,
                           max_tokens: int = SCENE_MAX_TOKENS) -> str:
    """重写发骚的上一段（替换 history 最后一段）."""
    prev_text = "\n\n".join(prev_history[-2:]) if prev_history else "（这是第一段，没有前文）"
    content = "场景信息：\n" + combo_text + "\n\n前文（要接上，保持连贯）：\n" + prev_text + "\n\n上一段（用户不满意、要重写这段）：\n" + last_output
    if direction:
        content += "\n\n用户调整方向（务必照此重写）：" + direction
    return _call_llm(SCENE_REWRITE_PROMPT, content, api_key,
                     max_tokens=max_tokens, timeout=SCENE_TIMEOUT, extend=True)


def generate_diaojiao(target_text: str, command: str, api_key: str = DEFAULT_KEY,
                      max_tokens: int = None) -> str:
    """调教：生成一轮命令式调教。

    P12-BR b（2026-09-30）：`max_tokens` 可传（原先写死 600）——
    不传就是 600，**零回归**；调用方按群传 `_tune("diaojiao.max_tokens", cid)`。
    """
    content = "目标资料：\n" + target_text + "\n\n命令方向：" + command
    return _call_llm(DIAOJIAO_PROMPT, content, api_key,
                     max_tokens=int(max_tokens or 600))


def generate_diaojiao_end(target_text: str, round_num: int, api_key: str = DEFAULT_KEY) -> str:
    """调教收尾小结."""
    content = "目标资料：\n" + target_text + "\n\n调教轮数：" + str(round_num)
    return _call_llm(DIAOJIAO_END_PROMPT, content, api_key, max_tokens=300)


def generate_heat_judge(chat_text: str, api_key: str = DEFAULT_KEY) -> str:
    """批量评定发情值增量（返回 JSON 文本）."""
    return _call_llm(HEAT_JUDGE_PROMPT, chat_text, api_key, max_tokens=600)


def generate_heat_climax(user_text: str, api_key: str = DEFAULT_KEY) -> str:
    """高潮事件：专属色色文字."""
    return _call_llm(HEAT_CLIMAX_PROMPT, user_text, api_key, max_tokens=800)


def generate_heat_fasao(user_text: str, api_key: str = DEFAULT_KEY) -> str:
    """发骚点名：命令她发骚."""
    return _call_llm(HEAT_FASAO_PROMPT, user_text, api_key, max_tokens=150)


def generate_perf_enrich(text: str, api_key: str = DEFAULT_KEY) -> str:
    """公演：丰富对话."""
    return _call_llm(PERF_ENRICH_PROMPT, text, api_key, max_tokens=400)


def generate_perf_insult(text: str, api_key: str = DEFAULT_KEY) -> str:
    """公演：羞辱被调者."""
    return _call_llm(PERF_INSULT_PROMPT, text, api_key, max_tokens=300)


def generate_perf_comment(text: str, api_key: str = DEFAULT_KEY) -> str:
    """公演：点评片段."""
    return _call_llm(PERF_COMMENT_PROMPT, text, api_key, max_tokens=200)


def generate_perf_ending(text: str, api_key: str = DEFAULT_KEY) -> str:
    """公演：结束点评."""
    return _call_llm(PERF_ENDING_PROMPT, text, api_key, max_tokens=300)


def generate_perf_novel(text: str, api_key: str = DEFAULT_KEY) -> str:
    """公演：生成小说（长文，用长超时）."""
    return _call_llm(PERF_NOVEL_PROMPT, text, api_key, max_tokens=8000, timeout=300, model=PERF_NOVEL_MODEL)


PERF_NOVEL_CONTINUE_PROMPT = _p("PERF_NOVEL_CONTINUE_PROMPT", PD["PERF_NOVEL_CONTINUE_PROMPT"])


def generate_perf_novel_continue(text: str, prev_tail: str, api_key: str = DEFAULT_KEY,
                                 done_headings: list = None) -> str:
    """续写小说的一段。

    done_headings：已生成的小节标题列表——把上一段生成的章节边界告诉模型，
    否则它不知道前面写到第几章了，编号会从"一"重来（2026-09-17 实测：《暗巷审讯实录》
    第 6 节之后编号重置成"## 二"、还冒出"## 阶段五"这种混称）。
    """
    content = "【已写好的前半部分结尾】\n" + prev_tail
    if done_headings:
        content += ("\n\n【已完成的章节标题（按顺序；本次要新开小节时必须接着这个序号往下排，"
                    "绝不要从「一」重来）】\n" + "\n".join(done_headings))
    content += "\n\n【本次要纳入的后续公演记录】\n" + text
    return _call_llm(PERF_NOVEL_CONTINUE_PROMPT, content, api_key,
                     max_tokens=8000, timeout=300, model=PERF_NOVEL_MODEL)


def generate_perf_novel_chunked(text: str, api_key: str = DEFAULT_KEY) -> str:
    """分段生成小说，避免超长公演被 max_tokens 截断遗漏。

    2026-09-14 事故：562 条公演内容按每块 5 条 → 113 次 pro 模型调用，
    串行跑要 2~4 小时，把主循环（所有群）堵死了 37 分钟以上。
    现在加硬上限 MAX_CHUNKS：块数超了就自动加宽每块，把总调用次数压到上限内。
    """
    lines = [l for l in text.split("\n") if l.strip()]
    header = ""
    if lines and lines[0].startswith("【被调教方"):
        header = lines[0]
        lines = lines[1:]
    MAX_CHUNKS = int(_tune("perf.novel_max_chunks"))  # 硬上限：单次公演最多 12 次模型调用（约 10~20 分钟）
    N = int(_tune("perf.novel_chunk"))  # 每段最多 5 条消息（更小分段，避免单段 token 不足导致省略细节）
    if len(lines) > N * MAX_CHUNKS:
        N = (len(lines) + MAX_CHUNKS - 1) // MAX_CHUNKS  # 加宽每块，压到上限内
    if len(lines) <= N:
        return generate_perf_novel(text, api_key)
    chunks = [lines[i:i + N] for i in range(0, len(lines), N)]
    parts = []
    prev_tail = ""
    heads = []  # 累积已生成的小节标题，喂给下一段以接续编号
    for i, chunk in enumerate(chunks):
        print("[perf-novel] 生成 " + str(i + 1) + "/" + str(len(chunks))
              + "（本块 " + str(len(chunk)) + " 条）", flush=True)
        chunk_text = "\n".join(chunk)
        if header and i == 0:
            chunk_text = header + "\n\n" + chunk_text
        if i == 0:
            part = generate_perf_novel(chunk_text, api_key)
        else:
            part = generate_perf_novel_continue(chunk_text, prev_tail, api_key,
                                                done_headings=heads[-15:])
        parts.append(part)
        for ln in part.split("\n"):  # 收集本段的小节标题，供下一段接续编号
            s = ln.strip()
            if s.startswith("## "):
                heads.append(s)
        prev_tail = part[-800:]
    return "\n\n".join(parts)


HOROSCOPE_PROMPT = _p("HOROSCOPE_PROMPT", PD["HOROSCOPE_PROMPT"])


def generate_horoscope(sign: str, element: str, ruler: str, moon: str,
                       dims: dict, yi: str, ji: str, date_str: str,
                       who: str = "客人", hint: str = "",
                       health: int = 0, total: int = 0,
                       api_key: str = DEFAULT_KEY) -> str:
    """写星座运势卡的五段正文（P12-K：一段变五段，一次调用出全篇）."""
    dim_txt = "、".join(k + str(v) for k, v in dims.items())
    allv = dict(dims or {})
    if health:
        allv["健康"] = health
    low = min(allv.items(), key=lambda kv: kv[1]) if allv else ("心情", 70)
    content = ("星座：" + sign + "（" + element + "象 · 守护星" + ruler + "）\n"
               + "今日月相：" + moon + "\n"
               + "日期：" + date_str + "\n"
               + "分数：综合" + str(total or low[1]) + " / " + dim_txt
               + (" / 健康" + str(health) if health else "") + "\n"
               + "最低项：" + low[0] + str(low[1]) + "\n"
               + "今日宜：" + yi + "；忌：" + ji + "\n"
               + "需要称呼的人：" + who)
    if hint:
        content += "\n" + hint
    return _call_llm(HOROSCOPE_PROMPT, content, api_key, max_tokens=800)


RECAP_PROMPT = _p("RECAP_PROMPT", PD["RECAP_PROMPT"])


def generate_recap(transcript: str, who: str, window_txt: str, mention_txt: str,
                   api_key: str = DEFAULT_KEY) -> str:
    """生成「你不在的时候群里发生了什么」的三行摘要（💬 / 🎭 / 📌）.

    P12-AK：`max_tokens` 400 → 800。转录现在**不再把每条砍到 80 字**（屋主要
    "输入内容丰富完整一些"），喂进去的信息多了，输出也该有地方写全 ——
    400 token 大约只够每段一句，容易把"发生了什么"讲成干提纲。
    P12-AM：800 → 1200。输出格式从"三段各一行（80~140 字）"改成
    "三段、段内 2~3 条"（250~500 字）—— 屋主反馈"内容单薄"，问题就出在那个格式上。
    """
    content = ("提问的人：" + who + "\n"
               + "离开时段：" + window_txt + "\n"
               + "有人找他/提到他的情况：" + (mention_txt or "无") + "\n\n"
               + "【这段时间的群聊记录】\n" + transcript)
    return _call_llm(RECAP_PROMPT, content, api_key, max_tokens=1200)


TREND_PROMPT = _p("TREND_PROMPT", PD["TREND_PROMPT"])


def generate_trend(data_txt: str, who: str, gender: str = "未知",
                   api_key: str = DEFAULT_KEY) -> str:
    """写「过去 7 天运势趋势」卡的最后一段回顾（只写这一段，不换行）.

    P12-BA：`gender`（女生/男生/未知）必须给 —— 提示词里写了"称呼按性别"，
    不给数据的话模型就照旧一律叫"姐姐"。
    """
    content = ("提问的人：" + str(who) + "\n性别：" + str(gender or "未知")
               + "\n\n" + data_txt)
    return _call_llm(TREND_PROMPT, content, api_key, max_tokens=500)


def generate_archive(archive_text: str, api_key: str = DEFAULT_KEY) -> str:
    """档案查询（已注册）：随机角度 + 注册档案 + 最新动态."""
    angle = random.choice(PROFILE_STYLES)
    content = "本次介绍角度（务必照此角度切入点评）：" + angle + "\n\n" + archive_text
    return _call_llm(ARCHIVE_PROMPT, content, api_key, max_tokens=600)


def generate_vote_options(topic: str, members: str = "", api_key: str = DEFAULT_KEY) -> list:
    """生成投票选项：主题 + 群成员名单 → 选项列表."""
    content = "投票主题：" + topic
    if members:
        content += "\n\n群成员名单：\n" + members
    raw = _call_llm(VOTE_OPTIONS_PROMPT, content, api_key, max_tokens=200)
    raw = (raw or "").strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(raw[start:end + 1])
        if isinstance(arr, list):
            return [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        pass
    return []


def generate_fortune(full: str, score: int, date_str: str, hint: str = "",
                     api_key: str = DEFAULT_KEY) -> str:
    """生成运势最后一句点评（一句话，不换行；数据由五行命理算分给出）。"""
    content = "用户：" + str(full) + "\n评分：" + str(score) + "分\n日期：" + str(date_str) + "\n" + str(hint)
    return _call_llm(FORTUNE_PROMPT, content, api_key, max_tokens=150)


def generate_greeting(period: str, date_str: str, api_key: str = DEFAULT_KEY) -> str:
    """生成定时问候语（早/中/晚）."""
    return _call_llm(GREETING_PROMPT, f"时间段：{period}\n日期：{date_str}", api_key, max_tokens=100)


STORY_START_PROMPT = _p("STORY_START_PROMPT", PD["STORY_START_PROMPT"])


STORY_STEP_PROMPT = _p("STORY_STEP_PROMPT", PD["STORY_STEP_PROMPT"])


STORY_END_PROMPT = _p("STORY_END_PROMPT", PD["STORY_END_PROMPT"])


def generate_story_start(protagonist: str, custom: str, api_key: str = DEFAULT_KEY) -> str:
    """剧情游戏：生成开局场景 + 3 选项."""
    content = "女主：" + protagonist
    if custom:
        content += "\n剧情方向：" + custom
    return _call_llm(STORY_START_PROMPT, content, api_key, max_tokens=800)


def generate_story_step(history_text: str, choice: str, api_key: str = DEFAULT_KEY) -> str:
    """剧情游戏：根据历史 + 本轮被选选项生成下一段 + 3 新选项."""
    content = "已经发生的剧情：\n" + history_text + "\n\n本轮群友投票选中的选项：" + choice
    return _call_llm(STORY_STEP_PROMPT, content, api_key, max_tokens=800)


def generate_story_end(history_text: str, api_key: str = DEFAULT_KEY) -> str:
    """剧情游戏：生成结局（有余味）."""
    content = "完整剧情：\n" + history_text
    return _call_llm(STORY_END_PROMPT, content, api_key, max_tokens=800)
