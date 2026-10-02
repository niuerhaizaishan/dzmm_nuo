"""修掉切片头部里的**重复常量定义**（一次性）。

起因：写 slice header 时把常量也抄了一份，同时 `consts=[...]` 又让工具从 bot.py
搬了一份进来 —— 同一个常量在模块里定义两次。文本相同时看不出来（后者覆盖前者，
值也对），但 `DJ_PARTICIPATE_HINT` 我抄漏了几个字，被审计的逐字比对抓了出来。

正确做法是**只留搬过来的那份**（它就是原文），把头部那份（**第一次出现**）删掉。
顺带一起删掉紧贴在它上面的注释行 —— 那行注释本来就在讲这个常量。
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# 模块 -> 要删掉头部定义（保留搬过来的那份）的常量名
DUP = {
    "runtime/text.py": ["_OPENCC_T2S"],
    "runtime/members.py": ["HISTORY_MAX"],
    "runtime/group_state.py": ["MEMORY_MAX"],
    "runtime/novel.py": ["_group_name_cache"],
    "runtime/vote.py": ["VOTE_DEFAULT_SECONDS"],
    "runtime/story.py": ["STORY_MAX_ROUND", "STORY_VOTE_SECONDS"],
    "runtime/perf.py": ["OWNER_UID"],
    "runtime/fasao.py": ["FASAO_TIMEOUT", "SCENES", "POSITIONS", "EXTRAS", "XP_BIAS"],
    "runtime/diaojiao.py": ["DJ_PARTICIPATE_HINT"],
}


def module_level_assigns(src: str):
    """{name: [lineno, ...]} —— 只看模块级。"""
    tree = ast.parse(src)
    out = {}
    for n in tree.body:
        names = []
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.append(n.target.id)
        for nm in names:
            out.setdefault(nm, []).append((n.lineno, n.end_lineno))
    return out


def main() -> int:
    total = 0
    for rel, names in sorted(DUP.items()):
        p = ROOT / rel
        raw = p.read_bytes().decode("utf-8")
        eol = "\r\n" if "\r\n" in raw else "\n"
        lines = raw.split(eol)
        assigns = module_level_assigns(raw)
        drop = set()
        for nm in names:
            spans = assigns.get(nm, [])
            if len(spans) < 2:
                print("  !! {}:{} 只出现 {} 次，跳过".format(rel, nm, len(spans)))
                continue
            # 第一次出现 = 头部那份，删掉它
            start, end = spans[0]
            # 往上吃掉紧贴的注释行（讲的就是这个常量）
            i = start - 2
            while i >= 0 and lines[i].lstrip().startswith("#"):
                drop.add(i)
                i -= 1
            for k in range(start - 1, end):
                drop.add(k)
            total += 1
        if not drop:
            continue
        kept = [ln for i, ln in enumerate(lines) if i not in drop]
        # 收掉删出来的 >=3 连续空行
        out = []
        blanks = 0
        for ln in kept:
            if ln.strip() == "":
                blanks += 1
                if blanks > 2:
                    continue
            else:
                blanks = 0
            out.append(ln)
        p.write_bytes(eol.join(out).encode("utf-8"))
        print("  {}  删 {} 处头部重复定义: {}".format(rel, len([n for n in names]),
                                              ", ".join(names)))

    print()
    print("共修 {} 处".format(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
