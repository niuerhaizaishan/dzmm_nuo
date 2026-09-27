"""给上班群打开「调教」（走 config_store.save：校验 → 备份 → 原子写 → reload）。

用法（容器内）：
    python tools/enable_diaojiao.py --dry-run    # 只看会改什么
    python tools/enable_diaojiao.py --apply

为什么不用手改 JSON：`config_store.save()` 会**先校验**（不合法一个字都不写）、
自动备份到 `config/backups/config-*.json`、原子替换，然后 reload。
改完 config.json 的 mtime 一变，bot 主循环 1 秒内自动热加载（不用重启）。
"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from admin import dashboard                                   # noqa: E402
from runtime import config_store                              # noqa: E402

CID_PREFIX = "f4ec4c21"          # 上班如上坟，下班如还魂
KEYS = ("diaojiao", "diaojiao_idle")


def main() -> None:
    dry = "--apply" not in sys.argv
    defaults = dashboard._config_defaults()
    old = dashboard._raw_from_store(defaults)
    new = json.loads(json.dumps(old, ensure_ascii=False))

    changed = []
    cid = ""
    for g in new.get("groups") or []:
        if str(g.get("cid") or "").startswith(CID_PREFIX):
            cid = g["cid"]
            feats = g.setdefault("features", {})
            for k in KEYS:
                if feats.get(k) is not True:
                    feats[k] = True
                    changed.append(k)

    print("目标群：", cid or "（没找到！）")
    print("要改的开关：", changed or "（已经开着，无需改动）")
    if not cid or not changed:
        return
    if dry:
        print("dry-run：没有写文件")
        return

    cfg = config_store.save(new, defaults)      # 校验 + 备份 + 原子写 + reload
    fm = cfg.feature_map()
    print("已写入。生效值：", {k: fm.get("{}|{}".format(cid, k)) for k in KEYS})
    print("config.json mtime 已更新，bot 主循环会在 1 秒内自动热加载。")


if __name__ == "__main__":
    main()
