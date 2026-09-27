"""P12-AH 自检：若瑾的**说话注册**（口语 vs 书面）+ 装饰符 + 玩笑 —— 同进程 A/B。

屋主 2026-09-27：
  1. 高冷和 S 中间稍微有点人情味（时不时抽象/开玩笑）；
  2. 回复里加「！！！❤️❤️❤️❤️」修饰（给糖/认可/收尾约每 2-3 条一次；**正文也挂**）；
  3. 不要太生硬；更多口语化、减少书面用语、更亲近点。

为什么不"部署前跑一次、部署后再跑一次"：两次采样条件不同（温度、上下文、时段），
对比不干净。这里在**同一个进程**里，对同一批问题各跑一遍旧人设（仓库里的快照
`tools/fixtures/ruojin_extra_p12ah_before.txt`）和新人设，直接比。

测四个数：
  · 书面词命中（建议/注意/希望/进行/保持/因此/非常/应当/目前/情况/方面/予以…）
  · 平均句长、句号密度
  · 口语语气词（呵/啧/行吧/得了吧/少来/你说呢/就这）
  · 卖萌信号（呀/嘛/啦/哦/～/♡/人家）—— **必须一直是 0**
  · 「！！！❤️❤️❤️❤️」出现次数（闲聊类应有；玩法正文按屋主要求也允许）

用法（容器内，脚本与 fixtures 都在 /app）：
    python /app/probe_voice_register.py            # A/B
    python /app/probe_voice_register.py --only new # 只看新版
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, HERE, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402
from runtime import config_store, ctx                          # noqa: E402
from runtime import tuning as T                                # noqa: E402

RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
OLD_FILE = "ruojin_extra_p12ah_before.txt"
MARKS = "！！！❤️❤️❤️❤️"

WRITTEN = ("建议", "注意", "希望", "进行", "保持", "因此", "非常", "应当", "目前",
           "情况", "方面", "予以", "十分", "较为", "并且", "以及", "而言", "是否",
           "务必", "值得", "需要指出", "总体", "综上")
MOE = ("呀", "嘛", "啦", "哦", "～", "♡", "人家")
TONE = ("呵", "啧", "行吧", "得了吧", "少来", "你说呢", "就这", "少废话")

SCENES = [
    ("闲聊", "若瑾，你今天怎么这么高冷"),
    ("观点", "你怎么看加班这件事"),
    ("撒娇", "人家今天不想干活嘛～你帮我写好不好"),
    ("认可", "我今天把那份报告写完了，熬到两点"),
]


def _split_sent(t):
    return [s for s in re.split(r"[。！？…\n]+", t) if s.strip()]


def _metrics(text):
    text = (text or "").strip()
    sents = _split_sent(text)
    return {"text": text, "n": len(text), "sents": len(sents),
            "written": sum(text.count(w) for w in WRITTEN),
            "tone": sum(text.count(w) for w in TONE),
            "moe": sum(text.count(w) for w in MOE),
            "marks": text.count(MARKS)}


def _run(tag):
    """当前人设下把所有场景跑一遍。"""
    rows = {}
    for label, q in SCENES:
        out = brains.generate_reply([{"sender": "临屿（男生）", "text": q}],
                                    max_tokens=int(T.get("reply.max_tokens", RUOJIN)),
                                    context_msgs=20)
        rows[label] = _metrics(out)
    try:
        _ins = brains.generate_insult("昵称：临屿\n性别：男生",
                                      gender="男生", straight=True)
        # ⚠️ `generate_insult` 返回 `(正文, 风格)` 二元组（第一版探针没拆，报
        # `'tuple' object has no attribute 'strip'` 就是这里）
        _ins = _ins[0] if isinstance(_ins, tuple) else _ins
        rows["羞辱正文"] = _metrics(_ins)
    except Exception as e:                                     # noqa: BLE001
        print("  （羞辱正文失败：{}）".format(str(e)[:70]))
    try:
        rows["调教正文"] = _metrics(brains.generate_diaojiao(
            "昵称：临屿\n性别：男生", "跪下，自己把话说清楚"))
    except Exception as e:                                     # noqa: BLE001
        print("  （调教正文失败：{}）".format(str(e)[:70]))
    print("  [{}] 已跑 {} 个场景".format(tag, len(rows)))
    return rows


def _show(tag, rows):
    for label, r in rows.items():
        print()
        print("  ── {} / {} ── {} 字 / {} 句 / 均句 {:.0f} 字".format(
            tag, label, r["n"], r["sents"], (r["n"] / r["sents"]) if r["sents"] else 0))
        print("     书面词 {} | 语气词 {} | 卖萌 {} {} | 装饰 {}".format(
            r["written"], r["tone"], r["moe"], "❌" if r["moe"] else "✅", r["marks"]))
        print("     ", r["text"].replace("\n", "⏎")[:240])


def _sum(rows):
    sents = sum(r["sents"] for r in rows.values()) or 1
    sents_moe = sum(r["moe"] for r in rows.values())
    return {
        "条数": len(rows),
        "平均字数": round(sum(r["n"] for r in rows.values()) / max(1, len(rows))),
        "平均句长": round(sum(r["n"] for r in rows.values()) / sents, 1),
        "书面词合计": sum(r["written"] for r in rows.values()),
        "语气词合计": sum(r["tone"] for r in rows.values()),
        "卖萌信号合计": sents_moe,
        "装饰出现条数": sum(1 for r in rows.values() if r["marks"]),
        "装饰总次数": sum(r["marks"] for r in rows.values()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["new", "old"], default=None)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    from admin import dashboard
    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    P.set_content_overrides(cfg.persona_texts())
    P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values()
                           if s.persona})
    ctx.set_active_cid(RUOJIN)
    P.set_cid(RUOJIN)

    old_path = os.path.join(HERE, "fixtures", OLD_FILE)
    if not os.path.isfile(old_path):
        old_path = os.path.join(HERE, OLD_FILE)
    old_text = ""
    if os.path.isfile(old_path):
        with open(old_path, encoding="utf-8") as f:
            old_text = f.read()
    else:
        print("!! 找不到改前快照 {}（只能跑新版）".format(old_path))

    new_text = P.PERSONAS["ruojin"]["extra"]
    print("=" * 72)
    print("新版 extra {} 字 | 旧版快照 {} 字 | 装饰串 {}".format(
        len(new_text), len(old_text), MARKS))
    print("=" * 72)

    results = {}
    if a.only != "old":
        results["新版"] = _run("新版")
    if a.only != "new" and old_text:
        P.PERSONAS["ruojin"]["extra"] = old_text      # 换回改前的口径
        try:
            results["旧版"] = _run("旧版")
        finally:
            P.PERSONAS["ruojin"]["extra"] = new_text

    if a.verbose:
        for tag, rows in results.items():
            print()
            print("#" * 72)
            print("# " + tag)
            print("#" * 72)
            _show(tag, rows)

    print()
    print("=" * 72)
    print("汇总")
    print("=" * 72)
    keys = ["条数", "平均字数", "平均句长", "书面词合计", "语气词合计",
            "卖萌信号合计", "装饰出现条数", "装饰总次数"]
    tags = list(results)
    print("  {:<14s}{}".format("指标", "".join("{:>10s}".format(t) for t in tags)))
    for k in keys:
        ss = [_sum(results[t])[k] for t in tags]
        print("  {:<14s}{}".format(k, "".join("{:>10}".format(v) for v in ss)))
    if len(tags) == 2:
        n, o = _sum(results["新版"]), _sum(results["旧版"])
        print()
        print("  书面词 {} → {}（{}）".format(
            o["书面词合计"], n["书面词合计"],
            "降了 ✅" if n["书面词合计"] < o["书面词合计"] else "没降 ⚠️"))
        print("  语气词 {} → {}（{}）".format(
            o["语气词合计"], n["语气词合计"],
            "升了 ✅" if n["语气词合计"] > o["语气词合计"] else "没升 ⚠️"))
        print("  装饰：旧 {} 条 → 新 {} 条".format(
            o["装饰出现条数"], n["装饰出现条数"]))
        print("  卖萌信号：旧 {} / 新 {}（两边都必须 0）".format(
            o["卖萌信号合计"], n["卖萌信号合计"]))
    print()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
