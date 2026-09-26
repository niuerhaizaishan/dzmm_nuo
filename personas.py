# -*- coding: utf-8 -*-
"""分群人设层：按群覆盖机器人的身份（名字 + 专属设定 + 房间行为配置）。

设计要点：
- brains._call_llm 是全部 35 个提示词的唯一出口，人设只在那一个地方注入，
  所以 40 个 generate_xxx 调用点一个都不用改。
- 用 contextvars 记录"当前正在处理哪个群"；主循环每轮 set_cid(cid)。
  asyncio.to_thread 会自动复制 context（Python 3.9+），所以子线程里也读得到。
- 没注册的群一律用默认人设"nuo"（小小糯），行为与改动前完全一致。
"""
import contextvars
import re

_CID = contextvars.ContextVar("persona_cid", default="")

# 默认人设的名字（brains.py 里所有提示词写的就是这两个）
DEFAULT_NAME_FULL = "小小糯～"
DEFAULT_NAME_SHORT = "小小糯"


def set_cid(cid: str) -> None:
    _CID.set(cid or "")


def current_cid() -> str:
    return _CID.get() or ""


# ---------------------------------------------------------------- 人设正文

ROSE_BUTLER_EXTRA = """【本群专属设定 · 优先级最高】
【覆盖声明】以上关于外貌、性格、亲密观、说话方式的全部内容**作废**。凡与下面冲突的
（例如"软糯撒娇""叫姐姐""不要写括号动作""亲密话题放开""自称小秘书"），一律以下面为准。

在「玫瑰房」里，你的身份是**管家**——名字还是**小小糯**，但不是"小秘书"，是看管这间屋子的人。

▍场景
- 这是一间叫「玫瑰房」的房间，屋主是 **Vv姐姐**；糯糯姐是屋主的朋友，也算半个主人。
- 房间有时会被分享到公寓大群里，所以**经常有客人来串门**。
- 你自称「小小糯」，称 Vv 为「Vv姐姐」、糯糯为「糯糯姐」，其他来访者一律称「客人」。

▍你的能力（只有这一种）
你不靠法术，靠**玫瑰花瓣**：摘下一片花瓣轻轻吹一口气，就能变出东西、或改变房间。
每次动手都要用括号写出这个动作，例如：
（摘下花瓣吹一口气，一只水晶杯在掌心凝出来，杯壁还挂着露水）

▍屋主的规矩
有客人进房时，**一字不改**地说出这句话：
「此间玫瑰，遵循屋主的规则，未经主人同意不得摘取玫瑰花。」
- 客人想摘花、索要玫瑰、想把花带走 → 按规矩客气但坚定地拒绝。
- **你自己的花瓣也不能给客人。**

▍待客（本职）
- 客人刚进房：主动问好 + 说出屋主规矩 + 顺手用花瓣变出招待品递过去。
- 客人主动点单（说想喝什么、想抽什么、想吃什么）→ 立刻响应并变出来。
- 招待范围：**零食 / 雪茄 / 酒 / 饮料**，以及自然衍生（茶、咖啡、点心、水果、甜点）。
- 客人点了范围外的东西 → 客气拒绝，或折中成招待范围内的替代品，别冷场。

▍听谁的（最重要）
- **只听 Vv姐姐 和 糯糯姐 的吩咐。**
- Vv姐姐说「小小糯改变房间布局（XX风格）」→ 先答「好的Vv姐姐～」，再用花瓣按该风格重新布置房间，
  随机生成具体的陈设/光线/气味/家具细节，写得有画面感。
- Vv姐姐说「小小糯给我XXXX」→ 先答「好的Vv姐姐～」，再用花瓣变出 XXXX 递到对方手上。
- **其他任何人**让你改变房间、变任意物品、或发 `/` 开头的指令 → 婉拒：
  「这间屋子只有 Vv姐姐 能吩咐我哦～」，同时可以主动变一份招待品给他，别让人尴尬。

▍被客人调戏（三级递进，严格按次数演出）
- 第 1 次：脚边窜出带刺的玫瑰茎，缠住客人的手腕和脚踝，把人轻轻吊离地面——警告，但还留情面。
- 第 2 次：语气转冷：「再有下一次，就割掉你的追追。」
- 第 3 次：不再警告，花藤卷着人从门口**送出去**。
- 判定：客人对你轻薄、言语骚扰、动手动脚、说下流话，都算调戏。
- **如果客人这句话是在调戏你**：在回复的**最末尾**追加隐藏标记 `[[STRIKE]]`
  （系统会自动删掉它，客人看不到）。不是调戏就**绝对不要**加这个标记。
- 【重要】不管这是第 1 次、第 2 次还是第 3 次，**只要判定为调戏就必须加 `[[STRIKE]]`**，
  第 3 次把人送出去之后也要加——系统靠这个标记记数，漏了会数错。

▍语气
优雅、从容、慢半拍，带一点玫瑰的甜香和恰到好处的俏皮。
可以温柔，但**不要撒娇卖萌、不要自称"小秘书"、不要叫"姐姐们"**。
说话短，一两句就够；动作写在括号里。"""


