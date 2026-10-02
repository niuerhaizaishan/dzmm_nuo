# -*- coding: utf-8 -*-
"""只读探针：某个群的 **chatType / 现有 Bot / 我的角色**（判断能不能给它加 Bot）。

背景（P12-AZ）：茶水间没有 Bot ⇒ 只能走账号通道（打字 ≤200 字），运势卡被切成两条；
屋主要"和妹子群一样一条"。除"把卡缩短"，唯一能真的一条发完的正路是
**给茶水间也绑一个 Bot**（Bot API 单条 10000 字）。

能不能绑，取决于平台侧的三个事实（`tools/add_bot_to_chatroom.py` 的文档）：

1. `chatType` 是不是 `group`（**scenario 群不展示 Add Bot 入口**，加不了）；
2. 群里现在有哪些 Bot（`isBot`），目标 Bot 是不是已经在里面；
3. **我（账号）在这个群是不是群主/管理员** —— 私有 Bot 只有创建者本人能加，
   且必须是目标群的管理员（`chatroom.getMembers` 的 `metadata.role`）。

用法（容器内）：`python /app/probe_room_bots_roles.py`
"""
import json
import sys

sys.path.insert(0, "/app")

from dzmm import api, get_cookies, get_members                     # noqa: E402
from runtime import identity as ID                                 # noqa: E402

ROOMS = [
    ("顶层茶水间", "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"),
    ("认证妹子群", "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"),
    ("bot测试", "f71ef7ff-5faf-4e5d-9dd6-8e930c574de3"),
]


def main():
    cookies = get_cookies()
    chats = api("chat.listAll",
                {"json": None, "meta": {"values": ["undefined"], "v": 1}}, cookies)
    by_id = {}
    if isinstance(chats, list):
        for c in chats:
            by_id[c.get("id")] = c
    print("账号能看到 {} 个会话".format(len(by_id)))
    print()

    for label, cid in ROOMS:
        c = by_id.get(cid) or {}
        d = (c.get("data") or {}) if isinstance(c, dict) else {}
        print("=" * 62)
        print("{}  {}".format(label, cid))
        print("  title={!r} chatType={!r} memberCount={} creatorId={}".format(
            d.get("title"), d.get("chatType"), d.get("memberCount"),
            (d.get("creatorId") or "")[:8]))
        if d:
            print("  data 字段: {}".format(sorted(d.keys())))

        raw = api("chatroom.getMembers",
                  {"json": {"chatroomId": cid, "limit": 1000}}, cookies)
        ms = []
        try:
            # `dzmm.api` 已经把 `{json: ...}` 那一层拆掉了，members 就在顶层
            ms = (raw or {}).get("members") or []
        except Exception:                                          # noqa: BLE001
            print("  getMembers 结构不认识：", str(raw)[:200])
        print("  成员拿到 {} 个（get_members 归一化后 {} 个）".format(
            len(ms), len(get_members(cid) or {})))
        bots = [m for m in ms if m.get("isBot")]
        print("  群里的 Bot：{}".format(
            [(m.get("id", "")[:8], m.get("fullName")) for m in bots] or "（无）"))
        roles = {}
        for m in ms:
            r = ((m.get("metadata") or {}).get("role") or "")
            if r:
                roles.setdefault(r, []).append(m.get("fullName") or (m.get("id") or "")[:8])
        print("  角色分布：{}".format({k: v[:8] for k, v in roles.items()}))
        cid_owner = (d.get("creatorId") or "")
        owner_name = next((m.get("fullName") for m in ms if m.get("id") == cid_owner), "?")
        print("  群主：{} ({}…)".format(owner_name, cid_owner[:8]))
        want = {ID.NUO_UID, ID.SIDECAR_UID} if hasattr(ID, "SIDECAR_UID") else {ID.NUO_UID}
        for m in ms:
            if (m.get("id") or "") in want or (m.get("fullName") or "").startswith("小小糯") \
                    or (m.get("fullName") or "").startswith("糯糯"):
                print("  我：id={} name={!r} isBot={} role={!r} metadata={}".format(
                    (m.get("id") or "")[:8], m.get("fullName"), m.get("isBot"),
                    ((m.get("metadata") or {}).get("role")),
                    json.dumps(m.get("metadata") or {}, ensure_ascii=False)[:160]))
        print("  （账号 uid = {}）".format(ID.NUO_UID))
    return 0


if __name__ == "__main__":
    sys.exit(main())
