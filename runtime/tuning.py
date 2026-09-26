"""可调参数（P12-B）：把散落的常量收成**一处读口** + 按群覆盖 + 硬边界。

### 为什么要有这个模块

盘点的结论：热值、冷却、概率、阈值、时长散在 `bot.py` / `personas.py` /
`runtime/*` 里共 **40 多个常量**，运营想调任何一个都得改代码。要让页面能调，
先得有一个**唯一的读口**——否则"页面上改了、某个调用点还是读旧常量"这种事
一定会发生（P12-0 刚修过同类的"看起来能配、实际不生效"）。

### 三层取值（与 `runtime/features.py` 同一套思路）

```
每群覆盖  >  全局覆盖  >  代码默认值
```

代码默认值就是 `features.py` 里各功能挂的 `Param.default`（**它等于今天的常量**），
所以"没有任何覆盖"时行为逐字节不变 —— 这是 P12-B 的验收标准。
参数定义（类型/单位/上下界）也只写一份，页面与校验共用。

### 硬边界不是装饰

`lo`/`hi` 在 `set_overrides()` 时就校验（越界直接抛），因为这些值真的会闯祸：
冷却设 0 → 刷屏 → 撞风控；`perf.novel_max_chunks` 调大 → 堵死主循环
（2026-09-14 实测堵了 37 分钟）。`DZMM_TUNING=off` 则是"页面配错了能秒退"的开关。

### 按群集合（scope）

`TREND_CIDS`/`RECAP_CIDS` 这两处**硬编码的按群集合**也收在这里：
默认值仍是代码里的那个集合（零行为变化），但可以被配置覆盖
（否则"运势榜只给认证群"这种事在页面上改不了）。
`ROOM_CIDS` 不在这里 —— 房间还牵着人设/屋主/警告阈值，整体挪进 P12-E 一起做，
半配置化的房间（有群没人设）比不配置更危险。
"""
import os

from runtime.features import FEATURES
from runtime.ctx import PRIMARY_CID

__all__ = [
    "TuningError", "PARAMS", "SCOPE_DEFAULTS", "get", "set_overrides",
    "overrides", "set_scopes", "scopes", "in_scope", "scope_of", "why",
    "summary", "registry", "all_defaults",
]


class TuningError(ValueError):
    """参数不合法（未知 key / 越界 / 类型不对）。**要让它冒出来**。"""


# key -> (Param, 所属功能 key)
PARAMS = {}
for _f in FEATURES:
    for _p in _f.params:
        if _p.key in PARAMS:
            raise TuningError("参数 key 重复：{}".format(_p.key))
        PARAMS[_p.key] = (_p, _f.key)
del _f, _p


# 按群集合的**代码默认值**（= 今天硬编码的那两个集合）。
# 想改默认就在这里改，但注意：改它等于改"今天的行为"。
SCOPE_DEFAULTS = {
    # 运势趋势/群运势榜：只在常驻群开放（那边才有 fortune_history）
    "trend": (PRIMARY_CID,),
    # 缺勤回顾：同上，且代码里 # 只在认证妹子群开放
    "recap": (PRIMARY_CID,),
}

# key / "cid|key" -> 覆盖值
_OVERRIDES = {}
# scope 名 -> (cid, ...)
_SCOPES = {}


def _env_off() -> bool:
    """`DZMM_TUNING=off` = 忽略所有覆盖，全用代码默认值（秒退开关）。"""
    return (os.environ.get("DZMM_TUNING") or "on").strip().lower() in \
        ("off", "0", "no", "false")


def _param(key: str):
    got = PARAMS.get(key)
    if got is None:
        raise TuningError(
            "未知参数 key={!r}。参数必须在 runtime/features.py 的 Param 里登记，"
            "否则页面上的输入框和实际读的值会各说各话".format(key))
    return got


def _coerce(p, v, where: str):
    """按默认值的类型转换 + 硬边界校验。"""
    default = p.default
    try:
        if isinstance(default, bool):
            out = bool(v)
        elif isinstance(default, int):
            out = int(v)
        elif isinstance(default, float):
            out = float(v)
        else:
            out = str(v)
    except Exception:
        raise TuningError("{} 的值 {!r} 与类型 {} 不符".format(
            where, v, type(default).__name__))
    if isinstance(out, (int, float)) and not isinstance(out, bool):
        if p.lo is not None and out < p.lo:
            raise TuningError("{} = {} 小于下界 {}".format(where, out, p.lo))
        if p.hi is not None and out > p.hi:
            raise TuningError("{} = {} 大于上界 {}".format(where, out, p.hi))
    return out


