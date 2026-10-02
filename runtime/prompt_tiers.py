# -*- coding: utf-8 -*-
"""文案模板的分档与校验（P12-G）。

### 为什么要分档

40 个提示词**不能一视同仁**：改坏一个问候语只是"话说得不好"，
改坏 `HEAT_JUDGE` 或 `STORY_STEP` 是**功能报错**（发情值不涨、剧情卡死）。
所以按"改坏的后果"分三档，三档的编辑方式与校验手段都不同（方案 §10.2/§10.3）：

| 档 | 数量 | 是什么 | 校验 |
|---|---|---|---|
| **C1** | 23 | 自由文案（输出是自由文本） | 非空 + 「必须保留的片段」 |
| **C2** | 11 | 形状敏感（不许换行 / 固定行数 / 固定项数） | C1 的 + 形状声明 + 行数字数上限（可自测） |
| **C3** | 6 | 输出被**程序解析**（JSON / 【1】【2】【3】） | C1 的 + 契约字面量 + **契约自测**（真调一次模型） |

⚠️ 分档是**从真实解析点数出来的**，不是照抄方案里那份估价：方案 §10.2 的
清单有两处重叠（`RANK`、`POEM_THEME` 同时出现在 C1/C2），而且把
`STORY_END` 当成带三选项的（其实只有开局/推进有选项，结局是 8 行以内的收尾）。
实现时逐个对过代码：`_extract_xp`（JSON）、`_parse_judge`（JSON）、
`generate_vote_options`（JSON）、`runtime/memory._ai_filter`（逐行）、
`runtime/story.py`（`【1】【2】【3】` 缺了就重试）。分区由
`tests/test_prompt_tiers.py` 钉死：40 个一个不多一个不少。

### 校验分两层

1. **静态**（`validate`）：非空、必须保留的片段、片段最少出现次数。
   这是"**不可能改崩**"的那一层：把契约字面量删掉直接被拒，不用等模型。
2. **自测**（`run_selftest`）：用**固定样例输入**真调一次模型，检查输出是否符合契约
   （JSON 能否解析、字段在不在、【1】【2】【3】齐不齐、换行数超没超）。
   保存时跑一次，失败就**一个字都不写** —— 把"事后发现功能坏了"变成"保存时被拒"。
"""
import json
import re

__all__ = ["PromptSpec", "SPECS", "TIERS", "TIER_LABEL", "spec", "all_specs",
           "by_tier", "tier_counts", "validate", "check_contract",
           "run_selftest", "CONTRACTS", "MAX_MSG_CHARS", "MAX_MSG_NEWLINES"]

# 账号通道发群消息的硬限制（见 runtime/outbound.py 的 group_message_chunks）
MAX_MSG_CHARS = 1000
MAX_MSG_NEWLINES = 10

TIERS = ("C1", "C2", "C3")
TIER_LABEL = {
    "C1": "C1 · 自由文案",
    "C2": "C2 · 形状敏感",
    "C3": "C3 · 结构化输出（保存即自测）",
}
TIER_NOTE = {
    "C1": "输出是自由文本，改坏了只是话说得不好。",
    "C2": "输出有形状约束（不换行 / 固定行数 / 固定项数），破坏了会被平台截断或把卡片撑坏。",
    "C3": "输出要被程序解析，破坏了不是难看而是**功能报错**。保存时会真调一次模型做契约自测。",
}
CONTRACTS = ("json_list", "json_objects", "lines", "three_options")


