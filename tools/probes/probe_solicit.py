# -*- coding: utf-8 -*-
"""P12-BD 探针：**收尾递话率** —— 她是不是每句都要"你来做点什么"。

屋主 2026-09-30 点出的真问题：
> "就是说话有点太像机器人了，就和工具熊每次都要问要不要调酒一样的询问一样。"

也就是：回复末尾总要**把话头递回去**——"要不要…""选一个，我等着""想让我理你，先把话说明白"
"想接话发 /命令 xxx"，或者贴一张指令菜单。真人聊天大多数时候是**说完就停**。

本探针把一个群里"我们"和"真人（排除其他 bot）"的消息末尾分类统计：

    · 提问收尾（？）
    · 派活收尾（要不要 / 好吗 / 行不行 / 选一个 / 说清楚 / 我等着 / 再来）
    · 指令菜单（出现 /xxx 形式的指令提示）
    · 陈述收尾（说完就停 —— 这才是人味）

用法（容器内）：`python /app/probe_solicit.py <cid> [n]`
"""
import re
import sys

sys.path.insert(0, "/app")

import dzmm                                                    # noqa: E402
from runtime import identity as ID                             # noqa: E402

DEFAULT_CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"          # 上班群（若瑾）
ASK = re.compile(r"[？?]\s*$")
# 招揽/派活：**要对方产出下一轮**的收尾（工具熊那种"要不要我给你调杯酒"、以及
# "选一个，我等着""坐下，好好说话，我听着""换个说法再来"）
SOLICIT = ("要不要", "好吗", "好不好", "行不行", "选一个", "说清楚", "说清楚点",
           "我等着", "我听着", "再来", "换个说法", "说句话", "冒个泡", "试试",
           "来吧", "说说看", "说正经", "好好说", "自己动", "别让我等", "给我",
           "收一收", "闭嘴", "坐下", "过来", "跪好", "把话", "接着", "继续")
MENU = re.compile(r"/[a-zA-Z\u4e00-\u9fa5]{1,6}")


def _tail(t, n=26):
    t = re.sub(r"[❤️♡\s]+$", "", (t or "").strip())
    return (t.split("\n")[-1] or t)[-n:]


def classify(t):
    """返回 (类别, 末尾, 是否带指令菜单)。

    类别 = 提问 / 派活 / 菜单 / 陈述 —— **陈述才是"说完就停"**。
    """
    tail = _tail(t)
    menu = bool(MENU.search(tail))
    if ASK.search(tail.rstrip("❤️♡ ")):
        return "提问", tail, menu
    if any(k in tail for k in SOLICIT):
        return "派活", tail, menu
    if menu:
        return "菜单", tail, menu
    return "陈述", tail, menu


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CID
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    # 认得 Bot（含别人家的），统计"真人"时要把它们排除
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        ID.register_bots(getattr(cfg, "bots", []) or [])
    except Exception:                                          # noqa: BLE001
        pass
    raw = dzmm.api("chatroom.getMembers", {"json": {"chatroomId": cid, "limit": 1000}}) \
        if False else None
    bots = set()
    try:
        members_raw = dzmm.api("chatroom.getMembers",
                               {"json": {"chatroomId": cid, "limit": 1000}})
        for m in (members_raw or {}).get("members") or []:
            if m.get("isBot"):
                bots.add(m.get("id"))
    except Exception as e:                                     # noqa: BLE001
        print("（拿 isBot 名单失败：{}）".format(str(e)[:60]))
    ID._name_cache.update(dzmm.get_members(cid) or {})

    msgs = dzmm.get_messages_all(cid, max_count=n) or []
    groups = {"【我们】": [], "【真人】": [], "【别的 bot】": []}
    for m in msgs:
        t = (m.get("content") or {}).get("text") or ""
        if not t.strip():
            continue
        uid = m.get("sent_by") or ""
        if ID.is_self(uid):
            groups["【我们】"].append(t)
        elif uid in bots:
            groups["【别的 bot】"].append(t)
        else:
            groups["【真人】"].append(t)

    for name, rows in groups.items():
        if not rows:
            print("{}：无".format(name))
            continue
        cnt = {"提问": 0, "派活": 0, "菜单": 0, "陈述": 0}
        menu_n = 0
        for t in rows:
            k, _tail_s, menu = classify(t)
            cnt[k] += 1
            menu_n += 1 if menu else 0
        tot = len(rows)
        print("{} {} 条".format(name, tot))
        print("   提问收尾 {:>3.0%} | 派活收尾 {:>3.0%} | 指令菜单 {:>3.0%} | **陈述收尾 {:>3.0%}**".format(
            cnt["提问"] / tot, cnt["派活"] / tot, menu_n / tot, cnt["陈述"] / tot))
        print("   → 递话率（提问+派活） = {:.0%}".format((cnt["提问"] + cnt["派活"]) / tot))
    print()
    print("【我们】逐条末尾（看是不是每句都在要人回应）：")
    for t in groups["【我们】"]:
        k, tail, menu = classify(t)
        print("   [{:<2}{}] …{!r}".format(k, " 菜单" if menu else "", tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
