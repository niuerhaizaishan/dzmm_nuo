"""P12-L：薇丝的自我介绍配图（**只发图，不带字**）。

官方 Bot API 只能发 **URL 图片**（`sendPhoto`，没有 multipart），所以图托管在自家
域名上：`https://www.nuonuo.pw/img/weisi.png`（Caddy 的 `/img/*` → 容器 `/srv/img`，
见 `deploy/Caddyfile` 与 `deploy/start_caddy.sh`）。**换图 = 换同名文件**，不用改配置。

三条触发路径（屋主 2026-09-27 定："两处都发，而且问'你是谁'这类自然语言也发"）：

1. `/自我介绍`・`/介绍`・`/你是谁`・`/whoami` 指令回复之后（`bot.py` 命令分支）；
2. 新客进房、管家问好之后（`bot.py` 的 `room_welcome` 分支）；
3. 群里自然语言问"你是谁 / 你叫什么 / 介绍一下你"之后（房间消息分支）。

**开关就是话术本身**：`p("intro_photo", cid)` 为空 = 不发（认证群那份默认就是空）。
所以"关掉配图"不需要新功能开关 —— 人设页里把那行 URL 清掉即可，热加载约 1 秒生效。

⚠️ 同一个问题可能在同一轮里被两条路径各看到一次（@ 分支 + 房间分支），
所以 `maybe_send` 带一个按 `(cid, 原文)` 的短去重窗口，避免同一个问题连发两张图。
"""
import asyncio
import re
import time

from personas import all_names, p as _p
from runtime.obs import log

__all__ = ["photo_url", "wants_photo", "send", "maybe_send", "reset_dedupe",
           "DEFAULT_URL"]

# 默认立绘（rose_butler 的话术里也写着这一条；改图就换同名文件）
DEFAULT_URL = "https://www.nuonuo.pw/img/weisi.png"

# "你是谁"这一类的问法。刻意写窄：只认**在问管家身份**的说法，
# 免得客人聊别的事也蹦出一张图（比如"你是什么意思"不会命中）。
_WHO_PATTERNS = (
    "你是谁", "你是哪位", "你是哪一位", "你谁啊", "你谁呀",
    "你叫什么", "你叫啥", "你的名字", "你名字",
    "介绍一下你", "自我介绍", "你是什么人",
    "你是干什么的", "你是做什么的", "你是干嘛的", "你干啥的",
)
# 句子里带了本群管家的名字时，再出现这些"问身份"的字眼才算
_WHO_WORDS = ("是谁", "谁啊", "哪位", "介绍", "干什么", "做什么", "干嘛的", "什么来头")
# 长句多半是在聊别的（"你是谁"这一问不会很长）
MAX_CHARS = 60
# 只靠"名字 + 问身份的词"命中时，句子要更短才算（这种判定最松，得收紧）
MAX_CHARS_NAME = 30
# 同一句的重复保护窗口（秒）：挡住 @ 分支与房间分支对同一条消息各发一次
DEDUP_SEC = 120.0

_sent: dict = {}


def photo_url(cid: str = None) -> str:
    """本群该发的配图 URL；没有/不是 http 开头 → 空串（= 不发）."""
    try:
        u = str(_p("intro_photo", cid) or "").strip()
    except Exception:                                        # noqa: BLE001
        return ""
    return u if u.startswith("http") else ""


def wants_photo(text: str, cid: str = None) -> bool:
    """这句话是不是在问"你是谁"（自然语言路径的判定点）."""
    t = re.sub(r"\s+", "", str(text or ""))
    if not t or t.startswith("/"):
        return False
    if len(t) <= MAX_CHARS and any(p in t for p in _WHO_PATTERNS):
        return True
    if len(t) > MAX_CHARS_NAME:
        return False
    try:
        names = [n for n in all_names(cid) if n]
    except Exception:                                        # noqa: BLE001
        names = []
    if names and any(n in t for n in names) and any(w in t for w in _WHO_WORDS):
        return True
    return False


def reset_dedupe() -> None:
    """清掉去重表（测试用）."""
    _sent.clear()


def _dup(cid: str, text: str) -> bool:
    """这条消息刚发过图吗（发了就跳过）."""
    key = (cid, re.sub(r"\s+", "", str(text or "")))
    now = time.time()
    for k, ts in list(_sent.items()):                        # 顺手清理过期项
        if now - ts > DEDUP_SEC:
            _sent.pop(k, None)
    if now - _sent.get(key, 0.0) < DEDUP_SEC:
        return True
    _sent[key] = now
    return False


async def send(sender, cid: str, reason: str = "") -> bool:
    """发配图。没配 URL / 没有 Bot 通道 / 发送失败 → False（**绝不抛**给主循环）.

    为什么只走官方 Bot：房间群里发消息的是**薇丝-bot**，若改走账号侧车，
    图片会以另一个身份（小小糯～）出现，人设立刻穿帮。
    """
    url = photo_url(cid)
    if not url:
        return False
    try:
        getter = getattr(sender, "bot_for", None)
        bot = getter(cid) if callable(getter) else None
    except Exception as e:                                   # noqa: BLE001
        log("🖼️ [{}] 找不到 Bot 通道：{}".format(cid[:8], str(e)[:60]))
        return False
    if bot is None:
        log("🖼️ [{}] 本群没有 Bot 发送器，配图跳过".format(cid[:8]))
        return False
    try:
        mid = await asyncio.to_thread(bot.send_photo_url, cid, url)
    except Exception as e:                                   # noqa: BLE001
        log("🖼️ [{}] 配图发送失败（{}）：{}".format(
            cid[:8], reason or "-", "{}: {}".format(type(e).__name__, str(e))[:100]))
        return False
    log("🖼️ [{}] 自我介绍配图已发（{}）mid={}".format(
        cid[:8], reason or "-", str(mid)[:8]))
    return True


async def maybe_send(sender, cid: str, text: str, reason: str = "") -> bool:
    """自然语言路径：先判意图，再判重复，最后发."""
    if not wants_photo(text, cid):
        return False
    if _dup(cid, text):
        log("🖼️ [{}] 这条刚发过配图，跳过：{}".format(cid[:8], str(text)[:20]))
        return False
    return await send(sender, cid, reason=reason or "自然语言")
