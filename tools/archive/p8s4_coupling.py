"""P8 第 4 步：把"哪些东西真的能拆出去"量出来。

拆一个模块的代价由三个数决定（不是由"它看起来像一个功能"决定）：

  fan_in   组外有多少个函数调用了组内   -> **bot.py 里要改多少处**
  fan_out  组内调用了多少个组外函数     -> 新模块要回指 bot.py 多少次
  全局     组内引用了哪些模块级全局     -> 需要注入还是可以当常量搬走

fan_in 高 = 拆出去会到处留"回指"，等于只是把文字搬了个位置（不产生解耦）。
真正值得拆的是 fan_in 低、且组内互相调用密集的**孤岛**。
"""
import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BOT = ROOT / "bot.py"

# 候选组：按"前缀/精确名"明确列，不靠关键词模糊匹配
# （关键词匹配踩过坑：`history` 含 `story`，把一堆历史函数算进了剧情机）
GROUPS = {
    "dice(骰子局)": None,          # 用前缀
    "vote+story(投票/剧情)": [
        "_get_vote_state", "_set_vote_state", "is_vote_start_cmd", "is_vote_end_cmd",
        "is_vote_choice_cmd", "_extract_vote_title", "_vote_members_text",
        "start_vote", "_record_vote", "_finish_vote", "_check_vote",
        "_record_story_vote", "_story_vote_result",
        "_get_story_state", "_set_story_state", "is_story_start_cmd",
        "is_story_end_cmd", "start_story", "_check_story", "_finish_story",
        "_advance_story",
    ],
    "perf(公演)": None,
    "fasao+scene(发骚/场景)": [
        "is_fasao_cmd", "fasao", "is_scene_cmd", "_parse_scene_target", "scene",
        "_get_fasao_state", "_set_fasao_state", "is_fasao_end_cmd",
        "_check_fasao_timeout", "end_fasao",
    ],
    "diaojiao(调教)": [
        "_get_dj_state", "_set_dj_state", "is_dj_command_cmd", "is_dj_end_cmd",
        "_dj_target_text", "_dj_end_text", "is_diaojiao_cmd", "start_diaojiao",
        "_check_diaojiao",
    ],
    "sign(星座)": None,
    "recap(缺勤复盘)": None,
    "trend(周趋势)": None,
    "fortune(运势)": None,
    "poem(藏头诗)": None,
}

PREFIX = {
    "dice(骰子局)": ("_dice_", "_dice_state", "_dice_save"),
    "perf(公演)": ("_perf_", "is_perf_", "_get_perf_", "_detect_perf_query",
                   "summarize_person_perf", "_handle_perf_", "_check_perf_interact"),
    "sign(星座)": ("_sign_", "_room_owner", "_is_room_trigger", "_is_flower_grab"),
    "recap(缺勤复盘)": ("_recap_", "is_recap_cmd", "recap_absent", "_day0",
                        "_msg_ts", "_fmt_ts"),
    "trend(周趋势)": ("_trend_", "is_trend_", "_wd_cn", "_md_cn",
                      "week_trend", "week_trend_board"),
    "fortune(运势)": ("_fortune_", "is_fortune_cmd", "fortune", "_char_wx",
                      "_name_wx", "_ganzhi", "_relation", "_clean_name",
                      "_get_fortune_history"),
    "poem(藏头诗)": ("_poem_", "is_poem_cmd", "poem", "_do_poem", "_norm_poem",
                     "_record_poem", "_get_poem_history", "_parse_poem_style"),
}


