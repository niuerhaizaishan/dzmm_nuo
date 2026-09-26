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
P12-F 加了**热加载**：`brains.refresh_prompts()` 会把 39 个 prompt 常量按磁盘上
的文件重算一遍（`_p()` 里登记了每个 prompt 的代码默认值，见 `brains._PROMPT_DEFAULTS`）。
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

__all__ = ["load", "stats", "sources", "prompt_dir", "DEFAULT_DIR",
           "clear_cache", "cache_stats"]


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


def load(name: str, default: str) -> str:
    """返回该 prompt 的实际内容。

    `prompts/<name>.md` 存在且**非空**则用它；否则用 `default`（代码里的字面量）。

    注意：返回的是文件的**原始内容**（不做 strip）—— 字面量可能有意义的
    首尾换行，strip 会改变 prompt。

    缓存按 **(路径, mtime, 大小)** 判定：content 变了就重读。
    文件"不存在"也会被缓存（mtime=-1），一旦有人创建它，mtime 就变了。
    """
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


def sources() -> dict:
    """每个 prompt 实际来自哪里：`"file"` 或 `"code"`。"""
    return dict(_sources)


def stats() -> dict:
    d = prompt_dir()
    from_file = sum(1 for v in _sources.values() if v == "file")
    return {
        "dir": str(d),
        "enabled": _enabled(),
        "total": len(_sources),
        "from_file": from_file,
        "from_code": len(_sources) - from_file,
        "files_on_disk": len(list(d.glob("*.md"))) if d.is_dir() else 0,
    }
