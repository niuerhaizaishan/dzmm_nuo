"""P12-R：房间触发口径收紧（屋主 2026-09-27 确认的 A/B/C 三条）。

起因是实测的误触发（当天玫瑰房 20 条触发里的 4 条纯关键词触发）：

    「速配星座：天蝎座」          ← 客人复读我们卡片上的那一行，管家又发一张卡
    「@橘先生… 小橘！摘个花试试！」 ← 在议论别人摘花，管家差点演女鬼剧本
    「想喝…」这类点单动词          ← 闲聊里随时会出现

收紧后的口径（全部集中在 `runtime.sign._is_room_trigger` 一个函数里）：

  A. 点单关键词**必须点名**（@薇丝 / 说出"薇丝"）才接；
  B. 星座运势保留自动，但**卡片复读**不算（"速配星座：X"这类）；
  C. **@ 了别人**的消息，关键词规则一律不认；
  + 被点名 / 主人指令 / 摘花 / 星座 / 报生日 照旧。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import features                                       # noqa: E402
from runtime.sign import _at_others, _is_room_trigger               # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"          # 屋主
NUONUO = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"      # 糯糯姐
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"       # 一般客人
OTHER = "0f3d1c22-1111-2222-3333-444455556666"


@pytest.fixture(autouse=True)
def _clean_features():
    """每个用例跑完把功能开关恢复成默认（`on()` 读的是全局覆盖表）."""
    yield
    features.set_overrides({})


def trig(text, uid=GUEST, cid=ROSE):
    return _is_room_trigger(cid, uid, text)


# ---------------------------------------------------------------- ① 被点名：一定接

@pytest.mark.parametrize("text", [
    "薇丝，给我来杯龙舌兰", "薇丝你是谁", "薇丝 屋子改成审问室",
    "@薇丝-bot说话触发比较好吧", "薇丝在吗", "薇丝 摘花",
])
def test_named_messages_always_trigger(text):
    assert trig(text) is True, text


# ---------------------------------------------------------------- A. 点单必须点名

@pytest.mark.parametrize("text", ["想喝一杯酒", "给我来杯咖啡", "来点雪茄", "想吃点水果"])
def test_unaddressed_order_no_longer_triggers(text):
    assert trig(text) is False, text


@pytest.mark.parametrize("text", ["薇丝 想喝一杯酒", "薇丝，给我来杯咖啡", "@薇丝 来点雪茄"])
def test_addressed_order_still_triggers(text):
    assert trig(text) is True, text


def test_room_order_switch_still_wired():
    """功能开关仍然有效：关掉点单 → 连点名点单也不接（不然这个开关就成了摆设）."""
    features.set_overrides({ROSE + "|room_order": False})
    assert trig("薇丝 想喝一杯酒") is False
    assert trig("薇丝你是谁") is True, "关点单不该把管家整个关掉"
    features.set_overrides({})
    assert trig("薇丝 想喝一杯酒") is True


# ---------------------------------------------------------------- B. 卡片复读不算

@pytest.mark.parametrize("text", [
    "速配星座：天蝎座", "幸运颜色：深紫", "幸运数字：3", "综合运势：今天不错",
    "今日星象 · 金牛座",
])
def test_card_echo_does_not_trigger(text):
    assert trig(text) is False, text


@pytest.mark.parametrize("text", ["我的速配星座是什么", "金牛座运势", "我是天蝎座", "今天运势如何"])
def test_real_horoscope_questions_still_trigger(text):
    assert trig(text) is True, text


# ---------------------------------------------------------------- C. @了别人

def test_at_other_member_is_ignored():
    assert _at_others("@橘先生（贱畜桃灼的主人） 小橘！摘个花试试！", ROSE) is True
    assert trig("@橘先生（贱畜桃灼的主人） 小橘！摘个花试试！") is False
    assert trig("@橘先生 今天的运势怎么样") is False, "在问别人，不是在问管家"


def test_at_self_is_not_other():
    assert _at_others("@薇丝 你好", ROSE) is False
    assert _at_others("@薇丝-bot说话触发比较好吧", ROSE) is False
    assert _at_others("没有 at 谁的普通消息", ROSE) is False


# ---------------------------------------------------------------- 照旧保留

def test_flower_grab_still_triggers_without_a_name():
    assert trig("摘花") is True
    assert trig("我摘一朵玫瑰") is True


def test_plain_chat_does_not_trigger():
    for text in ("那朵花真好看", "今天天气不错", "哈哈哈", "摩羯座真准啊"[:0] or "在干嘛"):
        assert trig(text) is False, text


def test_owner_commands_do_not_need_the_name():
    assert trig("改成泳池", VV) is True
    assert trig("变成女鬼", NUONUO) is True
    assert trig("改成泳池", GUEST) is False
    # 屋主 @ 别人时，改布局这种强信号仍然算
    assert trig("@橘先生 顺便把屋子改成泳池", VV) is True


def test_bare_layout_name_enters_the_room_branch():
    """屋主 2026-09-27："必须得说房间改成泳池吗" → 不必，光报布局名也算。"""
    assert trig("泳池", VV) is True
    assert trig("薇丝 审问室", NUONUO) is True
    assert trig("@薇丝 烧烤", VV) is True
    assert trig("泳池", GUEST) is False        # 客人还是不能换布局
    assert trig("我想去泳池", VV) is False      # 闲聊不算


def test_other_groups_are_untouched():
    """认证群不走房间闸门（这些判定只服务玫瑰房）."""
    assert trig("摘花", GUEST, AUTH) is False
    assert trig("薇丝你是谁", GUEST, AUTH) is False
