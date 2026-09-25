"""跨模块契约（冻结 dataclass）。

参考 `DDZM/src/dzmm_bot/runtime/contracts.py`。

设计意图：把"消息、出站任务、心跳、Bot 身份"这些跨模块流转的东西固定成不可变
数据结构，替代现在到处传裸 dict 的做法。这样：
  - 字段少一个、类型写错，在构造时就炸，而不是在生产里静默出错；
  - 出站队列（P3）要落库时，直接按 dataclass 建表。
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

__all__ = [
    "LoginState",
    "DeliveryKind",
    "OutboundStatus",
    "BotIdentity",
    "InboundMessage",
    "OutboundMessage",
    "WorkerHeartbeat",
]


class LoginState(StrEnum):
    """侧车（普通账号）的登录态。"""

    READY = "ready"
    AUTH_REQUIRED = "auth_required"
    AUTH_IN_PROGRESS = "auth_in_progress"


class DeliveryKind(StrEnum):
    GROUP = "group"
    DIRECT = "direct"


class OutboundStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class BotIdentity:
    """一个官方 Bot 的身份。

    现阶段只有认证妹子群一个；设计上按列表承载，日后加群只改配置。
    `name` / `short` 必须来自配置，**不再从成员表查**——Bot API 没有成员接口。
    """

    key: str          # "nuo_main"
    name: str         # "小小糯～"（@我 判定用）
    short: str        # "小小糯"
    cid: str          # 绑定的 chatroom_id
    token: str        # X-Bot-Token
    persona: str      # personas 里的键
    secret: str = ""  # Webhook Secret（P10 才用）


@dataclass(frozen=True)
class InboundMessage:
    """入站消息（当前来自 Socket.IO）。"""

    chatroom_id: str
    platform_message_id: str
    sender_platform_id: str
    text: str
    received_at: datetime
    source_type: str = "group"
    is_bot: bool = False


@dataclass(frozen=True)
class OutboundMessage:
    """出站任务。业务只**写入**它，由 Worker 领租约发出（P3）。"""

    text: str
    destination_chatroom_id: str
    delivery_kind: DeliveryKind = DeliveryKind.GROUP
    content_type: str = "text"                 # text / image / share
    inbound_message_id: str | None = None
    id: UUID = field(default_factory=uuid4)
    status: OutboundStatus = OutboundStatus.PENDING
    lease_worker_id: str | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    platform_sent_id: str | None = None
    # 非空 → 必须走普通账号（官方 Bot 不能撤回）
    recall_after_seconds: int | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class WorkerHeartbeat:
    worker_id: str
    login_state: LoginState
    recorded_at: datetime
    listening: bool = True
    account_display_name: str | None = None
    bot_delivery_state: str = "unknown"
    bot_delivery_error: str | None = None
