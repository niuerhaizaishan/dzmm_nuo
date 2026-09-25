"""pytest 共享夹具。

`state` 夹具把 bot 的状态读写换成内存 dict，
避免测试污染 `state.json`（那里面是线上发情值/状态机数据）。
"""
import shutil
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


@pytest.fixture
def dbdir():
    """临时目录，落在项目内。

    替代 pytest 的 `tmp_path`：本机沙箱下 tmpdir 插件会 PermissionError
    （basetemp 在系统 Temp，scandir 被拒）。
    """
    d = Path(__file__).resolve().parent / "_tmp" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def isolated(monkeypatch, dbdir):
    """把 `state_store` 指到临时目录，并清掉单例与后端缓存。

    需要真实存储层参与（而非内存 dict）的测试用它；用完自动关闭。
    """
    import state_store

    state_store.close()
    monkeypatch.setattr(state_store, "STATE_FILE", dbdir / "state.json")
    monkeypatch.setattr(state_store, "PROFILES_FILE", dbdir / "profiles.json")
    monkeypatch.setenv("DZMM_STORE_DB", str(dbdir / "store.db"))
    monkeypatch.delenv("DZMM_STORE", raising=False)
    yield monkeypatch
    state_store.close()


@pytest.fixture
def state(monkeypatch):
    """隔离的状态存储：内存 dict 后端。

    P8 起 bot 的读写走 `state_store.group_get/group_set`（不再经 `bot.load_state`），
    所以这里**同时**替换 state_store 的读写入口，否则测试会写到真实库。
    """
    import state_store

    store: dict = {}
    monkeypatch.setattr(bot, "load_state", lambda: store)
    monkeypatch.setattr(bot, "save_state", lambda s: None)
    monkeypatch.setattr(state_store, "load_state", lambda: store)
    monkeypatch.setattr(state_store, "save_state", lambda s: None)
    monkeypatch.setattr(
        state_store, "group_get",
        lambda cid, key, default=None: (store.get(cid) or {}).get(key, default))

    def _group_set(cid, key, value):
        g = dict(store.get(cid) or {})
        g[key] = value
        store[cid] = g

    monkeypatch.setattr(state_store, "group_set", _group_set)
    monkeypatch.setattr(state_store, "top_get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(state_store, "top_set", lambda key, value: store.__setitem__(key, value))
    return store


@pytest.fixture
def cid():
    return "test-cid-0001"