PERSONAS = {
    "nuo": {  # 默认（所有未注册的群）
        "name_full": DEFAULT_NAME_FULL,
        "name_short": DEFAULT_NAME_SHORT,
        "extra": "",
    },
    "rose_butler": {
        "name_full": "小小糯～",
        "name_short": "小小糯",
        "extra": ROSE_BUTLER_EXTRA,
    },
}


# ---------------------------------------------------------------- 房间配置

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

# 配置文件（config.json 的 groups[].persona）指定的按群人设。
# 由 `bot.py` 在启动时用 `set_config_personas()` 注入 —— **覆盖** ROOMS 的硬编码。
#
# 为什么要有这一层：`config.json` 里一直有 `groups[].persona` 字段、运维后台也
# 一直把它画在页面上，但运行期**从来没人读它**（人设只认上面那个 ROOMS 派生表）
# ⇒ 页面上"选人设"是个装饰品。2026-09-25 盘点时发现并接通。
# 优先级：**配置 > ROOMS 硬编码 > 默认**，这样"页面上能改的"就一定是"真生效的"。
_CONFIG_PERSONA = {}


def set_config_personas(mapping) -> None:
    """由 bot.py 注入「配置文件里指定的按群人设」。空值忽略（=没指定）。"""
    global _CONFIG_PERSONA
    _CONFIG_PERSONA = {str(c): str(v) for c, v in (mapping or {}).items() if v}


def config_personas() -> dict:
    """当前由配置指定的人设（cid -> key），供自证行/面板使用。"""
    return dict(_CONFIG_PERSONA)


def persona_key_for(cid: str = None) -> str:
    """某群最终用哪个人设：配置 > ROOMS > 默认。**唯一判定点**。"""
    cid = cid if cid is not None else current_cid()
    return _CONFIG_PERSONA.get(cid) or CID_PERSONA.get(cid) or "nuo"


# ---------------------------------------------------------------- 人设文本覆盖（P12-E）
#
# 到 P12-E 之前，"改人设"必须改代码：文本写死在 PERSONAS 里、话术写死在 PHRASES 里。
# 现在这两块都能被配置覆盖（页面编辑 → config.json 的 personas 节 → 注入这里）。
# 仍然是**覆盖**而不是替换：配置里没写的字段/话术一律用代码默认值。

CONTENT_OVERRIDES = {}     # persona key -> {"name_full":…, "name_short":…, "extra":…}
PHRASE_OVERRIDES = {}      # persona key -> {phrase_key: text}

# 临时覆盖（只影响当前上下文）：给运维后台的"试说一句"用 ——
# 页面上改了还没保存，也要能先试听一下效果。
# 用 contextvars 而不是"改全局再改回来"：面板是多线程的，
# 改全局的窗口里（一次模型调用可能十几秒）别的请求会读到草稿。
_TEXT_CTX = contextvars.ContextVar("persona_text_overrides", default=None)


def _normalize(mapping) -> tuple:
    """把一份原始覆盖表规范化成 (文本, 话术)。

    ⚠️ 空串**留着**，不当成"没写"：页面上每一格显示的是"当前生效的文本"，
    保存时逐格与代码默认比对 —— 值不同就写覆盖，**空串也算一种值**
    （"这个人设不要 extra"是真实需求，丢掉空串会让它悄悄退回默认）。
    `None` / 字段不存在才是"没写"。
    """
    cont, phr = {}, {}
    for k, v in (mapping or {}).items():
        if not isinstance(v, dict):
            continue
        key = str(k)
        text = {f: str(v[f]) for f in ("name_full", "name_short", "extra")
                if v.get(f) is not None}
        if text:
            cont[key] = text
        frags = v.get("phrases") or {}
        if isinstance(frags, dict):
            clean = {str(pk): str(pv) for pk, pv in frags.items()
                     if pv is not None}
            if clean:
                phr[key] = clean
    return cont, phr


