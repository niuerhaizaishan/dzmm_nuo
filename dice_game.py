"""吹牛骰子（大话骰）判定引擎 —— 纯函数，与 bot 主逻辑解耦。

规则（已确认）：
- 每人 5 个骰子（1-6 点）
- 每轮随机一个 1~6 的点数作为「万能点」，公布到群里；万能点开牌时顶任何点数
- 一旦有人叫过「N 个万能点」，万能点失效只算它自己
- 轮流叫「N 个 X」；下家加码或开
- 加码：N' > N 或 (N' = N 且 X' > X)，点数 1 < 2 < … < 6
- 首叫 N ≥ 玩家人数
- 开：实际 X 数量（含万能点）≥ N → 叫的人赢，否则开的人赢
- 超时弃权：弃权者直接判输
"""
import random

DIE_FACES = [1, 2, 3, 4, 5, 6]

# 骰子点数 → 显示（🎲 + 数字）
DIE_EMOJI = {1: "🎲1", 2: "🎲2", 3: "🎲3", 4: "🎲4", 5: "🎲5", 6: "🎲6"}


def roll_dice(n: int = 5) -> list:
    """摇 n 个骰子，返回点数列表。"""
    return [random.randint(1, 6) for _ in range(n)]


def roll_wild() -> int:
    """每轮随机 1~6 的万能点数。"""
    return random.randint(1, 6)


def dice_str(dice: list) -> str:
    """骰子列表 → 显示字符串，如 [3,3,5,1,6] → '🎲3 🎲3 🎲5 🎲1 🎲6'。"""
    return " ".join(DIE_EMOJI.get(d, str(d)) for d in dice)


def count_point(all_dice: dict, x: int, wild: int, called_wild: bool) -> int:
    """数全场 x 的数量（含万能点 wild）。

    all_dice: {uid: [1,3,3,5,6], ...}
    wild: 本轮万能点数（1~6），开牌时可顶任何点数
    called_wild: 是否已经有人叫过万能点（叫过万能点后它失效，只算它自己）
    """
    total = 0
    for dice in all_dice.values():
        for d in dice:
            if d == x:
                total += 1
            elif d == wild and not called_wild and x != wild:
                total += 1  # 万能点顶 x
    return total


def is_legal_raise(old: tuple, new: tuple, player_count: int) -> bool:
    """加码合法性。

    old: (n, x) 上一次叫数，或 None 表示首叫
    new: (n, x) 本次叫数
    player_count: 玩家人数（首叫 N ≥ 人数）
    """
    new_n, new_x = new
    if new_n < 1 or new_x not in DIE_FACES:
        return False
    if old is None:
        return new_n >= player_count  # 首叫 N ≥ 人数
    old_n, old_x = old
    if new_n > old_n:
        return True
    if new_n == old_n and new_x > old_x:
        return True
    return False


def judge_open(all_dice: dict, call: tuple, wild: int, called_wild: bool) -> bool:
    """开牌判定。

    call: (n, x) 被开的叫数
    wild: 本轮万能点
    called_wild: 是否已叫过万能点
    返回 True = 叫的人赢，False = 开的人赢
    """
    actual = count_point(all_dice, call[1], wild, called_wild)
    return actual >= call[0]


def parse_call(text: str) -> tuple | None:
    """解析叫数指令，如 '3个3' / '5个6' / '2个1' → (3, 3)。
    非法返回 None。"""
    import re
    m = re.match(r"^(\d+)\s*个\s*([1-6])$", text.strip())
    if not m:
        return None
    n = int(m.group(1))
    x = int(m.group(2))
    return (n, x)