def get(key: str, cid: str = None):
    """取参数值：每群覆盖 > 全局覆盖 > 代码默认值。"""
    p, _feat = _param(key)
    if _env_off():
        return p.default
    if cid is not None:
        v = _OVERRIDES.get("{}|{}".format(cid, key))
        if v is not None:
            return _coerce(p, v, "{}[{}]".format(key, (cid or "")[:8]))
    v = _OVERRIDES.get(key)
    return p.default if v is None else _coerce(p, v, key)


def set_overrides(mapping) -> None:
    """注入参数覆盖：{"heat.climax_threshold": 120, "cid|dice.timeout": 60}。

    **越界/未知 key 直接抛** —— 调用方（页面的保存路径）要的就是这个：
    宁可在保存时报错，也不要让一个"冷却=0"静悄悄上线。
    """
    global _OVERRIDES
    out = {}
    for k, v in (mapping or {}).items():
        if v is None:
            continue
        parts = str(k).split("|")
        key = parts[-1]
        cid = parts[0] if len(parts) == 2 else None
        p, _feat = _param(key)
        where = key if cid is None else "{}[{}]".format(key, cid[:8])
        out[str(k)] = _coerce(p, v, where)
    _OVERRIDES = out


def overrides() -> dict:
    return dict(_OVERRIDES)


def why(key: str, cid: str = None) -> str:
    """这个值现在是从哪来的（自证/页面用）。"""
    if _env_off():
        return "env(DZMM_TUNING=off)"
    if cid is not None and _OVERRIDES.get("{}|{}".format(cid, key)) is not None:
        return "config(本群)"
    if _OVERRIDES.get(key) is not None:
        return "config(全局)"
    return "default"


# ---------------------------------------------------------------- 按群集合

def set_scopes(mapping) -> None:
    """注入按群集合覆盖：{"trend": ["cid1", "cid2"], "recap": []}。

    只接受**已登记的** scope 名：拼错就是"这个开关没生效"，
    而那正是我们要防的事。空列表是合法的（= 谁都不给）。
    """
    global _SCOPES
    out = {}
    for k, v in (mapping or {}).items():
        name = str(k)
        if name not in SCOPE_DEFAULTS:
            raise TuningError("未知的按群集合名 {!r}（可用：{}）".format(
                name, ", ".join(sorted(SCOPE_DEFAULTS))))
        if v is None:
            continue
        if isinstance(v, str):
            v = [x for x in v.split(",") if x.strip()]
        out[name] = tuple(str(x).strip() for x in v if str(x).strip())
    _SCOPES = out


def scopes() -> dict:
    return {k: list(v) for k, v in _SCOPES.items()}


def scope_of(name: str) -> tuple:
    """当前生效的集合（配置优先，否则代码默认）。"""
    if name not in SCOPE_DEFAULTS:
        raise TuningError("未知的按群集合名 {!r}".format(name))
    if _env_off():
        return SCOPE_DEFAULTS[name]
    return _SCOPES.get(name, SCOPE_DEFAULTS[name])


def in_scope(name: str, cid: str) -> bool:
    return (cid or "") in scope_of(name)


# ---------------------------------------------------------------- 自证/面板

def all_defaults() -> dict:
    return {k: p.default for k, (p, _f) in PARAMS.items()}


def summary() -> dict:
    return {"params": len(PARAMS),
            "overridden": len(_OVERRIDES),
            "scopes": {k: len(scope_of(k)) for k in SCOPE_DEFAULTS},
            "scope_overridden": sorted(_SCOPES),
            "env_off": _env_off()}


def registry() -> list:
    """给页面/测试用的完整参数清单（纯数据）。"""
    out = []
    for key, (p, feat) in PARAMS.items():
        out.append({"key": key, "label": p.label, "default": p.default,
                    "unit": p.unit, "lo": p.lo, "hi": p.hi, "tier": p.tier,
                    "note": p.note, "feature": feat,
                    "value": get(key), "source": why(key)})
    return out
