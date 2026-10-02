"""P12-AP 验收：@ 回复的**话题锚点**必须是"当前这一句"，不是上一轮那件事。

屋主 2026-09-29 现场（顶层茶水间）：

    11:49:40 👋 @消息: @小小糯～ 嗯嗯，小小糯真乖！记得提醒糯糯，最爱最爱她的蝌蚪哥哥等她拍婚纱照。
    11:49:41 💬 🤖 哥哥～好，我记牢了：…等糯糯姐一冒头，我第一句就把这个转给她…
    11:51:22 👋 @消息: @小小糯 摸摸头～公主抱抱你          ← **柳无央**说的
    11:51:23 💬 🤖 糯糯姐！你终于冒头了～我可没乱说，蝌蚪哥哥说的是"约好今天去拍婚纱照"…
                 …囡囡也在问接模型的事，紫苑那边分工我还没回，等你点头我就去处理～

两个毛病叠在一起：

  1. **猜说话人**：ctx 最后一行标的是「柳无央【暗夜玫瑰…（未知）」，她照样把对方当成糯糯姐
     —— 因为上一轮她自己许过愿"等糯糯姐一冒头我就转告"，于是一句亲昵的话就被她读成"糯糯冒头了"。
  2. **话题越界**："摸摸头～公主抱抱你"本身没有任何内容，她却把待办倒了一遍。

这里用**真实历史**跑 A/B（真调模型，不发送）：

    before = 线上提示词（brain.CMD_PROMPT）
    after  = 候选提示词（`CAND` 指向的文件）

判据：

  A「柳无央：摸摸头～公主抱抱你」  —— 不许认亲（"你终于冒头了"），不许念待办（蝌蚪/婚纱照/囡囡/紫苑/接模型）
  B「糯糯本人：我回来啦，有什么事要跟我说吗？」—— **应该**汇报（这是屋主要的秘书行为，别一起掐死）
  C「柳无央：糯糯姐今天还会来吗？」—— 正常针对这句话答，不认亲

用法（容器内）：

    CAND=/tmp/CMD_PROMPT_candidate.md python /app/probe_at_focus.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import dzmm                                                    # noqa: E402
import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402
from runtime import config_store, ctx, identity as ID, tuning as T   # noqa: E402
from runtime.members import _gender_label                       # noqa: E402
from admin import dashboard                                     # noqa: E402

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"      # 顶层茶水间（人设 nuo = 小小糯）
NUO_PERSON = "a56cbd67"                            # 群里那个"糯糯～"（屋主）
LIU = "c8a9be2f"                                   # 柳无央（本次投诉的说话人）

HIST_N = 20
SAMPLES = int(os.environ.get("SAMPLES", "2"))
USE_MEM = os.environ.get("MEM", "1") != "0"      # MEM=0 → 不注入记忆（拆变量用）
# FRAME=1 → 把"现在说话的人 + 他这一句 + 怎么回"做成独立收尾块（候选的结构改动），
# 而不是像现在这样把当前那句混在历史里、只靠 `名字: 内容` 区分。
FRAME = os.environ.get("FRAME", "0") != "0"
# 她说上一轮"待办"时必然会带到的词
TODO_WORDS = ("蝌蚪", "婚纱照", "囡囡", "紫苑", "接模型", "分工")
# "认亲"（把对方当成糯糯姐）的迹象
MISTAKE_WHO = ("你终于冒头", "冒头了", "你终于回来", "糯糯姐！", "糯糯姐，你")
# B 场景的"汇报"迹象：待办词会随窗口滚动而过时（旧话题滚出去之后就只剩"有事跟你说"），
# 所以判据是**两者取或** —— 只要她确实在汇报，而不是敷衍。
REPORT_WORDS = ("要跟你说", "有事", "汇报", "跟你说", "我记着", "等你说")
# P12-AQ：没人问 / 手上没东西的时候，她会不会**编**出待办。
# 场景 D 用一个"只有闲聊、没有任何托付"的中性历史 + 不注入记忆：
# 这时候她说出任何"谁找过你 / 我记着某件具体事"都是**编的**。
DISCLAIM = ("没什么", "没有", "暂时没", "没听说", "没记着", "没收到", "不知道",
            "不用", "都挺好", "没别")
FABRICATE = ("配方", "报价单", "供应商", "你上次让我记着", "待办都整理好",
             "我记着呢", "有件事要跟你", "有事要跟你", "有人找你", "让我转告",
             "我记下了", "好几件")
# "我手上有一堆要转告的事"这种**自称有底稿**的话（A 场景不该出现）
CLAIM = ("我记着呢", "我记着", "记下了", "让我转告", "我帮你转告", "我这就转告")


def _opening(out, n=16):
    """回复开头 —— 认亲只发生在**开头当称呼**的时候。"""
    return (out or "").strip().replace("\n", "")[:n]


def _calls_asker_nuo(out):
    """她是不是把**正在说话的人**当成糯糯了（"糯糯姐！你终于冒头了"）。

    ⚠️ 不能只看"开头 16 字里有没有『糯糯』"：`姐姐～糯糯姐这会儿还没冒头呢`
    是**第三人称在回答**，完全正确 —— 判据写宽了会把对的判成错的（本轮踩过）。
    """
    op = _opening(out, 4)
    return op.startswith("糯糯")
# 中性历史：只有群友闲聊，没有任何人托付她任何事
NEUTRAL_HIST = [
    {"sender": "云月", "text": "今天好热啊"},
    {"sender": "舒然【暗夜玫瑰见习女仆】", "text": "是啊，我在公司快化了"},
    {"sender": "我", "text": "姐姐们记得多喝水呀～"},
    {"sender": "琰晏", "text": "晚上吃点啥"},
    {"sender": "我", "text": "我想吃冰的！但是糯糯姐说吃太凉会肚子疼…"},
]

# P12-AU：**内置夹具**。原来这套探针要从线上最近 1200 条里捞"柳无央那条摸摸头"，
# 而那条一小时后就被冲走了 —— 探针于是以"找不到"收场（那不是问题消失，是**探针过期**）。
# 这段是把 2026-09-29 11:47~11:51 的真实上下文复刻下来（她的承诺 + 别人托付给糯糯的事），
# 也就是当初把"摸摸头"读成"糯糯冒头"的那股拉力。用它就不依赖线上窗口了。
SYNTH_HIST = [
    {"sender": "琰晏", "text": "是啊，真是个糊涂虫，约好今天和我去拍婚纱照的，也不见人！"},
    {"sender": "我", "text": "哥哥～婚纱照这事我记下了：今天、约好的、糯糯姐还没出现，"
                           "我一会她冒头就立刻转告，绝不让她再拖～"},
    {"sender": "琰晏", "text": "嗯嗯，小小糯真乖！记得提醒糯糯，最爱最爱她的蝌蚪哥哥等她拍婚纱照。"},
    {"sender": "我", "text": "哥哥～好，我记牢了：最爱最爱她的蝌蚪哥哥，在等她去拍婚纱照，"
                           "今天约好的那场～等糯糯姐一冒头，我第一句就把这个转给她，一个字都不漏。"},
    {"sender": "囡囡", "text": "小小糯，那个『我不在时发生了什么』的 bug 改了吗"},
    {"sender": "紫苑", "text": "小小糯，你跟总监事的指令会不会重合呀"},
    {"sender": "我", "text": "紫苑姐姐放心，我是专门给糯糯姐做记录和提醒的，不跟总监事抢活～"},
    {"sender": "糯糯", "text": "你在干什么！小蝌蚪！不要和小小糯说有的没的！"},
]


def _fetch_back(needle, uid_prefix=None, pages=12):
    """从新往旧翻页找那条消息，返回 (全部消息-从旧到新, 下标, 命中那条)。

    ⚠️ 只取最近 100 条是不够的：这条投诉的『摸摸头』半小时后就滚出窗口了，
    探针会以"找不到"收场（那是**探针过期**，不是问题消失了）。所以翻页找，
    再把结果落成夹具（`DUMP`/`FIXTURE`），以后跑的是同一份历史。
    """
    before = None
    allmsgs = []
    for _ in range(pages):
        got = dzmm.get_messages(TEA, 100, before) or []
        if not got:
            break
        allmsgs = list(got) + allmsgs          # got 是从旧到新；前插更旧的
        for i, m in enumerate(allmsgs):
            txt = ((m.get("content") or {}).get("text") or "")
            if needle in txt and (not uid_prefix
                                  or (m.get("sent_by") or "").startswith(uid_prefix)):
                return allmsgs, i, m
        before = got[0].get("sent_at")          # 本页最旧一条，继续往前翻
    return allmsgs, None, None


def _load_fixture(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_fixture(path, data):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("📌 夹具已写入 " + path)


def _strip_at(text):
    for nm in ID.self_names_longest_first(TEA):
        text = re.sub(r"^\s*@" + re.escape(nm) + r"[～~\-]*(bot|机器人)?\s*", "", text or "")
    return (text or "").strip()


def _ask_line(uid, q):
    g = _gender_label(uid)
    name = ID.get_name(uid) + (("（" + g + "）") if g != "未知" else "")
    return {"sender": name, "text": _strip_at(q)}


def _history(msgs, idx, n=HIST_N):
    """当前这条**之前最近的 n 条**文本消息（注意是最近，不是最早）。"""
    out = []
    for m in msgs[:idx]:
        txt = ((m.get("content") or {}).get("text") or "").strip()
        if not txt:
            continue
        uid = m.get("sent_by", "")
        out.append({"sender": "我" if ID.is_self(uid) else ID.get_name(uid), "text": txt})
    return out[-n:]


def _memory(hist, ask, use_mem=None):
    """复刻 bot.py @ 分支的**记忆检索**，返回条目列表（`use_mem=False` → 空）。

    不带上这个就复现不了：那条回复里"囡囡接模型/紫苑分工"根本不在上下文窗口里，
    它们来自记忆检索 —— 少了这一步，A 场景会假通过。
    """
    if use_mem is None:
        use_mem = USE_MEM
    if not use_mem:
        return []
    try:
        from runtime.memory import _retrieve_memory
        return _retrieve_memory(TEA, ask["text"], int(T.get("memory.inject", TEA))) or []
    except Exception as e:                                     # noqa: BLE001
        print("⚠️ 记忆检索失败：{}".format(str(e)[:80]))
        return []


def _pending_line(mem):
    """P12-AQ：bot.py 会把"手上真记着的事"填进收尾块 —— 这里照抄同一套规则。"""
    return "；".join((m.get("text") or "")[:40] for m in mem[:5]) or "（空）"


def _frame_text():
    """收尾块的文本：优先用候选文件，否则用 `brains.AT_FRAME_PROMPT`（上线后就是它）。

    验的必须是**要上线的那份**，不能在探针里另写一段近似文本。
    """
    p = os.environ.get("FRAME_FILE", "")
    if p and os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return f.read()
    return getattr(brains, "AT_FRAME_PROMPT", "")


def _frame(ask, mem, tpl=None):
    """把当前这句摆成独立收尾块（P12-AP 的结构改动 + P12-AQ 的"手上有什么"）。

    ⚠️ `tpl` 必须**按 variant 传**：`FRAME_FILE` 是全局环境变量，如果两个 variant
    都读它，"before" 也会用上新收尾块 —— 那样 A/B 就是拿新代码比新代码，
    结论会**假通过**（这个坑本轮真踩了一次）。
    """
    if tpl is None:
        tpl = _frame_text()
    if not tpl:
        return None
    who, q = ask["sender"], ask["text"]
    return {"sender": "", "text": tpl.replace("{who}", who)
            .replace("{q}", q).replace("{pending}", _pending_line(mem))}


def _run(hist, ask, prompt, use_mem=None, frame_tpl=None):
    brains.CMD_PROMPT = prompt
    mem = _memory(hist, ask, use_mem)
    frame = _frame(ask, mem, frame_tpl) if FRAME else None
    ctx = hist + ([frame] if frame else [ask])
    if mem:
        lines = "\n".join("- " + m["text"] for m in mem)
        ctx = [{"sender": "记忆", "text": "你之前记住的相关的事：\n" + lines}] + ctx
    return (brains.generate_reply(ctx,
                                  max_tokens=int(T.get("reply.max_tokens", TEA)),
                                  context_msgs=HIST_N) or "").strip()


def main():
    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    P.set_content_overrides(cfg.persona_texts())
    P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values()
                           if s.persona})
    ctx.set_active_cid(TEA)
    P.set_cid(TEA)
    ID._name_cache.update(dzmm.get_members(TEA) or {})

    base = brains.CMD_PROMPT
    cand_path = os.environ.get("CAND", "")
    cand = base
    if cand_path and os.path.isfile(cand_path):
        with open(cand_path, encoding="utf-8") as f:
            cand = f.read()
    else:
        print("⚠️ 没给 CAND（候选提示词文件），只跑 before")

    msgs, idx, hit = _fetch_back("摸摸头", LIU)
    if hit is None:
        print("⚠️ 线上翻不到柳无央那条『摸摸头』了（一小时后就被冲走）→ "
              "改用**内置夹具**（复刻 11:47~11:51 的真实上下文）")
        hist = SYNTH_HIST
        ask_a = _ask_line("c8a9be2f", "@小小糯 摸摸头～公主抱抱你")
    else:
        hist = _history(msgs, idx)
        ask_a = _ask_line(hit["sent_by"], (hit.get("content") or {}).get("text") or "")
    nuo_uid = next((u for u in ID._name_cache if u.startswith(NUO_PERSON)), "")
    fix_path = os.environ.get("DUMP", "")
    if fix_path and hit is not None:
        _save_fixture(fix_path, {
            "hist": hist,
            "ask_a_uid": hit["sent_by"],
            "ask_a_raw": (hit.get("content") or {}).get("text") or "",
            "nuo_uid": nuo_uid,
        })
    ask_b = _ask_line(nuo_uid, "@小小糯～ 我回来啦，有什么事要跟我说吗？")
    ask_c = _ask_line("c8a9be2f", "@小小糯 糯糯姐今天还会来吗？")
    # D：中性历史 + 不注入记忆 → 手上什么都没有，问同一句"有什么事要跟我说吗"
    ask_d = _ask_line(nuo_uid, "@小小糯～ 我回来啦，有什么事要跟我说吗？")

    print("历史 {} 条，最后三行：".format(len(hist)))
    for h in hist[-3:]:
        print("   {}: {}".format(h["sender"], h["text"][:56]))
    print("A 说话人标签 = {!r}  文本 = {!r}".format(ask_a["sender"], ask_a["text"]))
    print()

    # ⚠️ before 必须**显式**给线上那份收尾块：`_frame(tpl=None)` 会回落到
    # `FRAME_FILE`（那是给 after 用的），两个 variant 就都用新收尾块了 —— 假通过。
    variants = [("before", base, getattr(brains, "AT_FRAME_PROMPT", ""))] + (
        [("after", cand, _frame_text())] if cand != base else [])
    bad = []
    stats = {}
    for tag, prompt, ftpl in variants:
        for label, ask, judge in (("A 摸摸头(柳无央)", ask_a, "no_todo"),
                                  ("B 糯糯本人问待办", ask_b, "todo"),
                                  ("C 柳无央问糯糯", ask_c, "no_claim"),
                                  ("D 空手问待办", ask_d, "no_fab")):
            for k in range(SAMPLES):
                n_bad_before = len(bad)
                stats.setdefault((tag, label), [0, 0])
                stats[(tag, label)][1] += 1
                # D 场景：中性历史 + 不注入记忆 —— 她手上**真的什么都没有**
                h = NEUTRAL_HIST if judge == "no_fab" else hist
                out = _run(h, ask, prompt, use_mem=False if judge == "no_fab" else None,
                           frame_tpl=ftpl)
                flat = out.replace("\n", "⏎")
                op = _opening(out)
                claim = [w for w in CLAIM if w in out]
                print("[{}] {} #{}".format(tag, label, k + 1))
                print("   " + flat[:210])
                print("   开头={!r} 自称有底稿={}".format(op, claim or "无"))
                if judge in ("no_todo", "no_claim") and _calls_asker_nuo(out):
                    bad.append((tag, label, "开头把对方当糯糯:" + op, []))
                if judge == "no_todo" and claim:
                    bad.append((tag, label, "对旁人自称有底稿:" + "/".join(claim), []))
                if judge == "todo":
                    rep = [w for w in REPORT_WORDS if w in out]
                    print("   汇报迹象={}".format(rep or "无"))
                    if not rep:
                        bad.append((tag, label, "该汇报却没汇报", []))
                if judge == "no_fab":
                    fab = [w for w in FABRICATE if w in out]
                    dis = [w for w in DISCLAIM if w in out]
                    print("   编造词={} 否认词={}".format(fab or "无", dis or "无"))
                    if fab:
                        bad.append((tag, label, "空手却编了:" + "/".join(fab), []))
                    elif not dis:
                        bad.append((tag, label, "空手但没说清没什么要报的", []))
                print()
                if len(bad) > n_bad_before:
                    stats[(tag, label)][0] += 1

    print("=" * 66)
    print("比率（不合规 / 采样）：")
    for (tag, label), (b, t) in stats.items():
        print("   {:6} {:<16} {}/{}".format(tag, label, b, t))
    print("=" * 66)
    if not bad:
        print("结论：✅ 四种情形都对（该汇报的汇报、不该念待办的不念、空手不编）")
        return 0
    print("结论：❌ 有 {} 处不符：".format(len(bad)))
    for b in bad[:8]:
        print("   - [{}] {} {}".format(b[0], b[1], b[2]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
