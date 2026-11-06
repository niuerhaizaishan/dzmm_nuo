"""茶水间「潜规则」付费玩法的纯规则层：职级、概率、费用、分账、金额解析。

这一层不碰数据库、不碰网络、不碰模型 —— 全部是纯函数，方便把方案的数值钉死在测试里。

方案来源：docs/茶水间潜规则付费概率玩法方案-2026-11-05（屋主定稿）。
"""
import re

# ---------------------------------------------------------------- 数值常量

BASE_FEE = 5              # 首段基础费用（摸鱼币）
CONTINUE_FEE = 5          # 每段续写费用
MAX_EXTRA = 50            # 额外加币上限
MAX_CONTINUES = 5         # 每场最多续写 5 段（首段 + 续写共 6 段）
EXTRA_STEP = 0.5          # 每 1 币增加 0.5 个百分点
PROBABILITY_CAP = 99      # 最终成功率上限，不提供必成功

# 职级从低到高（第 11 条方案原文顺序）
RANKS = [
    "实习生", "正式员工", "小组长", "副主管", "主管",
    "部门副经理", "部门经理", "部门副总监", "部门总监",
    "公司负责人", "核心董事会",
]
RANK_LEVEL = {name: index + 1 for index, name in enumerate(RANKS)}

# 主任职级差 → (发起者更高时的成功率, 发起者更低时的成功率)
# 平级单列；6 级及以上封顶 99 / 1。
PROBABILITY_TABLE = {
    0: (50.0, 50.0),
    1: (60.0, 40.0),
    2: (70.0, 30.0),
    3: (80.0, 20.0),
    4: (90.0, 10.0),
    5: (95.0, 5.0),
}
PROBABILITY_FAR = (99.0, 1.0)

# 分账比例（键是"发起者相对目标"）：上级→下级 50%、平级 40%、下级→上级 30%
SPLIT_RATIO = {"up": 0.50, "peer": 0.40, "down": 0.30}

EMPLOYEE_ID = re.compile(r"^#\d+$")          # 工号：#0060
INTEGER = re.compile(r"^\d+$")

# 平台 gender 的取值实测不止男女（2026-11-05 茶水间实测）：
#   female / male / lesbian / gay / femboy / other / None
# 只有明确的女/男才决定称谓；非二元标签一律"未知"，用姓名或中性称呼，绝不回头猜。
FEMALE_VALUES = {"female", "f", "woman", "girl", "女", "女生", "女性", "lesbian", "transwoman"}
MALE_VALUES = {"male", "m", "man", "boy", "男", "男生", "男性", "gay", "transman"}


class RuleProblem(ValueError):
    """参数或规则层面的拒绝（不扣币、不抽取）。"""


# ---------------------------------------------------------------- 职级与概率

FEMALE_HINTS = (
    "姐", "妹", "妈", "娘", "姨", "婶", "嫂", "婆", "娜", "丽", "婷", "婉", "莹", "妍",
    "雅", "诗", "柔", "萌", "甜", "桃", "樱", "幼", "萝", "酱", "少女", "小姐", "太太",
    "夫人", "公主", "女神", "姑娘", "丫头", "兔", "猫娘", "女儿",
)
MALE_HINTS = (
    "哥", "弟", "叔", "伯", "爷", "舅", "兄", "先生", "少爷", "汉子", "君", "爹", "爸",
    "老公", "大叔", "男人", "公子",
)
FEMALE_EMOJI = ("\U0001FA77", "\U0001F495", "\U0001F49E", "\U0001F338", "\U0001F380",
                "\U0001F484", "\U0001F457", "\U0001F485", "\U0001F467", "\u2640")
MALE_EMOJI = ("\U0001F468", "\u2642", "\U0001F466")


def guess_gender(name):
    """按名字/昵称猜性别："男" / "女" / ""（猜不出来）。

    只做加减分：女性线索多就用女、男性线索多就用男、打平或没线索返回空。
    这是**屋主 2026-11-06 明确要求的口径**（性别资料缺失时不阻塞剧情），不是事实判定。
    """
    text = name if isinstance(name, str) else ""
    if not text:
        return ""
    female = sum(text.count(word) for word in FEMALE_HINTS)
    male = sum(text.count(word) for word in MALE_HINTS)
    female += sum(1 for emoji in FEMALE_EMOJI if emoji in text)
    male += sum(1 for emoji in MALE_EMOJI if emoji in text)
    if female > male:
        return "女"
    if male > female:
        return "男"
    return ""


def normalize_gender(value):
    """平台 gender 归一："男" / "女" / "未知" / ""（空串＝平台没给，交给调用方回退本地档案）。

    规则来自方案 §8：只认平台明确填写的值；非二元标签（other / femboy / questioning…）
    一律按"未知"处理 —— 既不猜成男也不猜成女，让提示词用姓名或中性称呼。
    """
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if not text:
        return ""
    if text in FEMALE_VALUES:
        return "女"
    if text in MALE_VALUES:
        return "男"
    return "未知"


def rank_level(name):
    if not isinstance(name, str):
        return None
    key = name.strip()
    if key in RANK_LEVEL:
        return RANK_LEVEL[key]
    # 名称里带后缀（如「主管（代理）」）时按最长前缀匹配一次
    hits = [item for item in RANKS if key.startswith(item)]
    if len(hits) == 1:
        return RANK_LEVEL[hits[0]]
    return None


def relation(actor_level, target_level):
    """发起者相对目标的关系："up"=发起者更高，"down"=更低，"peer"=平级。"""
    if actor_level > target_level:
        return "up"
    if actor_level < target_level:
        return "down"
    return "peer"


