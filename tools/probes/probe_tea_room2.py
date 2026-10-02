"""顶层茶水间 深挖（只读）：到底能不能读、能不能发、群里多热闹。

第一轮探针（`probe_tea_room.py`）结果：
    顶层茶水间--泛亚太色色摸鱼[G]公司  cid=4fc4a2fd-465a-4436-9884-dbb5e60c6af2
    101 人；侧车账号 503e3cb9 与 Bot eee88c3d **都不在成员表里**；
    可是账号能在 chat.listAll 里看到它、也能读到最近消息；配置里没这个群。

"不在成员表却读得到"必须查清 —— 它决定了这个功能是"加个配置就能上"还是
"得先请人把账号/Bot 拉进群"。顺手把群的热度也量出来（recap 要把一窗口的消息喂给模型，
群越热闹越贵越慢）。

用法（容器内）：
    python /app/probe_tea_room2.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import dzmm                                                    # noqa: E402
from runtime.identity import NUO_UID                           # noqa: E402

CID = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
BOT_UID = "eee88c3d-af91-4ecc-96df-d87eb9a358db"


def main():
    # 1) 会话原始数据（看有没有 scenario / room 之类的标记）
    chats = dzmm.api("chat.listAll", {"json": None,
                                      "meta": {"values": ["undefined"], "v": 1}}) or []
    me = next((c for c in chats if isinstance(c, dict) and c.get("id") == CID), None)
    print("== chat.listAll 里这一条 ==")
    if isinstance(me, dict):
        d = me.get("data") or {}
        print("  id   =", me.get("id"))
        print("  keys =", sorted(me.keys()))
        for k in sorted(d.keys()):
            v = str(d.get(k))
            print("  data.{:<18s} {}".format(k, v[:70]))
    else:
        print("  **没找到**（账号看不到这个会话？）")

    # 2) 成员表：按 uid 与名字两种方式找
    print()
    print("== 成员表核查 ==")
    try:
        members = dzmm.get_members(CID) or {}
    except Exception as e:                                     # noqa: BLE001
        members = {}
        print("  getMembers 失败：", str(e)[:80])
    print("  成员数 =", len(members))
    print("  按 uid 找 503e3cb9 :", repr(members.get(NUO_UID)))
    print("  按 uid 找 eee88c3d :", repr(members.get(BOT_UID)))
    hits = [(u, n) for u, n in members.items() if "小小糯" in str(n) or "若瑾" in str(n)]
    print("  名字里含「小小糯/若瑾」的成员 :", hits or "无")
    # 头像/资料接口：在群里能不能查到这个账号
    for label, uid in (("侧车账号", NUO_UID), ("Bot", BOT_UID)):
        try:
            info = dzmm.get_user(CID, uid)
            print("  getUser({}) = {}".format(label, str(info)[:110] if info else "None"))
        except Exception as e:                                 # noqa: BLE001
            print("  getUser({}) 失败：{}".format(label, str(e)[:60]))

    # 3) 热度：最近 100 条覆盖多长时间
    print()
    print("== 群热度（决定 recap 的耗时代价）==")
    try:
        msgs = dzmm.get_messages(CID, limit=100) or []
        if msgs:
            t0, t1 = msgs[0].get("sent_at", ""), msgs[-1].get("sent_at", "")
            print("  最近 100 条：{}  →  {}".format(str(t0)[:19], str(t1)[:19]))
            span = 0.0
            try:
                from datetime import datetime
                a = datetime.fromisoformat(str(t0).replace(" ", "T").replace("+00", ""))
                b = datetime.fromisoformat(str(t1).replace(" ", "T").replace("+00", ""))
                span = (b - a).total_seconds() / 3600.0
            except Exception:
                pass
            if span > 0:
                print("  覆盖 ≈ {:.1f} 小时 → 平均 {:.0f} 条/小时".format(span, len(msgs) / span))
            print("  最近 3 条：")
            for m in msgs[-3:]:
                txt = ((m.get("content") or {}).get("text") or "").replace("\n", " ")
                print("    [{}] {}".format(str(m.get("sent_by"))[:8], txt[:60]))
    except Exception as e:                                     # noqa: BLE001
        print("  读历史失败：", str(e)[:80])

    # 4) 这个群在不在配置里
    print()
    print("== 配置现状 ==")
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        print("  config.groups 里的群数 =", len(cfg.group_specs))
        print("  含顶层茶水间 =", CID in cfg.group_specs)
        print("  scopes =", dict(cfg.scopes or {}) or "无")
        print("  bots =", [(b.key, b.cid[:8]) for b in cfg.bots])
    except Exception as e:                                     # noqa: BLE001
        print("  读配置失败：", str(e)[:80])
    print()
    print("done")


if __name__ == "__main__":
    sys.exit(main())
