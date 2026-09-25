"""P7 存储层单测：SQLite 内核 + 统一入口的后端切换。

最关键的两条：
  - **两个写者各写各的 key，互不覆盖**（JSON 后端做不到）
  - JSON 后端的丢更新行为被**记录成对照测试**（不是要修它，是要证明差异）
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import state_store                                     # noqa: E402
from runtime.store import (                            # noqa: E402
    SCOPE_GROUP, SCOPE_META, SCOPE_PROFILE, Store,
)

CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"
UID_1 = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"


@pytest.fixture
def store(dbdir):
    s = Store(dbdir / "store.db")
    yield s
    s.close()


# ---------------------------------------------------------------- 内核

def test_set_and_get(store):
    store.set(SCOPE_GROUP, CID_A, {"last_id": "m1", "heat": {"u": 5}})
    assert store.get(SCOPE_GROUP, CID_A) == {"last_id": "m1", "heat": {"u": 5}}


def test_get_missing_returns_none(store):
    assert store.get(SCOPE_GROUP, CID_A) is None


def test_save_scope_upserts(store):
    store.save_scope(SCOPE_GROUP, {CID_A: {"v": 1}})
    store.save_scope(SCOPE_GROUP, {CID_A: {"v": 2}})
    assert store.load_scope(SCOPE_GROUP) == {CID_A: {"v": 2}}


def test_save_scope_does_not_delete_absent_keys(store):
    """整 scope 覆盖式 upsert，但**不删**本次未出现的 key —— 多写者的关键。"""
    store.save_scope(SCOPE_GROUP, {CID_A: {"v": 1}})
    store.save_scope(SCOPE_GROUP, {CID_B: {"v": 2}})
    got = store.load_scope(SCOPE_GROUP)
    assert got[CID_A] == {"v": 1}
    assert got[CID_B] == {"v": 2}


def test_scopes_are_isolated(store):
    store.set(SCOPE_GROUP, CID_A, {"g": 1})
    store.set(SCOPE_PROFILE, CID_A, {"p": 1})
    store.set(SCOPE_META, CID_A, {"m": 1})
    assert store.get(SCOPE_GROUP, CID_A) == {"g": 1}
    assert store.get(SCOPE_PROFILE, CID_A) == {"p": 1}
    assert store.get(SCOPE_META, CID_A) == {"m": 1}


def test_delete(store):
    store.set(SCOPE_GROUP, CID_A, {"v": 1})
    store.delete(SCOPE_GROUP, CID_A)
    assert store.get(SCOPE_GROUP, CID_A) is None


def test_meta_roundtrip(store):
    assert store.meta_get("nope") is None
    assert store.meta_get("nope", 7) == 7
    store.meta_set("k", {"a": 1})
    assert store.meta_get("k") == {"a": 1}


def test_counts(store):
    store.set(SCOPE_GROUP, CID_A, {})
    store.set(SCOPE_GROUP, CID_B, {})
    store.set(SCOPE_PROFILE, UID_1, {})
    assert store.counts() == {"group": 2, "profile": 1}


def test_unicode_roundtrip(store):
    """中文/emoji 必须原样回来（曾因 ensure_ascii 与编码踩过坑）。"""
    val = {"name": "糯糯～（裳姐姐的骚贱小母狗❤）", "text": "🍡 测试"}
    store.set(SCOPE_GROUP, CID_A, val)
    assert store.get(SCOPE_GROUP, CID_A) == val


# ---------------------------------------------------------------- 多写者

def test_two_writers_do_not_lose_updates(dbdir):
    """模拟两个进程：各自只改自己的 key，**都保留**。

    这正是旧 JSON 后端做不到的（见下一个对照测试）。
    """
    path = dbdir / "store.db"
    a, b = Store(path), Store(path)
    try:
        a.save_scope(SCOPE_GROUP, {CID_A: {"v": 1}})
        b.save_scope(SCOPE_GROUP, {CID_B: {"v": 2}})
    finally:
        a.close()
        b.close()
    merged = Store(path).load_scope(SCOPE_GROUP)
    assert merged[CID_A] == {"v": 1}
    assert merged[CID_B] == {"v": 2}


def test_json_backend_loses_updates_contrast(dbdir):
    """对照：旧 JSON 后端的"读整个文件→写回整个文件"会丢更新。

    注意失效形态：**不是 key 消失，而是更新被陈旧的快照"回退"** ——
    B 在 A 之前读的文件，它写回时把 CID_A 的**旧值**一起写回去了。

    这条**不是要修 JSON 后端**，而是把差异钉成可执行的证据。
    """
    f = dbdir / "state.json"
    f.write_text(json.dumps({CID_A: {"v": 1}}), encoding="utf-8")

    a = json.loads(f.read_text(encoding="utf-8"))       # 进程 A 读
    b = json.loads(f.read_text(encoding="utf-8"))       # 进程 B 读（同一时刻）
    a[CID_A] = {"v": 11}
    f.write_text(json.dumps(a), encoding="utf-8")       # A 写：CID_A -> 11
    b[CID_B] = {"v": 2}
    f.write_text(json.dumps(b), encoding="utf-8")       # B 写：把 CID_A 带回旧值

    final = json.loads(f.read_text(encoding="utf-8"))
    assert final[CID_B] == {"v": 2}                     # B 的改动在
    assert final[CID_A] == {"v": 1}                     # ← A 的改动被回退掉了


# ---------------------------------------------------------------- 一次性导入

def test_import_json_once_and_idempotent(dbdir):
    state_json = dbdir / "state.json"
    profiles_json = dbdir / "profiles.json"
    state_json.write_text(json.dumps({
        CID_A: {"last_id": "m1"},
        "dice_game": {"active": True},                 # 非 UUID 顶层键
    }), encoding="utf-8")
    profiles_json.write_text(json.dumps({UID_1: {"name": "糯糯"}}), encoding="utf-8")

    st = Store(dbdir / "store.db")
    try:
        first = st.import_json(state_json, profiles_json)
        assert first["group"] == 2                     # 顶层键**全部**导入，不过滤
        assert first["profiles"] == 1

        group = st.load_scope(SCOPE_GROUP)
        assert group[CID_A] == {"last_id": "m1"}
        assert group["dice_game"] == {"active": True}  # ← 非 UUID 顶层键必须在
        assert st.get(SCOPE_PROFILE, UID_1) == {"name": "糯糯"}

        # 再调一次不应重复导入（也不应报错）
        second = st.import_json(state_json, profiles_json)
        assert second.get("skipped") is True
    finally:
        st.close()


def test_import_survives_missing_files(dbdir):
    st = Store(dbdir / "store.db")
    try:
        stats = st.import_json(dbdir / "nope1.json", dbdir / "nope2.json")
        assert stats["group"] == 0 and stats["profiles"] == 0
    finally:
        st.close()


def test_import_survives_corrupt_json(dbdir):
    bad = dbdir / "state.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    st = Store(dbdir / "store.db")
    try:
        stats = st.import_json(bad, dbdir / "nope.json")
        assert stats["group"] == 0
    finally:
        st.close()


# ---------------------------------------------------------------- 统一入口

@pytest.fixture
def isolated(monkeypatch, dbdir):
    """把 state_store 指到临时目录，并清掉单例与后端缓存。"""
    state_store.close()
    monkeypatch.setattr(state_store, "STATE_FILE", dbdir / "state.json")
    monkeypatch.setattr(state_store, "PROFILES_FILE", dbdir / "profiles.json")
    monkeypatch.setenv("DZMM_STORE_DB", str(dbdir / "store.db"))
    monkeypatch.delenv("DZMM_STORE", raising=False)
    yield monkeypatch
    state_store.close()


def test_sqlite_backend_roundtrip(isolated):
    assert state_store.using_sqlite() is True
    state_store.save_state({CID_A: {"x": 1}})
    assert state_store.load_state() == {CID_A: {"x": 1}}


def test_non_uuid_top_level_keys_are_preserved(isolated):
    """⚠️ **回归守卫**：`dice_game` 这类非 cid 顶层键必须原样保留。

    早期版本在这里按"键是不是 UUID"过滤 `load_state()`，
    会让**骰子局状态永远读成空**（`_dice_state()` 读的正是 `state["dice_game"]`）。
    这个 bug 是在导入计数报出"1 个非 UUID 顶层键"时才被发现的。
    """
    state_store.save_state({
        CID_A: {"x": 1},
        "dice_game": {"active": True, "phase": "calling", "order": ["u1"]},
    })
    got = state_store.load_state()
    assert got[CID_A] == {"x": 1}
    assert got["dice_game"] == {"active": True, "phase": "calling", "order": ["u1"]}


def test_dice_state_roundtrip_through_store(isolated):
    """端到端：bot 的骰子状态读写经过存储层后仍正确。"""
    import bot
    bot._dice_save({"active": True, "phase": "calling", "order": ["u1", "u2"]})
    st = bot._dice_state()
    assert st["phase"] == "calling"
    assert st["order"] == ["u1", "u2"]


def test_profiles_roundtrip_sqlite(isolated):
    assert state_store.upsert_profile(UID_1, {"name": "糯糯"}) is True
    assert state_store.get_profile(UID_1) == {"name": "糯糯"}
    assert state_store.load_profiles() == {UID_1: {"name": "糯糯"}}


def test_json_backend_still_works(isolated):
    isolated.setenv("DZMM_STORE", "json")
    state_store.close()
    assert state_store.using_sqlite() is False
    state_store.save_state({CID_A: {"x": 2}})
    # JSON 后端下走文件
    raw = json.loads((isolated_StateFile := state_store.STATE_FILE).read_text(encoding="utf-8"))
    assert raw[CID_A] == {"x": 2}
    assert state_store.load_state() == {CID_A: {"x": 2}}


def test_json_backend_profiles(isolated):
    isolated.setenv("DZMM_STORE", "json")
    state_store.close()
    assert state_store.upsert_profile(UID_1, {"name": "x"}) is True
    assert state_store.get_profile(UID_1) == {"name": "x"}


def test_stats_reports_backend(isolated):
    s = state_store.stats()
    assert s["backend"] == "sqlite"
    assert "db" in s
