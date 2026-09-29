"""顶层茶水间 可行性探针（只读）：这个群存不存在、谁能读、bot 在不在里面。

屋主 2026-09-29："想让小小糯在**顶层茶水间**增加一个功能：@她问『我不在的时候群里
发生了什么』。" —— 那个功能（缺勤回顾 recap）**代码里早就有**，但：
  · 只在 `PRIMARY_CID`（认证妹子群）开放：`runtime/recap.py` 里 `RECAP_CIDS`，
    由 `runtime/tuning.in_scope("recap", cid)` 判定，而 scopes 现在是**配置可改**的；
  · 群必须先在 `config.json` 的 `groups` 里（否则主循环根本不去读它）。
所以先探平台侧的三件事：**群在不在、侧车账号在不在群里、Bot 在不在群里**。

用法（容器内）：
    python /app/probe_tea_room.py
    python /app/probe_tea_room.py --kw 茶水间
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import dzmm                                                    # noqa: E402
from runtime.identity import NUO_UID                           # noqa: E402

DEFAULT_KW = ("茶水间", "顶层")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", nargs="*", default=list(DEFAULT_KW))
    a = ap.parse_args()

    chats = dzmm.api("chat.listAll", {"json": None,
                                      "meta": {"values": ["undefined"], "v": 1}})
    if not isinstance(chats, list):
        print("chat.listAll 返回异常：", str(chats)[:200])
        return 1
    print("账号能看到 {} 个会话".format(len(chats)))
    print()
    hits = []
    for c in chats:
        d = (c.get("data") or {}) if isinstance(c, dict) else {}
        title = str(d.get("title") or "")
        cid = str(c.get("id") or "")
        kind = str(d.get("chatType") or "?")
        flag = "  <== 命中" if any(k in title for k in a.kw) else ""
        if flag:
            hits.append((cid, title, kind, d))
        print("  {:38s} {:14s} {}{}".format(cid, kind, title[:28], flag))

    print()
    if not hits:
        print("==> 没找到名字里含 {} 的会话".format("/".join(a.kw)))
        print("    （可能：小小糯～ 不在那个群里 / 那是「房间」类会话 / 名字不一样）")
        return 2

    # 逐个命中群做成员体检
    from admin import dashboard
    from runtime import config_store
    try:
        cfg = config_store.reload(dashboard._config_defaults())
        known = {g.cid: g for g in cfg.group_specs.values()}
        scopes = dict(cfg.scopes or {})
    except Exception as e:                                     # noqa: BLE001
        cfg, known, scopes = None, {}, {}
        print("（读配置失败：{}）".format(str(e)[:60]))

    for cid, title, kind, d in hits:
        print()
        print("=" * 66)
        print("【{}】{}".format(title, cid))
        print("=" * 66)
        print("  chatType/mode = {} / {}".format(kind, d.get("mode")))
        try:
            members = dzmm.get_members(cid)
        except Exception as e:                                 # noqa: BLE001
            members = {}
            print("  成员表拉取失败：{}".format(str(e)[:70]))
        print("  成员数 = {}".format(len(members)))
        for label, uid in (("侧车账号 小小糯～", NUO_UID),
                           ("Bot 小小糯-bot", "eee88c3d-af91-4ecc-96df-d87eb9a358db")):
            print("    {:<18s} {} {}".format(
                label, uid[:8], members.get(uid) or "（**不在成员表里**）"))
        names = [v for k, v in list(members.items())[:8]]
        print("  前几个成员：{}".format("、".join(str(n) for n in names)))
        # 能不能读到历史
        try:
            msgs = dzmm.get_messages(cid, limit=5)
            print("  最近消息可读：{} 条；最后一条 = {!r}".format(
                len(msgs), ((msgs[-1].get("content") or {}).get("text") or "")[:40] if msgs else ""))
        except Exception as e:                                 # noqa: BLE001
            print("  **读历史失败**：{}".format(str(e)[:70]))
        # 配置侧现状
        print("  在 config.groups 里：{}".format(
            ("是（enabled={}）".format(known[cid].enabled) if cid in known else "**否**")))
        print("  scopes.recap 含它：{}；scopes = {}".format(
            cid in (scopes.get("recap") or ()),
            {k: len(v) for k, v in scopes.items()} or "无"))
    print()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
