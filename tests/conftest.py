"""pytest 共享夹具。

`state` 夹具把 bot 的状态读写换成内存 dict，
避免测试污染 `state.json`（那里面是线上发情值/状态机数据）。

P12-AB（2026-09-27 体检）：`dbdir` 的清理原来是
`shutil.rmtree(..., ignore_errors=True)` —— 测试里很多地方直接 `OutboxStore(...)`
不关连接，Windows 上句柄还开着就删不掉，于是 `tests/_tmp` 一路攒到 **2,202 个目录 /
44.7 MB** 都没人发现（静默忽略错误 = 没人知道）。现在：先 `gc` + 关 state_store、
再带 chmod 重试几次，删不掉的**报出来**；另外每次 pytest 开跑前先扫一遍残留。
"""
import gc
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402

TMP_ROOT = ROOT / "tests" / "_tmp"
_UNREMOVED: list = []

# 代理类环境变量（大小写都算，urllib 两套都读）
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "WS_PROXY", "WSS_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "ws_proxy", "wss_proxy")


@pytest.fixture(autouse=True)
def _no_proxy_env():
    """测试进程内不许残留代理环境变量。

    `sio_subscribe` 被 import 时会调 `set_process_proxy_env()` 把
    HTTP(S)_PROXY/WS(S)_PROXY 写进**进程环境**（DZMM_PROXY 缺省
    127.0.0.1:7890）。那之后的用例只要用裸 `urllib.request.urlopen`，
    就全被拽去连代理 —— 本机 Clash 活着所以测不出来，CI 上没有代理，
    直接 Connection refused（2026-10-02 CI 首跑 25 连挂的根因）。
    所以每个用例前后各清一遍；本机本来就有的全局代理原样恢复。
    """
    saved = {k: os.environ.get(k) for k in _PROXY_KEYS}
    for k in _PROXY_KEYS:
        os.environ.pop(k, None)
    yield
    for k in _PROXY_KEYS:
        os.environ.pop(k, None)
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def _on_rmtree_error(func, path, _exc):
    """Windows 上只读/占用的文件：改权限再试一次（拿不到就算了）。"""
    try:
        os.chmod(path, 0o700)
        func(path)
    except Exception:                                          # noqa: BLE001
        pass


def _force_rmtree(d: Path) -> bool:
    """尽力把一个测试临时目录删干净，返回是否成功。

    顺序很关键：先 gc（CPython 下 sqlite 连接随引用计数释放）再删；
    很多测试直接 `OutboxStore(dbdir / "o.db")` 不关 —— 句柄一放就能删掉。
    """
    try:
        import state_store
        state_store.close()
    except Exception:                                          # noqa: BLE001
        pass
    for _ in range(3):
        gc.collect()
        try:
            shutil.rmtree(d, onerror=_on_rmtree_error)
        except Exception:                                      # noqa: BLE001
            pass
        if not d.exists():
            return True
        time.sleep(0.05)
    return not d.exists()


@pytest.fixture(scope="session", autouse=True)
def _sweep_tmp_dirs():
    """开跑前把上次残留的临时目录扫一遍（测试自己会漏下少量）。"""
    if TMP_ROOT.exists():
        for child in list(TMP_ROOT.iterdir()):
            if child.is_dir():
                _force_rmtree(child)
    yield
    if _UNREMOVED:
        print("\n[dbdir] 有 {} 个临时目录没删掉：{}".format(
            len(_UNREMOVED), _UNREMOVED[:3]))


@pytest.fixture
def dbdir():
    """临时目录，落在项目内。

    替代 pytest 的 `tmp_path`：本机沙箱下 tmpdir 插件会 PermissionError
    （basetemp 在系统 Temp，scandir 被拒）。
    """
    d = TMP_ROOT / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield d
    finally:
        if not _force_rmtree(d):
            _UNREMOVED.append(str(d))


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
