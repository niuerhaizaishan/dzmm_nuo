# -*- coding: utf-8 -*-
"""按配置纪律增改 Bot（P12-H）。

为什么要有这个工具，而不是让人手改 `config.json`：
配置是**服务端状态**，手改容易写出校验不过的东西（cid 不在群列表、两个 Bot 绑同一个群、
token_env 打错一个字 —— 最后一种最阴：配置看着对，运行期却说"token 没解析到"）。
这里一律走 `config_store.save()`：**先校验、再备份、原子写**，与运维后台同一条纪律。

用法（容器里或宿主机上，`DZMM_CONFIG` 指向配置）：

    python tools/cfg_bots.py list
    python tools/cfg_bots.py add --key weisi_rose --name 薇丝-bot --short 薇丝 \
        --cid 4a56be71-... --token-env DZMM_BOT_TOKEN_ROSE [--uid <平台用户id>]
    python tools/cfg_bots.py set-uid --key weisi_rose --uid eee88c3d-...
    python tools/cfg_bots.py enable/disable --key weisi_rose
    python tools/cfg_bots.py rm --key weisi_rose

⚠️ Bot 列表属于**结构性配置**：改完要重启容器才生效（热加载只管功能/参数/人设/提示词）。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "admin"))


def _defaults():
    """给 config_store 校验用的默认值（从 bot.py 的 AST 读，不 import bot）。"""
    import dashboard
    d = dashboard._config_defaults()
    if d.get("error"):
        raise SystemExit("读默认值失败：{}".format(d["error"]))
    return d


def _load():
    from runtime import config_store as cs
    defaults = _defaults()
    cfg = cs.reload(defaults)
    raw = json.loads(json.dumps(cfg.as_dict(), ensure_ascii=False))
    return cs, defaults, raw


def _save(cs, defaults, raw, what):
    try:
        cs.save(raw, defaults)          # 校验不过一个字都不写
    except Exception as e:                                       # noqa: BLE001
        raise SystemExit("保存被拒（原文件未动）：{}".format(e))
    print("OK {}（已备份 + 原子写）".format(what))
    print("   配置文件：{}".format(cs.config_path()))
    print("   ⚠️ Bot 列表是结构性配置：需要重启容器才生效。")


def _find(raw, key):
    for b in raw.get("bots") or []:
        if b.get("key") == key:
            return b
    return None


def cmd_list(args):
    cs, defaults, raw = _load()
    import os
    print("配置文件：{}".format(cs.config_path()))
    print("版本：{}（支持 {}）".format(
        (raw.get("version")), ",".join(str(v) for v in cs.SUPPORTED_VERSIONS)))
    groups = {g.get("cid"): (g.get("alias") or "") for g in raw.get("groups") or []}
    print("群（{}）：".format(len(groups)))
    for cid, alias in groups.items():
        print("   {}  {}".format((cid or "")[:8], alias))
    bots = raw.get("bots") or []
    print("Bot（{}）：".format(len(bots)))
    for b in bots:
        env = b.get("token_env") or ""
        tok = (os.environ.get(env) or "").strip() if env else (b.get("token") or "")
        print("   key={:<12} name={:<10} cid={} enabled={} uid={}".format(
            b.get("key"), b.get("name") or "-", (b.get("cid") or "")[:8],
            b.get("enabled", True), (b.get("uid") or "-")[:8]))
        print("      token_env={} -> token {}".format(
            env or "(内联)", "已解析" if tok else "**没解析到**"))
        print("      绑定群别名：{}".format(groups.get(b.get("cid")) or "**不在群列表里**"))


def cmd_add(args):
    cs, defaults, raw = _load()
    if _find(raw, args.key):
        raise SystemExit("Bot key={} 已存在（要用 set-uid/enable 改它）".format(args.key))
    bot = {"key": args.key, "name": args.name or args.key,
           "short": args.short or args.name or args.key,
           "cid": args.cid, "token_env": args.token_env, "enabled": True}
    if args.uid:
        bot["uid"] = args.uid
    if args.token:
        bot["token"] = args.token
    raw.setdefault("bots", []).append(bot)
    _save(cs, defaults, raw, "已新增 Bot {}（绑 {}）".format(args.key, args.cid[:8]))


def cmd_set_uid(args):
    cs, defaults, raw = _load()
    b = _find(raw, args.key) or sys.exit("没有这个 Bot：{}".format(args.key))
    if args.uid:
        b["uid"] = args.uid
    else:
        b.pop("uid", None)
    _save(cs, defaults, raw, "Bot {} 的 uid = {}".format(
        args.key, args.uid or "（清空，启动时按名字认）"))


def cmd_toggle(args, on):
    cs, defaults, raw = _load()
    b = _find(raw, args.key) or sys.exit("没有这个 Bot：{}".format(args.key))
    b["enabled"] = bool(on)
    _save(cs, defaults, raw, "Bot {} enabled={}".format(args.key, bool(on)))


def cmd_rm(args):
    cs, defaults, raw = _load()
    before = len(raw.get("bots") or [])
    raw["bots"] = [b for b in (raw.get("bots") or []) if b.get("key") != args.key]
    if len(raw["bots"]) == before:
        raise SystemExit("没有这个 Bot：{}".format(args.key))
    _save(cs, defaults, raw, "已删除 Bot {}".format(args.key))


def main(argv=None):
    ap = argparse.ArgumentParser(description="按配置纪律增改 Bot（P12-H）")
    # ⚠️ 不用 `required=True`：宿主机是 python 3.6（argparse 3.7 才支持），
    # 而运维的人很可能直接在服务器上跑这个工具。
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list").set_defaults(fn=cmd_list)

    a = sub.add_parser("add")
    a.add_argument("--key", required=True)
    a.add_argument("--name", default="")
    a.add_argument("--short", default="")
    a.add_argument("--cid", required=True)
    a.add_argument("--token-env", dest="token_env", required=True)
    a.add_argument("--token", default="", help="不推荐：内联 token（密钥会进配置文件）")
    a.add_argument("--uid", default="", help="平台用户 id（可留空，启动时按名字认）")
    a.set_defaults(fn=cmd_add)

    u = sub.add_parser("set-uid")
    u.add_argument("--key", required=True)
    u.add_argument("--uid", default="")
    u.set_defaults(fn=cmd_set_uid)

    for name, on in (("enable", True), ("disable", False)):
        t = sub.add_parser(name)
        t.add_argument("--key", required=True)
        t.set_defaults(fn=lambda a, _on=on: cmd_toggle(a, _on))

    r = sub.add_parser("rm")
    r.add_argument("--key", required=True)
    r.set_defaults(fn=cmd_rm)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
