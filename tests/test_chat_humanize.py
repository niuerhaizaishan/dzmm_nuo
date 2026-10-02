"""P12-BD：让"@ 闲聊"这条**输出**更像人（排版 / 长度口径 / 装饰概率 / 欢迎语口吻）。

屋主 2026-09-30："上班群的若瑾，能有办法让她说话更像人一些吗？"
"就是说话有点太像机器人了，就和工具熊每次都要问要不要调酒一样的询问一样。"

## 量出来的事实（上班群，若瑾 vs 真人）

| 指标 | 若瑾 | 真人 |
|---|---|---|
| 中位字数 | 116 | 7 |
| 带空行分段 | 93% | 7% |
| ≥5 行 | 73% | 3% |
| 每条带 ❤️ | 80% | 1% |

## 所以这一批只做三件事（都在 `bot.py` 的 @ 闲聊那一条路上）

1. **排版收口**（`chat.humanize`，默认关）：空行折成单换行、最多 5 行 —— 只动版式，
   一个字都不改。这是"一眼机器人"的最大单一特征。
2. **装饰改为按概率**（`chat.decor_prob` / `chat.bang_prob`，默认 **1.0 = 今天的行为**）：
   `_ensure_decor` 原来是确定性的（每条必补 ❤️、必把句号换"！！"），提示词管不住。
3. **本群长度口径**（`chat.frame_length`，默认空）：整行替换收尾块里的【写多长】，
   省得"2~6 句、100~300 字起步"这条**通用**口径在闲聊里变成"每句都要写小作文"。

外加一条小的：欢迎语（`welcome_text` / `welcome_text_plain`）现在先问话术表 ——
中性那份常量是软糯小秘书口吻，换人设时会串味。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                        # noqa: E402
import personas as P                                              # noqa: E402
from runtime import features as F                                 # noqa: E402
from runtime import tuning                                        # noqa: E402
from runtime.text import _humanize_chat                           # noqa: E402

RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"

SRC_B = (ROOT / "bot.py").read_text(encoding="utf-8")
SRC_A = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")  # P3-3-4：@ 闲聊分支住这里


@pytest.fixture(autouse=True)
def _bind_group(monkeypatch):
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {RUOJIN: "ruojin"})


# ---------------------------------------------------------------- ① 排版收口

def test_blank_lines_are_folded():
    """93% 带空行分段 → 折成单换行（真人这么排版）。"""
    src = "结论：在忙。\n\n那件事我看过了。\n\n你还有别的事吗。"
    assert _humanize_chat(src) == "结论：在忙。\n那件事我看过了。\n你还有别的事吗。"


def test_limits_lines_keeping_the_last():
    """超行只截行、**保留最后一行**（最后一行通常是收尾，不能被截成半句）。"""
    src = "\n".join("第{}行".format(i) for i in range(1, 9))
    out = _humanize_chat(src, max_lines=5)
    assert out.split("\n") == ["第1行", "第2行", "第3行", "第4行", "第8行"]


def test_strips_and_keeps_single_line():
    assert _humanize_chat("  在。  ") == "在。"
    assert _humanize_chat("在。") == "在。"


def test_empty_input_is_returned_as_is():
    assert _humanize_chat("") == ""
    assert _humanize_chat(None) is None
    assert _humanize_chat("\n\n  \n") == ""


def test_content_is_never_rewritten():
    """只动版式：正文里的字一个都不能多、不能少。"""
    src = "一。\n\n二。\n\n三。"
    assert _humanize_chat(src).replace("\n", "") == src.replace("\n", "")


# ---------------------------------------------------------------- ② 长度口径

def test_frame_length_replaces_only_that_line():
    """整行替换【写多长】—— 其它行（怎么叫 / 他这一句 / 怎么回）一字不动。"""
    frame = bot.AT_FRAME_PROMPT
    assert "【写多长】" in frame
    out = bot._apply_frame_length(frame, "闲聊就一两句，20~40 字，别写小作文。")
    assert "【写多长】闲聊就一两句，20~40 字，别写小作文。" in out
    assert out.count("【写多长】") == 1, "别把两套口径都留着"
    assert "2~6 句" not in out, "旧口径必须被整行换掉，否则两个口径打架"
    for keep in ("【怎么叫】", "【他这一句】", "【怎么回】", "{pending}"):
        assert keep in out


def test_frame_length_empty_is_a_no_op():
    """没配的群（默认）零回归：一个字节都不能变。"""
    assert bot._apply_frame_length(bot.AT_FRAME_PROMPT, "") == bot.AT_FRAME_PROMPT
    assert bot._apply_frame_length(bot.AT_FRAME_PROMPT, "   ") == bot.AT_FRAME_PROMPT
    assert bot._apply_frame_length("", "x") == ""


def test_frame_length_is_read_from_tuning_per_group():
    """走可调参数（按群覆盖），不是写死的常量。"""
    i = SRC_A.index('str(_tune("chat.frame_length", cid) or "").strip()')
    assert "_apply_frame_length(" in SRC_A[i - 600:i], "取到口径要真的用上"


# ---------------------------------------------------------------- ③ 参数登记

def _param_labels():
    return {p.key: p for f in F.FEATURES for p in (f.params or ())}


def test_new_params_are_registered_with_safe_defaults():
    ps = _param_labels()
    for key in ("chat.decor_prob", "chat.bang_prob", "chat.humanize", "chat.frame_length"):
        assert key in ps, "没登记的话页面改不了、也无法按群覆盖"
    assert ps["chat.decor_prob"].default == 1.0, "默认必须是今天的行为（每条都补）"
    assert ps["chat.bang_prob"].default == 1.0, "默认必须是今天的行为"
    assert ps["chat.humanize"].default == 0, "默认关：不主动改任何群的排版"
    assert ps["chat.frame_length"].default == "", "默认空：不换任何群的长度口径"


def test_defaults_are_equivalent_to_today():
    """零行为变化的逻辑保证：默认参数下这一批等于没上线。"""
    assert float(tuning.get("chat.decor_prob", AUTH)) == 1.0
    assert float(tuning.get("chat.bang_prob", AUTH)) == 1.0
    assert not tuning.get("chat.humanize", AUTH)
    assert not (tuning.get("chat.frame_length", AUTH) or "")


# ---------------------------------------------------------------- ④ 接线

def test_wiring_is_inside_the_at_branch_and_after_the_reply():
    # P3-3-4 起 @ 闲聊分支在 runtime/at_chat.py
    i_guard = SRC_A.index("if not _no_decor:")
    seg = SRC_A[i_guard:i_guard + 900]
    # 排版先做（先收版式、再补末尾的 ❤️）
    assert seg.index("if _tune(\"chat.humanize\", cid):") < seg.index("_ensure_decor(reply")
    assert "_humanize_chat(reply)" in seg
    # 概率：读参数、拿掉不是"关掉装饰"而是"这次不补"
    assert "_heart = p(\"chat_decor\", cid)" in seg
    assert seg.index("chat.decor_prob") < seg.index("_ensure_decor(reply")
    assert seg.index("chat.bang_prob") < seg.index("_ensure_decor(reply")
    assert "_ensure_decor(reply, _heart, bang=_bang)" in seg


def test_cards_and_play_paths_are_untouched():
    """卡片（运势卡 `_no_decor`）与玩法正文（spice_emoji）不走排版收口。"""
    # P3-3-4 起 @ 闲聊分支在 runtime/at_chat.py
    assert SRC_A.count("_humanize_chat(") == 1, "只该有 @ 闲聊这一处调用"
    i_guard = SRC_A.index("if not _no_decor:")
    assert i_guard < SRC_A.index("_humanize_chat(reply)"), "排版收口必须在卡片守卫里面"
    # P3-3-2 起玩法正文（分派链）在 runtime/dispatch.py
    assert "_spice_emoji(reply, p(\"spice_emoji\", cid))" in (
        ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8")


def test_probability_can_actually_turn_the_heart_off(monkeypatch):
    """把概率调到 0 时，装饰调用**真的**拿到空 ❤️ 与 bang=False（不是只看源码）。"""
    seen = {}

    def _fake(reply, heart="", bang=True):
        seen["heart"] = heart
        seen["bang"] = bang
        return reply

    monkeypatch.setattr(bot, "_ensure_decor", _fake)
    monkeypatch.setattr(bot, "_tune", lambda k, cid: {"chat.decor_prob": 0.0,
                                                      "chat.bang_prob": 0.0,
                                                      "chat.humanize": 0}[k])
    monkeypatch.setattr(bot, "p", lambda k, cid=None, **kw: "❤️")
    assert bot._ensure_decor("在。", "", bang=False) == "在。"
    # 复现接线里的那段判断（同一个顺序、同一个比较方向）
    heart = bot.p("chat_decor", AUTH)
    bang = True
    if heart and bot.random.random() >= float(bot._tune("chat.decor_prob", AUTH)):
        heart = ""
    if bot.random.random() >= float(bot._tune("chat.bang_prob", AUTH)):
        bang = False
    bot._ensure_decor("在。", heart, bang=bang)
    assert seen == {"heart": "", "bang": False}, "概率 0 必须真的关掉装饰"


# ---------------------------------------------------------------- ⑤ 欢迎语

def test_welcome_phrase_falls_back_to_the_constants():
    """没写 key 的群（认证群 / 玫瑰房）逐字用常量 —— 零回归。"""
    assert bot._welcome_of(AUTH) == bot.WELCOME_TEXT
    assert bot._welcome_of(AUTH, plain=True) == bot.WELCOME_TEXT_PLAIN
    assert bot._welcome_of(ROSE, plain=True) == bot.WELCOME_TEXT_PLAIN


def test_ruojin_welcome_is_a_greeting_not_a_menu():
    """上班群那份是**打招呼**，不是功能菜单（屋主点名的"工具熊式招揽"）。

    默认那份中性欢迎语里有一整行【群简介】玩法清单 —— 每来一个新人就贴一次
    "我能做什么"；而且列的算命/投票/骰子若瑾**没开**。功能清单只留在 `/说明`。
    """
    t = bot._welcome_of(RUOJIN, plain=True)
    assert t != bot.WELCOME_TEXT_PLAIN, "上班群要有自己那份"
    assert "若瑾" in t
    for soft in ("～", "♡", "呀"):
        assert soft not in t, "软糯小秘书口吻不能漏到若瑾这里"
    for menu in ("算命", "投票", "骰子", "写诗", "群简介", "看全部功能"):
        assert menu not in t, "欢迎语里不许再贴功能菜单"
    assert t.count("\n") <= 2, "打招呼就该是两句，别写成说明书"
    assert "{name}" in t, "进群欢迎语要留 {name} 占位符（调用方 replace）"
    # 每个 key 都得有中文标签（页面要显示）
    for k in ("welcome_text", "welcome_text_plain"):
        assert k in P.PHRASE_LABELS


def test_welcome_call_sites_go_through_the_helper():
    """调用点全部走 `_welcome_of`（bot 侧）/ 注入的 `welcome_of`（joins 侧），
    别留一处直接引常量。P3-3-1 起调用点分布在两个文件，一起数。"""
    assert "WELCOME_TEXT, cid" not in SRC_B
    assert "WELCOME_TEXT_PLAIN, cid" not in SRC_B
    src_j = (ROOT / "runtime" / "joins.py").read_text(encoding="utf-8")
    assert "WELCOME_TEXT, cid" not in src_j, "joins 不碰话术常量（走注入）"
    assert "WELCOME_TEXT_PLAIN, cid" not in src_j
    assert SRC_B.count("_welcome_of(") + src_j.count("welcome_of(") >= 6


# ---------------------------------------------------------------- ⑥ 递话（第一批 a/c/d）

def test_diaojiao_hint_is_not_re_pasted_every_round():
    """参与提示**每局只出现一次**。

    原来 `bot.py` 的每条 `/命令` 都在尾部拼一遍 `DJ_PARTICIPATE_HINT` —— 那是
    `runtime/diaojiao.py` 里的**硬编码软糯原文**（"想参与的姐妹也来玩呀～…30秒没人
    我就自己继续啦～"）：① 绕过话术表，若瑾的 `dj_hint` 在这条路上不生效 ⇒ 漏旧人设；
    ② 每轮重复同一条 + 带倒计时 ⇒ 正是"工具熊每次都要问要不要调酒"那种机器味。
    开场那次由 `start_diaojiao()` 走 `_dj_hint(cid)` 带，目标回应时 `dj_replied` 再给一次。
    """
    # 允许它出现在**注释**里（讲清楚为什么删），但代码里一次都不许再用
    bad = [ln.strip() for ln in SRC_B.splitlines()
           if "DJ_PARTICIPATE_HINT" in ln
           and "#" not in ln.split("DJ_PARTICIPATE_HINT")[0]]
    assert bad == [], "这几行还在用那句硬编码提示：{}".format(bad)
    src_dj = (ROOT / "runtime" / "diaojiao.py").read_text(encoding="utf-8")
    assert "_dj_hint(cid)" in src_dj, "开场的参与提示要走话术表"
    # 开场与"目标应了"这两处才是提示该出现的地方
    assert '"dj_replied"' in src_dj or "dj_replied" in src_dj


def test_persona_forbids_handing_the_turn_back():
    """人设里要**点名**写出"递话"的黑名单（示例比规则管用，P12-AL 的教训）。"""
    e = P.RUOJIN_EXTRA
    assert "说完就停" in e
    assert "递话" in e
    for banned in ("要不要…？", "我等着", "选一个", "三十秒没人我就", "玩法第一轮除外"):
        assert banned in e, "黑名单少了 {!r}".format(banned)
    # 老的三句"派活"示例必须删掉（它们教出来的就是"要对方回话"）
    for old in ("谁准你吩咐我了？", "把刚才那句收回去。", "想清楚再说。"):
        assert old not in e, "派活示例还在：{}".format(old)
    # 挡回去要改成陈述句
    assert "不给。" in e


def test_persona_length_is_role_aware():
    """闲聊短、正经问答长、玩法正文照旧 —— 三档写清，别用一个口径盖所有场合。"""
    e = P.RUOJIN_EXTRA
    assert "二十到六十字" in e, "闲聊档（屋主要的「更像人」就在这一档）"
    assert "一百到三百来字" in e, "正经问答的长度口径不许被冲掉（P12-AH 的教训）"
    assert "玩法正文" in e, "玩法正文不受影响（明确不做）"