class PromptSpec(object):
    """一个提示词的分档信息（不可变，纯数据）。"""

    __slots__ = ("key", "tier", "title", "note", "must_keep", "min_counts",
                 "max_newlines", "max_chars", "contract", "sample_system",
                 "sample_user", "expect_nonempty", "expect_any_of")

    def __init__(self, key, tier, title, note="", must_keep=(), min_counts=(),
                 max_newlines=None, max_chars=None, contract="",
                 sample_system=None, sample_user=None, expect_nonempty=False,
                 expect_any_of=()):
        assert tier in TIERS, tier
        assert not contract or contract in CONTRACTS, contract
        self.key = key
        self.tier = tier
        self.title = title
        self.note = note
        self.must_keep = tuple(must_keep)
        self.min_counts = tuple(min_counts)
        self.max_newlines = max_newlines
        self.max_chars = max_chars
        self.contract = contract
        # 自测用：把这段文本当**用户输入**（system 就是被编辑的提示词本身）
        self.sample_system = sample_system     # 仅 MEMORY_FILTER 需要（{limit} 之类）
        self.sample_user = sample_user
        self.expect_nonempty = expect_nonempty
        self.expect_any_of = tuple(expect_any_of)


def _c1(key, title, note="", **kw):
    return PromptSpec(key, "C1", title, note, **kw)


def _c2(key, title, note="", **kw):
    return PromptSpec(key, "C2", title, note, **kw)


def _c3(key, title, note="", **kw):
    return PromptSpec(key, "C3", title, note, **kw)


