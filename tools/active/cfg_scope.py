"""按运维决定调整"管哪几个群"：命令行里列出的群 = 启用，其余 = 停用。

### 为什么单独做个工具，不直接改 JSON

`enabled=false` 的群运行期**完全不碰**（不读消息、不回复、不接指令、不筛查、
不推送、不同步档案）—— 这正是"彻底停掉某个群"的手段。

但配置是 bot 的启动前置：**文件不合法 → `bot.py` 直接 SystemExit 拒绝启动**，
线上就是挂掉。手改 JSON 完全可能写出一个"看着对、起不来"的文件。
所以这里走和运维后台**同一条** `config_store.save()`：校验 → 原子替换 → 自动备份；
再补一行审计（后台的审计只由 dashboard 写，命令行改动同样要留痕）。

用法（容器内跑，见 docs/小小糯总方案.md 的 P11 运维方式）：

    python tools/cfg_scope.py --show                    # 只看现状
    python tools/cfg_scope.py <cid> [<cid> ...]         # 只启用这些群，其余停用
    python tools/cfg_scope.py --all                     # 全部启用（回到原样）

注意：**只动 `enabled`**，不动 mute/screen/push。停用的群保留它原来的开关值，
将来重新启用时不用重新配一遍。
"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")


def _load():
    import dashboard as dash
    from runtime import config_store as cs
    defaults = dash._config_defaults()
    if defaults.get("error"):
        raise SystemExit("读 bot.py 默认值失败：" + str(defaults["error"]))
    raw = json.loads(cs.config_path().read_text(encoding="utf-8"))
    cfg = cs.load(defaults)
    return cs, defaults, raw, cfg


def _show(raw, cfg):
    print("配置文件里的群（{} 个，生效 {} 个）：".format(
        len(raw["groups"]), len(cfg.groups)))
    for g in raw["groups"]:
        mark = "✔ 生效" if g.get("enabled", True) else "✘ 停用"
        print("  {} {}  alias={}  静默={} 筛查={} 推送={}".format(
            mark, g["cid"][:8], (g.get("alias") or "")[:20],
            g.get("mute"), g.get("screen"), g.get("push")))
    print("  Bot：{}".format([b["key"] for b in raw.get("bots") or []]))
    print("  生效口径：群={} 静默={} 筛查={} 推送={}".format(
        len(cfg.groups), len(cfg.mute), len(cfg.screen), len(cfg.push)))


def main(argv):
    cs, defaults, raw, cfg = _load()

    if not argv or argv[0] == "--show":
        _show(raw, cfg)
        return 0

    if argv[0] == "--all":
        keep = {g["cid"] for g in raw["groups"]}
    else:
        keep = set(argv)
        known = {g["cid"] for g in raw["groups"]}
        unknown = keep - known
        if unknown:
            raise SystemExit("这些 cid 不在配置里，拒绝执行：{}".format(sorted(unknown)))

    changed = []
    for g in raw["groups"]:
        want = g["cid"] in keep
        if bool(g.get("enabled", True)) != want:
            changed.append("群 {} enabled : {} -> {}".format(
                g["cid"][:8], bool(g.get("enabled", True)), want))
            g["enabled"] = want

    if not changed:
        print("没有变化。")
        _show(raw, cfg)
        return 0

    print("将要做出的改动：")
    for c in changed:
        print("  " + c)

    from admin import config_ui
    old = json.loads(json.dumps(cfg.as_dict(), ensure_ascii=False))
    cs.save(raw, defaults)                     # 校验不过这里会抛，一个字都不写
    config_ui.audit_log(cs.audit_path(), "cli", changed)
    print("\n已写入 {}（已自动备份，已记审计）".format(cs.config_path()))

    # 回读自证：不看写进去的 dict，重新从文件读一遍
    cs._cache = None
    cfg2 = cs.load(defaults, force=True)
    raw2 = json.loads(cs.config_path().read_text(encoding="utf-8"))
    print("回读：")
    _show(raw2, cfg2)
    diff = config_ui.diff_summary(old, raw2)
    print("  与改动前相比：{}".format(diff if diff else "（无）"))
    base = json.loads(json.dumps(raw, ensure_ascii=False))
    if diff != changed:
        print("  !! 回读到的改动与预期不一致，请人工复核")
        return 1
    print("  ✓ 回读一致")
    print("\n**需要重启容器才生效**：cd /opt/dzmm && docker compose up -d --force-recreate dzmm-bot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
