"""发送链组装（P3-16 从 bot.py 拆出）。

最终形态：`OutboxSender( RoutingSender( DzmmBotSender / 账号 ) )` ——
出站队列放**最外层**（无论走 Bot 还是账号都落库、串行、可审计），路由层按
配置里的 Bot 注册表把官方 Bot 的群路由到 Bot 通道（`DZMM_BOT_ROUTING=on`
才启用；任何一层异常都退回更简单的形态，绝不阻断 bot 启动）。`pick_leftover`
是主循环"这一轮没轮到、放回队列"的兜底（同一条最多 REQUEUE_MAX 次，防刷屏）。

**不持有 bot 侧状态**：配置（cfg）、源站（origin）、官方 Bot 发送器类
（dzmm_bot_sender_cls —— test_bot_routing_chain patch 的是 `bot.DzmmBotSender`）、
防御性导入标志（runtime_import_ok/err —— 测试 patch `bot._RUNTIME_IMPORT_OK`）
kw-only 注入；RoutingSender/OutboxSender/OutboxStore/INTERRUPTED_NOTE 无测试
patch，模块直连。模块内链式调用把注入一路透传。
"""
import os

from runtime.outbox import INTERRUPTED_NOTE, OutboxSender, OutboxStore
from runtime.obs import ROOT, log
from runtime.routing import RoutingSender


__all__ = ["truthy_env", "outbox_enabled", "log_route", "bot_senders",
           "wrap_with_bot_routing", "wrap_with_outbox", "pick_leftover",
           "REQUEUE_MAX"]


# 同一条消息最多被"放回队列"几次（兜底防刷屏；正常路径 ≤1 次）
REQUEUE_MAX = 3


def truthy_env(name: str, default: str = "off") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("on", "1", "yes", "true")


def outbox_enabled() -> bool:
    """DZMM_OUTBOX=off/0/no/false 可退回直发（回滚开关）。默认开。"""
    v = (os.environ.get("DZMM_OUTBOX") or "on").strip().lower()
    return v not in ("off", "0", "no", "false")


def log_route(event: str, cid: str, detail: str = "") -> None:
    """路由审计。account 太频繁不记；bot_* 事件都记（切换期观察用）。"""
    if event == "account":
        return
    log("bot路由: {} cid={} {}".format(event, (cid or "")[:8], str(detail)[:80]))


def bot_senders(*, cfg, origin, dzmm_bot_sender_cls) -> dict:
    """按**配置里的 Bot 注册表**造发送器：`{cid: DzmmBotSender}`。

    P12-H 之前这里只读 `DZMM_BOT_TOKEN`/`DZMM_BOT_CID` 两个环境变量，
    于是一个 Bot 都加不了 —— 配置里写 `bots[]` 也不会有人送（P11 留下的缺口：
    页面上能加 Bot，运行期没人读）。现在唯一真源是 `cfg.bots`：
    每个 `enabled` 且 token 能解析到（`token_env` → `.env`，或内联）的 Bot
    各拿一个发送器，绑到它自己的 `cid` 上。

    一个群只能有一个 Bot（`config_store` 校验过），所以 cid 就是路由键。
    """
    senders = {}
    origin = (os.environ.get("DZMM_BOT_ORIGIN") or origin).rstrip("/")
    proxy = os.environ.get("DZMM_PROXY") or None
    for b in cfg.bots:
        if not b.enabled:
            log("bot路由: 跳过未启用的 Bot {}（{}）".format(b.key, (b.cid or "")[:8]))
            continue
        token = b.resolve_token()
        if not token:
            log("bot路由: ⚠️ Bot {} 的 token 没解析到（token_env={!r}）—— 这个群退回账号".format(
                b.key, b.token_env))
            continue
        try:
            senders[b.cid] = dzmm_bot_sender_cls(origin, token, proxy=proxy)
            log("bot路由: Bot {}({}) -> 群 {} 已就绪（uid={}）".format(
                b.key, b.name or "-", (b.cid or "")[:8], (b.uid or "未配置")[:8]))
        except Exception as e:                                 # noqa: BLE001
            log("bot路由: Bot {} 初始化失败（该群退回账号）：{}".format(
                b.key, str(e)[:80]))
    return senders


