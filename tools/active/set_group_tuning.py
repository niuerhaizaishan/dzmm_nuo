"""给某个群写一条**按群参数覆盖**（P12-AG）。

用法（容器内）：
    python /app/set_group_tuning.py --cid-prefix bd7fe6a4 --key reply.max_tokens --value 1200
    python /app/set_group_tuning.py ... --apply

与 `enable_quote_reply.py` 同一套路：走 `config_store.save()` —— 先校验
（`Param` 的 lo/hi 会拦住危险值，不合法一个字都不写）→ 备份 → 原子写 → reload。
config.json 的 mtime 一变，bot 主循环 1 秒内热加载，**不用重启**。
"""
import argparse
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from admin import dashboard                                   # noqa: E402
from runtime import config_store                              # noqa: E402
from runtime import tuning as T                               # noqa: E402


def _load():
    """读配置并**灌进 tuning** —— 否则 `T.get()` 返回的是代码默认值，
    dry-run 会显示"原来是 800"，而配置里其实早就写着别的值（2026-09-27 踩过）。"""
    defaults = dashboard._config_defaults()
    raw = dashboard._raw_from_store(defaults)
    cfg = config_store._parse(raw, defaults)
    T.set_overrides(cfg.tuning_map())
    return defaults, raw, cfg


def main() -> int:
    ap = argparse.ArgumentParser(description="写一条按群 tuning 覆盖")
    ap.add_argument("--cid-prefix", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--value", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    try:
        val = int(a.value)
    except ValueError:
        val = a.value

    defaults, old, _cfg0 = _load()
    new = json.loads(json.dumps(old, ensure_ascii=False))

    cid = ""
    for g in new.get("groups") or []:
        if str(g.get("cid") or "").startswith(a.cid_prefix):
            cid = g["cid"]
            g.setdefault("tuning", {})[a.key] = val

    print("目标群：", cid or "（没找到）")
    print("要写：{} = {}（原来是 {}）".format(a.key, val, T.get(a.key, cid) if cid else "-"))
    if not cid:
        return 1
    if not a.apply:
        print("dry-run：没有写文件（要真的写加 --apply）")
        return 0

    cfg = config_store.save(new, defaults)          # 校验（含 Param 边界）+ 备份 + 原子写
    got = cfg.tuning_map().get("{}|{}".format(cid, a.key))
    print("已写入。生效值：{} = {}".format(a.key, got))
    print("config.json mtime 已更新，bot 主循环会在 1 秒内自动热加载。")
    return 0 if str(got) == str(val) else 1


if __name__ == "__main__":
    sys.exit(main())
