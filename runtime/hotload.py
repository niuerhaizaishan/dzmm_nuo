# -*- coding: utf-8 -*-
"""运行中热加载（P12-F）：改完页面**不用重启**就生效。

### 为什么要有这一层

P12-C/D/E 把"功能开关 / 参数 / 按群集合 / 人设文本与话术"搬进了 `config.json`，
但生效方式还是**重启容器**。而重启在生产上是件有代价的事：
账号通道要重新登录、Socket.IO 要重连、正在进行的骰子/投票/公演状态要重新加载
—— 于是"改一句文案"这种事，运维会本能地不想做，页面就白做了。

### 什么能热加载，什么不能（这条边界是**故意**划死的）

| 能（本模块负责） | 不能（必须重启，页面会如实标出来） |
|---|---|
| 功能开关 `features` | 群清单 `groups[].cid` / `enabled` |
| 参数 `tuning` + 按群集合 `scopes` | `mute` / `screen` / `push` |
| 人设文本与话术 `personas` | Bot 注册表 `bots`（绑定/token/开关） |
| 每个群用哪个人设 `groups[].persona` | 群别名之外的一切群级结构 |
| 提示词文件 `prompts/*.md` | |

为什么群/Bot 不能热改：**Socket.IO 订阅器在启动时用 `GROUPS` 建好了长连接**
（`sio_subscribe.SioSubscriber(GROUPS)`），主循环的 `group_meta` / `last_http_fallback`
也按启动时的群列表初始化。运行中换群列表 = 那条通道还在收旧群、新群没人收
—— "看起来加了群、其实收不到消息"，比直接要求重启糟得多。

### 安全三条（这是本模块的全部难度所在）

1. **先校验、后应用**：`config_store.reload()` 解析 + 校验全过才动运行期；
   文件坏了就**保持旧值**并在日志里说清楚（绝不半新半旧）。
2. **整体替换而不是逐项改**：`set_overrides()` / `set_content_overrides()`
   都是"算出新 dict → 一次性赋值"，读侧永远看到完整的旧状态或完整的新状态。
3. **失败不影响主循环**：`maybe_reload()` 把所有异常收进报告，绝不抛出
   （热加载出错不能让机器人停摆）。

### 触发方式

- **自动**：主循环每轮检查 `config.json` 的 mtime（一次 `stat`，很便宜），
  变了才做完整解析；`prompts/*.md` 的变化每 5 秒查一次（那一查要 stat 39 个文件）。
- **手动**：运维后台的「立即热加载」按钮。

### ⚠️ 运维后台和机器人是**两个进程**（同一个容器里）

`entrypoint.sh` 起了两个进程：`python -u bot.py`（真正跑机器人的）
和 `python -u admin/dashboard.py`（8090 上的面板）。P12-F 第一版忘了这件事，
把"生效状态"直接读模块内存 —— 面板进程里那份永远是"没配置、0 次"，
于是页面上既不显示"已生效"、按钮点了也没反应（**而机器人那边其实是好的**）。
所以跨进程用两个小文件（都在挂载的 `config/` 里，重启都在）：

| 文件 | 谁写 | 谁读 | 干什么 |
|---|---|---|---|
| `config/hotload_status.json` | 机器人进程（每次热加载后） | 面板 | "最后什么时候、改了什么、成不成、要不要重启" |
| `config/hotload_request` | 面板（点按钮） | 机器人进程（下一轮循环消费掉） | "请立刻重读一次" |

这也是这个仓库既有的做法（`check_health_state.json` / `renew_fail_state.json`
都是"进程写状态文件、面板读"）。按钮因此是**异步**的：点了之后约 1 秒生效，
页面上会如实写"已请求主进程立即热加载"。

### 开关

`DZMM_HOTLOAD=off` → `maybe_reload()` 秒退（沿用 P12-A/B 里 `DZMM_*=off` 的约定）。
"""
import json
import os
import time

__all__ = ["configure", "configured", "set_defaults", "maybe_reload", "reload_now",
           "apply_cfg", "status", "page_state", "structural_diff",
           "live_structural", "read_status_file", "request_reload",
           "status_file", "request_file", "HOT_KEYS", "RESTART_NOTE", "ENV_OFF"]

