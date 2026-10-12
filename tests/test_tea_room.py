"""P12-AK：顶层茶水间的缺勤回顾（@她问"我不在的时候群里发生了什么"）。

屋主 2026-09-29 的口径（三次改口，最后定这版）：
  1. ~~忽略别的 bot~~ → **忽略"打卡 / 摸鱼躲藏 / 记忆考核"内容**；
  2. 窗口默认值**先短一点**；
  3. 小小糯的**输入内容要丰富完整一些**。

## 取证（`tools/probe_tea_noise.py`，2026-09-29 取样 100 条）

群 `4fc4a2fd-…`（顶层茶水间，779 人）里 **39% 的消息是噪音**：

| 噪音 | 谁发的 | 长什么样 |
|---|---|---|
| 摸鱼躲藏 | `总监事「bot」`(311a2438) 38 条 | "XX，躲猫猫开始，开局不扣除…请选择一个地点：1（…）2（…）"、"【巡查】巡查 1（…）…" |
| 记忆考核 | 同上 | "第 3 级通过。现在收手可获得 3 摸鱼币…"、"恭喜 晓 完成第 5 级记忆考核" |
| 打卡/彩票 | 同上 | "✅ 已购 5 注（10 摸鱼币）🎲 机选号码…" |
| 游戏指令 | 群友 | `/开始摸鱼躲藏`、`/躲 3`、`/记忆考核`、`/答案 pIH7Z`、`/继续`、`/帮助` |

还有一条**连带风险**：主循环把 `/xxx` 一律当"给我的指令"，在这个群里会走到兜底
回一句"这个我不会" —— 779 人的群每条游戏指令回一句就是刷屏。所以加了 `slash_cmds`
闸门（默认开＝今天的行为；茶水间关掉）。
"""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402
import dzmm                                                    # noqa: E402
import personas as P                                           # noqa: E402
import pytest                                                  # noqa: E402
from runtime import features as F                              # noqa: E402
from runtime import recap as RC                                # noqa: E402
from runtime import tuning as T                                # noqa: E402

CID = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"


@pytest.fixture
def tea(monkeypatch):
    """把按群参数与名字查询定住（别在测试里打 HTTP / 依赖真实配置）。"""
    vals = {"recap.ignore_kw": "打卡,摸鱼,躲猫猫,巡查,记忆考核,摸鱼币",
            "recap.default_hours": 3, "recap.max_hours": 48}
    monkeypatch.setattr(RC, "_tune", lambda k, cid=None: vals.get(k, T.get(k, cid)))
    monkeypatch.setattr(RC, "get_name", lambda uid: {"u1": "紫苑", "u2": "efe"}.get(uid, uid))
    monkeypatch.setattr(RC, "_pme", lambda cid=None: "小小糯")
    monkeypatch.setattr(RC, "is_self", lambda uid: uid == "me")
    return vals


def _m(uid, text, ctype="text", sent_at="2026-09-29 03:00:00+00"):
    return {"sent_by": uid, "sent_at": sent_at, "content": {"type": ctype, "text": text}}


# ---------------------------------------------------------------- ① 噪音过滤

def test_ignore_kw_parses_the_configured_list(monkeypatch):
    monkeypatch.setattr(RC, "_tune", lambda k, cid=None: "打卡，躲猫猫、巡查 摸鱼")
    assert RC._ignore_kw(CID) == ("打卡", "躲猫猫", "巡查", "摸鱼")
    monkeypatch.setattr(RC, "_tune", lambda k, cid=None: "")
    assert RC._ignore_kw(CID) == (), "空配置 = 不过滤（别的群一个字不变）"


