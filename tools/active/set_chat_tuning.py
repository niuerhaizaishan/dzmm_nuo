"""给上班群配 P12-BD 的四个"更像人"参数（装饰概率 / 排版 / 长度口径）。

用法（容器内）：
    python tools/set_chat_tuning.py                 # dry-run，只看会改什么
    python tools/set_chat_tuning.py --apply
    python tools/set_chat_tuning.py --apply --cid-prefix f4ec4c21
    python tools/set_chat_tuning.py --reset --apply  # 回到代码默认（撤回这条改动）

与 `enable_quote_reply.py` 同一套路：走 `config_store.save()` —— 先校验（不合法
一个字都不写）→ 备份到 `config/config_backups/` → 原子替换 → reload。
config.json 的 mtime 一变，bot 主循环 1 秒内热加载，**不用重启**。

⚠️ 前置条件：这四项参数是在 P12-BD 里**新登记**的（`runtime/features.py`）。
参数登记表是 `import` 时建的，**热加载不会重建它** —— 所以必须先用
`deploy.ps1 -Action deploy`（build + up）把新代码带进容器，再跑这个脚本；
否则 `set_overrides()` 会以"未知参数 key"拒绝保存（这是刻意的：宁可在保存时
报错，也不要让一个不生效的参数静默上线）。

为什么是这四个值（依据见 docs/P12-BD-若瑾更像人-方案.md §1 的量表）：
  · 上班群若瑾 15 条 vs 真人 515 条：中位长度 116 vs 7 字、空行分段 93% vs 7%、
    ❤️ 80% vs 1%、派活收尾 67% vs 2%。
  · `decor_prob=0.3`（三条里一条带 ❤️）、`bang_prob=0.5`（一半把句号换成"！！"）：
    装饰是屋主 P12-AH 点名要的**不关**，只降密度；
  · `humanize=1`：空行折成单换行、最多 5 行（只动版式，不改字）；
  · `frame_length`：把收尾块里那句"2~6 句、100~300 字起步"换成闲聊口径
    （整行替换，省得两个口径互相打架）。
"""
import argparse
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from admin import dashboard                                   # noqa: E402
from runtime import config_store                              # noqa: E402

DEFAULT_PREFIX = "f4ec4c21"          # 上班如上坟，下班如还魂（若瑾）

# 收尾块【写多长】那一行的**替换值**（前缀由 `_apply_frame_length` 补）。
LENGTH_LINE = (
    "长度照真人来：**一两句就够**，20~60 字、最多 4 行；他认真问你事情"
    "（在忙什么、这个该怎么办）才多写两句。**别凑字数**，别把一句话写成小作文，"
    "也别列条目、别分小标题、别写「首先/其次」。"
)

VALUES = {
    "chat.decor_prob": 0.3,
    "chat.bang_prob": 0.5,
    "chat.humanize": 1,
    "chat.frame_length": LENGTH_LINE,
    # P12-BQ：同一个人连着 @ 她不必条条秒回（真人不会）；90 秒够她把话说完
    "chat.reply_cooldown": 90,
    # P12-BQ e：收尾在"递话"时重写一次（+1~2s，只在命中时）
    "chat.solicit_guard": 1,
    # P12-BE：她只读"我和正在跟我说话的那个人"的记录，不再对全群一一品评
    "reply.ctx_mode": 1,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="配置上班群的 P12-BD 参数（更像人）")
    ap.add_argument("--apply", action="store_true", help="不传就只 dry-run")
    ap.add_argument("--reset", action="store_true", help="把这四项清掉 = 回到代码默认")
    ap.add_argument("--cid-prefix", default=DEFAULT_PREFIX)
    a = ap.parse_args()

    defaults = dashboard._config_defaults()
    old = dashboard._raw_from_store(defaults)
    new = json.loads(json.dumps(old, ensure_ascii=False))

    cid, changed = "", []
    for g in new.get("groups") or []:
        if not str(g.get("cid") or "").startswith(a.cid_prefix):
            continue
        cid = g["cid"]
        tune = g.setdefault("tuning", {})
        for k, v in VALUES.items():
            if a.reset:
                if k in tune:
                    del tune[k]
                    changed.append(k)
            elif tune.get(k) != v:
                tune[k] = v
                changed.append(k)
        if not tune:            # 全清空了就别留一个空 dict（配置更干净）
            g.pop("tuning", None)

    print("目标群：", cid or "（没找到，检查 --cid-prefix）")
    for k in sorted(VALUES):
        print("  {} → {}".format(k, "（清掉，用默认）" if a.reset else VALUES[k]))
    print("要改的项：", changed or "（已经是这个值，无需改动）")
    if not cid or not changed:
        return 1 if not cid else 0
    if not a.apply:
        print("dry-run：没有写文件（要真的写加 --apply）")
        return 0

    cfg = config_store.save(new, defaults)          # 校验 + 备份 + 原子写 + reload
    tm = cfg.tuning_map()
    print("已写入。生效值：")
    ok = True
    for k in sorted(VALUES):
        got = tm.get("{}|{}".format(cid, k))
        want = None if a.reset else VALUES[k]
        good = (got == want) or (a.reset and got is None)
        ok = ok and good
        print("  {} = {!r}  {}".format(k, got, "OK" if good else "**不对**"))
    print("config.json mtime 已更新，bot 主循环会在 1 秒内热加载。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
