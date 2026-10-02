"""inflight：「正在处理」标记与重启重放（P3-14 从 bot.py 拆出）。

三个分支都是**先把 last_id 推到最新、再去生成回复**，异常时靠 `except` 回滚
（rollback_last_id）。但**进程被 SIGKILL（容器重建）时 except 根本来不及跑** ⇒
`last_id` 停在最新那条上 ⇒ 重启后"它看起来已经处理过了" ⇒ 那条消息**永远不会
被再读**（= 静默丢一条回复；屋主 2026-09-30 "怎么 ROSE 房又没反应了" 的现场）。

对策：推进 last_id 的同时落一个"正在处理"的标记（落盘）；正常跑完那一刻清掉；
启动时 recover_inflight 若发现标记还在、而且**群里在那条之后没有我们发的话**，
就把 last_id 退回它前面一条 ⇒ 交回主循环重放（读回判据来自 P12-AN 的老规矩：
只看群里有什么）。

**不持有 bot 侧状态**：状态读写（load_state/save_group）、平台读口
（get_messages/is_self —— test_inflight patch 的是 `bot.get_messages`/`bot.is_self`）
与日志出口（log —— test_inflight patch `bot.log` 断言日志内容）全部 kw-only
注入 → monkeypatch `bot.*` 的既有接缝保持不变。
"""
import time


__all__ = ["rollback_last_id", "mark_inflight", "clear_inflight",
           "recover_inflight", "INFLIGHT_KEY", "INFLIGHT_STALE_SEC",
           "INFLIGHT_PROBE_MSGS"]


INFLIGHT_KEY = "inflight"
INFLIGHT_STALE_SEC = 3600       # 超过 1 小时的标记不再自动重放（否则重启会翻旧账）
INFLIGHT_PROBE_MSGS = 50        # 回看多少条消息来判断"到底回没回"


def rollback_last_id(cid: str, old_last_id: str, *,
                     load_state, save_group, log) -> None:
    """生成失败（网络超时/空回复）时回滚 last_id，让这条消息下轮重试."""
    state = load_state()
    group_state = state.get(cid) or {}
    group_state["last_id"] = old_last_id
    state[cid] = group_state
    save_group(cid, group_state)


def mark_inflight(cid: str, mid: str, *,
                  load_state, save_group, log) -> None:
    """记下"这条消息正在处理"（落盘）—— 只在进程被硬杀时才有意义。"""
    if not mid:
        return
    try:
        state = load_state()
        group_state = state.get(cid) or {}
        group_state[INFLIGHT_KEY] = {"mid": str(mid), "at": time.time()}
        state[cid] = group_state
        save_group(cid, group_state)
    except Exception as e:                                     # noqa: BLE001
        log("  ⚠️ 记 inflight 失败（不影响本轮）: " + str(e)[:50])


def clear_inflight(cid: str, *, load_state, save_group, log) -> None:
    """这一轮跑完了（回复已发 / 本来就不用回）—— 标记清掉。"""
    try:
        state = load_state()
        group_state = state.get(cid) or {}
        if INFLIGHT_KEY in group_state:
            group_state.pop(INFLIGHT_KEY, None)
            state[cid] = group_state
            save_group(cid, group_state)
    except Exception as e:                                     # noqa: BLE001
        log("  ⚠️ 清 inflight 失败: " + str(e)[:50])


def recover_inflight(state: dict, groups: list, *, load_state, save_group,
                     get_messages, is_self, log) -> int:
    """启动时收拾"上次被杀掉时正在处理"的那条消息。返回重放了几条。

    判据（顺序即优先级）：
      1. 群里能找到那条消息，且它**之后**有我们发的消息 ⇒ 已经回过了，只清标记；
      2. 它之后没有我们的话 ⇒ 把 `last_id` 退到它前面一条，让主循环重放；
      3. 它已经滚出回看窗口（或标记太旧）⇒ 没法自动补，**留一行警告**（不许静默）。
    """
    replayed = 0
    for cid in groups:
        try:
            group_state = state.get(cid) or {}
            mark = group_state.get(INFLIGHT_KEY) or {}
            mid = str(mark.get("mid") or "")
            if not mid:
                continue
            age = time.time() - float(mark.get("at") or 0)
            if age > INFLIGHT_STALE_SEC:
                log("  ℹ️ [" + cid[:8] + "] inflight 标记太旧（{}s 前），只清不重放".format(
                    int(age)))
                clear_inflight(cid, load_state=load_state, save_group=save_group, log=log)
                continue
            msgs = get_messages(cid, INFLIGHT_PROBE_MSGS) or []
            idx = next((i for i, m in enumerate(msgs)
                        if m.get("message_id") == mid), -1)
            if idx < 0:
                log("  ⚠️ [" + cid[:8] + "] 上次被杀时在处理 " + mid[:8]
                    + "，但它已滚出最近 {} 条窗口 —— 这条回复补不回来了".format(
                        INFLIGHT_PROBE_MSGS))
                clear_inflight(cid, load_state=load_state, save_group=save_group, log=log)
                continue
            if any(is_self(m.get("sent_by") or "") for m in msgs[idx + 1:]):
                clear_inflight(cid, load_state=load_state, save_group=save_group, log=log)                            # 回过了
                continue
            if idx == 0:
                log("  ⚠️ [" + cid[:8] + "] inflight 那条已是最早一条，没法退回，跳过")
                clear_inflight(cid, load_state=load_state, save_group=save_group, log=log)
                continue
            group_state["last_id"] = msgs[idx - 1]["message_id"]
            state[cid] = group_state
            save_group(cid, group_state)
            clear_inflight(cid, load_state=load_state, save_group=save_group, log=log)
            replayed += 1
            log("  🔁 [" + cid[:8] + "] 上次被杀时正在处理 " + mid[:8]
                + "（没人回过），已退回 last_id 让主循环重放")
        except Exception as e:                                 # noqa: BLE001
            log("  ⚠️ [" + (cid or "")[:8] + "] inflight 恢复失败: " + str(e)[:60])
    if replayed:
        log("  🔁 重启恢复：重放 {} 条上次被打断的消息".format(replayed))
    return replayed
