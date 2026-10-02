# -*- coding: utf-8 -*-
"""P12-BE 探针：**实际送进模型的那 20 条上下文**里到底有几个说话人。

屋主 2026-09-30：
> "如果我回复她，她会读取之前很多人的记录，一一批判回复。
>  能设置成那种，我回复她，她只会读取我之前的和她的记录，来进行回复嘛"

她答话时用的上下文 = `_TURNS[cid]`（进程内滚动记录：**全群每个人 + 别的 bot + 她自己**），
取最近 `reply.context_msgs`（默认 20）条。这个探针把一个群**此刻**的上下文原样打印出来：
说话人分布、有没有别的 bot、以及每条的来源。

用法（容器内）：`python /app/probe_ctx_scope.py <cid> [n]`
"""
import sys

sys.path.insert(0, "/app")

sys.argv = sys.argv[:1] + sys.argv[1:]
import bot                                                     # noqa: E402
import dzmm                                                    # noqa: E402
from runtime import identity as ID                             # noqa: E402

DEFAULT_CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"          # 上班群（若瑾）


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CID
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    # 先认 Bot（她自己 + 别人家的），否则 is_self 判不准、说话人会数错
    try:
        from admin import dashboard
        from runtime import config_store
        cfg = config_store.reload(dashboard._config_defaults())
        ID.register_bots(getattr(cfg, "bots", []) or [])
    except Exception as e:                                     # noqa: BLE001
        print("（注册 Bot 身份失败：{}）".format(str(e)[:60]))
    ID._name_cache.update(dzmm.get_members(cid) or {})
    bots = set()
    try:
        raw = dzmm.api("chatroom.getMembers", {"json": {"chatroomId": cid, "limit": 1000}})
        for m in (raw or {}).get("members") or []:
            if m.get("isBot"):
                bots.add(m.get("id"))
                ID.learn_bot_uid(m.get("id"), m.get("fullName") or "")
    except Exception:                                          # noqa: BLE001
        pass

    bot._seed_turns(cid)                       # 阻塞读平台（探针里无所谓）
    ctx = bot._recent_turns(cid, n)
    print("群 {}：上下文 {} 条（reply.context_msgs 默认 20）".format(cid[:8], len(ctx)))
    who = {}
    for c in ctx:
        s = c["sender"] or "（系统块）"
        who[s] = who.get(s, 0) + 1
        print("  {:<12} | {}".format(s, c["text"].replace("\n", "⏎")[:64]))
    others = [k for k in who if k not in ("我", "（系统块）")]
    print()
    print("不同说话人 {} 个：{}".format(len(others), others))
    print("其中别的 bot：{}".format([k for k in others if k in bots] or "（按 isBot 名单没认出来）"))
    mine = who.get("我", 0)
    print("她自己占 {} 条 / 别人占 {} 条".format(mine, len(ctx) - mine))
    return 0


if __name__ == "__main__":
    sys.exit(main())
