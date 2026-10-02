"""配置生命周期（P3-17 从 bot.py 拆出）。

四件事，都发生在"配置改了"与"运行期生效"的边界上：
  · run_apply_config_overrides —— 启动时把热配置（功能/参数/集合/人设）注入
    运行期。P12-F 起只有一份实现：runtime/hotload.py（热加载与启动走同一条路）；
    hotload 不可用（老版本/异常）时退回逐项注入，**行为不变优先**。
  · run_hot_reload_check —— 主循环每轮调一次：配置/提示词改了就地生效。
    maybe_reload 丢工作线程（读 config.json + 最多 39 个 prompt 文件几十毫秒，
    P12-AA）；出错不抛（热加载的问题绝不能让机器人停摆）。
  · log_hotload —— 热加载自证：改了什么、什么时候、要不要重启，一行说清。
  · run_migrate_config_file —— 旧版配置启动时升级落盘（读路径不写文件）。
  · live_structural —— 交给 hotload 的"启动时定下来"的结构性配置快照：只读
    bot 的运行期镜像（GROUPS 等），不是文件 —— 文件里改了群/Bot，运行期不知道，
    页面要如实说"需要重启"。
  · load_root_module —— 按文件路径加载根目录模块：sio_subscribe 会把项目根
    从 sys.path 摘掉，在它之后 import 根目录模块必然 ModuleNotFoundError（P12-J）。

**接缝注入**：cfg/群集合是 bot 侧状态 → kw-only；runtime.hotload 的 patch
（test_loop_blocking 打在模块属性上）经 `_hotload.maybe_reload` 动态查找穿透。
_FEATURES_DEFAULT_KW（聚合 bot 默认常量）留在 bot，migrate 注入 default_kw。
"""
import asyncio
import sys

from personas import set_config_personas
from personas import set_content_overrides as set_persona_texts
from runtime import config_store
from runtime import features as _features, tuning as _tuning
from runtime.obs import ROOT, log


__all__ = ["run_apply_config_overrides", "live_structural", "run_hot_reload_check",
           "log_hotload", "run_migrate_config_file", "load_root_module"]


_HOT_CAT = {"features": "功能", "tuning": "参数", "scopes": "按群集合",
            "personas": "人设", "prompts": "提示词"}


def run_apply_config_overrides(cfg) -> None:
    """把配置里的热配置（功能/参数/集合/人设）注入运行期。

    P12-F 起这里**只有一份实现**：`runtime/hotload.py`（热加载与启动走同一条路，
    免得"启动时生效的"和"热加载生效的"慢慢长成两套）。

    `_features` / `_tuning` 在**运行时导入**那块才出现，所以这个函数
    必须在那边之后调用。
    """
    c = cfg
    try:
        from runtime import hotload as _hotload
        _hotload.apply_cfg(c, source="startup")
        return
    except Exception as e:                                     # noqa: BLE001
        # hotload 不可用（老版本/异常）时退回逐项注入：**行为不变优先**
        log("P12: hotload 不可用（{}），退回逐项注入".format(str(e)[:60]))
    _features.set_overrides(c.feature_map())
    _tuning.set_overrides(c.tuning_map())
    _tuning.set_scopes(c.scopes)
    set_persona_texts(c.personas)          # P12-E：人设文本/话术覆盖
    set_config_personas({s.cid: s.persona for s in c.group_specs.values()})


def live_structural(*, cfg, groups, mute_cids, screen_cids, push_cids) -> dict:
    """交给 `hotload`：**启动时定下来**的结构性配置（用来判断要不要重启）。

    只读 bot 里的运行期镜像（`groups`/`mute_cids`/…），不是文件 —— 这正是要点：
    文件里改了群/Bot，运行期是不知道的，页面要如实说"需要重启"。
    """
    return {
        "groups": tuple(groups),
        "mute": tuple(sorted(mute_cids)),
        "screen": tuple(sorted(screen_cids)),
        "push": tuple(push_cids),
        "bots": tuple((b.key, b.cid) for b in cfg.bots if b.enabled),
    }


