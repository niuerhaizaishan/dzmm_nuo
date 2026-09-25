"""打印解析函数的真实返回（repr），用于写特征化测试。

用 Python 输出而非 PowerShell 读源码，避免控制台代码页把中文读花。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402

CASES = [
    ("_strip_parenthesis", "糯糯～（裳姐姐的骚贱小母狗❤）"),
    ("_strip_parenthesis", "清歌"),
    ("_extract_remember", "记住 我喜欢芒果"),
    ("_extract_remember", "@小小糯 帮我记住 我不吃香菜"),
    ("_extract_remember", "记住"),
    ("_extract_remember", "记住 好"),
    ("_extract_remember", "今天天气不错"),
    ("_normalize_cn", "Ｎｕｏ　Ｎｕｏ"),
    ("_normalize_cn", ""),
    ("_simplify", "糯糯～后缀"),
    ("_extract_continue_custom", "/继续 被围观"),
    ("_extract_continue_custom", "/继续"),
    ("_extract_continue_custom", "/接下去 更露骨一点"),
    ("_normalize_choice", "A"),
    ("_normalize_choice", "b"),
    ("_normalize_choice", "3"),
    ("_normalize_choice", "X"),
    ("is_vote_choice_cmd", "/选 2"),
    ("is_vote_choice_cmd", "/B"),
    ("is_vote_choice_cmd", "/9"),
    ("is_vote_choice_cmd", "/选"),
    ("_extract_deadline_seconds", "投票 3分钟 今晚吃啥"),
    ("_extract_deadline_seconds", "投票 2小时 A|B"),
    ("_extract_deadline_seconds", "投票 30秒 A|B"),
    ("_extract_deadline_seconds", "投票 A|B"),
    ("_extract_novel_title", "# 暗巷审讯实录\n正文内容"),
    ("_extract_novel_title", "没有标题的正文"),
    ("_parse_poem_style", "古风"),
    ("_parse_poem_style", "七律"),
    ("_detect_perf_query", "今天公演怎么样"),
    ("_detect_perf_query", "随便说点什么"),
    ("is_recap_cmd", "/我不在的时候群里发生了什么"),
    ("is_recap_cmd", "/回顾"),
    ("is_trend_board_cmd", "/运势趋势"),
    ("is_ambiguous_probe", "x"),
]

print("=== 解析函数实测返回 ===")
for name, arg in CASES:
    fn = getattr(bot, name, None)
    if fn is None:
        print("  {:<24} ({!r}) -> 函数不存在".format(name, arg))
        continue
    try:
        r = fn(arg) if arg != "x" else None
    except Exception as e:
        r = "EXC {}: {}".format(type(e).__name__, str(e)[:60])
    if arg == "x":
        continue
    print("  {:<24} {!r:<34} -> {!r}".format(name, arg, r))
