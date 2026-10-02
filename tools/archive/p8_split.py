"""一次性迁移（P8 第 1、2 步）：状态存取器 + 发情值魔数集中。

第 1 步：把 8 组"读全部群 → 改 → 写全部群"的样板，换成 `state_store.group_get/group_set`
        （单键读写，只写该群那一行）。
第 2 步：把散落的 5/8/10/15/20 集中为 `HEAT_*` 常量。

每个替换都断言出现次数；任何一条不符即整体中止，不写半成品（沿用 P4.2 的做法）。
"""
import re
import sys
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent.parent / "bot.py"

# ---------------------------------------------------------------- 第 1 步

ACCESSOR_PAIRS = [
    # 记忆
    ('    return (load_state().get(cid) or {}).get("memory") or []',
     '    return state_store.group_get(cid, "memory", []) or []', 1),
    ('    try:\n'
     '        state = load_state()\n'
     '        gs = state.get(cid) or {}\n'
     '        mem = gs.get("memory") or []\n'
     '        mem.append({"text": text, "from": from_name, "at": time.strftime("%Y-%m-%d %H:%M")})\n'
     '        gs["memory"] = mem[-MEMORY_MAX:]\n'
     '        state[cid] = gs\n'
     '        save_state(state)',
     '    try:\n'
     '        mem = state_store.group_get(cid, "memory", []) or []\n'
     '        mem.append({"text": text, "from": from_name,\n'
     '                    "at": time.strftime("%Y-%m-%d %H:%M")})\n'
     '        state_store.group_set(cid, "memory", mem[-MEMORY_MAX:])', 1),
    # 骰子局（顶层键）
    ('def _dice_state() -> dict:\n'
     '    """读骰子局状态。"""\n'
     '    state = load_state()\n'
     '    return state.get("dice_game") or {}\n'
     '\n'
     '\n'
     'def _dice_save(g: dict) -> None:\n'
     '    """写骰子局状态。"""\n'
     '    state = load_state()\n'
     '    state["dice_game"] = g\n'
     '    save_state(state)',
     'def _dice_state() -> dict:\n'
     '    """读骰子局状态（**顶层键**，不按 cid 存）。"""\n'
     '    return state_store.top_get("dice_game") or {}\n'
     '\n'
     '\n'
     'def _dice_save(g: dict) -> None:\n'
     '    """写骰子局状态。"""\n'
     '    state_store.top_set("dice_game", g)', 1),
    # last_action
    ('    try:\n'
     '        state = load_state()\n'
     '        gs = state.get(cid) or {}\n'
     '        gs["last_action"] = action\n'
     '        state[cid] = gs\n'
     '        save_state(state)\n'
     '    except Exception:\n'
     '        pass',
     '    try:\n'
     '        state_store.group_set(cid, "last_action", action)\n'
     '    except Exception:\n'
     '        pass', 1),
    ('        return (load_state().get(cid) or {}).get("last_action")',
     '        return state_store.group_get(cid, "last_action")', 1),
    # 五个状态机
    ('def _get_fasao_state(cid: str) -> dict:\n'
     '    """读取发骚状态."""\n'
     '    return (load_state().get(cid) or {}).get("fasao") or {}\n'
     '\n'
     '\n'
     'def _set_fasao_state(cid: str, fs: dict) -> None:\n'
     '    """写入发骚状态."""\n'
     '    state = load_state()\n'
     '    group = state.get(cid) or {}\n'
     '    group["fasao"] = fs\n'
     '    state[cid] = group\n'
     '    save_state(state)',
     'def _get_fasao_state(cid: str) -> dict:\n'
     '    """读取发骚状态."""\n'
     '    return state_store.group_get(cid, "fasao", {}) or {}\n'
     '\n'
     '\n'
     'def _set_fasao_state(cid: str, fs: dict) -> None:\n'
     '    """写入发骚状态."""\n'
     '    state_store.group_set(cid, "fasao", fs)', 1),
    ('def _get_dj_state(cid: str) -> dict:\n'
     '    """读取调教状态."""\n'
     '    return (load_state().get(cid) or {}).get("diaojiao") or {}\n'
     '\n'
     '\n'
     'def _set_dj_state(cid: str, dj: dict) -> None:\n'
     '    """写入调教状态."""\n'
     '    state = load_state()\n'
     '    group = state.get(cid) or {}\n'
     '    group["diaojiao"] = dj\n'
     '    state[cid] = group\n'
     '    save_state(state)',
     'def _get_dj_state(cid: str) -> dict:\n'
     '    """读取调教状态."""\n'
     '    return state_store.group_get(cid, "diaojiao", {}) or {}\n'
     '\n'
     '\n'
     'def _set_dj_state(cid: str, dj: dict) -> None:\n'
     '    """写入调教状态."""\n'
     '    state_store.group_set(cid, "diaojiao", dj)', 1),
    ('def _get_perf_state(cid: str) -> dict:\n'
     '    state = load_state()\n'
     '    return (state.get(cid) or {}).get("perf") or {}\n'
     '\n'
     '\n'
     'def _set_perf_state(cid: str, perf: dict) -> None:\n'
     '    state = load_state()\n'
     '    group = state.get(cid) or {}\n'
     '    group["perf"] = perf\n'
     '    state[cid] = group\n'
     '    save_state(state)',
     'def _get_perf_state(cid: str) -> dict:\n'
     '    """读取公演状态。"""\n'
     '    return state_store.group_get(cid, "perf", {}) or {}\n'
     '\n'
     '\n'
     'def _set_perf_state(cid: str, perf: dict) -> None:\n'
     '    """写入公演状态。"""\n'
     '    state_store.group_set(cid, "perf", perf)', 1),
    ('def _get_vote_state(cid: str) -> dict:\n'
     '    state = load_state()\n'
     '    return (state.get(cid) or {}).get("vote") or {}\n'
     '\n'
     '\n'
     'def _set_vote_state(cid: str, vote: dict) -> None:\n'
     '    state = load_state()\n'
     '    group = state.get(cid) or {}\n'
     '    group["vote"] = vote\n'
     '    state[cid] = group\n'
     '    save_state(state)',
     'def _get_vote_state(cid: str) -> dict:\n'
     '    """读取投票状态。"""\n'
     '    return state_store.group_get(cid, "vote", {}) or {}\n'
     '\n'
     '\n'
     'def _set_vote_state(cid: str, vote: dict) -> None:\n'
     '    """写入投票状态。"""\n'
     '    state_store.group_set(cid, "vote", vote)', 1),
    ('def _get_story_state(cid: str) -> dict:\n'
     '    state = load_state()\n'
     '    return (state.get(cid) or {}).get("story") or {}\n'
     '\n'
     '\n'
     'def _set_story_state(cid: str, story: dict) -> None:\n'
     '    state = load_state()\n'
     '    group = state.get(cid) or {}\n'
     '    group["story"] = story\n'
     '    state[cid] = group\n'
     '    save_state(state)',
     'def _get_story_state(cid: str) -> dict:\n'
     '    """读取剧情状态。"""\n'
     '    return state_store.group_get(cid, "story", {}) or {}\n'
     '\n'
     '\n'
     'def _set_story_state(cid: str, story: dict) -> None:\n'
     '    """写入剧情状态。"""\n'
     '    state_store.group_set(cid, "story", story)', 1),
]

