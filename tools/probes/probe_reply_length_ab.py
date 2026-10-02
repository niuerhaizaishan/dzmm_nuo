"""P12-AU 取证：茶水间 @ 闲聊回复为什么变短（真调模型，不发送）。

屋主 2026-09-29 晚："**茶水间@小小糯 闲聊回复变短了！**"
线上实测（平台读原件）：18:43/18:44/18:45 三条闲聊回复 **32 / 43 / 60 字**，
而上午同类回复是 150~340 字。

嫌疑：P12-AP/P12-AQ 往提示词和收尾块里加了一批"克制"规则 ——
"只回答最后一行那句话""没有实质内容时就只接住这一句""别接着上一轮往下讲"
"没人问就别主动汇报"。这些本来是为了治"摸摸头被读成糯糯冒头"，
但可能把**正常闲聊**也一起压短了。

所以这里把两处改动拆开量（同一批问题、各 3 次采样）：

    A 旧提示词 + 无收尾块   ← P12-AP 之前的行为
    B 新提示词 + 无收尾块   ← 只看提示词规则的影响
    C 新提示词 + 收尾块     ← 现在线上的组合

用法（容器内，需先把旧提示词放 /tmp/CMD_PROMPT_old.md）：

    docker cp tools/fixtures/CMD_PROMPT_pre_p12ap.md dzmm-bot:/tmp/CMD_PROMPT_old.md
    docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python /app/probe_reply_length_ab.py
"""
import os
import statistics
import sys
import time

APP = "/app"
for _p in (APP,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
# 改动前（P12-AP 之前）的提示词。**别再从线上备份里临时抠** ——
# 那个只躺在服务器 /tmp 里，容器一重建就没了（本会话已经因为"探针依赖易失的东西"
# 栽过一次：`probe_at_focus.py` 要捞的那条消息一小时后被冲走，探针以"找不到"收场）。
# 仓库夹具：`tools/fixtures/CMD_PROMPT_pre_p12ap.md`，跑之前 `docker cp` 成 /tmp/CMD_PROMPT_old.md。
OLD_PROMPT_FILE = os.environ.get("OLD_PROMPT", "/tmp/CMD_PROMPT_old.md")
SAMPLES = int(os.environ.get("SAMPLES", "3"))

# 茶水间里典型的"闲聊"（都是没有具体任务的那种；线上变短的就是这一类）
QS = [
    ("琰晏（男生）", "琰晏哥哥 晚上好呀"),
    ("安澜（女生）", "今天过得怎么样"),
    ("路人甲（未知）", "在干嘛呢"),
    ("墨莲（女生）", "群里好热闹啊"),
    ("琰晏（男生）", "晚饭吃了没"),
]
HIST = [
    {"sender": "云月", "text": "今天好热啊"},
    {"sender": "我", "text": "姐姐们记得多喝水呀～"},
    {"sender": "琰晏", "text": "晚上吃点啥"},
]


def main():
    import brains
    import personas as P
    from runtime import config_store, ctx, identity as ID, tuning as T
    from admin import dashboard

    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    T.set_scopes(getattr(cfg, "scopes", {}) or {})
    P.set_content_overrides(cfg.persona_texts())
    P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values()
                           if s.persona})
    ctx.set_active_cid(TEA)
    P.set_cid(TEA)

    new_prompt = brains.CMD_PROMPT
    frame = getattr(brains, "AT_FRAME_PROMPT", "")
    try:
        with open(OLD_PROMPT_FILE, encoding="utf-8") as f:
            old_prompt = f.read()
    except Exception as e:                                     # noqa: BLE001
        print("❌ 读不到旧提示词 {}：{}".format(OLD_PROMPT_FILE, str(e)[:80]))
        return 2
    print("旧提示词 {} 字 / 新提示词 {} 字 / 收尾块 {} 字".format(
        len(old_prompt), len(new_prompt), len(frame)))
    if not frame:
        print("❌ 没有 AT_FRAME_PROMPT（部署里应该有）")
        return 2

    variants = [("A 旧提示词+无收尾块", old_prompt, False),
                ("B 新提示词+无收尾块", new_prompt, False),
                ("C 新提示词+收尾块", new_prompt, True)]

    results = {}
    for vname, prompt, use_frame in variants:
        brains.CMD_PROMPT = prompt
        for who, q in QS:
            for _ in range(SAMPLES):
                c = list(HIST)
                if use_frame:
                    c.append({"sender": "", "text": frame.replace("{who}", who)
                              .replace("{q}", q).replace("{pending}", "（空）")})
                else:
                    c.append({"sender": who, "text": q})
                out = brains.generate_reply(
                    c, max_tokens=int(T.get("reply.max_tokens", TEA)),
                    context_msgs=20) or ""
                out = out.strip()
                results.setdefault((vname, q), []).append(len(out))
        print("{} 跑完".format(vname))

    print("\n" + "=" * 78)
    print("每个问题的字数（{} 次采样）".format(SAMPLES))
    print("=" * 78)
    print("{:<24}{:>26}{:>26}{:>26}".format("问题", *[v[0][:8] for v in variants]))
    for _who, q in QS:
        row = [results.get((v[0], q), []) for v in variants]
        print("{:<24}{:>26}{:>26}{:>26}".format(
            q[:22],
            *["{} (均{:.0f})".format(r, statistics.mean(r)) if r else "-"
              for r in row]))

    print("\n" + "=" * 78)
    print("总体")
    print("=" * 78)
    for vname, _p, _f in variants:
        vals = [n for r in results.values() for n in r
                if True] if False else [
            n for (vn, _q), r in results.items() if vn == vname for n in r]
        if not vals:
            continue
        print("{:<22} n={} 均值={:.0f} 中位={:.0f} 最短={} 最长={}  短于100字={}/{}".format(
            vname, len(vals), statistics.mean(vals), statistics.median(vals),
            min(vals), max(vals), len([x for x in vals if x < 100]), len(vals)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
