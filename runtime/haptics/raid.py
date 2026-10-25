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
import re
import time

from runtime.obs import log
from runtime.haptics import cues as _cues
from runtime.haptics import share_client as _share

DEFAULT_FILE = "/app/data/haptics_raid.json"
CONFIG_TTL = 2.0          # 配置缓存：热改文件 2 秒内生效，不用重启
MIN_NARRATION = 80        # (b) 少于这么多字的段落不调模型（状态栏/短回复不值得震）

_CFG = {"at": 0.0, "data": None}
_LAST_ROLE = {}           # 角色 → 上次触发的 monotonic
_LAST_ANY = 0.0
_LAST_LEVEL = None        # 上一次判出来的强度，作为"这回合该更紧还是更松"的锚点
_QUEUE = None
_WORKER = None

# (b) 模型判断用的提示词。
# 为什么只要一个数字：早先让模型直接给"强度 时长"，实测**区分度很弱**
# （真实团本 8 段叙述全挤在 30~55%，拿到宝石那一刻反而是 40%），
# 而且同样的段落两次跑出不同的数。改成"只判紧张度，映射由代码做"：
# 模型只负责"这段有多激烈"，强度/时长走固定曲线 → 强弱一定单调、可复现。
HAPTIC_CURVE_PROMPT = (
    "你在给一台震动设备定强度。读这段剧情，判断它此刻的紧张/情色强度，输出 0~100 的一个整数。\n"
    "0-20 平静铺垫、环境描写；21-40 有动作但没出事；"
    "41-60 对抗、装置生效、危险逼近；61-80 被挂住/失守/拿到关键物；"
    "81-100 高潮或彻底失守。\n"
    "上一回合给的强度：{prev}\n"
    "玩家这回合的动作：{action}\n"
    "**要跟上一回合比**：更紧张就明显更高，缓和/收尾就明显更低，差不多才给相近的数。"
    "不要每次都给四五十。\n"
    "只输出这个整数，不要单位、标点、解释或任何多余文字。\n\n"
    "剧情叙述：\n"
)
DRAMA_LEVEL = (0.20, None)     # 紧张度 0→1 映射到的强度区间（上限取 cues.MAX_LEVEL）
DRAMA_MS = (400, 1500)         # 紧张度 0→1 映射到的时长区间
DRAMA_RE = re.compile(r"\d+")


def clamp_drama(score):
    try:
        score = int(round(float(score)))
    except (TypeError, ValueError):
        return None
    return max(0, min(score, 100))


def from_drama(score, role="模型"):
    """0~100 的紧张度 → 一条 Cue（纯函数，可测）。"""
    score = clamp_drama(score)
    if score is None:
        return None
    ratio = score / 100.0
    low, high = DRAMA_LEVEL
    high = _cues.MAX_LEVEL if high is None else high
    level = low + (high - low) * ratio
    lo_ms, hi_ms = DRAMA_MS
    duration = int(round(lo_ms + (hi_ms - lo_ms) * ratio))
    return _cues.Cue(role, round(level, 3), duration)


def _judge(text, context=None):
    """(b) 让模型给一个紧张度，再映射成触觉。任何异常/不合规返回 None（退回 a）。

    context: {"prev": 上一回合触发的强度 0~1，可选, "action": 玩家这回合的动作，可选}
    """
    context = context or {}
    prev = context.get("prev")
    prompt = HAPTIC_CURVE_PROMPT.format(
        prev=("{:.0f}%".format(prev * 100) if prev else "（还没有，这是开局）"),
        action=((context.get("action") or "").strip()[:60] or "（没说，只有叙述）"))
    try:
        import brains
        raw, _finish = brains._call_llm_raw(prompt, text[-1500:],
                                            max_tokens=12, timeout=12)
    except Exception:                                          # noqa: BLE001
        return None
    numbers = DRAMA_RE.findall(raw or "")
    if len(numbers) >= 2:
        # 兼容模型自己写成"强度 时长"两块的旧格式
        return _cues.parse_plain(raw)
    if numbers:
        return from_drama(numbers[0])
    return None


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
    global _LAST_ANY, _LAST_LEVEL, _QUEUE, _WORKER
    _CFG["at"] = 0.0
    _CFG["data"] = None
    _LAST_ROLE.clear()
    _LAST_ANY = 0.0
    _LAST_LEVEL = None
    _QUEUE = None
    _WORKER = None


def _text_of(message):
    content = (message or {}).get("content") or {}
    return str(content.get("text") or "").strip()


