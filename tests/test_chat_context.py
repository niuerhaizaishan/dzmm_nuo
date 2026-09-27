"""P12-AC：`@` 问答要**带上下文、能多写点、用自己的知识答**（屋主 2026-09-27）。

改之前的三件事：
1. 上下文：只喂**末尾 4 条**，而且 `is_self` 的直接 `continue` —— **机器人自己
   说过的话全被丢掉**，所以每句都像从零开始（用户感受就是"没有上下文"）。
2. 长度：`generate_reply` 默认 400 token，提示词还写着"简洁，一般 1-3 句话
   （群聊里不要写小作文）"。
3. 知识：提示词要求"不知道就说不知道""天气/新闻/股价…不要编造"，于是爱答
   "这个我不太清楚，建议看看天气预报" —— 用户要的是"用你自己的知识回答"。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402
import bot                                                     # noqa: E402
from runtime import features as F                              # noqa: E402


# ---------------------------------------------------------------- 注册表/参数

def test_chat_is_a_registered_tunable_feature():
    f = F.get("chat")
    assert f.default is True, "默认开着（否则等于悄悄改掉今天的行为）"
    params = {p.key: p for p in f.params}
    assert params["reply.max_tokens"].default == 800
    assert params["reply.context_msgs"].default == 20
    # 边界要拦得住危险值
    assert params["reply.max_tokens"].lo >= 100 and params["reply.max_tokens"].hi <= 3000
    assert params["reply.context_msgs"].lo >= 4 and params["reply.context_msgs"].hi <= 60


# ---------------------------------------------------------------- 上下文

def _capture(monkeypatch):
    seen = {}

    def fake(sp, uc, api_key=None, **kw):
        seen["content"] = uc
        seen.update(kw)
        return "答"

    monkeypatch.setattr(brains, "_call_llm", fake)
    return seen


def test_generate_reply_honours_context_size(monkeypatch):
    """`context_msgs` 说了算：给 6 条时第 1 条要进来，给 3 条时不能进来。"""
    msgs = [{"sender": "甲", "text": "第{}条".format(i)} for i in range(1, 7)]
    seen = _capture(monkeypatch)
    brains.generate_reply(msgs, context_msgs=6)
    assert "第1条" in seen["content"] and "第6条" in seen["content"]
    seen.clear()
    brains.generate_reply(msgs, context_msgs=3)
    assert "第1条" not in seen["content"]
    assert "第4条" in seen["content"] and "第6条" in seen["content"]


def test_generate_reply_defaults_to_twenty(monkeypatch):
    """不传就用 20 条（以前写死 `[-10:]`）。"""
    msgs = [{"sender": "甲", "text": "第{}条".format(i)} for i in range(1, 26)]
    seen = _capture(monkeypatch)
    brains.generate_reply(msgs)
    assert "第6条" in seen["content"] and "第25条" in seen["content"]
    assert "第5条" not in seen["content"]


def test_generate_reply_passes_the_token_budget(monkeypatch):
    seen = _capture(monkeypatch)
    brains.generate_reply([{"sender": "甲", "text": "在吗"}], max_tokens=800)
    assert seen["max_tokens"] == 800


def test_at_branch_includes_its_own_lines_and_reads_the_tunings():
    """源码级：`@` 分支必须把"我自己说过的话"喂进去、并读这两个参数与闸门。"""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    i = src.index("普通问答：带上下文")
    j = src.index('reply = (reply or "").strip()', i)
    seg = src[i:j]
    assert '_feat_on(cid, "chat")' in seg, "要能被开关关掉"
    assert '{"sender": "我", "text": mtxt}' in seg, "自己说过的话要带上（标成「我」）"
    assert "_tune(\"reply.context_msgs\", cid)" in seg
    assert "_tune(\"reply.max_tokens\", cid)" in seg
    # 旧的"末尾 4 条 + 丢掉自己的话"不许回来
    assert "for m in msgs[-4:]" not in seg


# ---------------------------------------------------------------- 提示词口径

def test_prompt_asks_for_more_and_for_own_knowledge():
    """提示词里必须真写着"用你自己的知识"和更长的篇幅（文件与代码默认各一份）。"""
    txt = (ROOT / "prompts" / "CMD_PROMPT.md").read_text(encoding="utf-8")
    assert "用你自己的知识、经验和判断回答" in txt
    assert "别动不动拒答" in txt
    assert "100-300 字" in txt
    assert "一般1-3句话" not in txt, "「只说 1-3 句」的旧口径必须撤掉"
    assert brains.CMD_PROMPT == txt.rstrip("\n") or "用你自己的知识" in brains.CMD_PROMPT


def test_ruojin_persona_matches_the_new_stance():
    """若瑾那份也要跟上（她原来写死"一到三句""不知道就说不知道"）。"""
    extra = __import__("personas").RUOJIN_EXTRA
    assert "一到三句" not in extra
    assert "拿自己的见识答" in extra
    assert "别打太极" in extra
