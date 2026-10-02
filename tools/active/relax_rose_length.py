"""放宽玫瑰房人设的篇幅口径（线上那份 **config.json 覆盖**才是生效的）。

背景：`ROSE_BUTLER_EXTRA` 是代码默认值，但线上 config.json 里
`personas.rose_butler.extra` 有一份**覆盖**（P12-E），覆盖优先 ⇒ 只改代码没用。
2026-09-27（P12-W）：平台已无"10 行截断"（超限消息自动走官方 Bot），
所以把"4～8 句 / 分行不超过 10 行"改成"4～10 句 / 分行不超过 20 行"。

用法：
    python tools/relax_rose_length.py --dry-run
    python tools/relax_rose_length.py --apply
"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import personas as P                                          # noqa: E402
from admin import dashboard                                   # noqa: E402
from runtime import config_store                              # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
OLD = "通常写 **4～8 句（约 200～400 字）**，分行不超过 10 行。"
NEW = ("通常写 **4～10 句（约 200～500 字）**，分行不超过 20 行。\n"
       "（长消息现在会**自动走官方 Bot 通道**，没有\"10 行就被砍\"的老限制了 ——"
       " 2026-09-27 实测：66 行的排行消息照发。）")


def main() -> None:
    dry = "--apply" not in sys.argv
    defaults = dashboard._config_defaults()
    raw = dashboard._raw_from_store(defaults)
    rb = ((raw.get("personas") or {}).get("rose_butler") or {})
    extra = rb.get("extra")
    if not extra:
        print("config.json 里没有 personas.rose_butler.extra 覆盖 —— "
              "那生效的就是代码默认值（已改），无需处理")
        return
    if OLD not in extra:
        print("没找到要替换的那句，当前篇幅段：")
        i = extra.find("篇幅")
        print("  " + extra[i:i + 160].replace("\n", "⏎"))
        print("（可能已经改过；不做任何写入）")
        return
    new_extra = extra.replace(OLD, NEW)
    print("替换：长度 {} → {}".format(len(extra), len(new_extra)))
    if dry:
        print("dry-run：没有写文件")
        return
    raw["personas"]["rose_butler"]["extra"] = new_extra
    config_store.save(raw, defaults)              # 校验 + 备份 + 原子写
    print("已写入 config.json；约 1 秒后热加载")
    # 复核：重新读一遍配置 + 注入，看**生效值**
    cfg = config_store.reload(defaults)
    P.set_content_overrides(cfg.persona_texts())
    eff = P.persona_for(ROSE)["extra"]
    print("生效值：4～10 句 =", "4～10 句" in eff, " / 20 行 =", "20 行" in eff,
          " / 旧口径残留 =", OLD in eff)


if __name__ == "__main__":
    main()