def test_noise_messages_never_reach_the_transcript(tea, monkeypatch):
    msgs = [
        _m("u1", "早上好各位"),
        _m("bot1", "紫苑，躲猫猫开始，开局不扣除。请选择一个地点：1（配电间）2（健身房）"),
        _m("u2", "/开始摸鱼躲藏"),
        _m("bot1", "第 3 级通过。现在收手可获得 3 摸鱼币，或发送 /继续 挑战下一层。"),
        _m("u1", "今天中午吃什么"),
        _m("bot1", "✅ 已购 5 注（10 摸鱼币） 　🎲 机选号码"),
    ]
    text, n = RC._recap_transcript(msgs, CID)
    assert "躲猫猫" not in text and "摸鱼币" not in text and "记忆" not in text
    assert "早上好各位" in text and "今天中午吃什么" in text
    assert n == 2, "转录里只该剩两条真人闲聊：{}".format(text)


def test_slash_traffic_is_noise_in_a_group_that_ignores_slash(tea, monkeypatch):
    """本群不接 `/` 指令（`slash_cmds` 关）时，`/躲 6`、`/答案 xxx`、`/继续` 都是别家 bot
    的协议，一律不进转录 —— 实测过滤后前 8 行里有 7 行是它们。"""
    monkeypatch.setattr(RC, "_feat_on", lambda cid, key: key != "slash_cmds")
    msgs = [_m("u1", "/躲 6"), _m("u1", "/答案 D4BfI"), _m("u1", "/继续"),
            _m("u2", "在聊年会的事")]
    text, n = RC._recap_transcript(msgs, CID)
    assert n == 1 and "在聊年会的事" in text, text
    # 反过来：接了斜杠指令的群（认证群）不该被这条规则误伤
    monkeypatch.setattr(RC, "_feat_on", lambda cid, key: True)
    _t2, n2 = RC._recap_transcript(msgs, CID)
    assert n2 == 4


def test_noise_does_not_count_as_someone_looking_for_you(tea):
    """游戏播报里出现你的名字，不算"有人找你"。"""
    msgs = [
        _m("bot1", "紫苑，躲猫猫开始，请选择地点"),          # 噪音（含名字）
        _m("u2", "紫苑你昨天说的那个事"),                      # 真提到
    ]
    hits, who, _s = RC._recap_mentions(msgs, "u1", RC._ignore_kw(CID))
    assert hits == 1 and who == ["efe"], (hits, who)


def test_production_passes_the_filter_into_mentions():
    """生产调用必须把过滤词与群号交给 `_recap_mentions` —— 忘了传就等于没过滤。"""
    src = (ROOT / "runtime" / "recap.py").read_text(encoding="utf-8")
    assert "_recap_mentions(msgs, uid, _kw, cid)" in src


def test_without_the_filter_nothing_is_dropped(monkeypatch, tea):
    """没配 `recap.ignore_kw` 的群（认证群）行为不变。"""
    monkeypatch.setattr(RC, "_tune", lambda k, cid=None: "" if k == "recap.ignore_kw" else 3)
    msgs = [_m("bot1", "紫苑，躲猫猫开始"), _m("u1", "在的")]
    text, n = RC._recap_transcript(msgs, CID)
    assert n == 2 and "躲猫猫" in text


# ---------------------------------------------------------------- ② 默认窗口

def test_default_window_uses_the_per_group_param(tea, monkeypatch):
    """没写时间段时用 `recap.default_hours`（原来写死 6 小时）。"""
    monkeypatch.setattr(RC.state_store, "load_state", lambda: {})
    since, until, label = RC._recap_window(CID, "u1", "我不在的时候发生了什么")
    hours = (until - since) / 3600.0
    assert abs(hours - 3) < 0.05, "应按按群参数取 3 小时，实际 {:.2f}".format(hours)
    assert label == "最近 3 小时"