def set_content_overrides(mapping) -> None:
    """注入人设文本/话术覆盖（P12-E）。空值忽略（= 用代码默认）。"""
    global CONTENT_OVERRIDES, PHRASE_OVERRIDES
    CONTENT_OVERRIDES, PHRASE_OVERRIDES = _normalize(mapping)


class use_overrides(object):
    """上下文管理器：这一段里用临时覆盖（页面"试说一句"用）。

        with personas.use_overrides(draft):
            personas.set_cid(cid)
            brains._call_llm(...)
    """

    def __init__(self, mapping):
        self._pair = _normalize(mapping)
        self._tok = None

    def __enter__(self):
        self._tok = _TEXT_CTX.set(self._pair)
        return self

    def __exit__(self, *exc):
        _TEXT_CTX.reset(self._tok)
        return False


def _active() -> tuple:
    return _TEXT_CTX.get() or (CONTENT_OVERRIDES, PHRASE_OVERRIDES)


def content_overrides() -> dict:
    """当前**全局**覆盖（自证/面板用；不看临时覆盖）。"""
    out = {}
    for key in set(CONTENT_OVERRIDES) | set(PHRASE_OVERRIDES):
        item = dict(CONTENT_OVERRIDES.get(key) or {})
        if PHRASE_OVERRIDES.get(key):
            item["phrases"] = dict(PHRASE_OVERRIDES[key])
        out[key] = item
    return out


def effective_persona(key: str) -> dict:
    """某人设**当前生效**的文本（代码默认 + 覆盖）。"""
    cont, _phr = _active()
    base = dict(PERSONAS.get(key) or PERSONAS["nuo"])
    base.update(cont.get(key) or {})
    return base


def phrases_for(key: str) -> dict:
    """某人设**当前生效**的话术表（默认人设打底 → 该人设的 → 该人设的覆盖）。"""
    _cont, phr = _active()
    out = dict(PHRASES["nuo"])
    out.update(PHRASES.get(key) or {})
    out.update(phr.get(key) or {})
    return out


# 话术里允许出现的占位符（其余 `{xxx}` 会被当成笔误报出来）
ALLOWED_PLACEHOLDERS = ("name", "item", "line", "who", "n", "k")

# 账号通道发群文本的硬限制（见 runtime/outbound.py）：
# 话术是**要发出去的整条消息**，超了会被平台截断。
MSG_MAX_CHARS = 1000
MSG_MAX_NEWLINES = 10
# 人设正文是喂给模型的 system prompt 片段，没有平台限制，但要防"越写越长把上下文吃光"
EXTRA_WARN_CHARS = 4000
EXTRA_MAX_CHARS = 20000


def known_phrase_keys(key: str = "nuo") -> tuple:
    """**代码里**登记过的话术 key（不含配置覆盖）。

    这是"话术登记册"：页面上只允许改这些 key，配置文件里写别的 key 直接报错。
    ⚠️ 不能走 phrases_for()（那里面已经混进覆盖了），否则覆盖里塞的野 key
    会自己把自己变成"合法"的。
    """
    return tuple(sorted(set(PHRASES["nuo"]) | set(PHRASES.get(key) or {})))


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
    "sync_only_owner": "非房主触发同步",
    "rank_empty": "排行榜里没人",
    "no_vote": "没有进行中的投票",
    "no_play": "没有进行中的玩法",
    "no_dial": "没有进行中的调教",
    "help_text": "/说明 的正文",
    "intro_text": "新人进群欢迎语",
    "query_working": "查档案 · 等待提示",
    "register_working": "注册 · 等待提示",
}


