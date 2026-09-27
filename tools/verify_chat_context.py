"""P12-AC 线上自检：`@` 问答现在**带上下文、能多写、用自己的知识答**。

真调模型（3 次），配合 `docker exec -d ... > /tmp/vac.log` 用：
1. 多轮上下文：把"我刚才说过什么"喂进去，让它接着聊（证明连续性）；
2. 长度：看字数（默认 `reply.max_tokens=800`，提示词要求 100-300 字）；
3. 知识口径：问一个"你怎么看…"的观点题，看它是**给出自己的判断**还是打太极。
"""
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import bot                                                     # noqa: E402
import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402
from runtime import config_store                              # noqa: E402
from runtime import ctx                                        # noqa: E402
from runtime import tuning as T                                # noqa: E402

RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"

from admin import dashboard                                    # noqa: E402
defaults = dashboard._config_defaults()
cfg = config_store.reload(defaults)
T.set_overrides(cfg.tuning_map())
P.set_content_overrides(cfg.persona_texts())
P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values() if s.persona})

print("=== 参数（按群）===")
for cid, label in ((RUOJIN, "工作群"), (AUTH, "认证群")):
    print("  {}：reply.max_tokens={} reply.context_msgs={}".format(
        label, T.get("reply.max_tokens", cid), T.get("reply.context_msgs", cid)))

# 一段真实感的多轮上下文：注意"我"= 机器人自己说过的话
HISTORY = [
    {"sender": "我", "text": "今天谁再让我改需求，我就把他的键盘泡进茶杯里。"},
    {"sender": "临屿", "text": "你昨天不是说要好好说话吗"},
    {"sender": "我", "text": "好好说话是对人，不是对提需求的人。"},
    {"sender": "临屿（男生）", "text": "那你现在心情怎么样，还在气吗？"},
]

print()
print("=== ① 多轮上下文 + ② 长度 ===")
ctx.set_active_cid(RUOJIN)
t0 = time.time()
out = brains.generate_reply(HISTORY, max_tokens=800, context_msgs=20)
print("  [工作群] {} 秒  {} 字 / {} 行".format(round(time.time() - t0, 1),
                                             len(out), out.count("\n") + 1))
print("  正文：", out.replace("\n", "⏎")[:300])
print("  是否接着上一轮（提到生气/需求/键盘/茶 之一）：",
      any(k in out for k in ("气", "需求", "键盘", "茶")))

print()
print("=== ③ 用自己的知识回答（观点题）===")
t0 = time.time()
out2 = brains.generate_reply(
    [{"sender": "临屿（男生）", "text": "你怎么看加班这件事？说点你自己的看法"}],
    max_tokens=800, context_msgs=20)
print("  [工作群] {} 秒  {} 字".format(round(time.time() - t0, 1), len(out2)))
print("  正文：", out2.replace("\n", "⏎")[:300])
deflect = ("不太清楚", "建议看看", "没有数据", "无法回答", "我不知道")
print("  有没有打太极（不该出现）：", [d for d in deflect if d in out2] or "无")

print()
print("=== 对照：认证群（小小糯，同样问题）===")
ctx.set_active_cid(AUTH)
out3 = brains.generate_reply(
    [{"sender": "紫苑（女生）", "text": "小小糯，你怎么看加班这件事？"}],
    max_tokens=800, context_msgs=20)
print("  {} 字：{}".format(len(out3), out3.replace("\n", "⏎")[:220]))
print("done")