# ---------------------------------------------------------------- 41 个
_SPECS = [
    # ============ C3 · 输出被程序解析（6 个） ============
    _c3("HEAT_JUDGE_PROMPT", "发情值评定",
        "输出必须是 JSON 数组；解析失败 ⇒ 发情值不涨（静默失效）",
        must_keep=("name", "delta", "JSON"),
        contract="json_objects",
        sample_user="小糯: 姐姐～我好想你，抱抱我嘛\n阿离: 今晚来我房间，跪着等我",
        expect_nonempty=True),
    _c3("VOTE_OPTIONS_PROMPT", "投票选项生成",
        "输出必须是 JSON 数组；解析失败 ⇒ 投票没有选项",
        must_keep=("JSON 数组",),
        contract="json_list",
        sample_user="主题：周末玩什么\n群成员：小糯、阿离、清歌",
        expect_nonempty=True),
    _c3("XP_PROMPT", "XP 标签提炼",
        "输出必须是 JSON 数组（`_extract_xp` 用 json.loads 解析）",
        must_keep=("JSON 数组",),
        contract="json_list",
        sample_user="昵称：小糯 性别：女 签名：想被主人拴着 简介：喜欢露出和黑丝",
        expect_nonempty=True),
    _c3("MEMORY_FILTER_PROMPT", "记忆精筛（唯一一条在 runtime/memory.py 里的）",
        "逐行输出原文（`_ai_filter` 按行比对），必须保留 `{limit}` 占位符",
        must_keep=("{limit}",),
        contract="lines",
        sample_system="{limit}",
        sample_user="问题：我喜欢吃什么\n\n记忆条目：\n- 我喜欢吃芒果\n- 最近在减肥",
        expect_any_of=("我喜欢吃芒果", "无")),
    _c3("STORY_START_PROMPT", "剧情开局",
        "结尾必须给【1】【2】【3】三个选项；缺了剧情走不下去",
        must_keep=("【1】", "【2】", "【3】"),
        contract="three_options",
        sample_user="女主昵称：小糯\n剧情方向：教室",
        expect_nonempty=True),
    _c3("STORY_STEP_PROMPT", "剧情推进",
        "同上：`runtime/story.py` 检查不到三选项会重试，再不行就报错",
        must_keep=("【1】", "【2】", "【3】"),
        contract="three_options",
        sample_user="【已有剧情】小糯被留在教室里\n【玩家的选择】【1】转身离开",
        expect_nonempty=True),

    # ============ C2 · 形状敏感（11 个） ============
    _c2("CMD_PROMPT", "主人格（@我 & 兜底聊天）",
        "体量最大、影响面最广的一篇。**分段结构不许删**：外貌/性格/说话方式/规则/示例",
        must_keep=("【外貌】", "【性格】", "【说话方式】", "【规则】", "示例："),
        min_counts=(("用户:", 4), ("你:", 4)),
        sample_user="在吗？今天心情不太好",
        max_newlines=None),
    _c2("AT_FRAME_PROMPT", "@ 回复的收尾块（谁在说话 / 他这一句 / 只回这一句）",
        "⚠️ 这条不是人设，是**拼进用户消息最后一块**的结构（P12-AP）。"
        "三个【】标记和 {who}/{q} 占位符缺一个，就等于退回"
        "『当前这句混在历史里』的老毛病 —— 实测她会把一句『摸摸头』读成"
        "『糯糯姐冒头了』，然后对着不相干的人把待办念一遍。"
        "占位符用 str.replace 填（正文里有花括号，不能 format）；没有自测样例（不调模型）。",
        must_keep=("【现在跟你说话的人】", "【他这一句】", "【怎么回】", "{who}", "{q}")),
    _c2("FORTUNE_PROMPT", "今日运势点评",
        "只写一句、不换行，拼进定长卡片",
        must_keep=("不要换行", "一句"),
        max_newlines=0, contract="", sample_user="昵称：小糯 分数：88 日期：3月3日",
        max_chars=200),
    _c2("HOROSCOPE_PROMPT", "星座运势正文",
        "一段、绝不换行、160~240 字",
        must_keep=("不要换行", "一段"),
        max_newlines=0, max_chars=400, sample_user="星座：天秤座 元素：风象"),
    _c2("TREND_PROMPT", "趋势回顾",
        "一段、绝不换行、160~240 字，拼进趋势卡",
        must_keep=("不要换行", "一段"),
        max_newlines=0, max_chars=400,
        sample_user="7 天分数：60,70,80,55,90,88,72 均值 73 最高 90 最低 55"),
    _c2("RECAP_PROMPT", "缺勤回顾",
        "三段（💬/🎭/📌），段内每条一行以「· 」开头，每段 2~3 条",
        must_keep=("💬", "🎭", "📌", "· "),
        max_newlines=14, sample_user="提问的人：小糯\n群里聊了打本和晚饭"),
    _c2("STORY_END_PROMPT", "剧情结局",
        "8 行以内的收尾，不带选项",
        must_keep=("8 行",),
        max_newlines=8, sample_user="【完整历史】小糯在教室里被留下"),
    _c2("RANK_PROMPT", "骚度排行榜",
        "固定编号表 1~5 + 总评一行",
        must_keep=("总评",),
        sample_user="成员：小糯 88 分；阿离 76 分；清歌 70 分"),
    _c2("POEM_PROMPT", "赋诗",
        "句数严格（词牌/七绝…），多行输出",
        must_keep=("句",),
        max_newlines=12),
    _c2("POEM_THEME_PROMPT", "命题赋诗",
        "同上：句数由词牌决定",
        must_keep=("句",),
        max_newlines=12),
    _c2("PERF_NOVEL_PROMPT", "公演小说（正文）",
        "20+ 条硬约束（逐条不漏、不编造、序号不重置）——**大改前先备份**",
        must_keep=("宁长勿缺", "禁止编造"),
        max_newlines=None, max_chars=None),
    _c2("PERF_NOVEL_CONTINUE_PROMPT", "公演小说（续写）",
        "同上，且不许重复已写过的内容",
        must_keep=("宁长勿缺",),
        max_newlines=None, max_chars=None),

    # ============ C1 · 自由文案（23 个） ============
    _c1("GREETING_PROMPT", "新人进群问候"),
    _c1("CUTE_ROAST_PROMPT", "可爱吐槽（主动冒泡）"),
    _c1("DEVIL_ROAST_PROMPT", "恶魔吐槽（主动冒泡）"),
    _c1("INSULT_PROMPT", "羞辱"),
    _c1("INSULT_CONTINUE_PROMPT", "羞辱（加长）"),
    _c1("DIAOJIAO_PROMPT", "调教"),
    _c1("DIAOJIAO_END_PROMPT", "调教收尾"),
    _c1("SCENE_PROMPT", "场景（发骚）"),
    _c1("SCENE_CONTINUE_PROMPT", "场景推进"),
    _c1("SCENE_REWRITE_PROMPT", "场景重写"),
    _c1("SCENE_END_PROMPT", "场景收尾"),
    _c1("SUMMARY_PROMPT", "群聊总结"),
    _c1("MEMORY_SUMMARY_PROMPT", "记忆总结"),
    _c1("PERSON_SUMMARY_PROMPT", "个人近况"),
    _c1("PROFILE_PROMPT", "档案介绍"),
    _c1("ARCHIVE_PROMPT", "档案"),
    _c1("PERF_ENRICH_PROMPT", "公演实时丰富"),
    _c1("PERF_INSULT_PROMPT", "公演羞辱"),
    _c1("PERF_COMMENT_PROMPT", "公演点评"),
    _c1("PERF_ENDING_PROMPT", "公演收尾"),
    _c1("HEAT_CLIMAX_PROMPT", "高潮判定文案"),
    _c1("HEAT_FASAO_PROMPT", "发骚判定文案"),
    _c1("DEFEND_PROMPT", "护主（当前未接线）",
        "盘点里标记为死代码；保留登记，改它不影响线上"),
]

