# -*- coding: utf-8 -*-
"""P12-BA 验收：运势卡的称呼**按性别**（真调模型，**不发送**）。

屋主 2026-09-30："还有，运势你也不能逮谁叫谁姐姐呀，要看性别的呀"。

这个探针在容器里对着**部署后的代码**跑真生成：挑茶水间里几位**已知性别**的成员
（男/女）+ 一位**性别未知**的，逐个打印：

    · `_gender_label_of()` 认出来的性别（女生/男生/未知）
    · 运势卡最后那句点评（模型真写的）
    · 那句里到底叫了「姐姐」还是「哥」还是只叫名字

⚠️ 只生成、只打印，**一条都不发**；顺手把 `_record_fortune` 摘成 no-op，
免得污染本群的运势历史（趋势榜要用它）。

用法（容器内）：`python /app/probe_fortune_gender.py`
"""
import sys

sys.path.insert(0, "/app")

import dzmm                                                    # noqa: E402
from runtime import ctx                                        # noqa: E402
from runtime import fortune as FT                              # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"      # 顶层茶水间（男的女的都有）
NAMES = ("苏白", "G_百戏", "彻", "囡囡", "御褚", "紫苑")


def main():
    FT._record_fortune = lambda *a, **kw: None          # 不写历史（探针不留副作用）
    ctx.set_active_cid(TEA)
    members = dzmm.get_members(TEA) or {}
    print("茶水间成员表拿到 {} 人".format(len(members)))
    print()
    for nm in NAMES:
        uid = next((u for u, n in members.items() if nm in (n or "")), "")
        if not uid:
            print("—— {}：成员表里没找到，跳过".format(nm))
            continue
        full = members[uid]
        glabel = FT._gender_label_of(uid, TEA)
        card = FT.fortune(uid, TEA)
        tail = (card or "").split("\n")[-1]
        calls = "姐姐" in tail, ("哥" in tail)
        # 判定要**对着认出来的性别**看：女生叫姐姐是对的，男生/未知叫姐姐才是 bug。
        if glabel == "女生":
            verdict = "✅ 女生叫姐姐（应当）" if calls[0] else "⚠️ 女生却叫了哥/名字"
        elif glabel == "男生":
            verdict = "❌ 男生被叫了姐姐" if calls[0] else "✅ 男生叫哥（应当）"
        else:
            verdict = "❌ 未知性别却叫了姐姐" if calls[0] else "✅ 未知性别只叫名字（应当）"
        print("—— {}（{}…）".format(full[:18], uid[:8]))
        print("   识别性别 = {}".format(glabel))
        print("   点评 = {}".format(tail[:120]))
        print("   判定 = {}（姐姐={} 哥={}）".format(verdict, calls[0], calls[1]))
        print()
    ctx.reset_active_cid()
    return 0


if __name__ == "__main__":
    sys.exit(main())
