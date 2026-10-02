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


# ---------------------------------------------------------------- 数据层（P3-6 拆分）
# 出厂数据住同包四个纯数据模块（nuo / rose_butler / ruojin / registry），
# 这里按**原名** re-import —— 所有 `from personas import X`、`P.PERSONAS`、
# `monkeypatch.setattr(P, ...)` 接缝与拆分前完全一致（所以函数必须留在本模块：
# patch 包属性改的就是本模块的全局，函数读的也是它）。

from .nuo import DEFAULT_NAME_FULL, DEFAULT_NAME_SHORT
from .registry import (CID_PERSONA, PERSONAS, PHRASES, PHRASE_LABELS,
                       ROOMS, ROSE_CID, VV_UID, NUONUO_UID)
from .rose_butler import (ROOM_FLOWER_NOUNS, ROOM_FLOWER_VERBS, ROOM_FLOWER_WORDS,
                          ROOM_GHOST_BACK, ROOM_GHOST_COOLDOWN, ROOM_GHOST_MAX_H,
                          ROOM_GHOST_TEXT, ROOM_GHOST_WARN, ROOM_GHOST_WARN_COOLDOWN,
                          ROOM_GREET_ITEMS, ROOM_GREET_LINES, ROOM_GREET_TEMPLATE,
                          ROOM_GUEST_REFUSE, ROOM_LEWD_WORDS, ROOM_MENU_WORDS,
                          ROOM_ORDER_VERBS, ROSE_BUTLER_EXTRA, ROSE_LUCKY_COLORS,
                          ROSE_LUCKY_DIRS, ROSE_LUCKY_ITEMS, ROSE_MOONS, ROSE_SIGNS,
                          ROSE_SIGN_EMOJI, ROSE_SIGN_JI, ROSE_SIGN_MATCH,
                          ROSE_SIGN_MAX_LINES, ROSE_SIGN_WORDS, ROSE_SIGN_YI)
from .ruojin import RUOJIN_EXTRA, RUOJIN_PHRASES, RUOJIN_TONE_WORDS


_CID = contextvars.ContextVar("persona_cid", default="")


def set_cid(cid: str) -> None:
    _CID.set(cid or "")


def current_cid() -> str:
    return _CID.get() or ""


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
# `me` / `me_full` = 本群人设的自称（见 fill()）—— 写「{me}去翻名册」比写死某个
# 名字稳：同一个 key 在认证群是「小小糯」、在玫瑰房是「薇丝」。
# `full`/`level`/`concl`/`days` 给"跨群固定句"用（运势兜底句、运势趋势空数据）。
ALLOWED_PLACEHOLDERS = ("name", "item", "line", "who", "n", "k", "me", "me_full",
                        "full", "level", "concl", "days")

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


def me(cid: str = None) -> str:
    """本群我该自称什么（短名）—— 认证群是「小小糯」，玫瑰房是「薇丝」."""
    return persona_for(cid)["name_short"] or DEFAULT_NAME_SHORT


def orientation(cid: str = None) -> str:
    """本群人设的取向（代码用的硬过滤依据）：`"straight"` = 只对男性有情欲玩法.

    默认（小小糯那套）返回空串 = 不限制，行为与以前完全一致。
    """
    return str(persona_for(cid).get("orientation") or "")


def is_straight(cid: str = None) -> bool:
    """本群人设是不是"异性恋"（见 `orientation`）."""
    return orientation(cid) == "straight"


def me_full(cid: str = None) -> str:
    """本群我该自称什么（全名，默认带波浪号的那种）."""
    return persona_for(cid)["name_full"] or DEFAULT_NAME_FULL


