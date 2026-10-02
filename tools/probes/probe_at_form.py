"""决定性验证：群里 @ 机器人的消息，**文本里字面写的是哪个名字**。

`is_at_me()` 做的是字面包含判断（`"@" + name in text`），
所以配置里该填哪个，取决于平台把哪个名字写进了消息文本 —— 不能靠猜。
"""
import os
import re
import sys

sys.path.insert(0, "/app")
os.chdir("/app")

from dzmm import get_messages_all  # noqa: E402

BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()
CID = (os.environ.get("DZMM_BOT_CID") or "").strip()

msgs = get_messages_all(CID, max_count=200) or []
print("拉到消息:", len(msgs), "条")

print()
print("=== A) 文本里出现 @小小糯* 的消息（看字面形式） ===")
pat = re.compile(r"@\s*小小糯[^\s，。！？,.!?]*")
seen = {}
for m in msgs:
    text = (m.get("content") or {}).get("text") or ""
    for hit in pat.findall(text):
        seen.setdefault(hit, 0)
        seen[hit] += 1
if seen:
    for k, v in sorted(seen.items(), key=lambda kv: -kv[1]):
        print("   {!r:22s} 出现 {} 次".format(k, v))
else:
    print("   （最近 200 条里没有 @小小糯* —— 换个思路看下面的 B）")

print()
print("=== B) 所有 @xxx 形式（看看平台 @ 的写法） ===")
at = {}
for m in msgs:
    text = (m.get("content") or {}).get("text") or ""
    for hit in re.findall(r"@[^\s，。！？,.!?]{1,20}", text):
        at.setdefault(hit, 0)
        at[hit] += 1
for k, v in sorted(at.items(), key=lambda kv: -kv[1])[:12]:
    print("   {!r:24s} {} 次".format(k, v))

print()
print("=== C) 含「小小糯」但不带 @ 的（判断有没有别的写法） ===")
forms = {}
for m in msgs:
    text = (m.get("content") or {}).get("text") or ""
    for hit in re.findall(r"小小糯[^\s，。！？,.!?]{0,8}", text):
        forms.setdefault(hit, 0)
        forms[hit] += 1
for k, v in sorted(forms.items(), key=lambda kv: -kv[1])[:12]:
    print("   {!r:24s} {} 次".format(k, v))

print()
print("=== D) 机器人自己发的消息（sent_by == BOT_UID） ===")
n = 0
for m in msgs:
    if m.get("sent_by") == BOT_UID:
        n += 1
        if n <= 3:
            print("   ", repr(((m.get("content") or {}).get("text") or ""))[:80])
print("   共", n, "条")
