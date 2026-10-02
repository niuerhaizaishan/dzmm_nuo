"""P12-X 线上自检：① 没在局里时 /talk、/ask 是否静默 ② 发骚/羞辱正文有没有表情。

真跑两次模型（羞辱 + 发骚各一次），配合 `docker exec -d ... > /tmp/vs.log` 用。
"""
import hashlib
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import bot                                                     # noqa: E402
import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402
from runtime import ctx                                        # noqa: E402
from runtime import fasao                                      # noqa: E402

AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
EMO = "💗💦🥵"


def emo_count(t: str) -> int:
    return sum(t.count(e) for e in EMO)


for f in ("/app/bot.py", "/app/personas.py", "/app/runtime/text.py"):
    print("  md5 {}  {}".format(hashlib.md5(open(f, "rb").read()).hexdigest()[:12], f))

print()
print("=== ① 没在局里时，不认识的斜杠指令 ===")
for cid, label in ((RUOJIN, "工作群"), (AUTH, "认证妹子群")):
    print("  {}：phrase_is_blank={}  spice_emoji={!r}".format(
        label, P.phrase_is_blank("dj_idle_unknown", cid),
        P.p("spice_emoji", cid)))
    print("     /talk xxx 会回：{!r}".format(
        "" if P.phrase_is_blank("dj_idle_unknown", cid)
        else (P.p("dj_idle_unknown", cid) or P.p("unknown_cmd", cid))[:40]))
    print("     /命令 xxx 会回：{!r}".format(P.p("no_dial", cid)))

print()
print("=== ② 真跑：羞辱 + 发骚（看表情）===")
ctx.set_active_cid(RUOJIN)
t0 = time.time()
raw, style = brains.generate_insult("昵称：临屿\n性别：男生", gender="男生", straight=True)
insult = bot._spice_emoji(raw, P.p("spice_emoji", RUOJIN))
print("  [工作群·羞辱] {} 秒  {} 字 / {} 个表情（风格 {}）".format(
    round(time.time() - t0, 1), len(insult), emo_count(insult), style))
print("      尾 24 字：…{}".format(insult[-24:].replace("\n", "⏎")))

t0 = time.time()
scene_msg = fasao.scene("/发骚 临屿 自己把话说清楚")
body = scene_msg.split("\n", 1)[1] if "\n" in scene_msg else ""
print("  [工作群·发骚] {} 秒  整条 {} 字 / 正文 {} 字 / {} 个表情".format(
    round(time.time() - t0, 1), len(scene_msg), len(body), emo_count(scene_msg)))
print("      首行：{}".format(scene_msg.split("\n")[0][:40]))
print("      尾 24 字：…{}".format(scene_msg[-24:].replace("\n", "⏎")))
print("      首行里有没有表情（应该没有）：", emo_count(scene_msg.split("\n")[0]))

ctx.set_active_cid(AUTH)
t0 = time.time()
raw2, _ = brains.generate_insult("昵称：紫苑\n性别：女生", gender="女生", straight=False)
insult2 = bot._spice_emoji(raw2, P.p("spice_emoji", AUTH))
print("  [认证群·羞辱] {} 秒  {} 字 / {} 个表情".format(
    round(time.time() - t0, 1), len(insult2), emo_count(insult2)))
print("      尾 24 字：…{}".format(insult2[-24:].replace("\n", "⏎")))
print("done")
