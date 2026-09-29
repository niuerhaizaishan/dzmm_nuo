"""P12-AL 验收：小小糯**按性别称呼**、温柔只给女生（真调模型，不发送）。

屋主要求："不要叫谁都叫姐姐，根据性别叫。只不过对女生温柔一点。"

做法：以顶层茶水间的人设（`nuo` = 小小糯）跑两批同样的问句，
对话里分别标「（男生）」「（女生）」—— 那就是 @ 问答时喂给模型的东西
（`bot.py` 会把 `_gender_label()` 的结果拼进上下文）。

判据：
  · 男生那条：**不许出现"姐姐"**；也不许出现黏人/色气动作（抱抱/亲亲/摸摸/蹭/小内内/贴贴）
  · 女生那条：允许"姐姐"，也应该更软更黏（允许上面的动作）
  · 未知那条：不许硬叫"姐姐"

用法（容器内）：
    python /app/verify_gender_address.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402
from runtime import config_store, ctx, tuning as T             # noqa: E402
from admin import dashboard                                    # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"      # 顶层茶水间（人设 nuo = 小小糯）
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"     # 认证群（同一个"小小糯"）

QS = ["你好呀", "今天怎么这么晚才回我", "我心情不太好", "你怎么看我这个人"]
CLINGY = ("抱抱", "亲亲", "摸摸", "蹭", "小内内", "贴贴", "舔", "湿", "♡")


def _ask(cid, sender, q):
    out = brains.generate_reply([{"sender": sender, "text": q}],
                                max_tokens=int(T.get("reply.max_tokens", cid)),
                                context_msgs=20)
    return (out or "").strip()


def _show(tag, sender, out):
    flags = []
    if "姐姐" in out:
        flags.append("叫姐姐")
    hit = [w for w in CLINGY if w in out]
    if hit:
        flags.append("黏人/色气:" + "/".join(hit))
    print("  【{}】{} → {}".format(tag, sender, "、".join(flags) or "干净"))
    print("     " + out.replace("\n", "⏎")[:170])
    return flags


def main():
    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    P.set_content_overrides(cfg.persona_texts())
    P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values()
                           if s.persona})
    ctx.set_active_cid(TEA)
    P.set_cid(TEA)
    print("人设 =", P.persona_key_for(TEA), "| 提示词里含性别规则:",
          "先看性别再决定怎么叫" in brains.CMD_PROMPT)
    print()

    bad = []
    for q in QS:
        print("问：" + q)
        m = _ask(TEA, "临屿（男生）", q)
        f = _show("男生", "临屿（男生）", m)
        if "叫姐姐" in f:
            bad.append(("男生叫姐姐", q, m[:60]))
        if any(x.startswith("黏人") for x in f):
            bad.append(("男生被黏", q, m[:60]))
        f2 = _ask(TEA, "紫苑（女生）", q)
        _show("女生", "紫苑（女生）", f2)
        f3 = _ask(TEA, "某人（未知）", q)
        f3r = _show("未知", "某人（未知）", f3)
        if "叫姐姐" in f3r:
            bad.append(("未知硬叫姐姐", q, f3[:60]))
        print()

    print("=" * 66)
    print("结论：", "✅ 男/未知都不叫姐姐、也不黏" if not bad
          else "❌ 有 {} 处不符（下面列前 5 条）".format(len(bad)))
    for t, q, s in bad[:5]:
        print("   - [{}] {} → {}".format(t, q, s))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
