"""日志（P8 第 4 步从 bot.py 拆出的叶子工具）。

`log` 被 bot.py 里几乎每个函数调用，是最典型的"叶子工具"：只依赖 `ROOT`
和 `datetime`，谁都不依赖。**必须先把它搬出来** —— 否则后面每拆一个模块，
新模块都要为了打一行日志回指 bot.py。
"""
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

__all__ = ["log", "ROOT"]


def log(msg: str) -> None:
    line = datetime.now().strftime("%H:%M:%S") + " " + msg
    print(line, flush=True)
    try:
        with open(ROOT / "bot.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
