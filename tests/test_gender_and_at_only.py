"""P12-AL：称呼按性别 + 只有 @ 才回复。

屋主 2026-09-29：
  1. "不要叫谁都叫姐姐，根据性别叫。只不过**对女生温柔一点**。"
  2. "最后，**只有 @ 小小糯的时候才触发回复**。"

## 为什么"按性别叫"要单独改

基础提示词里"姐姐"出现 **13 次**、4 个示例**全是**"姐姐" —— 旧规则虽然写了
"女生叫姐姐、男生叫哥哥"，但示例与人格把模型训练成了一律叫姐姐（实测：对男生也叫姐姐）。
所以这次是**改示例 + 写死规则**，不是改一句称呼说明。
（性别从 `runtime.members._gender_label()` 来：本地档案直接读，查不到算"未知"，
@ 问答那条路会把它拼进对话：`名字（男生）`。）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402
import pytest                                                  # noqa: E402
from runtime import features as F                              # noqa: E402

PROMPT = ROOT / "prompts" / "CMD_PROMPT.md"


def _t():
    return PROMPT.read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 按性别称呼

def test_prompt_teaches_gender_first_addressing():
    t = _t()
    assert "先看性别再决定怎么叫" in t
    assert "（女生）/（男生）/（未知）" in t
    assert '女生** → 叫"姐姐"' in t or '叫"姐姐"' in t
    assert '**男生** → 叫"哥哥"' in t
    assert "未知" in t and "别硬叫" in t
    # 明确点出最常犯的错
    assert "见谁都叫" in t and "姐姐" in t


def test_gentleness_is_reserved_for_girls():
    t = _t()
    assert "温柔是给女生的" in t
    # 对男生：可爱但不腻，且点名不许做的事
    assert "可爱但不腻" in t
    for bad in ("不撒娇", "不黏人"):
        assert bad in t


def test_few_shot_has_both_genders():
    """光靠规则不管用 —— 示例里必须有男生，否则模型照旧一律"姐姐"。"""
    t = _t()
    ex = t.split("示例：")[-1]
    assert "（男生）" in ex, "示例里要有男生的问答"
    assert "（女生）" in ex, "也要有女生的（做对照）"
    # 男生那条回答里不许叫姐姐
    male_line = [ln for ln in ex.split("\n") if ln.startswith("你:") and "哥哥" in ln]
    assert male_line, "男生示例的回答要用「哥哥」"
    assert "姐姐" not in male_line[0], "男生示例里不能出现「姐姐」"


def test_prompt_file_matches_code_default():
    assert _t() == brains.CMD_PROMPT, "文件与代码默认值必须逐字节一致"


# ---------------------------------------------------------------- ② 只 @ 才回

def test_at_only_registered_and_off_by_default():
    f = F.get("at_only")
    assert f.default is False, "默认关＝今天的行为（认证群靠非 @ 消息跑骰子/发骚/排行）"
    assert f.stage == "wired"
    assert "只 @" in f.label


def test_scan_loop_gates_everything_on_at_only():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    i = src.index("P12-AL：`at_only` 的群里")
    seg = src[i:i + 700]
    assert '_feat_on(cid, "at_only")' in seg
    assert "is_at_me(t, cid)" in seg
    assert "continue" in seg
    # 引用回复那条路（quote_reply）在 at_only 群里仍要能被放行（默认它是关的）
    assert '_quotes_self(m)' in seg


@pytest.mark.parametrize("cid,expect", [
    ("bd7fe6a4-0065-4551-ad3d-94116dba8f9c", False),   # 认证群：必须保持"能非 @ 触发"
    ("f4ec4c21-fb48-4be2-9972-e4128f65923b", False),   # 上班群同理
])
def test_at_only_is_not_on_for_the_busy_groups(cid, expect):
    assert F.on(cid, "at_only") is expect
