"""酒馆魔法/禁言（P12-CG，瑞雯群）钉点测试。

判定的纯逻辑（is_tavern_cmd/_split_duration）与 handle 流程（mock sender +
monkeypatch 平台调用）分开钉。协议形状见 `dzmm.set_member_silence`。
"""
import asyncio

import pytest

from runtime import features
from runtime import tavern


CID = "13a7287a-2ec6-419e-9646-7f30a5154e6c"   # 🍺瑞雯の酒馆🎵


@pytest.fixture(autouse=True)
def _features_default_off(monkeypatch):
    """注册表默认关；流程测试钉成开，"关"的断言单独覆盖。"""
    features.set_overrides({CID + "|tavern_magic": True,
                            CID + "|tavern_mute": True})
    tavern._LAST_TARGET.clear()
    async def authorize(self, cid, actor):
        return ""
    monkeypatch.setattr(tavern.Platform, "authorize", authorize)
    yield
    tavern._LAST_TARGET.clear()
    features.set_overrides({})


# ---------------------------------------------------------------- 前缀判定

def test_magic_prefixes():
    for spell in tavern.SPELLS:
        assert tavern.is_tavern_cmd(spell) == ("magic", spell, "")
        assert tavern.is_tavern_cmd(spell + " 张三")[0] == "magic"
        assert tavern.is_tavern_cmd(spell + " 张三")[2] == "张三"
    assert tavern.is_tavern_cmd("/除你衣物游戏") is None, "非前缀语义：不吞别的指令"
    assert tavern.is_tavern_cmd("/荧光闪烁  长名字 带空格")[2] == "长名字 带空格"


def test_mute_parse():
    assert tavern.is_tavern_cmd("/小嘴巴闭起来") == ("mute", None, "")
    assert tavern.is_tavern_cmd("/小嘴巴闭起来 10分钟") == ("mute", "10m", "")
    assert tavern.is_tavern_cmd("/小嘴巴闭起来 10分钟 张三") == ("mute", "10m", "张三")
    assert tavern.is_tavern_cmd("/小嘴巴闭起来 张三 1小时") == ("mute", "1h", "张三")
    assert tavern.is_tavern_cmd("/小嘴巴闭起来 永久") == ("mute", "forever", "")
    assert tavern.is_tavern_cmd("/小嘴巴闭起来 24小时 张三") == ("mute", "24h", "张三")
    assert tavern.is_tavern_cmd("/小嘴巴闭起来 7天 张三") == ("mute", "7d", "张三")
    assert tavern.is_tavern_cmd("/小嘴巴闭起来 张三") == ("mute", None, "张三"), \
        "没写时长：原样留给 handle 提示用法"


def test_unmute_parse():
    assert tavern.is_tavern_cmd("/恢复如初") == ("unmute", None, "")
    assert tavern.is_tavern_cmd("/恢复如初 张三") == ("unmute", None, "张三")


def test_no_false_positives():
    for t in ("/查 清歌", "/帮助", "除你衣物", "", None, "/小嘴巴闭起来啦"):
        assert tavern.is_tavern_cmd(t) is None


def test_feat_of_mapping():
    """登记表名必须是 `_FEATURES`（test_feature_registry 的静态扫描认这个形状）。"""
    assert tavern._FEATURES["magic"] == "tavern_magic"
    assert tavern._FEATURES["mute"] == "tavern_mute"
    assert tavern._FEATURES["unmute"] == "tavern_mute"


def test_resolve_target_falls_back_to_history(monkeypatch):
    """被回复消息不在本轮窗口时，必须补拉最近历史找 sent_by（2026-11-02 实测）。"""
    monkeypatch.setattr(tavern, "get_full_name", lambda uid: "张三")
    old_msg = {"message_id": "quoted-1", "sent_by": "uid-old"}
    monkeypatch.setattr("dzmm.get_messages", lambda cid, n: [old_msg])
    message = {"content": {"reference": {"id": "quoted-1"}}}
    uid, name = tavern._resolve_target(CID, "", message, msgs=[])
    assert (uid, name) == ("uid-old", "张三")
    # 本轮窗口命中时不发平台请求
    uid2, _ = tavern._resolve_target(CID, "", message, msgs=[old_msg])
    assert uid2 == "uid-old"


# ---------------------------------------------------------------- handle 流程

class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, text, cid):
        self.sent.append((text, cid))
        return "mid-1"


@pytest.fixture
def target_env(monkeypatch):
    """钉住目标解析与平台调用，记录调用参数。"""
    calls = {"silence": []}

    def fake_resolve(cid, name, message, msgs):
        if name:
            return ("uid-target", "张三")
        return "", ""

    def fake_silence(cid, uid, duration):
        calls["silence"].append((cid, uid, duration))
        return {"silencedUntil": "2026-11-02T10:00:00Z"}

    monkeypatch.setattr(tavern, "_resolve_target", fake_resolve)
    monkeypatch.setattr(tavern, "set_member_silence", fake_silence)
    return calls


def test_magic_renders_name(target_env):
    s = FakeSender()
    assert asyncio.run(tavern.handle(s, CID, "uid-a", "/除你衣物 张三",
                                     {}, [], dry=False)) is True
    text, cid = s.sent[0]
    assert "张三" in text
    assert "小熊宝宝内裤" in text
    assert cid == CID


def test_magic_without_target_prompts(target_env):
    s = FakeSender()
    asyncio.run(tavern.handle(s, CID, "uid-a", "/荧光闪烁", {}, []))
    assert "对谁施法" in s.sent[0][0]