def all_names(cid: str = None) -> set:
    """本群「我」可能被叫到的名字。

    给「这句话是不是在叫我」这类判定用：玫瑰房的客人叫的是**人设名**「薇丝」，
    代码里写死的「小小糯」只是**默认人设**的名字，两个都得认（改名不能丢旧名，
    否则老群友叫顺口的旧名字会突然没人理）。
    """
    names = set()
    for k in ("name_short", "name_full"):
        nm = str(persona_for(cid).get(k) or "").strip()
        if nm:
            names.add(nm)
            names.add(nm.rstrip("～~ "))   # 「薇丝～」也要能被「薇丝」叫到
    names.add(DEFAULT_NAME_SHORT)
    names.add(DEFAULT_NAME_FULL)
    return {n for n in names if n}


class _KeepUnknown(dict):
    """格式化用的映射：**缺失的占位符原样留着**，不抛 KeyError。

    群里发一条文案时，少填一个占位符最多是文案难看；
    抛异常 = 整条消息发不出去（这是真实事故级差别）。
    """

    def __missing__(self, key):
        return "{" + str(key) + "}"


def fill(text: str, cid: str = None, **kw) -> str:
    """按本群人设填一段**群里文案**：`{me}`/`{me_full}` + 残留的默认名替换。

    与 apply_persona() 的区别：**不追加 extra** —— 这是要发出去的话，不是系统提示词。

    三件事：
    1. `{me}` / `{me_full}` → 本群自称（永不失败，就是字符串替换）；
    2. 文案里写死的默认名（旧代码遗留的「小小糯」）→ 本群自称；
    3. 其余占位符（`{name}`/`{who}`…）用 format_map 填，**填不上的原样留着**，
       绝不抛异常。

    默认人设 + 没有占位符时零开销（直接原样返回）。
    """
    if not text:
        return text
    p = persona_for(cid)
    s = text
    if p["name_short"] != DEFAULT_NAME_SHORT or p["name_full"] != DEFAULT_NAME_FULL:
        s = s.replace(DEFAULT_NAME_FULL, p["name_full"])
        s = s.replace(DEFAULT_NAME_SHORT, p["name_short"])
    s = s.replace("{me_full}", p["name_full"]).replace("{me}", p["name_short"])
    if kw:
        try:
            s = s.format_map(_KeepUnknown(kw))
        except Exception:
            pass   # 文案里有 `{`、`}` 这种非法写法 → 保持原样，别把消息搞没
    return s


def p(key: str, cid: str = None, **kw) -> str:
    """取当前群的固定话术（本群没有就回落默认人设；都没有返回空串）.

    走 phrases_for()，所以配置里改过的话术在这里就已经生效了。
    出口统一过 fill()：`{me}` 占位符和遗留的默认名都在这里按本群人设替换好，
    所以话术表里可以放心写「{me}去翻名册」。
    """
    # 用 persona_key_for 而不是 CID_PERSONA：配置指定的人设也要拿到它自己的话术，
    # 否则会出现"人设换成了房间管家、固定话术还是默认那套"的半吊子状态。
    s = phrases_for(persona_key_for(cid)).get(key)
    if s is None:
        s = PHRASES["nuo"].get(key) or ""
    return fill(s, cid, **kw)


def phrase_is_blank(key: str, cid: str = None) -> bool:
    """这条话术是不是被**明确写成空**（= 要求"什么都不说"）。

    空串与"没写过"是两回事：没写过 → 回落默认话术；写成空串 → 明确静默。
    用途（2026-09-27 屋主："工作群里 /talk、/ask 这类**没在局里就不理**"）：
    若瑾那份把 `dj_idle_unknown` 写成 ""，调用方据此静默；
    认证群没写过这个 key ⇒ 仍旧回 `unknown_cmd`，行为一个字不变。
    """
    k = persona_key_for(cid)
    layers = [PHRASES.get(k) or {}]
    try:
        _cont, phr = _active()
        layers.append(phr.get(k) or {})
    except Exception:                                          # noqa: BLE001
        pass
    for layer in layers:
        if key in layer:
            return not str(layer[key] or "").strip()
    return False
