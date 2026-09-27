"""P12-AF：认证群小小糯的「上下文 / 长度 / 知识」拉到与上班群若瑾同一水平 —— 人设不动。

屋主 2026-09-27 原话：
    "妹子认证群里的小小糯的上下文、长度、知识等调整为临屿这样，但是人设以及
     其他不变。"（临屿 = 上班群里那个跟若瑾聊天的群友，uid a4897fb3，
     `tools/probe_nuo_vs_ruojin.py` 查过：是人，不是 bot，认证群里没有他。）

⚠️ 先查证再动手，结论是**数字上早就一样了**：

| 项 | 认证群/小小糯 | 上班群/若瑾 |
|---|---|---|
| `reply.max_tokens` | 800 | 800 |
| `reply.context_msgs` | 20 | 20 |
| `chat`（@ 问答） | 开 | 开 |
| 群级 tuning 覆盖 | 无 | 无 |
| 滚动上下文（P12-AD） | 有 | 有 |
| **人设 extra 字数** | **0** | **1536** |

差的只有最后一行：若瑾那 1536 字里**又强调了一遍长度/知识口径**（而且落在
prompt 最末尾，recency 生效），小小糯则是 0 字、只有那份 CMD_PROMPT ——
更要命的是里面的 **4 组 few-shot 全是 1-2 句的短回答**，模型照着示例学，
比照着规则学长得多（规则在中间、示例在最后）。

所以本期**只改 CMD_PROMPT.md**（小小糯那份主人格提示词），三处：
1. 长度那条点明"示例里的短句只是寒暄示范，不代表回答长度"；
2. 【规则】里加一条"聊天记录里标着『我:』的是你自己刚说过的话"（承接 P12-AD）；
3. 补一组**长回答示例**（约 200 字、有她自己的立场、仍是软糯口吻）。

刻意**不新增 persona extra**：`nuo` 的 extra 保持为空，"默认人设零开销原样返回"
这条既有不变量不动（`tests/test_persona_texts.py` / `test_persona_config.py` 钉着它）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402

AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"    # 认证妹子群（小小糯）
RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"  # 上班群（若瑾）
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"    # 玫瑰房（薇丝）

PROMPT_FILE = ROOT / "prompts" / "CMD_PROMPT.md"


def _text():
    return PROMPT_FILE.read_text(encoding="utf-8")


def _examples():
    """把 few-shot 里的 (用户, 你) 成对拆出来。"""
    pairs, cur = [], None
    for line in _text().split("示例：")[-1].strip().split("\n"):
        if line.startswith("用户:"):
            cur = line[len("用户:"):].strip()
        elif line.startswith("你:") and cur is not None:
            pairs.append((cur, line[len("你:"):].strip()))
            cur = None
    return pairs


# ---------------------------------------------------------------- 口径
def test_length_target_is_still_there_and_says_examples_are_not_the_target():
    t = _text()
    assert "2-6 句" in t and "100-300 字" in t, "日常口径还是它（屋主原话）"
    assert "不代表回答长度" in t, "要点明示例的短句只是寒暄示范，否则长度口径会被示例压过去"
    # 展开那一档要**给足**：屋主 2026-09-27 反馈"妹子群还是有点短"
    assert "三四百字很正常" in t and "写到五百字也不算长" in t
    # 反向钉住：不许再给它加上限（P12-AF 加过"别超过 300 字"，实测把 342 压到 252）
    assert "别超过 300 字" not in t, "上限会直接抵消『值得展开就展开写清楚』"


def test_no_paragraph_cap_at_all():
    """屋主 2026-09-27 定：段落限制**不需要** —— 既不要「不要分段」，也别卡段数。

    历程：原口径是"不要分段落" → P12-AC 放宽到"最多 2-3 段" → 本次（P12-AG）
    屋主看到为段数纠结（她写了 4 段）直接说"这个不需要"，于是**取消约束**：
    话多就自然分段，只保留"不要加引号包裹、不要写括号动作"。
    """
    t = _text()
    assert "分段随意" in t
    for bad in ("不要分段", "别分段", "不分段", "不要分段落", "最多分", "2-3 段"):
        assert bad not in t, "段落限制要彻底去掉：{}".format(bad)
    # 别的提示词里也不许留老口径
    for f in sorted((ROOT / "prompts").glob("*.md")):
        assert "不要分段" not in f.read_text(encoding="utf-8"), f.name
    # 该留的还留着
    assert "不要加引号包裹" in t and "不要写括号动作" in t


def test_knowledge_stance_is_unchanged_and_even_stronger():
    t = _text()
    assert "用你自己的知识、经验和判断回答" in t
    assert "别动不动拒答" in t and "别打太极" in t
    assert "只有**实时/精确数据**不许编" in t
    assert "别一味附和" in t, "知识口径再进一步：他说得不对可以软软地反驳"


def test_continuity_rule_matches_the_p12ad_transcript():
    """上下文长什么样，提示词得说清楚：标着『我:』的是她自己说过的话。"""
    t = _text()
    assert '标着"我:"的那几句是你自己刚说过的话' in t
    assert "别把同一句再答一遍" in t, "这正是 P12-AD 修的『说两遍』那条"
    assert "先看清他这句在接哪句" in t


# ---------------------------------------------------------------- 长度示范
def test_there_is_at_least_one_long_example_answer():
    pairs = _examples()
    assert len(pairs) >= 4, "few-shot 不许删（prompt_tiers 的 min_counts 钉着 ≥4）"
    lens = [len(a) for _q, a in pairs]
    assert max(lens) >= 250, \
        "长回答示例要够长（现在 340+ 字），否则模型照着示例学还是短：{}".format(lens)


def test_the_long_example_keeps_her_voice():
    """写长 ≠ 变成说教：长示例里也得有姐姐/～/软糯口气，人设没被动过。"""
    pairs = _examples()
    long_ones = [(q, a) for q, a in pairs if len(a) >= 120]
    assert long_ones, "先要有长示例"
    q, a = long_ones[-1]
    assert "姐姐" in a and "～" in a, "长示例也要是小小糯的口吻"
    assert "我" in a, "第一人称"


# ---------------------------------------------------------------- 人设没动
def test_the_persona_itself_is_untouched():
    t = _text()
    for frag in ("【外貌】", "【性格】", "【性癖/亲密观】", "【说话方式】",
                 "银白双马尾", "软糯", "姐姐", '自称"小小糯"', "百合"):
        assert frag in t, "人设段不该被动：{}".format(frag)


def test_nuo_still_has_no_extra_so_the_zero_cost_invariant_holds():
    """刻意不新增 extra：默认人设仍然零开销原样返回（老测试钉着这条）。"""
    assert P.PERSONAS["nuo"]["extra"] == ""
    base = "你是小小糯～。"
    assert P.apply_persona(base, AUTH) == base
    assert P.apply_persona(base) == base


# ---------------------------------------------------------------- 真正喂给模型的那份
def test_what_the_auth_group_actually_sends(monkeypatch):
    """行为级：认证群 @ 问答时，系统提示词里必须**真的**带着新口径与长示例。"""
    seen = {}
    real = brains._call_llm_raw

    def spy(system_prompt, user_content, *a, **kw):
        seen["sp"] = system_prompt
        seen["uc"] = user_content
        return "答", "stop"

    monkeypatch.setattr(brains, "_call_llm_raw", spy)
    P.set_cid(AUTH)
    try:
        brains.generate_reply([{"sender": "姐姐", "text": "你怎么看加班"}],
                              max_tokens=800, context_msgs=20)
    finally:
        P.set_cid("")

    sp = seen["sp"]
    assert "不代表回答长度" in sp
    assert '标着"我:"的那几句是你自己刚说过的话' in sp
    assert "小小糯，你说人为什么要上班啊" in sp
    assert "我是小小糯～" in sp, "还是小小糯本人"


def test_other_personas_keep_their_override_last(monkeypatch):
    """若瑾/薇丝读到的最后一段仍必须是她们的覆盖声明（recency 站在她们那边）。

    否则上面那份**小小糯口吻**的长示例会离模型更近，把她们的 S 属性/管家口吻带偏。
    """
    # 按群的人设映射平时由 bot 从 config 注入；测试里得自己摆上
    P.set_config_personas({RUOJIN: "ruojin", ROSE: "rose_butler"})
    try:
        for cid, key in ((RUOJIN, "ruojin"), (ROSE, "rose_butler")):
            assert P.persona_key_for(cid) == key
            extra = P.PERSONAS[key]["extra"]
            assert "覆盖声明" in extra
            got = P.apply_persona("你是小小糯～。", cid)
            assert got.rstrip().endswith(extra.rstrip()), \
                "{} 的覆盖声明必须落在最末尾".format(key)
            # 而且这句 1-2 句的软糯示例不该是模型最后读到的东西
            assert not got.rstrip().endswith("不代表回答长度")
    finally:
        P.set_config_personas({})


def test_file_and_code_default_stay_identical():
    assert _text() == brains.CMD_PROMPT, "一边改了另一边没改（线上用哪份取决于挂载）"
