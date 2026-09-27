"""`reply.max_tokens` 到底是不是**卡住长度的那个天花板**？（P12-AG 先量再改）

`max_tokens` 只是**上限**，不是目标长度：模型自己写完就停（`finish_reason="stop"`）时，
把它从 800 调到 2000 一个字都不会变。所以"回复太短"这件事必须先分清是
**被截断**（`finish_reason="length"`）还是**模型自己就写这么短**（`"stop"`）——
前者调参数有用，后者只能改提示词。

用法（容器内）：
    python /app/probe_length_ceiling.py                 # 认证群
    python /app/probe_length_ceiling.py --cid f4ec4c21 --n 3
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import brains                                                  # noqa: E402
import personas as P                                           # noqa: E402
from runtime import config_store, ctx                          # noqa: E402
from runtime import tuning as T                                # noqa: E402

AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"

QS = [
    "你怎么看异地恋啊，我有点撑不住了",
    "我最近老是失眠，有什么办法吗",
    "我跟我妈又吵架了，她老是要我考公",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default=AUTH)
    ap.add_argument("--n", type=int, default=3, help="问几个问题")
    a = ap.parse_args()

    from admin import dashboard
    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    P.set_content_overrides(cfg.persona_texts())
    P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values()
                           if s.persona})

    ctx.set_active_cid(a.cid)
    P.set_cid(a.cid)
    sp = P.apply_persona(brains.CMD_PROMPT)
    cur = int(T.get("reply.max_tokens", a.cid))
    print("群 {} 当前 reply.max_tokens = {}".format(a.cid[:8], cur))
    print("人设 extra {} 字 | 提示词 {} 字".format(
        len(((P.persona_for(a.cid) or {}).get("extra") or "")), len(sp)))
    print()

    rows = []
    for q in QS[:a.n]:
        for mt in (cur, 2000):
            t0 = time.time()
            text, finish = brains._call_llm_raw(sp, q, brains.DEFAULT_KEY, int(mt))
            n = len((text or "").strip())
            rows.append((mt, n, finish, q))
            print("  max_tokens={:<5} -> {:>4} 字 / finish={:<7} / {:.1f}s | {}".format(
                mt, n, finish, time.time() - t0, q[:22]))
        print()

    print("=" * 66)
    cut = [r for r in rows if r[2] == "length"]
    print("被截断（finish=length）的条数：{}/{}".format(len(cut), len(rows)))
    by_mt = {}
    for mt, n, _f, _q in rows:
        by_mt.setdefault(mt, []).append(n)
    for mt, ns in sorted(by_mt.items()):
        print("  max_tokens={:<5} 平均 {} 字 {}".format(mt, sum(ns) // len(ns), ns))
    print()
    if cut:
        print("=> 有截断：调大 reply.max_tokens（或给这条链路开自动续写）确实有用")
    else:
        print("=> 没有截断：800 不是天花板，改参数**不会**让话变长，得改提示词/示例")
    return 0


if __name__ == "__main__":
    sys.exit(main())
