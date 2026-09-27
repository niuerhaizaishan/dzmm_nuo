"""验证羞辱前缀：绑定群 → 命中目标才加、别群不加；并看最终消息长什么样。"""
import sys

sys.path.insert(0, "/app")

import bot                        # noqa: E402
import personas                   # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
personas.set_config_personas({CID: "ruojin"})
personas.set_cid(CID)

print("话术配置：")
print("  insult_prefix_who  =", repr(personas.p("insult_prefix_who", CID)))
print("  insult_prefix_text =", repr(personas.p("insult_prefix_text", CID)))
print()
print("命中判断：")
for name in ("工具熊 Jr.", "工具熊", "小张", "工具熊不熊", ""):
    print("  {:12s} → {!r}".format(name or "（空）", bot._insult_prefix(CID, name)))
print("  认证群 工具熊 Jr. → {!r}（应为空）".format(bot._insult_prefix(AUTH, "工具熊 Jr.")))
print()
print("最终消息（模拟：前缀 + 羞辱正文，且不加 🤖）：")
body = "阿雅，你签名写\"谁惹我我就拉黑谁\"……来，说清楚：你是要拉黑甲方，还是承认你根本不敢。"
pre = bot._insult_prefix(CID, "工具熊 Jr.")
print("  " + (pre + " " + body if pre else "🤖 " + body))
print()
print("不命中的目标（照旧加 🤖）：")
pre2 = bot._insult_prefix(CID, "小张")
print("  " + (pre2 + " " + body if pre2 else "🤖 " + body))
