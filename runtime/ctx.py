"""当前群上下文（P8 第 4 步；P12-I 改成**上下文局部**）。

`_ACTIVE_CID` 原来是 `bot.py` 的模块级全局：**24 个函数读、2 处写**。
拆模块时它是最大的障碍 —— 每个搬出去的模块都要知道"现在在处理哪个群"。

这里成为它的**唯一真源**，同时收留 `PRIMARY_CID`（常驻群的 cid）：
搬出去的模块要用它（`TREND_CIDS = {PRIMARY_CID}` 这类），
留在 `bot.py` 里就是一个模块外的隐式依赖。

P12-I（按群并行）：主循环改成"每个群一个实例"之后，两个实例会**同时**设置
"我在处理哪个群"。普通全局在这里会串台（Rose 的房间逻辑读到认证群的 cid），
所以真源必须是 `contextvars.ContextVar`：每个 asyncio 任务各持一份。
`bot.py` 里的 `_ACTIVE_CID` 退化成"给外部脚本/测试看的遗留镜像"，
读点一律走 `bot._active_cid()`（= 这里的 `get_active_cid()`）。

⚠️ 外部脚本（`batch_register.py` / `diag_fortune.py` / `regen_novel.py` 等）
过去直接写 `bot._ACTIVE_CID = cid`，必须改成 `bot._set_active_cid(cid)`，
否则真源不会更新 —— 搬出去的模块会读到旧的群。
"""
import contextvars

PRIMARY_CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"  # 常驻群（妹子认证群，page 常驻不 goto）

# P12-I（按群并行）：**必须是 contextvars，不能是普通全局**。
# 主循环改成"每个群一个实例"之后，两个实例会同时设置"我在处理哪个群"；
# 普通全局会互相覆盖（Rose 的房间调用读到认证群的 cid），contextvars 里
# 每个 asyncio 任务各自持有一份，互不干扰。
# 初值 = 常驻群，与 bot.py 的 `_ACTIVE_CID = GROUPS[0]` 对齐。
_active_cid = contextvars.ContextVar("active_cid", default=PRIMARY_CID)


def get_active_cid() -> str:
    """当前正在处理的群 cid（**当前任务/上下文**的那一份）。"""
    return _active_cid.get() or ""


def set_active_cid(cid: str) -> None:
    """设置当前上下文的群 cid。"""
    _active_cid.set(cid or "")


def reset_active_cid(cid: str = None) -> None:
    """给测试/一次性脚本用：把当前上下文重置成指定群（默认常驻群）。"""
    _active_cid.set(cid or PRIMARY_CID)


# P3-7：撞验证码旗标的**唯一真源**（进程级，非任务局部 —— 风控挡的是整个账号的发送）。
# 写点：main_loop 三处（恢复×2、触发×1）；读点：runtime/sender.py（发送前/重试前判断）。
_captcha_active = False


def captcha_active() -> bool:
    """现在是否处于「撞验证码」状态（发送路径要放宽/收紧的依据）。"""
    return _captcha_active


def set_captcha_active(v: bool) -> None:
    """更新撞验证码旗标（main_loop 专用）。"""
    global _captcha_active
    _captcha_active = bool(v)


__all__ = ["PRIMARY_CID", "get_active_cid", "set_active_cid", "reset_active_cid",
           "captcha_active", "set_captcha_active"]