def collect(texts, conf, prev_level=None):
    """把这一批消息变成待发的 Cue 列表（不含冷却判断，方便离线测试）。

    批内按顺序推进两个上下文：`action`＝最近一句玩家动作（短消息），
    `prev`＝上一次判出来的强度。这样模型能判断"比上一回合更紧还是更松"。
    """
    limit = max(0, int(conf.get("per_turn") or 2))
    out = []
    action = ""
    prev = prev_level
    for text in texts:
        if not text:
            continue
        if len(text) < MIN_NARRATION:
            action = text.strip()
        found = _collect_with(text, conf, context={"prev": prev, "action": action})
        for cue in found:
            if len(out) >= limit:
                return out
            out.append(cue)
            prev = cue.level
    return out


MIN_GAP = 3.0             # 两次「触发批次」之间至少隔这么久（同批次内多条不算）


def _filter_cues(picked, conf, state, now):
    """把一批候选按冷却规则筛一遍。**线上和离线仿真共用这一份规则。**

    state: {"last_any": 上次触发时间, "last_role": {角色: 上次触发时间}}

    * 「两次触发之间至少 3 秒」按**批次**算：同一回合解析出的两条是排着队发的
      （发一条等 duration+250ms），本来就隔着时间，不能按条再卡一次 3 秒
      ——那样同一个回合永远只能发一条。
    * 同一角色 `cooldown_s` 内不重复（跨回合）。
    """
    last_any = state.get("last_any") or 0.0
    if last_any and now - last_any < MIN_GAP:
        return []
    gap = float(conf.get("cooldown_s") or 10)
    last_role = state.setdefault("last_role", {})
    ready = []
    for cue in picked:
        last = last_role.get(cue.role)
        if last and now - last < gap:
            continue
        ready.append(cue)
    for cue in ready:
        last_role[cue.role] = now
    if ready:
        state["last_any"] = now
    return ready


def _allowed_cues(picked, conf):
    """线上版本：用真实的 monotonic 时钟 + 模块级状态。"""
    global _LAST_ANY
    state = {"last_any": _LAST_ANY, "last_role": _LAST_ROLE}
    ready = _filter_cues(picked, conf, state, time.monotonic())
    _LAST_ANY = state["last_any"]
    return ready


def plan(items, conf=None, judge=None):
    """离线仿真：把一段**历史消息**排成"当时本该震什么"，完全不动设备。

    items: [{"at": 秒, "text": "...", "by": 可选}]，按时间升序
    返回 [{"at", "head", "picked", "cues", "dropped"}]

    用的是和线上同一套 `collect()` + `_filter_cues()`，所以"仿真会震"≈"线上会震"。
    连"上一回合强度"和"玩家这回合的动作"也一起喂给模型（与线上一致），
    否则模型每段都判 45 上下，仿真就不像真的。
    judge 可注入假模型（测试用）。
    """
    conf = dict(conf or load())
    rows = []
    state = {"last_any": 0.0, "last_role": {}}
    ordered = sorted(items or [], key=lambda item: float(item.get("at") or 0))
    action = ""
    prev_level = None
    for item in ordered:
        at = float(item.get("at") or 0)
        text = item.get("text") or ""
        is_short = len(text) < MIN_NARRATION
        if is_short:
            action = text.strip()          # 玩家这一手，给下一次叙述当上下文
        picked = _collect_with(text, conf, judge,
                               {"prev": prev_level, "action": action})
        ready = _filter_cues(picked, conf, state, at)
        if ready:
            prev_level = ready[-1].level
        rows.append({
            "at": at,
            "head": (text.strip().replace("\n", " ")[:60]),
            "picked": [cue.as_dict() for cue in picked],
            "cues": [cue.as_dict() for cue in ready],
            "dropped": [cue.as_dict() for cue in picked if cue not in ready],
        })
    return rows


def _collect_with(text, conf, judge=None, context=None):
    """collect() 的单条版本，支持注入假模型和上下文。"""
    limit = max(0, int(conf.get("per_turn") or 2))
    found = []
    if conf.get("mode") == "b" and len(text) >= MIN_NARRATION:
        judged = (judge or _judge)(text, context) if judge else _judge(text, context)
        if judged is not None:
            found = [judged]
    if not found:
        found = _cues.parse_cues(text)
    return found[:limit]


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
    global _LAST_LEVEL
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
        picked = collect(texts, conf, prev_level=_LAST_LEVEL)
        if not picked:
            return 0
        ready = _allowed_cues(picked, conf)
        if dry:
            for cue in ready or picked:
                log("[dry] 📳 剧情触觉 " + _cues.describe(cue))
            return len(ready or picked)
        if not ready:
            return 0
        queue = _ensure_worker()
        for cue in ready:
            queue.put_nowait(cue)
        _LAST_LEVEL = ready[-1].level      # 下一回合"比上一回合更紧还是更松"的锚点
        return len(ready)
    except Exception as exc:                                   # noqa: BLE001
        log("📳 剧情触觉解析异常：" + type(exc).__name__)
        return 0
