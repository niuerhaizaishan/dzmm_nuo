"""成员名册：快照对账 + 风控后补筛（P3-10 从 bot.py 拆出）。

为什么需要对账：会话真死 / 风控墙期间读不到消息，连"X 加入了群聊"都看不见，
`pending_screen` 里也不会有记录（那是"看见了、但查不了资料"才入队的）；
入群消息一旦滚出补拉窗口，新人就**永久漏掉**。平台不给入群时间，判据只能是
成员列表快照：

  · 首次跑（无快照）⇒ **只建快照、绝不筛查**（否则整群被当新人）；
  · 之后 ⇒ `当前成员 − 快照` = 新增 ⇒ 照常筛查；
  · 快照只增不减（退群的人留着，免得再进群被重筛）。

**不持有 bot 侧状态**：筛查链（handle_joins）、发送（send）、欢迎话术
（welcome_of/personas）、昵称（get_full_name/is_self）、群状态读写
（load_state/save_group）全部 kw-only 注入 —— monkeypatch `bot.*` 的
既有接缝（test_recovery_ownership）保持不变。
"""
import asyncio

import state_store
from runtime.obs import log


__all__ = ["reconcile_members", "rescreen_pending", "remember_members",
           "KNOWN_MEMBERS_KEY"]


KNOWN_MEMBERS_KEY = "known_members"     # P12-BJ：本群见过的成员快照（uid 列表）


def remember_members(cid: str, uids) -> None:
    """把**刚处理过**的成员记进快照（只增不减，与对账同口径）。

    ⚠️ 2026-10-02：原先快照**只有 `reconcile_members` 会写**，入群事件那条路从不更新它
    ⇒ 正常入群的人在下一次容器重启后**仍算"没见过"** ⇒ 被对账又筛一遍、再欢迎一遍。
    实测：08:43:14 已经正常欢迎过的人，08:46:14 又被成员对账捡起来当新成员处理
    （那次还因为闸门是假的被踢了）。
    """
    uids = [u for u in (uids or []) if u]
    if not uids:
        return
    known = set(state_store.group_get(cid, KNOWN_MEMBERS_KEY, []) or [])
    state_store.group_set(cid, KNOWN_MEMBERS_KEY, sorted(known | set(uids)))


async def reconcile_members(sender, cid: str, dry: bool, *,
                            handle_joins, send, welcome_of, personas,
                            get_full_name, is_self) -> list:
    """P12-BJ：**成员快照对账** —— 补那些"入群事件我们压根没看见"的新成员。

    为什么需要：会话真死 / 风控墙期间**读不到消息**，连"X 加入了群聊"都看不见
    ⇒ `pending_screen` 里也不会有记录（那个队列是"看见了、但查不了资料"才入队的）。
    2026-09-30 的现场：13:12 新人入群，直到 13:29 会话恢复、消息补拉才被筛查 ——
    如果那条入群消息已经滚出补拉窗口，就会**永久漏掉**。

    平台没给入群时间（成员字段只有 id/fullName/avatar/bio/isBot/gender/metadata），
    所以判据只能是**成员列表快照**：

      · 首次跑（还没有快照）⇒ **只建快照、绝不筛查**（否则整群都会被当成新人踢掉）；
      · 之后每次 ⇒ `当前成员 − 快照` = 新增 ⇒ 交给 `handle_joins` 照常筛查；
      · 快照只增不减（退群的人留着，免得他再进群被当新人重筛一遍）。

    返回真正处理过的 uid 列表（空 = 没有新成员）。
    """
    try:
        from dzmm import api as _api
        raw = await asyncio.to_thread(
            _api, "chatroom.getMembers", {"json": {"chatroomId": cid, "limit": 1000}})
        members = (raw or {}).get("members") or []
    except Exception as e:                                     # noqa: BLE001
        log("  ⚠️ [" + cid[:8] + "] 成员对账：拉成员表失败，" + str(e)[:60])
        return []
    cur = [m.get("id") for m in members
           if m.get("id") and not m.get("isBot") and not is_self(m.get("id"))]
    known = set(state_store.group_get(cid, KNOWN_MEMBERS_KEY, []) or [])
    if not known:
        state_store.group_set(cid, KNOWN_MEMBERS_KEY, cur)
        log("  📇 [" + cid[:8] + "] 建立成员快照（" + str(len(cur)) + " 人），本轮不筛查")
        return []
    new = [u for u in cur if u not in known]
    if not new:
        return []
    # 先落快照再筛查：筛查里会发消息/踢人，失败也不该让同一批人反复触发
    state_store.group_set(cid, KNOWN_MEMBERS_KEY, sorted(known | set(cur)))
    log("  📇 [" + cid[:8] + "] 成员对账：发现 " + str(len(new))
        + " 名没见过的成员（可能是断线期间入群的），开始筛查")
    passed = await handle_joins(sender, cid, new, dry)
    for jid in passed:
        jname = get_full_name(jid)
        await send(sender, cid, "🤖 " + personas.fill(welcome_of(cid), cid).replace(
            "{name}", jname), dry)
    return new


async def rescreen_pending(sender, cid: str, dry: bool, *,
                           handle_joins, send, welcome_of, personas,
                           get_full_name, load_state, save_group) -> None:
    """风控解除后补筛查撞验证码期间延迟的新成员。"""
    state = load_state()
    gs = state.get(cid) or {}
    pending = list(gs.get("pending_screen") or [])
    if not pending:
        return
    gs["pending_screen"] = []
    state[cid] = gs
    save_group(cid, gs)
    log("🔄 [" + cid[:8] + "] 风控解除，补筛查 " + str(len(pending)) + " 名新成员")
    passed = await handle_joins(sender, cid, pending, dry)
    for jid in passed:
        jname = get_full_name(jid)
        await send(sender, cid, "🤖 " + personas.fill(welcome_of(cid), cid).replace("{name}", jname), dry)
