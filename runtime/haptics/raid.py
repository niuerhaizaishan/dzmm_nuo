"""月之瞳 · 剧情自适应触觉（把 DM 的叙述变成设备震动）。

两条路，共用同一条"解析 → 触发"链：

  a) 提示词档位（默认，不花模型调用）
     DM 每回合最多输出一行 `【震动】角色 强度% 时长ms`，这里解析后触发。
  b) 模型判断（`mode: "b"`）
     小小糯读这一回合的叙述，自己给强度和时长；模型不可用 / 输出不合规
     **自动退回 a**，所以永远不会"该震的时候没反应"。

安全边界一律在这里，跟提示词无关：
  * 单条强度 ≤ `max_level`（默认 0.7）、时长 ≤ 3000ms（`cues.MAX_MS`）
  * 每回合最多 `per_turn`（默认 2）条
  * 同一角色 `cooldown_s`（默认 10 秒）内不重复触发
  * 房主没开「允许小小糯控制」/ 没开分享 → 直接不发，也不报错

触发是**排队 + 后台协程**：主循环把指令塞进队列就继续跑，
不会因为"正在震 1.2 秒"把整群这一轮堵住。
"""
import asyncio
import json
import os
import time

from runtime.obs import log
from runtime.haptics import cues as _cues
from runtime.haptics import share_client as _share

DEFAULT_FILE = "/app/data/haptics_raid.json"
CONFIG_TTL = 2.0          # 配置缓存：热改文件 2 秒内生效，不用重启

_CFG = {"at": 0.0, "data": None}
_LAST_ROLE = {}           # 角色 → 上次触发的 monotonic
_LAST_ANY = 0.0
_QUEUE = None
_WORKER = None

# (b) 模型判断用的提示词。要求它**只**输出两个数字，省一套解析。
HAPTIC_CURVE_PROMPT = (
    "你在替一台震动设备做强度判断。读下面这段剧情叙述，判断此刻该给出的触觉。\n"
    "只输出一行，两个整数，用空格隔开：强度 时长\n"
    "强度 0~70（百分比，越大越强），时长 200~3000（毫秒）。\n"
    "剧情越紧张/越接近高潮给得越强越长；平淡叙述给 20~30、300~500。\n"
    "不确定就输出 30 500。不要输出任何解释、标点、单位或多余文字。\n\n"
    "剧情：\n"
)


def config_path():
    return os.environ.get("DZMM_HAPTICS_RAID_FILE", DEFAULT_FILE)


def load(force=False):
    """读配置文件。坏文件/缺文件 = 关闭，绝不抛。"""
    now = time.monotonic()
    if not force and _CFG["data"] is not None and now - _CFG["at"] < CONFIG_TTL:
        return _CFG["data"]
    data = {"enabled": False, "cids": [], "mode": "a", "per_turn": 2,
            "cooldown_s": 10, "max_level": None}
    try:
        with open(config_path(), encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            data.update(raw)
    except Exception:                                          # noqa: BLE001
        data["enabled"] = False
    data["enabled"] = bool(data.get("enabled")) and bool(data.get("cids"))
    data["mode"] = str(data.get("mode") or "a").lower()
    if data["mode"] not in ("a", "b"):
        data["mode"] = "a"
    data["cids"] = [str(c) for c in (data.get("cids") or []) if str(c or "").strip()]
    _CFG["at"] = now
    _CFG["data"] = data
    return data


def is_raid(cid):
    conf = load()
    return bool(conf["enabled"]) and str(cid) in conf["cids"]


def reset():
    """给测试用：清缓存/冷却/队列。"""
    global _LAST_ANY, _QUEUE, _WORKER
    _CFG["at"] = 0.0
    _CFG["data"] = None
    _LAST_ROLE.clear()
    _LAST_ANY = 0.0
    _QUEUE = None
    _WORKER = None


def _text_of(message):
    content = (message or {}).get("content") or {}
    return str(content.get("text") or "").strip()


def _judge(text):
    """(b) 让模型给一条。任何异常/不合规都返回 None（调用方退回 a）。"""
    try:
        import brains
        raw, _finish = brains._call_llm_raw(HAPTIC_CURVE_PROMPT, text[-1500:],
                                            max_tokens=24, timeout=12)
    except Exception:                                          # noqa: BLE001
        return None
    return _cues.parse_plain(raw)


def collect(texts, conf):
    """把这一批消息变成待发的 Cue 列表（不含冷却判断，方便离线测试）。"""
    limit = max(0, int(conf.get("per_turn") or 2))
    out = []
    for text in texts:
        if not text:
            continue
        found = []
        if conf.get("mode") == "b":
            judged = _judge(text)
            if judged is not None:
                found = [judged]
        if not found:
            found = _cues.parse_cues(text)      # a：提示词档位（也是 b 的兜底）
        for cue in found:
            if len(out) >= limit:
                return out
            out.append(cue)
    return out


def _allowed(cue, conf):
    """冷却检查：同角色 cooldown_s 内不重复，任意两条至少隔 3 秒。"""
    global _LAST_ANY
    now = time.monotonic()
    gap = float(conf.get("cooldown_s") or 10)
    if _LAST_ANY and now - _LAST_ANY < 3.0:
        return False
    last = _LAST_ROLE.get(cue.role)
    if last and now - last < gap:
        return False
    _LAST_ANY = now
    _LAST_ROLE[cue.role] = now
    return True


async def _worker_loop(queue):
    while True:
        cue = await queue.get()
        try:
            result = await _share.client().command(cue.level, cue.duration_ms)
            if result.get("ok"):
                log("📳 剧情触觉 " + _cues.describe(cue))
            else:
                log("📳 剧情触觉没发出去：" + str(result.get("detail") or "")[:40])
            await asyncio.sleep((cue.duration_ms + 250) / 1000.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                               # noqa: BLE001
            log("📳 剧情触觉异常：" + type(exc).__name__)
        finally:
            queue.task_done()


def _ensure_worker():
    global _QUEUE, _WORKER
    if _QUEUE is None:
        _QUEUE = asyncio.Queue()
    if _WORKER is None or _WORKER.done():
        _WORKER = asyncio.ensure_future(_worker_loop(_QUEUE))
    return _QUEUE


async def watch(cid, pending, *, dry=False, is_self=None):
    """主循环在静默群里调它：解析这一批新消息里的触觉行并排队下发。"""
    try:
        conf = load()
        if not conf["enabled"] or str(cid) not in conf["cids"]:
            return 0
        if not _share.enabled():
            return 0
        texts = []
        for message in pending or []:
            if is_self is not None and is_self(message.get("sent_by", "")):
                continue
            text = _text_of(message)
            if text:
                texts.append(text)
        if not texts:
            return 0
        picked = collect(texts, conf)
        if not picked:
            return 0
        ready = [cue for cue in picked if _allowed(cue, conf)]
        if dry:
            for cue in ready or picked:
                log("[dry] 📳 剧情触觉 " + _cues.describe(cue))
            return len(ready or picked)
        if not ready:
            return 0
        queue = _ensure_worker()
        for cue in ready:
            queue.put_nowait(cue)
        return len(ready)
    except Exception as exc:                                   # noqa: BLE001
        log("📳 剧情触觉解析异常：" + type(exc).__name__)
        return 0
