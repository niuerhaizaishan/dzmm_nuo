"""P12-W 线上自检：真跑一次发骚正文（看长度/是否截断）+ 排行榜前 10 + 参数生效值。

真调两次模型（认证群 + 上班群各一次），所以脚本会跑 1 分钟上下；
配合 `docker exec -d ... > /tmp/vlo.log` 用，避免 ssh 卡在无输出上。
"""
import hashlib
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import bot                                                     # noqa: E402  (装好配置/人设)
import brains                                                  # noqa: E402
from runtime import ctx                                        # noqa: E402
from runtime import tuning as T                                # noqa: E402

AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
END_OK = "。！？…♡～)）\"”』」*_"


def tail_flag(t: str) -> str:
    t = (t or "").rstrip()
    return "" if (t and t[-1] in END_OK) else "   ← 结尾不像句子"


def one(cid: str, label: str, ai_input: str) -> None:
    ctx.set_active_cid(cid)
    t0 = time.time()
    text = brains.generate_scene(ai_input)
    dt = time.time() - t0
    print("  [{}] {} 秒  {} 字 / {} 行{}".format(
        label, round(dt, 1), len(text), text.count("\n") + 1, tail_flag(text)))
    print("      尾 30 字：…{}".format(text[-30:].replace("\n", "⏎")))


for f in ("/app/brains.py", "/app/bot.py", "/app/prompts/SCENE_PROMPT.md"):
    print("  md5 {}  {}".format(hashlib.md5(open(f, "rb").read()).hexdigest()[:12], f))

print()
print("=== 参数 ===")
for cid, label in ((AUTH, "认证妹子群"), (RUOJIN, "上班群")):
    print("  {} scene.max_tokens = {}".format(label, T.get("scene.max_tokens", cid)))

print()
print("=== 排行榜（认证群，真实状态）===")
ctx.set_active_cid(AUTH)
rank = bot.rank_summary()
lines = rank.splitlines()
print("  {} 行 / {} 字".format(len(lines), len(rank)))
print("  首行：", lines[0])
for ln in lines[1:4]:
    print("   ", ln)
print("   ...")
print("   ", lines[-2])
print("   ", lines[-1])
print("  结尾不像句子" if rank.rstrip()[-1] not in END_OK else "  结尾正常")

print()
print("=== 真跑发骚正文（P12-W 之后）===")
one(RUOJIN, "上班群", "主角：临屿（性别：男生）\n用户描述（务必据此描写润色，不要增加其他人物/场景/道具/体位）：跪下，自己把话说清楚")
one(AUTH, "认证群", "主角：紫苑（性别：女生）\n场景：教室\n体位：骑乘\n附加：蒙着眼不许摘")
print()
print("done")
