"""补发：把**因为会话掉线而漏掉的 @ 回复**补上（真生成 + 走 bot 自己的出站队列）。

屋主 2026-09-29 18:10 在顶层茶水间 @ 了一句「下午群里发生了什么？」，而
15:49~18:11 账号会话是失效的（见 `docs/P12-AS-会话失效静默宕机.md`）：
**读不到消息**，所以那条 @ 从来没有被处理过。18:11 恢复后也不会自动补 ——
SIO 只推新消息，HTTP 兜底读是低频的，而 `last_id` 还停在掉线前。

这个工具做的事，等价于"她当时读到了"：

    1. 找到那条漏掉的 @（按文本/uid 匹配），拿到它的 message_id；
    2. 用**真管线**生成回复（`recap_absent`，和 @ 分支同一条路）；
    3. 把回复塞进**出站队列**（`OutboxStore.enqueue`）—— 由 bot 自己的
       `OutboxSender(RoutingSender)` 发出去（茶水间没有 Bot ⇒ 走账号通道，
       自带 190 字切分 + 读回验证）；
    4. 把本群 `last_id` 推到那条 @ 上，**防止 bot 以后重读历史时再答一遍**。

⚠️ 为什么要塞队列而不是自己发：另起一个 Playwright 会去抢 bot 正在用的
`pw_profile`（`profile appears to be in use`）。队列是 bot 与外部工具之间
唯一安全的"代为说话"通道。

用法（容器内，先 `docker cp` 进 /app）：
    python /app/send_missed_recap.py            # 只看会发什么（dry）
    python /app/send_missed_recap.py --send     # 真入队
"""
import asyncio
import os
import sys
import time

APP = "/app"
for _p in (APP, os.path.dirname(APP)):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

TEA = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"      # 顶层茶水间
NEEDLE = "下午群里发生了什么"                       # 漏掉的那句（屋里现场原文）
SENDER_HINT = "a56cbd67"                           # 发问的人（糯糯～）
DRY = "--send" not in sys.argv


def _save_cooldown(state_store, cid, uid):
    """摘掉该用户在本群的回顾冷却，返回旧值（供 dry run 放回）。"""
    try:
        st = state_store.load_state()
        g = st.get(cid) or {}
        cd = dict(g.get("recap_ts") or {})
        old = cd.pop(uid, None)
        g["recap_ts"] = cd
        st[cid] = g
        state_store.save_state(st)
        return old
    except Exception:                                          # noqa: BLE001
        return None


def _restore_cooldown(state_store, cid, uid, old):
    if not old:
        return
    try:
        st = state_store.load_state()
        g = st.get(cid) or {}
        cd = dict(g.get("recap_ts") or {})
        cd[uid] = old
        g["recap_ts"] = cd
        st[cid] = g
        state_store.save_state(st)
    except Exception:                                          # noqa: BLE001
        pass


