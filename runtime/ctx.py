"""当前群上下文（P8 第 4 步）。

`_ACTIVE_CID` 原来是 `bot.py` 的模块级全局：**24 个函数读、2 处写**。
拆模块时它是最大的障碍 —— 每个搬出去的模块都要知道"现在在处理哪个群"。

这里成为它的**唯一真源**。`bot.py` 保留一个同名镜像 `_ACTIVE_CID` 给自己那 24 个
读点（不在 936 行的 `main_loop` 里做 24 处机械替换），但两个写点必须走
`bot._set_active_cid()`，由它同时更新镜像和这里。

为什么不干脆把 24 处读点也改掉：那是在最长的那个函数里做机械替换，
而收益只是"少一个镜像变量"。写点只有 2 处，改成 setter 是可验证的；
镜像与真源的一致性由 `tests/test_p8s4_split.py` 与部署闸门共同钉住
（`bot.py` 里 `_ACTIVE_CID =` 只允许出现在 setter 内部）。

⚠️ 外部脚本（`batch_register.py` / `diag_fortune.py` / `regen_novel.py` 等）
过去直接写 `bot._ACTIVE_CID = cid`，必须改成 `bot._set_active_cid(cid)`，
否则真源不会更新 —— 搬出去的模块会读到旧的群。
"""

_active_cid = ""


def get_active_cid() -> str:
    """当前正在处理的群 cid（未设置过则空串）。"""
    return _active_cid


def set_active_cid(cid: str) -> None:
    global _active_cid
    _active_cid = cid or ""


__all__ = ["get_active_cid", "set_active_cid"]
