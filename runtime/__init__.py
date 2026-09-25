"""跨模块契约与配置（P1）。

参考 `DDZM/src/dzmm_bot/runtime/`。业务代码**只**通过这里流转消息与出站任务，
不再直接调平台接口。
"""
from .contracts import (
    BotIdentity,
    DeliveryKind,
    InboundMessage,
    LoginState,
    OutboundMessage,
    OutboundStatus,
    WorkerHeartbeat,
)
from .outbound import (
    BOT_GROUP_MAX_CHARS,
    BOT_GROUP_MAX_NEWLINES,
    group_message_chunks,
    requires_bot_group_sender,
    should_use_bot,
)
from .settings import BotConfig, Settings, load_settings

__all__ = [
    "BotIdentity",
    "DeliveryKind",
    "InboundMessage",
    "LoginState",
    "OutboundMessage",
    "OutboundStatus",
    "WorkerHeartbeat",
    "BOT_GROUP_MAX_CHARS",
    "BOT_GROUP_MAX_NEWLINES",
    "group_message_chunks",
    "requires_bot_group_sender",
    "should_use_bot",
    "BotConfig",
    "Settings",
    "load_settings",
]
