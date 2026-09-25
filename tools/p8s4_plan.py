"""给一批候选函数算"能不能自包含"：需要哪些模块级名字、哪些拿不到。

用来决定切片边界，不写盘。
"""
import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot.py"

GROUPS = {
    "text": ["_get_opencc", "_normalize_cn", "_simplify", "_strip_parenthesis",
             "_is_stop_word", "_dedupe_options", "_normalize_choice",
             "_extract_deadline_seconds", "_squash", "_clean_name", "_norm_poem"],
    "members": ["_extract_msgs", "_get_msgs_by_uid", "_member_profile_text",
                "_find_in_profiles", "_find_user_in_msgs", "find_user_by_name",
                "_find_uid_by_name", "_gender_label", "_random_member_uid",
                "_archive_text_from_profile"],
    "fortune": ["is_fortune_cmd", "fortune", "_fortune_calc", "_fortune_score",
                "_fortune_level", "_record_fortune", "_get_fortune_history",
                "_char_wx", "_name_wx", "_ganzhi", "_relation"],
    "trend": ["is_trend_cmd", "is_trend_board_cmd", "_trend_days", "_trend_spark",
              "_trend_chong", "_trend_stats", "_wd_cn", "_md_cn", "week_trend",
              "week_trend_board"],
    "recap": ["is_recap_cmd", "_recap_intent", "_recap_window", "_day0", "_msg_ts",
              "_fmt_ts", "_recap_mentions", "_recap_transcript", "recap_absent"],
    "sign": ["_room_owner", "_is_room_trigger", "_is_flower_grab", "_sign_from_md",
             "_sign_intent", "_sign_find", "_sign_parse_birth", "_sign_stored",
             "_sign_remember", "_sign_calc", "_sign_recent", "_sign_card",
             "_sign_reply"],
    "poem": ["is_poem_cmd", "poem", "_do_poem", "_poem_text", "_poem_resolve",
             "_record_poem", "_get_poem_history", "_parse_poem_style"],
    "memory": ["_get_memory", "_remember", "_extract_remember", "is_remember_cmd",
               "is_recall_cmd", "_extract_names", "_extract_keywords", "_fast_rank",
               "_ai_filter", "_retrieve_memory", "_append_memory_ref",
               "_record_history", "_on_rename", "_refresh_profile_latest"],
    "poem+memory": ["is_poem_cmd", "poem", "_do_poem", "_poem_text",
                    "_poem_resolve", "_record_poem", "_get_poem_history",
                    "_parse_poem_style", "_get_memory", "_remember",
                    "_extract_remember", "is_remember_cmd", "is_recall_cmd",
                    "_extract_names", "_extract_keywords", "_fast_rank",
                    "_ai_filter", "_retrieve_memory", "_append_memory_ref",
                    "_record_history", "_on_rename", "_refresh_profile_latest"],
    "vote+story": ["_get_vote_state", "_set_vote_state", "is_vote_start_cmd",
                   "is_vote_end_cmd", "is_vote_choice_cmd", "_extract_vote_title",
                   "_vote_members_text", "start_vote", "_record_vote",
                   "_finish_vote", "_check_vote", "_record_story_vote",
                   "_story_vote_result", "_get_story_state", "_set_story_state",
                   "is_story_start_cmd", "is_story_end_cmd", "start_story",
                   "_check_story", "_finish_story", "_advance_story"],
    "perf": ["_detect_perf_query", "summarize_person_perf", "is_perf_start_cmd",
             "is_perf_end_cmd", "_get_perf_state", "_set_perf_state",
             "_get_perf_content", "_get_perf_content_full", "_perf_heat_target",
             "_perf_m_name", "_perf_send", "_handle_perf_start", "_perf_end_bg",
             "_handle_perf_end", "_check_perf_interact"],
    "fasao+scene": ["is_fasao_cmd", "fasao", "is_scene_cmd", "_parse_scene_target",
                    "scene", "_get_fasao_state", "_set_fasao_state",
                    "is_fasao_end_cmd", "_check_fasao_timeout", "end_fasao"],
    "diaojiao": ["_get_dj_state", "_set_dj_state", "is_dj_end_cmd",
                 "_dj_target_text", "_dj_end_text", "is_diaojiao_cmd",
                 "start_diaojiao", "_check_diaojiao"],
    "heat": ["_add_heat"],
    "last_action": ["_set_last_action", "_get_last_action"],
}


def main() -> int:
    src = BOT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    consts = set()
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            consts.add(n.targets[0].id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            consts.add(n.target.id)

    def needed(node):
        local = set()
        for a in (node.args.args + node.args.kwonlyargs + node.args.posonlyargs):
            local.add(a.arg)
        if node.args.vararg:
            local.add(node.args.vararg.arg)
        if node.args.kwarg:
            local.add(node.args.kwarg.arg)
        for x in ast.walk(node):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                local.add(x.id)
            elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)) and x is not node:
                local.add(x.name)
            elif isinstance(x, ast.ExceptHandler) and x.name:
                local.add(x.name)
            elif isinstance(x, (ast.Import, ast.ImportFrom)):
                for al in x.names:
                    local.add((al.asname or al.name).split(".")[0])
            elif isinstance(x, ast.comprehension) and isinstance(x.target, ast.Name):
                local.add(x.target.id)
        return {x.id for x in ast.walk(node)
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
                and x.id not in local}

    for gname, members in GROUPS.items():
        present = [m for m in members if m in funcs]
        missing = [m for m in members if m not in funcs]
        mset = set(present)
        need_funcs, need_consts = set(), set()
        for m in present:
            for n in needed(funcs[m]):
                if n == m or n in mset:
                    continue
                if n in funcs:
                    need_funcs.add(n)
                elif n in consts:
                    need_consts.add(n)
        lines = sum((funcs[m].end_lineno or funcs[m].lineno) - funcs[m].lineno + 1
                    for m in present)
        print("=" * 78)
        print("{}  ({} 函数 / {} 行)".format(gname, len(present), lines))
        if missing:
            print("  !! bot.py 里没有: {}".format(missing))
        print("  组外函数 ({}):".format(len(need_funcs)))
        for n in sorted(need_funcs):
            print("      {:32s} {:5d}-{:5d}".format(
                n, funcs[n].lineno, funcs[n].end_lineno))
        print("  组外常量 ({}): {}".format(
            len(need_consts), ", ".join(sorted(need_consts)[:14])
            + (" ..." if len(need_consts) > 14 else "")))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
