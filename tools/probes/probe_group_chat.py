# -*- coding: utf-8 -*-
"""看一眼某个群里"我们发的消息"长什么样（人工评审用：长度 / 语气零件 / 装饰密度）。

用法（容器内）：`python /app/probe_group_chat.py <cid> [n]`
"""
import re
import sys

sys.path.insert(0, "/app")

import dzmm                                                    # noqa: E402
from runtime import identity as ID                             # noqa: E402

DEFAULT_CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"          # 上班群（若瑾）
SOFT = ("～", "呀", "嘛", "啦", "哦", "呢")
BOOKISH = ("我认为", "建议你", "需要注意", "综上", "首先", "其次", "总的来说",
           "希望", "需要注意的是", "我们可以", "由此可见")


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CID
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    # 把配置里的 Bot 也认成"我自己"（否则若瑾/薇丝发的消息会被算成群友）
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        ID.register_bots(getattr(cfg, "bots", []) or [])
    except Exception as e:                                     # noqa: BLE001
        print("（注册 Bot 身份失败，'我们'可能统计不准：{}）".format(str(e)[:60]))
    ID._name_cache.update(dzmm.get_members(cid) or {})
    # 平台单次上限 100 ⇒ 用 `get_messages_all` 翻页（游标是 sent_at）
    msgs = dzmm.get_messages_all(cid, max_count=n) or [] if n > 100 \
        else (dzmm.get_messages(cid, n) or [])
    mine, theirs = [], []
    for m in msgs:
        t = (m.get("content") or {}).get("text") or ""
        (mine if ID.is_self(m.get("sent_by") or "") else theirs).append((m, t))
    print("群 {} · 最近 {} 条：我们 {} 条 / 别人 {} 条".format(cid[:8], len(msgs),
                                                           len(mine), len(theirs)))
    print()
    for m in msgs:
        t = (m.get("content") or {}).get("text") or ""
        who = "【我】" if ID.is_self(m.get("sent_by") or "") else \
            ID.get_name(m.get("sent_by") or "")[:10]
        if len(sys.argv) > 3 and sys.argv[3] == "mine" and who != "【我】":
            continue
        print("{:>8} {:<10} {:>4}字 {:>2}行 | {}".format(
            (m.get("sent_at") or "")[11:19], who, len(t), t.count("\n") + 1,
            t[:90].replace("\n", "⏎")))
    print()
    def _stats(name, rows):
        if not rows:
            print("{}：无".format(name))
            return
        lens = sorted(len(t) for _, t in rows)
        n = len(lens)
        med = lens[n // 2]
        print("{} {} 条：中位 {} 字 / 均值 {:.0f} / 最长 {} | ≤20字 {:.0%} | 5行以上 {:.0%}".format(
            name, n, med, sum(lens) / n, lens[-1],
            sum(1 for x in lens if x <= 20) / n,
            sum(1 for _, t in rows if t.count("\n") >= 4) / n))
        print("      带 ❤️ {:.0%} | 带 ！ {:.0%} | 带空行 {:.0%} | 软尾音 {:.0%}".format(
            sum(1 for _, t in rows if "❤" in t) / n,
            sum(1 for _, t in rows if "！" in t or "!" in t) / n,
            sum(1 for _, t in rows if "\n\n" in t) / n,
            sum(1 for _, t in rows if any(s in t for s in SOFT)) / n))
    _stats("【我们】", mine)
    _stats("【别人】", theirs)
    if not mine:
        return 0
    lens = [len(t) for _, t in mine]
    print("我们这 {} 条：长度 min/avg/max = {}/{:.0f}/{}".format(
        len(lens), min(lens), sum(lens) / len(lens), max(lens)))
    print("  带 ❤️ 的：{} 条 | 带 ！的：{} 条 | 一条 <=20 字：{} 条".format(
        sum(1 for _, t in mine if "❤" in t),
        sum(1 for _, t in mine if "！" in t or "!" in t),
        sum(1 for _, t in mine if len(t) <= 20)))
    print("  出现软尾音零件 {} 的：{} 条".format(
        "/".join(SOFT), sum(1 for _, t in mine if any(s in t for s in SOFT))))
    print("  书面腔（{}…）命中：{} 条".format(
        BOOKISH[0], sum(1 for _, t in mine if any(b in t for b in BOOKISH))))
    # 开头词分布（看有没有"开场白套路"）
    heads = {}
    for _, t in mine:
        h = re.sub(r"^🤖\s*", "", t.strip())[:6]
        heads[h] = heads.get(h, 0) + 1
    print("  开头 6 字重复度：", sorted(heads.items(), key=lambda kv: -kv[1])[:6])
    return 0


if __name__ == "__main__":
    sys.exit(main())