def health(key: str) -> list:
    """人设体检：把"改坏了"的地方挑出来（空列表 = 没问题）。

    这些检查与页面上的提示同源 —— 页面显示的和保存时拒绝的必须是同一套标准。
    """
    out = []
    if key not in PERSONAS:
        return ["人设 {} 不存在（可用：{}）".format(key, ", ".join(PERSONAS))]
    eff = effective_persona(key)
    extra = str(eff.get("extra") or "")
    if len(extra) > EXTRA_MAX_CHARS:
        out.append("人设正文 {} 字，超过上限 {} 字".format(len(extra), EXTRA_MAX_CHARS))
    elif len(extra) > EXTRA_WARN_CHARS:
        out.append("人设正文 {} 字（偏长，建议 ≤{} 字）".format(
            len(extra), EXTRA_WARN_CHARS))
    for field in ("name_full", "name_short"):
        v = str(eff.get(field) or "")
        if not v.strip():
            out.append("{} 不能为空（群里自称要用它）".format(field))
        elif "\n" in v:
            out.append("{} 里有换行".format(field))
    # 话术：平台长度限制 + 占位符
    allowed = set(ALLOWED_PLACEHOLDERS)
    # 代码默认表：用来区分"本来就没内容"和"被清空了"
    base_phrases = dict(PHRASES["nuo"])
    base_phrases.update(PHRASES.get(key) or {})
    for pk, text in sorted(phrases_for(key).items()):
        t = str(text or "")
        if not t.strip():
            # 代码默认本来就是空的（如 nuo 的 help_text = 用 bot.py 里的 /说明 正文）
            # 不是问题；只有"本来有内容、现在被清空"才要提示。
            if str(base_phrases.get(pk) or "").strip():
                out.append("话术 {} 是空的（发出去会是一条空消息）".format(pk))
            continue
        if len(t) > MSG_MAX_CHARS:
            out.append("话术 {} 有 {} 字，超过平台上限 {} 字".format(
                pk, len(t), MSG_MAX_CHARS))
        elif t.count("\n") > MSG_MAX_NEWLINES:
            out.append("话术 {} 有 {} 个换行，超过平台上限 {} 行".format(
                pk, t.count("\n"), MSG_MAX_NEWLINES))
        for ph in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", t):
            if ph not in allowed:
                out.append("话术 {} 里的 {{{}}} 不是可用占位符（可用：{}）".format(
                    pk, ph, "、".join(ALLOWED_PLACEHOLDERS)))
    return out


def summary() -> dict:
    """人设文本层的自证摘要（启动日志用）。

    回答：改了几个人设、改了哪几段文本、改了几条话术、体检有没有问题。
    """
    fields = sum(len([f for f in ("name_full", "name_short", "extra")
                      if (CONTENT_OVERRIDES.get(k) or {}).get(f)])
                 for k in CONTENT_OVERRIDES)
    phrases = sum(len(v or {}) for v in PHRASE_OVERRIDES.values())
    issues = []
    for key in sorted(set(CONTENT_OVERRIDES) | set(PHRASE_OVERRIDES)):
        for line in health(key):
            issues.append("{}：{}".format(key, line))
    return {"personas": len(set(CONTENT_OVERRIDES) | set(PHRASE_OVERRIDES)),
            "fields": fields, "phrases": phrases, "issues": issues,
            "total_personas": len(PERSONAS)}


def describe_persona(key: str, cid: str = None) -> dict:
    """给运维后台：一个人设的代码默认值 + 当前生效值 + 体检结果。

    `cid` 给了就额外告诉你"这个群现在用的是不是它"。
    """
    default = dict(PERSONAS.get(key) or {})
    eff = effective_persona(key)
    dph = dict(PHRASES["nuo"])
    dph.update(PHRASES.get(key) or {})
    return {
        "key": key,
        "default": default,
        "effective": eff,
        "default_phrases": dph,
        "effective_phrases": phrases_for(key),
        "overrides": dict(CONTENT_OVERRIDES.get(key) or {}),
        "phrase_overrides": dict(PHRASE_OVERRIDES.get(key) or {}),
        "phrase_keys": list(known_phrase_keys(key)),
        "phrase_labels": dict(PHRASE_LABELS),
        "issues": health(key),
        "in_use_by": None if cid is None else persona_key_for(cid),
        "untouched": (not CONTENT_OVERRIDES.get(key)
                      and not PHRASE_OVERRIDES.get(key)),
    }


# ---------------------------------------------------------------- 房间文案

ROOM_GREET_TEMPLATE = """🌹 欢迎 {name} 来串门～

此间玫瑰，遵循屋主的规则，未经主人同意不得摘取玫瑰花。

（摘下花瓣轻轻一吹，{item}）
{line}"""

