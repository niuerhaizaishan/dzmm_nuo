"""P12-W：长文生成的**截断自动续写** + 发骚正文长度上限 + 排行只列前 10。

起因（屋主 2026-09-27）：
  1. "看下妹子群，昨天发骚生成的内容有截断" —— 认证群一段正文停在
     「屁股却不受控制地往裳手心里」（700 token 额度用完，`finish_reason=length`）；
  2. "上班群，发骚的内容太少了吧" —— 同一处上限 + 提示词里"250-350字/10 行以内"；
  3. "妹子群，发骚排行只展示前 10 名就可以" —— 65 人全列 = 2074 字 / 66 行刷屏；
  4. "现在是不是没有 10 行的限制了" —— 平台实测收 66 行没问题，提示词那条该撤。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402
import bot                                                     # noqa: E402
import state_store                                             # noqa: E402


# ---------------------------------------------------------------- 1. 续写

def test_join_continuation_strips_the_repeated_tail():
    """模型常把上一段的尾巴重抄一遍再往下写 —— 接的时候要去重。"""
    head = "她把脸埋进枕头里，肩膀抖得停不下来"
    more = "肩膀抖得停不下来，嘴里却还在小声认错。"
    out = brains._join_continuation(head, more)
    assert out == "她把脸埋进枕头里，肩膀抖得停不下来，嘴里却还在小声认错。"


def test_join_continuation_keeps_content_when_nothing_repeats():
    assert brains._join_continuation("前半段。", "后半段。") == "前半段。后半段。"


def test_call_llm_extends_only_when_truncated(monkeypatch):
    """`extend=True`：被掐断就再要一段接上；正常收尾就**只调一次**。"""
    calls = []
    seq = [("第一段写到一半", "length"), ("，接着写完了。", "stop")]

    def fake_raw(sp, uc, api_key=None, max_tokens=0, timeout=0, model=None):
        calls.append(uc)
        return seq[min(len(calls) - 1, len(seq) - 1)]

    monkeypatch.setattr(brains, "_call_llm_raw", fake_raw)
    monkeypatch.setattr(brains, "_call_llm_raw", fake_raw)
    out = brains._call_llm("SYS", "写点什么", max_tokens=100, extend=True)
    assert out == "第一段写到一半，接着写完了。"
    assert len(calls) == 2, "截断了就该再要一段"
    assert "已经写好的部分" in calls[1], "续写要把已写内容带回去，否则模型会重写开头"

    calls.clear()
    monkeypatch.setattr(brains, "_call_llm_raw",
                        lambda *a, **kw: (calls.append(1) or ("一次写完。", "stop")))
    out = brains._call_llm("SYS", "写点什么", max_tokens=100, extend=True)
    assert out == "一次写完。" and len(calls) == 1, "没截断就不该多调"


def test_call_llm_without_extend_never_retries(monkeypatch):
    """默认（短句类）行为不变：截断也不续写、只调一次。"""
    calls = []
    monkeypatch.setattr(brains, "_call_llm_raw",
                        lambda *a, **kw: (calls.append(1) or ("短。", "length")))
    assert brains._call_llm("SYS", "x", max_tokens=10) == "短。"
    assert len(calls) == 1


def test_call_llm_extend_gives_up_after_max_rounds(monkeypatch):
    calls = []
    monkeypatch.setattr(brains, "_call_llm_raw",
                        lambda *a, **kw: (calls.append(1) or ("永远写不完", "length")))
    out = brains._call_llm("SYS", "x", max_tokens=10, extend=True, max_rounds=3)
    assert len(calls) == 3, "最多 3 段，不能无限续"
    assert out.startswith("永远写不完")


# ---------------------------------------------------------------- 2. 发骚正文

def test_scene_generation_asks_for_a_longer_budget(monkeypatch):
    """发骚正文的额度要够长（1800），而且走 tuning，可按群调。"""
    seen = {}

    def fake(sp, uc, api_key=None, **kw):
        seen.update(kw)
        return "正文"

    monkeypatch.setattr(brains, "_call_llm", fake)
    brains.generate_scene("主角：X")
    assert seen["max_tokens"] == brains.SCENE_MAX_TOKENS >= 1500
    assert seen["extend"] is True, "长文必须开自动续写（否则又会掐在半句话上）"

    from runtime import fasao
    from runtime import tuning as T
    T.set_overrides({"c1|scene.max_tokens": 2400})
    try:
        seen.clear()
        monkeypatch.setattr(fasao, "generate_scene",
                            lambda *a, **kw: seen.update(kw) or "x")
        monkeypatch.setattr(fasao, "_parse_scene_target", lambda t: (None, "某人", []))
        from runtime import ctx
        ctx.set_active_cid("c1")
        try:
            out = fasao.scene("/发骚 某人 测试")
        finally:
            ctx.reset_active_cid()
        assert seen.get("max_tokens") == 2400, "要按群读 scene.max_tokens：{}".format(seen)
        assert "🎲" in out
    finally:
        T.set_overrides({})


def test_scene_length_hint_is_injected_per_group(monkeypatch):
    """P12-AX：正文长度改成**按群注入**【长度要求】，认证群减半不影响上班群。

    屋主 2026-09-29："妹子认证群发骚输出太长了，一半就行"——但上班群 09-27 提过
    "内容太少"，所以不能改提示词默认档一刀切。配了 `scene.length` 才注入。
    """
    from runtime import ctx, fasao
    from runtime import tuning as T
    seen = {}

    def _run(cid, overrides):
        T.set_overrides(overrides)
        ctx.set_active_cid(cid)
        seen.clear()
        monkeypatch.setattr(fasao, "generate_scene",
                            lambda ai, **kw: seen.update(ai=ai) or "x")
        monkeypatch.setattr(fasao, "_parse_scene_target", lambda t: (None, "某人", []))
        try:
            fasao.scene("/发骚 某人 测试")
        finally:
            ctx.reset_active_cid()

    try:
        # 认证群：配了要求 → 注入
        _run("c-auth", {"c-auth|scene.length": "350-550 字，最多 8 行"})
        assert "【长度要求】350-550 字，最多 8 行" in seen["ai"], seen["ai"]
        # 上班群：没配 → 一个字不加（继续用提示词默认档 700-1100 字）
        _run("c-work", {})
        assert "【长度要求】" not in seen["ai"], seen["ai"]
    finally:
        T.set_overrides({})



    """"10 行以内 / 否则会被系统截断"这条已过时（实测 66 行照发），必须撤掉。

    P12-AX（2026-09-29 屋主："妹子认证群发骚输出太长了，一半就行"）之后，
    长度**不再写死在提示词里**：提示词给默认档，具体以运行时注入的【长度要求】为准
    （按群参数 `scene.length`，认证群 350-550 字，上班群不配 = 保持 700-1100 字）。
    """
    files = ("SCENE_PROMPT.md", "SCENE_CONTINUE_PROMPT.md", "SCENE_REWRITE_PROMPT.md")
    for f in files:
        txt = (ROOT / "prompts" / f).read_text(encoding="utf-8")
        assert "10 行以内" not in txt, f
        assert "会被系统截断" not in txt, f
    # 主场景那篇：默认档 + 以【长度要求】为准
    main_txt = (ROOT / "prompts" / "SCENE_PROMPT.md").read_text(encoding="utf-8")
    assert "700-1100" in main_txt, "默认档要留着（上班群靠它）"
    assert "【长度要求】" in main_txt, "长度要以运行时注入的要求为准"


# ---------------------------------------------------------------- 3. 排行前 10

def test_rank_lists_only_the_top_ten(state):
    """65 个人的群不能再刷 66 行：只列前 10 + 一行汇总。"""
    from runtime import ctx
    cid = "c-rank"
    heat = {"u{:02d}".format(i): 100 - i for i in range(25)}
    state_store.group_set(cid, "heat_value", heat)
    ctx.set_active_cid(cid)
    try:
        out = bot.rank_summary()
    finally:
        ctx.reset_active_cid()
    lines = out.splitlines()
    assert lines[0] == "🔥 骚度排行"
    assert len(lines) == 12, "标题 + 前 10 名 + 汇总行：{}".format(lines)
    assert lines[1].startswith("🥇")
    assert lines[10].startswith("10. ")
    assert "只列前 10 名" in lines[-1] and "共 25 人" in lines[-1]


def test_rank_shows_no_summary_line_when_everyone_fits(state):
    """人少就不加汇总行（别画蛇添足）。"""
    from runtime import ctx
    cid = "c-rank-small"
    state_store.group_set(cid, "heat_value", {"a": 3, "b": 2})
    ctx.set_active_cid(cid)
    try:
        out = bot.rank_summary()
    finally:
        ctx.reset_active_cid()
    assert len(out.splitlines()) == 3
    assert "只列前" not in out
