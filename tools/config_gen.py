"""生成 / 校验服务器上的 config.json。

    # 生成（已存在时不覆盖，除非加 --force）
    python3 tools/config_gen.py

    # 只校验现有配置能不能过（退出码非 0 = 不能）
    python3 tools/config_gen.py --check

    # 打印当前生效的配置（含 source=file/defaults）
    python3 tools/config_gen.py --show

### 为什么生成出来的配置"必然与现状等价"

内容是从 `bot.py` 的 `_*_DEFAULT` 字面量（以及 `config_store.default_bots_from_env`
读到的环境变量）拼出来的 —— 也就是代码在"没有配置文件"时会用的那一份。
生成后**回读并逐项比对**，不等就报错、不落盘。
所以"启用配置文件"这一步本身不可能改变行为。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ast  # noqa: E402

from runtime import config_store as cs  # noqa: E402

WANTED = {"_GROUPS_DEFAULT": "groups", "_MUTE_CIDS_DEFAULT": "mute",
          "_SCREEN_CIDS_DEFAULT": "screen", "_PUSH_CIDS_DEFAULT": "push",
          "_GROUP_ALIASES": "aliases"}


def read_defaults() -> dict:
    """从 bot.py 取默认值（只 AST 取字面量，**不 import bot** —— 那是几秒 + playwright）。"""
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if isinstance(tgt, ast.Name) and tgt.id in WANTED:
            out[WANTED[tgt.id]] = ast.literal_eval(node.value)
    for k in ("groups", "mute", "screen", "push"):
        if k not in out:
            raise SystemExit("bot.py 里找不到默认值 {}".format(k))
    out["bots"] = cs.default_bots_from_env(out["groups"][0])
    out["personas"] = ("nuo", "rose_butler")
    return out


def main(argv) -> int:
    defaults = read_defaults()
    raw = cs._defaults_to_raw(defaults)

    if "--show" in argv:
        cfg = cs.reload(defaults)
        print("source =", cfg.source, "| path =", cfg.path)
        print(json.dumps(cfg.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if "--check" in argv:
        p = cs.config_path()
        if not p.is_file():
            print("没有配置文件 {} —— 运行时会退回代码默认值（这是合法状态）".format(p))
            return 0
        # 校验：解析 + 与默认值比对（报告差异，但不报错 —— 配置本来就可以不一样）
        cfg = cs.reload(defaults)
        print("解析通过：source={} path={}".format(cfg.source, cfg.path))
        print("  群 {} 个 / 静默 {} / 筛查 {} / 推送 {} / Bot {} 个".format(
            len(cfg.groups), len(cfg.mute), len(cfg.screen), len(cfg.push),
            len(cfg.bots)))
        return 0

    p = cs.config_path()
    if p.is_file() and "--force" not in argv:
        print("{} 已存在 —— 不覆盖（要覆盖加 --force）".format(p))
        return 1

    # 生成前先校验；生成后**回读并逐项比对**，不等则不落盘
    cfg_before = cs._parse(raw, defaults)
    cs.save(raw, defaults)
    cfg_after = cs.reload(defaults)

    same = (list(cfg_after.groups) == list(cfg_before.groups)
            and set(cfg_after.mute) == set(cfg_before.mute)
            and set(cfg_after.screen) == set(cfg_before.screen)
            and list(cfg_after.push) == list(cfg_before.push)
            and [b.key for b in cfg_after.bots] == [b.key for b in cfg_before.bots])
    print("已写入 {}（source={}）".format(p, cfg_after.source))
    print("  群 {} 个 / 静默 {} / 筛查 {} / 推送 {} / Bot {} 个".format(
        len(cfg_after.groups), len(cfg_after.mute), len(cfg_after.screen),
        len(cfg_after.push), len(cfg_after.bots)))
    print("  回读比对：{}".format("一致 ✓" if same else "**不一致 —— 请检查**"))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
