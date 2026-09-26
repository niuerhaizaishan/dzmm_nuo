"""发送链装配测试：`OutboxSender( RoutingSender( 账号 ) )`。

重点验证两件事：
1. **默认不上线**：`DZMM_BOT_ROUTING` 未设时链路里不应出现 RoutingSender。
2. **按配置装配**（P12-H）：Bot 列表的唯一真源是 `config.json` 的 `bots[]`，
   不是环境变量 —— 之前这里只读 `DZMM_BOT_TOKEN`/`DZMM_BOT_CID`，
   于是"配置里加了 Bot"根本不会有人送（页面上能加、运行期没人读）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot                                              # noqa: E402
from runtime.config_store import BotSpec               # noqa: E402
from runtime.outbox import OutboxSender                # noqa: E402
from runtime.routing import RoutingSender              # noqa: E402

CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"


class FakeAccount:
    def __init__(self):
        self.calls = []

    async def send(self, text, cid, allow_fallback=True):
        self.calls.append((text, cid))
        return True

    def start(self):
        return "started"


class FakeCfg:
    """只提供 `_bot_senders()` 需要的那一个字段。"""

    def __init__(self, bots):
        self.bots = tuple(bots)


def _bot(key="nuo_main", cid=CID, token_env="DZMM_BOT_TOKEN", uid="", enabled=True,
         name="小小糯-bot", short="小小糯"):
    return BotSpec(key=key, name=name, short=short, cid=cid,
                   token=None or "", token_env=token_env, enabled=enabled, uid=uid)


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
    # 默认给一个"配置里有一个 Bot"的世界；需要别的形状的用例自己改
    monkeypatch.setattr(bot, "_CFG", FakeCfg([_bot()]))
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
    """配置里有 Bot ⇒ 路由覆盖它绑定的那个群。"""
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok-abc")
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert isinstance(w, OutboxSender)
    assert isinstance(w._inner, RoutingSender)
    assert w._inner.bot_cids == (CID,)
    assert w._inner._account is acc


def test_routing_covers_every_configured_bot(env):
    """**两个 Bot 就覆盖两个群** —— 这正是 P12-H 补的那块。"""
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok-nuo")
    env.setenv("DZMM_BOT_TOKEN_ROSE", "tok-rose")
    env.setattr(bot, "_CFG", FakeCfg([
        _bot("nuo_main", CID, "DZMM_BOT_TOKEN", uid="eee88c3d-0000-0000-0000-000000000001"),
        _bot("weisi_rose", ROSE, "DZMM_BOT_TOKEN_ROSE", name="薇丝-bot", short="薇丝"),
    ]))
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert isinstance(w._inner, RoutingSender)
    assert set(w._inner.bot_cids) == {CID, ROSE}
    assert w._inner.bot_for(ROSE).token == "tok-rose", "每个群用自己那把 token"


def test_disabled_bot_is_skipped(env):
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok")
    env.setattr(bot, "_CFG", FakeCfg([_bot(enabled=False)]))
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert w._inner is acc, "未启用的 Bot 不该让链路变成 RoutingSender"


def test_routing_on_without_token_falls_back(env):
    """配置里有 Bot，但它的 token_env 在环境里没有 ⇒ 这个群退回账号。"""
    env.setenv("DZMM_BOT_ROUTING", "on")
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert w._inner is acc


def test_routing_falls_back_to_env_single_bot(env):
    """配置里一个可用 Bot 都没有时，退回"按 env 造一个"的老行为。"""
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok-env")
    env.setenv("DZMM_BOT_CID", ROSE)
    env.setattr(bot, "_CFG", FakeCfg([]))
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert isinstance(w._inner, RoutingSender)
    assert w._inner.bot_cids == (ROSE,)


def test_routing_on_with_nothing_anywhere_falls_back(env):
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setattr(bot, "_CFG", FakeCfg([]))
    acc = FakeAccount()
    w = bot._wrap_with_outbox(acc, dry=False)
    assert w._inner is acc


def test_routing_init_exception_falls_back(env, monkeypatch):
    env.setenv("DZMM_BOT_ROUTING", "on")
    env.setenv("DZMM_BOT_TOKEN", "tok")

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
