"""P12-O：女鬼场景配图的守卫测试。

屋主口径："有人摘花触发女鬼，或者命令你变为女鬼场景的情况下，发送这张图。"
摘花那条是**剧本事件**（不靠文本判定，`bot.py` 里直接调 `send`），这里主要盯命令那条：
认出「变成女鬼 / 演一下女鬼场景」，且**只有屋主**能下这个命令。
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import bot_photo, ghost_photo as GP                    # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"
NUONUO = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"


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
        return "mid-ghost"


class _Sender:
    def __init__(self, bot):
        self.bot = bot

    def bot_for(self, cid):
        return self.bot


def test_url_is_the_hosted_ghost_shot():
    assert GP.URL.startswith("https://www.nuonuo.pw/img/room/")
    assert GP.URL.endswith("ghost.jpg")


@pytest.mark.parametrize("text", [
    "薇丝变成女鬼", "薇丝 演一下女鬼场景", "进入女鬼模式", "召唤红衣女鬼",
    "薇丝放出来女鬼", "来个女鬼场景", "切换成闹鬼的样子",
])
def test_owner_command_hits(text):
    assert GP.wants_photo(text, ROSE, VV) is True, text
    assert GP.wants_photo(text, ROSE, NUONUO) is True, text      # 糯糯姐也算主人


@pytest.mark.parametrize("text", ["", "女鬼真吓人", "这房间闹鬼吗", "给我来杯酒"])
def test_no_command_word_no_photo(text):
    assert GP.wants_photo(text, ROSE, VV) is False, text


def test_guest_cannot_command_the_ghost():
    assert GP.wants_photo("薇丝变成女鬼", ROSE, GUEST) is False


def test_long_small_talk_is_ignored():
    long = "薇丝你说这个女鬼到底是什么来头啊我有点好奇能不能讲讲她变成女鬼之前的故事呢"
    assert GP.wants_photo(long, ROSE, VV) is False


def test_send_posts_the_url_and_dedupes():
    bot = _Bot()
    s = _Sender(bot)
    assert asyncio.run(GP.maybe_send(s, ROSE, "薇丝变成女鬼", VV)) is True
    assert bot.calls == [(ROSE, GP.URL)]
    assert asyncio.run(GP.maybe_send(s, ROSE, "再演一下女鬼", VV)) is False   # 短时间内不重复
    assert len(bot.calls) == 1


def test_send_never_raises_without_channel():
    assert asyncio.run(GP.send(_Sender(None), ROSE)) is False
    assert asyncio.run(GP.send(object(), ROSE)) is False


def test_bot_wires_both_ghost_paths():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "_ghost_photo.send(" in src, "摘花放鬼那条要发图"
    assert "_ghost_photo.maybe_send(" in src, "命令进入女鬼场景那条要发图"
    assert '_ghost_after = "摘花放鬼"' in src
    assert '_ghost_after = "命令"' in src


def test_ghost_command_really_enters_the_state():
    """屋主 2026-09-27："要进状态啊" —— 命令必须真的把房间切进女鬼状态，
    而不是只演一段 + 发张图（否则链路不闭环：没人能"复原"这间屋子）。"""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    i = src.index("命令女鬼上场")
    window = src[max(0, i - 700):i + 700]
    assert 'group_state["rose_ghost"] = {"active": True' in window
    assert "_personas.fill(ROOM_GHOST_TEXT, cid)" in window


def test_owner_ghost_command_reaches_the_room_branch():
    """光说"变成女鬼"（不带管家名字）也要能进房间分支，否则命令石沉大海。"""
    from runtime.sign import _is_room_trigger
    assert _is_room_trigger(ROSE, VV, "变成女鬼") is True
    assert _is_room_trigger(ROSE, NUONUO, "演一下女鬼场景") is True
    assert _is_room_trigger(ROSE, GUEST, "变成女鬼") is False     # 客人不算下命令
    assert _is_room_trigger(ROSE, VV, "女鬼真吓人") is False      # 闲聊不触发


def test_dedupe_is_shared_with_other_photos():
    """三个配图功能共用一张去重表（bot_photo），不会各记各的."""
    assert bot_photo.dup(ROSE, "ghost") is False
    assert bot_photo.dup(ROSE, "ghost") is True
    bot_photo.reset_dedupe()
    assert bot_photo.dup(ROSE, "ghost") is False
