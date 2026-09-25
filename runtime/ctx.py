"""当前群上下文（P8 第 4 步）。

`_ACTIVE_CID` 原来是 `bot.py` 的模块级全局：**24 个函数读、2 处写**。
拆模块时它是最大的障碍 —— 每个搬出去的模块都要知道"现在在处理哪个群"。

这里成为它的**唯一真源**，同时收留 `PRIMARY_CID`（常驻群的 cid）：
搬出去的模块要用它（`TREND_CIDS = {PRIMARY_CID}` 这类），
留在 `bot.py` 里就是一个模块外的隐式依赖。

`bot.py` 保留一个同名镜像 `_ACTIVE_CID` 给自己那 24 个读点（不在 936 行的
`main_loop` 里做 24 处机械替换），但两个写点必须走 `bot._set_active_cid()`，
由它同时更新镜像和这里。

为什么不干脆把 24 处读点也改掉：那是在最长的那个函数里做机械替换，
而收益只是"少一个镜像变量"。写点只有 2 处，改成 setter 是可验证的；
镜像与真源的一致性由 `tests/test_p8s4_split.py` 与部署闸门共同钉住
（`bot.py` 里 `_ACTIVE_CID =` 只允许出现在 setter 内部）。

⚠️ 外部脚本（`batch_register.py` / `diag_fortune.py` / `regen_novel.py` 等）
过去直接写 `bot._ACTIVE_CID = cid`，必须改成 `bot._set_active_cid(cid)`，
否则真源不会更新 —— 搬出去的模块会读到旧的群。
"""
PRIMARY_CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"  # 常驻群（妹子认证群，page 常驻不 goto）

# 初值 = 常驻群，与 bot.py 的 `_ACTIVE_CID = GROUPS[0]` 对齐：
# 这样"主循环还没轮到设置"的那段时间里，真源与镜像也不会不一致。
_active_cid = PRIMARY_CID


def get_active_cid() -> str:
    """当前正在处理的群 cid。"""
    return _active_cid


def set_active_cid(cid: str) -> None:
    global _active_cid
    _active_cid = cid or ""


__all__ = ["PRIMARY_CID", "get_active_cid", "set_active_cid"]
