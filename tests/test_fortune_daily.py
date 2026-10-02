"""P12-AX：运势分数必须**天天变**（认证群昨天今天同分）。

屋主 2026-09-29："妹子认证群运势怎么昨天和今天都分数都没变呀，不知道 rose 群有没有这个问题。"

## 根因（线上实测 + 读码）

算分 = `命基(按本命五行，固定) + 名格 + 本命 + 人和 + 日冲/合`，其中"名格/本命"看的是
**当日天干的五行** —— 而天干五行**两天才换一次**（甲乙木、丙丁火、戊己土…）。
线上样本（时间是 CST）：

    御褚 09-29 07:37 → 51 分 ；09-30 07:12 → 51 分（那两天日柱 丙午 / 丁未，**都是火**）
    囡囡 09-29 18:32 → 77 分 ；09-30 08:28 → 86 分（跨了五行块，所以变了）

四维（财运/事业/…）的 seed 更只按**名字**算 ⇒ 同一个人永远不变。

## 修法

1. 加一项**日干阴阳**（甲丙戊庚壬 = 阳 +3，乙丁己辛癸 = 阴 −3）：阴阳是**隔天交替**的，
   所以分数必然天天变；并在卡的算式行里展示成"± 阴阳3"、命盘行标出"（阳）/（阴）"。
2. 四维 seed **补上日期**（`dt_now.toordinal()`）。
3. 同一天同一人仍然**完全稳定**（不引入随机），历史上报过的分数不会变。

对照：玫瑰房走的是 `runtime/sign.py`，它 `md5(uid|日期|星座)` ⇒ **日期进了哈希，没有这个问题**。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import fortune as FT                                  # noqa: E402


D1 = "2026-09-29"      # 丙午（火，阳）
D2 = "2026-09-30"      # 丁未（火，阴）—— 与 D1 同五行、只差阴阳
UID = "u-test-yinyang"
NAME = "御褚"


def _calc(date_str):
    return FT._fortune_calc(UID, NAME, date_str, "")


def test_same_day_is_stable():
    """同一天同一人必须完全稳定（确定性，不引入随机）。"""
    a, b = _calc(D1), _calc(D1)
    assert a["score"] == b["score"]
    assert a["dims"] == b["dims"]


def test_yinyang_alternates_daily():
    """日干阴阳隔天交替 —— 这是"天天变"的保证。"""
    days = [(datetime(2026, 9, 29) + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(4)]
    yy = [FT._fortune_calc(UID, NAME, d, "")["yinyang"] for d in days]
    assert yy == [3, -3, 3, -3] or yy == [-3, 3, -3, 3], yy


def test_score_changes_between_two_days_in_the_same_element_block():
    """09-29 / 09-30 是同五行（火）的两天 —— 修之前这两天分数一模一样。"""
    a, b = _calc(D1), _calc(D2)
    assert a["day_wx"] == b["day_wx"] == "火", "这两天的日五行本来就都是火（用来说明问题）"
    assert a["score"] != b["score"], \
        "同五行块内分数必须靠阴阳区分开，否则又出现『昨天今天一个分』"


def test_dims_change_daily():
    """四维的 seed 必须含日期（原来只按名字 ⇒ 永远不变）。"""
    dims = {tuple(sorted(_calc("2026-09-%02d" % d)["dims"].items()))
            for d in range(20, 28)}
    assert len(dims) > 1, "四维一周内一个样 = seed 里没有日期"


def test_card_shows_the_yinyang_term():
    """卡上要能看出这一项，否则"分数变了"看着像玄学。"""
    info = _calc(D1)
    assert info["day_gan"] == "丙" and info["yinyang"] == 3
    info2 = _calc(D2)
    assert info2["day_gan"] == "丁" and info2["yinyang"] == -3