# 页面上的原话（运维看的就是这句）
RESTART_NOTE = ("群清单 / Bot 注册表 / 静默·筛查·推送 属于**结构性配置**："
                "Socket.IO 订阅通道与主循环的群列表在启动时就固定了，"
                "改这些必须重启容器（否则会出现「加了群但收不到消息」）。")

HOT_KEYS = ("features", "tuning", "scopes", "personas", "persona选择", "prompts")

# 由 bot.py 注入：给 config_store 用的默认值 / 取"运行期真正的结构性配置"
_DEFAULTS = None
_LIVE_FN = None
# 这个进程**是不是那个"应用配置"的进程**（= bot 主进程）。
# ⚠️ 与"有没有 defaults"是两件事：面板进程也算得出 defaults（从 bot.py 的 AST 读），
# 但它绝不能去应用配置 —— 运行期状态在另一个进程里。
_OWNER = False

# 最近一次热加载的状态（页面与自证都读它）
_LAST = {
    "at": 0.0,          # 上次**成功应用**的时间
    "mtime": 0.0,       # 上次看过的 config.json mtime（成功的或失败的都算"看过了"）
    "report": None,     # 上次的报告（apply_cfg 的返回）
    "error": "",        # 上次校验失败的原因（空 = 没问题）
    "count": 0,         # 启动以来成功热加载的次数
}
_PROMPT_CHECK_EVERY = 5.0
_last_prompt_check = [0.0]
_prompt_sig = [None]


def configured() -> bool:
    """这个进程是不是"应用配置的那个"（= bot 主进程）。"""
    return _OWNER


def set_defaults(defaults) -> None:
    """只登记 defaults（**不**接管应用）：面板进程用它来解析/校验配置文件。"""
    global _DEFAULTS
    if defaults is not None and _DEFAULTS is None:
        _DEFAULTS = defaults


def configure(defaults=None, live_fn=None) -> None:
    """由 `bot.py` 在运行时导入之后调用。

    `defaults`：`config_store` 需要的默认值（群/Bot/人设白名单）
    `live_fn`：返回**运行期**的结构性配置（群/静默/筛查/推送/Bot），用来判断
               "文件里的改动是不是需要重启才能生效"
    """
    global _DEFAULTS, _LIVE_FN, _OWNER
    if defaults is not None:
        _DEFAULTS = defaults
    if live_fn is not None:
        _LIVE_FN = live_fn
        _OWNER = True
    _LAST["mtime"] = _config_mtime()
    _LAST["at"] = _LAST["at"] or time.time()
    _prompt_sig[0] = _prompts_sig()
    # 立刻写一次状态文件：面板进程要靠它知道"现在这个进程是什么状态"
    # （否则重启后面板会一直显示上一次运行留下的旧状态）
    _write_status_file({"at": _LAST["at"], "count": _LAST["count"],
                        "source": "startup", "changes": {}, "counts": {},
                        "prompts": [], "structural": [], "error": ""})


# ---------------------------------------------------------------- 跨进程状态文件

def status_file():
    from runtime import config_store
    return config_store.config_path().parent / "hotload_status.json"


def request_file():
    from runtime import config_store
    return config_store.config_path().parent / "hotload_request"


def _write_status_file(rep) -> None:
    """把一次热加载的结果落盘（面板进程读它）。失败不影响功能。"""
    try:
        ch = rep.get("changes") or {}
        data = {
            "at": rep.get("at") or time.time(),
            "mtime": _config_mtime(),
            "count": _LAST["count"],
            "source": rep.get("source") or "",
            "changes": ch,
            "n_changes": sum(len(v or []) for v in ch.values()),
            "counts": rep.get("counts") or {},
            "prompts": rep.get("prompts") or [],
            "structural": rep.get("structural") or [],
            "error": rep.get("error") or "",
            "invalid": bool(rep.get("invalid")),
            "ok": bool(rep.get("ok", True)),
            "pid": os.getpid(),
            # 机器人**启动时定下来的**结构性配置（面板拿它比"文件里改了什么"）。
            # 没有它，面板就分不清"这个改动已被处理过（因为根本应用不了）"
            # 和"已经生效了" —— 结构性改动会永远不生效，必须一直显示"要重启"。
            "live_structural": _jsonable(live_structural()),
        }
        p = status_file()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(str(tmp), str(p))
    except Exception:                                          # noqa: BLE001
        pass


