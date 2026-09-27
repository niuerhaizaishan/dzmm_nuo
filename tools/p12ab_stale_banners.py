# -*- coding: utf-8 -*-
"""P12-AB：给体检点名的过期文档加"已过期"横幅（幂等）。

屋主/后来人翻文档时最怕"照着过期文档办事"。这里在标题下插一段横幅，
写清**具体哪里过期**，并指向体检报告与代码。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

BANNER = (
    "\n> ⚠️ **本文档已过期（2026-09-27 全面体检标记）**：{why}\n"
    "> 只作历史资料参考；当前事实以代码与 `docs/全面体检报告-2026-09-27.md` 为准。\n"
    "> 体检时逐条核对过，过期点如上。\n"
)

# 文件名 -> 具体过期点
STALE = {
    "小小糯总方案.md":
        "文中的规模数字已漂移（称 bot.py 3255 行 / runtime 30 个模块，"
        "实测 4380 行 / 40 个模块），且同一文档里「25 项功能」与「~40 项功能」自相矛盾。",
    "功能与人设配置方案.md":
        "头部写「一行代码都还没动」，正文却记着 P12-A/C/E/G 已完成；"
        "文中 schema 为 v3，代码已是 v4；测试条数（17/29/19/25）与实测（20/30/11/28）不一致。",
    "盘点-群友功能.md":
        "行号引用已失效（例：文中指向 bot.py 的「回忆」入口，实际已搬到 "
        "runtime/memory.py:195）。",
    "保全清单.md":
        "依据的 dzmm_cloud/ 目录已不存在；表格里的验收项 0 个勾选，测试条数也与现状不符。",
    "BotAPI实施计划.md":
        "基线目录 dzmm_cloud/ 已删除，文中引用的 context_store.py 不存在，"
        "5 个待办全部未更新（项目实际已跑到 P12 并上线）。",
    "BotAPI能力矩阵.md":
        "状态仍写「Q1/Q2/Q3 待 webhook 接入」，而 Webhook 接收端早已上线运行。",
    "剧情游戏-方案A.md":
        "写「待实现」，实际 /剧情 已实现并可用（见 runtime/story.py）。",
}
# 这些文档仍然有效，只在前几行加了"当前版本"指针，不算过期
CURRENT_NOTE = {
    "P12-I-按群并行.md": "当前有效；P12-AA 起落盘改成了逐群写（见 P12-AA 记录）。",
}


def main() -> None:
    check = "--check" in sys.argv
    done = 0
    for name, why in STALE.items():
        p = DOCS / name
        if not p.exists():
            print("!! 找不到 " + name)
            continue
        txt = p.read_text(encoding="utf-8")
        if "本文档已过期" in txt:
            print("已标记过，跳过：", name)
            continue
        lines = txt.splitlines(keepends=True)
        if not lines or not lines[0].startswith("#"):
            print("!! 标题行不认识，跳过：", name)
            continue
        out = lines[0] + BANNER.format(why=why) + "".join(lines[1:])
        if not check:
            p.write_text(out, encoding="utf-8")
        done += 1
        print("+" if check else "√", name)
    print("（--check：未写入）" if check else "已标记 {} 篇".format(done))


if __name__ == "__main__":
    main()