# ---------------------------------------------------------------- 第 2 步

HEAT_BLOCK = '''# ============ 发情值增量（P8 集中管理）============
# 原先 5/8/10/15/20 散落在十余个函数里，改规则要到处找。括号内是原行号，便于对照。
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


def _add_heat(cid: str, uid: str, delta: int) -> None:'''

HEAT_PAIRS = [
    ('    _add_heat(cid, uid, 15)\n    return "🎯 点中 "',
     '    _add_heat(cid, uid, HEAT_FASAO_PICK)\n    return "🎯 点中 "', 1),
    ('_add_heat(_ACTIVE_CID, selected_uid, 10)  # 被抽中 → 发情值 +10',
     '_add_heat(_ACTIVE_CID, selected_uid, HEAT_SCENE_PICK)', 1),
    # 发骚续写（含上下文锚点，避免与羞辱续写的同名调用混淆）
    ('        target_uid = fs.get("uid")\n'
     '        if target_uid:\n'
     '            _add_heat(cid, target_uid, 10)\n'
     '        if sender_uid and sender_uid != target_uid:\n'
     '            _add_heat(cid, sender_uid, 10)',
     '        target_uid = fs.get("uid")\n'
     '        if target_uid:\n'
     '            _add_heat(cid, target_uid, HEAT_SCENE_CONTINUE_TARGET)\n'
     '        if sender_uid and sender_uid != target_uid:\n'
     '            _add_heat(cid, sender_uid, HEAT_SCENE_CONTINUE_SENDER)', 1),
    # 羞辱续写
    ('    # 续写加分（和目标原指令一致：羞辱+15）\n'
     '    target_uid = action.get("uid")\n'
     '    if target_uid:\n'
     '        _add_heat(cid, target_uid, 15)\n'
     '    if sender_uid and sender_uid != target_uid:\n'
     '        _add_heat(cid, sender_uid, 10)',
     '    # 续写加分（和目标原指令一致：羞辱 +15）\n'
     '    target_uid = action.get("uid")\n'
     '    if target_uid:\n'
     '        _add_heat(cid, target_uid, HEAT_INSULT_TARGET)\n'
     '    if sender_uid and sender_uid != target_uid:\n'
     '        _add_heat(cid, sender_uid, HEAT_INSULT_SENDER)', 1),
    ('_add_heat(cid, target_uid, 20)  # 被调教一轮 +20',
     '_add_heat(cid, target_uid, HEAT_DIAOJIAO_ROUND)  # 被调教一轮', 1),
    ('        _add_heat(cid, starter, 8)', '        _add_heat(cid, starter, HEAT_VOTE_STARTER)', 1),
    ('            _add_heat(cid, uid, 5)', '            _add_heat(cid, uid, HEAT_VOTE_VOTER)', 1),
    ('            _add_heat(cid, uid, 8)', '            _add_heat(cid, uid, HEAT_STORY_VOTER)', 1),
    ('        _add_heat(cid, protagonist_uid, 15)',
     '        _add_heat(cid, protagonist_uid, HEAT_STORY_PROTAGONIST)', 1),
    # 被羞辱（主分派 4921）。注：羞辱续写那处（3056）已被上面的**上下文锚点**先替换掉，
    # 所以这里只剩 1 处 —— 顺序有依赖，不要调换。
    ('_add_heat(cid, target_uid, 15)', '_add_heat(cid, target_uid, HEAT_INSULT_TARGET)', 1),
]

