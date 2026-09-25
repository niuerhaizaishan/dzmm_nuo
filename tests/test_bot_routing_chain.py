"""发送链装配测试：`OutboxSender( RoutingSender( 账号 ) )`。

重点验证**默认不上线**：`DZMM_BOT_ROUTING` 未设时链路里不应出现 RoutingSender。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot                                              # noqa: E402
from runtime.outbox import OutboxSender                # noqa: E402
from runtime.routing import RoutingSender              # noqa: E402

CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


class FakeAccount:
    def __init__(self):
        self.calls = []

    async def send(self, text, cid, allow_fallback=True):
        self.calls.append((text, cid))
        return True

    def start(self):
        return "started"


@pytest.fixture(autouse=True)
def _quiet_log(monkeypatch):
    """测试不写 bot.log。"""
    monkeypatch.setattr(bot, "log", lambda *a, **k: None)


@pytest.fixture
def env(dbdir, monkeypatch):
    monkeypatch.setenv("DZMM_OUTBOX_DB", str(dbdir / "outbox.db"))
    for k in ("DZMM_BOT_ROUTING", "DZMM_BOT_TOKEN", "DZMM_BOT_CID",
              "DZMM_BOT_ORIGIN", "DZMM_OUTBOX"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


# ---------------------------------------------------------------- 直通

def test_dry_returns_raw_sender(env):
    acc = FakeAccount()
    assert bot._wrap_with_outbox(acc, dry=True) is acc


def test_runtime_unavailable_returns_raw(env, monkeypatch):
    monkeypatch.setattr(bot, "_RUNTIME_IMPORT_OK", False)
    acc = FakeAccount()
    assert bot._wrap_with_outbox(acc, dry=False) is acc


def test_outbox_off_returns_inner(env):
    env.setenv("DZMM_OUTBOX", "off")
    acc = FakeAccount()
    assert bot._wrap_with_outbox(acc, dry=False) is acc


# ---------------------------------------------------------------- 默认不上线

def test_bot_routing_is_off_by_default(env):
    """关键：接线了，但**默认不让 Bot 发言** —— 切换时机由人决定。"""
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert isinstance(w, OutboxSender)
    assert w._inner is acc                       # 链路里没有 RoutingSender
    assert not isinstance(w._inner, RoutingSender)


# ---------------------------------------------------------------- 显式开启

def test_routing_on_builds_full_chain(env):
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok-abc")
    env.setenv("DZMM_BOT_CID", CID)
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert isinstance(w, OutboxSender)
    assert isinstance(w._inner, RoutingSender)
    assert w._inner.bot_cids == (CID,)
    assert w._inner._account is acc


def test_routing_on_without_token_falls_back(env):
    env.setenv("DZMM_BOT_ROUTING", "on")
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert w._inner is acc


def test_routing_on_without_cid_falls_back(env):
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok")
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert w._inner is acc


def test_routing_init_exception_falls_back(env, monkeypatch):
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok")
    env.setenv("DZMM_BOT_CID", CID)

    def boom(*a, **k):
        raise RuntimeError("构造失败")

    monkeypatch.setattr(bot, "DzmmBotSender", boom)
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert w._inner is acc


def test_routing_on_but_outbox_off(env):
    """两个开关互相独立：可以只要路由、不要队列。"""
    env.setenv("DZMM_OUTBOX", "off")
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok")
    env.setenv("DZMM_BOT_CID", CID)
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert isinstance(w, RoutingSender)


# ---------------------------------------------------------------- 开关解析

@pytest.mark.parametrize("val,expected", [
    ("on", True), ("ON", True), ("1", True), ("true", True), ("yes", True),
    ("off", False), ("0", False), ("no", False), ("false", False), ("", False),
])
def test_truthy_env(monkeypatch, val, expected):
    monkeypatch.setenv("DZMM_X_TEST", val)
    assert bot._truthy_env("DZMM_X_TEST") is expected


def test_truthy_env_default_when_unset(monkeypatch):
    monkeypatch.delenv("DZMM_X_TEST", raising=False)
    assert bot._truthy_env("DZMM_X_TEST") is False
    assert bot._truthy_env("DZMM_X_TEST", "on") is True


@pytest.mark.parametrize("val,expected", [
    ("on", True), ("", True), ("off", False), ("0", False), ("no", False), ("false", False),
])
def test_outbox_enabled_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("DZMM_OUTBOX", val)
    assert bot._outbox_enabled() is expected
