"""P12-AA：存储/队列的两处隐患回归（2026-09-27 体检发现）。

1. `store.import_json` 读失败却照样写"已导入"标记 ⇒ 失败**永久**变成"迁移完成"，
   旧数据静默留在 JSON 里，再也不重试（与 P12-U 分叉事故同族）。
2. `OutboxStore` 无锁 + 默认 `check_same_thread=True` ⇒ 工作线程一碰就抛，
   被 except 吞掉后表现成"偶发丢队列"（`store.Store` 早已修过同款）。
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.outbox import OutboxStore, PENDING, SENT          # noqa: E402
from runtime.store import IMPORT_MARKER, SCOPE_GROUP, Store     # noqa: E402


# ---------------------------------------------------------------- 1. 迁移标记

def test_import_json_does_not_mark_done_when_a_file_is_corrupt(dbdir):
    """state.json 坏掉时：**不能**落 IMPORT_MARKER，而且下次调用要重试。"""
    db = dbdir / "store.db"
    state = dbdir / "state.json"
    profiles = dbdir / "profiles.json"
    state.write_text("{ this is not json", encoding="utf-8")
    profiles.write_text('{"u1": {"name": "甲"}}', encoding="utf-8")

    st = Store(db)
    stats = st.import_json(state, profiles)
    assert stats["failed"], "要如实报告失败"
    assert st.meta_get(IMPORT_MARKER) is None, "读失败就不能标记已导入"
    # 档案那份是好的 ⇒ 已经导进去了（部分成功要保留，但不能因此标记完成）
    assert st.get("profile", "u1") == {"name": "甲"}

    # 修好之后重跑：这次成功并落标记
    state.write_text('{"c1": {"last_id": "m1"}}', encoding="utf-8")
    st2 = Store(db)
    st2.import_json(state, profiles)
    assert st2.meta_get(IMPORT_MARKER) is not None, "修好后要能正常完成迁移"
    assert st2.get(SCOPE_GROUP, "c1") == {"last_id": "m1"}
    # 再跑一次是幂等的
    assert Store(db).import_json(state, profiles) == {"skipped": True}


def test_import_json_tolerates_missing_files(dbdir):
    """文件不存在 = 没什么可导入，**算成功**（否则每次启动都重试、每次告警）。"""
    st = Store(dbdir / "s2.db")
    stats = st.import_json(dbdir / "nope-state.json", dbdir / "nope-profiles.json")
    assert stats["failed"] == []
    assert st.meta_get(IMPORT_MARKER) is not None


# ---------------------------------------------------------------- 2. 出站队列

def test_outbox_store_is_usable_from_a_worker_thread(dbdir):
    """工作线程读写出站队列必须成立（否则就是 P12-U 的重演）。"""
    st = OutboxStore(dbdir / "o.db")
    with ThreadPoolExecutor(2) as ex:
        oid = ex.submit(st.enqueue, chatroom_id="cid-1", text="你好").result()
        assert ex.submit(st.get, oid).result()["text"] == "你好"
        assert ex.submit(st.pending_count).result() == 1
        # 主线程也能看到工作线程写的
        assert st.get(oid)["status"] == PENDING
        assert ex.submit(st.lease, oid, "w1").result() is True
        st.mark_sent(oid)
        assert ex.submit(st.get, oid).result()["status"] == SENT
        # 并发写不炸、不丢
        futs = [ex.submit(st.enqueue, chatroom_id="cid-2", text="x{}".format(i))
                for i in range(20)]
        for f in futs:
            f.result()
        assert st.pending_count() >= 20
    st.close()


def test_outbox_store_has_a_lock_and_cross_thread_connection():
    """源码级：连接要放开线程限制，且每个碰连接的方法都在锁里（防回退）。"""
    src = (ROOT / "runtime" / "outbox.py").read_text(encoding="utf-8")
    assert "check_same_thread=False" in src
    assert "self._lock = threading.RLock()" in src
    # 11 个数据方法 + close 都要加锁
    assert src.count("with self._lock:") >= 12, src.count("with self._lock:")
