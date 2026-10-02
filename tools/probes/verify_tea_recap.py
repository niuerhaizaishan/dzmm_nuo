"""顶层茶水间 recap 验收（**不发消息**）：只看"读→过滤→转录→出稿"这条链。

屋主口径：输入要丰富完整、忽略打卡/摸鱼躲藏/记忆考核、窗口默认短一点。
这个探针把三件事一起量出来：
  1. 窗口内一共多少条 → 过滤掉多少条噪音 → 转录多少行/多少字（对比"不过滤"的样子）；
  2. 真调一次 `recap_absent()`（**不发送**，只拿文本），看三段结构与耗时；
  3. 把转录的前几行打出来，人眼确认"是不是真内容，而不是游戏播报"。

用法（容器内）：
    python /app/verify_tea_recap.py
    python /app/verify_tea_recap.py --uid <某个群友的uid> --q "我不在的时候群里发生了什么"
"""
import argparse
import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import dzmm                                                    # noqa: E402
from runtime import config_store, ctx, tuning as T             # noqa: E402
from runtime import features as F                              # noqa: E402
from runtime import recap as RC                                # noqa: E402
from runtime.identity import _load_member_names                # noqa: E402
from admin import dashboard                                    # noqa: E402

CID = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
DEFAULT_UID = "79c910d0-e0e8-40c8-9d84-ad4c6a45bd49"          # 紫苑（群创建者）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", default=DEFAULT_UID)
    ap.add_argument("--q", default="我不在的时候群里发生了什么")
    a = ap.parse_args()

    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    T.set_scopes(cfg.scopes)
    # ⚠️ 不加载功能覆盖，`F.on()` 会按**代码默认值**回答（第一版把 slash_cmds 假报成 True）
    F.set_overrides(cfg.feature_map())
    ctx.set_active_cid(CID)

    print("== 本群生效值 ==")
    print("  recap 在范围内 :", T.in_scope("recap", CID))
    print("  default_hours  :", T.get("recap.default_hours", CID))
    print("  max_msgs       :", T.get("recap.max_msgs", CID))
    print("  ignore_kw      :", repr(T.get("recap.ignore_kw", CID)))
    print("  slash_cmds     :", F.on(CID, "slash_cmds"))
    print()

    _load_member_names(CID)
    since, until, label = RC._recap_window(CID, a.uid, a.q)
    from datetime import datetime
    since_utc = datetime.utcfromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S")
    msgs = dzmm.get_messages_since(CID, since_utc, max_count=600) or []
    msgs = [m for m in msgs if ((m.get("content") or {}).get("text") or "").strip()]
    print("== 窗口：{}（{} ~ {}）==".format(
        label or "(按上次发言)",
        RC._fmt_ts(since), RC._fmt_ts(time.time())))
    print("  窗口内消息 =", len(msgs))

    kw = RC._ignore_kw(CID)
    noise = [m for m in msgs if RC._is_noise((m.get("content") or {}).get("text", ""), kw)]
    print("  其中被判为噪音 = {} 条（{:.0%}）".format(
        len(noise), (len(noise) / len(msgs)) if msgs else 0))

    kept = msgs[-int(T.get("recap.max_msgs", CID)):]
    t_new, n_new = RC._recap_transcript(kept, CID)
    saved = RC._ignore_kw
    try:
        RC._ignore_kw = lambda cid=None: ()          # 临时关掉过滤，看看"不过滤"有多脏
        t_old, n_old = RC._recap_transcript(kept, CID)
    finally:
        RC._ignore_kw = saved
    print("  转录（过滤后）  = {} 行 / {} 字".format(n_new, len(t_new)))
    print("  转录（不过滤）  = {} 行 / {} 字".format(n_old, len(t_old)))
    print()
    print("== 过滤后的转录前 8 行 ==")
    for ln in t_new.split("\n")[:8]:
        print("   " + ln[:90])
    print()

    print("== 真调一次 recap_absent（不发送）==")
    t0 = time.time()
    out = asyncio.run(RC.recap_absent(CID, a.uid, a.q))
    print("  耗时 {:.1f} 秒 / {} 字".format(time.time() - t0, len(out or "")))
    print()
    for ln in str(out or "").split("\n"):
        print("   " + ln)
    print()
    ok = bool(out) and all(k in out for k in ("💬", "🎭", "📌"))
    print("=> ", "✅ 三段齐全" if ok else "❌ 段落不全（看上面）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