def base_probability(actor_level, target_level):
    """基础成功率（百分比，float）。差 6 级及以上按 99/1 封顶。"""
    distance = abs(actor_level - target_level)
    if distance >= 6:
        high, low = PROBABILITY_FAR
    else:
        high, low = PROBABILITY_TABLE[distance]
    return high if actor_level > target_level else low


def max_extra_for(base):
    """把基础成功率推到 99% 所需的最小加币（可能为 0）。"""
    need = PROBABILITY_CAP - base
    if need <= 0:
        return 0
    return min(MAX_EXTRA, int(-(-need // EXTRA_STEP)))       # ceil(need / 0.5)


def final_probability(base, extra):
    """最终成功率：min(99, 基础 + 加币×0.5)。加币越界返回 None。"""
    if isinstance(extra, bool) or not isinstance(extra, int):
        return None
    if extra < 0 or extra > MAX_EXTRA:
        return None
    return min(float(PROBABILITY_CAP), base + extra * EXTRA_STEP)


def total_fee(extra):
    """首段总费用 = 5 + 加币。"""
    return BASE_FEE + int(extra)


def split_ratio(rel):
    return SPLIT_RATIO[rel]


def split_amount(total, ratio):
    """分账金额向下取整；系统消耗 = 总额 - 分账。"""
    return int(total * ratio)                     # int() 对正数即 floor


def format_probability(value):
    """90.0 → '90%'，67.5 → '67.5%'。"""
    if value is None:
        return "?"
    if abs(value - round(value)) < 1e-9:
        return "{}%".format(int(round(value)))
    return "{:.1f}%".format(value)


# ---------------------------------------------------------------- 金额解析

def take_extra(tokens):
    """从目标之后的 token 里取出加币金额。

    约定（方案 §4.2/§4.3）：**目标之后第一个独立整数**就是加币；
    它后面的全部内容都是剧情方向，方向里的数字不再当金额。
    返回 (extra, direction_tokens, error)。
    """
    tokens = list(tokens or [])
    if not tokens:
        return 0, [], None
    head = tokens[0]
    if not INTEGER.match(head):
        return 0, tokens, None                     # 第一个词不是整数 ⇒ 没填金额
    extra = int(head)
    if extra > MAX_EXTRA:
        return None, [], "额外加币最多 {} 币（你写了 {} 币）".format(MAX_EXTRA, extra)
    return extra, tokens[1:], None


def parse_extra_text(text):
    """单独解析一个金额（用于「#潜规则 40」这种只有金额的引用模式以外场景）。"""
    key = (text or "").strip()
    if not key:
        return 0
    if not INTEGER.match(key):
        raise RuleProblem("额外加币只能是 0~{} 的整数".format(MAX_EXTRA))
    extra = int(key)
    if extra > MAX_EXTRA:
        raise RuleProblem("额外加币最多 {} 币（你写了 {} 币）".format(MAX_EXTRA, extra))
    return extra


def checkout(extra, base):
    """一次发起/续写的账目摘要。超上限时给出"最多可用金额"的提示。"""
    cap_amount = max_extra_for(base)
    if extra > cap_amount:
        raise RuleProblem(
            "按这个职级差，加 {} 币就到 {}% 封顶了；本次最多可用 {} 币".format(
                cap_amount, PROBABILITY_CAP, cap_amount))
    probability = final_probability(base, extra)
    return {
        "extra": extra,
        "base": base,
        "probability": probability,
        "fee": total_fee(extra),
        "cap_amount": cap_amount,
    }


def help_text():
    """`#潜规则帮助` 的正文（版本 A，屋主 2026-11-06 选定；多行发送）。"""
    lines = [
        "🎫 潜规则怎么玩",
        "",
        "发起：#潜规则 目标 [加币] [方向]｜或引用对方消息后只发 #潜规则",
        "花钱：首段 {} 币起，加币 0~{}（每 1 币 +{}%，最高 {}%）".format(
            BASE_FEE, MAX_EXTRA, EXTRA_STEP, PROBABILITY_CAP),
        "续写：#继续 [方向]，{} 币一段，最多 {} 段｜结束：#结束".format(
            CONTINUE_FEE, MAX_CONTINUES),
        "开关：#允许潜规则 / #拒绝潜规则｜查自己：#潜规则状态",
        "全部数值：#潜规则规则",
    ]
    return "\n".join(lines)


def rules_text():
    """`#潜规则规则` 的正文（纯文本，群里可直接读）。"""
    lines = [
        "🎫 潜规则规则",
        "费用：首段 {} 币起；额外加币 0~{} 币，每 1 币 +{} 个百分点，最高 {}%。".format(
            BASE_FEE, MAX_EXTRA, EXTRA_STEP, PROBABILITY_CAP),
        "概率（发起者比目标高 / 低）：平级 50%｜1级 60/40｜2级 70/30｜3级 80/20｜4级 90/10"
        "｜5级 95/5｜6级及以上 99/1。",
        "分账：上级→下级 50%，平级 40%，下级→上级 30%（向下取整，失败不分成）。",
        "续写：每段 {} 币，最多 {} 段（首段 + 续写共 {} 段），不重抽概率。".format(
            CONTINUE_FEE, MAX_CONTINUES, MAX_CONTINUES + 1),
        "退款：生成失败或平台明确未投递，退回本次费用；正常概率失败照扣、不分成。",
        "次数：不限，扣币即可（同一时间只能在一场里）。",
        "指令：#潜规则 目标 [加币] [方向]｜#潜规则 [加币] [方向]（引用对方消息）"
        "｜#继续 [方向]｜#结束｜#潜规则状态｜#潜规则帮助",
    ]
    return "\n".join(lines)