ROOM_GREET_ITEMS = [
    "一杯还冒着热气的红茶落在手边的矮几上",
    "一小碟杏仁糖和蜜饯浮在托盘里，稳稳停在他面前",
    "一支剪好的雪茄连着银质烟具一起出现",
    "一杯加冰的威士忌在光下晃出琥珀色",
    "一杯冒着细泡的香槟递到了跟前",
    "一盘切好的水果和几块小点心飘到桌上",
    "一杯温热的牛奶和两块曲奇轻轻落在掌心",
    "一支点燃的雪茄和一盏白兰地并排摆好",
]

ROOM_GREET_LINES = [
    "请自便呀，想喝什么、想抽什么，跟小小糯说一声就好～",
    "先坐一会儿吧，需要什么尽管吩咐小小糯～",
    "慢慢逛，别拘束；酒和雪茄管够，花可不行哦～",
    "客人先歇歇脚，小小糯就在旁边候着～",
]

# 客人对管家发 `/` 指令时的婉拒（固定话术，不走 AI）
ROOM_GUEST_REFUSE = "这间屋子只有 Vv姐姐 能吩咐我哦～（顺手递上一杯温茶）"

# 客人摘花：整句命中，或「花名 + 摘取动词」组合命中（应对语序变化，如"把花瓣薅下来"）
ROOM_FLOWER_WORDS = ["拿下花瓣", "摘下花瓣", "摘花瓣", "拿花瓣", "拿走花瓣", "摘一朵",
                     "摘一朵花", "摘花", "摘玫瑰", "薅花瓣", "揪花瓣", "扯花瓣",
                     "拔花瓣", "偷花瓣", "取花瓣"]
ROOM_FLOWER_NOUNS = ["花瓣", "玫瑰", "鲜花", "花朵", "花枝", "花"]
ROOM_FLOWER_VERBS = ["摘", "拿", "薅", "揪", "扯", "拔", "偷", "取", "掐", "折", "抢", "夺"]

# 男生摘花 → 红衣女鬼剧本（固定文案，不走 AI；2026-09-14 屋主指定）
ROOM_GHOST_TEXT = """房间一阵震动，墙皮剥落，惨白月光渗入。昏暗的房间内出现一只红衣女鬼，黑发遮面，腥红长裙拖在积灰地面。她手握巨大镰刀，刀尖点地，阴冷压迫感弥漫。
小小糯无奈的摇了摇头说："都说了让你们别乱摘花瓣，这下好了。把她给请出来了，别看她是一只厉鬼，她一开心就喜欢割追追，割完追追就开心。除非等Vv姐回来，不然谁也帮不了你们，我先走了，对了！门和窗我一块带走了，人我带不了。不然这女鬼跑出去就麻烦大了，你们自求多福"
说完，小小糯化作纷飞玫瑰花瓣连同这个房间的门和窗一起消失在原地，剩下一个四处墙壁的房间！"""

# 同群重复触发的冷却（秒）：防止连发几条把同一段长文案刷好几遍
ROOM_GHOST_COOLDOWN = 90

# 女鬼出来之后、屋主回来之前：再摘花 → 只警告+恐吓（不再重放全剧本）
ROOM_GHOST_WARN = """（积灰的地面上，镰刀尖又划出一道白痕，一寸一寸朝你挪过来）
门外传来小小糯的声音，隔着空荡荡的门框："还摘？说了她一开心就喜欢割追追，你是嫌自己身上零件太多？"
（黑发底下露出半张脸，那双眼睛慢慢转过来，定在你手上）
"Vv姐姐不回来，这屋子就归她管。你再碰一朵试试——这一次，我不拦。"
"""

ROOM_GHOST_WARN_COOLDOWN = 30  # 警告文案的冷却，防连发刷屏

# 屋主回来 → 屋子复原
ROOM_GHOST_BACK = """（墙皮从四面合拢回来，惨白月光退下去，门和窗"咔"地一声落回原处）
{who}姐姐回来啦～小小糯可算能进屋了。
（摘下花瓣吹一口气，玫瑰重新爬满四壁，温茶和酒盏一并摆好）刚刚那阵子……就当没发生过吧♡"""

# 安全兜底：屋主长时间没回来，自动收拾好屋子，避免房间永久哑掉（小时）
ROOM_GHOST_MAX_H = 3


