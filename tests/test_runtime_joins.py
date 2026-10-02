"""P3 Step 3-1：入群链路抽到 runtime/joins.py 的行为测试。

对应 EXECUTION-PLAN §Phase 3 Step 3-1（_handle_joins 抽出）。
结构不变量由 test_p8s4_split.py 的自动守卫覆盖（同名双定义 / runtime 禁 import bot）；
这里钉**行为**：检测、筛查分路、撞码延迟、分派四支、欢迎语闸门。
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import features as F                                 # noqa: E402
from runtime import joins as J                                    # noqa: E402
import state_store                                                # noqa: E402


def _join_msg(uid: str) -> dict:
    return {"sent_by": uid, "content": {"text": "xxx 加入了群聊"}}


class _Sender:
    """假发送端：记录 (text, cid)。"""

    def __init__(self):
        self.sent = []

    async def send(self, text, cid):
        self.sent.append((text, cid))
        return True


@pytest.fixture
def quiet(monkeypatch):
    """把外部世界全部换掉：不碰平台 API、不碰飞书、不真睡 2 秒。"""
    sent = []

    async def _sleep(_s):
        pass

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return sent


def _wire_names(monkeypatch, names):
    monkeypatch.setattr(J, "get_full_name", lambda uid: names.get(uid, uid))


# ---------------------------------------------------------------- 检测

def test_detect_joins_basic_and_dedupe(monkeypatch):
    monkeypatch.setattr(J, "is_self", lambda uid: uid == "bot1")
    pending = [_join_msg("u1"), _join_msg("u1"), _join_msg("bot1"),
               {"sent_by": "u2", "content": {"text": "大家好"}}]
    assert J.detect_joins(pending) == ["u1"], "bot 排除、重复去重、普通消息不算"


def test_is_join_msg_wording():
    assert J.is_join_msg(_join_msg("u1")) is True
    assert J.is_join_msg({"sent_by": "u", "content": {"text": "我加入了群聊哦"}}) is True
    assert J.is_join_msg({"sent_by": "u", "content": {"text": "今天天气不错"}}) is False


# ---------------------------------------------------------------- 筛查

def test_tourist_is_kicked(quiet, monkeypatch):
    _wire_names(monkeypatch, {"u1": "游客9527"})
    removed, feishu = [], []
    monkeypatch.setattr(J, "remove_member", lambda cid, uid: removed.append((cid, uid)))
    monkeypatch.setattr(J, "send_weixin", lambda msg: feishu.append(msg))
    s = _Sender()
    passed = asyncio.run(J.screen_members(s, "jc1", ["u1"], False,
                                          welcome_of=lambda cid, plain=False: "Hi {name}",
                                          captcha_active=lambda: False))
    assert passed == []
    assert removed == [("jc1", "u1")]
    assert any("游客账号" in t for t, _ in s.sent), "先提醒本人"
    assert any("已移出游客账号" in t for t, _ in s.sent), "再通知群里"


def test_tourist_passes_when_gate_off(quiet, monkeypatch):
    F.set_overrides({"jc2|screen_tourist": False})
    _wire_names(monkeypatch, {"u1": "游客9527"})
    removed = []
    monkeypatch.setattr(J, "remove_member", lambda cid, uid: removed.append(uid))
    s = _Sender()
    passed = asyncio.run(J.screen_members(s, "jc2", ["u1"], False,
                                          welcome_of=lambda cid, plain=False: "Hi {name}",
                                          captcha_active=lambda: False))
    assert passed == ["u1"], "踢游客关了 → 放行去发欢迎语"
    assert removed == []


def test_male_is_kicked(quiet, monkeypatch):
    _wire_names(monkeypatch, {"u1": "张三"})
    monkeypatch.setattr(J, "get_user", lambda cid, uid: {"gender": "male"})
    monkeypatch.setattr(J, "send_weixin", lambda msg: None)
    removed = []
    monkeypatch.setattr(J, "remove_member", lambda cid, uid: removed.append(uid))
    passed = asyncio.run(J.screen_members(_Sender(), "jc3", ["u1"], False,
                                          welcome_of=lambda cid, plain=False: "Hi {name}",
                                          captcha_active=lambda: False))
    assert passed == []
    assert removed == ["u1"]


def test_verified_female_passes(quiet, monkeypatch):
    _wire_names(monkeypatch, {"u1": "小姐姐"})
    monkeypatch.setattr(J, "get_user", lambda cid, uid: {"gender": "female"})
    monkeypatch.setattr(J, "get_verified", lambda uid: {"verified": True})
    s = _Sender()
    passed = asyncio.run(J.screen_members(s, "jc4", ["u1"], False,
                                          welcome_of=lambda cid, plain=False: "Hi {name}",
                                          captcha_active=lambda: False))
    assert passed == ["u1"], "认证妹子放行，欢迎语由分派层发"
    assert s.sent == []


def test_unverified_female_warns(quiet, monkeypatch):
    _wire_names(monkeypatch, {"u1": "小姐姐"})
    monkeypatch.setattr(J, "get_user", lambda cid, uid: {"gender": "female"})
    monkeypatch.setattr(J, "get_verified", lambda uid: {"verified": False})
    monkeypatch.setattr(J, "send_weixin", lambda msg: None)
    s = _Sender()
    passed = asyncio.run(J.screen_members(s, "jc5", ["u1"], False,
                                          welcome_of=lambda cid, plain=False: "Hi {name}",
                                          captcha_active=lambda: False))
    assert passed == []
    assert any("未通过妹子认证" in t for t, _ in s.sent), "@本人提醒"
    assert not any("已移出" in t for t, _ in s.sent), "未认证不踢"


def test_null_gender_warns_admin(quiet, monkeypatch):
    _wire_names(monkeypatch, {"u1": "神秘人"})
    monkeypatch.setattr(J, "get_user", lambda cid, uid: {"gender": ""})
    monkeypatch.setattr(J, "send_weixin", lambda msg: None)
    s = _Sender()
    passed = asyncio.run(J.screen_members(s, "jc6", ["u1"], False,
                                          welcome_of=lambda cid, plain=False: "Hi {name}",
                                          captcha_active=lambda: False))
    assert passed == []
    assert any("Hi 神秘人" in t for t, _ in s.sent), "先发欢迎"
    assert any("未填性别" in t for t, _ in s.sent), "再请管理员留意"


def test_captcha_delays_screening(isolated, quiet, monkeypatch):
    """撞验证码：不踢、群里说明、飞书告警、记入 pending_screen（恢复后补筛）。"""
    F.set_overrides({"jc7|screen_pending": True, "jc7|alert_captcha": True})
    _wire_names(monkeypatch, {"u1": "普通人"})
    monkeypatch.setattr(J, "get_user", lambda cid, uid: None)  # 撞码 = 查不到
    feishu = []
    monkeypatch.setattr(J, "send_weixin", lambda msg: feishu.append(msg))
    s = _Sender()
    passed = asyncio.run(J.screen_members(s, "jc7", ["u1"], False,
                                          welcome_of=lambda cid, plain=False: "Hi {name}",
                                          captcha_active=lambda: True))
    assert passed == []
    assert any("撞平台验证码" in t for t, _ in s.sent)
    assert feishu, "延迟筛查要发飞书告警"
    assert state_store.group_get("jc7", "pending_screen") == ["u1"], "记入待筛队列"


# ---------------------------------------------------------------- 分派

def _dispatch_kwargs(cid_screen=None, cid_room=None, screen_fn=None, welcome_of=None):
    return dict(
        screen_cids=set(cid_screen or ()),
        room_cids=set(cid_room or ()),
        screen_fn=screen_fn or (lambda *a: asyncio.sleep(0, result=[])),
        welcome_of=welcome_of or (lambda cid, plain=False: "Hi {name}"),
    )


def test_dispatch_screen_branch_welcomes_passed(quiet):
    F.set_overrides({"ds1|screen": True, "ds1|screen_welcome": True})
    seen = []

    async def _screen(sender, cid, uids, dry):
        seen.append(list(uids))
        return ["u1"]

    s = _Sender()
    asyncio.run(J.handle_join_event(s, "ds1", ["u1"], False, {},
                                    **dict(_dispatch_kwargs(cid_screen=["ds1"], screen_fn=_screen))))
    assert seen == [["u1"]]
    assert s.sent == [("🤖 Hi u1", "ds1")], "放行者逐个发欢迎语（{name} 已替换）"


def test_dispatch_screen_off_still_welcomes(quiet):
    """筛查关了：不踢人，但仍发中性欢迎语（闸门只包筛查那一支）。"""
    F.set_overrides({"ds2|screen": False, "ds2|screen_welcome": True})
    seen = []

    async def _screen(sender, cid, uids, dry):
        seen.append(uids)
        return []

    s = _Sender()
    asyncio.run(J.handle_join_event(s, "ds2", ["u1"], False, {},
                                    **dict(_dispatch_kwargs(cid_screen=["ds2"], screen_fn=_screen))))
    assert seen == [], "screen=False 时不该进筛查"
    assert s.sent == [("🤖 Hi u1", "ds2")]


def test_dispatch_room_greets_and_sends_intro(quiet, monkeypatch):
    F.set_overrides({"dr1|room_welcome": True})
    photos = []

    class _Photo:
        @staticmethod
        async def send(sender, cid, reason=""):
            photos.append((cid, reason))

    monkeypatch.setattr(J, "_intro_photo", _Photo)
    s = _Sender()
    asyncio.run(J.handle_join_event(s, "dr1", ["u1"], False, {},
                                    **_dispatch_kwargs(cid_room=["dr1"])))
    assert len(s.sent) == 1 and s.sent[0][0].startswith("🤖"), "房间迎客：固定模板问好"
    assert photos == [("dr1", "进房问好")], "P12-L：问好之后补一张立绘"


def test_dispatch_room_ghost_is_silent(quiet):
    """女鬼期间屋子没人，不迎客。"""
    F.set_overrides({"dr2|room_welcome": True, "dr2|room_ghost": True})
    s = _Sender()
    asyncio.run(J.handle_join_event(s, "dr2", ["u1"], False, {"rose_ghost": {"active": True}},
                                    **_dispatch_kwargs(cid_room=["dr2"])))
    assert s.sent == []


def test_dispatch_room_welcome_off_is_silent(quiet):
    F.set_overrides({"dr3|room_welcome": False})
    s = _Sender()
    asyncio.run(J.handle_join_event(s, "dr3", ["u1"], False, {},
                                    **_dispatch_kwargs(cid_room=["dr3"])))
    assert s.sent == []


def test_dispatch_screen_welcome_gate(quiet):
    """`screen_welcome` 关了：筛查照跑，但放行者不发欢迎语。"""
    F.set_overrides({"ds3|screen": True, "ds3|screen_welcome": False})

    async def _screen(sender, cid, uids, dry):
        return ["u1"]

    s = _Sender()
    asyncio.run(J.handle_join_event(s, "ds3", ["u1"], False, {},
                                    **dict(_dispatch_kwargs(cid_screen=["ds3"], screen_fn=_screen))))
    assert s.sent == []
