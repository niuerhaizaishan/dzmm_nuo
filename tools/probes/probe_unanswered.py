# -*- coding: utf-8 -*-
"""诊断：四个群里"最后一条别人说的话"是不是**比我们最后一条消息还新**（= 可能漏了）。

P12-BC 背景（屋主 2026-09-30："怎么 ROSE 房又没反应了"）：

玫瑰房最后一条是 `02:07:43(UTC) 亦书: 摩羯座`，而薇丝最后一条停在 `01:05:48`；
**本群 `last_id` 正好等于那条没被回答的消息** —— 而 10:07:07（= 02:07:07 UTC）
我刚好在做部署（`docker compose up -d` 重建容器）。
`last_id` 是在**生成之前**就推进的（`bot.py:3419` 注释："先推进，避免重复处理"），
进程被 SIGKILL 时异常处理来不及跑 ⇒ 那条消息就像"处理过了"一样被跳过。

这个探针把四个群的**原始事实**摆出来（谁最后说了什么、我们最后回了什么、差多久），
至于"该不该回"要按各群的规则看（茶水间 `at_only`、认证群只在 @/指令时回…）。

用法（容器内）：`python /app/probe_unanswered.py`
"""
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/app")

import dzmm                                                    # noqa: E402
import state_store                                             # noqa: E402
from runtime import identity as ID                             # noqa: E402

GROUPS = [
    ("认证妹子群", "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"),
    ("上班群", "f4ec4c21-fb48-4be2-9972-e4128f65923b"),
    ("玫瑰房", "4a56be71-9ca6-4785-b45f-18725d187f1a"),
    ("顶层茶水间", "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"),
]


def _ago(ts):
    try:
        t = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return int(time.time() - t.timestamp())
    except Exception:                                          # noqa: BLE001
        return -1


def main():
    print("现在（UTC）= {}".format(
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
    for label, cid in GROUPS:
        ID._name_cache.update(dzmm.get_members(cid) or {})
        msgs = dzmm.get_messages(cid, 30) or []
        last_me = next((m for m in reversed(msgs) if ID.is_self(m.get("sent_by") or "")), None)
        last_them = next((m for m in reversed(msgs)
                          if not ID.is_self(m.get("sent_by") or "")), None)
        print("=" * 66)
        print("{}  last_id={}".format(label, (state_store.group_get(cid, "last_id", "") or "")[:8]))
        for tag, m in (("我们最后一条", last_me), ("别人最后一条", last_them)):
            if not m:
                print("  {}：—".format(tag))
                continue
            t = (m.get("content") or {}).get("text") or ""
            print("  {}：{} {} {!r}（{}s 前）".format(
                tag, (m.get("sent_at") or "")[11:19],
                "我" if ID.is_self(m.get("sent_by") or "") else
                ID.get_name(m.get("sent_by") or "")[:10],
                t[:44].replace("\n", "⏎"), _ago(m.get("sent_at") or "")))
        if last_me and last_them:
            gap = _ago(last_me.get("sent_at") or "") - _ago(last_them.get("sent_at") or "")
            if last_them.get("sent_at") > last_me.get("sent_at"):
                print("  ⚠️ 别人那条**比我们最后一条还新**（差 {}s）→ 可能漏了".format(gap))
            else:
                print("  ✅ 我们最后一条更新（差 {}s）".format(-gap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