def main():
    import dzmm
    import state_store
    from runtime import config_store, ctx, identity as ID, tuning as T
    from runtime import prompts as _prompts            # noqa: F401  确保提示词已登记
    from runtime.recap import recap_absent
    from runtime.text import _ensure_decor
    from admin import dashboard

    cfg = config_store.reload(dashboard._config_defaults())
    T.set_overrides(cfg.tuning_map())
    # ⚠️ 必须同时装 scopes：`recap` 是按**群范围**开放的（`scopes.recap`），
    # 只装 overrides 的话茶水间会被判成"不在范围内" → `recap_absent` 直接返回空。
    # （实测踩过：本工具第一版就是这样，生成结果是空字符串。）
    T.set_scopes(getattr(cfg, "scopes", {}) or {})
    ctx.set_active_cid(TEA)
    ID._name_cache.update(dzmm.get_members(TEA) or {})

    # 1) 找那条漏掉的 @
    msgs = dzmm.get_messages(TEA, 100) or []
    hit = next((m for m in msgs
                if NEEDLE in ((m.get("content") or {}).get("text") or "")
                and (m.get("sent_by") or "").startswith(SENDER_HINT)), None)
    if hit is None:
        print("❌ 最近 100 条里没找到那句 %r（可能已滚出窗口）" % NEEDLE)
        return 2
    mid = hit.get("message_id")
    uid = hit.get("sent_by")
    ask = (hit.get("content") or {}).get("text") or ""
    print("找到漏掉的 @：{}  {}: {}".format(hit.get("sent_at"), ID.get_name(uid), ask))
    print("  message_id = {}".format(mid))

    # 2) 走真管线生成（`exclude_mid` = 把提问本身排除，P12-AN 修过的那条）
    from runtime.features import on as _feat_on
    from runtime.recap import _recap_intent, _recap_window, _tune_scope
    try:
        _since, _until, _wlabel = _recap_window(TEA, uid, ask)
        print("  诊断：recap scope={} feature={} intent={} 窗口={}~{} ({})".format(
            _tune_scope("recap", TEA), _feat_on(TEA, "recap"), _recap_intent(ask),
            int(_since), int(_until or 0), _wlabel))
    except Exception as e:                                     # noqa: BLE001
        print("  诊断失败：{}".format(str(e)[:120]))
    # ⚠️ 冷却必须先摘掉：`recap_absent` 每次生成都会写 `recap_ts`（**包括 dry run**），
    # 所以连着跑两次，第二次就会拿到"这一段刚给你说完呢～"这种冷却话术 ——
    # 本工具第一版就是这么把一句冷却话术当成"补发内容"入队的。
    # 补发场景下冷却没有意义（人根本没收到过），所以这里先摘、dry run 完再放回。
    _old_cd = _save_cooldown(state_store, TEA, uid)
    reply = asyncio.run(recap_absent(TEA, uid, ask, mid)) or ""
    reply = _ensure_decor(reply.strip(), "")           # 茶水间没有 chat_decor
    if not reply:
        print("❌ 生成失败（空回复）")
        return 1
    text = "🤖 " + reply
    print("\n生成的回复（{} 字）：\n{}".format(len(text), text))

    if DRY:
        # dry run 不留副作用：把刚才为了生成而摘掉的冷却放回去
        _restore_cooldown(state_store, TEA, uid, _old_cd)
        print("\n[dry] 没有发送（冷却已还原）。要真发加 --send")
        return 0

    # 3) 真发：走 Socket.IO（HTTP），**不碰浏览器**
    #    ⚠️ 为什么不用出站队列：`OutboxSender` 是**发送期的包装器**，不是轮询 worker ——
    #    直接往 `outbound` 表里插一行，永远没人来发（本工具第一版就这么干了，
    #    那条消息在库里躺到天荒地老）。只有 bot 自己调 `sender.send()` 时才会入队+投递。
    #    另起 Playwright 也不行：会和 bot 抢 `pw_profile`（profile in use）。
    #    ⇒ 只剩 Socket.IO 这条路（P12-AN 给账号通道加读回验证的就是它）。
    from bot import ACCOUNT_MSG_MAX_CHARS, _split_for_account
    import dzmm
    from socketio import DZMMSocket
    from runtime import identity as ID

    parts = _split_for_account(text, ACCOUNT_MSG_MAX_CHARS)
    skip = 0
    if "--skip" in sys.argv:
        try:
            skip = int(sys.argv[sys.argv.index("--skip") + 1])
        except Exception:                                      # noqa: BLE001
            skip = 0
    print("\n切成 {} 段：{}{}".format(
        len(parts), [len(p) for p in parts],
        "（跳过前 {} 段）".format(skip) if skip else ""))
    sock = DZMMSocket(_origin())
    cookie_str = dzmm.get_cookie_string()
    me = ID.NUO_UID
    failed = []
    for i, part in enumerate(parts, 1):
        if i <= skip:
            continue
        # ⚠️ Socket.IO 这条路是**间歇性**抽风的（"Session ID unknown" 时好时坏），
        # 实测第 2 段连着 3 次重试全失败 —— 所以这里每段各自硬重试，
        # 而且**每段发完都读回确认**，别把"半截消息"留在群里。
        ok = False
        for attempt in range(1, 7):
            if sock.send_message(TEA, me, part, cookie_str, retries=2):
                time.sleep(2)
                if _landed(dzmm, me, part):
                    ok = True
                    break
            time.sleep(2.5)
            print("    第 {} 段第 {} 次没成，继续重试…".format(i, attempt))
        print("  第 {}/{} 段（{} 字）-> {}".format(i, len(parts), len(part),
                                              "✅ 已落地" if ok else "❌ 失败"))
        if not ok:
            failed.append(i)

    if failed:
        print("\n❌ 这些段没发出去：{}（再跑一次 `--send --skip {}` 可只补这些）".format(
            failed, failed[0] - 1))
        return 1

    # 5) 推进 last_id，避免 bot 以后重读历史再答一遍
    old = state_store.group_get(TEA, "last_id", "")
    state_store.group_set(TEA, "last_id", mid)
    print("\n✅ 全部落地。last_id: {} -> {}（防止重复应答）".format(
        (old or "")[:8], (mid or "")[:8]))
    return 0


def _landed(dzmm, me, part) -> bool:
    """这段到底进群了没有（拿前 24 字当指纹）。账号通道必须读回，别信返回值。"""
    try:
        got = dzmm.get_messages(TEA, 20) or []
        head = part[:24]
        return any(((m.get("content") or {}).get("text") or "").startswith(head[:12])
                   or head in ((m.get("content") or {}).get("text") or "")
                   for m in got if (m.get("sent_by") or "") == me)
    except Exception:                                          # noqa: BLE001
        return False


def _origin():
    import env_config
    return env_config.get_dzmm_origin()


if __name__ == "__main__":
    sys.exit(main())
