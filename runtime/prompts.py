"""Prompt 外置（P8 第 3 步）。

### 设计：**覆盖**，不是搬家

```python
CMD_PROMPT = _p("CMD_PROMPT", \"\"\"<原来的字符串>\"\"\")
```

- 代码里的字面量**保留为默认值** → 文件缺失 / 为空 / 读失败时，行为与改动前**完全一致**。
  这就避开了"prompt 外置"最常见的坑：**文件没了 → prompt 变空 → LLM 乱答**。
- `prompts/<NAME>.md` 存在且非空则覆盖 → 调 prompt 不用改代码、不用重新 build。
- `stats()` 报告实际有几个来自文件，便于确认生效的到底是哪一份。

### 开关

- `DZMM_PROMPT_DIR`：prompt 目录（默认仓库根下的 `prompts/`）
- `DZMM_PROMPTS=off`：整体关闭，只用代码里的默认值

### 生效时机

`brains.py` 在 **import 时**调用 `load()`，所以只改文件不重启是不生效的。
P12-F 加了**热加载**：`brains.refresh_prompts()` 会把已登记的 prompt 常量按磁盘上
的文件重算一遍（`declare()` 把每个 prompt 的代码默认值登记进中央登记册；
P3-5 起出厂默认值住 `prompt_defaults.PD`，brains 只写 `PD["KEY"]`）。
`load()` 自己带**mtime 缓存**（同一文件没动就不重复读盘），
所以"每轮循环检查一次有没有改"是很便宜的。
"""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = _ROOT / "prompts"

_sources: dict[str, str] = {}          # name -> "file" | "code"
# name -> (path, mtime_ns, size, content)：文件没动就直接用缓存
_cache: dict[str, tuple] = {}
# **中央登记册**（P12-G）：name -> {"module": 常量所在的模块, "default": 代码默认值}
# 谁调 `load()` 谁被登记（记的是调用方模块），于是
#   - `brains` 里的 39 个、
#   - `runtime/memory.py` 里的第 40 个（MEMORY_FILTER）
# 都能被 `refresh()` 一起重算并写回**各自的**模块全局 ——
# 不需要每个模块自己维护一份"默认值清单"（那份清单一定会漂移）。
_REGISTRY: dict[str, dict] = {}

__all__ = ["load", "declare", "stats", "sources", "prompt_dir", "DEFAULT_DIR",
           "clear_cache", "cache_stats", "registry", "default_of", "refresh"]


def prompt_dir() -> Path:
    return Path(os.environ.get("DZMM_PROMPT_DIR") or DEFAULT_DIR)


def _enabled() -> bool:
    v = (os.environ.get("DZMM_PROMPTS") or "on").strip().lower()
    return v not in ("off", "0", "no", "false")


def clear_cache() -> None:
    """丢掉 mtime 缓存（改了文件想立刻重读时用）。"""
    _cache.clear()


def cache_stats() -> dict:
    return {"cached": len(_cache),
            "hits": _hits[0], "misses": _hits[1]}


_hits = [0, 0]


def registry() -> dict:
    """登记册：每个 prompt 的代码默认值、所在模块、当前来源。"""
    out = {}
    for name, rec in _REGISTRY.items():
        out[name] = {
            "name": name,
            "module": rec.get("module") or "",
            "default": rec.get("default") or "",
            "default_len": len(rec.get("default") or ""),
            "source": _sources.get(name, "code"),
        }
    return out


def default_of(name: str) -> str:
    return ((_REGISTRY.get(name) or {}).get("default")) or ""


def _read(name: str, default: str) -> str:
    """真正取值（读文件 / 用默认）——**不登记**，供 `refresh()` 复用。"""
    if not _enabled():
        _sources[name] = "code"
        return default
    try:
        p = prompt_dir() / (name + ".md")
        try:
            st = p.stat()
            sig = (str(p), st.st_mtime_ns, st.st_size)
        except OSError:
            sig = (str(p), -1, -1)
        hit = _cache.get(name)
        if hit is not None and hit[:3] == sig:
            _hits[0] += 1
            if hit[3] is not None:
                _sources[name] = "file"
                return hit[3]
            _sources[name] = "code"
            return default
        _hits[1] += 1
        raw = None
        if sig[1] >= 0:
            txt = p.read_text(encoding="utf-8")
            if txt.strip():            # 空文件 = 没覆盖，退回默认值（不要静默变空）
                raw = txt
        _cache[name] = (sig[0], sig[1], sig[2], raw)
        if raw is not None:
            _sources[name] = "file"
            return raw
    except Exception:
        pass
    _sources[name] = "code"
    return default


def declare(name: str, default: str) -> str:
    """**声明**"这个模块持有这个 prompt 常量"（只有常量定义处该调它）。

    ```python
    CMD_PROMPT = _p("CMD_PROMPT", \"\"\"…默认值…\"\"\")     # brains 里的 _p = declare
    ```

    ⚠️ 为什么"声明"与"读取"必须分开（P12-G 踩过）：
    页面、自测、闸门都要**读**当前内容，如果它们也顺手登记，
    登记册就会变成"最后一个读者"（`admin.dashboard`），于是热加载把常量
    写进后台进程、机器人那边永远不更新 —— 而且完全看不出来。
    所以 `load()` 只读不登记。
    """
    name = str(name)
    try:
        import sys as _sys
        _mod = _sys._getframe(1).f_globals.get("__name__", "")
    except Exception:                                          # noqa: BLE001
        _mod = ""
    _REGISTRY[name] = {"module": _mod, "default": default}
    return _read(name, default)


def load(name: str, default: str) -> str:
    """**读取**该 prompt 的实际内容（不登记）。

    `prompts/<name>.md` 存在且**非空**则用它；否则用 `default`（代码里的字面量）。

    注意：返回的是文件的**原始内容**（不做 strip）—— 字面量可能有意义的
    首尾换行，strip 会改变 prompt。

    缓存按 **(路径, mtime, 大小)** 判定：content 变了就重读。
    文件"不存在"也会被缓存（mtime=-1），一旦有人创建它，mtime 就变了。
    """
    return _read(str(name), default)


def refresh() -> list:
    """把所有已登记的 prompt 重算一遍并**写回各自的模块**（P12-G 热加载）。

    返回内容变了的 prompt 名（自证/日志用）。
    """
    import sys
    changed = []
    for name, rec in list(_REGISTRY.items()):
        mod = sys.modules.get(rec.get("module") or "")
        # 只写回"确实持有这个常量"的模块：`hasattr` 这一条挡住了两类事故 ——
        # 登记册被污染（记成了读者模块）、有人把常量改名后忘了改登记册。
        if mod is None or not hasattr(mod, name):
            continue
        try:
            new = _read(name, rec.get("default") or "")
        except Exception:                                      # noqa: BLE001
            continue
        if getattr(mod, name) != new:
            changed.append(name)
            setattr(mod, name, new)
    return sorted(changed)


def sources() -> dict:
    """每个 prompt 实际来自哪里：`"file"` 或 `"code"`。"""
    return dict(_sources)


def stats() -> dict:
    d = prompt_dir()
    from_file = sum(1 for v in _sources.values() if v == "file")
    return {
        "dir": str(d),
        "enabled": _enabled(),
        "total": len(_REGISTRY),
        "from_file": from_file,
        "from_code": len(_REGISTRY) - from_file,
        "files_on_disk": len(list(d.glob("*.md"))) if d.is_dir() else 0,
    }
