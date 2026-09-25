"""统一配置：`Settings.from_environment()`。

参考 `DDZM/src/dzmm_bot/runtime/settings.py`。

目的：替代现在 `bot.py` 顶部硬编码的 `GROUPS` / `PRIMARY_CID` / 各种阈值，
以及散落在 `.env` 里的键。**启动即校验**——配置写错立刻 fail fast，
而不是跑到线上某个分支才炸。
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Settings", "BotConfig", "load_settings"]

# 认证妹子群（本次迁移唯一目标群）
DEFAULT_GROUP_CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
# 侧车账号（原 NUO_UID，登录态持有者）
DEFAULT_SIDECAR_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"
# 糯糯～（群主 / Bot 创建者）
DEFAULT_OWNER_UID = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"


@dataclass(frozen=True)
class BotConfig:
    """单个官方 Bot 的配置（对应 contracts.BotIdentity）。"""

    key: str
    name: str
    short: str
    cid: str
    token: str
    persona: str = "nuo"
    secret: str = ""


@dataclass(frozen=True)
class Settings:
    # 平台
    origin: str
    proxy: str | None
    # 群 / 账号
    group_cid: str
    sidecar_uid: str
    owner_uid: str
    # Bot 通道（token 为空 = 关闭 Bot 通道，全部走旧通道）
    bot: BotConfig | None
    # 发送通道开关：legacy | botapi
    send_transport: str
    # 数据
    data_dir: Path
    outbox_db: Path
    # LLM
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    # 出站并发（1..16）
    outbound_concurrency: int = 4
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_environment(cls, env: dict | None = None) -> "Settings":
        e = os.environ if env is None else env

        def get(name: str, default: str | None = None) -> str | None:
            if env is None:
                return os.environ.get(name, default)
            return e.get(name, default)

        def required(name: str) -> str:
            v = get(name)
            if not v:
                raise ValueError(f"{name} 必须设置且非空")
            return v

        def optional(name: str) -> str | None:
            v = get(name)
            return v or None

        origin = (get("DZMM_ORIGIN") or "https://www.dzmm.ai").rstrip("/")
        proxy = optional("DZMM_PROXY") or None

        group_cid = get("DZMM_GROUP_CID") or DEFAULT_GROUP_CID
        sidecar_uid = get("DZMM_SIDECAR_UID") or DEFAULT_SIDECAR_UID
        owner_uid = get("DZMM_OWNER_UID") or DEFAULT_OWNER_UID

        send_transport = (get("SEND_TRANSPORT") or "legacy").strip()
        if send_transport not in ("legacy", "botapi"):
            raise ValueError(
                f"SEND_TRANSPORT 只能是 legacy 或 botapi，收到 {send_transport!r}")

        data_dir = Path(get("DZMM_DATA_DIR") or ".").resolve()
        outbox_db = Path(get("DZMM_OUTBOX_DB") or (data_dir / "data" / "outbox.db"))

        # Bot 通道：有 token 才启用
        token = optional("DZMM_BOT_TOKEN")
        bot: BotConfig | None = None
        if token:
            bot = BotConfig(
                key=get("DZMM_BOT_KEY") or "nuo_main",
                name=get("DZMM_BOT_NAME") or "小小糯～",
                short=get("DZMM_BOT_SHORT") or "小小糯",
                cid=get("DZMM_BOT_CID") or group_cid,
                token=token,
                persona=get("DZMM_BOT_PERSONA") or "nuo",
                secret=optional("DZMM_BOT_SECRET") or "",
            )
            if bot.cid != group_cid:
                raise ValueError(
                    f"Bot 绑定群({bot.cid}) 与目标群({group_cid}) 不一致；"
                    "现阶段只服务认证群，避免误发")

        conc = int(get("DZMM_OUTBOUND_CONCURRENCY") or 4)
        if not 1 <= conc <= 16:
            raise ValueError("DZMM_OUTBOUND_CONCURRENCY 必须在 1..16")

        return cls(
            origin=origin,
            proxy=proxy,
            group_cid=group_cid,
            sidecar_uid=sidecar_uid,
            owner_uid=owner_uid,
            bot=bot,
            send_transport=send_transport,
            data_dir=data_dir,
            outbox_db=outbox_db,
            deepseek_api_key=optional("DEEPSEEK_API_KEY"),
            deepseek_model=get("DZMM_DEEPSEEK_MODEL") or "deepseek-v4-flash",
            deepseek_base_url=get("DZMM_DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            outbound_concurrency=conc,
        )

    @property
    def bot_enabled(self) -> bool:
        """Bot 通道是否可用：既要配了 token，又要开关打开。"""
        return self.bot is not None and self.send_transport == "botapi"


def load_settings() -> Settings:
    return Settings.from_environment()