def main() -> int:
    src = BOT.read_text(encoding="utf-8")
    tree = ast.parse(src)

    globals_assigned = set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    globals_assigned.add(n.id)

    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def refs(node):
        local = set()
        for a in (node.args.args + node.args.kwonlyargs + node.args.posonlyargs):
            local.add(a.arg)
        if node.args.vararg:
            local.add(node.args.vararg.arg)
        if node.args.kwarg:
            local.add(node.args.kwarg.arg)
        calls = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                local.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)) and n is not node:
                local.add(n.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                local.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for al in n.names:
                    local.add((al.asname or al.name).split(".")[0])
            elif isinstance(n, ast.comprehension) and isinstance(n.target, ast.Name):
                local.add(n.target.id)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                calls.add(n.func.id)
        g = {n.id for n in ast.walk(node)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
             and n.id not in local}
        return g, calls

    data = {name: refs(node) for name, node in funcs.items()}

    # 解析每个组的成员
    members = {}
    for gname, explicit in GROUPS.items():
        if explicit is not None:
            members[gname] = sorted(n for n in explicit if n in funcs)
            continue
        pre = PREFIX[gname]
        members[gname] = sorted(n for n in funcs
                                if any(n == p or n.startswith(p) for p in pre))

    # 调用图（模块级函数之间）
    callers = defaultdict(set)
    for name, (_, calls) in data.items():
        for c in calls:
            if c in funcs:
                callers[c].add(name)

    print("=" * 96)
    print("候选组拆分代价（fan_in 低 + fan_out 低 = 真孤岛）")
    print("=" * 96)
    print("{:24s} {:>4s} {:>6s} {:>8s} {:>7s}  {}".format(
        "组", "函数", "行数", "fan_in", "fan_out", "引用的模块级全局"))
    print("-" * 96)

    rows = []
    for gname, mem in sorted(members.items(), key=lambda kv: -len(kv[1])):
        if not mem:
            continue
        mset = set(mem)
        lines = sum((funcs[n].end_lineno or funcs[n].lineno) - funcs[n].lineno + 1
                    for n in mem)
        fin = set()
        fout = set()
        gset = set()
        for n in mem:
            for other, (_, calls) in data.items():
                if other in mset:
                    continue
                if n in calls:
                    fin.add(other)
            for c in data[n][1]:
                if c in funcs and c not in mset:
                    fout.add(c)
            gset |= (data[n][0] & globals_assigned)
        rows.append((gname, len(mem), lines, len(fin), len(fout), sorted(gset)))
        print("{:24s} {:>4d} {:>6d} {:>8d} {:>7d}  {}".format(
            gname, len(mem), lines, len(fin), len(fout),
            ", ".join(sorted(gset)[:6]) + (" ..." if len(gset) > 6 else "")))

    print()
    print("=" * 96)
    print("每组被谁调用（fan_in 明细）—— 这些就是拆出去后 bot.py 里必须改的地方")
    print("=" * 96)
    for gname, mem in sorted(members.items(), key=lambda kv: -len(kv[1])):
        if not mem:
            continue
        mset = set(mem)
        fin = defaultdict(list)
        for n in mem:
            for other, (_, calls) in data.items():
                if other not in mset and n in calls:
                    fin[other].append(n)
        print()
        print("--- {} : {} 个外部调用方 ---".format(gname, len(fin)))
        for other in sorted(fin):
            print("    {:28s} -> {}".format(other, ", ".join(sorted(fin[other]))))

    print()
    print("=" * 96)
    print("每组的 fan_out 明细（新模块要回指 bot.py 的具体函数 = 拆分成本）")
    print("=" * 96)
    for gname, mem in sorted(members.items(), key=lambda kv: -len(kv[1])):
        if not mem:
            continue
        mset = set(mem)
        fout = set()
        gset = set()
        for n in mem:
            fout |= (data[n][1] & set(funcs)) - mset
            gset |= (data[n][0] & globals_assigned)
        print()
        print("--- {} : {} 个组外函数, {} 个全局 ---".format(
            gname, len(fout), len(gset)))
        print("    组外函数: {}".format(", ".join(sorted(fout)) or "（无！完全自包含）"))
        print("    全局    : {}".format(", ".join(sorted(gset)) or "（无）"))

    # main_loop 引用面：它是分发中枢，单独看
    print("=" * 96)
    print("main_loop 的引用面（分发中枢；改这里 = 真风险）")
    print("=" * 96)
    ml = data["main_loop"]
    called = sorted(c for c in ml[1] if c in funcs)
    print("main_loop 调用了 {} 个模块级函数".format(len(called)))
    print("  其中属于上述各组的：")
    for gname, mem in sorted(members.items(), key=lambda kv: -len(kv[1])):
        inside = sorted(set(called) & set(mem))
        if inside:
            print("    {:24s} {} 个: {}".format(gname, len(inside), ", ".join(inside)))
    outside = sorted(set(called) - {n for m in members.values() for n in m})
    print("  组外 {} 个: {}".format(len(outside), ", ".join(outside[:30])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