def test_the_question_itself_is_not_part_of_the_window(tea, monkeypatch):
    """屋主现场报的 bug：窗口塌成 0 分钟、还显示"群里 1 条"（那 1 条就是提问自己）。

    根因：他在这个群**没发过言**，`bot.py` 的发言统计在处理这条提问时刚把它写进
    `last_speak[uid]`（`prev_speak` 还是空的）⇒ `since = last_speak` = 提问那一刻。
    """
    now = time.time()
    # 没发过言的人：prev 空、last 就是"刚才那句提问"
    monkeypatch.setattr(RC.state_store, "load_state",
                        lambda: {CID: {"last_speak": {"u9": now - 5}}})
    since, until, label = RC._recap_window(CID, "u9", "我不在的时候群里发生了什么")
    assert label == "最近 3 小时", "起点太近就该退回默认窗口，而不是 0 分钟：{}".format(label)
    assert (until - since) / 3600.0 > 2.9


def test_a_genuine_last_speak_still_wins(tea, monkeypatch):
    """他真的离开了 40 分钟：那就用他的上一次发言当起点（别一律退回默认）。"""
    now = time.time()
    monkeypatch.setattr(RC.state_store, "load_state",
                        lambda: {CID: {"last_speak": {"u9": now - 2400}}})
    since, _u, label = RC._recap_window(CID, "u9", "我不在的时候群里发生了什么")
    assert label == "" and abs((_u - since) - 2400) < 5


def test_prev_speak_is_preferred_over_last(tea, monkeypatch):
    """有 prev_speak（上上条）时用它 —— 那才是"这次发言之前"的边界。"""
    now = time.time()
    monkeypatch.setattr(RC.state_store, "load_state",
                        lambda: {CID: {"prev_speak": {"u9": now - 7200},
                                       "last_speak": {"u9": now - 30}}})
    since, _u, label = RC._recap_window(CID, "u9", "我不在的时候群里发生了什么")
    assert label == "" and abs((_u - since) - 7200) < 5


def test_recap_accepts_exclude_mid():
    """`recap_absent` 要能排掉当前那条提问（否则它会被算成"群里发生的事"）。"""
    import inspect
    sig = inspect.signature(RC.recap_absent)
    assert "exclude_mid" in sig.parameters
    # P3-3-4 起 @ 问答那条路在 runtime/at_chat.py
    src = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")
    assert 'recap_absent(\n                cid, at_sender, q,' in src, \
        "@ 问答那条路要把当前消息 id 传进去"
    assert "(_cmd_msg or {}).get(\"message_id\")" in (
        ROOT / "runtime" / "dispatch.py").read_text(encoding="utf-8"), \
        "指令那条路也要传（P3-3-2 起分派链在 runtime/dispatch.py）"


def test_explicit_window_still_wins(tea, monkeypatch):
    monkeypatch.setattr(RC.state_store, "load_state", lambda: {})
    since, _u, label = RC._recap_window(CID, "u1", "我不在的时候两小时发生了啥")
    assert label == "最近 2 小时"


def test_bounded_message_fetch_starts_at_window_end(monkeypatch):
    """有明确终点时不能先取最新消息，否则新消息会吃完分页额度。"""
    calls = []
    pages = {
        "2026-10-08 00:00:00+00": [
            _m("u1", "昨天的消息", sent_at="2026-10-07 12:00:00+00"),
        ],
        "2026-10-07 12:00:00+00": [
            _m("u2", "昨天更早的消息", sent_at="2026-10-07 03:00:00+00"),
        ],
    }

    def fake_get_messages(chat_id, limit, before, cookies):
        calls.append(before)
        return pages.get(before, [])

    monkeypatch.setattr(dzmm, "get_messages", fake_get_messages)
    result = dzmm.get_messages_since(
        CID, "2026-10-07 00:00:00+00", max_count=50,
        until_time="2026-10-08 00:00:00+00")

    assert calls[0] == "2026-10-08 00:00:00+00"
    assert [item["content"]["text"] for item in result] == [
        "昨天更早的消息", "昨天的消息"]


# ---------------------------------------------------------------- ③ 输入更完整

