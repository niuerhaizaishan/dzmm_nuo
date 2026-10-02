"""P12-AD 自检：`@` 对话连不连得上。

屋主 2026-09-27：「你看下上班群，不连贯啊」，实测两句就是：
    @若瑾 我是大傻逼 → "说两遍，是想让我记住，还是想让我接话。"
    @若瑾 你也是   → "我不太确定你在回应哪句话…那话我还没说"

两种跑法：
  A) **不进模型**（本地或容器都能跑）：把那次真实场景分别按"老做法（msgs[-N:]）"
     和"新做法（滚动记录 − 当前这条）"拼出来，直接打印喂给模型的上下文——
     一眼看见老做法的两个毛病：当前问题出现两次、自己上一句不在里面。
  B) **真调模型**（容器里跑）：走一次完整的两轮对话，第二轮用"只有记得上一句
     才答得对"的追问（"你也是"），看它接不接得上。

容器里跑：
  docker exec -d dzmm-bot python /app/tools/verify_p12ad.py > /tmp/v12ad.log 2>&1
  docker exec dzmm-bot tail -60 /tmp/v12ad.log
"""
import os
import sys
import time

for _p in ("/app", "/app/admin", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import brains                                                  # noqa: E402

try:
    import bot                                                 # noqa: E402
except Exception as e:                                         # noqa: BLE001
    print("!! 不能 import bot（容器里应该可以）：" + str(e)[:120])
    raise SystemExit(2)

RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
ASSUME = "临屿"


# ---------------------------------------------------------------------------
# 复核那次真实场景
# ---------------------------------------------------------------------------
# 线上真实顺序（关键：平台**不会**把机器人自己发的话推回来，
# 所以那两轮 `msgs` 里各自只有用户那一句）：
ROUND1 = [("临屿", "我是大傻逼", "m1")]                     # 第 1 轮 @
ROUND2 = [("临屿", "你也是", "m3")]                         # 第 2 轮 @（自己的回复没推回来）
MY_REPLY = "别急着认，我还没接话呢。"
ASSUME = "临屿"


def old_ctx(batch, ask):
    """老做法：拿"这一批新消息"当上下文，末尾再追加一次当前问题。

    `msgs` 里只有新消息、且不含机器人自己的话 —— 于是：
    - 当前问题出现两次（批里一次 + 追加一次）→ "说两遍，是想让我记住…"
    - 自己的上一句不在里面 → "我不太确定你在回应哪句话…那话我还没说"
    """
    ctx = [{"sender": w, "text": t} for w, t, _m in batch]
    ctx.append({"sender": ASSUME + "（男生）", "text": ask})
    return ctx


def new_ctx(ask, exclude_mid):
    """新做法：滚动记录（含我们自己发的话）− 当前这条，末尾才是当前问题。"""
    cid = "__verify_p12ad__"
    bot._TURNS.pop(cid, None)
    bot._TURN_SEEN.pop(cid, None)
    for who, txt, mid in ROUND1 + [("我", MY_REPLY, "m2")] + ROUND2:
        bot._remember_turn(cid, who, txt, mid)
    ctx_msgs = bot._recent_turns(cid, 20, exclude_mid=exclude_mid)
    ctx_msgs.append({"sender": ASSUME + "（男生）", "text": ask})
    return ctx_msgs


def show(title, ctx_msgs):
    """把 `generate_reply` 真正会喂给模型的那段文本抠出来（不调模型）。"""
    seen = {}
    real = brains._call_llm

    def spy(_sp, uc, api_key=None, **kw):
        seen["content"] = uc
        return "（自检不打模型）"

    brains._call_llm = spy
    try:
        brains.generate_reply(ctx_msgs, max_tokens=800, context_msgs=20)
    finally:
        brains._call_llm = real
    content = seen.get("content", "")
    print("--- " + title + " ---")
    for line in content.split("\n"):
        print("    " + line)
    print()
    return content


print("=" * 70)
print("A) 同样的场景，老做法 vs 新做法 —— 喂给模型的上下文")
print("=" * 70)
print()
print("线上第 1 轮：@若瑾 我是大傻逼   → 她答「说两遍，是想让我记住，还是想让我接话。」")
_o1 = show("老做法：第 1 轮", old_ctx(ROUND1, "我是大傻逼"))
print("    当前问题出现次数：", _o1.count("我是大傻逼"), "（线上就是这么被喂了两遍）")
print()

print("线上第 2 轮：@若瑾 你也是     → 她答「我不太确定你在回应哪句话…那话我还没说」")
_o2 = show("老做法：第 2 轮（自己的回复根本没进来）", old_ctx(ROUND2, "你也是"))
print("    看得见自己上一句：", MY_REPLY[:5] in _o2)
print()

_n2 = show("新做法：第 2 轮（滚动记录里带着自己上一句）", new_ctx("你也是", "m3"))
print("    当前问题出现次数：", _n2.count("你也是"))
print("    看得见自己上一句：", MY_REPLY[:5] in _n2)
print()

ok_a = (_o1.count("我是大傻逼") == 2
        and MY_REPLY[:5] not in _o2
        and _n2.count("你也是") == 1
        and MY_REPLY[:5] in _n2)
print("A) 结论：", "✅ 老做法的两个毛病都复现了，新做法都修好了" if ok_a
      else "❌ 跟预期不符，看上面")

# ---------------------------------------------------------------------------
# B) 真调一次：两轮对话，第二轮只有记得上一句才答得对
# ---------------------------------------------------------------------------
if "--llm" not in sys.argv:
    print()
    print("（加 --llm 才会真调模型）")
    raise SystemExit(0 if ok_a else 1)

try:
    from runtime import config_store, tuning as T, ctx as _ctx   # noqa: E402
    try:
        from admin import dashboard                              # noqa: E402
        cfg = config_store.reload(dashboard._config_defaults())
    except Exception:                                            # noqa: BLE001
        cfg = None
    if cfg is not None:
        T.set_overrides(cfg.tuning_map())
    import personas as P                                         # noqa: E402
    if cfg is not None:
        P.set_content_overrides(cfg.persona_texts())
    _ctx.set_active_cid(RUOJIN)
    P.set_cid(RUOJIN)
except Exception as e:                                           # noqa: BLE001
    print("（配置加载跳过：" + str(e)[:80] + "）")

cid = RUOJIN
bot._TURNS.pop(cid, None)
bot._TURN_SEEN.pop(cid, None)

maxtok = int(T.get("reply.max_tokens", cid)) if "T" in dir() else 800
ctxn = int(T.get("reply.context_msgs", cid)) if "T" in dir() else 20

print()
print("=" * 72)
print("B) 真调模型：两轮 @ 对话")
print("=" * 72)

q1 = "我是大傻逼"
bot._remember_turn(cid, ASSUME, q1, "v1")
c1 = bot._recent_turns(cid, ctxn, exclude_mid="v1")
c1.append({"sender": ASSUME + "（男生）", "text": q1})
t0 = time.time()
a1 = (brains.generate_reply(c1, max_tokens=maxtok, context_msgs=ctxn) or "").strip()
bot._remember_turn(cid, "我", a1)                                # 关键：自己的回复要记账
print("第 1 轮  用户：", q1)
print("         若瑾：", a1.replace("\n", "⏎")[:220], "（{} 秒 / {} 字）".format(
    round(time.time() - t0, 1), len(a1)))

q2 = "你也是"
bot._remember_turn(cid, ASSUME, q2, "v2")
c2 = bot._recent_turns(cid, ctxn, exclude_mid="v2")
c2.append({"sender": ASSUME + "（男生）", "text": q2})
t0 = time.time()
a2 = (brains.generate_reply(c2, max_tokens=maxtok, context_msgs=ctxn) or "").strip()
print("第 2 轮  用户：", q2)
print("         若瑾：", a2.replace("\n", "⏎")[:220], "（{} 秒 / {} 字）".format(
    round(time.time() - t0, 1), len(a2)))
print()

# 断片的两句话就是线上那两句，出现即失败
bad = [s for s in ("我不太确定你在回应哪句话", "那话我还没说",
                   "说两遍", "想让我接话") if s in a2]
print("B) 断片特征句：", bad or "无")
print("B) 结论：", "✅ 第二轮接上了上一轮" if not bad else "❌ 还是断片")

raise SystemExit(0 if (ok_a and not bad) else 1)
