"""多 Bot 身份（P12-H）：一个群里一个 Bot，每个都是"我自己"。

为什么这是一个必须钉住的安全性质：`is_self()` 是全项目**唯一**的"这条消息是不是我发的"
判定点（P8 把那 ~25 处裸比较收敛到了这里）。少认一个 Bot，它在自己群里发的消息
就会被当成群友 —— 轻则统计污染，重则**自问自答**（兜底聊天会把 Bot 的问候当用户输入）。

覆盖四层：
1. 注册：配置里的每个启用 Bot 都进"我自己"（uid + 名字）；
2. 认领：配置里没写 uid 时，按**成员表显示名**认出它（启动查一次 + 循环里缓存兜底）；
3. 名字判定：`@薇丝-bot` 也算 @ 我（否则被 @ 不理人）；
4. 不误伤：真人、侧车账号、空值都不能被当成 Bot；`bootstrap` 不能把账号自己吞掉。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import config_store as cs  # noqa: E402
from runtime import identity as ident  # noqa: E402

CID_NUO = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
CID_OTHER = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"
UID_NUO_BOT = "eee88c3d-1111-2222-3333-444455556666"
UID_WEISI = "7f3a1c02-1111-2222-3333-444455556666"

BOTS = [
    {"key": "nuo_main", "name": "小小糯-bot", "short": "小小糯",
     "cid": CID_NUO, "uid": UID_NUO_BOT},
    {"key": "weisi_rose", "name": "薇丝-bot", "short": "薇丝",
     "cid": CID_ROSE, "uid": UID_WEISI},
]


@pytest.fixture(autouse=True)
def _clean():
    """每个用例都从"没有任何配置 Bot"开始，结束后也还原。"""
    ident.register_bots([])
    saved_cache = dict(ident._name_cache)
    yield
    ident.register_bots([])
    ident._name_cache.clear()
    ident._name_cache.update(saved_cache)


# ---------------------------------------------------------------- 1. 注册

def test_register_bots_adds_every_bot_as_self():
    got = ident.register_bots(BOTS)
    assert got == {"nuo_main": UID_NUO_BOT, "weisi_rose": UID_WEISI}
    for uid in (UID_NUO_BOT, UID_WEISI):
        assert ident.is_self(uid) is True
    assert ident.self_uids() >= {ident.NUO_UID, UID_NUO_BOT, UID_WEISI}


def test_register_bots_accepts_botspec_dataclass():
    """真实调用点传的是 `config_store.BotSpec`，不是 dict。"""
    spec = cs.BotSpec(key="weisi_rose", name="薇丝-bot", short="薇丝",
                      cid=CID_ROSE, token_env="DZMM_BOT_TOKEN_ROSE",
                      uid=UID_WEISI)
    got = ident.register_bots([spec])
    assert got == {"weisi_rose": UID_WEISI}
    assert ident.is_self(UID_WEISI) is True


def test_register_bots_without_uid_still_contributes_names():
    ident.register_bots([{"key": "k", "name": "薇丝-bot", "short": "薇丝",
                          "cid": CID_ROSE, "uid": ""}])
    assert ident.bot_uids() == set()
    assert {"薇丝-bot", "薇丝"} <= ident.self_names(), "名字要用于 @我 判定"


def test_register_bots_ignores_junk():
    ident.register_bots([None, {}, {"name": "没有 key"}, "字符串"])
    assert ident.bots() == []
    assert ident.is_self("some-user") is False
    assert ident.is_self("") is False
    assert ident.is_self(None) is False


def test_register_bots_replaces_previous_set():
    """重注册要**整体替换**（配置删掉一个 Bot 后不能还认它是自己）。"""
    ident.register_bots(BOTS)
    ident.register_bots([BOTS[0]])
    assert ident.is_self(UID_WEISI) is False
    assert ident.is_self(UID_NUO_BOT) is True


def test_account_is_still_self_and_env_bot_too(monkeypatch):
    monkeypatch.setattr(ident, "BOT_UID", UID_NUO_BOT)
    assert ident.is_self(ident.NUO_UID) is True
    assert ident.is_self(UID_NUO_BOT) is True


# ---------------------------------------------------------------- 2. 按名字认领

@pytest.mark.parametrize("member_name,should_match", [
    ("薇丝-bot", True),
    ("薇丝", True),
    ("薇丝bot", True),
    ("薇丝～", True),
    ("薇丝（505房）", False),        # 带别的内容不算（防误伤真人）
    ("薇丝的小号", False),
    ("小小糯～", True),              # 侧车账号的名字（本来就是 self）
    ("砚先生（夏薇的老婆）", False),  # 真人名字里带"薇" —— 绝不能被吞掉
    ("", False),
])
def test_name_matching_is_tight(member_name, should_match):
    ident.register_bots(BOTS)      # 两个 Bot 都登记：这样"小小糯～"那条才有对照
    got = ident._name_matches_bot(member_name)
    assert bool(got) is should_match, member_name


def test_learn_bot_uid_from_cache():
    ident.register_bots([{"key": "weisi_rose", "name": "薇丝-bot", "short": "薇丝",
                          "cid": CID_ROSE, "uid": ""}])
    ident._name_cache[UID_WEISI] = "薇丝-bot"
    assert ident.learn_bot_uid(UID_WEISI) is True
    assert ident.is_self(UID_WEISI) is True
    assert ident.bots()[0]["uid"] == UID_WEISI, "认出来之后要写回登记表"
    # 第二次不该再"新认出来"
    assert ident.learn_bot_uid(UID_WEISI) is False
    # 真人不会被认成 Bot
    ident._name_cache["some-guy"] = "随便一个人"
    assert ident.learn_bot_uid("some-guy") is False
    assert ident.is_self("some-guy") is False


def test_learn_bot_uid_accepts_explicit_name():
    ident.register_bots([{"key": "k", "name": "薇丝-bot", "short": "薇丝",
                          "cid": CID_ROSE, "uid": ""}])
    assert ident.learn_bot_uid(UID_WEISI, "薇丝") is True
    assert ident.is_self(UID_WEISI) is True


def test_learn_bot_uid_noop_for_self_and_empty():
    ident.register_bots(BOTS)
    assert ident.learn_bot_uid("") is False
    assert ident.learn_bot_uid(None) is False
    assert ident.learn_bot_uid(ident.NUO_UID) is False
    assert ident.learn_bot_uid(UID_NUO_BOT) is False


def test_resolve_bot_uids_from_members():
    ident.register_bots([{"key": "weisi_rose", "name": "薇丝-bot", "short": "薇丝",
                          "cid": CID_ROSE, "uid": ""}])

    def fake_members(cid):
        assert cid == CID_ROSE
        return {"someone": "一个群友", UID_WEISI: "薇丝-bot",
                "other": "Vv（渴了去喝水呀）"}

    got = ident.resolve_bot_uids(fetch_members=fake_members)
    assert got == {"weisi_rose": UID_WEISI}
    assert ident.is_self(UID_WEISI) is True


def test_resolve_bot_uids_survives_failure():
    """Bot 还没被拉进群 / 网络失败：不能抛，也不能乱认。"""
    ident.register_bots([{"key": "weisi_rose", "name": "薇丝-bot", "short": "薇丝",
                          "cid": CID_ROSE, "uid": ""}])

    def boom(cid):
        raise RuntimeError("network down")

    assert ident.resolve_bot_uids(fetch_members=boom) == {"weisi_rose": ""}
    assert ident.bot_uids() == set()


def test_resolve_bot_uids_skips_known():
    ident.register_bots(BOTS)
    called = []

    def fake(cid):
        called.append(cid)
        return {}

    ident.resolve_bot_uids(fetch_members=fake)
    assert called == [], "已经有 uid 的 Bot 不必再查成员表"


def test_resolve_bot_uids_never_claims_the_account():
    """侧车账号显示名「小小糯～」与 Bot 简称「小小糯」只差一个后缀 —— 不能认成 Bot。

    2026-09-26 真的发生过：`Bot nuo_main 的身份 = 已认到 503e3cb9`（那是账号）。
    危害不大（账号本来就是 self），但会让日志说谎、并污染 Bot 记录。
    """
    ident.register_bots([{"key": "nuo_main", "name": "小小糯-bot", "short": "小小糯",
                          "cid": CID_NUO, "uid": ""}])

    def fake(cid):
        return {ident.NUO_UID: "小小糯～",          # 账号
                "真-bot": "小小糯-bot",              # 这个才是
                "real-user": "路人"}

    got = ident.resolve_bot_uids(fetch_members=fake)
    assert got == {"nuo_main": "真-bot"}, got
    assert ident.is_self("真-bot") is True
    assert ident.bots()[0]["uid"] == "真-bot"


def test_resolve_prefers_exact_name_over_suffix():
    ident.register_bots([{"key": "weisi_rose", "name": "薇丝-bot", "short": "薇丝",
                          "cid": CID_ROSE, "uid": ""}])

    def fake(cid):
        # 字典顺序故意让"带后缀的"排在前面，精确匹配必须仍然胜出
        return {"suffix-guy": "薇丝bot", "exact-guy": "薇丝-bot"}

    got = ident.resolve_bot_uids(fetch_members=fake)
    assert got["weisi_rose"] == "exact-guy", got


# ---------------------------------------------------------------- 3. @我 判定

def test_at_bot_name_counts_as_at_me():
    ident.register_bots(BOTS)
    assert ident.is_at_me("@薇丝-bot 你好呀") is True
    assert ident.is_at_me("@小小糯 在吗") is True
    assert ident.is_at_me("@路人甲 在吗") is False


def test_self_names_includes_all_bots():
    ident.register_bots(BOTS)
    names = ident.self_names()
    assert {"薇丝-bot", "薇丝", "小小糯-bot", "小小糯"} <= names


def test_longest_first_stripping_handles_both_bots():
    """剥 @ 必须长名优先（`self_names` 是 set，顺序不定）。"""
    ident.register_bots(BOTS)
    names = ident.self_names_longest_first()
    assert names == sorted(names, key=len, reverse=True)
    assert names.index("小小糯-bot") < names.index("小小糯")
    assert names.index("薇丝-bot") < names.index("薇丝")


# ---------------------------------------------------------------- 4. 配置层

def _raw(**bot_over):
    b = {"key": "weisi_rose", "name": "薇丝-bot", "short": "薇丝",
         "cid": CID_ROSE, "token_env": "DZMM_BOT_TOKEN_ROSE"}
    b.update(bot_over)
    return {"version": cs.SCHEMA_VERSION,
            "groups": [{"cid": CID_ROSE, "alias": "玫瑰房", "enabled": True}],
            "bots": [b]}


BASE = {"groups": [CID_ROSE], "mute": (), "screen": (), "push": (),
        "aliases": {}, "bots": [], "personas": ()}


def test_schema_v4_roundtrips_bot_uid():
    cfg = cs._parse(_raw(uid=UID_WEISI), BASE)
    assert cfg.bots[0].uid == UID_WEISI
    out = cfg.as_dict()["bots"][0]
    assert out["uid"] == UID_WEISI
    # 空 uid 不写进文件（少点噪声），读回来是空串
    cfg2 = cs._parse(_raw(), BASE)
    assert cfg2.bots[0].uid == ""
    assert "uid" not in cfg2.as_dict()["bots"][0]


def test_bots_for_identity_only_enabled():
    """停用的 Bot 不进"我是谁" —— 它根本没在发消息。"""
    raw = _raw()
    raw["groups"].append({"cid": CID_NUO, "alias": "认证群", "enabled": True})
    raw["groups"].append({"cid": CID_OTHER, "alias": "停用群", "enabled": True})
    raw["bots"].append({"key": "nuo_main", "name": "小小糯-bot", "short": "小小糯",
                        "cid": CID_NUO, "token_env": "DZMM_BOT_TOKEN",
                        "uid": UID_NUO_BOT, "enabled": True})
    raw["bots"].append({"key": "dead", "name": "停用的-bot", "short": "停用",
                        "cid": CID_OTHER, "token_env": "X", "enabled": False})
    cfg = cs._parse(raw, BASE)
    keys = {g["key"] for g in cfg.bots_for_identity()}
    assert keys == {"weisi_rose", "nuo_main"}, keys
    assert all({"key", "uid", "cid", "name", "short"} <= set(g)
               for g in cfg.bots_for_identity())


def test_migrating_v3_file_keeps_working():
    """v3 文件（没有 bots[].uid）读进来照样能用，只是 uid 为空。"""
    raw = _raw()
    raw["version"] = 3
    cfg = cs._parse(raw, BASE)
    assert cfg.bots[0].uid == ""
    assert cfg.migrated_from == 3