def test_long_messages_are_not_truncated_at_80_chars(tea):
    """原来别人的话砍到 80 字、自己的砍到 100 字 —— 屋主要"丰富完整"。"""
    long_line = "今天下午在茶水间聊到公司年会的事，" * 8      # 120+ 字
    text, _n = RC._recap_transcript([_m("u1", long_line)], CID)
    assert long_line[:110] in text, "长消息不该被砍到 80 字：{}".format(text[:160])
    assert "…" not in text


def test_a_single_huge_paste_is_capped(tea):
    """但单条要封顶（300 字），否则一条长文就把提示词吃光。"""
    text, _n = RC._recap_transcript([_m("u1", "啊" * 1000)], CID)
    assert text.count("啊") == 300


def test_non_text_messages_are_marked_not_dropped(tea):
    """图片/语音/表情原来直接被丢掉（`if not t: continue`）。"""
    msgs = [_m("u1", "", ctype="sticker"), _m("u1", "", ctype="image"),
            _m("u1", "", ctype="audio"), _m("u1", "有字")]
    text, n = RC._recap_transcript(msgs, CID)
    assert "[表情]" in text and "[图片]" in text and "[语音]" in text
    assert n == 4


def test_transcript_has_time_and_day_separators(tea):
    """跨天要能看出"哪句是几点/哪天说的"—— 否则 recap 讲不出先后。"""
    msgs = [_m("u1", "昨天这句", sent_at="2026-09-28 03:00:00+00"),
            _m("u1", "今天这句", sent_at="2026-09-29 03:00:00+00")]
    text, _n = RC._recap_transcript(msgs, CID)
    days = [ln for ln in text.split("\n") if ln.startswith("— ")]
    assert len(days) == 2, text
    assert all("[" in ln for ln in text.split("\n") if not ln.startswith("— "))
    assert "] 紫苑: " in text


def test_recap_output_budget_was_raised(monkeypatch):
    """输入变全了、格式也改成多行了，输出也要有地方写（400 → 800 → **1200**）。"""
    seen = {}

    def spy(_sp, _uc, _key=None, max_tokens=None, **kw):
        seen["max_tokens"] = max_tokens
        return "x"

    monkeypatch.setattr(brains, "_call_llm", spy)
    brains.generate_recap("转录", "紫苑", "最近 3 小时", "无")
    assert seen["max_tokens"] == 1200


# ---------------------------------------------------------------- ⑤ 内容更丰富

def test_recap_prompt_asks_for_multi_line_sections():
    """屋主 2026-09-29："内容要再丰富完善一些，有些单薄"。

    旧提示词硬性要求"只输出 3 行、每行 35/25/20 字" —— 满打满算 80~140 字，
    内容必然单薄。现在改成三段、段内每条一行（每段 2~3 条）。
    """
    import brains
    p = brains.RECAP_PROMPT
    assert "只输出 **3 行**" not in p, "旧的'只输出三行'必须撤掉"
    assert "每段 2~3 条" in p
    assert "· <一条一个要点" in p
    assert "总共 6~9 行" in p
    # 每段要写什么，要具体到"谁和谁、结论或分歧"
    assert "谁和谁、聊了什么、聊出什么结论或分歧" in p
    assert "带上他的原话" in p


def test_recap_parser_keeps_every_line_of_a_section(monkeypatch):
    """解析器原来每段只留**最后一行**（`body[k] = s`）—— 多写也被丢掉。"""
    raw = ("💬 大家在聊：\n"
           "· 糯糯和橘先生在接龙唱山歌，越唱越起劲\n"
           "· 珍妮接了一句花歌，把场子接住了\n"
           "🎭 群里的事：\n"
           "· 09:20 投票结果出来了，09:30 演《公园》\n"
           "· 黑瞳进群了\n"
           "📌 等你回：\n"
           "· 破坏菌早上喊了你一声「紫苑姐」\n")
    body = RC._parse_recap_sections(raw)
    assert len(body["💬"]) == 2 and len(body["🎭"]) == 2 and len(body["📌"]) == 1
    assert body["💬"][0].startswith("糯糯和橘先生")
    assert "大家在聊" not in " ".join(body["💬"]), "标题行不能被当成内容"


