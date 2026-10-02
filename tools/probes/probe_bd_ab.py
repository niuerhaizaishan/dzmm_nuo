# -*- coding: utf-8 -*-
"""P12-BD 上线验证：上班群的若瑾**现在**像不像人（真调模型，不发消息）。

两条一起量：

  ① 线上真实流量 —— 21:06 热加载之后她真发出去的那些回复（长度 / 空行 / ❤️ / 递话）；
  ② 离线 A/B —— 同一批**真实 @ 她的输入**，各采样 N 次，对比：
       A 旧口径：`AT_FRAME_PROMPT` 原样（"2~6 句、100~300 字起步"）
       B 新口径：`chat.frame_length` 把【写多长】整行换掉（线上生效的那份）
     两条都走**同一个人设**（现版 `RUOJIN_EXTRA`，含"说完就停"），
     所以 A/B 的差就是"长度口径"这一项的差。

口径与 `probe_solicit.py` 一致（那个探针量的是改动**之前**的线上分布：
递话率 67%、中位 116 字、空行分段 93%、❤️ 80%）。

用法（容器内，先 docker cp 进去）：
    docker cp tools/probe_bd_ab.py dzmm-bot:/tmp/probe_bd_ab.py
    docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python /tmp/probe_bd_ab.py [样本数]
"""
import random
import statistics
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")
sys.path.insert(0, "/tmp")          # probe_solicit.py 被 docker cp 到这里

import bot                                                     # noqa: E402
import brains                                                  # noqa: E402
import dzmm                                                    # noqa: E402
import personas as P                                           # noqa: E402
from admin import dashboard                                    # noqa: E402
from runtime import config_store, ctx, identity as ID          # noqa: E402
from runtime import tuning as T                                # noqa: E402
from probe_solicit import classify                             # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"        # 上班如上坟，下班如还魂（若瑾）
SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 3
FALLBACK_Q = ["在忙什么", "今天怎么样", "这个该怎么办", "晚上吃什么", "你还在吗"]
HIST = [{"sender": "工具熊 Jr.", "text": "✧ 2026-09-30 运势 ✧ 今日宜摸鱼"},
        {"sender": "晴天☀️", "text": "这班上得我人都麻了"},
        {"sender": "我", "text": "麻了就去睡，别硬撑。"}]


def setup():
    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    P.set_content_overrides(cfg.persona_texts())
    P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values() if s.persona})
    ctx.set_active_cid(CID)
    P.set_cid(CID)


def real_questions(limit=5):
    """从群里捞**最近真的 @ 她**的那几句（上线后的流量就是这一类）。"""
    out = []
    try:
        ID._name_cache.update(dzmm.get_members(CID) or {})
        msgs = dzmm.get_messages_all(CID, max_count=300) or []
    except Exception as e:                                     # noqa: BLE001
        print("（拉群消息失败：{}）".format(str(e)[:80]))
        return out
    for m in reversed(msgs):
        t = ((m.get("content") or {}).get("text") or "").strip()
        uid = m.get("sent_by") or ""
        if not t or ID.is_self(uid) or not ID.is_at_me(t, CID):
            continue
        if len(t) < 3 or len(t) > 60:
            continue
        out.append((ID.get_name(uid), t))
        if len(out) >= limit:
            break
    return out


def live_replies(n=400):
    """热加载之后她自己发出去的回复（真实流量）。"""
    try:
        msgs = dzmm.get_messages_all(CID, max_count=n) or []
    except Exception:                                          # noqa: BLE001
        return []
    out = []
    for m in msgs:
        t = ((m.get("content") or {}).get("text") or "").strip()
        if t.startswith("🤖") and ID.is_self(m.get("sent_by") or ""):
            out.append(t.lstrip("🤖 ").strip())
    return out


def stats(name, rows):
    if not rows:
        print("{}：无".format(name))
        return
    kinds = {"提问": 0, "派活": 0, "菜单": 0, "陈述": 0}
    heart = blank = menu_n = 0
    for t in rows:
        k, _tail, menu = classify(t)
        kinds[k] += 1
        menu_n += 1 if menu else 0
        heart += 1 if "❤️" in t else 0
        blank += 1 if "\n\n" in t else 0
    tot = len(rows)
    lens = [len(t) for t in rows]
    print("{} {} 条".format(name, tot))
    print("   递话率(提问+派活) {:>4.0%} | 陈述收尾 {:>4.0%} | 菜单 {:>4.0%} | "
          "❤️ {:>4.0%} | 空行分段 {:>4.0%}".format(
              (kinds["提问"] + kinds["派活"]) / tot, kinds["陈述"] / tot,
              menu_n / tot, heart / tot, blank / tot))
    print("   字数 中位={} 均值={:.0f} 最短={} 最长={}".format(
        int(statistics.median(lens)), statistics.mean(lens), min(lens), max(lens)))
    print("   末尾（全部，看还有哪些还在递话）：")
    for t in rows:
        k, tail, _m = classify(t)
        print("     [{:<2}] {!r}".format(k, tail))


def one_frame(cid, who, q, use_length):
    frame = bot.AT_FRAME_PROMPT
    if use_length:
        frame = bot._apply_frame_length(frame, str(T.get("chat.frame_length", cid) or ""))
    return frame.replace("{who}", who).replace("{q}", q).replace("{pending}", "（空）")


def main():
    setup()
    print("== 参数 ==")
    for k in ("chat.decor_prob", "chat.bang_prob", "chat.humanize", "chat.frame_length"):
        v = T.get(k, CID)
        print("  {} = {}".format(k, (str(v)[:46] + "…") if len(str(v)) > 47 else v))

    print("\n== ① 线上真实流量（21:06 热加载之后的回复）==")
    stats("【线上·现在】", live_replies())

    qs = real_questions()
    if not qs:
        qs = [("阿宝", q) for q in FALLBACK_Q]
        print("（没捞到真实 @ 输入，用固定问题）")
    print("\n输入（最近真的 @ 她的 {} 句）：".format(len(qs)))
    for who, q in qs:
        print("   {:<10} {}".format(who[:10], q[:40]))

    results = {"A 旧口径（原【写多长】）": [], "B 新口径（chat.frame_length）": []}
    for vname, use_len in (("A 旧口径（原【写多长】）", False),
                           ("B 新口径（chat.frame_length）", True)):
        for who, q in qs:
            for _ in range(SAMPLES):
                c = [dict(h) for h in HIST]
                c.append({"sender": "", "text": one_frame(CID, who, q, use_len)})
                try:
                    out = brains.generate_reply(
                        c, max_tokens=int(T.get("reply.max_tokens", CID)),
                        context_msgs=int(T.get("reply.context_msgs", CID)))
                except Exception as e:                         # noqa: BLE001
                    print("   生成失败：{}".format(str(e)[:80]))
                    continue
                out = (out or "").strip()
                if not out:
                    continue
                if T.get("chat.humanize", CID):
                    out = bot._humanize_chat(out)
                heart = P.p("chat_decor", CID)
                if heart and random.random() >= float(T.get("chat.decor_prob", CID)):
                    heart = ""
                bang = random.random() < float(T.get("chat.bang_prob", CID))
                results[vname].append(bot._ensure_decor(out, heart, bang=bang))

    print()
    for vname, rows in results.items():
        stats(vname, rows)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