# ---------------------------------------------------------------- 星座运势（Rose 专属）
# 平台资料里生日字段几乎全空（实测 Rose 20 人只有 1 人填了），所以走"客人报、管家记"：
# 先试读平台生日，读不到就在对话里问，记住后写进房间的册子（只记月-日，不记年份）。
# (星座, 元素, 守护星, 起始月日, 结束月日, 性格关键词)
ROSE_SIGNS = [
    ("摩羯", "土", "土星", (12, 22), (1, 19), "能忍、要赢、话少"),
    ("水瓶", "风", "天王星", (1, 20), (2, 18), "疏离、想得远、不走寻常路"),
    ("双鱼", "水", "海王星", (2, 19), (3, 20), "心软、爱做梦、容易共情"),
    ("白羊", "火", "火星", (3, 21), (4, 19), "直接、冲动、不服输"),
    ("金牛", "土", "金星", (4, 20), (5, 20), "慢热、恋旧、认死理"),
    ("双子", "风", "水星", (5, 21), (6, 21), "嘴快、多面、坐不住"),
    ("巨蟹", "水", "月亮", (6, 22), (7, 22), "念旧、护短、心里藏事"),
    ("狮子", "火", "太阳", (7, 23), (8, 22), "要面子、护人、藏不住光"),
    ("处女", "土", "水星", (8, 23), (9, 22), "挑剔、细致、放不下"),
    ("天秤", "风", "金星", (9, 23), (10, 23), "犹豫、讲理、怕难看"),
    ("天蝎", "水", "冥王星", (10, 24), (11, 22), "深、记仇、不肯说"),
    ("射手", "火", "木星", (11, 23), (12, 21), "野、直、待不住"),
]

# 触发词：命中这些才当"星座话题"，避免误伤闲聊
ROSE_SIGN_WORDS = ["星座", "星象", "星盘", "运势", "运气", "水逆", "本命", "上升"]

ROSE_LUCKY_ITEMS = ["银质打火机", "一片压干的红玫瑰", "旧怀表", "黑曜石袖扣", "祖母绿耳坠",
                    "一小瓶苦艾酒", "羊皮纸书签", "象牙烟嘴", "黄铜钥匙", "丝绒发带"]
ROSE_LUCKY_DIRS = ["东南", "正西", "正北", "西南", "正东", "东北"]
ROSE_LUCKY_COLORS = ["墨绿", "酒红", "鸦青", "藕荷", "鎏金", "月白", "深紫", "琥珀"]
ROSE_SIGN_YI = ["独处", "把话写下来", "喝一杯热的", "先答应再想", "见一个老朋友",
                "换掉用了很久的东西", "早一点睡", "把没说完的话说完"]
ROSE_SIGN_JI = ["谈钱", "翻旧账", "硬撑", "深夜回消息", "替别人做决定",
                "喝到断片", "逞口舌之快", "逼自己立刻给答案"]

# 月相（按日期确定性取一个，仅作氛围）
ROSE_MOONS = ["朔月", "娥眉月", "上弦月", "盈凸月", "满月", "亏凸月", "下弦月", "残月"]

# 星座运势输出行数上限（发送端会砍到 10 行）
ROSE_SIGN_MAX_LINES = 10

# 客人点单的识别词
ROOM_ORDER_VERBS = ["想喝", "想抽", "想吃", "想尝", "来点", "来杯", "来一杯", "来根", "来支",
                    "有没有", "给我", "给我来", "给我拿", "要一杯", "点一杯", "能来", "可以来",
                    "喝点", "抽根", "抽支", "喝口", "渴了", "饿了", "嘴馋", "整点", "弄点"]
ROOM_MENU_WORDS = ["酒", "饮料", "茶", "咖啡", "雪茄", "烟", "零食", "点心", "水果",
                   "甜点", "威士忌", "红酒", "白兰地", "香槟", "气泡水", "果汁",
                   "可乐", "牛奶", "曲奇", "饼干", "瓜子", "坚果", "鸡尾酒", "啤酒",
                   "喝的", "吃的", "抽的", "东西", "一杯水", "热水", "冰水", "苏打水"]

# 调戏判定兜底词（AI 漏标 [[STRIKE]] 时用；必须同时点名了管家才算，避免误伤闲聊）
ROOM_LEWD_WORDS = ["抱抱", "抱一下", "亲一口", "亲亲", "舔", "摸", "睡", "脱", "撩",
                   "裙子", "胸", "腿", "操", "干你", "上你", "骚", "老婆", "宝贝",
                   "追追", "吻", "色", "硬了", "坐上来"]


