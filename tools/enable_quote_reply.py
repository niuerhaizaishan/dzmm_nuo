"""给指定群打开「别人引用回复我也接话」（P12-AE）。

用法（容器内）：
    python tools/enable_quote_reply.py                 # dry-run，只看会改什么
    python tools/enable_quote_reply.py --apply
    python tools/enable_quote_reply.py --apply --cid-prefix f4ec4c21

与 `enable_diaojiao.py` 同一套路：走 `config_store.save()` —— 先校验（不合法
一个字都不写）→ 备份到 `config/config_backups/` → 原子替换 → reload。
config.json 的 mtime 一变，bot 主循环 1 秒内热加载，**不用重启**。

为什么默认关、要按群打开：这是一条**新增的触入口**。今天的行为是"只有 @ 才答"，
默认打开等于在所有群悄悄多出一种回答时机（玫瑰房那套"谁能触发管家"的房间策略
也会被绕过）。所以只在明确要的群上开。
"""
import argparse
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from admin import dashboard                                   # noqa: E402
from runtime import config_store                              # noqa: E402

KEY = "quote_reply"
DEFAULT_PREFIX = "f4ec4c21"          # 上班如上坟，下班如还魂


def main() -> int:
    ap = argparse.ArgumentParser(description="给某个群打开「引用回复也接话」")
    ap.add_argument("--apply", action="store_true", help="不传就只 dry-run")
    ap.add_argument("--cid-prefix", default=DEFAULT_PREFIX)
    a = ap.parse_args()

    defaults = dashboard._config_defaults()
    old = dashboard._raw_from_store(defaults)
    new = json.loads(json.dumps(old, ensure_ascii=False))

    cid, changed = "", []
    for g in new.get("groups") or []:
        if str(g.get("cid") or "").startswith(a.cid_prefix):
            cid = g["cid"]
            feats = g.setdefault("features", {})
            if feats.get(KEY) is not True:
                feats[KEY] = True
                changed.append(KEY)

    print("目标群：", cid or "（没找到，检查 --cid-prefix）")
    print("要改的开关：", changed or "（已经开着，无需改动）")
    if not cid or not changed:
        return 1 if not cid else 0
    if not a.apply:
        print("dry-run：没有写文件（要真的写加 --apply）")
        return 0

    cfg = config_store.save(new, defaults)          # 校验 + 备份 + 原子写 + reload
    fm = cfg.feature_map()
    got = fm.get("{}|{}".format(cid, KEY))
    print("已写入。生效值：{} = {}".format(KEY, got))
    print("config.json mtime 已更新，bot 主循环会在 1 秒内自动热加载。")
    return 0 if got is True else 1


if __name__ == "__main__":
    sys.exit(main())
