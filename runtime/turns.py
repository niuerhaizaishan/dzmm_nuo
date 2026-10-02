"""对话轮次上下文（P3-15 从 bot.py 拆出）。

每群一份滚动对话记录（`_TURNS`：who/text/mid/uid），供 @ 问答 / 斜杠指令拼
"她刚才说了什么"。三个配套件：
  · `_TURN_SEEN` —— message_id 去重（同一批消息 + 平台回读只记一次）；
  · `other_bot_uids` —— 本群**别家 bot** 的 uid（工具熊的 /luck 运势卡刷屏，
    不进她的上下文；按可靠度：平台 isBot > 已知 Bot uid > 昵称带 "bot"，
    认不出来就算了，宁可漏也不误杀）；结果按群缓存 ttl 秒；
  · `seed_turns` —— 第一次用到某群的上下文时从平台补一次历史（每群每进程一次）。

**状态真源在本模块**（`_TURNS` 等都是本模块的可变对象）；bot.py 以**同一对象**
re-export —— tests 直接操作 `bot._TURNS` / `bot._GROUP_BOTS` 依然生效。
**接缝注入**：recent_turns 的 other_bot_uids/is_self（test_ctx_bilateral patch
`bot._other_bot_uids`）、seed_turns 的 get_messages/is_self/get_name
（test_chat_transcript patch `bot.*`）——凡是测试 patch 的 bot 名字都必须经壳注入。
"""
import time

from runtime.identity import bot_uids
from runtime.obs import log


__all__ = ["remember_turn", "recent_turns", "seed_turns",
           "platform_api", "other_bot_uids"]


_TURNS: dict = {}            # cid -> [{"who", "text", "mid", "uid"}]
_TURN_MAX = 80               # 每群最多记多少条（够 context_msgs 用）
_TURN_SEEN: dict = {}        # cid -> {message_id}（防同一批/回读重复入上下文）
_TURN_SEEDED: set = set()    # 已经从平台补过一次历史的群
_GROUP_BOTS: dict = {}       # P12-BE：cid -> (ts, {别的 bot 的 uid})，见 `other_bot_uids`


def platform_api(method: str, params: dict):
    """薄封装：平台 API（bot.py 里原先只有 main 之外零星几处直接 import）。

    P12-BE 需要 `chatroom.getMembers` 的 `isBot` 标志（把别家 bot 从上下文里剔掉）。
    `dzmm.api()` 自己 timeout=15，调用方必须走线程（见 `_other_bot_uids`）。
    """
    from dzmm import api as _api
    return _api(method, params)


def other_bot_uids(cid: str, ttl: float = 600.0) -> set:
    """P12-BE c：本群里**别家 bot**的 uid 集合（工具熊那种刷屏不进她的上下文）。

    取法按可靠度递减：
      1. 平台 `chatroom.getMembers` 里的 `isBot`（唯一确凿依据）；
      2. 我们自己那三个 Bot 的 uid（`bot_uids()`）；
      3. 兜底：昵称里带 "bot" 的（`工具熊 Jr.` 这种**认不出来就算了**，宁可漏也不误杀）。
    阻塞调用，调用方必须走 `asyncio.to_thread`；结果按群缓存 `ttl` 秒，
    失败时**不缓存**（下一次再试），且绝不让异常冒出去。
    """
    now = time.time()
    got = _GROUP_BOTS.get(cid)
    if got and now - got[0] < ttl:
        return got[1]
    out = set()
    try:
        out |= {str(u) for u in (bot_uids() or ())}
    except Exception:                                          # noqa: BLE001
        pass
    try:
        raw = platform_api("chatroom.getMembers", {"json": {"chatroomId": cid, "limit": 1000}})
        for m in (raw or {}).get("members") or []:
            if m.get("isBot"):
                out.add(str(m.get("id") or ""))
            elif "bot" in str(m.get("name") or m.get("nickname") or "").lower():
                out.add(str(m.get("id") or ""))
        _GROUP_BOTS[cid] = (now, out)
    except Exception as e:                                     # noqa: BLE001
        log("⚠️ [" + cid[:8] + "] 取 isBot 名单失败（只按已知 Bot 过滤）：" + str(e)[:50])
    return out


