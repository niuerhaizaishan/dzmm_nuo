"""看出站队列里**真正发出去**的消息（长度 + 结尾），用来判断"发骚内容被截断"。

`outbox.db` 保着每条出站消息的原文（不删），所以这是最直接的证据：
如果模型被 max_tokens 截断，正文会停在半句话上（结尾没有句号、或断在词中间）。
"""
import sqlite3
import sys
import time

DB = "/app/data/outbox.db"
AUTH = "bd7fe6a4"      # 认证妹子群
RUOJIN = "f4ec4c21"    # 上班群
CUT = "🎲"             # 发骚/场景输出的开头标记

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
print("表：", [r[0] for r in con.execute(
    "select name from sqlite_master where type='table'")])

since = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
rows = list(con.execute(
    "select created_at, chatroom_id, status, length(text) n, text from outbound"
    " where created_at >= ? order by created_at desc limit 400", (since,)))
print("总行数（窗口内）：", len(rows))

for want, label in ((AUTH, "认证妹子群"), (RUOJIN, "上班群")):
    print()
    print("=== {} 最近的长消息（>=120 字）===".format(label))
    picked = [r for r in rows
              if str(r["chatroom_id"]).startswith(want) and (r["n"] or 0) >= 120]
    print("  条数：", len(picked))
    for r in picked[:12]:
        t = r["text"]
        lines = t.count("\n") + 1
        print("  {}  {} 字 / {} 行  [{}]".format(
            time.strftime("%m-%d %H:%M:%S", time.localtime(r["created_at"])),
            r["n"], lines, r["status"]))
        print("     尾 30 字：…{}".format(t[-30:].replace("\n", "⏎")))
        print("     首 24 字：{}".format(t[:24].replace("\n", "⏎")))

print()
print("=== 所有以 🎲 开头的消息（发骚/场景）===")
for r in [x for x in rows if x["text"].lstrip().startswith(CUT)][:15]:
    t = r["text"]
    print("  {} {}  {} 字 / {} 行  尾：…{}".format(
        str(r["chatroom_id"])[:8],
        time.strftime("%m-%d %H:%M:%S", time.localtime(r["created_at"])),
        r["n"], t.count("\n") + 1, t[-24:].replace("\n", "⏎")))
