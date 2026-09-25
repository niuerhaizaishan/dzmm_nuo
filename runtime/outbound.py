"""出站决策与长消息分片。

`BOT_GROUP_MAX_CHARS` / `BOT_GROUP_MAX_NEWLINES` / `group_message_chunks` /
`requires_bot_group_sender` 移植自 `DDZM/src/dzmm_bot/runtime/outbound.py`
（那边已在生产验证：普通账号受 1000 字符 / 10 换行限制，超限必须走官方 Bot）。

`should_use_bot` 是本项目新增的**路由表**实现（总方案 §5）：把"什么内容走哪条
通道"写成纯函数并加测试，避免这段判断散落在业务流程里被改错。
"""
from .contracts import DeliveryKind

__all__ = [
    "BOT_GROUP_MAX_CHARS",
    "BOT_GROUP_MAX_NEWLINES",
    "group_message_chunks",
    "requires_bot_group_sender",
    "should_use_bot",
]

BOT_GROUP_MAX_CHARS = 1000
BOT_GROUP_MAX_NEWLINES = 10


def group_message_chunks(text: str) -> list[str]:
    """把文本切成满足普通账号限制的若干段（每段 ≤1000 字符、≤10 换行）。

    注意：换行可能落在切分边界上而被吞掉（上游 DDZM 的既有行为，原样移植）。
    内容（非换行字符）不会丢失。
    """
    chunks: list[str] = []
    current: str | None = None
    for line_number, line in enumerate(text.split("\n")):
        remaining = line
        first_piece = True
        while remaining or first_piece:
            first_piece = False
            capacity = BOT_GROUP_MAX_CHARS
            if current is not None:
                capacity -= len(current) + (1 if line_number else 0)
            if capacity <= 0 or (
                current is not None
                and line_number
                and current.count("\n") >= BOT_GROUP_MAX_NEWLINES
            ):
                chunks.append(current)
                current = None
                continue
            piece = remaining[:capacity]
            remaining = remaining[capacity:]
            if current is None:
                current = piece
            elif line_number:
                current = f"{current}\n{piece}"
            else:
                current += piece
            if remaining:
                chunks.append(current)
                current = None
    if current is not None:
        chunks.append(current)
    return chunks


def requires_bot_group_sender(text: str) -> bool:
    """普通账号发不出去（超长或换行过多）→ 必须走官方 Bot。"""
    return (
        len(text) > BOT_GROUP_MAX_CHARS
        or text.count("\n") > BOT_GROUP_MAX_NEWLINES
    )


def should_use_bot(
    *,
    delivery_kind: DeliveryKind | str = DeliveryKind.GROUP,
    content_type: str = "text",
    recall_after_seconds: int | None = None,
    bot_enabled: bool,
) -> bool:
    """路由表：只有"群 + 文本 + 不需撤回 + Bot 已启用"才走官方 Bot。

    其余一律普通账号 —— 因为：
      - 私聊：Bot API 不处理私聊
      - 图片 / 分享：Bot API 不支持上传与 share
      - 需要撤回：只有普通账号能 `message:recall`
    """
    if not bot_enabled:
        return False
    if str(delivery_kind) != str(DeliveryKind.GROUP):
        return False
    if content_type != "text":
        return False
    if recall_after_seconds is not None:
        return False
    return True