SPECS = {s.key: s for s in _SPECS}


def spec(key: str):
    return SPECS.get(key)


def all_specs():
    return [SPECS[k] for k in _ORDER]


# 保持与 prompts 登记册一致的顺序（C3 → C2 → C1），页面上按档分组画
_ORDER = [s.key for s in _SPECS]


def by_tier():
    out = {t: [] for t in TIERS}
    for s in all_specs():
        out[s.tier].append(s)
    return out


def tier_counts():
    out = {t: 0 for t in TIERS}
    for s in all_specs():
        out[s.tier] += 1
    return out


# ---------------------------------------------------------------- 静态校验

def validate(key: str, text: str) -> list:
    """保存前的**静态**校验：返回问题清单（空 = 通过）。

    这一层不花钱、不调模型，专门挡"把契约字面量删了"这类错误。

    ⚠️ 这里**不检查长度**：`max_chars` / `max_newlines` 是**输出**（模型生成的那段）
    的限制，而本函数拿到的是**提示词本身** —— 提示词 498 字、要求输出 200 字，
    完全是正常的事。输出的形状由 `run_selftest()` 对着真回复检查。
    """
    s = SPECS.get(key)
    if s is None:
        return ["未登记的提示词 {}".format(key)]
    out = []
    if not (text or "").strip():
        return ["内容不能为空（想恢复代码默认值请用「恢复默认」）"]
    for frag in s.must_keep:
        if frag not in text:
            out.append("缺少必须保留的片段：{}".format(frag))
    for frag, n in s.min_counts:
        got = text.count(frag)
        if got < n:
            out.append("「{}」至少要出现 {} 次（现在 {} 次）".format(frag, n, got))
    return out


# ---------------------------------------------------------------- 契约校验

def _extract_json(reply: str):
    """从回复里抠出 JSON（模型爱加 ```json 围栏和解释）。"""
    t = re.sub(r"```(?:json)?", "", reply or "").strip()
    for a, b in (("[", "]"), ("{", "}")):
        i, j = t.find(a), t.rfind(b)
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except Exception:                                  # noqa: BLE001
                continue
    return None


