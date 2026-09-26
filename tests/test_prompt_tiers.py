"""文案模板分档与校验（P12-G）。

P12-G 的难点不是"页面能改文案"，而是**改坏不可能保存成功**。所以测试分三层：

1. **分区是完整的**：40 个提示词（含第 40 个 `MEMORY_FILTER`）**一个不多一个不少**
   地落在 C1/C2/C3 里 —— 将来有人加一个 prompt 忘了分档，这里立刻红。
2. **静态校验挡住"把契约删了"**：C3 的 JSON 字段名、`【1】【2】【3】`、
   `{limit}` 占位符、`CMD_PROMPT` 的分段结构 —— 删掉就被拒。
3. **契约校验认得真输出的形状**：JSON 数组 / 带字段的对象 / 三选项 / 逐行原文，
   以及"根本没按格式来"的情况都要报出来。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains  # noqa: E402,F401  （导入即登记 39 个 prompt）
import runtime.memory  # noqa: E402,F401  （第 40 个）
from runtime import prompt_tiers as PT  # noqa: E402
from runtime import prompts as RP  # noqa: E402

C3 = ("HEAT_JUDGE_PROMPT", "VOTE_OPTIONS_PROMPT", "XP_PROMPT",
      "MEMORY_FILTER_PROMPT", "STORY_START_PROMPT", "STORY_STEP_PROMPT")


# ---------------------------------------------------------------- 1. 分区

def test_every_registered_prompt_has_a_tier():
    reg = set(RP.registry())
    specs = set(PT.SPECS)
    assert len(reg) == 40, "登记册里应当是 40 个（39 + MEMORY_FILTER）"
    assert reg - specs == set(), "这些 prompt 没有分档：{}".format(sorted(reg - specs))
    assert specs - reg == set(), "这些分档没有对应 prompt：{}".format(sorted(specs - reg))


def test_tier_counts_are_pinned():
    """数量钉死：加/减一个 prompt 必须同时改这里（提醒作者想清楚分档）。"""
    assert PT.tier_counts() == {"C1": 23, "C2": 11, "C3": 6}


def test_c3_are_exactly_the_machine_parsed_ones():
    """C3 = 输出被**程序解析**的那些（对着真实解析点数的，不是抄文档估价）。"""
    assert set(PT.by_tier()["C3"][i].key for i in range(len(PT.by_tier()["C3"]))) == set(C3)
    for key in C3:
        assert PT.spec(key).contract, key
    # 其余两档不该有 contract
    for tier in ("C1", "C2"):
        assert not [s.key for s in PT.by_tier()[tier] if s.contract]


def test_memory_filter_is_the_externalised_40th():
    assert "MEMORY_FILTER_PROMPT" in PT.SPECS
    spec = PT.spec("MEMORY_FILTER_PROMPT")
    assert spec.tier == "C3"
    assert "{limit}" in spec.must_keep
    assert RP.registry()["MEMORY_FILTER_PROMPT"]["module"] == "runtime.memory"


def test_每条的静态校验对当前文本都通过():
    """现网文本必须全部通过（否则第一次保存就卡住）。"""
    bad = {}
    for key in PT.SPECS:
        issues = PT.validate(key, RP.load(key, RP.default_of(key)))
        if issues:
            bad[key] = issues
    assert not bad, bad


def test_must_keep_fragments_really_exist_in_the_defaults():
    """必须保留的片段必须真的在代码默认值里 —— 写错了会让"永远存不进去"。"""
    bad = {}
    for key, spec in PT.SPECS.items():
        default = RP.default_of(key)
        for frag in spec.must_keep:
            if frag not in default:
                bad.setdefault(key, []).append(frag)
    assert not bad, "这些 must_keep 在代码默认值里找不到：{}".format(bad)


def test_defaults_are_never_empty():
    for key in PT.SPECS:
        assert RP.default_of(key).strip(), key


# ---------------------------------------------------------------- 2. 静态校验

def test_validate_rejects_empty_and_missing_fragments():
    assert PT.validate("STORY_STEP_PROMPT", "")
    bad = PT.validate("STORY_STEP_PROMPT", "请给三个选项")
    assert any("【1】" in x for x in bad)
    ok = PT.validate("STORY_STEP_PROMPT", "结尾：\n【1】a\n【2】b\n【3】c")
    assert ok == []


def test_validate_checks_min_counts_for_cmd_prompt():
    """主人格的分段结构（含 4 组 few-shot）不许删。"""
    spec = PT.spec("CMD_PROMPT")
    assert spec.min_counts == (("用户:", 4), ("你:", 4))
    default = RP.default_of("CMD_PROMPT")
    assert PT.validate("CMD_PROMPT", default) == []
    cut = default.split("示例：")[0]        # 把 few-shot 整段删掉
    assert PT.validate("CMD_PROMPT", cut), "删掉示例段必须被拒"


def test_validate_does_not_confuse_prompt_length_with_output_shape():
    """`max_chars` 是**输出**的限制，不能拿来量提示词本身。"""
    spec = PT.spec("FORTUNE_PROMPT")
    assert spec.max_chars == 200
    default = RP.default_of("FORTUNE_PROMPT")
    assert len(default) > 200, "前置条件：提示词本身比输出的字数上限长"
    assert PT.validate("FORTUNE_PROMPT", default) == []


# ---------------------------------------------------------------- 3. 契约

@pytest.mark.parametrize("reply,ok", [
    ('["小M","露出"]', True),
    ("```json\n[\"小M\"]\n```", True),
    ("小M、露出", False),
    ("[]", False),                     # expect_nonempty
])
def test_json_list_contract(reply, ok):
    got = PT.check_contract("XP_PROMPT", reply)
    assert (not got) is ok, got


@pytest.mark.parametrize("reply,ok", [
    ('[{"name":"小糯","delta":15}]', True),
    ('[{"n":1}]', False),
    ('{"name":"小糯"}', False),
    ("[]", False),                     # 这条样例本该有人涨
])
def test_json_objects_contract(reply, ok):
    got = PT.check_contract("HEAT_JUDGE_PROMPT", reply)
    assert (not got) is ok, got


@pytest.mark.parametrize("reply,ok", [
    ("【1】a\n【2】b\n【3】c", True),
    ("【1】a\n【2】b", False),
    ("abc", False),
])
def test_three_options_contract(reply, ok):
    got = PT.check_contract("STORY_STEP_PROMPT", reply)
    assert (not got) is ok, got


def test_lines_contract_matches_sample_memories():
    assert PT.check_contract("MEMORY_FILTER_PROMPT", "我喜欢吃芒果") == []
    assert PT.check_contract("MEMORY_FILTER_PROMPT", "无") == []
    assert PT.check_contract("MEMORY_FILTER_PROMPT", "芒果很好吃"), \
        "对不上原文的行要报出来（否则精筛等于没做）"


def test_free_text_prompts_have_no_contract():
    """C1 没有契约：不给它套"必须解析成 JSON"这类规则。

    注意 `check_contract` 对没有契约的 prompt 一律返回空 —— 连"空回复"也不报，
    因为那不是**契约**问题（C1 的空回复由自测里的形状检查/人眼负责）。
    """
    assert PT.spec("SUMMARY_PROMPT").contract == ""
    assert PT.check_contract("SUMMARY_PROMPT", "随便一段话") == []
    assert PT.check_contract("GREETING_PROMPT", "") == []
    # 有契约的才管空回复
    assert PT.check_contract("XP_PROMPT", "") == ["模型没有返回任何内容"]


# ---------------------------------------------------------------- 4. 自测

def test_selftest_skips_when_no_sample():
    r = PT.run_selftest("PERF_NOVEL_PROMPT")
    assert r["skipped"] is True and r["ok"] is True


def test_selftest_uses_the_given_draft_and_input():
    seen = {}

    def fake(system, user, **kw):
        seen["system"] = system
        seen["user"] = user
        return '["小M"]'

    r = PT.run_selftest("XP_PROMPT", llm=fake, text="【草稿提示词】", user="我的输入")
    assert r["ok"] is True, r
    assert seen["system"] == "【草稿提示词】"
    assert seen["user"] == "我的输入"


def test_selftest_reports_contract_failure_and_shape():
    def fake(system, user, **kw):
        return "我拒绝输出 JSON"

    r = PT.run_selftest("VOTE_OPTIONS_PROMPT", llm=fake)
    assert r["ok"] is False
    assert any("JSON" in x for x in r["issues"])
    assert r["shape"]["chars"] > 0


def test_selftest_checks_shape_limits():
    def fake(system, user, **kw):
        return "第一行\n第二行"          # FORTUNE 要求绝不换行

    r = PT.run_selftest("FORTUNE_PROMPT", llm=fake)
    assert r["ok"] is False
    assert any("换行" in x for x in r["issues"]), r["issues"]


def test_selftest_reports_llm_failure_instead_of_raising():
    def boom(*a, **kw):
        raise RuntimeError("no api key")

    r = PT.run_selftest("XP_PROMPT", llm=boom)
    assert r["ok"] is False and "no api key" in r["issues"][0]