def remember_turn(cid: str, who: str, text: str,
                  mid: str = None, uid: str = None) -> None:
    """把一句话记进本群的滚动上下文（含我们自己说的话）。

    P12-BE（2026-09-30）：**多记一个 uid**。原先只记 `who`（昵称）——
    于是"双边上下文"（只跟她正在说话的那个人）根本没法按人过滤：同名的、
    改过名的全认错。uid 是稳定主键，`is_self(uid)` 也顺带把"我"认出来。
    """
    text = (text or "").strip()
    if not text:
        return
    if mid:
        seen = _TURN_SEEN.setdefault(cid, set())
        if mid in seen:
            return
        seen.add(mid)
        if len(seen) > 300:                                    # 有界，别无限涨
            _TURN_SEEN[cid] = set(list(seen)[-150:])
    lst = _TURNS.setdefault(cid, [])
    # 近 3 条里有同一个人说过一模一样的话 → 当回读，不再记。
    # 为什么按"文本"也要去一次：我们自己发出去的话，要么在发送处记（没有 message_id），
    # 要么稍后被平台回读进来（有 message_id），只有按文本才能把这两次并成一次。
    if any(t.get("who") == who and t.get("text") == text for t in lst[-3:]):
        return
    lst.append({"who": who, "text": text, "mid": mid, "uid": uid or ""})
    del lst[:-_TURN_MAX]


def recent_turns(cid: str, n: int = 20, exclude_mid: str = None,
                 only_uid: str = None, drop_bots: bool = True, *,
                 other_bot_uids, is_self) -> list:
    """取最近 N 条上下文。

    `exclude_mid` = 当前这条问题，别喂两遍。
    P12-BE：`only_uid` 非空 = **双边**（只留"我"和这个人）；`drop_bots` = 别家 bot 不进上下文。
    """
    bots = other_bot_uids(cid) if drop_bots else set()
    out = []
    for t in _TURNS.get(cid, [])[-max(1, int(n)):]:
        if exclude_mid and t.get("mid") == exclude_mid:
            continue
        uid = t.get("uid") or ""
        if drop_bots and uid and uid in bots and not is_self(uid):
            continue                       # 工具熊的 /luck 运势卡刷屏，别当群友发言（**自己除外**）
        if only_uid and uid and not (is_self(uid) or uid == only_uid):
            continue                       # 双边：只留"我"和他
        out.append({"sender": t["who"], "text": t["text"]})
    return out


def seed_turns(cid: str, *, get_messages, is_self, get_name,
                read_timeout) -> None:
    """第一次用到某个群的上下文时，从平台补一次最近消息（每群每进程一次）。

    阻塞调用（平台 HTTP）—— 调用方必须走 `asyncio.to_thread`。
    """
    if cid in _TURN_SEEDED:
        return
    _TURN_SEEDED.add(cid)
    if _TURNS.get(cid):
        return
    try:
        msgs = get_messages(cid, 20, None, None, read_timeout)
    except Exception as e:                                     # noqa: BLE001
        log("⚠️ [" + cid[:8] + "] 补对话历史失败（不影响回答）：" + str(e)[:60])
        return
    for m in msgs or []:
        uid = m.get("sent_by", "")
        txt = ((m.get("content") or {}).get("text") or "").strip()
        if not txt:
            continue
        remember_turn(cid, "我" if is_self(uid) else get_name(uid), txt,
                       m.get("message_id"), uid)
    log("🧠 [" + cid[:8] + "] 对话历史已补 " + str(len(_TURNS.get(cid) or [])) + " 条")
