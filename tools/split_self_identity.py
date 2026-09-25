"""一次性迁移：把「我自己」从单一 NUO_UID 拆成 账号 + 官方 Bot 两个身份（P4.2）。

为什么必须做：P4.1 上线后 Bot 会发群消息，它的消息 `sent_by` 是 **Bot id**，
不是账号 uid。现有回声过滤只认账号 uid → **Bot 自己的发言会被当成群友**，
轻则统计污染，重则自触发循环。

⚠️ 为什么不用全局替换：`NUONUO_UID`（真人 uid）**包含** `NUO_UID` 子串，
朴素替换会把它改成 `NUOSIDECAR_UID`。本脚本用**精确整串匹配 + 计数断言**。

每个替换都断言出现次数；任何一条不符即整体中止，绝不写入半成品。
"""
import re
import sys
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent / "bot.py"

# (旧串, 新串, 期望次数) —— 顺序有意安排：先长后短，避免短串吃掉长串
PAIRS = [
    # ⚠️ 必须最先：行 2652 的列表推导以 `!= NUO_UID]` 结尾，它**同时**匹配下面
    # `if m.get("sent_by") and m.get("sent_by") != NUO_UID` —— 先处理带 `]` 的，
    # 那条的期望次数才会是正确的 1。
    ('m.get("sent_by") != NUO_UID]', 'not is_self(m.get("sent_by"))]', 1),
    # --- 含 and / or 的复合条件（先处理） ---
    ('had_new = any(m.get("sent_by") != NUO_UID for m in pending)',
     'had_new = any(not is_self(m.get("sent_by")) for m in pending)', 1),
    ('if m.get("sent_by") in (uid, NUO_UID):',
     'if is_self(m.get("sent_by")) or m.get("sent_by") == uid:', 1),
    ('if m.get("sent_by") and m.get("sent_by") != NUO_UID',
     'if m.get("sent_by") and not is_self(m.get("sent_by"))', 1),
    ('if not cfg or not text or uid == NUO_UID:',
     'if not cfg or not text or is_self(uid):', 1),
    ('if not uid or uid == NUO_UID or delta <= 0:',
     'if not uid or is_self(uid) or delta <= 0:', 1),
    ('if uid and uid != NUO_UID and uid not in uids:',
     'if uid and not is_self(uid) and uid not in uids:', 1),
    ('if uid and uid != NUO_UID and uid != starter:',
     'if uid and not is_self(uid) and uid != starter:', 1),
    ('if uid and uid != NUO_UID and uid != OWNER_UID:',
     'if uid and not is_self(uid) and uid != OWNER_UID:', 1),
    ('if uid and uid != NUO_UID and text and',
     'if uid and not is_self(uid) and text and', 1),
    # --- 其余复合条件 ---
    ('if starter and starter != NUO_UID:',
     'if starter and not is_self(starter):', 1),
    ('if uid2 and uid2 != NUO_UID:',
     'if uid2 and not is_self(uid2):', 1),
    ('if protagonist_uid and protagonist_uid != NUO_UID:',
     'if protagonist_uid and not is_self(protagonist_uid):', 1),
    ('uids.discard(NUO_UID)', 'uids.difference_update(self_uids())', 1),
    ('if u == NUO_UID:', 'if is_self(u):', 1),
    # --- 简单条件（按出现次数批处理） ---
    ('if uid and uid != NUO_UID:', 'if uid and not is_self(uid):', 3),
    ('if m.get("sent_by") == NUO_UID:', 'if is_self(m.get("sent_by")):', 3),
    ('if _m.get("sent_by") == NUO_UID:', 'if is_self(_m.get("sent_by")):', 1),
    ('if uid == NUO_UID:', 'if is_self(uid):', 4),
    ('if muid == NUO_UID:', 'if is_self(muid):', 2),
    ('if m.get("sent_by") != NUO_UID:', 'if not is_self(m.get("sent_by")):', 1),
    # ⚠️ 必须最后：`m.get("sent_by") == NUO_UID` 是上一条的**子串**
    ('m.get("sent_by") == NUO_UID', 'is_self(m.get("sent_by"))', 1),
    # --- A.3 @我 的名字集合 ---
    ('    full = get_full_name(NUO_UID)  # 小小糯～\n'
     '    simple = get_name(NUO_UID)  # 小小糯\n'
     '    for name in {full, simple}:',
     '    for name in self_names():', 1),
    ('    for name in {get_full_name(NUO_UID), get_name(NUO_UID)}:',
     '    for name in self_names():', 1),
    ('    for n in {get_full_name(NUO_UID), get_name(NUO_UID)}:',
     '    for n in self_names():', 1),
]

