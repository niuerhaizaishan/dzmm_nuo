"""自身身份拆分（P4.2）。

「我自己」有两个身份：侧车账号 `NUO_UID`（私聊/图片/分享/其他群）
与官方 Bot `BOT_UID`（群文本）。回声过滤与「排除自己」必须同时认这两个。

P8 第 4 步之后，这组函数住在 `runtime/identity.py`（bot.py 只是 re-export）。
所以**打补丁要打真身**：`monkeypatch.setattr(bot, "BOT_UID", ...)` 当年有效，
是因为函数就读 bot 自己的全局；现在它们读的是 `runtime.identity` 的全局，
补 bot 上的那份副本不会影响任何行为 —— 这正是本次改动暴露出来的东西。

本文件还包含两条**迁移守卫**测试：
  - 业务逻辑里不得再有裸 `NUO_UID` 比较
  - `NUONUO_UID`（真人 uid，名字里含 `NUO_UID` 子串）不得被改坏
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
import runtime.identity as identity  # noqa: E402

BOT_ID = "eee88c3d-af91-4ecc-96df-d87eb9a358db"
ROOT = Path(__file__).resolve().parent.parent
BOT_PY = ROOT / "bot.py"
IDENTITY_PY = ROOT / "runtime" / "identity.py"


def _patch_bot_id(monkeypatch, value):
    """改身份的**真身**，并同步 bot 上的 re-export 副本（有些断言读 bot）。"""
    monkeypatch.setattr(identity, "BOT_UID", value)
    monkeypatch.setattr(bot, "BOT_UID", value)


# ---------------------------------------------------------------- is_self

def test_account_uid_is_self():
    assert bot.is_self(bot.NUO_UID) is True


def test_ordinary_user_is_not_self():
    assert bot.is_self("some-real-user") is False


def test_falsy_is_not_self():
    assert bot.is_self("") is False
    assert bot.is_self(None) is False


def test_bot_uid_is_self_when_configured(monkeypatch):
    _patch_bot_id(monkeypatch, BOT_ID)
    assert bot.is_self(BOT_ID) is True


def test_bot_uid_is_not_self_when_unset(monkeypatch):
    """未配置 Bot 时（如未部署 Bot 通道），行为退回原来只认账号。"""
    _patch_bot_id(monkeypatch, "")
    assert bot.is_self(BOT_ID) is False


# ---------------------------------------------------------------- self_uids

def test_self_uids_contains_account():
    assert bot.NUO_UID in bot.self_uids()


def test_self_uids_adds_bot(monkeypatch):
    _patch_bot_id(monkeypatch, BOT_ID)
    assert bot.self_uids() == {bot.NUO_UID, BOT_ID}


def test_self_uids_without_bot(monkeypatch):
    _patch_bot_id(monkeypatch, "")
    assert bot.self_uids() == {bot.NUO_UID}


# ---------------------------------------------------------------- self_names

def test_self_names_includes_configured_bot_name(monkeypatch):
    _patch_bot_id(monkeypatch, BOT_ID)
    monkeypatch.setattr(identity, "BOT_NAME", "小小糯")
    monkeypatch.setattr(identity, "BOT_NAME_SHORT", "小小糯")
    assert "小小糯" in bot.self_names()


def test_self_names_never_contains_empty(monkeypatch):
    """成员表查不到名字时不能把空串塞进集合（否则 `"@" in text` 恒真）。"""
    _patch_bot_id(monkeypatch, "")
    assert "" not in bot.self_names()


def test_is_at_me_matches_bot_name(monkeypatch):
    _patch_bot_id(monkeypatch, BOT_ID)
    monkeypatch.setattr(identity, "BOT_NAME", "小小糯")
    monkeypatch.setattr(identity, "BOT_NAME_SHORT", "小小糯")
    assert bot.is_at_me("@小小糯 你好") is True
    assert bot.is_at_me("随便聊聊") is False


# ---------------------------------------------------------------- 迁移守卫

def test_no_bare_nuouid_comparison_left_in_business_logic():
    """除 `is_self()` 自身外，业务逻辑里不得再有裸 `NUO_UID` 比较。

    P8 第 4 步起 `is_self` 住在 runtime/identity.py，所以两处都要扫。
    """
    pat = re.compile(r"(==|!=)\s*NUO_UID|NUO_UID\s*(==|!=)|discard\(NUO_UID\)")
    outside = []
    for path in (BOT_PY, IDENTITY_PY):
        for i, l in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(l) and "return uid == NUO_UID" not in l:
                outside.append((path.name, i, l.strip()))
    assert outside == [], "仍有未拆分的 NUO_UID 比较：{}".format(outside)


def test_nuonuo_uid_not_corrupted():
    """`NUONUO_UID` 名字里含 `NUO_UID` 子串 —— 迁移不得改坏它（真人不该动）。

    原来只数 `bot.py` 里的出现次数，P8 第 4 步把用到它的函数搬进了
    `runtime/sign.py`，次数就跟着变了 —— 这种"跟着文件走"的断言每次搬家都要改，
    改久了就会变成随手改数字。

    所以改成直接盯**真正的失效模式**：批量把 `NUO_UID` 改名成 `SIDECAR_UID`
    的时候，`NUONUO_UID` 会被子串替换误伤。这才是要防的东西。
    """
    assert bot.NUONUO_UID
    assert "SIDECAR" not in bot.NUONUO_UID
    for path in [BOT_PY] + sorted((ROOT / "runtime").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for mangled in ("NUOSIDECAR", "NUO_SIDECAR", "SIDECARNUO"):
            assert mangled not in text, "{} 被批量替换误伤：{}".format(
                path.name, mangled)


def test_import_from_personas_not_broken():
    """`NUONUO_UID` 是从 personas 导入的，值应仍是那个真人 uid。"""
    assert bot.NUONUO_UID == "a56cbd67-3a1e-439a-8beb-d1557dbeff79"
