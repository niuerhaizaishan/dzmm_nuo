"""P12-T：第三个 Bot「若瑾」的人设守卫。

屋主口径："人设基本参考小小糯，名字改为若瑾，同时**纯 S 属性、异性恋**。"
基础提示词（`CMD_PROMPT` 那套）写的是小小糯的软糯/百合/被引导，所以若瑾必须靠
`RUOJIN_EXTRA` 的**覆盖声明**压住 —— 这几条钉住"压得住"这件事不会悄悄失效。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import personas as P                                          # noqa: E402

RUOJIN_CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"


@pytest.fixture(autouse=True)
def _bind_group(monkeypatch):
    """把「若瑾那个群」绑到 ruojin 人设上（线上是 config.json 的 groups[].persona 在做）.

    不绑的话 `persona_key_for(未知群)` 会回落到默认人设（小小糯）—— 这正是设计：
    哪个群用哪套人设由配置决定，代码只提供"可用的人设"。
    """
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {RUOJIN_CID: "ruojin"})


def test_persona_registered_with_own_name():
    rec = P.PERSONAS.get("ruojin")
    assert rec, "若瑾的人设 key 必须在代码里登记（配置只能覆盖已存在的 key）"
    assert rec["name_full"] == "若瑾" and rec["name_short"] == "若瑾"
    assert P.me(RUOJIN_CID) == "若瑾"
    assert P.me_full(RUOJIN_CID) == "若瑾"


def test_extra_declares_the_override_first():
    """覆盖声明必须在最前面 —— 它是唯一的"压住基础人设"的机制。"""
    extra = P.RUOJIN_EXTRA
    assert "覆盖声明" in extra[:80]
    assert "作废" in extra[:120]
    for word in ("纯 S", "异性恋"):
        assert word in extra, word
    for bad in ("叫姐姐", "软糯撒娇", "百合", "自称妹妹"):
        assert bad in extra, "要明确点名作废的说法：{}".format(bad)


def test_phrases_speak_in_her_voice():
    """固定话术不能漏出默认人设：既没有"小小糯"，也没有撒娇尾音。"""
    for key in P.known_phrase_keys("ruojin"):
        got = P.p(key, RUOJIN_CID)
        assert "小小糯" not in got, key
        assert "～" not in got, "若瑾不用波浪号讨好人：{}".format(key)
    assert P.p("intro_text", RUOJIN_CID).startswith("我是若瑾")
    assert "母狗" not in P.p("help_text", RUOJIN_CID), "帮助文案别一上来就露骨"


def test_health_is_clean():
    assert P.health("ruojin") == [], P.health("ruojin")


# ---------------------------------------------------------------- P12-AH 人情味 / 口语 / 装饰
#
# 屋主 2026-09-27："① 高冷和 S 中间稍微有点人情味（时不时抽象/开玩笑）；
# ② 回复里加「！！！❤️❤️❤️❤️」修饰；③ 不要太生硬"，追加"更多口语化、更亲近点"。
# 三个决定：❤️ 给糖/认可/收尾约每 2-3 条一次、**正文也挂**、玩笑"每四五条一句冷的"。
# ⚠️ 这一期的风险是**滑回软糯**（P12-Y 花一轮才压住），所以下面既有"要有的"，
#    也有"绝不许再出现的"。

AH_BEFORE = (ROOT / "tools" / "fixtures" / "ruojin_extra_p12ah_before.txt")


def test_ah_adds_the_human_side_but_keeps_the_s():
    e = P.RUOJIN_EXTRA
    assert "▍活人气" in e
    for frag in ("每四五条", "一本正经地把他拆穿", "自嘲", "考核"):
        assert frag in e, frag
    # 玩笑的边界：玩法期间不许笑场
    assert "玩法进行中" in e and "不搞笑" in e
    assert "不堆网络烂梗" in e and "丢主导权" in e


def test_ah_goes_more_colloquial_and_drops_written_style():
    e = P.RUOJIN_EXTRA
    # 书面腔/术语被换掉
    for written in ("幼态化", "陈述句多于问句", "白描", "描述层面", "不求宠"):
        assert written not in e, "书面用语还在：{}".format(written)
    # 换成口语的说法
    for spoken in ("装嫩", "少问、多陈述", "一句大白话", "玩法上", "说人话，别说公文"):
        assert spoken in e, spoken
    # 对照示例（few-shot 比规则管用）
    assert "▍怎么说人话" in e
    assert e.count("→") >= 4, "正反例对照至少要四组"


def test_ah_decorates_with_the_marks_and_keeps_one_definition():
    assert P.RUOJIN_WARM_MARKS == "！！！❤️❤️❤️❤️"
    e = P.RUOJIN_EXTRA
    assert P.RUOJIN_WARM_MARKS in e, "提示词里要带上这串（否则模型不知道长什么样）"
    assert "一条消息最多一处" in e, "控频是这串值不值钱的关键"
    assert "每两三条" in e
    # 屋主当天选的：正文也挂
    assert "玩法正文里也要带" in e
    # ⚠️ 第一版只写"可以带"，实测 5 条回复里出现 0 次 —— 必须写成"要真用出来"，
    #    并且明确它**不算卖萌**（否则模型会跟"别堆♡"那条合并理解）
    assert "要真用出来" in e
    assert "不算" in e and "卖萌" in e
    # 话术表里只有一处登记，且能取到
    assert P.p("warm_marks", RUOJIN_CID) == P.RUOJIN_WARM_MARKS
    # 不许漏到别的群
    auth = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
    assert P.p("warm_marks", auth) == "", "认证群不该拿到若瑾的装饰串"


def test_ah_colloquial_does_not_mean_shorter():
    """第一版写"能两个字说完就别写四个字"，实测把均句长从 24 压到 19、总字数 150→97。"""
    e = P.RUOJIN_EXTRA
    assert "口语化不等于答得少" in e
    assert "能两个字说完就别写四个字" not in e
    assert "一百到三百来字" in e, "长度口径不许被口语化冲掉"


def test_ah_tone_whitelist_and_moe_blacklist():
    assert "得了吧" in P.RUOJIN_TONE_WORDS and "你说呢" in P.RUOJIN_TONE_WORDS
    assert P.RUOJIN_TONE_WORDS in P.RUOJIN_EXTRA
    # 卖萌信号任何时候都不许进 extra 的"可用"清单
    for bad in ("呀", "嘛", "啦", "哦"):
        assert bad not in P.RUOJIN_TONE_WORDS
    assert "不撒娇" in P.RUOJIN_EXTRA and "不装嫩" in P.RUOJIN_EXTRA


def test_ah_paragraph_cap_is_gone_like_the_base_prompt():
    """P12-AG 已按屋主意见取消段落限制，她的 extra 不能还留着"最多两三段"。"""
    assert "最多分段两三段" not in P.RUOJIN_EXTRA
    assert "分段随意" in P.RUOJIN_EXTRA


def test_ah_phrases_are_warmer_but_still_her():
    cid = RUOJIN_CID
    assert P.p("mem_saved", cid) == "记住了！！！"
    assert P.p("fasao_end", cid).startswith("🎲 到这里")
    assert P.RUOJIN_WARM_MARKS in P.p("dj_end", cid)
    assert "❤️" in P.p("reg_ok", cid, who="糯糯")
    # 仍然不许出现软糯标记（现有断言之外的定点检查）
    for key in ("mem_saved", "fasao_end", "dj_end", "reg_ok", "no_play", "not_found"):
        txt = P.p(key, cid, who="糯糯")
        for mark in ("～", "呀", "嘛", "啦", "♡", "人家"):
            assert mark not in txt, "{} 里有软萌标记 {!r}: {}".format(key, mark, txt)


def test_ah_has_a_before_snapshot_to_compare_against():
    """A/B 对照的基准文本要留在仓库里（`tools/probe_voice_register.py` 用它跑"旧版"）。"""
    assert AH_BEFORE.is_file(), "缺少 P12-AH 之前的 extra 快照"
    old = AH_BEFORE.read_text(encoding="utf-8")
    assert "▍活人气" not in old, "快照必须是**改之前**的版本"
    assert "幼态化" in old and "陈述句多于问句" in old
    assert old != P.RUOJIN_EXTRA


def test_default_groups_are_untouched():
    """认证群还是小小糯（别把若瑾的覆盖招呼到别人头上）."""
    from personas import ROOMS
    auth = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
    assert P.me(auth) == "小小糯"
    assert auth not in ROOMS, "认证群不由 ROOMS 派生（它走默认人设）"


# ---------------------------------------------------------------- 羞辱前缀

def test_insult_prefix_only_hits_the_configured_target():
    """屋主指定：羞辱「工具熊」时消息最前面加 `/talk`（做成话术，可改可热加载）."""
    import bot
    assert bot._insult_prefix(RUOJIN_CID, "工具熊 Jr.") == "/talk"
    assert bot._insult_prefix(RUOJIN_CID, "工具熊") == "/talk"
    assert bot._insult_prefix(RUOJIN_CID, "小张") == ""
    assert bot._insult_prefix(RUOJIN_CID, "") == ""
    # 别的群没配 → 不加（认证群一个字不变）
    assert bot._insult_prefix("bd7fe6a4-0065-4551-ad3d-94116dba8f9c", "工具熊 Jr.") == ""


def test_insult_prefix_is_data_not_code():
    """关键词与内容都从话术表来（页面能改；改完热加载 1 秒生效）."""
    assert P.p("insult_prefix_who", RUOJIN_CID) == "工具熊"
    assert P.p("insult_prefix_text", RUOJIN_CID) == "/talk"
    assert not P.p("insult_prefix_who", "bd7fe6a4-0065-4551-ad3d-94116dba8f9c")


def test_bot_wires_the_prefix_and_drops_the_robot_mark():
    """加了前缀的消息不能再有"🤖 "在前面 —— 否则 /talk 就不在"最最前面"了."""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "_insult_prefix(cid, get_full_name(target_uid))" in src
    assert 'reply if _no_prefix else ("🤖 " + reply)' in src
    assert "_no_prefix = False" in src


# ---------------------------------------------------------------- 调教话术（P12-V）

DJ_KEYS = ("dj_format", "dj_no_target", "dj_start", "dj_timeout",
           "dj_replied", "dj_idle", "dj_hint")
CUTE_MARKS = ("哦～", "呀", "姐妹", "啦～", "～\n", "～。")


def test_diaojiao_phrases_default_to_the_old_literals():
    """没配覆盖的群（认证群/玫瑰房）一个字都不能变 —— 默认值 = 原来的硬编码字面量."""
    auth = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
    assert P.p("dj_format", auth) == "格式：/调教 目标 命令（例如 /调教 糯糯 学狗叫）"
    assert P.p("dj_no_target", auth) == "没找到调教目标哦～换个人试试？"
    assert P.p("dj_start", auth, who="糯糯") == "🔗 调教开始！目标：糯糯～"
    assert P.p("dj_timeout", auth) == "⏱️ 调教结束啦（5分钟没动静）～"
    assert "30秒没人发我就自己来" in P.p("dj_replied", auth)
    assert P.p("dj_idle", auth) == "30秒没人发命令呀～那我继续啦～"
    from runtime.diaojiao import DJ_PARTICIPATE_HINT
    assert P.p("dj_hint", auth) == DJ_PARTICIPATE_HINT


def test_ruojin_diaojiao_phrases_are_s_voiced():
    """若瑾是纯 S：调教这套文案不能漏出「哦～/呀/姐妹」的软糯口吻."""
    for key in DJ_KEYS:
        txt = P.p(key, RUOJIN_CID, who="糯糯")
        assert txt, "{} 必须有若瑾版本".format(key)
        for mark in CUTE_MARKS:
            assert mark not in txt, "{} 里还有软萌口吻 {!r}：{}".format(key, mark, txt)
    assert "{who}" not in P.p("dj_start", RUOJIN_CID, who="糯糯")


def test_diaojiao_flow_uses_the_phrases(monkeypatch):
    """`start_diaojiao` 真的走话术表（不是又写死一份）—— 用若瑾的音色验一次."""
    from runtime import ctx
    from runtime import diaojiao as dj

    monkeypatch.setattr(dj, "_parse_scene_target", lambda rest: ("u-1", "糯糯", ["学狗叫"]))
    monkeypatch.setattr(dj, "_dj_target_text", lambda uid: "目标资料")
    monkeypatch.setattr(dj, "generate_diaojiao", lambda *a, **kw: "跪下。")
    monkeypatch.setattr(dj, "_set_dj_state", lambda *a, **kw: None)
    monkeypatch.setattr(dj, "get_profile", lambda uid: {"xp": []})
    ctx.set_active_cid(RUOJIN_CID)
    try:
        out = dj.start_diaojiao(RUOJIN_CID, "/调教 糯糯 学狗叫", "u-2")
    finally:
        ctx.reset_active_cid()
    assert out.startswith("🔗 调教开始。目标：糯糯。"), out
    assert "跪下。" in out
    assert out.rstrip().endswith(P.p("dj_hint", RUOJIN_CID)), out
    assert "想参与的姐妹" not in out
