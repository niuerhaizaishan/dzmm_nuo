"""解析函数特征化测试。

期望值全部来自 `tools/parse_probe.py` 的实测 `repr()` 输出，
**不是从源码猜的**——PowerShell 读中文源码会乱码，猜必错。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402


# ---------------------------------------------------------------- 昵称处理

def test_strip_parenthesis_removes_parenthetical():
    assert bot._strip_parenthesis("糯糯～（裳姐姐的骚贱小母狗❤）") == "糯糯～"


def test_strip_parenthesis_keeps_plain_name():
    assert bot._strip_parenthesis("清歌") == "清歌"


def test_simplify_cuts_at_tilde():
    assert bot._simplify("糯糯～后缀") == "糯糯"


def test_normalize_cn_fullwidth_and_case():
    assert bot._normalize_cn("Ｎｕｏ\u3000Ｎｕｏ") == "nuonuo"


def test_normalize_cn_empty():
    assert bot._normalize_cn("") == ""


# ---------------------------------------------------------------- 记忆提取

def test_extract_remember_strips_leading_pronoun():
    """注意：前缀清理会吃掉开头的"我"，所以返回 '喜欢芒果' 而非 '我喜欢芒果'。"""
    assert bot._extract_remember("记住 我喜欢芒果") == "喜欢芒果"


def test_extract_remember_from_at_message():
    assert bot._extract_remember("@小小糯 帮我记住 我不吃香菜") == "不吃香菜"


def test_extract_remember_rejects_too_short_or_unrelated():
    assert bot._extract_remember("记住") is None
    assert bot._extract_remember("记住 好") is None          # 长度 < 2
    assert bot._extract_remember("今天天气不错") is None      # 无触发词


# ---------------------------------------------------------------- 续写方向

def test_extract_continue_custom_with_direction():
    assert bot._extract_continue_custom("/继续 被围观") == "被围观"


def test_extract_continue_custom_without_direction():
    assert bot._extract_continue_custom("/继续") == ""


def test_continue_keyword_missing_is_not_recognized():
    """实测：'接下去' **不在**关键词表里（表里是 接着写/往下写/继续/续写/接着/再来/再写）。"""
    assert bot._extract_continue_custom("/接下去 更露骨一点") == ""


# ---------------------------------------------------------------- 投票选项

def test_normalize_choice_letters_and_digits():
    assert bot._normalize_choice("A") == "1"
    assert bot._normalize_choice("b") == "2"        # 大小写不敏感
    assert bot._normalize_choice("3") == "3"
    assert bot._normalize_choice("X") is None


def test_vote_choice_cmd_forms():
    assert bot.is_vote_choice_cmd("/选 2") == "2"
    assert bot.is_vote_choice_cmd("/B") == "2"
    assert bot.is_vote_choice_cmd("/9") == "9"
    assert bot.is_vote_choice_cmd("/选") is None


# ---------------------------------------------------------------- 截止时长

def test_extract_deadline_minutes():
    rest, secs = bot._extract_deadline_seconds("投票 3分钟 今晚吃啥")
    assert secs == 180
    assert rest == "投票  今晚吃啥"      # 注意：留下双空格（未做空白折叠）


def test_extract_deadline_hours_and_seconds():
    assert bot._extract_deadline_seconds("投票 2小时 A|B")[1] == 7200
    assert bot._extract_deadline_seconds("投票 30秒 A|B")[1] == 30


def test_extract_deadline_absent():
    rest, secs = bot._extract_deadline_seconds("投票 A|B")
    assert secs is None
    assert rest == "投票 A|B"


# ---------------------------------------------------------------- 小说标题

def test_extract_novel_title_from_heading():
    assert bot._extract_novel_title("# 暗巷审讯实录\n正文内容") == "暗巷审讯实录"


def test_extract_novel_title_fallback():
    assert bot._extract_novel_title("没有标题的正文") == "公演小说"


# ---------------------------------------------------------------- 诗风格

def test_parse_poem_style_known():
    style, rest = bot._parse_poem_style("七律")
    assert style is not None and "七言" in style
    assert rest == ""


def test_parse_poem_style_unknown_becomes_topic():
    """'古风' 不是风格关键词 → 被当成题目返回。"""
    style, rest = bot._parse_poem_style("古风")
    assert style is None
    assert rest == "古风"


# ---------------------------------------------------------------- 回顾触发

def test_recap_trigger_prefix():
    assert bot.is_recap_cmd("/我不在的时候群里发生了什么") is not None


def test_recap_not_triggered_by_unrelated():
    assert bot.is_recap_cmd("/回顾") is None
