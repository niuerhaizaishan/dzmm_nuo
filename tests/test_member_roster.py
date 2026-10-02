"""P12-AO：成员表必须**整份**拉回来，否则昵称缓存缺人 → 叫错名字。

线上实测（2026-09-28，茶水间 782 人）：

    chatroom.getMembers 不带 limit → 101 人   ← 平台默认静默截断，返回体里没有任何提示
    chatroom.getMembers limit=1000 → 782 人

后果不是"少几个名字"，而是**那 681 个人会被叫成一串 uid**：
昵称缓存里没有他们，`get_full_name` 在事件循环上只能退化成
「档案名 → uid 前 8 位」。用真实消息对过一遍：近 300 条里 15 个发言人，
5 个被叫成 `bcec3c30` / `4f8d0b08` 这种 uid 前缀；补上 limit 后全部恢复真名。
（认证群只有 74 人 ≤ 101，所以那边一直是对的 —— 这就是"有时候会叫错"。）

这里钉两件事：请求里必须带够大的 limit；缓存不许在本地再截断。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_get_members_sends_a_big_enough_limit(monkeypatch):
    """不带 limit 就会被平台截到 101 人 —— 这个参数是**功能**，不是优化。"""
    import dzmm

    sent = {}

    def fake_api(path, inp, cookies=None, timeout=15):
        sent["path"] = path
        sent["inp"] = inp
        return {"members": [{"id": "u1", "fullName": "甲"}]}

    monkeypatch.setattr(dzmm, "api", fake_api)
    monkeypatch.setattr(dzmm, "get_cookies", lambda: {})

    assert dzmm.get_members("cid-1") == {"u1": "甲"}
    assert sent["path"] == "chatroom.getMembers"
    limit = (sent["inp"].get("json") or {}).get("limit")
    assert isinstance(limit, int) and limit >= 1000, sent["inp"]


def test_load_member_names_keeps_every_member(monkeypatch):
    """782 人的群（茶水间真实规模）要 782 个都进缓存，本地不再截断。"""
    import runtime.identity as ident

    people = {"uid-{:04d}".format(i): "成员{}".format(i) for i in range(782)}
    monkeypatch.setattr(ident, "get_members", lambda cid: dict(people))
    monkeypatch.setattr(ident, "_name_cache", {})
    monkeypatch.setattr(ident, "_pending_names", set())
    ident._WARNED.clear()

    ident._load_member_names("tea-room")

    assert len(ident._name_cache) == 782
    assert ident._name_cache["uid-0781"] == "成员781"


def test_truncated_roster_is_reported(monkeypatch):
    """万一哪天又只拿回 101 人：日志里要能看见，而不是继续静默叫 uid。"""
    import runtime.identity as ident

    logged = []
    monkeypatch.setattr("runtime.obs.log", lambda m: logged.append(m), raising=False)
    monkeypatch.setattr(ident, "get_members",
                        lambda cid: {"u{}".format(i): "人{}".format(i) for i in range(101)})
    monkeypatch.setattr(ident, "_name_cache", {})
    monkeypatch.setattr(ident, "_pending_names", set())
    ident._WARNED.clear()

    ident._load_member_names("tea-room")

    assert any("截断" in m for m in logged), logged
