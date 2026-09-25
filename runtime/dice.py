"""吹牛骰子局的状态机（P8 第 4 步从 bot.py 整段搬出）。

为什么第一个拆它：侦察量出来的耦合最低 ——
  fan_in = 1（只有 main_loop 调它）
  fan_out = 2（只要 `get_name` / `log`）
  引用的模块级全局 **0 个**
拆完对 bot.py **零回指**，所以它同时也是"拆分机制"本身的验证。

纯牌面规则本来就在 `dice_game.py`（发牌 / 叫点 / 判定 / 开牌）；
这里只管**局面**：座位、轮次、看板、各方指令、私聊发骰。
"""
import asyncio
import time

from dice_game import count_point, dice_str, is_legal_raise, roll_dice, roll_wild
from dzmm import get_private_cid
from runtime.identity import get_name
from runtime.obs import log
import state_store

__all__ = [
    "_dice_state", "_dice_save", "_dice_current_player", "_dice_send_private",
    "_dice_board_text", "_dice_deal_round", "_dice_start_cmd", "_dice_join_cmd",
    "_dice_exit_cmd", "_dice_begin_cmd", "_dice_next_round_cmd", "_dice_call_cmd",
    "_dice_open_cmd", "_dice_quit_cmd", "_dice_skip_cmd", "_dice_status_text",
]


def _dice_state() -> dict:
    """读骰子局状态（**顶层键**，不按 cid 存）。"""
    return state_store.top_get("dice_game") or {}


def _dice_save(g: dict) -> None:
    """写骰子局状态。"""
    state_store.top_set("dice_game", g)


def _dice_current_player(g: dict) -> str:
    """当前轮到谁（order[current_idx]）。"""
    order = g.get("order") or []
    idx = g.get("current_idx", 0)
    if not order:
        return ""
    return order[idx % len(order)]


async def _dice_send_private(sender, uid: str, text: str) -> bool:
    """私聊发消息给 uid（纯 Playwright，不走 Socket.IO 兜底，避免假成功）。"""
    cid = await asyncio.to_thread(get_private_cid, uid)
    if not cid:
        log("  私聊 chat_id 未找到 uid=" + uid[:8])
        return False
    return await sender.send(text, cid, allow_fallback=False)


def _dice_board_text(g: dict, final: bool = False) -> str:
    """看板：轮结束看板 或 总看板。"""
    players = g.get("players", [])
    stats = g.get("stats", {})
    round_num = g.get("round", 1)
    lines = []
    if final:
        lines.append(f"📊 骰子局结束！共 {round_num} 轮，参与 {len(players)} 人")
    else:
        lines.append(f"📊 第 {round_num} 轮结束（当前 {len(players)} 人）")
    lines.append("参与人员：" + "、".join("@" + get_name(p) for p in players))
    lines.append("累计战绩：")
    for p in players:
        s = stats.get(p, {})
        lines.append(f"@{get_name(p)}  {s.get('win', 0)}胜{s.get('lose', 0)}负")
    if not final:
        lines.append("发 /下一轮 开下一轮（没在发骚时 /继续 也行），发起者可 /结束")
    return "\n".join(lines)


async def _dice_deal_round(sender, g: dict) -> str:
    """发骰 + 返回本轮广播文本（编号 + 首叫提示 + 发骰失败提示）。"""
    order = g.get("order", [])
    failed = []
    for p in order:
        ok = await _dice_send_private(sender, p, "你的骰子：" + dice_str(g["dice"][p]))
        log("  发骰 " + get_name(p) + ": " + ("✅" if ok else "❌"))
        if not ok:
            failed.append(p)
    num = len(order)
    lines = [f"🎲 骰子局（第 {g.get('round', 1)} 轮）开始！"]
    wild = g.get("wild")
    if wild:
        lines.append(f"🃏 本轮万能点：{dice_str([wild])}（可顶任何点数）")
    for i, p in enumerate(order, 1):
        lines.append(f"{i}号 @{get_name(p)}")
    lines.append(f"由 1号 首叫（最少叫 {num} 个，格式 /N个X）")
    if failed:
        lines.append("⚠️ " + "、".join("@" + get_name(p) for p in failed)
                     + " 骰子没发成功（私聊未解锁），请先私聊小小糯发一句话，再发 /看骰 查看～")
    return "\n".join(lines)


async def _dice_start_cmd(cid: str, uid: str) -> str:
    """发起骰子局。"""
    g = _dice_state()
    if g.get("active"):
        return "🎲 已经有一局骰子在进行啦～先 /结束 再开新的～"
    g = {"active": True, "phase": "waiting", "host": uid, "cid": cid,
         "players": [uid], "order": [], "dice": {},
         "current_call": None, "last_caller": None, "wild": None, "called_wild": False,
         "current_idx": 0, "last_action_ts": time.time(), "timeout_streak": 0,
         "round": 1, "stats": {}, "round_history": []}
    _dice_save(g)
    return "🎲 吹牛骰子局发起！想玩的发 /加入，人齐后发起者发 /开始～"


