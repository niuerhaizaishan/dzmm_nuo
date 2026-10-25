"""团本监控热加载：登记一个群 → 订阅它的消息并跟剧情，**不用重启容器**。

登记入口（私聊或群里都行）：
    /监控 <团本群链接>        ← 直接贴链接
    /监控 <群 ID>
    引用那条链接再发 /监控     ← 从被引用的原文里抠 ID

登记写进 `haptics_raid.json` 的 `cids`，这个监督循环每 5 秒看一次：
  * 新 cid → 加进订阅名单 + `message:join-room`（**socket 推送，不轮询**）+ 起一个轻量循环
  * cid 被移出 → 停循环、退出订阅

轻量循环只做两件事（团本群是静默群，本来就不发言）：
  * DM 的叙述 → `raid.watch()`（剧情跟随）
  * 群里的 /震 /预设 /停 → 静默执行，回执走私聊
"""
import asyncio

from runtime.obs import log
from runtime.haptics import bot_commands as _bot_commands
from runtime.haptics import raid

_TASKS = {}      # cid -> asyncio.Task
_JOINED = set()  # 已经订阅过的 cid


async def supervise(subscriber, sender, *, is_self=None, interval=5.0, known_groups=None):
    """每 5 秒对一次账：配置文件里有的 cid 都要有一个循环在跑。

    `known_groups` = config 里的群（它们已经有主循环在 drain，绝不能重复接管，
    否则两边抢同一个 buffer，消息会随机丢一半）。
    """
    known = set(known_groups or [])
    await asyncio.sleep(3)                     # 等 socket 先连上
    while True:
        try:
            conf = raid.load(force=True)
            want = [cid for cid in (conf.get("cids") or []) if conf.get("enabled")]
            for cid in want:
                if cid in known:
                    continue                   # 主循环已经在管，别插手
                if cid not in _TASKS or _TASKS[cid].done():
                    _TASKS[cid] = asyncio.ensure_future(
                        _loop(cid, subscriber, sender, is_self))
                    log("📡 团本监控启动 " + cid[:8])
            for cid in list(_TASKS):
                if cid not in want:
                    _TASKS.pop(cid).cancel()
                    log("📡 团本监控停止 " + cid[:8])
        except Exception as exc:                               # noqa: BLE001
            log("📡 监控监督异常：" + type(exc).__name__)
        await asyncio.sleep(interval)


async def _subscribe(subscriber, cid):
    """把这个群加进订阅名单并加入 socket 房间（房间动作/推送都要）。"""
    if cid in _JOINED:
        return
    try:
        if cid not in subscriber.groups:
            subscriber.groups.append(cid)
        joined = await subscriber.join_room(cid)
        _JOINED.add(cid)
        log("[sio] join {} -> {}".format(cid[:8], "ok" if joined else "延迟重试"))
    except Exception as exc:                                   # noqa: BLE001
        log("[sio] join {} 失败: {}".format(cid[:8], str(exc)[:50]))


async def _loop(cid, subscriber, sender, is_self):
    await _subscribe(subscriber, cid)
    while True:
        try:
            msgs = subscriber.drain(cid)
            if msgs:
                await raid.watch(cid, msgs, is_self=is_self)
                await _run_commands(cid, msgs, sender, is_self)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                               # noqa: BLE001
            log("📡 团本循环异常 " + cid[:8] + "：" + type(exc).__name__)
        await asyncio.sleep(0.8)


async def _run_commands(cid, msgs, sender, is_self):
    for msg in msgs:
        text = ((msg.get("content") or {}).get("text") or "").strip()
        uid = msg.get("sent_by", "")
        if not text or _bot_commands.command(text) is None:
            continue
        if is_self is not None and is_self(uid):
            continue
        ref = ((msg.get("content") or {}).get("reference") or {})
        quote = ((ref.get("content") or {}).get("text") or "")
        try:
            reply = await _bot_commands.handle(text, uid=uid, owner=False, quote=quote)
            log("📳 [{}] 监控群指令 {} → {}".format(
                cid[:8], text[:16], (reply or "").replace("\n", " ")[:50]))
            if reply:
                from runtime.dice import _dice_send_private
                await _dice_send_private(sender, uid, "🤖 " + reply)
        except Exception as exc:                               # noqa: BLE001
            log("⚠️ [{}] 监控群指令异常：{}".format(cid[:8], type(exc).__name__))
