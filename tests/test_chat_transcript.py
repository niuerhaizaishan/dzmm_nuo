"""P12-AD：`@` 对话要**连得上**（2026-09-27 屋主："你看下上班群，不连贯啊"）。

实测两句就露馅（上班群 18:50-18:51）：
    用户：@若瑾 我是大傻逼
    她  ：说两遍，是想让我记住，还是想让我接话。      <- 当前问题被喂了两遍
    用户：@若瑾 你也是
    她  ：我不太确定你在回应哪句话…那话我还没说         <- 她看不到自己上一句

根因：`msgs` 是**这一批新消息**，不是对话记录。批处理扫描里 `is_self → continue`
（只处理指令，不记录），机器人自己发出去的话也不回到这条队列，所以
`msgs[-N:]` 里既没有历史、又把当前问题算进去了一遍。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402
import bot                                                     # noqa: E402


def _fresh(cid):
    """每个用例用一个独占的 cid，免得互相看见。"""
    bot._TURNS.pop(cid, None)
    bot._TURN_SEEN.pop(cid, None)
    bot._TURN_SEEDED.discard(cid)
    return cid


# ---------------------------------------------------------------- 基本记账

def test_remembers_both_sides_in_order():
    cid = _fresh("t-order")
    bot._remember_turn(cid, "甲", "在吗", "m1")
    bot._remember_turn(cid, "我", "在的", "m2")
    bot._remember_turn(cid, "甲", "刚干嘛去了", "m3")
    ctx = bot._recent_turns(cid, 10)
    assert [(c["sender"], c["text"]) for c in ctx] == [
        ("甲", "在吗"), ("我", "在的"), ("甲", "刚干嘛去了")]


def test_blank_text_is_ignored():
    cid = _fresh("t-blank")
    bot._remember_turn(cid, "甲", "   ", "m1")
    bot._remember_turn(cid, "甲", "", "m2")
    bot._remember_turn(cid, "甲", None)
    assert bot._recent_turns(cid, 10) == []


def test_same_message_id_is_not_recorded_twice():
    """webhook 推一遍、HTTP 兜底又拉一遍 —— 同一 message_id 只能进一次。"""
    cid = _fresh("t-dedupe-mid")
    bot._remember_turn(cid, "甲", "同一句", "m1")
    bot._remember_turn(cid, "甲", "同一句", "m1")
    assert len(bot._recent_turns(cid, 10)) == 1


def test_consecutive_identical_line_collapses():
    """同一句话紧接着又来一遍（有 id 的回读 / 我们自己发的话被回读）不能记两遍。"""
    cid = _fresh("t-dedupe-text")
    bot._remember_turn(cid, "我", "好的呀")
    bot._remember_turn(cid, "我", "好的呀")
    assert len(bot._recent_turns(cid, 10)) == 1
    bot._remember_turn(cid, "我", "好的呀", "echo-1")     # 平台回读同一条
    assert len(bot._recent_turns(cid, 10)) == 1


def test_a_genuine_repeat_far_apart_is_kept():
    cid = _fresh("t-repeat")
    bot._remember_turn(cid, "甲", "在吗", "m1")
    for i in range(3):
        bot._remember_turn(cid, "甲", "别的话{}".format(i), "x{}".format(i))
    bot._remember_turn(cid, "甲", "在吗", "m2")
    assert [t["text"] for t in bot._recent_turns(cid, 10)].count("在吗") == 2


def test_groups_do_not_see_each_other():
    a, b = _fresh("t-iso-a"), _fresh("t-iso-b")
    bot._remember_turn(a, "甲", "A群的话", "ma")
    bot._remember_turn(b, "乙", "B群的话", "mb")
    assert [c["text"] for c in bot._recent_turns(a, 10)] == ["A群的话"]
    assert [c["text"] for c in bot._recent_turns(b, 10)] == ["B群的话"]
    assert len(bot._recent_turns(_fresh("t-iso-empty"), 10)) == 0


def test_history_is_bounded_per_group():
    cid = _fresh("t-bound")
    for i in range(bot._TURN_MAX + 25):
        bot._remember_turn(cid, "甲", "第{}句".format(i), "m{}".format(i))
    lst = bot._TURNS[cid]
    assert len(lst) == bot._TURN_MAX, "内存里的历史要有上限"
    assert lst[-1]["text"] == "第{}句".format(bot._TURN_MAX + 24)
    assert "第0句" not in [t["text"] for t in lst]


# ---------------------------------------------------------------- 当前问题只出现一次

def test_current_question_is_excluded_from_context():
    """这就是"说两遍，是想让我记住"那个 bug。"""
    cid = _fresh("t-exclude")
    bot._remember_turn(cid, "甲", "你好", "m1")
    bot._remember_turn(cid, "我", "好呀", "m2")
    bot._remember_turn(cid, "甲", "我是大傻逼", "m3")     # 当前这条已经进了记录
    ctx = bot._recent_turns(cid, 10, exclude_mid="m3")
    assert [c["text"] for c in ctx] == ["你好", "好呀"]
    # 别的问题不受影响
    assert len(bot._recent_turns(cid, 10, exclude_mid="m1")) == 2
    # 不传就都留着（其他地方还能用）
    assert len(bot._recent_turns(cid, 10)) == 3


def test_generate_reply_gets_own_previous_line():
    """行为级：交给模型的上下文里必须有"她上一句说过的"，且当前问题只有一条。"""
    cid = _fresh("t-pipeline")
    bot._remember_turn(cid, "甲", "我是大傻逼", "m1")              # 上一轮问题
    bot._remember_turn(cid, "我", "别急着认，我还没接话呢", "m2")   # 她的上一句回复
    bot._remember_turn(cid, "甲", "你也是", "m3")                  # 当前提问
    ctx = bot._recent_turns(cid, 20, exclude_mid="m3")
    ctx.append({"sender": "甲（男生）", "text": "你也是"})

    seen = {}

    def fake(_sp, uc, api_key=None, **kw):
        seen["content"] = uc
        return "答"

    real = brains._call_llm
    brains._call_llm = fake
    try:
        brains.generate_reply(ctx, max_tokens=800, context_msgs=20)
    finally:
        brains._call_llm = real

    content = seen["content"]
    assert "别急着认，我还没接话呢" in content, "她必须看得见自己上一句"
    assert content.count("你也是") == 1, "当前问题只能出现一次"


def test_context_size_limit_still_applies_to_transcript():
    cid = _fresh("t-limit")
    for i in range(30):
        bot._remember_turn(cid, "甲", "第{}句".format(i), "m{}".format(i))
    assert len(bot._recent_turns(cid, 5)) == 5
    assert [c["text"] for c in bot._recent_turns(cid, 5)] == \
        ["第{}句".format(i) for i in range(25, 30)]


# ---------------------------------------------------------------- 首次补历史

def test_seed_pulls_platform_history_once(monkeypatch):
    cid = _fresh("t-seed")
    calls = []

    def fake_get_messages(c, *a, **kw):
        calls.append((c, a, kw))
        return [
            {"message_id": "p1", "sent_by": "u-other",
             "content": {"text": "平台上的旧话"}},
            {"message_id": "p2", "sent_by": "me",
             "content": {"text": "平台上的旧回复"}},
            {"message_id": "p3", "sent_by": "u-other", "content": {}},
        ]

    monkeypatch.setattr(bot, "get_messages", fake_get_messages)
    monkeypatch.setattr(bot, "is_self", lambda uid: uid == "me")
    monkeypatch.setattr(bot, "get_name", lambda uid: "别人" if uid == "u-other" else uid)

    bot._seed_turns(cid)
    assert len(calls) == 1, "补一次就够"
    texts = [(t["who"], t["text"]) for t in bot._TURNS[cid]]
    assert texts == [("别人", "平台上的旧话"), ("我", "平台上的旧回复")]

    bot._seed_turns(cid)
    assert len(calls) == 1, "同一个群不要每句 @ 都去拉平台"


def test_seed_does_not_clobber_live_history(monkeypatch):
    """已经在聊了就别用平台历史去插队（顺序会乱）。"""
    cid = _fresh("t-seed-skip")
    bot._remember_turn(cid, "甲", "刚说的话", "m1")
    called = []
    monkeypatch.setattr(bot, "get_messages",
                        lambda *a, **kw: called.append(1) or [])
    bot._seed_turns(cid)
    assert not called
    assert [t["text"] for t in bot._TURNS[cid]] == ["刚说的话"]


def test_seed_failure_is_not_fatal(monkeypatch):
    """平台拉不到历史时，@ 问答照样要能答。"""
    cid = _fresh("t-seed-fail")

    def boom(*a, **kw):
        raise RuntimeError("平台 502")

    monkeypatch.setattr(bot, "get_messages", boom)
    bot._seed_turns(cid)                     # 不抛
    assert bot._recent_turns(cid, 10) == []
    assert cid in bot._TURN_SEEDED, "失败也要记住别再反复打平台"


# ---------------------------------------------------------------- 接线（源码级）

def test_loop_records_the_whole_batch_including_own_messages():
    """批处理里 `is_self → continue` 只是不处理指令，不代表不用记账。"""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    i = src.index("_lag = message_lag(msgs)")
    seg = src[i:i + 900]
    assert "_remember_turn(" in seg
    assert '"我" if is_self(_muid) else get_name(_muid)' in seg
    assert "_m.get(" in seg or "_m.get('" in seg


def test_own_replies_are_recorded_on_every_send_path():
    """三处发回复的地方（指令 / @问答 / 房间）都要记，不然下一句又断片。"""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    sends = src.count('log("💬 " + reply[:200])')
    assert sends == 3, "发回复的地方数变了，去看是不是漏了记账"
    assert src.count('_remember_turn(cid, "我"') >= 3
