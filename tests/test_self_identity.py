"""自身身份拆分（P4.2）。

「我自己」有两个身份：侧车账号 `NUO_UID`（私聊/图片/分享/其他群）
与官方 Bot `BOT_UID`（群文本）。回声过滤与「排除自己」必须同时认这两个。

本文件还包含两条**迁移守卫**测试：
  - 业务逻辑里不得再有裸 `NUO_UID` 比较
  - `NUONUO_UID`（真人 uid，名字里含 `NUO_UID` 子串）不得被改坏
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402

BOT_ID = "eee88c3d-af91-4ecc-96df-d87eb9a358db"
BOT_PY = Path(__file__).resolve().parent.parent / "bot.py"


# ---------------------------------------------------------------- is_self

def test_account_uid_is_self():
    assert bot.is_self(bot.NUO_UID) is True


def test_ordinary_user_is_not_self():
    assert bot.is_self("some-real-user") is False


def test_falsy_is_not_self():
    assert bot.is_self("") is False
    assert bot.is_self(None) is False


def test_bot_uid_is_self_when_configured(monkeypatch):
    monkeypatch.setattr(bot, "BOT_UID", BOT_ID)
    assert bot.is_self(BOT_ID) is True


def test_bot_uid_is_not_self_when_unset(monkeypatch):
    """未配置 Bot 时（如未部署 Bot 通道），行为退回原来只认账号。"""
    monkeypatch.setattr(bot, "BOT_UID", "")
    assert bot.is_self(BOT_ID) is False


# ---------------------------------------------------------------- self_uids

def test_self_uids_contains_account():
    assert bot.NUO_UID in bot.self_uids()


def test_self_uids_adds_bot(monkeypatch):
    monkeypatch.setattr(bot, "BOT_UID", BOT_ID)
    assert bot.self_uids() == {bot.NUO_UID, BOT_ID}


def test_self_uids_without_bot(monkeypatch):
    monkeypatch.setattr(bot, "BOT_UID", "")
    assert bot.self_uids() == {bot.NUO_UID}


# ---------------------------------------------------------------- self_names

def test_self_names_includes_configured_bot_name(monkeypatch):
    monkeypatch.setattr(bot, "BOT_UID", BOT_ID)
    monkeypatch.setattr(bot, "BOT_NAME", "小小糯")
    monkeypatch.setattr(bot, "BOT_NAME_SHORT", "小小糯")
    assert "小小糯" in bot.self_names()


def test_self_names_never_contains_empty(monkeypatch):
    """成员表查不到名字时不能把空串塞进集合（否则 `"@" in text` 恒真）。"""
    monkeypatch.setattr(bot, "BOT_UID", "")
    assert "" not in bot.self_names()


def test_is_at_me_matches_bot_name(monkeypatch):
    monkeypatch.setattr(bot, "BOT_UID", BOT_ID)
    monkeypatch.setattr(bot, "BOT_NAME", "小小糯")
    monkeypatch.setattr(bot, "BOT_NAME_SHORT", "小小糯")
    assert bot.is_at_me("@小小糯 你好") is True
    assert bot.is_at_me("随便聊聊") is False


# ---------------------------------------------------------------- 迁移守卫

def test_no_bare_nuouid_comparison_left_in_business_logic():
    """除 `is_self()` 自身外，业务逻辑里不得再有裸 `NUO_UID` 比较。"""
    src = BOT_PY.read_text(encoding="utf-8").splitlines()
    pat = re.compile(r"(==|!=)\s*NUO_UID|NUO_UID\s*(==|!=)|discard\(NUO_UID\)")
    hits = [(i, l.strip()) for i, l in enumerate(src, 1) if pat.search(l)]
    outside = [h for h in hits if "return uid == NUO_UID" not in h[1]]
    assert outside == [], "仍有未拆分的 NUO_UID 比较：{}".format(outside)


def test_nuonuo_uid_not_corrupted():
    """`NUONUO_UID` 名字里含 `NUO_UID` 子串 —— 迁移不得改坏它（真人不该动）。"""
    assert bot.NUONUO_UID
    assert "SIDECAR" not in bot.NUONUO_UID
    src = BOT_PY.read_text(encoding="utf-8")
    assert src.count("NUONUO_UID") == 3


def test_import_from_personas_not_broken():
    """`NUONUO_UID` 是从 personas 导入的，值应仍是那个真人 uid。"""
    assert bot.NUONUO_UID == "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
