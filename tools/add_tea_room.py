"""把「顶层茶水间」接进来（P12-AK）：只开 @ 问答 + 缺勤回顾，其余功能全关。

屋主 2026-09-29 定的口径：
  · 这个群**只用「小小糯～」账号**发（不绑 Bot —— 平台侧那个 Bot 也不在群里）；
  · **忽略打卡 / 摸鱼躲藏 / 记忆考核**内容（recap 的输入过滤，按群配）；
  · 窗口默认值先短一点（3 小时）；输入内容要丰富完整（代码侧已改）。

为什么这个工具必须**在部署完新代码之后**跑：它要写 `slash_cmds`（新功能）与
`recap.ignore_kw` / `recap.default_hours`（新参数），而 `config_store.save()`
会**先校验**"键必须在册" —— 容器里还是旧代码时会直接拒掉。

为什么功能要显式关一大片：`_feat_on` 默认**全开**，群一接进来，如果不管，
她会在 779 人的茶水间里主动发早安/午间记忆汇报/排行推送/入群欢迎。

用法（容器内）：
    python /app/add_tea_room.py              # dry-run，打印会改什么
    python /app/add_tea_room.py --apply
"""
import argparse
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

from admin import dashboard                                   # noqa: E402
from runtime import config_store                              # noqa: E402
from runtime import features as F                             # noqa: E402

CID = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"   # 认证群（recap 原本只有它）
ALIAS = "顶层茶水间"
KEEP = ("chat", "recap", "memory_remember", "memory_recall", "at_only")
# ⚠️ `at_only`（P12-AL，屋主："只有 @ 小小糯 的时候才触发回复"）**只在这个群开**：
#    认证群/上班群的骰子、发骚、排行、问候本来都靠非 @ 消息触发，那边一开等于全废。
IGNORE_KW = "打卡,摸鱼,躲藏,躲猫猫,巡查,记忆考核,摸鱼币,机选号码"
TUNING = {"recap.default_hours": 3, "recap.ignore_kw": IGNORE_KW}


def main() -> int:
    ap = argparse.ArgumentParser(description="接顶层茶水间（只开 @ 问答 + recap）")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    defaults = dashboard._config_defaults()
    old = dashboard._raw_from_store(defaults)
    new = json.loads(json.dumps(old, ensure_ascii=False))

    groups = new.setdefault("groups", [])
    g = next((x for x in groups if str(x.get("cid")) == CID), None)
    created = g is None
    if created:
        g = {"cid": CID}
        groups.append(g)
    g.update({"cid": CID, "alias": ALIAS, "enabled": True, "persona": "nuo",
              "mute": False, "screen": False, "push": False})

    off = [k for k in F.keys() if k not in KEEP]
    g["features"] = {k: (k in KEEP) for k in sorted(F.keys())}
    g["tuning"] = dict(TUNING)

    # recap 的按群集合是**覆盖**语义：原来只有认证群，这里必须两项都写
    scopes = new.setdefault("scopes", {})
    scopes["recap"] = [AUTH, CID]

    print("目标群：", CID, "(新建)" if created else "(已存在，覆盖)")
    print("别名：", ALIAS, "| persona: nuo（小小糯）| screen/push: 关 | Bot: 不绑（走账号）")
    print("开：", ", ".join(KEEP))
    print("关：{} 项 → {}".format(len(off), ", ".join(off[:12]) + " …"))
    print("scopes.recap =", scopes["recap"])
    print("tuning =", g["tuning"])
    if not a.apply:
        print()
        print("dry-run：没有写文件（要真的写加 --apply）")
        return 0

    cfg = config_store.save(new, defaults)      # 校验 + 备份 + 原子写 + reload
    fm = cfg.feature_map()
    print()
    print("已写入。抽样确认：")
    for k in ("chat", "recap", "slash_cmds", "greet_morning", "perf"):
        print("  {:<14s} = {}".format(k, fm.get("{}|{}".format(CID, k))))
    print("  scopes.recap   =", cfg.scopes.get("recap"))
    print("  tuning(本群)    =", {k: cfg.tuning_map().get("{}|{}".format(CID, k))
                                  for k in TUNING})
    print()
    print("⚠️ groups 不热加载：**必须重启容器**才生效（scopes/tuning 已热加载）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
