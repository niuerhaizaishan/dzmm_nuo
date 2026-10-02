# -*- coding: utf-8 -*-
"""按配置纪律改人设（P12-H 起用）。

和 `tools/cfg_bots.py` 同一个道理：配置是**服务端状态**，手改容易写出校验不过的东西。
这里一律走 `config_store.save()`（**先校验、再备份、原子写**），并且人设属于**热配置** ——
写完约 1 秒就生效，**不需要重启容器**（见 runtime/hotload.py）。

最常用的是"改名"：换 Bot 之后，人设里的自称必须跟着换，否则模型会照着
`extra` 里那句"你自称「小小糯」"继续自称旧名字 —— 而 `extra` 的优先级是最高的
（它自己写着"以上全部作废，一律以下面为准"）。所以改名不只是两个字段，
还得把 `extra` 与话术里**指向自己的那几处**一起换掉。

用法：

    python tools/cfg_persona.py show --key rose_butler
    python tools/cfg_persona.py rename --key rose_butler --to 薇丝            # 从「小小糯」改成薇丝
    python tools/cfg_persona.py rename --key rose_butler --to 薇丝 --from 小小糯
    python tools/cfg_persona.py set --key rose_butler --full 薇丝 --short 薇丝

⚠️ 只改**人设文本**（personas 节），不碰群/Bot/功能开关；`--dry-run` 只打印不写。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "admin"))

DEFAULT_FROM = "小小糯"


def _load():
    import dashboard
    from runtime import config_store as cs
    defaults = dashboard._config_defaults()
    if defaults.get("error"):
        raise SystemExit("读默认值失败：{}".format(defaults["error"]))
    cfg = cs.reload(defaults)
    raw = json.loads(json.dumps(cfg.as_dict(), ensure_ascii=False))
    return cs, defaults, raw, cfg


def _spec(key):
    import personas as P
    if key not in P.PERSONAS:
        raise SystemExit("没有这个人设：{}（可用：{}）".format(
            key, ", ".join(sorted(P.PERSONAS))))
    return P


def effective(key, raw, P):
    """这个人设**当前生效**的文本 = 代码默认 + 配置覆盖。"""
    ov = (raw.get("personas") or {}).get(key) or {}
    d = dict(P.PERSONAS[key])
    extra = ov["extra"] if "extra" in ov else (d.get("extra") or "")
    names = {f: (ov[f] if f in ov else (d.get(f) or ""))
             for f in ("name_full", "name_short")}
    ph = dict(P.PHRASES["nuo"])
    ph.update(P.PHRASES.get(key) or {})
    ph.update(ov.get("phrases") or {})
    return names, extra, ph


def build_rename(key, raw, P, to, frm=DEFAULT_FROM):
    """算出"改名"要写进配置的 personas 覆盖（纯函数，好测）。"""
    names, extra, ph = effective(key, raw, P)
    item = {"name_full": to, "name_short": to}
    changes = []
    if names["name_full"] != to:
        changes.append("name_full: {!r} -> {!r}".format(names["name_full"], to))
    if names["name_short"] != to:
        changes.append("name_short: {!r} -> {!r}".format(names["name_short"], to))
    if extra:
        new_extra = extra.replace(frm, to)
        if new_extra != extra:
            item["extra"] = new_extra
            changes.append("extra: 替换 {} 处 {!r} -> {!r}".format(
                extra.count(frm), frm, to))
        elif "extra" in ((raw.get("personas") or {}).get(key) or {}):
            item["extra"] = extra          # 保留既有覆盖
    frags = {}
    for k, v in ph.items():
        if v and frm in v:
            frags[k] = v.replace(frm, to)
    if frags:
        item["phrases"] = frags
        changes.append("话术: {} 条里替换了自称（{}）".format(
            len(frags), ", ".join(sorted(frags))))
    return item, changes


def _apply(cs, defaults, raw, item, key, changes, dry):
    personas = dict(raw.get("personas") or {})
    personas[key] = item
    raw["personas"] = personas
    print("将写入的人设覆盖（{}）：".format(key))
    print(json.dumps(item, ensure_ascii=False, indent=1)[:1200])
    print()
    print("改动摘要：")
    for c in changes or ["（无变化）"]:
        print("   -", c)
    if dry:
        print("\n--dry-run：没有写文件。")
        return
    try:
        cs.save(raw, defaults)             # 校验不过一个字都不写
    except Exception as e:                                     # noqa: BLE001
        raise SystemExit("保存被拒（原文件未动）：{}".format(e))
    print("\nOK 已保存（备份 + 原子写）：{}".format(cs.config_path()))
    print("⚠️ 人设是**热配置**：约 1 秒后自动生效，不用重启容器。")


def cmd_show(args):
    cs, defaults, raw, cfg = _load()
    P = _spec(args.key)
    names, extra, ph = effective(args.key, raw, P)
    print("人设 {}：".format(args.key))
    print("  name_full  = {!r}".format(names["name_full"]))
    print("  name_short = {!r}".format(names["name_short"]))
    print("  extra      = {} 字（其中「{}」出现 {} 次）".format(
        len(extra), args.frm, extra.count(args.frm)))
    hit = {k: v.count(args.frm) for k, v in ph.items() if v and args.frm in v}
    print("  话术里出现「{}」的：{}".format(
        args.frm, hit or "无"))
    print("  配置里现有覆盖：{}".format(
        json.dumps((raw.get("personas") or {}).get(args.key) or {}, ensure_ascii=False)[:200]))


def cmd_rename(args):
    cs, defaults, raw, cfg = _load()
    P = _spec(args.key)
    item, changes = build_rename(args.key, raw, P, args.to, args.frm)
    _apply(cs, defaults, raw, item, args.key, changes, args.dry_run)


def cmd_set(args):
    cs, defaults, raw, cfg = _load()
    P = _spec(args.key)
    names, extra, ph = effective(args.key, raw, P)
    item = {}
    if args.full is not None:
        item["name_full"] = args.full
    if args.short is not None:
        item["name_short"] = args.short
    if not item:
        raise SystemExit("没给要改的字段（--full / --short）")
    changes = ["{}: {!r} -> {!r}".format(k, names[k], v) for k, v in item.items()]
    _apply(cs, defaults, raw, item, args.key, changes, args.dry_run)


def main(argv=None):
    ap = argparse.ArgumentParser(description="按配置纪律改人设（走 config_store.save）")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("show")
    s.add_argument("--key", required=True)
    s.add_argument("--frm", default=DEFAULT_FROM)
    s.set_defaults(fn=cmd_show)

    r = sub.add_parser("rename", help="改自称（含 extra 与话术里指向自己的那几处）")
    r.add_argument("--key", required=True)
    r.add_argument("--to", required=True)
    r.add_argument("--frm", default=DEFAULT_FROM)
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_rename)

    t = sub.add_parser("set")
    t.add_argument("--key", required=True)
    t.add_argument("--full", default=None)
    t.add_argument("--short", default=None)
    t.add_argument("--dry-run", action="store_true")
    t.set_defaults(fn=cmd_set)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
