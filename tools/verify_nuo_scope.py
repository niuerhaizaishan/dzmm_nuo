"""P12-AF 线上自检：认证群小小糯的「长度 / 知识 / 连续性」现在与若瑾同档，且人设没被改。

真调模型。用法（容器内）：
    python /app/verify_nuo_scope.py --tag before     # 推新提示词**之前**跑一遍
    python /app/verify_nuo_scope.py --tag after      # 推之后再跑，同一批问题

看四件事：
  1. **长度**：实质问题是不是还能答到 100 字以上（原先示例全是 1-2 句，会把她带短）；
  2. **知识**：有没有拿出自己的观点/判断，而不是"这个我不太清楚"打太极；
  3. **人设没动**：还是软糯姐姐口吻（"姐姐"/"～"/自称小小糯）；
  4. **若瑾不受影响**：同一问题给她，不许出现"小小糯/～"那套糯味。
"""
import argparse
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402
from runtime import config_store, ctx                          # noqa: E402
from runtime import tuning as T                                # noqa: E402

AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"    # 认证妹子群（小小糯）
RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"  # 上班群（若瑾）

# 刻意**不用**新示例里那道题（"人为什么要上班"）—— 免得变成自己考自己
QS = [
    ("观点题", "你怎么看异地恋啊，我有点撑不住了"),
    ("知识题", "我最近老是失眠，有什么办法吗"),
]

DEFLECT = ("这个我不太清楚", "我不太清楚", "我没有数据", "无法回答", "我不知道",
           "说不准", "帮不了")


def deflects(t: str) -> str:
    head = t.strip()[:24]
    for s in DEFLECT:
        if head.startswith(s):
            return "开头就推掉：" + s
    if len(t) < 60 and any(s in t for s in DEFLECT):
        return "很短且只有不知道"
    return ""


def ask(cid, q, label):
    ctx.set_active_cid(cid)
    P.set_cid(cid)
    t0 = time.time()
    out = (brains.generate_reply([{"sender": "姐姐（女生）", "text": q}],
                                 max_tokens=int(T.get("reply.max_tokens", cid)),
                                 context_msgs=int(T.get("reply.context_msgs", cid)))
           or "").strip()
    print("  【{}】{} 字 / {} 秒".format(label, len(out), round(time.time() - t0, 1)))
    print("    问：", q)
    print("    答：", out.replace("\n", "⏎")[:400])
    print("    打太极：", deflects(out) or "无")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    from admin import dashboard
    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    P.set_content_overrides(cfg.persona_texts())
    P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values()
                           if s.persona})

    print("=" * 70)
    print("tag = {} | CMD_PROMPT = {} 字 | 长示例 = {}".format(
        a.tag or "(未标)", len(brains.CMD_PROMPT),
        "有" if "不代表回答长度" in brains.CMD_PROMPT else "无"))
    print("=" * 70)

    print()
    print("一、认证群 / 小小糯（正文长度、知识、人设）")
    outs = []
    for label, q in QS:
        outs.append((label, ask(AUTH, q, label)))

    print()
    print("二、上班群 / 若瑾（同一道观点题，确认没被糯味带偏）")
    r = ask(RUOJIN, QS[0][1], "观点题")

    print()
    print("三、认证群的**上下文连续性**（P12-AD 那套滚动记录，认证群也吃到了吗）")
    import bot                                                 # noqa: E402
    ctx.set_active_cid(AUTH)
    P.set_cid(AUTH)
    cid = AUTH
    bot._TURNS.pop(cid, None)
    bot._TURN_SEEN.pop(cid, None)
    q1 = "我上周养了只猫，叫团子"
    bot._remember_turn(cid, "姐姐", q1, "c1")
    c1 = bot._recent_turns(cid, 20, exclude_mid="c1")
    c1.append({"sender": "姐姐（女生）", "text": q1})
    a1 = brains.generate_reply(c1, max_tokens=int(T.get("reply.max_tokens", cid)),
                               context_msgs=20)
    bot._remember_turn(cid, "我", a1)
    print("  第 1 轮  姐姐：", q1)
    print("          小小糯：", a1.replace("\n", "⏎")[:160])

    q2 = "它今天不理我，我有点难过"
    bot._remember_turn(cid, "姐姐", q2, "c2")
    c2 = bot._recent_turns(cid, 20, exclude_mid="c2")
    c2.append({"sender": "姐姐（女生）", "text": q2})
    a2 = brains.generate_reply(c2, max_tokens=int(T.get("reply.max_tokens", cid)),
                               context_msgs=20)
    print("  第 2 轮  姐姐：", q2)
    print("          小小糯：", a2.replace("\n", "⏎")[:200])
    knows = ("团子" in a2) or ("猫" in a2)
    print("  第 2 轮记不记得'团子/猫'（只有带上下文才答得对）：", knows)
    print("  上下文里有没有她自己上一句：",
          any(t["text"] == a1 for t in c2) or "（上一句在 ctx 里）")
    print("  断片特征句：", [s for s in ("我不太确定你在回应哪句话", "那话我还没说",
                                       "说两遍") if s in a2] or "无")

    print()
    print("=" * 70)
    print("结论")
    print("=" * 70)
    nuo_long = all(100 <= len(o) <= 400 for _l, o in outs)
    nuo_no_deflect = all(not deflects(o) for _l, o in outs)
    nuo_voice = all(("～" in o or "姐姐" in o or "小小糯" in o) for _l, o in outs)
    ruojin_clean = ("小小糯" not in r) and ("～" not in r)
    print("  认证群两问都在 100-400 字     ：", nuo_long, [len(o) for _l, o in outs])
    print("  认证群没有打太极              ：", nuo_no_deflect)
    print("  认证群人设没被动（软糯口吻）  ：", nuo_voice)
    print("  认证群第二轮接得上上一轮      ：", knows)
    print("  若瑾没被糯味带偏              ：", ruojin_clean)
    ok = nuo_long and nuo_no_deflect and nuo_voice and ruojin_clean and knows
    print()
    print("=> ", "✅ 全部达标" if ok else "❌ 有项没达标（看上面逐条）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
