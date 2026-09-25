"""公演小说落盘（P8 第 4 步从 bot.py 搬出）。

标题提取 / 文件名生成 / 目录创建。放在公演之前，因为公演结束时要存小说。

`_group_name_cache` 是**可变对象**：`bot.py` 的 `get_group_name` 也用同一个 dict
（群名 5 分钟缓存），所以搬出来之后两边共享，不需要改 `get_group_name`。
"""
import time
from datetime import datetime
from pathlib import Path

from dzmm import get_chatroom_name

ROOT = Path(__file__).resolve().parent.parent


__all__ = ["_extract_novel_title", "_novel_filename", "_novel_save_path"]


_group_name_cache = {}  # cid -> {"name":..., "ts":...}


def _extract_novel_title(novel: str) -> str:
    """提取小说标题（第一个 # 行）."""
    for line in novel.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return "公演小说"


def _novel_filename(novel: str) -> str:
    """生成小说文件名：标题 + 生成时间."""
    title = _extract_novel_title(novel)
    for ch in '/\\:*?"<>|':
        title = title.replace(ch, "")
    title = title.strip() or "公演小说"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return title + "_" + ts + ".md"


def _novel_save_path(cid: str, novel: str) -> str:
    """返回小说完整保存路径（novels/<群名>/<标题>_<时间>.md），自动建目录."""
    fname = _novel_filename(novel)
    gname = ""
    cache = _group_name_cache.get(cid, {"name": "", "ts": 0.0})
    if time.time() - cache["ts"] < 300 and cache["name"]:
        gname = cache["name"]
    else:
        try:
            gname = get_chatroom_name(cid) or ""
        except Exception:
            gname = ""
        cache["name"] = gname
        cache["ts"] = time.time()
        _group_name_cache[cid] = cache
    for ch in '/\\:*?"<>|':
        gname = gname.replace(ch, "")
    gname = gname.strip() or cid[:8]
    d = ROOT / "novels" / gname
    d.mkdir(parents=True, exist_ok=True)
    return str(d / fname)