ANCHOR = 'NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"  # 小小糯～（agent 账号）'

DEFS = '''NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"  # 侧车账号（登录态持有者）

# ---------------------------------------------------------------- 自身身份（P4.2）
# 从 P4 起，「我自己」有**两个**身份，必须分清：
#   NUO_UID  —— 侧车账号：私聊 / 图片 / 分享 / 其他群，以及历史数据里的旧发言
#   BOT_UID  —— 官方 Bot：群文本发送（P4.1 的 RoutingSender）
# 回声过滤与「排除自己」必须同时认这两个。否则 Bot 的发言会被当成群友 ——
# 轻则统计污染，重则自触发循环。
BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()
BOT_NAME = (os.environ.get("DZMM_BOT_NAME") or "小小糯").strip()
BOT_NAME_SHORT = (os.environ.get("DZMM_BOT_SHORT") or "小小糯").strip()


def self_uids() -> set:
    """全部「我自己」的 uid。"""
    ids = {NUO_UID}
    if BOT_UID:
        ids.add(BOT_UID)
    return ids


def is_self(uid) -> bool:
    """这条消息是不是我自己发的（侧车账号或官方 Bot）。"""
    if not uid:
        return False
    return uid == NUO_UID or (bool(BOT_UID) and uid == BOT_UID)


def self_names() -> set:
    """判定「@的是我」用的名字集合。

    账号名查成员表；Bot 名**读配置**（Bot API 没有成员接口，查不到）。
    """
    names = {get_full_name(NUO_UID), get_name(NUO_UID)}
    if BOT_UID:
        names.add(BOT_NAME)
        names.add(BOT_NAME_SHORT)
    return {n for n in names if n}
'''


def main() -> int:
    src = BOT.read_text(encoding="utf-8")

    if "def is_self(" in src:
        print("已经迁移过（检测到 is_self），中止以免重复应用")
        return 1
    if ANCHOR not in src:
        print("找不到锚点行，中止")
        return 1

    for old, new, expect in PAIRS:
        got = src.count(old)
        if got != expect:
            print("中止：期望 {} 次，实际 {} 次\n  {!r}".format(expect, got, old[:70]))
            return 1
        src = src.replace(old, new)
        print("  替换 {:>2} 处: {}...".format(expect, old.strip()[:52]))

    src = src.replace(ANCHOR, DEFS, 1)
    BOT.write_text(src, encoding="utf-8")

    # ---- 自检 ----
    left = re.findall(r'(?<![A-Z_])NUO_UID(?![\w])', src)
    cmp_all = [l.strip() for l in src.splitlines()
               if re.search(r'(==|!=)\s*NUO_UID|NUO_UID\s*(==|!=)|discard\(NUO_UID\)', l)]
    # 唯一允许保留的一处，是 is_self() 自己的函数体
    cmp_left = [l for l in cmp_all if "return uid == NUO_UID" not in l]
    trap = len(re.findall(r'NUONUO_UID', src))
    print()
    print("剩余 NUO_UID 标识符: {} 处（定义块 + 遗留发送身份，属预期）".format(len(left)))
    print("剩余比较/排除用法: {} 处（应为 0；is_self 自身那处已排除）".format(len(cmp_left)))
    print("NUONUO_UID 保留: {} 处（应为 3）".format(trap))
    if cmp_left or trap != 3:
        print("!! 自检未通过：{}".format(cmp_left))
        return 1
    print("OK 迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
