"""P12-AE：别人**右键点击回复**我的消息时，也要接话（2026-09-27 屋主）。

屋主原话：
    "在用户第一次 @ 若瑾说话，若瑾回复后，如果用户在回复的内容上通过右键点击
     回复后，若瑾应该自动回复。"

依据不是猜的：线上 dump 出来的原始消息里，被引用的那条确实留着痕 ——
    content.reference = {"id": "<uuid>", "sentBy": "<uid>",
                         "content": {"text": "被引用的原文", "type": "text"}}
（`tools/probe_reply_capability.py --only in`，40 条里 3 条有；下面是**逐字**抄的形状。）

另一半（把她自己的回复挂在对方那条上）**官方 Bot API 做不到**：出站
`send-message` 的 15 个候选字段一个都没进 schema（对照组 `chatroom_id` 非法
会报 fieldErrors，说明这套判定看得见校验错误）。所以本文件只钉入站那半。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                     # noqa: E402
import webhook_server                                          # noqa: E402
from runtime import features as F                              # noqa: E402

ME = "9938211c-b245-4cad-a594-cd9a1eb4966a"      # 若瑾 bot
OTHER = "a4897fb3-d286-4c08-91fb-93be6d0fc974"   # 群友


def _msg(text, sent_by=OTHER, reference=None, mid="m1"):
    c = {"type": "text", "text": text}
    if reference is not None:
        c["reference"] = reference
    return {"message_id": mid, "sent_by": sent_by, "content": c}


# 线上逐字抄下来的形状
REF_MINE = {"id": "470750e7-38e5-4d1f-966f-553075047766", "sentBy": ME,
            "content": {"text": "他应了。下一条命令谁发——/命令 xxx。", "type": "text"}}
REF_OTHER = {"id": "5e22d599-24b2-4664-9692-e72085fd2072", "sentBy": OTHER,
             "content": {"text": "我chovy", "type": "text"}}


# ---------------------------------------------------------------- 取引用

def test_parses_the_real_platform_shape():
    ref = bot._ref_of(_msg("你也是", reference=REF_MINE))
    assert ref["id"] == REF_MINE["id"]
    assert ref["sent_by"] == ME
    assert ref["text"] == "他应了。下一条命令谁发——/命令 xxx。"


def test_flat_reference_shape_also_works():
    """`reference.text`（不带内层 content）也认 —— 免得形状一变就静默失灵。"""
    ref = bot._ref_of(_msg("在吗", reference={"id": "x", "sentBy": ME, "text": "原文"}))
    assert ref["text"] == "原文"


def test_no_reference_is_empty_not_crash():
    for m in (_msg("普通消息"), {"content": None}, {}, {"content": {"reference": "str"}}):
        assert bot._ref_of(m) == {}
        assert bot._quotes_self(m) is False


def test_quotes_self_only_when_the_quoted_one_is_me(monkeypatch):
    monkeypatch.setattr(bot, "is_self", lambda uid: uid == ME)
    assert bot._quotes_self(_msg("你也是", reference=REF_MINE)) is True
    assert bot._quotes_self(_msg("你说呢", reference=REF_OTHER)) is False
    assert bot._quotes_self(_msg("没引用")) is False


def test_quotes_self_ignores_empty_sender(monkeypatch):
    """引用对象没有 sentBy 时不能当"引用了我"（否则谁都能触发）。"""
    monkeypatch.setattr(bot, "is_self", lambda uid: True)   # 极端：is_self 什么都说是
    assert bot._quotes_self(_msg("hi", reference={"id": "x", "content": {"text": "?"}})) is False


# ---------------------------------------------------------------- 开关

def test_feature_registered_and_off_by_default():
    f = F.get("quote_reply")
    assert f.default is False, "默认关＝今天的行为（只有 @ 才答），别悄悄改线上"
    assert f.stage == "wired"


def test_scan_loop_consults_the_switch():
    # P3-19 起主循环体在 runtime/mainloop.py（bot 模块桥 B 前缀，子串兼容）
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    i = src.index("for m in pending:")
    j = src.index("_pick_leftover(", i)
    seg = src[i:j]
    # P12-BM B：判据统一走 `_quote_reply_on(cid)`（= quote_reply 开关 **或** 本群 at_only）
    assert "_quote_reply_on(cid)" in seg, "要能按群关掉／at_only 群天然放行"
    assert "_quotes_self(m)" in seg
    assert "_via_quote" in seg, "要记住这条是引用触发（上下文里要摆出被引用的原文）"


def test_quote_trigger_helper_covers_at_only_groups():
    """P12-BM B：`at_only` 群里"引用她"天然算触发。

    为什么：`at_only` 的语义就是"只接对她说的话"，而引用她的消息明显是对她说的；
    不这样的话每开一个新群都要记得单独开 `quote_reply` —— 茶水间 2026-09-30 就是
    这么静默不应的（屋主："我回复她发的消息不能触发她啊？"）。
    """
    src = (ROOT / "runtime" / "reply_gate.py").read_text(encoding="utf-8")
    i = src.index("def quote_reply_on(")
    seg = src[i:i + 900]
    assert '_feat_on(cid, "quote_reply")' in seg
    assert '_feat_on(cid, "at_only")' in seg, "at_only 群要天然放行"
    # 非 at_only 群（玫瑰房那套房间策略）仍然只看 quote_reply ⇒ 这里必须是 or 关系
    # （在本函数段内取最后一个 return —— 窗口会越过短函数，不能直接对 seg 取尾）
    assert " or " in seg[:seg.find("\n\n\n")].split("return")[-1]


def test_quoted_original_is_put_into_context_without_duplicating():
    # P3-3-4 起 @ 闲聊分支在 runtime/at_chat.py
    src = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    i = src.index("P12-AE：右键回复我的那条")
    seg = src[i:i + 900]
    assert "_ref_of(_at_msg_obj" in seg
    assert "not any(c[\"text\"] == _rtxt" in seg, "已经在上下文里就别再塞一遍"


# ---------------------------------------------------------------- webhook 那条路

def test_webhook_keeps_the_reference():
    """webhook 以前只取 type/text，reference 被整个丢掉。"""
    update = {"message": {
        "message_id": "m9", "chat_id": "bd7fe6a4-0065-4551-ad3d-94116dba8f9c",
        "sent_by": OTHER, "text": "你也是",
        "content": {"type": "text", "text": "你也是", "reference": REF_MINE},
    }}
    out = webhook_server.to_internal(update)
    assert out is not None
    assert out["content"]["reference"] == REF_MINE


def test_webhook_without_reference_is_none():
    update = {"message": {
        "message_id": "m10", "chat_id": "bd7de6a4-0065-4551-ad3d-94116dba8f9c",
        "sent_by": OTHER, "text": "普通消息",
    }}
    out = webhook_server.to_internal(update)
    assert out is not None
    assert out["content"]["reference"] is None
