"""P12-P：`/查` 对所有人开放（房间群的指令闸门）的守卫测试。

背景（2026-09-27 屋主："开放 /查 指令给所有人"）：

玫瑰房里客人的 `/` 指令一律被改成管家的婉拒（"这间屋子只有 Vv姐姐 能吩咐我哦～"），
于是客人发 `/查 某人` 也被挡了。但 `/查` 是"看档案"，不是"吩咐管家做事"，
所以它要从那道闸门里豁免出去（Guest can query, cannot command）。

判定集中在 `bot._room_cmd_refused` / `bot.is_open_room_cmd`，这里钉住：
  - 开放：`/查`、`/whois`、`/资料`（含不带名字的 `/查`）、
    `/注册`（2026-10-02 屋主开放：客人在玫瑰房可给自己建档；
    代他人注册仍被 reg_self_only 拦）；
  - 不开放：其余一切 `/` 指令（仍走婉拒）；
  - **主人不受影响**：主人发什么指令都执行。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                      # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
VV = "8dbfa74f-ed8d-4d2b-ba80-b35126996b0f"          # 屋主
NUONUO = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"      # 糯糯姐
GUEST = "ee68905d-c027-4328-9223-2b04cb551c63"       # 一般客人


# ---------------------------------------------------------------- 谁被放行

def test_profile_cmd_family_is_open():
    for c in ("/查 某人", "/whois 某人", "/资料 某人", "/查", "/查  某人 ",
              "/注册"):
        assert bot.is_open_room_cmd(c) is True, c


def test_other_commands_are_not_open():
    for c in ("/写诗 月亮", "/算命", "/帮助", "/发骚 x", "/投票 x",
              "查 某人", "", None):
        assert bot.is_open_room_cmd(c) is False, c


def test_guest_query_is_not_refused_in_room():
    assert bot._room_cmd_refused(ROSE, "/查 橘先生", GUEST) is False
    assert bot._room_cmd_refused(ROSE, "/whois 橘先生", GUEST) is False
    assert bot._room_cmd_refused(ROSE, "/资料 橘先生", GUEST) is False
    assert bot._room_cmd_refused(ROSE, "/注册", GUEST) is False, \
        "2026-10-02 屋主开放：客人在玫瑰房可给自己建档"


def test_guest_other_commands_are_still_refused():
    for c in ("/写诗 月亮", "/算命", "/帮助"):
        assert bot._room_cmd_refused(ROSE, c, GUEST) is True, c


def test_owners_are_never_refused():
    for uid in (VV, NUONUO):
        for c in ("/写诗 月亮", "/查 某人", "/算命"):
            assert bot._room_cmd_refused(ROSE, c, uid) is False, (uid, c)


def test_non_room_groups_are_not_affected():
    """认证群没有这道闸门（客人本来就能用 /查）—— 这里必须返回 False，别误伤."""
    assert bot._room_cmd_refused(AUTH, "/写诗 月亮", GUEST) is False
    assert bot._room_cmd_refused(AUTH, "/查 某人", GUEST) is False


def test_empty_command_is_not_refused():
    assert bot._room_cmd_refused(ROSE, "", GUEST) is False


# ---------------------------------------------------------------- 接线

def test_main_loop_uses_the_single_gate():
    """闸门只能有一个判定点：`main_loop` 必须调 `_room_cmd_refused`."""
    # P3-19 起主循环体在 runtime/mainloop.py（bot 模块桥 B 前缀，子串兼容）
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    assert "_room_cmd_refused(cid, cmd, cmd_sender)" in src
    # 婉拒那条路也要放行开放指令（否则 `/查` 会从另一条路被挡）
    # P3-3-3 起婉拒分支在 runtime/room.py（房间消息处理块整体搬出）
    src_r = (ROOT / "runtime" / "room.py").read_text(encoding="utf-8")
    assert "and not is_open_room_cmd(r_text)" in src_r
