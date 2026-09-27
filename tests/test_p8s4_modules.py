"""P8 第 4 步搬出来的模块：行为测试。

搬家的行为保持由 `tools/p8s4_audit.py`（逐字比对源码文本）保证，
所以这里**不是**在"用测试兜住搬家风险" —— 而是在补这次搬家顺手暴露出来的、
原本就没人测的纯函数。它们全是"错了会静默出错"的类型：

  - 昵称归一化错了 → 认错人（A 的档案按到 B 头上）
  - 单字模糊匹配没挡住 → 「裳」命中「裳姐姐」，指令打到别人身上
  - 诗句超过 9 行 → 加上标题正好 10 行，发送时被平台的 10 行上限砍掉最后一句
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import runtime.members as members  # noqa: E402
import runtime.text as textmod  # noqa: E402


# ---------------------------------------------------------------- 昵称归一化

def test_normalize_cn_folds_width_space_and_case():
    assert textmod._normalize_cn("ＡＢＣ") == textmod._normalize_cn("abc")
    assert textmod._normalize_cn("糯 糯") == textmod._normalize_cn("糯糯")
    assert textmod._normalize_cn("  ") == ""
    assert textmod._normalize_cn("") == ""
    assert textmod._normalize_cn(None) == ""


def test_normalize_cn_maps_cjk_variant_chars():
    """异体字必须归到一起，否则同一人两个名字。"""
    for variant, normal in (("羣", "群"), ("為", "为"), ("峯", "峰"), ("夠", "够")):
        assert textmod._normalize_cn(variant) == textmod._normalize_cn(normal), variant


def test_normalize_cn_traditional_to_simplified_if_opencc_available():
    """opencc 可用时必须繁简归一；不可用时跳过（不能因此报错）。"""
    cc = textmod._get_opencc()
    if cc is None:
        pytest.skip("opencc 不可用，本环境只验证不抛异常")
    assert textmod._normalize_cn("龍") == textmod._normalize_cn("龙")


def test_get_opencc_caches_and_never_raises():
    a = textmod._get_opencc()
    b = textmod._get_opencc()
    assert a is b or (a is None and b is None)


# ---------------------------------------------------------------- 昵称截断

def test_simplify_cuts_at_tilde_or_bracket():
    assert textmod._simplify("糯糯～（裳姐姐的小母狗）") == "糯糯"
    assert textmod._simplify("糯糯(abc)") == "糯糯"
    assert textmod._simplify("糯糯") == "糯糯"
    # 分隔符在开头时不能把名字截成空
    assert textmod._simplify("～糯糯") == "～糯糯"


def test_strip_parenthesis_keeps_tilde_prefix():
    """保留波浪号前缀、只去括号内容（发骚 prompt 靠它避免串角色）。"""
    assert textmod._strip_parenthesis("糯糯～（裳姐姐的骚贱小母狗❤）") == "糯糯～"
    assert textmod._strip_parenthesis("糯糯～") == "糯糯～"


def test_clean_name_keeps_only_chinese():
    assert textmod._clean_name("糯糯～（小母狗）") == "糯糯"
    assert textmod._clean_name("依依❤") == "依依"
    # 只要有中文就只留中文（"Vv姐姐" → "姐姐"）；一个中文都没有才退回原名
    assert textmod._clean_name("Vv姐姐") == "姐姐"
    assert textmod._clean_name("Vv") == "Vv"


# ---------------------------------------------------------------- 命令词工具

def test_is_stop_word_covers_all_end_words():
    for w in ("停止", "停下", "结束", "终止"):
        assert textmod._is_stop_word(w) is True
        assert textmod._is_stop_word("/" + w) is True
        assert textmod._is_stop_word("  /" + w + "  ") is True


def test_is_stop_word_rejects_others():
    assert textmod._is_stop_word("继续") is False
    assert textmod._is_stop_word("") is False
    assert textmod._is_stop_word("/") is False


def test_normalize_choice_accepts_letters_and_digits():
    assert textmod._normalize_choice("a") == "1"
    assert textmod._normalize_choice(" B ") == "2"
    assert textmod._normalize_choice("3") == "3"
    assert textmod._normalize_choice("Z") is None
    assert textmod._normalize_choice("") is None


def test_dedupe_options_keeps_order_and_drops_blank():
    assert textmod._dedupe_options(["a", " b ", "a", "", "c"]) == ["a", "b", "c"]


def test_squash_flattens_and_marks_truncation():
    assert textmod._squash("a\n\nb   c", 10) == "a b c"
    assert textmod._squash("abcdef", 3) == "abc…"
    assert textmod._squash("abc", 3) == "abc"
    assert textmod._squash(None, 3) == ""


def test_norm_poem_caps_at_twenty_lines():
    """P12-W：上限 9 → 20。

    原来的 9 行是"标题占 1 行正好 10 行，别撞普通账号的 10 换行上限"；
    现在超限消息会自动改走官方 Bot API（实测 66 行照发），所以放宽，
    免得长调宋词/律诗被**悄悄砍掉尾巴**。
    """
    long_poem = "\n".join("第{}句".format(i) for i in range(30))
    out = textmod._norm_poem(long_poem)
    assert len(out.split("\n")) == 20
    assert textmod._norm_poem("").split("\n") == [""]


def test_norm_poem_drops_blank_lines():
    assert textmod._norm_poem("甲\n\n乙\n   \n丙") == "甲\n乙\n丙"


# ---------------------------------------------------------------- 消息抽取

def test_extract_msgs_filters_by_sender_and_drops_join_notice():
    """「加入了群聊」不是发言 —— 混进去会让 AI 以为 TA 说过话。"""
    msgs = [
        {"sent_by": "u1", "content": {"text": "你好"}},
        {"sent_by": "u1", "content": {"text": "小糯 加入了群聊"}},
        {"sent_by": "u2", "content": {"text": "别人说的"}},
        {"sent_by": "u1", "content": {"text": "   "}},
        {"sent_by": "u1", "content": {}},
    ]
    assert members._extract_msgs("u1", msgs) == ["你好"]


def test_extract_msgs_handles_empty():
    assert members._extract_msgs("u1", []) == []
    assert members._extract_msgs("u1", None or []) == []


# ---------------------------------------------------------------- 找人（子串陷阱）

def test_find_uid_by_name_single_char_skips_substring_match(monkeypatch):
    """单字不参与**子串**匹配（否则「裳」会在「裳姐姐」里命中）。

    ⚠️ 实测发现：单字**仍会被前缀匹配命中**（第 3 步没有长度判断）——
    所以 `_find_uid_by_name("裳")` 现在返回「裳姐姐」。
    这**可能是刻意的**（群里「裳」就是裳姐姐的常用简称），也可能只是漏了判断。
    P8 第 4 步是纯结构重构，**不在这里改行为**：这条测试钉住现状，
    真要改的话是一次显式的、看得见的行为变更。
    """
    monkeypatch.setattr(members, "_name_cache",
                        {"u1": "裳姐姐", "u2": "依依"}, raising=False)
    monkeypatch.setattr(members, "load_profiles", lambda: {})
    # 现状：前缀命中
    assert members._find_uid_by_name("裳") == "u1"
    # 子串命中（单字）不走：把「裳」放在中间，任何 uid 都不该被选中
    assert members._find_uid_by_name("姐") == ""
    assert members._find_uid_by_name("裳姐姐") == "u1"


def test_find_uid_by_name_exact_beats_prefix(monkeypatch):
    monkeypatch.setattr(members, "_name_cache",
                        {"u1": "糯糯～（小母狗）", "u2": "糯糯"}, raising=False)
    monkeypatch.setattr(members, "load_profiles", lambda: {})
    # 精确（去括号后）匹配必须赢过前缀匹配
    assert members._find_uid_by_name("糯糯") == "u2"


def test_find_uid_by_name_falls_back_to_profiles(monkeypatch):
    monkeypatch.setattr(members, "_name_cache", {}, raising=False)
    monkeypatch.setattr(members, "load_profiles",
                        lambda: {"u9": {"name": "玫瑰"}})
    assert members._find_uid_by_name("玫瑰") == "u9"
    assert members._find_uid_by_name("") == ""


# ---------------------------------------------------------------- 变化轨迹

def test_profile_history_migrates_old_name_history():
    """老的 name_history 必须自动并进 history，不能丢（丢了查人就看不到以前的样子）。"""
    p = {"name_history": [{"at": "01-01", "from": "旧名"}]}
    hist = members._profile_history(p)
    assert hist == [{"at": "01-01", "name": "旧名", "sig": "", "bio": ""}]
    assert p["history"] is hist          # 就地写回，下次不再迁移


def test_history_lines_renders_changes():
    p = {"history": [{"at": "01-01", "name": "旧名", "sig": "签名", "bio": "简介"},
                     {"at": "02-02", "name": "新名", "sig": "", "bio": ""}]}
    lines = members._history_lines(p, n=2)
    assert len(lines) == 2
    assert "旧名" in lines[0] and "签名" in lines[0]
    assert "新名" in lines[1]


def test_archive_text_from_profile_includes_history():
    p = {"name": "依依", "gender": "female", "history":
         [{"at": "01-01", "name": "旧依依", "sig": "", "bio": ""}]}
    out = members._archive_text_from_profile(p)
    assert "依依" in out
    assert "旧依依" in out
    assert "变化轨迹" in out
