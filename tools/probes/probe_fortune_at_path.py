# -*- coding: utf-8 -*-
"""验证茶水间 @ 那条路（A2 自然语言）**现在通了** —— 对着部署后的代码跑，不发送。

事故：`bot.py` 的 @ 分支用了 `is_fortune_ask` / `fortune_reply` 两个名字，
但 P12-AW 时忘了把它们加进 `from runtime.fortune import …` ⇒ 茶水间里**任何 @**
都会 `NameError` → 回滚 last_id → 那条 @ 永远没人答（屋主 10:22:42："@小小糯～ 给我卜一卦"）。

这个探针把那条分支的每一步都走一遍：

    _strip_at → is_fortune_ask → _feat_on(本群,"fortune") → fortune(...) 出整张卡

⚠️ 用 `fortune()`（不带当天缓存）而不是 `fortune_reply()` —— 免得把探针这张卡
写进本群缓存，屋主之后再问就只能拿到它了。

用法（容器内）：`python /app/probe_fortune_at_path.py`
"""
import sys

sys.path.insert(0, "/app")

import bot                                                     # noqa: E402
import dzmm                                                    # noqa: E402
from runtime import ctx                                        # noqa: E402
from runtime.features import on as _feat_on                    # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
ASK = "@小小糯～ 给我卜一卦"
WHO = "糯糯"                       # 提问的人（成员表里按名字找 uid）


def main():
    ctx.set_active_cid(TEA)
    members = dzmm.get_members(TEA) or {}
    uid = next((u for u, n in members.items() if (n or "").startswith(WHO)), "")
    print("提问人 = {}（{}…）".format(WHO, uid[:8]))

    q = bot._strip_at(ASK, TEA)
    print("① _strip_at          -> {!r}".format(q))
    print("② is_fortune_ask     -> {}".format(bot.is_fortune_ask(q)))
    print("③ 本群 fortune 开着吗 -> {}".format(_feat_on(TEA, "fortune")))
    print("④ callable(fortune)  -> {}".format(callable(getattr(bot, "fortune", None))))
    print("   callable(fortune_reply) -> {}".format(
        callable(getattr(bot, "fortune_reply", None))))
    card = bot.fortune(uid, TEA)
    lines = card.split("\n")
    print("⑤ 生成的卡：{} 字 / {} 行".format(len(card), len(lines)))
    print("   末行点评 = {}".format(lines[-1][:100]))
    print("   一条发得下吗（≤{} 字）→ {}".format(
        bot.ACCOUNT_MSG_MAX_CHARS,
        "会走长连接一条发" if len(card) > bot.ACCOUNT_MSG_MAX_CHARS else "直接打字"))
    ctx.reset_active_cid()
    ok = (bot.is_fortune_ask(q) and _feat_on(TEA, "fortune") and len(lines) >= 8)
    print("\n==> {}".format("✅ @ 那条路通了" if ok else "❌ 还有问题"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
