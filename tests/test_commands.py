"""命令识别与分派矩阵（特征化测试 / characterization tests）。

这些测试**记录现状行为**，不是判断对错。目的是：P3/P4/P8 重构时，
任何判定逻辑或命令归属的变化都会立刻失败，并指向 `docs/保全清单.md` 第四节。

生成依据：`tools/cmd_matrix.py`。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from runtime import identity  # noqa: E402

PREDICATES = [
    "is_help_cmd", "is_guide_cmd", "is_intro_cmd", "is_devil_cmd", "is_sync_cmd",
    "is_register_cmd", "is_profile_cmd", "is_rank_cmd", "is_summary_cmd",
    "is_recall_cmd", "is_remember_cmd",
    "is_fasao_cmd", "is_scene_cmd", "is_fasao_end_cmd",
    "is_insult_cmd", "is_continue_cmd", "is_rewrite_cmd", "_is_stop_word",
    "is_diaojiao_cmd", "is_dj_command_cmd", "is_dj_end_cmd",
    "is_perf_start_cmd", "is_perf_end_cmd", "is_review_cmd",
    "is_vote_start_cmd", "is_vote_end_cmd", "is_vote_choice_cmd",
    "is_story_start_cmd", "is_story_end_cmd",
    "is_fortune_cmd", "is_poem_cmd", "is_recap_cmd",
    "is_trend_cmd", "is_trend_board_cmd",
]


# 实测：只有 is_profile_cmd 用空串表示"不是命令"；其余判定用 None/False。
# 而 is_register_cmd / is_rewrite_cmd 的空串表示"是命令但无载荷"。
# 空串语义不统一 —— 这是重构陷阱，见 docs/保全清单.md 第四节。
EMPTY_MEANS_NOT_MATCH = {"is_profile_cmd"}


def hits(cmd: str) -> set[str]:
    """该命令命中的判定集合（按各判定自身的哨兵约定）。"""
    out = set()
    for p in PREDICATES:
        fn = getattr(bot, p, None)
        if fn is None:
            continue
        try:
            r = fn(cmd)
        except Exception:                       # noqa: BLE001
            continue
        if r is None or r is False:
            continue
        if r == "" and p in EMPTY_MEANS_NOT_MATCH:
            continue
        out.add(p)
    return out


# ---------------------------------------------------------------- 基本识别

@pytest.mark.parametrize("cmd,pred", [
    ("/帮助", "is_help_cmd"),
    ("/菜单", "is_help_cmd"),
    ("/说明", "is_guide_cmd"),
    ("/指南", "is_guide_cmd"),
    ("/自我介绍", "is_intro_cmd"),
    ("/whoami", "is_intro_cmd"),
    ("/同步", "is_sync_cmd"),
    ("/注册", "is_register_cmd"),
    ("/查 清歌", "is_profile_cmd"),
    ("/资料 清歌", "is_profile_cmd"),
    ("/骚度排行", "is_rank_cmd"),
    ("/算命", "is_fortune_cmd"),
    ("/运势", "is_fortune_cmd"),
    ("/占卜", "is_fortune_cmd"),
    ("/发骚", "is_fasao_cmd"),
    ("/发情点名", "is_fasao_cmd"),
    ("/体位骰", "is_scene_cmd"),
    ("/羞辱 糯糯", "is_insult_cmd"),
    ("/diss 糯糯", "is_insult_cmd"),
    ("/调教 糯糯 跪下", "is_diaojiao_cmd"),
    ("/公演开始", "is_perf_start_cmd"),
    ("/公演结束", "is_perf_end_cmd"),
    ("/投票 A | B", "is_vote_start_cmd"),
    ("/选 1", "is_vote_choice_cmd"),
    ("/剧情开始 糯糯 天台", "is_story_start_cmd"),
    ("/剧情", "is_story_start_cmd"),
    ("/剧情结束", "is_story_end_cmd"),
    ("/继续", "is_continue_cmd"),
    ("/记住 我喜欢芒果", "is_remember_cmd"),
    ("/记忆", "is_recall_cmd"),
    ("/周运", "is_trend_cmd"),
    ("/吹牛骰子", "is_dj_command_cmd"),
])
def test_command_is_recognized(cmd, pred):
    assert pred in hits(cmd), f"{cmd} 未命中 {pred}（实际 {sorted(hits(cmd))}）"


# ---------------------------------------------------------------- 载荷解析

def test_profile_payload():
    assert bot.is_profile_cmd("/查 清歌") == "清歌"
    assert bot.is_profile_cmd("/whois 清歌") == "清歌"


def test_devil_on_off_payload():
    assert bot.is_devil_cmd("/吐槽开") == "on"
    assert bot.is_devil_cmd("/吐槽关") == "off"


def test_dj_command_payload():
    assert bot.is_dj_command_cmd("/命令 跪下") == "跪下"


def test_review_payload():
    assert bot.is_review_cmd("/同意") == "approve"
    assert bot.is_review_cmd("/退回") == "reject"
    assert bot.is_review_cmd("/忽略") == "ignore"


def test_vote_choice_payload():
    assert bot.is_vote_choice_cmd("/选 1") == "1"
    assert bot.is_vote_choice_cmd("/1") == "1"


def test_remember_payload():
    assert bot.is_remember_cmd("/记住 我喜欢芒果") == "我喜欢芒果"


def test_trend_returns_days():
    assert bot.is_trend_cmd("/周运") == 7


def test_recognized_but_empty_payload():
    """识别到了但没有载荷——下游必须自行处理空串。

    保全清单第四节记录：旧版矩阵脚本把空串误判成"未命中"。
    """
    assert bot.is_register_cmd("/注册") == ""
    assert bot.is_rewrite_cmd("/重写") == ""


# ---------------------------------------------------------------- 停用词

@pytest.mark.parametrize("word", ["/停止", "/停下", "/结束", "/终止"])
def test_stop_words(word):
    assert bot._is_stop_word(word) is True


def test_non_stop_word():
    assert bot._is_stop_word("/发骚") is False


# ---------------------------------------------------------------- 高危重叠（特征化）

@pytest.mark.parametrize("cmd,expected", [
    # 结束词同时命中"开始类"，靠 dispatch 顺序 + active 标志兜住。
    # 注意 _is_stop_word 是"去掉前导 / 后前缀匹配 停止/停下/结束/终止"，
    # 所以 /结束 命中它，而 /投票结束、/调教结束 **不**命中。
    ("/投票结束", {"is_vote_start_cmd", "is_vote_end_cmd"}),
    ("/发骚结束", {"is_fasao_cmd", "is_scene_cmd", "is_fasao_end_cmd"}),
    ("/调教结束", {"is_diaojiao_cmd", "is_dj_end_cmd"}),
    ("/吐槽开", {"is_devil_cmd", "is_insult_cmd"}),
    ("/吐槽关", {"is_devil_cmd", "is_insult_cmd"}),
    ("/发骚", {"is_fasao_cmd", "is_scene_cmd"}),
    ("/记忆", {"is_recall_cmd", "is_remember_cmd"}),
])
def test_known_overlaps_are_unchanged(cmd, expected):
    """若这里失败：说明命令归属变了，**先回 `docs/保全清单.md` 第四节登记**，
    再确认 dispatch 顺序是否需要同步调整。"""
    assert hits(cmd) == expected, f"{cmd} 命中集合变了: {sorted(hits(cmd))}"


def test_bare_slash_command_is_dj_catchall():
    """`is_dj_command_cmd` 是兜底捕获：任何裸命令都命中。

    ⇒ 它**只能**在调教进行中被 `_check_diaojiao` 使用，
      绝不能提到主分派前面，否则会吞掉所有命令。
    """
    for cmd in ("/诗 糯糯", "/周运", "/生日", "/吹牛骰子", "/开", "/退出"):
        assert bot.is_dj_command_cmd(cmd) is not None, cmd


def test_stop_words_hit_all_end_predicates():
    """`/结束` 命中所有状态机的结束判定 —— 顺序 + active 标志决定先结束谁。"""
    got = hits("/结束")
    for p in ("is_fasao_end_cmd", "is_dj_end_cmd", "is_perf_end_cmd",
              "is_vote_end_cmd", "is_story_end_cmd"):
        assert p in got, f"/结束 未命中 {p}"


# ---------------------------------------------------------------- 入群判定（P3/P4 必修）

def test_join_msg_matches_human_wording_only():
    """现状：只认"加入了群聊"。Bot 入群是"已加入群聊"——**差一个"了"字，侥幸不匹配**。

    这条测试固定当前行为；P3/P4 要改成按 content.type / isBot 判定，
    届时**同步更新此测试**（不要直接删）。
    """
    human = {"content": {"text": "某某 加入了群聊"}}
    bot_join = {"content": {"type": "system", "text": "机器人 小小糯 已加入群聊"}}
    assert bot._is_join_msg(human) is True
    assert bot._is_join_msg(bot_join) is False      # ← 靠措辞巧合


def test_detect_joins_excludes_bot_when_configured(monkeypatch):
    """**P4.2 已修**：配置了 BOT_UID 后，Bot 的入群消息不再被当成新成员。

    修复前 `_detect_joins` 只按 NUO_UID 排除，Bot id 不同 → 排除不掉。
    """
    bot_id = "eee88c3d-af91-4ecc-96df-d87eb9a358db"
    # 读口是 runtime.identity（joins.detect_joins → is_self → self_uids）；
    # bot.BOT_UID 只是壳的 re-export，patch 它影响不到判定 —— 本地过只是
    # 因为真机环境恰好把这个 uid 认进了 _BOT_UIDS（2026-10-02 CI 揭穿）。
    monkeypatch.setattr(identity, "BOT_UID", bot_id)
    pending = [
        {"sent_by": bot.NUO_UID, "content": {"text": "小小糯 加入了群聊"}},
        {"sent_by": bot_id, "content": {"text": "机器人 小小糯 加入了群聊"}},
        {"sent_by": "real-user-uid", "content": {"text": "真人 加入了群聊"}},
    ]
    joins = bot._detect_joins(pending)
    assert "real-user-uid" in joins
    assert bot.NUO_UID not in joins
    assert bot_id not in joins          # ← P4.2 修复点


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
