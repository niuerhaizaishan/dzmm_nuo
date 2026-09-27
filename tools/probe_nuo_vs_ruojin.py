"""对照探针：认证群的小小糯 vs 上班群的若瑾 —— 上下文/长度/知识**今天各取到什么值**。

屋主 2026-09-27："妹子认证群里的小小糯的上下文、长度、知识等调整为临屿这样，
但是人设以及其他不变。" 动手之前先把"到底差在哪"摆出来（可能根本没差：

- `reply.max_tokens` / `reply.context_msgs` 的**代码默认值**是全局的（800 / 20），
  `chat` 也是默认开；P12-AC 还把 `prompts/CMD_PROMPT.md` 改成了
  "2-6 句/100-300 字 + 用你自己的知识 + 别打太极"。
- 所以真正可能有差别的只剩：**按群覆盖**、以及 **人设 extra**（若瑾那 1900 字里
  另写了一遍长度/知识口径，小小糯的 extra 是空的）。

顺便查一下"临屿"在群里到底是人还是 Bot（两边都查）。

用法（容器内）：
    python /app/probe_nuo_vs_ruojin.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import bot                                                     # noqa: E402
import dzmm                                                    # noqa: E402
import personas as P                                           # noqa: E402
from runtime import config_store, tuning as T                  # noqa: E402
from runtime import features as F                              # noqa: E402

AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"   # 认证妹子群（小小糯）
RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"  # 上班群（若瑾）
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"   # 玫瑰房（薇丝）

try:
    from admin import dashboard
    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    P.set_content_overrides(cfg.persona_texts())
except Exception as e:                                         # noqa: BLE001
    cfg = None
    print("（配置加载跳过：" + str(e)[:100] + "）")

print("=" * 72)
print("一、三个群今天各自取到的值（生效值 = 覆盖 > 代码默认）")
print("=" * 72)

labels = ((AUTH, "认证群/小小糯"), (RUOJIN, "上班群/若瑾"), (ROSE, "玫瑰房/薇丝"))
for cid, label in labels:
    key = P.persona_key_for(cid)
    extra = (P.effective_persona(key) or {}).get("extra") or ""
    spec = cfg.group(cid) if cfg else None
    print()
    print("【{}】{}".format(label, cid))
    print("  人设 key            = {}".format(key))
    print("  人设 extra 字数      = {}".format(len(extra)))
    print("  chat（@问答）开关    = {}".format(
        F.on(cid, "chat") if cfg else "(未加载配置)"))
    print("  quote_reply（引用接话）= {}".format(
        F.on(cid, "quote_reply") if cfg else "(未加载配置)"))
    print("  reply.max_tokens    = {}".format(T.get("reply.max_tokens", cid)))
    print("  reply.context_msgs  = {}".format(T.get("reply.context_msgs", cid)))
    print("  群级 features 覆盖   = {} 项".format(
        len(getattr(spec, "features", None) or {})))
    print("  群级 tuning 覆盖     = {}".format(
        dict(getattr(spec, "tuning", None) or {}) or "无"))
    print("  人设文本覆盖         = {}".format(
        "有" if (cfg and key in (cfg.personas or {})) else "无"))

# 参数默认值 vs 覆盖值：把"谁被按群改过"说清楚
print()
print("--- 全局 features/tuning 覆盖（不分群的）---")
if cfg:
    print("  features =", dict(cfg.features or {}) or "无")
    print("  tuning   =", {k: v for k, v in (cfg.tuning or {}).items()} or "无")

print()
print("=" * 72)
print("二、generate_reply 真正吃到的两段文本（认证群）")
print("=" * 72)
import brains                                                  # noqa: E402
print("  CMD_PROMPT 字数 = {}".format(len(brains.CMD_PROMPT)))
for kw in ("2-6 句", "100-300 字", "用你自己的知识", "别动不动拒答", "别打太极"):
    print("    {:<16s} {}".format(kw, "有" if kw in brains.CMD_PROMPT else "没有"))
print("  （`brains.generate_reply` 对三个群用的都是同一份 CMD_PROMPT；")
print("    若瑾那套是额外追加 RUOJIN_EXTRA，小小糯 extra 为空）")

print()
print("=" * 72)
print("三、'临屿' 是谁（上班群 + 认证群都查）")
print("=" * 72)
for cid in (RUOJIN, AUTH):
    try:
        members = dzmm.get_members(cid) or {}
    except Exception as e:                                     # noqa: BLE001
        print("  {} 成员表拉取失败：{}".format(cid[:8], str(e)[:60]))
        continue
    hits = [(u, n) for u, n in members.items() if "临屿" in (n or "")]
    print("  {}（{} 人）：".format(cid[:8], len(members)))
    if not hits:
        print("    没有叫「临屿」的成员")
    for u, n in hits:
        info = dzmm.get_user(cid, u) or {}
        print("    uid={} name={!r} isBot={} gender={}".format(
            u, n, info.get("isBot"), info.get("gender")))
    cnt = {}
    for m in (dzmm.get_messages(cid, limit=40) or []):
        u = m.get("sent_by", "")
        cnt[u] = cnt.get(u, 0) + 1
    bots = []
    for u in cnt:
        info = dzmm.get_user(cid, u) or {}
        if info.get("isBot"):
            bots.append((u, members.get(u, "?")))
    print("    最近 40 条里发言的 Bot：{}".format(bots or "无"))
print()
print("done")