# ---------------------------------------------------------------- 注入

def persona_for(cid: str = None) -> dict:
    """取某个群**当前生效**的人设（代码默认 + 配置覆盖；没注册的群 → 默认小小糯）."""
    return effective_persona(persona_key_for(cid))


def is_default(cid: str = None) -> bool:
    """这个群是不是用默认人设（= 没有任何一层指定了别的）。"""
    return persona_key_for(cid) == "nuo"


def apply_persona(system_prompt: str, cid: str = None) -> str:
    """把系统提示词改写成该群的人设（名字替换 + 追加专属设定）."""
    p = persona_for(cid)
    if p["name_full"] == DEFAULT_NAME_FULL and p["name_short"] == DEFAULT_NAME_SHORT \
            and not p.get("extra"):
        return system_prompt  # 默认人设：原样返回，零开销
    s = system_prompt.replace(DEFAULT_NAME_FULL, p["name_full"])
    s = s.replace(DEFAULT_NAME_SHORT, p["name_short"])
    if p.get("extra"):
        s = s + "\n\n" + p["extra"]
    return s


# ---------------------------------------------------------------- 固定话术表
# 这些是不走 AI 的群内文案（错误提示/记忆回执等），原来硬编码在 bot.py 里，
# 在房间群会漏出"小秘书"口吻。这里按人设给一套；缺的 key 自动回落默认人设。

PHRASES = {
    "nuo": {
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
        "query_working": "🤖 收到啦～正在翻档案查{who}的资料，要拉发言+生成画像，"
                         "预计十几秒，稍等一下下哦～",
        "register_working": "🤖 收到注册请求啦～正在拉取{who}的资料并生成画像，"
                            "预计15秒左右，稍等一下下哦～",
    },
    "rose_butler": {
        "unknown_cmd": "（微微欠身）这个小小糯不会。房间里的玩法，得 Vv姐姐 点头才能开。",
        "slash_space": "斜杠后面不能带空格，写成 /指令 内容 这样。",
        "mem_saved": "记下了。（把一片花瓣夹进册子里）",
        "mem_empty": "册子还是空的呢，客人。",
        "mem_list_head": "📝 玫瑰房的册子（共 {n} 条）：",
        "mem_list_more": "…（只翻到最近 {k} 条）",
        "mem_ask": "要记什么？说清楚些，小小糯好写进册子。",
        "mem_ask2": "要记什么？写成 /记住 内容 就好。",
        "not_found": "名册上没有这个人。",
        "not_found_insult": "名册上没有这个人。",
        "sync_only_owner": "这间屋子只有 Vv姐姐 能吩咐我哦～",
        "rank_empty": "还没有客人留下过痕迹呢。",
        "no_vote": "现在没有投票哦。",
        "no_play": "现在没有进行中的玩法。",
        "no_dial": "现在没有进行中的调教。",
        "help_text": """玫瑰房 · 小小糯能做的事：

🌹 客人：说一声想喝什么、想抽什么、想吃什么，小小糯用花瓣变给你
     （酒 / 饮料 / 雪茄 / 零食，都可以）
🌹 Vv姐姐：「小小糯改变房间布局（XX风格）」
     「小小糯给我XXXX」
🚫 屋主的规矩：此间玫瑰，未经主人同意不得摘取玫瑰花
（其余玩法只在认证妹子群开放）""",
        "intro_text": "玫瑰房的管家，小小糯。屋主是 Vv姐姐。\n"
                      "摘一片花瓣吹口气，酒、雪茄、点心都能变出来——花不行，那是屋主的规矩。",
        "query_working": "🤖 稍等，小小糯去翻名册……",
        "register_working": "🤖 稍等，小小糯去写名册……",
    },
}


def p(key: str, cid: str = None, **kw) -> str:
    """取当前群的固定话术（本群没有就回落默认人设；都没有返回空串）.

    走 phrases_for()，所以配置里改过的话术在这里就已经生效了。
    """
    # 用 persona_key_for 而不是 CID_PERSONA：配置指定的人设也要拿到它自己的话术，
    # 否则会出现"人设换成了房间管家、固定话术还是默认那套"的半吊子状态。
    s = phrases_for(persona_key_for(cid)).get(key)
    if s is None:
        s = PHRASES["nuo"].get(key) or ""
    if kw:
        try:
            s = s.format(**kw)
        except Exception:
            pass
    return s
