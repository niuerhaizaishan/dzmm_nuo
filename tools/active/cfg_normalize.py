"""把配置文件按**当前 schema 规范化**重写一遍（走校验 + 备份 + 原子写）。

用途：老版本升级留下的空节、字段顺序、缩进风格不一致 —— 一次抹平。
它不会改变任何**语义**：读 → `as_dict()` → `save()`，
所以"写完再读回来"必须与写之前逐字段等价（顺序与空节除外）。

用法（容器内，见 docs/小小糯总方案.md 的 P11 运维方式）：

    python tools/cfg_normalize.py --check     # 只看有没有需要规范的
    python tools/cfg_normalize.py             # 规范化（先自动备份）
"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")


def main(argv):
    dry = "--check" in argv
    import dashboard as dash
    from runtime import config_store as cs

    defaults = dash._config_defaults()
    if defaults.get("error"):
        raise SystemExit("读 bot.py 默认值失败：" + str(defaults["error"]))
    p = cs.config_path()
    if not p.is_file():
        raise SystemExit("没有配置文件：{}".format(p))

    before_text = p.read_text(encoding="utf-8")
    before = json.loads(before_text)
    cfg = cs.load(defaults, force=True)
    want = cfg.as_dict()
    want_text = json.dumps(want, ensure_ascii=False, indent=2) + "\n"

    if before_text == want_text:
        print("已经规范，无需改动。")
        return 0

    # 先证明"语义等价"再写：除了 version/空节/顺序，内容必须一致
    b_groups = {g["cid"]: g for g in before.get("groups") or []}
    w_groups = {g["cid"]: g for g in want.get("groups") or []}
    if b_groups != w_groups or (before.get("bots") or []) != (want.get("bots") or []):
        print("!! 规范化会改变内容，已中止（请人工检查）：")
        print("   群数 {} -> {}，Bot 数 {} -> {}".format(
            len(b_groups), len(w_groups),
            len(before.get("bots") or []), len(want.get("bots") or [])))
        return 1
    dropped = [k for k in before if k not in want]
    print("将做的改动：")
    print("  版本 {} -> {}".format(before.get("version"), want.get("version")))
    if dropped:
        print("  去掉空节/多余键：{}".format(", ".join(dropped)))
    if dry:
        print("（--check：没有写入）")
        return 0

    cs.save(want, defaults)
    back = json.loads(cs.config_path().read_text(encoding="utf-8"))
    print("已写入 {}（已自动备份）".format(cs.config_path()))
    print("回读：version={} 群={} Bot={} 键={}".format(
        back.get("version"), len(back.get("groups") or []),
        len(back.get("bots") or []), sorted(back.keys())))
    print("✓ 回读与预期一致" if back == json.loads(want_text) else "!! 回读不一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