async def _dice_join_cmd(uid: str) -> str:
    """加入骰子局（等待中立即加入；游戏中加入下一轮生效）。"""
    g = _dice_state()
    if not g.get("active"):
        return "现在没有骰子局哦～发 /吹牛骰子 发起～"
    if uid in g.get("players", []):
        return "你已经在局里啦～"
    if g.get("phase") == "waiting":
        g["players"].append(uid)
        _dice_save(g)
        return f"@{get_name(uid)} 加入！当前 {len(g['players'])} 人～"
    # 游戏中加入，下一轮生效（本轮不参与 order）
    g["players"].append(uid)
    _dice_save(g)
    return f"@{get_name(uid)} 加入！下一轮参战～"


def _dice_exit_cmd(uid: str) -> str:
    """退出游戏（不算输）。"""
    g = _dice_state()
    if not g.get("active") or uid not in g.get("players", []):
        return ""
    old_order = g.get("order", [])
    old_idx = g.get("current_idx", 0) % max(len(old_order), 1)
    old_cur = old_order[old_idx] if old_order else ""
    g["players"].remove(uid)
    new_order = [p for p in old_order if p != uid]
    g["order"] = new_order
    if new_order:
        if old_cur == uid:
            g["current_idx"] = min(old_idx, len(new_order) - 1)
        elif old_cur in new_order:
            g["current_idx"] = new_order.index(old_cur)
        else:
            g["current_idx"] = 0
    else:
        g["current_idx"] = 0
    _dice_save(g)
    if len(g["players"]) < 2:
        g["active"] = False
        g["phase"] = "done"
        _dice_save(g)
        return f"@{get_name(uid)} 退出游戏，剩余不足 2 人，骰子局结束。"
    return f"@{get_name(uid)} 退出游戏（不算输），剩余 {len(g['players'])} 人。"


async def _dice_begin_cmd(sender, uid: str) -> str:
    """开始骰子局（发骰 + 随机编号）。"""
    g = _dice_state()
    if not g.get("active"):
        return "现在没有骰子局哦～发 /吹牛骰子 发起～"
    if uid != g.get("host"):
        return "只有发起者能 /开始 哦～"
    if g.get("phase") != "waiting":
        return "已经开始了～"
    if len(g.get("players", [])) < 2:
        return "至少 2 人才能开始哦～"
    import random
    players = list(g["players"])
    random.shuffle(players)
    g["order"] = players
    g["dice"] = {p: roll_dice(5) for p in players}
    g["phase"] = "calling"
    g["current_call"] = None
    g["last_caller"] = None
    g["wild"] = roll_wild()
    g["called_wild"] = False
    g["current_idx"] = 0
    g["last_action_ts"] = time.time()
    g["timeout_streak"] = 0
    g["round"] = 1
    g["stats"] = {}
    g["round_history"] = []
    _dice_save(g)
    return await _dice_deal_round(sender, g)


async def _dice_next_round_cmd(sender) -> str:
    """/下一轮（或发骚未进行时的 /继续）：进入下一轮（新加入者追加到顺序，重新摇骰发骰）。"""
    g = _dice_state()
    if not g.get("active") or g.get("phase") != "round_end":
        return ""
    # 新加入的人追加到 order 末尾
    order = list(g.get("order", []))
    for p in g.get("players", []):
        if p not in order:
            order.append(p)
    g["order"] = order
    g["round"] = g.get("round", 1) + 1
    g["dice"] = {p: roll_dice(5) for p in order}
    g["phase"] = "calling"
    g["current_call"] = None
    g["last_caller"] = None
    g["wild"] = roll_wild()
    g["called_wild"] = False
    g["current_idx"] = 0
    g["last_action_ts"] = time.time()
    g["timeout_streak"] = 0
    _dice_save(g)
    return await _dice_deal_round(sender, g)


async def _dice_call_cmd(uid: str, call) -> str:
    """叫数加码。"""
    g = _dice_state()
    if not g.get("active") or g.get("phase") != "calling":
        return "现在没有进行中的骰子局哦～"
    cur = _dice_current_player(g)
    if uid != cur:
        return f"还没轮到你哦～现在轮到 @{get_name(cur)}～"
    if not is_legal_raise(g.get("current_call"), call, len(g.get("order", []))):
        return "叫数不合法哦～要比上一个大（个数更多，或点数更大）～"
    g["current_call"] = list(call)
    g["last_caller"] = uid
    if call[1] == g.get("wild"):
        g["called_wild"] = True
    g["current_idx"] = (g.get("current_idx", 0) + 1) % len(g["order"])
    g["last_action_ts"] = time.time()
    g["timeout_streak"] = 0  # 正常行动重置超时计数
    _dice_save(g)
    nxt = _dice_current_player(g)
    n, x = call
    tip = "（🎲万能点失效！）" if x == g.get("wild") else ""
    return f"@{get_name(uid)} 叫 {n}个{x}{tip}！轮到 @{get_name(nxt)}（加码 /N个X 或 /开）"


