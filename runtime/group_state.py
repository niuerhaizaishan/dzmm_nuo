"""按群持久化状态的**薄存取器**（P8 第 4 步从 bot.py 搬出）。

这是 P8 第 1 步那批"读全部群 → 改 → 写全部群"样板的最终归宿：
它们全部只做一件事 —— 从 `state_store` 按群读/写一个键。
单独成模块是因为**五个状态机、记忆、上一段动作全都要用**，
留在 bot.py 里，后面每个切片都得回指它。

⚠️ 骰子局的 `_dice_state`/`_dice_save` 不在这里，而在 `runtime/dice.py`：
它用的是**顶层键**（`dice_game` 不按 cid 存），不是"按群"的。
"""
import time

import state_store


__all__ = [
    "_get_fasao_state", "_set_fasao_state", "_get_dj_state", "_set_dj_state",
    "_get_perf_state", "_set_perf_state", "_get_vote_state", "_set_vote_state",
    "_get_story_state", "_set_story_state",
    "_get_memory", "_remember", "_set_last_action", "_get_last_action",
]


MEMORY_MAX = 100


def _get_fasao_state(cid: str) -> dict:
    """读取发骚状态."""
    return state_store.group_get(cid, "fasao", {}) or {}


def _set_fasao_state(cid: str, fs: dict) -> None:
    """写入发骚状态."""
    state_store.group_set(cid, "fasao", fs)


def _get_dj_state(cid: str) -> dict:
    """读取调教状态."""
    return state_store.group_get(cid, "diaojiao", {}) or {}


def _set_dj_state(cid: str, dj: dict) -> None:
    """写入调教状态."""
    state_store.group_set(cid, "diaojiao", dj)


def _get_perf_state(cid: str) -> dict:
    """读取公演状态。"""
    return state_store.group_get(cid, "perf", {}) or {}


def _set_perf_state(cid: str, perf: dict) -> None:
    """写入公演状态。"""
    state_store.group_set(cid, "perf", perf)


def _get_vote_state(cid: str) -> dict:
    """读取投票状态。"""
    return state_store.group_get(cid, "vote", {}) or {}


def _set_vote_state(cid: str, vote: dict) -> None:
    """写入投票状态。"""
    state_store.group_set(cid, "vote", vote)


def _get_story_state(cid: str) -> dict:
    """读取剧情状态。"""
    return state_store.group_get(cid, "story", {}) or {}


def _set_story_state(cid: str, story: dict) -> None:
    """写入剧情状态。"""
    state_store.group_set(cid, "story", story)


def _get_memory(cid: str) -> list:
    """读取记忆列表（按群）。"""
    return state_store.group_get(cid, "memory", []) or []


def _remember(cid: str, text: str, from_name: str) -> None:
    """写入一条记忆（滑动窗口，最多 MEMORY_MAX 条），失败静默。"""
    try:
        mem = state_store.group_get(cid, "memory", []) or []
        mem.append({"text": text, "from": from_name,
                    "at": time.strftime("%Y-%m-%d %H:%M")})
        state_store.group_set(cid, "memory", mem[-MEMORY_MAX:])
    except Exception:
        pass


def _set_last_action(cid: str, action: dict) -> None:
    """记录上一段动作上下文（供 /继续 续写）."""
    try:
        state_store.group_set(cid, "last_action", action)
    except Exception:
        pass


def _get_last_action(cid: str):
    """读取上一段动作上下文."""
    try:
        return state_store.group_get(cid, "last_action")
    except Exception:
        return None
