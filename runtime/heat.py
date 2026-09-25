"""发情值（P8 第 4 步从 bot.py 搬出）。

`_add_heat` 是**最热的写路径**（几乎每个玩法得分都要调它），
所以 P8 第 1 步就先把它改成了单键写入（只写该群那一行）。

11 个 `HEAT_*` 取值与 P8 之前的字面量逐个相同，由
`tests/test_p8_refactor.py` 钉死 —— 调平衡只改常量，改错了测试会说话。
搬到这里之后，调用方从 `runtime.heat` 取（bot.py 仍 re-export，老调用点不用改）。
"""
import state_store

from runtime.identity import is_self

__all__ = [
    "_add_heat",
    "HEAT_SCENE_PICK", "HEAT_SCENE_CONTINUE_TARGET", "HEAT_SCENE_CONTINUE_SENDER",
    "HEAT_INSULT_TARGET", "HEAT_INSULT_SENDER", "HEAT_FASAO_PICK",
    "HEAT_DIAOJIAO_ROUND", "HEAT_VOTE_STARTER", "HEAT_VOTE_VOTER",
    "HEAT_STORY_VOTER", "HEAT_STORY_PROTAGONIST",
]


HEAT_SCENE_PICK = 10              # 发骚骰子：被抽中（2887）


HEAT_SCENE_CONTINUE_TARGET = 10   # 发骚续写：目标（3028）


HEAT_SCENE_CONTINUE_SENDER = 10   # 发骚续写：发起人（3030）


HEAT_INSULT_TARGET = 15           # 被羞辱（3056、4921）


HEAT_INSULT_SENDER = 10           # 羞辱续写：发起人（3058）


HEAT_FASAO_PICK = 15              # 被点名发骚 /发情点名（1680）


HEAT_DIAOJIAO_ROUND = 20          # 被调教一轮（3290）


HEAT_VOTE_STARTER = 8             # 投票：发起人（3562）


HEAT_VOTE_VOTER = 5               # 投票：参与者（3565）


HEAT_STORY_VOTER = 8              # 剧情：投票者（3746）


HEAT_STORY_PROTAGONIST = 15       # 剧情：主角（3748）


def _add_heat(cid: str, uid: str, delta: int) -> None:
    """给某人加发情值（持久化，排除小小糯）."""
    if not uid or is_self(uid) or delta <= 0:
        return
    # P8：单键读写 —— 只写该群那一行（原先每次都要重写全部群）
    heat = state_store.group_get(cid, "heat_value", {}) or {}
    heat[uid] = heat.get(uid, 0) + delta
    state_store.group_set(cid, "heat_value", heat)