async def _dice_open_cmd(uid: str) -> str:
    """开牌判定：记输赢 → 看板 → round_end。"""
    g = _dice_state()
    if not g.get("active") or g.get("phase") != "calling":
        return "现在没有进行中的骰子局哦～"
    cur = _dice_current_player(g)
    if uid != cur:
        return f"还没轮到你哦～现在轮到 @{get_name(cur)}～"
    if not g.get("current_call"):
        return "还没有人叫数呢，不能开～"
    caller = g.get("last_caller")
    if caller == uid:
        return "你不能开自己叫的数哦～"
    call = tuple(g["current_call"])
    actual = count_point(g["dice"], call[1], g.get("wild", 1), g.get("called_wild", False))
    caller_win = actual >= call[0]
    winner = caller if caller_win else uid
    loser = uid if caller_win else caller
    # 记录输赢
    stats = g.get("stats", {})
    stats.setdefault(winner, {"win": 0, "lose": 0})["win"] += 1
    stats.setdefault(loser, {"win": 0, "lose": 0})["lose"] += 1
    g["stats"] = stats
    g.setdefault("round_history", []).append(
        {"round": g.get("round", 1), "winner": winner, "loser": loser, "actual": actual})
    g["phase"] = "round_end"
    g["last_action_ts"] = time.time()
    _dice_save(g)
    lines = ["🎲 开牌！全场骰子："]
    for p in g.get("order", []):
        lines.append(f"@{get_name(p)}：{dice_str(g['dice'][p])}")
    n, x = call
    lines.append(f"@{get_name(caller)} 叫 {n}个{x}，实际 {actual} 个")
    lines.append(f"🎉 @{get_name(winner)} 赢！@{get_name(loser)} 输了，接受惩罚～")
    lines.append("")
    lines.append(_dice_board_text(g))
    return "\n".join(lines)


async def _dice_quit_cmd(uid: str, timeout: bool = False) -> str:
    """主动 /结束：等待阶段取消，游戏中出总看板。"""
    g = _dice_state()
    if not g.get("active"):
        return ""
    if g.get("phase") == "waiting":
        g["active"] = False
        g["phase"] = "done"
        _dice_save(g)
        return "🎲 骰子局已取消～"
    if g.get("phase") not in ("calling", "round_end"):
        return ""
    g["active"] = False
    g["phase"] = "done"
    _dice_save(g)
    return _dice_board_text(g, final=True)


def _dice_skip_cmd(uid: str) -> str:
    """超时跳过：uid 移到顺序最后，游戏继续。连续超时满一圈则本轮作废。"""
    g = _dice_state()
    if not g.get("active") or g.get("phase") != "calling":
        return ""
    order = g.get("order", [])
    n = len(order)
    if n == 0:
        return ""
    idx = g.get("current_idx", 0) % n
    if order[idx] != uid:
        return ""
    # uid 移到最后，current_idx 指向原下一位（最后一位超时则回到 0）
    new_order = order[:idx] + order[idx + 1:] + [order[idx]]
    g["order"] = new_order
    g["current_idx"] = 0 if idx == n - 1 else idx
    g["last_action_ts"] = time.time()
    g["timeout_streak"] = g.get("timeout_streak", 0) + 1
    # 连续超时满一圈（全场轮空）→ 本轮作废，进入 round_end
    if g["timeout_streak"] >= n:
        g["phase"] = "round_end"
        g["last_action_ts"] = time.time()
        _dice_save(g)
        return "⏱️ 大家都超时了，这一轮作废！\n\n" + _dice_board_text(g)
    _dice_save(g)
    nxt = _dice_current_player(g)
    return f"⏱️ @{get_name(uid)} 超时，自动跳到下一位！轮到 @{get_name(nxt)}"


def _dice_status_text(g: dict) -> str:
    """骰子局状态文本。"""
    if not g.get("active"):
        return "现在没有进行中的骰子局哦～发 /吹牛骰子 发起～"
    if g.get("phase") == "waiting":
        return f"🎲 骰子局等待中（{len(g.get('players', []))} 人），发起者发 /开始～"
    if g.get("phase") == "round_end":
        return f"🎲 第 {g.get('round', 1)} 轮已结束，发 /下一轮 开下一轮（没在发骚时 /继续 也行）～"
    order = g.get("order", [])
    cur = _dice_current_player(g)
    call = g.get("current_call")
    lines = [f"🎲 骰子局第 {g.get('round', 1)} 轮进行中（{len(order)} 人）"]
    lines.append("顺序：" + " → ".join(f"{i+1}号@{get_name(p)}" for i, p in enumerate(order)))
    if call:
        n, x = call
        lines.append(f"当前叫数：{n}个{x}（@{get_name(g.get('last_caller', ''))} 叫的）")
    else:
        lines.append("当前叫数：无（等 1号 首叫）")
    lines.append(f"轮到：@{get_name(cur)}")
    wild = g.get("wild")
    if wild:
        if g.get("called_wild"):
            lines.append(f"⚠️ 已叫过万能点 🎲{wild}，它不再万能")
        else:
            lines.append(f"🃏 本轮万能点：🎲{wild}（可顶任何点数）")
    return "\n".join(lines)