def _jsonable(v):
    """把元组转成列表（JSON 里没有元组；比的时候两边都按序列化后的样子比）。"""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def read_status_file() -> dict:
    """读状态文件（面板进程用）。读不到返回 {}。"""
    try:
        return json.loads(status_file().read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def request_reload() -> bool:
    """请求**机器人进程**立刻热加载一次（面板进程点按钮走这条）。

    只写一个小文件，主循环下一轮（≤1 秒）会消费掉它并强制重读 —— 面板没有
    权限直接改机器人进程的内存，所以只能这么"隔空喊话"，但这样也顺带保证了
    "谁来应用配置"始终只有一个进程。
    """
    try:
        p = request_file()
        p.write_text(str(time.time()), encoding="utf-8")
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _take_request() -> bool:
    """机器人进程：有没有人请求立刻热加载（有就消费掉）。"""
    p = request_file()
    try:
        if p.is_file():
            p.unlink()
            return True
    except Exception:                                          # noqa: BLE001
        pass
    return False


def _enabled() -> bool:
    v = (os.environ.get("DZMM_HOTLOAD") or "on").strip().lower()
    return v not in ("off", "0", "no", "false")


ENV_OFF = "DZMM_HOTLOAD=off"


# ---------------------------------------------------------------- 签名（什么时候该看一眼）

def _config_mtime() -> float:
    from runtime import config_store
    try:
        return config_store.config_path().stat().st_mtime
    except OSError:
        return 0.0


def _prompts_sig():
    """prompts/ 目录的指纹：文件个数 + 最新 mtime + 总大小。

    ⚠️ 不能只看目录本身的 mtime：**改一个已存在的文件不会改目录 mtime**
    （只有增删改名才会），于是"改文案"永远检测不到。
    """
    try:
        from runtime import prompts as rp
        d = rp.prompt_dir()
        if not d.is_dir():
            return (str(d), 0, 0.0, 0)
        n = 0
        newest = 0.0
        total = 0
        for p in d.glob("*.md"):
            try:
                st = p.stat()
            except OSError:
                continue
            n += 1
            newest = max(newest, st.st_mtime)
            total += st.st_size
        return (str(d), n, newest, total)
    except Exception:                                          # noqa: BLE001
        return None


# ---------------------------------------------------------------- 对比

def live_parts() -> dict:
    """运行期**当前**生效的四块（覆盖表）。"""
    from runtime import features as F
    from runtime import tuning as T
    import personas as P
    return {
        "features": dict(F.overrides()),
        "tuning": dict(T.overrides()),
        "scopes": {k: list(v) for k, v in (T.scopes() or {}).items()},
        "personas": P.content_overrides(),
        "persona_map": {k: v for k, v in (P.config_personas() or {}).items()},
    }


def live_structural() -> dict:
    """运行期的结构性配置：由 bot.py 注入的取数函数给（拿不到就返回空）。

    它代表"**启动时定下来的**那份"，所以和文件里一比就知道要不要重启。
    """
    if _LIVE_FN is None:
        return {}
    try:
        return dict(_LIVE_FN() or {})
    except Exception:                                          # noqa: BLE001
        return {}


def structural_diff(live=None, cfg=None) -> list:
    """文件里的结构性配置 vs 运行期的：返回人话差异（空 = 不需要重启）。"""
    from runtime import config_store
    if cfg is None:
        if _DEFAULTS is None:
            return []
        try:
            cfg = config_store.load(_DEFAULTS)
        except Exception:                                      # noqa: BLE001
            return ["配置文件读不了"]
    live = live_structural() if live is None else live
    if not live:
        return []
    out = []
    if tuple(live.get("groups") or ()) != tuple(cfg.groups):
        out.append("群清单/启用状态：{} 个 → {} 个（{}）".format(
            len(live.get("groups") or ()), len(cfg.groups),
            ", ".join(_short_cid(c) for c in cfg.groups[:4]) or "无"))
    for name, key in (("静默群", "mute"), ("筛查群", "screen"), ("推送群", "push")):
        a = sorted(live.get(key) or ())
        b = sorted(_flat(getattr(cfg, key)))
        if a != b:
            out.append("{}：{} 个 → {} 个".format(name, len(a), len(b)))
    lb = sorted((b[0], b[1]) for b in (live.get("bots") or ()))
    nb = sorted((b.key, b.cid) for b in cfg.bots if b.enabled)
    if lb != nb:
        out.append("Bot 注册表：{} 个 → {} 个（绑定/token 变化）".format(len(lb), len(nb)))
    return out


def _flat(v):
    return tuple(v or ())


def _short_cid(c):
    return str(c or "")[:8]


def parts_diff(old: dict, new: dict) -> dict:
    """四块**热**配置的差异（人话），用于报告与页面提示。"""
    out = {"features": [], "tuning": [], "scopes": [], "personas": [], "prompts": []}
    for k in sorted(set(old.get("features") or {}) | set(new.get("features") or {})):
        a = (old.get("features") or {}).get(k)
        b = (new.get("features") or {}).get(k)
        if a != b:
            out["features"].append("{} : {} → {}".format(
                k, _onoff(a), _onoff(b)))
    for k in sorted(set(old.get("tuning") or {}) | set(new.get("tuning") or {})):
        a = (old.get("tuning") or {}).get(k)
        b = (new.get("tuning") or {}).get(k)
        if a != b:
            out["tuning"].append("{} : {} → {}".format(k, a, b))
    for k in sorted(set(old.get("scopes") or {}) | set(new.get("scopes") or {})):
        a = sorted((old.get("scopes") or {}).get(k) or [])
        b = sorted((new.get("scopes") or {}).get(k) or [])
        if a != b:
            out["scopes"].append("{} : {} 个群 → {} 个群".format(k, len(a), len(b)))
    op, np_ = old.get("personas") or {}, new.get("personas") or {}
    for k in sorted(set(op) | set(np_)):
        a, b = op.get(k) or {}, np_.get(k) or {}
        for f in sorted(set(a) | set(b)):
            if f == "phrases":
                for pk in sorted(set(a.get("phrases") or {}) | set(b.get("phrases") or {})):
                    x = (a.get("phrases") or {}).get(pk)
                    y = (b.get("phrases") or {}).get(pk)
                    if x != y:
                        out["personas"].append(
                            "{}.话术.{} : {} → {}".format(k, pk, _short(x), _short(y)))
            elif a.get(f) != b.get(f):
                out["personas"].append("{}.{} : {} → {}".format(
                    k, f, _short(a.get(f)), _short(b.get(f))))
    om, nm = old.get("persona_map") or {}, new.get("persona_map") or {}
    for k in sorted(set(om) | set(nm)):
        if om.get(k) != nm.get(k):
            out["personas"].append("群 {} 的人设：{} → {}".format(
                _short_cid(k), om.get(k) or "（默认）", nm.get(k) or "（默认）"))
    return out


def _onoff(v):
    if v is None:
        return "跟随默认"
    return "开" if v else "关"


def _short(v, n=24):
    s = " ".join(str("" if v is None else v).split())
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------- 应用

def apply_cfg(cfg, source="auto", with_prompts=True) -> dict:
    """把一份**已校验过**的配置里的热配置应用到运行期，返回报告。

    失败率为零是设计目标：这里只做"算好新值 → 整体替换"，
    任何异常都收进报告的 `errors`（绝不抛出，热加载不能拖垮主循环）。
    """
    rep = {"at": time.time(), "source": source, "ok": True, "errors": [],
           "changes": {"features": [], "tuning": [], "scopes": [],
                       "personas": [], "prompts": []},
           "counts": {}, "prompts": []}
    try:
        old = live_parts()
    except Exception as e:                                      # noqa: BLE001
        old = {}
        rep["errors"].append("读运行期状态失败：{}".format(e))
    new = {
        "features": cfg.feature_map(),
        "tuning": cfg.tuning_map(),
        "scopes": {k: list(v) for k, v in (cfg.scopes or {}).items()},
        "personas": cfg.persona_texts(),
        "persona_map": {s.cid: s.persona for s in cfg.group_specs.values()
                        if s.persona},
    }
    rep["changes"].update(parts_diff(old, new))
    try:
        from runtime import features as F
        from runtime import tuning as T
        import personas as P
        F.set_overrides(new["features"])
        T.set_overrides(new["tuning"])
        T.set_scopes(new["scopes"])
        P.set_content_overrides(new["personas"])
        P.set_config_personas(new["persona_map"])
        rep["counts"] = {
            "features": len(new["features"]), "tuning": len(new["tuning"]),
            "scopes": len(new["scopes"]), "personas": len(new["personas"]),
            "persona_map": len(new["persona_map"]),
        }
    except Exception as e:                                      # noqa: BLE001
        rep["ok"] = False
        rep["errors"].append("应用失败：{}".format(e))
    if with_prompts:
        try:
            import brains
            rep["prompts"] = list(brains.refresh_prompts() or [])
            rep["changes"]["prompts"] = list(rep["prompts"])
        except Exception as e:                                  # noqa: BLE001
            rep["errors"].append("prompt 热加载失败：{}".format(e))
    _prompt_sig[0] = _prompts_sig()
    return rep


def _reload_config(source="auto") -> dict:
    """重新解析 config.json 并应用。**校验不过一个字都不应用。**"""
    from runtime import config_store
    try:
        cfg = config_store.reload(_DEFAULTS)
    except Exception as e:                                      # noqa: BLE001
        msg = "{}: {}".format(type(e).__name__, e)
        _LAST["error"] = msg[:300]
        _LAST["mtime"] = _config_mtime()      # 记住"这个坏版本已经看过"，别每轮刷屏
        rep = {"at": time.time(), "source": source, "ok": False, "errors": [msg],
               "changes": {}, "counts": {}, "prompts": [], "invalid": True,
               "error": msg[:300]}
        _write_status_file(rep)
        return rep
    rep = apply_cfg(cfg, source=source)
    rep["structural"] = structural_diff(cfg=cfg)
    _LAST.update({"at": rep["at"], "mtime": _config_mtime(),
                  "report": rep, "error": "", "count": _LAST["count"] + 1})
    _write_status_file(rep)
    return rep


def maybe_reload() -> dict:
    """主循环每轮调用：该重载就重载，否则立刻返回 None。

    便宜的那一段（一次 `stat`）每轮都做；贵的（完整解析 + 39 个 prompt 文件）
    只在 mtime 变了才做（或者面板点了「立即热加载」）。
    """
    if not _enabled() or not configured():
        return None
    if _take_request():
        return _reload_config("manual")
    m = _config_mtime()
    if m and abs(m - (_LAST["mtime"] or 0.0)) > 1e-6:
        return _reload_config("auto")
    now = time.time()
    if now - _last_prompt_check[0] >= _PROMPT_CHECK_EVERY:
        _last_prompt_check[0] = now
        sig = _prompts_sig()
        if sig is not None and _prompt_sig[0] is not None and sig != _prompt_sig[0]:
            try:
                import brains
                changed = list(brains.refresh_prompts() or [])
            except Exception as e:                              # noqa: BLE001
                changed = []
                _LAST["error"] = "prompt 热加载失败：{}".format(e)
            _prompt_sig[0] = sig
            rep = {"at": now, "source": "prompts", "ok": True, "errors": [],
                   "changes": {"prompts": changed}, "counts": {},
                   "prompts": changed}
            # prompt 热加载也算一次热加载：不然后台看到的"次数"会与
            # "最近一次生效时间"对不上（改文案明明生效了，次数却不动）。
            _LAST["count"] += 1
            _LAST["at"] = now
            _LAST["report"] = rep
            _write_status_file(rep)
            return rep
        _prompt_sig[0] = sig
    return None


def reload_now() -> dict:
    """手动触发（页面按钮）：忽略 mtime，完整重放一次。"""
    if not configured():
        return {"at": time.time(), "source": "manual", "ok": False,
                "errors": ["热加载未启用（这个进程不是 bot 主进程）"],
                "changes": {}, "counts": {}, "prompts": []}
    return _reload_config("manual")


# ---------------------------------------------------------------- 状态（页面/自证）

def status() -> dict:
    rep = _LAST.get("report") or {}
    return {
        "enabled": _enabled(),
        "configured": configured(),
        "at": _LAST["at"],
        "at_text": (time.strftime("%H:%M:%S", time.localtime(_LAST["at"]))
                    if _LAST["at"] else ""),
        "count": _LAST["count"],
        "error": _LAST["error"],
        "last": rep,
    }


def page_state(defaults=None) -> dict:
    """给运维后台的三个页面：现在到底是"已生效 / 下一次循环生效 / 要重启 / 文件坏了"。

    **两个进程都能用**：
      - 机器人进程（`configured()`）→ 直接读内存里的运行期状态；
      - 面板进程 → 读 `config/hotload_status.json`（机器人写的），
        文件不存在（从没热加载过 / 老版本留下的）就返回 None，
        页面退回 P12-D 的旧提示（"要重启"）——**宁可保守，也不能谎报"已生效"**。

    `defaults`：面板进程要用的 config_store 默认值（它自己有，从 bot.py 的 AST 读）。
    """
    from runtime import config_store
    d = defaults if defaults is not None else _DEFAULTS
    live = configured()
    if live:
        base = {"at_text": status()["at_text"], "count": _LAST["count"],
                "last_changes": ((_LAST.get("report") or {}).get("changes") or {}),
                "recorded_structural": ((_LAST.get("report") or {})
                                        .get("structural") or []),
                "recorded_mtime": _LAST["mtime"],
                "recorded_invalid": bool(_LAST.get("error")),
                "source": ((_LAST.get("report") or {}).get("source") or ""),
                "recorded_error": _LAST.get("error") or ""}
    else:
        data = read_status_file()
        if not data:
            return None
        at = float(data.get("at") or 0)
        base = {"at_text": (time.strftime("%H:%M:%S", time.localtime(at))
                            if at else ""),
                "count": data.get("count") or 0,
                "last_changes": data.get("changes") or {},
                "recorded_structural": data.get("structural") or [],
                "recorded_mtime": data.get("mtime") or 0.0,
                "recorded_invalid": bool(data.get("invalid")),
                "recorded_error": data.get("error") or "",
                "live_structural": data.get("live_structural") or {},
                "source": data.get("source") or ""}
    # 文件现在到底合不合法：本进程自己解析一遍（面板有独立的 config_store）
    cfg, cfg_ok, err = None, False, ""
    if d is not None:
        try:
            cfg = config_store.load(d, force=True)
            cfg_ok = True
        except Exception as e:                                  # noqa: BLE001
            err = "{}: {}".format(type(e).__name__, e)
    else:
        cfg_ok = not base.get("recorded_invalid")
        err = base.get("recorded_error") or ""
    if not cfg_ok:
        return {"enabled": _enabled(), "at_text": base.get("at_text") or "",
                "count": base.get("count") or 0, "pending": False,
                "structural": [], "error": err or "配置读不了", "invalid": True,
                "out_of_process": not live, "last_changes": {}}
    m = _config_mtime()
    pending = bool(m and abs(m - (base.get("recorded_mtime") or 0.0)) > 1e-6)
    # 结构性提示：**只要文件与运行期不一致就一直显示**（与 pending 无关）。
    # 面板进程没有运行期内存，用机器人写进状态文件的那份快照来比。
    if live:
        struct = structural_diff(cfg=cfg)
    else:
        snap = base.get("live_structural") or {}
        struct = (structural_diff(live=snap, cfg=cfg) if snap
                  else (base.get("recorded_structural") or []))
    return {
        "enabled": _enabled(),
        "at_text": base.get("at_text") or "",
        "count": base.get("count") or 0,
        "pending": pending,
        "invalid": False,
        "out_of_process": not live,
        "source": base.get("source") or "",
        "structural": struct,
        # 文件现在是好的 ⇒ 上一次的校验错误已经过期，不要再吓人
        "error": "",
        "last_changes": base.get("last_changes") or {},
    }
