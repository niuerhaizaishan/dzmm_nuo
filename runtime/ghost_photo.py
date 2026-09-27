"""P12-O：红衣女鬼场景配图（摘花放鬼 / 命令进入女鬼场景 → 发这张图）。

屋主 2026-09-27 指定："有人摘花触发女鬼，或者命令你变为女鬼场景的情况下，发送这张图。"

两条触发路径：

1. **摘花放鬼**（男生摘花 → `ROOM_GHOST_TEXT` 固定剧本）：`bot.py` 那条分支发完文案后
   直接调 `send(...)`（这条不靠文本判定，是明确的剧本事件）；
2. **命令进入女鬼场景**：屋主说「{me}变成女鬼」「演一下女鬼场景」这类 → `maybe_send(...)`
   （判意图 + 去重）。**只有屋主**能下这个命令 —— 与房间其余"下命令"的口径一致。

图托管在自家域名（同 P12-L/N）：`https://www.nuonuo.pw/img/room/ghost.jpg`。
发送统一走 `runtime/bot_photo.py`（官方 Bot 通道、绝不抛、短去重）。
"""
import re

from runtime import bot_photo

__all__ = ["URL", "wants_photo", "send", "maybe_send"]

URL = "https://www.nuonuo.pw/img/room/ghost.jpg"

# "女鬼场景"的关键词
_SCENE_WORDS = ("女鬼", "红衣", "镰刀", "闹鬼", "鬼场景", "鬼片")
# "进入/扮演"的意图词（两字以上，避免"改天再说女鬼"这种也命中）
_CMD_WORDS = ("变成", "扮演", "演一下", "演个", "放出来", "放个", "放出", "进入",
              "切换", "来一下", "来一个", "来个", "上演", "开启", "启动", "召唤",
              "现身", "上场", "弄成", "变个")
# 命令都短：长句基本是在聊女鬼的故事，不是在下命令（"能不能讲讲她变成女鬼之前…"）
MAX_CHARS = 20


def wants_photo(text: str, cid: str = None, uid: str = None) -> bool:
    """这条消息是不是**屋主**在命令进入女鬼场景."""
    t = re.sub(r"\s+", "", str(text or ""))
    if not t or len(t) > MAX_CHARS:
        return False
    if not uid:
        return False
    try:
        from runtime.sign import _room_owner
    except Exception:                                        # noqa: BLE001
        return False
    if not _room_owner(cid, uid):
        return False                       # 只有屋主能下命令（客人由 AI 婉拒）
    if not any(w in t for w in _SCENE_WORDS):
        return False
    return any(w in t for w in _CMD_WORDS)


async def send(sender, cid: str, reason: str = "") -> bool:
    """发女鬼场景图（摘花放鬼那条路直接调它）."""
    return await bot_photo.send(sender, cid, URL, reason="女鬼·" + (reason or "场景"))


async def maybe_send(sender, cid: str, text: str, uid: str = None,
                     reason: str = "") -> bool:
    """命令路径：判意图 → 判重复 → 发."""
    if not wants_photo(text, cid, uid):
        return False
    if bot_photo.dup(cid, "ghost"):
        return False
    return await send(sender, cid, reason=reason or "命令")