def wrap_with_bot_routing(account, *, cfg, origin, dzmm_bot_sender_cls,
                          runtime_import_ok):
    """把装了官方 Bot 的群路由到 Bot。**默认关闭** —— 接线但不上线。

    `DZMM_BOT_ROUTING=on` 才启用；缺 token/cid 或初始化异常一律退回账号。
    切换时机由人决定，不由代码替人决定。

    配置里一个可用 Bot 都没有时，退回"按环境变量造一个"的老路（P4 的行为），
    免得没挂 config.json 的旧部署突然没了 Bot 通道。
    """
    if not runtime_import_ok:
        return account
    if not truthy_env("DZMM_BOT_ROUTING", "off"):
        log("bot路由: 未启用（DZMM_BOT_ROUTING=off），全部走账号")
        return account
    try:
        senders = bot_senders(cfg=cfg, origin=origin, dzmm_bot_sender_cls=dzmm_bot_sender_cls)
        if not senders:
            token = (os.environ.get("DZMM_BOT_TOKEN") or "").strip()
            cid = (os.environ.get("DZMM_BOT_CID") or "").strip()
            if token and cid:
                origin = (os.environ.get("DZMM_BOT_ORIGIN") or origin).rstrip("/")
                senders = {cid: dzmm_bot_sender_cls(
                    origin, token, proxy=(os.environ.get("DZMM_PROXY") or None))}
                log("bot路由: 配置里没有可用 Bot，退回 env 单 Bot cid={}".format(cid[:8]))
            else:
                log("bot路由: 没有任何可用 Bot（配置与 env 都没有），退回账号")
                return account
        log("bot路由: 已启用 origin={} 覆盖群={} 只走官方={}".format(
            (os.environ.get("DZMM_BOT_ORIGIN") or origin).rstrip("/"),
            ",".join(c[:8] for c in senders),
            "on" if truthy_env("DZMM_OFFICIAL_ONLY", "off") else "off"))
        # P12-J：`DZMM_OFFICIAL_ONLY=on` 时，Bot 明确拒绝也**不回退账号**
        # （代价：那条消息会丢；好处：群里每条都是官方 Bot 发的，没有例外）
        return RoutingSender(account, senders, on_route=log_route,
                             official_only=truthy_env("DZMM_OFFICIAL_ONLY", "off"))
    except Exception as e:                                     # noqa: BLE001
        log("bot路由: 初始化失败，退回账号：" + str(e)[:100])
        return account


def wrap_with_outbox(sender, dry: bool, *, cfg, origin,
                     dzmm_bot_sender_cls, runtime_import_ok,
                     runtime_import_err):
    """组装发送链：`OutboxSender( RoutingSender( 账号 ) )`。

    出站队列放**最外层** —— 无论最终走 Bot 还是账号，都要落库、串行、可审计。
    任何一层异常都退回更简单的形态，不阻断 bot 启动。
    """
    if dry:
        return sender
    if not runtime_import_ok:
        log("outbox: runtime 包不可用，直发（" + runtime_import_err + "）")
        return sender

    inner = wrap_with_bot_routing(sender, cfg=cfg, origin=origin,
                          dzmm_bot_sender_cls=dzmm_bot_sender_cls,
                          runtime_import_ok=runtime_import_ok)

    if not outbox_enabled():
        log("outbox: 已禁用（DZMM_OUTBOX=off），直发")
        return inner
    try:
        db = os.environ.get("DZMM_OUTBOX_DB") or str(ROOT / "data" / "outbox.db")
        store = OutboxStore(db)
        # 启动时清理上次进程留下的未终态记录：发送是同步内联的，
        # 所以此刻任何 pending/leased 都是"上次发送途中被杀"留下的孤儿，
        # 没有任何代码会再去处理它（会让队列深度永远看着像有积压）。
        # 只标 failed 记录原因，**不重发**（不知道平台收没收到，重发就是重复打扰）。
        try:
            n = store.abandon_interrupted(INTERRUPTED_NOTE)
            if n:
                log("outbox: 清理上次中断的出站记录 {} 条（已标 failed，不重发）".format(n))
        except Exception as e:                             # noqa: BLE001
            log("outbox: 清理中断记录失败（不影响发送）：" + str(e)[:60])
        wrapped = OutboxSender(inner, store)
        log("outbox: 已启用 db=" + db + " worker=" + wrapped.worker_id)
        return wrapped
    except Exception as e:                                 # noqa: BLE001
        log("outbox: 初始化失败，退回直发：" + str(e)[:100])
        return inner


def pick_leftover(cid, cmd, cmd_msg, at_msg, at_msg_obj, room_obj, already=None):
    """这一轮没轮到、需要放回队列的消息。

    铁律：**这一轮真正被处理的那条绝不放回**（优先级 `cmd` > `@消息` > 房间消息）。

    ⚠️ 2026-09-27 玫瑰房事故就是这么刷屏的：一条 @ 消息**同时**命中 @ 分支与房间分支
    （`_is_room_trigger` 把"被点名"也算房间触发），@ 分支赢了、房间那条被放回队列，
    下一轮捞回来的还是它 ⇒ 每轮重来一次，薇丝每 1~2 秒回一条（实测 52 次放回、
    约 3 分钟，最后靠重启清空内存队列才停下）。

    另外给一条兜底：同一条最多放回 `REQUEUE_MAX` 次 —— 宁可漏一条，也绝不刷屏。
    """
    out = list(already or [])
    consumed = cmd_msg if cmd else (at_msg_obj if at_msg else room_obj)
    for obj in (at_msg_obj, room_obj, cmd_msg):
        if obj is None or obj is consumed:
            continue
        if any(obj is x for x in out):
            continue
        out.append(obj)
    keep = []
    for obj in out:
        n = int(obj.get("_requeue_n") or 0)
        if n >= REQUEUE_MAX:
            log("♻️ [{}] 同一条已放回 {} 次，不再放回（防刷屏）：{}".format(
                cid[:8], n, str((obj.get("content") or {}).get("text") or "")[:20]))
            continue
        obj["_requeue_n"] = n + 1
        keep.append(obj)
    return keep
