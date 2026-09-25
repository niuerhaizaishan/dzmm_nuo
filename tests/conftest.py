"""pytest 共享夹具。

`state` 夹具把 bot 的状态读写换成内存 dict，
避免测试污染 `state.json`（那里面是线上发情值/状态机数据）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


@pytest.fixture
def state(monkeypatch):
    """隔离的状态存储：load_state 返回同一个内存 dict。"""
    store: dict = {}
    monkeypatch.setattr(bot, "load_state", lambda: store)
    monkeypatch.setattr(bot, "save_state", lambda s: None)
    return store


@pytest.fixture
def cid():
    return "test-cid-0001"