async def run_hot_reload_check() -> None:
    """主循环每轮调一次（P12-F）：配置/提示词改了就地生效，不用重启。

    出错不抛（热加载的问题绝不能让机器人停摆），只在日志里说清楚。

    P12-AA：整体丢进工作线程 —— `maybe_reload()` 在配置/提示词真的变过时要
    读 config.json + 最多 39 个 prompt 文件（几十毫秒），以前直接跑在事件循环上。
    便宜的那一段（一次 `stat`）也跟着挪走，代价可忽略（线程池 16 个工作线程）。
    """
    try:
        from runtime import hotload as _hotload
        rep = await asyncio.to_thread(_hotload.maybe_reload)
    except Exception as e:                                     # noqa: BLE001
        log("P12: 热加载检查异常（不影响运行）：" + str(e)[:100])
        return
    if not rep:
        return
    log_hotload(rep)


def log_hotload(rep) -> None:
    """热加载自证：**改了什么、什么时候、要不要重启**，一行说清。"""
    ch = rep.get("changes") or {}
    n = sum(len(v or []) for v in ch.values())
    if rep.get("invalid") or not rep.get("ok", True):
        log("P12: 热加载 **未生效**（配置文件不合法，运行期保持原样）：{}".format(
            "；".join(rep.get("errors") or [])[:200]))
        return
    if n:
        for cat, items in ch.items():
            for line in (items or []):
                log("P12: 热加载 {} · {}".format(_HOT_CAT.get(cat, cat), line))
    _c = rep.get("counts") or {}
    log("P12: 热加载完成 来源={} 改动={} 项 生效 功能={} 参数={} 集合={} 人设={} prompts={}".format(
        rep.get("source"), n,
        _c.get("features", "-"), _c.get("tuning", "-"), _c.get("scopes", "-"),
        _c.get("personas", "-"), len(rep.get("prompts") or [])))
    for line in (rep.get("structural") or []):
        log("P12: ⚠️ 需要重启才生效 · " + line)
    for line in (rep.get("errors") or []):
        log("P12: 热加载有异常（已跳过）· " + line)


def run_migrate_config_file(*, default_kw) -> None:
    """旧版配置在**启动时**升级落盘（读路径不写文件，见 config_store.migrate_file）。

    放在运行时导入之后（这里才拿得到 `log`）。不需要 reload `_CFG` ——
    `_parse` 内部已把旧版在**内存里**升级过了，这里只是让磁盘上的版本跟上。
    """
    try:
        m = config_store.migrate_file(default_kw)
        if m.get("migrated"):
            log("P11: 配置已从 v{} 升级到 v{}（已备份 {}）".format(
                m["from"], m["to"], m.get("backup") or "无"))
        elif m.get("error"):
            log("P11: 配置未升级（{}）".format(m["error"]))
    except Exception as e:                                     # noqa: BLE001
        log("P11: 配置升级检查异常（不影响启动）：" + str(e)[:80])


def load_root_module(name: str):
    """按**文件路径**加载项目根目录下的模块（P12-J）。

    为什么不直接 `import`：`sio_subscribe` 被 import 时会把项目根目录从
    `sys.path` 里摘掉（`sys.path = [p for p in sys.path if p not in ("", _ROOT)]`
    —— 为了不让项目自己的 `socketio.py` 顶掉 `python-socketio` 包）。
    于是"在它之后 import 根目录下的模块"必然 `ModuleNotFoundError`：
    P12-J 的 Webhook 接收端就是这么挂的（日志里只有一句
    `No module named 'webhook_server'`，手工 `import` 又是好的）。
    按路径加载不受 `sys.path` 影响，也不再受这类副作用牵连。
    """
    import importlib.util
    path = ROOT / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("加载不到 {}（{}）".format(name, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
