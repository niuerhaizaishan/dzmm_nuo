"""P12-N：房间布局配图的守卫测试（改布局发对应图、复原回到玫瑰房）。

屋主 2026-09-27 给的四种布局：审问室 / 玫瑰泳池 / 户外烧烤 / 玫瑰房（初始）。
这里盯三件事：

1. **认得出**屋主那句「{me}改变房间布局（XX风格）」的各种自然说法；
2. **认不出就不发**（比如"赛博朋克"）—— 宁可不发，也不能发一张不相干的房间；
3. **别人下不了命令**：客人说"改成泳池"不该换布局、更不该发图。
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import bot_photo, room_layout as RL                    # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"          # 屋主
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"       # 一般客人


@pytest.fixture(autouse=True)
def _fresh():
    bot_photo.reset_dedupe()
    yield
    bot_photo.reset_dedupe()


class _Bot:
    def __init__(self):
        self.calls = []

    def send_photo_url(self, cid, url, caption=""):
        self.calls.append((cid, url))
        return "mid-1"


class _Sender:
    def __init__(self, bot):
        self.bot = bot

    def bot_for(self, cid):
        return self.bot


# ---------------------------------------------------------------- 认布局

@pytest.mark.parametrize("text,expect", [
    ("薇丝改变房间布局（审问室风格）", "审问室"),
    ("换成审问室吧", "审问室"),
    ("把屋子布置成审讯室", "审问室"),
    ("改一下房间，要泳池那种", "玫瑰泳池"),
    ("变成泳池派对风格", "玫瑰泳池"),
    ("把露台弄成户外烧烤", "户外烧烤"),
    ("来个BBQ风格", "户外烧烤"),
    ("重新布置成烧烤摊", "户外烧烤"),
    ("恢复原样", "玫瑰房"),
    ("换回原来的样子", "玫瑰房"),
    ("改回玫瑰房", "玫瑰房"),
    ("撤回，回到原来", "玫瑰房"),
])
def test_layout_of_hits(text, expect):
    assert RL.layout_of(text) == expect, text


@pytest.mark.parametrize("text", ["", "今天天气不错", "赛博朋克风格", "高科技实验室", "给我来杯酒"])
def test_layout_of_misses(text):
    assert RL.layout_of(text) == "", text


def test_every_layout_has_a_hosted_url():
    for name in RL.FILE_OF:
        assert name in [n for n, _ in RL.LAYOUTS], name
        assert RL.photo_url(name).startswith("https://www.nuonuo.pw/img/room/")
        assert RL.photo_url(name).endswith(".jpg")
    assert RL.photo_url("不存在的布局") == ""
    assert len(RL.FILE_OF) == 4, "屋主给了四张图：审问室/泳池/烧烤/玫瑰房"


# ---------------------------------------------------------------- 谁能触发

@pytest.mark.parametrize("text", [
    "薇丝改变房间布局（审问室风格）", "换成泳池", "布置成烧烤", "恢复原样",
])
def test_owner_can_trigger(text):
    assert RL.wants_photo(text, ROSE, VV) is True, text


@pytest.mark.parametrize("text", ["改成泳池", "换成审问室", "换个风格"])
def test_guest_cannot_trigger(text):
    assert RL.wants_photo(text, ROSE, GUEST) is False, text


def test_unknown_style_sends_nothing():
    assert RL.wants_photo("薇丝把房间改成赛博朋克风格", ROSE, VV) is False


def test_bare_room_name_is_not_a_command():
    """光说"玫瑰房"（没有改/换/恢复的意图）不发图 —— 免得闲聊就换布局。"""
    assert RL.wants_photo("玫瑰房真好看", ROSE, VV) is False


# ------------------------------------------------- 光报布局名也算（屋主 2026-09-27）

@pytest.mark.parametrize("text,expect", [
    ("泳池", "玫瑰泳池"), ("薇丝 泳池", "玫瑰泳池"), ("@薇丝 泳池", "玫瑰泳池"),
    ("泳池风格", "玫瑰泳池"), ("审问室", "审问室"), ("薇丝 审问室", "审问室"),
    ("烧烤", "户外烧烤"), ("户外烧烤", "户外烧烤"), ("玫瑰房", "玫瑰房"),
    ("恢复", "玫瑰房"), ("泳池吧", "玫瑰泳池"),
])
def test_bare_layout_name_works_for_owner(text, expect):
    assert RL._bare_layout(text, ROSE) == expect, text
    assert RL.is_layout_cmd(text, ROSE, VV) is True, text


@pytest.mark.parametrize("text", [
    "我想去泳池", "泳池真好看", "泳池照片", "今天聊点别的", "玫瑰房不错",
])
def test_bare_name_does_not_fire_on_chatter(text):
    assert RL._bare_layout(text, ROSE) == "", text
    assert RL.is_layout_cmd(text, ROSE, VV) is False, text


def test_bare_name_still_owner_only():
    assert RL.is_layout_cmd("泳池", ROSE, GUEST) is False


# ---------------------------------------------------------------- 发送

def test_maybe_send_posts_the_matching_url_once():
    bot = _Bot()
    s = _Sender(bot)
    assert asyncio.run(RL.maybe_send(s, ROSE, "薇丝改变房间布局（审问室风格）", VV)) is True
    assert bot.calls == [(ROSE, RL.photo_url("审问室"))]
    # 同一种布局短时间内不重复发
    assert asyncio.run(RL.maybe_send(s, ROSE, "还是审问室", VV)) is False
    assert len(bot.calls) == 1
    # 换一种布局照发
    assert asyncio.run(RL.maybe_send(s, ROSE, "换成泳池", VV)) is True
    assert len(bot.calls) == 2


def test_send_layout_used_by_ghost_restore():
    bot = _Bot()
    assert asyncio.run(RL.send_layout(_Sender(bot), ROSE, RL.DEFAULT_LAYOUT,
                                      reason="屋子复原")) is True
    assert bot.calls == [(ROSE, RL.photo_url("玫瑰房"))]


def test_send_never_raises_without_channel_or_url():
    assert asyncio.run(RL.send_layout(_Sender(None), ROSE, "玫瑰房")) is False
    assert asyncio.run(RL.send_layout(_Sender(_Bot()), ROSE, "不存在的布局")) is False


def test_bot_wires_layout_hooks():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "_room_layout.send_layout(" in src, "屋子复原那条要发图"
    assert "_room_layout.maybe_send(" in src, "改布局那条要发图"
    assert "_layout_after = _room_layout.DEFAULT_LAYOUT" in src


def test_intro_and_layout_share_one_dedupe_table():
    """两个功能共用 bot_photo 的去重表：一个清了另一个也干净（不再各维护一份）。"""
    assert bot_photo.dup(ROSE, "layout:审问室") is False
    assert bot_photo.dup(ROSE, "layout:审问室") is True
    bot_photo.reset_dedupe()
    assert bot_photo.dup(ROSE, "layout:审问室") is False