ADD_HEAT_DEF = 'def _add_heat(cid: str, uid: str, delta: int) -> None:'


def main() -> int:
    src = BOT.read_text(encoding="utf-8")

    if "HEAT_INSULT_TARGET" in src and "state_store.group_get" in src:
        print("已经迁移过，中止以免重复应用")
        return 1

    print("第 1 步：状态存取器")
    for old, new, expect in ACCESSOR_PAIRS:
        got = src.count(old)
        if got != expect:
            print("中止：期望 {} 次，实际 {} 次\n  {!r}".format(expect, got, old[:80]))
            return 1
        src = src.replace(old, new)
    print("  已替换 {} 组".format(len(ACCESSOR_PAIRS)))

    print("第 2 步：发情值魔数")
    if src.count(ADD_HEAT_DEF) != 1:
        print("中止：_add_heat 定义不唯一")
        return 1
    src = src.replace(ADD_HEAT_DEF, HEAT_BLOCK, 1)
    for old, new, expect in HEAT_PAIRS:
        got = src.count(old)
        if got != expect:
            print("中止：期望 {} 次，实际 {} 次\n  {!r}".format(expect, got, old[:80]))
            return 1
        src = src.replace(old, new)
    print("  已替换 {} 处调用".format(sum(p[2] for p in HEAT_PAIRS)))

    BOT.write_text(src, encoding="utf-8")

    # ---- 自检：不该再出现裸数字增量 ----
    left = re.findall(r"_add_heat\([^)]*,\s*\d+\)", src)
    print()
    print("剩余裸数字 _add_heat 调用: {} 处（应为 0）".format(len(left)))
    if left:
        print("  " + "\n  ".join(left[:6]))
        return 1
    # 旧的"读全部群→写全部群"样板应只剩 _remember 之外的地方（这里要求 group_* 已接入）
    if "state_store.group_set" not in src:
        print("!! 未见 group_set 接入")
        return 1
    print("OK 迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
