"""P12-AP：@ 回复**只针对当前这一句**，说话人who以标签为准。

屋主 2026-09-29 现场（顶层茶水间）：

    11:49:40 👋 @消息: @小小糯～ 嗯嗯，小小糯真乖！记得提醒糯糯，最爱最爱她的蝌蚪哥哥等她拍婚纱照。
    11:49:41 💬 🤖 哥哥～好，我记牢了：…等糯糯姐一冒头，我第一句就把这个转给她…
    11:51:22 👋 @消息: @小小糯 摸摸头～公主抱抱你          ← 说话人是 **柳无央**
    11:51:23 💬 🤖 糯糯姐！你终于冒头了～…囡囡也在问接模型的事，紫苑那边分工我还没回…

一句话的寒暄被读成了"糯糯姐回来了"，然后对着不相干的人把待办念了一遍。

## 为什么不是"改改提示词"就完事

`CMD_PROMPT` 里**本来就有**"聚焦用户当前问的问题回答，不要被聊天记录里无关的话题带偏"
（这条从 P12-AC 就在）。实测它挡不住 —— 因为把当前这句**混在历史里**（`说话人: 内容`）时，
模型看到的历史里全是"等糯糯姐冒头我就转告"和记忆里给糯糯姐留的待办，
一句亲昵的话就被解释成"糯糯来了"。

`tools/probe_at_focus.py` 的 A/B（真实历史，各 3 次采样）：

    before（只混在历史里）：A 场景 3/3 认亲 + 念待办
    after （独立收尾块）  ：A 场景 3/3 干净；B（糯糯本人问待办）照样汇报；C 正常答

所以修法是**结构**：当前这句不再当普通历史行，而是拼成一块独立的
`AT_FRAME_PROMPT`（谁在说话 / 他这一句 / 只回这一句），放在**最后**（模型对末尾最敏感）。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402

BOT_PY = ROOT / "bot.py"
AT_CHAT_PY = ROOT / "runtime" / "at_chat.py"          # P3-3-4：@ 闲聊分支住这里
FRAME_MD = ROOT / "prompts" / "AT_FRAME_PROMPT.md"
CMD_MD = ROOT / "prompts" / "CMD_PROMPT.md"


def _frame():
    return FRAME_MD.read_text(encoding="utf-8")


def _bot_src():
    """P3-3-4 起 @ 分支（含收尾块接线）在 runtime/at_chat.py。"""
    return AT_CHAT_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 收尾块本身

def test_frame_has_the_three_markers_and_placeholders():
    t = _frame()
    for frag in ("【现在跟你说话的人】", "【他这一句】", "【怎么回】"):
        assert frag in t, frag
    assert "{who}" in t and "{q}" in t, "少了占位符就没法填说话人和原话"
    assert t.count("{who}") >= 2, "『他就是{who}』这半句必须把说话人写死"
    # P12-AQ：手上真记着的事（治"空手编待办"）
    assert "【你手上真记着的事】" in t and "{pending}" in t


def test_frame_forbids_the_two_observed_mistakes():
    """这次的两个毛病必须在收尾块里点名：认错人 + 念待办。"""
    t = _frame()
    assert "不是聊天记录里" in t, "要写明『他就是标签上那个人，不是历史里的别人』"
    assert "亲昵" in t, "要点名『别因为亲昵就认定对方是历史里那个人』"
    assert "别接着上一轮" in t and "待办" in t, "要禁止把上一轮/待办接着讲"


def test_frame_forbids_fabricating_a_report():
    """P12-AQ：空手被问"有什么事要跟我说吗"时编待办 —— 收尾块要钉死这条。"""
    t = _frame()
    assert "只能讲上一行" in t and "真出现过" in t, "汇报的来源必须限定"
    assert "（空）" in t and "没什么要跟你说的" in t, "空手时要给她一句能说的话"
    assert "不许编" in t
    # 现场编出来的那几种空头话/细节，要点名禁掉
    for bad in ("你上次让我记着的那件事", "待办都整理好了", "桌上", "配方"):
        assert bad in t, bad


def test_frame_is_short_enough_to_not_eat_the_budget():
    """收尾块是**每一条 @ 消息**都要带的固定开销，不能养肥。

    P12-AP 352 → P12-AQ 352 → **P12-AU 436**：加的那几句是"该写多长"，
    它是这次"闲聊变短"事故的修复本体，属于必须留的（实测：收尾块把均值
   从 89 字压到 55 字、15/15 条短于 100 字）。再往上加之前请先想清楚。
    """
    # P12-BE 再加一行"只跟他一个人说"（屋主："我回复她，她会一一批判其他人"）= 506 字，
    # 所以上限放到 **560**；再往上加之前请先想清楚。
    assert len(_frame()) <= 560, len(_frame())


def test_frame_tells_her_how_long_to_write():
    """P12-AU：收尾块必须把"话题"和"篇幅"分开写。

    屋主 2026-09-29 晚："茶水间@小小糯 闲聊回复变短了！"
    根因：原来那句"**只回他这一句**…没实质内容就只接住这一句"本意是话题锚点，
    被她读成了"少说话"（实测均值 122 → 55 字）。
    """
    t = _frame()
    assert "【写多长】" in t, "篇幅要单独成条，别混在『怎么回』里"
    assert "别用一两个短句把人打发" in t
    assert "100~300 字起步" in t
    # 哪些算"有内容"要点名，否则她会把寒暄归到"没内容"那一类
    for ex in ("今天怎么样", "在干嘛", "吃了没"):
        assert ex in t, ex
    # 只有纯贴贴/纯招呼才允许短回
    assert "只有**纯贴贴或纯招呼**" in t or "只有" in t and "摸摸头" in t


def test_cmd_prompt_separates_greeting_from_no_content():
    """P12-AU：主提示词也要写清"正常寒暄不算没内容"。"""
    t = CMD_MD.read_text(encoding="utf-8")
    assert "正常寒暄不算这一类" in t
    assert "只有" in t and "才可以短回" in t


def test_pending_line_is_empty_when_she_has_nothing():
    """空手必须是明确的「（空）」—— 含糊的空白正是她开始编的地方。"""
    import bot
    assert bot._pending_line([]) == "（空）"
    assert bot._pending_line(None) == "（空）"
    assert bot._pending_line([{"text": "  "}]) == "（空）"


def test_pending_line_lists_real_items_with_caps():
    """有东西就逐条列出来，并且**有上限**（这一行每条 @ 都要带）。"""
    import bot
    mem = [{"text": "蝌蚪哥哥让你提醒糯糯拍婚纱照"},
           {"text": "囡囡在催那个功能"}]
    got = bot._pending_line(mem)
    assert got == "蝌蚪哥哥让你提醒糯糯拍婚纱照；囡囡在催那个功能"
    # 单条截断
    long_one = bot._pending_line([{"text": "字" * 200}])
    assert len(long_one) == 40
    # 条数上限
    many = bot._pending_line([{"text": "事{}".format(i)} for i in range(20)])
    assert many.count("；") == 4, "最多 5 条"


def test_frame_file_matches_code_default():
    """外置契约：文件与 `brains` 里的默认值逐字节一致（挂了就是线上用哪份看运气）。"""
    assert brains.AT_FRAME_PROMPT == _frame()


# ---------------------------------------------------------------- ② 接线

def test_at_branch_appends_the_frame_last():
    """@ 分支必须把**收尾块**（而不是裸的当前这句）放在 ctx 最后。"""
    src = _bot_src()
    assert "AT_FRAME_PROMPT.replace(" in src, "没接上收尾块"
    # 占位符用 replace 填（正文里有花括号，format 会炸）。
    # 断言按片段来：源码里这个调用是跨行的，整串匹配不上。
    assert '"{who}", _who' in src
    assert '"{q}", q' in src
    assert '"{pending}", _pending_line(mem)' in src, "没把『手上真记着的事』填进去"
    assert "AT_FRAME_PROMPT.format(" not in src, "format 遇到正文里的花括号会抛异常"
    # 当前这句绝不能再以普通历史行的身份混进去（那正是修之前的写法）
    assert 'ctx.append({"sender": get_name(at_sender)' not in src, \
        "又退回『当前这句混在历史里』的老写法了"


def test_frame_carries_speaker_label_including_gender():
    """说话人标签要连性别一起带上 —— 而且性别必须**查得到**（P12-BN A1）。

    原来用只看本地档案的 `_gender_label`：档案里没性别（茶水间大部分人都没档案）
    ⇒ 标签被**整个省掉** ⇒ 模型只看到"顾影"两个字、零性别信息 ⇒ 叫了"姐姐"
    （2026-09-30 08:52 现场）。现在与运势卡（P12-BA）同源：`_gender_of` = 档案优先 +
    实时查平台，且**必须在线程里**（它会发平台请求）。
    """
    src = _bot_src()
    i_frame = src.index("AT_FRAME_PROMPT.replace(")
    seg = src[max(0, i_frame - 2500):i_frame]
    assert "asyncio.to_thread(_gender_of, at_sender, cid)" in seg, \
        "性别要和运势卡同源，且在线程里查"
    assert "_gender_label(at_sender)" not in src, "别再用只看档案的那个"
    assert "_who = get_name(at_sender)" in seg
    assert 'if glabel != "未知" else ""' in seg, "未知时仍然不写标签"


def test_memory_is_retrieved_before_the_frame():
    """P12-AQ 的顺序要求：记忆检索必须在收尾块**之前** ——
    否则 `{pending}` 填不到"手上真记着的事"，那一行就成了一句空话。"""
    src = _bot_src()
    i_mem = src.index("mem = await asyncio.to_thread(")
    i_frame = src.index("AT_FRAME_PROMPT.replace(")
    assert i_mem < i_frame, "记忆检索跑到收尾块后面去了，{pending} 会是空的"


def test_pending_helper_is_task_scoped_not_a_module_constant():
    """`_pending_line` 要能被测试直接调（治"编待办"的那条事实依据）。"""
    src = _bot_src()
    assert "def _pending_line(mem" in src


def test_frame_is_after_history_in_source_order():
    """源码顺序：先取历史、再追加收尾块（顺序反了收尾块就不是最后一块）。"""
    src = _bot_src()
    i_hist = src.index("ctx = _recent_turns(cid, _ctxn, exclude_mid=_ask_mid,")
    i_frame = src.index("AT_FRAME_PROMPT.replace(")
    assert i_hist < i_frame


def test_no_duplicate_current_message():
    """当前这句只许出现一次：收尾块替代了原来那行，别两处都塞。

    （`ctx.append(` 在 @ 分支里还有一次是 P12-AE 的"被引用原文"，那是**引用**，
    不是当前这句，所以这里按收尾块数，不按 append 数。）
    """
    src = _bot_src()
    seg = src[src.index("ctx = _recent_turns("):src.index("generate_reply, ctx,")]
    assert seg.count("AT_FRAME_PROMPT.replace(") == 1, "收尾块塞了不止一次"
    assert seg.count('"text": q') == 0, "当前这句又被当成普通历史行喂了一遍"


# ---------------------------------------------------------------- ③ 提示词规则

def test_cmd_prompt_keeps_the_topic_anchor_rules():
    t = CMD_MD.read_text(encoding="utf-8")
    assert "只回答最后一行那句话" in t
    assert "不许猜" in t and "名字标签" in t
    assert "记忆" in t and "没人问就别主动汇报" in t
    assert "只有糯糯姐问的时候才能讲" in t
    # 老规则不该被删掉（它是 @ 回复的基本要求）
    assert "认真思考用户的问题" in t


def test_old_vague_rule_is_gone():
    """旧那句"先看清他这句在接哪句"太含糊，已被拆成可执行的四条。"""
    t = CMD_MD.read_text(encoding="utf-8")
    assert "先看清他这句在接哪句" not in t


def test_bot_source_has_no_prompt_text_duplication():
    """收尾块的正文只在 `prompts/AT_FRAME_PROMPT.md`（和 brains 默认值）里，别在 bot.py / at_chat.py 抄一份。"""
    for path in (BOT_PY, AT_CHAT_PY):
        src = path.read_text(encoding="utf-8")
        assert "现在跟你说话的人】" not in src, str(path)
        assert re.search(r'AT_FRAME_PROMPT\s*=\s*"', src) is None, str(path)