def test_mute_without_duration_prompts(target_env):
    s = FakeSender()
    asyncio.run(tavern.handle(s, CID, "uid-a", "/小嘴巴闭起来 张三", {}, []))
    assert "缝多久" in s.sent[0][0]
    assert target_env["silence"] == [], "未指定时长不得调平台"


def test_mute_success_reply(target_env):
    s = FakeSender()
    asyncio.run(tavern.handle(s, CID, "uid-a", "/小嘴巴闭起来 1小时 张三", {}, []))
    assert target_env["silence"] == [(CID, "uid-target", "1h")]
    text, _ = s.sent[0]
    assert "Quietus" in text and "张三" in text and "1小时" in text


def test_unmute_success_reply(target_env):
    s = FakeSender()
    asyncio.run(tavern.handle(s, CID, "uid-a", "/恢复如初 张三", {}, []))
    assert target_env["silence"] == [(CID, "uid-target", None)]
    text, _ = s.sent[0]
    assert "Reparo" in text and "张三" in text


def test_mute_forbidden_becomes_reply(target_env, monkeypatch):
    def forbidden(cid, uid, duration):
        raise RuntimeError("setMemberSilence: 不能禁言群主、管理员或自己")
    monkeypatch.setattr(tavern, "set_member_silence", forbidden)
    s = FakeSender()
    asyncio.run(tavern.handle(s, CID, "uid-a", "/小嘴巴闭起来 10分钟 张三", {}, []))
    text, _ = s.sent[0]
    assert "不能禁言群主、管理员或自己" in text
    assert "setMemberSilence" not in text, "内部前缀不外泄"


def test_feature_off_is_silent(target_env):
    features.set_overrides({CID + "|tavern_magic": False,
                            CID + "|tavern_mute": True})
    s = FakeSender()
    assert asyncio.run(tavern.handle(s, CID, "uid-a", "/除你衣物 张三",
                                     {}, [], dry=False)) is True
    assert s.sent == []


def test_dry_mode_no_send(target_env):
    s = FakeSender()
    asyncio.run(tavern.handle(s, CID, "uid-a", "/除你衣物 张三", {}, [], dry=True))
    assert s.sent == []
    assert target_env["silence"] == []


def test_previous_target_is_remembered_and_scoped_to_issuer(target_env):
    sender = FakeSender()
    asyncio.run(tavern.handle(sender, CID, "uid-a", "/荧光闪烁 张三", {}, []))
    asyncio.run(tavern.handle(sender, CID, "uid-a", "/荧光闪烁", {}, []))
    assert "张三" in sender.sent[-1][0]
    asyncio.run(tavern.handle(sender, CID, "uid-b", "/荧光闪烁", {}, []))
    assert "对谁施法" in sender.sent[-1][0]


def test_non_admin_cannot_mute_or_unmute(target_env, monkeypatch):
    async def denied(self, cid, actor):
        raise tavern.ModerationUnavailable("只有当前群管理员可以操作")
    monkeypatch.setattr(tavern.Platform, "authorize", denied)
    sender = FakeSender()
    for cmd in ("/小嘴巴闭起来 永久 张三", "/恢复如初 张三"):
        asyncio.run(tavern.handle(sender, CID, "ordinary-member", cmd, {}, []))
        assert "只有当前群管理员" in sender.sent[-1][0]
    assert target_env["silence"] == []


def test_permission_lookup_failure_does_not_mute(target_env, monkeypatch):
    async def unavailable(self, cid, actor):
        raise TimeoutError()
    monkeypatch.setattr(tavern.Platform, "authorize", unavailable)
    sender = FakeSender()
    asyncio.run(tavern.handle(sender, CID, "uid-a", "/小嘴巴闭起来 1小时 张三", {}, []))
    assert "本次未执行" in sender.sent[-1][0]
    assert target_env["silence"] == []


def test_explicit_missing_target_never_mutes_previous_target(target_env, monkeypatch):
    tavern._LAST_TARGET[(CID, "uid-a")] = ("previous-uid", "上一个人")
    monkeypatch.setattr(tavern, "_resolve_target", lambda *args: ("", ""))
    sender = FakeSender()
    asyncio.run(tavern.handle(sender, CID, "uid-a", "/小嘴巴闭起来 1小时 不存在的人", {}, []))
    assert "没找到目标" in sender.sent[-1][0]
    assert target_env["silence"] == []


@pytest.mark.parametrize("status,payload", [(403, {}), (500, {}), (200, {})])
def test_silence_api_rejects_errors_and_unconfirmed_results(monkeypatch, status, payload):
    from unittest.mock import Mock
    import dzmm
    response = Mock(status_code=status)
    response.json.return_value = payload
    monkeypatch.setattr(dzmm.requests, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(dzmm, "_throttle", lambda *args, **kwargs: None)
    monkeypatch.setattr(dzmm, "_note_resp", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError):
        dzmm.set_member_silence(CID, "target", "1h", cookies={})


@pytest.mark.parametrize("duration,until", [("1h", "2026-11-03T00:00:00Z"), (None, None)])
def test_silence_api_unwraps_confirmed_trpc_result(monkeypatch, duration, until):
    from unittest.mock import Mock
    import dzmm
    response = Mock(status_code=200)
    response.json.return_value = {"result": {"data": {"json": {"silencedUntil": until}}}}
    monkeypatch.setattr(dzmm.requests, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(dzmm, "_throttle", lambda *args, **kwargs: None)
    assert dzmm.set_member_silence(CID, "target", duration, cookies={}) == {"silencedUntil": until}
