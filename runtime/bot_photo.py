"""P12-N：按 URL 往群里发图的**唯一出口**（只走官方 Bot 通道）。

官方 Bot API 只能发 URL 图片（`sendPhoto`，没有 multipart），所以 URL 一律指向自家
域名上托管的图（Caddy `/img/*` → 容器 `/srv/img`，见 `deploy/Caddyfile` 与
`deploy/start_caddy.sh`）。**换图 = 换同名文件**。

为什么要有这个模块（而不是每个功能各自发）：

1. **只走官方 Bot**：房间群里说话的是 Bot（薇丝-bot）。若改走账号侧车，图片会以另一个
   身份（小小糯～）出现，人设当场穿帮。
2. **绝不抛异常**：它挂在每条回复后面，抛一次就会毁掉整轮消息处理（这是纪律，不是风格）。
3. **短去重**：同一条消息可能被两条分支各看到一次，同 key 在窗口内只发一张。
"""
import asyncio
import re
import time

from runtime.obs import log

__all__ = ["send", "dup", "reset_dedupe", "DEDUP_SEC"]

DEDUP_SEC = 120.0
_sent: dict = {}


def reset_dedupe() -> None:
    """清空去重表（测试用）."""
    _sent.clear()


def dup(cid: str, key: str) -> bool:
    """这个 key 刚发过吗？是则不重复发。

    `key` 一般是「标签:内容」；同一 key 在 `DEDUP_SEC` 内只放行一次。
    """
    k = (cid, re.sub(r"\s+", "", str(key or "")))
    now = time.time()
    for kk, ts in list(_sent.items()):          # 顺手清理过期项
        if now - ts > DEDUP_SEC:
            _sent.pop(kk, None)
    if now - _sent.get(k, 0.0) < DEDUP_SEC:
        return True
    _sent[k] = now
    return False


async def send(sender, cid: str, url: str, reason: str = "") -> bool:
    """发一张 URL 图。没有 URL / 没有 Bot 通道 / 平台报错 → `False`（**绝不抛**）."""
    if not url or not str(url).startswith("http"):
        return False
    try:
        getter = getattr(sender, "bot_for", None)
        bot = getter(cid) if callable(getter) else None
    except Exception as e:                                   # noqa: BLE001
        log("🖼️ [{}] 找不到 Bot 通道：{}".format(cid[:8], str(e)[:60]))
        return False
    if bot is None:
        log("🖼️ [{}] 本群没有 Bot 发送器，图片跳过".format(cid[:8]))
        return False
    try:
        mid = await asyncio.to_thread(bot.send_photo_url, cid, url)
    except Exception as e:                                   # noqa: BLE001
        log("🖼️ [{}] 配图发送失败（{}）：{}".format(
            cid[:8], reason or "-",
            "{}: {}".format(type(e).__name__, str(e))[:100]))
        return False
    log("🖼️ [{}] 配图已发（{}）mid={}".format(cid[:8], reason or "-", str(mid)[:8]))
    return True
