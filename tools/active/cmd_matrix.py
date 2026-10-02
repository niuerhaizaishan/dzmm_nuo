"""生成「命令 × 判定函数」命中矩阵。

用途：
  1. 作为保全清单的客观依据（哪些命令存在、归哪个功能）
  2. 暴露前缀重叠 / fall-through —— 这些是重构最容易静默改坏的地方

只读，不改任何状态。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import bot  # noqa: E402

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

CMDS = [
    "/帮助", "/说明", "/自我介绍", "/吐槽开", "/吐槽关", "/同步",
    "/注册", "/查 清歌", "/骚度排行", "/算命", "/运势", "/占卜",
    "/发骚", "/发骚 糯糯 天台", "/发骚结束", "/体位骰", "/场景骰",
    "/羞辱 糯糯", "/骂 糯糯", "/diss 糯糯", "/损 糯糯", "/吐槽 糯糯",
    "/调教 糯糯 跪下", "/命令 跪下", "/调教结束",
    "/公演开始", "/公演结束", "/同意", "/退回", "/忽略",
    "/投票 A | B", "/投票结束", "/选 1", "/1",
    "/剧情开始 糯糯 天台", "/剧情", "/剧情结束",
    "/继续", "/重写", "/结束", "/停止",
    "/诗 糯糯", "/写诗", "/周运", "/趋势", "/生日",
    "/记住 我喜欢芒果", "/记忆", "/我不在的时候群里发生了什么",
    "/吹牛骰子", "/加入", "/牌局", "/看骰", "/开始", "/下一轮", "/开", "/退出",
]

# 「不是命令」的哨兵值：实测结果 —— **只有 is_profile_cmd 用空字符串表示"不是"**
# （其 docstring 亦写"空串表示不是"）；其余判定一律 None/False。
# 而 is_register_cmd / is_rewrite_cmd 的空字符串表示"是命令，但无载荷"。
# ⇒ 空串语义在判定之间不统一，这是重构陷阱，不能盲目统一成 str|None。
EMPTY_MEANS_NOT_MATCH = {"is_profile_cmd"}


def matches(pred: str, r) -> bool:
    if r is None or r is False:
        return False
    if r == "" and pred in EMPTY_MEANS_NOT_MATCH:
        return False
    return True


def payload(r):
    if isinstance(r, bool):
        return r
    if isinstance(r, str):
        return repr(r) + ("(空载荷)" if r == "" else "")
    return repr(r)


hits = {p: [] for p in PREDICATES}
for cmd in CMDS:
    for p in PREDICATES:
        fn = getattr(bot, p, None)
        if fn is None:
            continue
        try:
            r = fn(cmd)
        except Exception as e:
            r = "EXC:{}".format(type(e).__name__)
        if matches(p, r):
            hits[p].append((cmd, r))

print("=== 每条命令命中的判定 ===")
for cmd in CMDS:
    row = [p for p in PREDICATES if any(c == cmd for c, _ in hits[p])]
    print("{:<26} -> {}".format(cmd, ", ".join(row) if row else "（无判定命中）"))

print()
print("=== 重叠告警：同一条命令命中多个『开始类/结束类』判定 ===")
STARTISH = ("is_fasao_cmd", "is_scene_cmd", "is_insult_cmd", "is_diaojiao_cmd",
            "is_perf_start_cmd", "is_vote_start_cmd", "is_story_start_cmd",
            "is_fortune_cmd", "is_poem_cmd", "is_devil_cmd")
ENDISH = ("is_fasao_end_cmd", "is_dj_end_cmd", "is_perf_end_cmd",
          "is_vote_end_cmd", "is_story_end_cmd", "_is_stop_word")
for cmd in CMDS:
    s = [p for p in STARTISH if any(c == cmd for c, _ in hits[p])]
    e = [p for p in ENDISH if any(c == cmd for c, _ in hits[p])]
    if s and e:
        print("  ⚠ {:<22} 开始类={}  结束类={}".format(cmd, s, e))
    elif len(s) > 1:
        print("  ⚠ {:<22} 多个开始类={}".format(cmd, s))

print()
print("=== 返回值不是布尔的判定（带解析结果）===")
for p in PREDICATES:
    vals = [(c, r) for c, r in hits[p] if not isinstance(r, bool)]
    if vals:
        print("  {}: {}".format(p, vals[:8]))
