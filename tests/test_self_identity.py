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


# ---------------------------------------------------------------- @ 剥离的顺序

def test_self_names_longest_first_is_sorted_by_length():
    """剥 @ 必须长名优先 —— 名字集合是 set，迭代顺序不确定。"""
    names = identity.self_names_longest_first()
    assert names == sorted(names, key=len, reverse=True)
    assert set(names) == identity.self_names_for()


def test_self_names_for_includes_the_persona_name():
    """P12-H 追加：**按群**算名字 —— 玫瑰房的客人叫的是「薇丝」。

    侧车账号叫「小小糯～」、Bot 叫「薇丝-bot」，只认 self_names() 的话
    「薇丝」既不算 @ 我、也不算提我（房间不接话、调戏不计数）。
    """
    import personas as P
    room = identity.self_names_for(P.ROSE_CID)
    assert "薇丝" in room
    assert set(room) >= identity.self_names()
    ordered = identity.self_names_longest_first(P.ROSE_CID)
    assert set(ordered) == room and ordered == sorted(ordered, key=len, reverse=True)
    # 没传 cid / 默认人设的群：就是老行为 + 默认名，不会凭空多出房间名
    assert "薇丝" not in identity.self_names_longest_first()


def test_strip_at_does_not_leave_a_prefix_residue(monkeypatch):
    """`@小小糯-bot` 必须整段剥掉，不能留下 `-bot`。

    名字互为前缀时（侧车「小小糯」 vs Bot「小小糯-bot」），
    若短名先被剥，残余会**原样进 LLM prompt**。
    这个 bug 今天就已经存在（残余是一个「～」），改名成 `小小糯-bot` 后会变得显眼。
    """
    monkeypatch.setattr(identity, "BOT_NAME", "小小糯-bot")
    monkeypatch.setattr(identity, "BOT_NAME_SHORT", "小小糯")
    # `self_names()` 只在 BOT_UID 非空时才把 Bot 名变体加进剥 @ 名单 ——
    # CI 上没有 DZMM_BOT_ID，长名缺席 → `@小小糯-bot` 被短名剥成 `-bot`。
    monkeypatch.setattr(identity, "BOT_UID", "uid-self")
    monkeypatch.setattr(identity, "get_full_name", lambda uid: "小小糯～")
    monkeypatch.setattr(identity, "get_name", lambda uid: "小小糯")
    monkeypatch.setattr(bot, "self_names_longest_first",
                        identity.self_names_longest_first)

    assert bot._strip_at("@小小糯-bot 你好呀") == "你好呀"
    assert bot._strip_at("@小小糯～ 你好呀") == "你好呀"
    assert bot._strip_at("@小小糯 你好呀") == "你好呀"
    # 反复跑很多次，掩盖不了"顺序不定"（排序后必须每次都对）
    for _ in range(50):
        assert bot._strip_at("@小小糯-bot 你好呀") == "你好呀"


def test_strip_at_keeps_non_name_text(monkeypatch):
    monkeypatch.setattr(identity, "BOT_NAME", "小小糯-bot")
    monkeypatch.setattr(identity, "BOT_NAME_SHORT", "小小糯")
    monkeypatch.setattr(identity, "get_full_name", lambda uid: "小小糯～")
    monkeypatch.setattr(identity, "get_name", lambda uid: "小小糯")
    monkeypatch.setattr(bot, "self_names_longest_first",
                        identity.self_names_longest_first)
    # 不是 @ 开头的名字不该被动
    assert bot._strip_at("小小糯-bot 你好") == "小小糯-bot 你好"


def test_perf_query_detection_strips_the_full_bot_name(monkeypatch):
    """公演的「谁表现怎么样」也要长名优先，否则目标名会带上 `-bot` 残余。"""
    import runtime.perf as perf
    monkeypatch.setattr(perf, "self_names_longest_first",
                        lambda: ["小小糯-bot", "小小糯～", "小小糯"])
    got = perf._detect_perf_query("@小小糯-bot 依依今天表现怎么样")
    assert got is not None
    target, _today = got
    assert "-bot" not in target, target
    assert "依依" in target, target