def check_contract(key: str, reply: str) -> list:
    """检查模型输出是否符合该提示词的契约（空 = 通过）。"""
    s = SPECS.get(key)
    if s is None or not s.contract:
        return []
    out = []
    txt = (reply or "").strip()
    if not txt:
        return ["模型没有返回任何内容"]
    if s.contract == "json_list":
        data = _extract_json(txt)
        if not isinstance(data, list):
            out.append("解析不出 JSON 数组（程序会当成空列表 ⇒ 功能静默失效）")
        else:
            if not all(isinstance(x, str) and x.strip() for x in data):
                out.append("数组里必须都是非空字符串")
            if s.expect_nonempty and not data:
                out.append("返回了空数组（这条样例本该有内容）")
    elif s.contract == "json_objects":
        data = _extract_json(txt)
        if not isinstance(data, list):
            out.append("解析不出 JSON 数组")
        else:
            for item in data:
                if not isinstance(item, dict) or "name" not in item or "delta" not in item:
                    # 注意 `{{` —— 这里的字面量花括号不能被 format 当字段名
                    out.append('元素必须是 {{"name":…,"delta":…}}（收到 {!r}）'.format(
                        item)[:120])
                    break
            if s.expect_nonempty and not data:
                out.append("返回了空数组（这条样例本该有人涨发情值）")
    elif s.contract == "three_options":
        miss = [x for x in ("【1】", "【2】", "【3】") if x not in txt]
        if miss:
            out.append("缺少选项标记：{}（剧情会卡死）".format("".join(miss)))
    elif s.contract == "lines":
        lines = [x.strip().lstrip("-·•* ").strip() for x in txt.splitlines()]
        lines = [x for x in lines if x]
        if not lines:
            out.append("一行都没有")
        elif s.expect_any_of and not any(
                any(e in x or x in e for x in lines) for e in s.expect_any_of):
            out.append("返回的行与样例里的记忆条目都对不上（精筛会退化成不过滤）")
    return out


def run_selftest(key: str, llm=None, text=None, user=None) -> dict:
    """用固定样例（或指定的输入）真调一次模型，检查契约与形状。

    返回 `{"ok": bool, "skipped": bool, "reply": str, "issues": [...], "shape": {...}}`

    - `llm(system, user, **kw) -> str`，默认用 `brains._call_llm`（可注入假实现做测试）。
    - `text` 给了就用**它**当提示词（页面上的"预览草稿"走这条），否则读当前生效的那份。
    - `user` 给了就用它当样例输入（页面上的"自己出一句"走这条）。
    """
    s = SPECS.get(key)
    if s is None:
        return {"ok": False, "skipped": False, "reply": "",
                "issues": ["未登记的提示词"], "shape": {}}
    if user is None:
        user = s.sample_user
    if not user:
        return {"ok": True, "skipped": True, "reply": "",
                "issues": [], "shape": {},
                "note": "这一条没有自测样例（只做静态校验）"}
    if llm is None:
        try:
            from brains import _call_llm as llm
        except Exception as e:                                  # noqa: BLE001
            return {"ok": False, "skipped": False, "reply": "",
                    "issues": ["调不了模型：{}".format(e)], "shape": {}}
    system = text
    if system is None:
        try:
            from runtime import prompts as RP
            system = RP.load(key, RP.default_of(key))
        except Exception:                                       # noqa: BLE001
            system = ""
    if s.sample_system is not None:
        try:
            system = system.format(limit=10)
        except Exception:                                       # noqa: BLE001
            pass
    issues = []
    try:
        # 400 → 800（P12-AM）：这条是"按提示词真调一次、看形状对不对"的体检。
        # recap 这类长输出的提示词，400 token 会在体检里被截断，然后误报"换行数不对"。
        reply = llm(system, user, max_tokens=800, timeout=45) or ""
    except Exception as e:                                      # noqa: BLE001
        return {"ok": False, "skipped": False, "reply": "",
                "issues": ["调用失败：{}".format(str(e)[:200])], "shape": {}}
    issues += check_contract(key, reply)
    shape = {"chars": len(reply), "newlines": reply.count("\n")}
    if s.max_newlines is not None and shape["newlines"] > s.max_newlines:
        issues.append("换行数 {} 超过这条的形状上限 {}（会被截断/撑坏卡片）".format(
            shape["newlines"], s.max_newlines))
    if s.max_chars is not None and shape["chars"] > s.max_chars:
        issues.append("长度 {} 字 超过上限 {} 字".format(shape["chars"], s.max_chars))
    return {"ok": not issues, "skipped": False, "reply": reply,
            "issues": issues, "shape": shape}