def test_recap_parser_accepts_inline_and_dash_styles():
    """模型可能写成「💬 大家在聊：糯糯在唱歌」或 `- xxx`，都要认。"""
    raw = ("💬 大家在聊：糯糯在唱歌\n"
           "- 橘先生接了下一句\n"
           "🎭 群里的事: 黑瞳进群了\n")
    body = RC._parse_recap_sections(raw)
    assert body["💬"] == ["糯糯在唱歌", "橘先生接了下一句"]
    assert body["🎭"] == ["黑瞳进群了"]


def test_recap_line_cap_fits_the_rich_format():
    """屋主现场："📌 等你回" 整段消失 —— 真凶是收尾那句 `lines[:10]`。

    新格式正常输出 2 + 3×(1 标题 + 2~3 条) = 11~14 行，旧上限 10 会把最后一段切掉。
    """
    src = (ROOT / "runtime" / "recap.py").read_text(encoding="utf-8")
    ret = [ln.strip() for ln in src.splitlines()
           if ln.strip().startswith('return "\\n".join(lines[')]
    assert ret, "找不到返回处"
    assert "lines[:10]" not in ret[0], "旧的 10 行上限必须撤掉：{}".format(ret[0])
    cap = int(ret[0].split("lines[:")[1].split("]")[0])
    assert cap >= 20, "上限要放得下 头2行 + 3段×(1标题+5条)：当前 {}".format(cap)


def test_recap_never_drops_the_waiting_section():
    """模型漏写「📌 等你回」时，代码要用已知事实补一条（实测 2026-09-29 就漏过）。

    补的内容只能来自 `hits`（谁提到过他），不许让模型编 —— 所以这段逻辑必须在
    `recap_absent` 里，且引用 `hits`/`who`/`sample`。
    """
    src = (ROOT / "runtime" / "recap.py").read_text(encoding="utf-8")
    i = src.index("📌 那段**不准缺席**")
    seg = src[i:i + 600]
    assert 'body["📌"]' in seg
    assert "hits" in seg and "没人等你，安心潜水吧" in seg


def test_recap_prompt_says_all_three_sections_are_mandatory():
    import brains
    assert "三段都必须写" in brains.RECAP_PROMPT or "每段都要写" in brains.RECAP_PROMPT


def test_recap_assembles_multi_line_sections():
    """拼装：补回固定标题 + `· ` 前缀，段内多条都要出现。"""
    src = (ROOT / "runtime" / "recap.py").read_text(encoding="utf-8")
    assert "_parse_recap_sections(raw)" in src, "recap_absent 要用这个解析器"
    assert "_SECTION_TITLE" in src
    assert '"{} {}："' in src, "要按段补标题"
    assert 'lines.append("· " + r' in src, "要按条补 `· ` 前缀"


# ---------------------------------------------------------------- ④ 斜杠闸门

def test_slash_cmds_is_registered_and_defaults_on():
    f = F.get("slash_cmds")
    assert f.default is True, "默认开＝今天的行为；只在特定群按群关掉"
    assert f.stage == "wired"


def test_scan_loop_consults_the_slash_switch():
    # P3-19 起主循环体在 runtime/mainloop.py（bot 桥 B 前缀，子串兼容）
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    i = src.index("P12-AK：这个群不接")
    seg = src[i:i + 500]
    assert '_feat_on(cid, "slash_cmds")' in seg, "关掉的群里 /xxx 必须完全不理"
    assert "continue" in seg


def test_tea_room_is_not_in_any_group_config_by_default():
    """这个群是**新接进来的**：代码里不该有它的痕迹（配置说了算）。"""
    for rel in ("bot.py", "runtime/ctx.py", "runtime/recap.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert CID not in src, "{} 里不该硬编码顶层茶水间的 cid".format(rel)
