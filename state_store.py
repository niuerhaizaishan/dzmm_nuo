"""状态 / 档案存储统一入口（P7）。

调用方看到的函数名与签名**和原来完全一样**，所以 `bot.py` / `profiles.py` 的
调用点**一处都不用改**：

    load_state() / save_state(d)
    load_profiles() / save_profiles(d) / get_profile(uid) / upsert_profile(uid, p)

### 解决什么（事实已核实，见 `runtime/store.py` 顶部）

1. `state.json` 是**非原子**写（文件 bind mount 上 `os.replace` 会 EBUSY，只能原地写）
   → 被 kill 时可能截断 → `load_state()` 静默返回 `{}` → **全群状态无声清零**
2. `state.json` 约 50KB，约 45 个写入点**每次都重写整文件**
3. `profiles.json` 有两个潜在写者（bot 与手工脚本 `batch_register.py`），并发会丢更新

> 注：`check_bot_health.py` 用的是它**自己的** `check_health_state.json`，
> 与 `state.json` 无关 —— 这里不做处理（已改为把它加进 compose 卷，否则每次
> 重建容器去重状态就丢、会重复告警）。

### 后端

- 默认 **SQLite**（`runtime/store.py`）：事务原子、按 key 增量写、多写者安全
- `DZMM_STORE=json` 或 `runtime` 包缺失 → 退回原来的 JSON 读写（文件保留，可回滚）
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
PROFILES_FILE = ROOT / "profiles.json"

_store = None
_sqlite_reason = ""


# ---------------------------------------------------------------- 后端选择

def _sqlite_enabled() -> bool:
    global _sqlite_reason
    if not _is_env_on():
        _sqlite_reason = "DZMM_STORE=json"
        return False
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from runtime.store import store_enabled
        if not store_enabled():
            _sqlite_reason = "DZMM_STORE=json"
            return False
        return True
    except Exception as e:                                  # noqa: BLE001
        _sqlite_reason = "runtime 不可用: " + str(e)[:60]
        return False


def _is_env_on() -> bool:
    v = (os.environ.get("DZMM_STORE") or "sqlite").strip().lower()
    return v not in ("json", "off", "0", "no", "false")


def using_sqlite() -> bool:
    return _sqlite_enabled()


def backend_reason() -> str:
    _sqlite_enabled()
    return "sqlite" if _sqlite_reason == "" else _sqlite_reason


def _get_store():
    """懒建 Store（首次自动从 JSON 导入一次）。"""
    global _store
    if _store is None:
        from runtime.store import Store, default_db_path
        db = os.environ.get("DZMM_STORE_DB") or str(default_db_path(ROOT))
        st = Store(db)
        try:
            st.import_json(STATE_FILE, PROFILES_FILE)
        except Exception:
            pass
        _store = st
    return _store


def close() -> None:
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:
            pass
        _store = None


# ---------------------------------------------------------------- state

def _load_state_json() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state_json(s: dict) -> None:
    # 保持原样：写 text（挂载点上 os.replace 会 Device busy）。仅作回滚后端。
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    """读全部状态 `{cid: {...}, "dice_game": {...}}` —— 与旧 JSON 语义**完全一致**。

    ⚠️ **绝不要**按"键是不是 UUID"过滤！`state.json` 除 cid 外还有顶层键，
    例如骰子局状态 `state["dice_game"]`（`_dice_state()` 读它、`_dice_save()` 写它）。
    早期版本按 UUID 过滤，会让**骰子局状态永远读成空**；现已由回归测试钉死。
    """
    if _sqlite_enabled():
        try:
            return _get_store().load_scope("group")
        except Exception:
            pass
    return _load_state_json()


def save_state(s: dict) -> None:
    if _sqlite_enabled():
        try:
            _get_store().save_scope("group", s or {})
            return
        except Exception:
            pass
    _save_state_json(s or {})


# ---------------------------------------------------------------- 群状态：单键读写

def group_get(cid: str, key: str, default=None):
    """读某群的**单个键**。"""
    if _sqlite_enabled():
        try:
            g = _get_store().get("group", cid) or {}
            return g.get(key, default)
        except Exception:
            pass
    return (_load_state_json().get(cid) or {}).get(key, default)


def group_set(cid: str, key: str, value) -> None:
    """写某群的**单个键** —— 只写该群那一行。

    比旧的"load 全部群 → 改 → save 全部群"更省，且竞态窗口更小
    （P8 状态存取器的核心价值）。
    """
    if _sqlite_enabled():
        try:
            st = _get_store()
            g = st.get("group", cid) or {}
            g[key] = value
            st.set("group", cid, g)
            return
        except Exception:
            pass
    raw = _load_state_json()
    g = raw.get(cid) or {}
    g[key] = value
    raw[cid] = g
    _save_state_json(raw)


def top_get(key: str, default=None):
    """读**顶层**键（如 `dice_game`）—— 注意它不按 cid 存。"""
    if _sqlite_enabled():
        try:
            return _get_store().get("group", key)
        except Exception:
            pass
    return _load_state_json().get(key, default)


def top_set(key: str, value) -> None:
    if _sqlite_enabled():
        try:
            _get_store().set("group", key, value)
            return
        except Exception:
            pass
    raw = _load_state_json()
    raw[key] = value
    _save_state_json(raw)


# ---------------------------------------------------------------- profiles

def _load_profiles_json() -> dict:
    try:
        if PROFILES_FILE.exists():
            return json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_profiles_json(data: dict) -> bool:
    try:
        PROFILES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def load_profiles() -> dict:
    if _sqlite_enabled():
        try:
            return _get_store().load_scope("profile")
        except Exception:
            pass
    return _load_profiles_json()


def save_profiles(data: dict) -> bool:
    if _sqlite_enabled():
        try:
            _get_store().save_scope("profile", data or {})
            return True
        except Exception:
            pass
    return _save_profiles_json(data or {})


def get_profile(uid: str):
    if _sqlite_enabled():
        try:
            return _get_store().get("profile", uid)
        except Exception:
            pass
    return _load_profiles_json().get(uid)


def upsert_profile(uid: str, profile: dict) -> bool:
    if _sqlite_enabled():
        try:
            _get_store().set("profile", uid, profile)
            return True
        except Exception:
            pass
    data = _load_profiles_json()
    data[uid] = profile
    return _save_profiles_json(data)


# ---------------------------------------------------------------- 诊断

def stats() -> dict:
    out = {"backend": "json" if not _sqlite_enabled() else "sqlite",
           "reason": backend_reason()}
    if out["backend"] == "sqlite":
        try:
            st = _get_store()
            out["db"] = st.path
            out["counts"] = st.counts()
        except Exception as e:                              # noqa: BLE001
            out["error"] = str(e)[:80]
    return out
