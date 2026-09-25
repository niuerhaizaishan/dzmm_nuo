"""按群与 Bot 的运行时配置（P11 第 A 期）。

### 为什么要有这一层

群清单、静默群、筛查群、推送群、房间配置、Bot 绑定的群 —— 这些原来全是
`bot.py` / `personas.py` 里的**源码常量**。改一次要动代码、重新构建、重启。
而"加一个群"、"给玫瑰群单独配人设"这类事会越来越频繁。

### 三条硬性设计

1. **文件不存在 ⇒ 完全退回代码里原来的默认值。**
   这一条让本期成为**零行为变化的纯重构**：不创建 `config.json` 时，行为与
   改动前逐字一致；也给了"配置文件写坏了就先删掉"这条最简单可靠的回滚路径。

2. **配置不是状态。** 刻意**不放** `state_store`：
   那是运行数据（玩法代码会整块覆盖、会随时间膨胀、清理它不影响业务），
   而配置需要可 diff、可备份、可回滚、且**坏掉时能手工改文件救回来** ——
   所以是一只人可读的 JSON。

3. **坏配置直接拒绝启动**，不带着半截配置跑。
   校验覆盖：cid 非空且唯一、Bot 绑定的群必须真的在群清单里、
   **两个 Bot 不能绑同一个群**（否则无法决定走哪个通道）、persona 必须存在。

### 密钥

Bot token **不写进这个文件**（它是密钥，而这个文件会被界面写）。
Bot 用 `token_env` 指向环境变量名（如 `DZMM_BOT_TOKEN_NUO_ROSE`），
值仍留在 `.env` / compose。也支持内联 `token`，但不推荐，见 README。

### 写入

`save()` 是**原子写 + 每次写前备份**（保留最近 N 版），供运维界面用。

⚠️ 为什么配置放在**目录**里（`config/config.json`）而不是仓库根下的单文件：
`config.json` 在容器里是 bind mount。**单文件**挂载时 `os.replace()` 会
`Device or resource busy`（EBUSY）—— `runtime/store.py` 的注释里早就记过这个坑
（`profiles.py`/`state.json` 当初就是这么被逼成"原地写"的），我第一版又踩了一次。
**挂载目录**就没这个问题：目录内可以自由 rename，原子替换成立。

顺带还消掉了另一个坑：目录挂载的源不存在时，Docker 只是建一个**空目录**，
无害；而**文件**挂载的源不存在时，Docker 会建一个**目录**顶在文件路径上，
于是配置读不到、界面写它会失败。
"""
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Config", "BotSpec", "GroupSpec", "ConfigError", "load", "save",
           "config_path", "backup_dir", "audit_path", "KEEP_BACKUPS",
           "SCHEMA_VERSION"]

SCHEMA_VERSION = 1
KEEP_BACKUPS = 20

_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIRNAME = "config"
_DEFAULT_FILE = _ROOT / CONFIG_DIRNAME / "config.json"


class ConfigError(ValueError):
    """配置不合法。**启动时必须让它冒出来**，不要吞掉。"""


@dataclass(frozen=True)
class GroupSpec:
    cid: str
    alias: str = ""
    enabled: bool = True
    mute: bool = False        # 静默监控：只读不回
    screen: bool = False      # 入群筛查（踢游客/男性）
    push: bool = False        # 定时推送（问候/排行/记忆汇报）
    persona: str = ""         # 空 = 用 personas 的默认
    # 刻意**没有** `bot` 字段：哪个 Bot 服务哪个群由 `bots[].cid` 唯一决定。
    # 两边都写 = 两个真源 = 迟早不一致，而"面板显示的绑定"与"实际生效的绑定"
    # 不一致正是运维后台最不该犯的错。


@dataclass(frozen=True)
class BotSpec:
    key: str
    name: str
    cid: str
    short: str = ""
    token: str = ""           # 内联 token（不推荐）
    token_env: str = ""       # 指向环境变量的名字（推荐）
    persona: str = ""
    enabled: bool = True

    def resolve_token(self, env=None) -> str:
        """取 token：环境变量优先，其次内联。"""
        e = os.environ if env is None else env
        if self.token_env:
            v = (e.get(self.token_env) or "").strip()
            if v:
                return v
        return (self.token or "").strip()


def default_bots_from_env(default_cid: str) -> list:
    """从环境变量造出默认的 Bot 注册表（与 P4 时代"单 Bot"配置等价）。

    刻意放在这里、由 `bot.py` 和运维后台**共用**：两边各自拼一份的话，
    迟早会漂 —— 而"面板显示的 Bot 列表"和"实际生效的 Bot 列表"不一致
    正是运维后台最不该犯的错。

    token **不内联**，走 `token_env` 指向 `.env` 里的变量名。
    """
    return [{
        "key": "nuo_main",
        "name": (os.environ.get("DZMM_BOT_NAME") or "小小糯").strip(),
        "short": (os.environ.get("DZMM_BOT_SHORT") or "小小糯").strip(),
        "cid": (os.environ.get("DZMM_BOT_CID") or "").strip() or default_cid,
        "token_env": "DZMM_BOT_TOKEN",
        "persona": "nuo",
    }]


@dataclass
class Config:
    groups: tuple = ()                 # (cid, ...) —— 与原来的 GROUPS 同序同义
    mute: frozenset = frozenset()
    screen: frozenset = frozenset()
    push: tuple = ()
    group_specs: dict = field(default_factory=dict)   # cid -> GroupSpec
    bots: tuple = ()                   # (BotSpec, ...)
    source: str = "defaults"           # "file" | "defaults"
    path: str = ""

    def bot_for(self, cid: str):
        for b in self.bots:
            if b.cid == cid:
                return b
        return None

    def group(self, cid: str):
        return self.group_specs.get(cid)

    def as_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "groups": [{"cid": g.cid, "alias": g.alias, "enabled": g.enabled,
                        "mute": g.mute, "screen": g.screen, "push": g.push,
                        "persona": g.persona}
                       for g in (self.group_specs[c] for c in self.groups)],
            "bots": [{"key": b.key, "name": b.name, "short": b.short, "cid": b.cid,
                      "token_env": b.token_env, "persona": b.persona,
                      "enabled": b.enabled,
                      **({"token": b.token} if b.token else {})}
                     for b in self.bots],
        }


# ---------------------------------------------------------------- 路径

def config_path() -> Path:
    return Path(os.environ.get("DZMM_CONFIG") or _DEFAULT_FILE)


def backup_dir() -> Path:
    return config_path().parent / "config_backups"


def audit_path() -> Path:
    """审计日志与配置放**同一个目录**。

    ⚠️ 不能放在 `/app` 根下：那里不在任何挂载卷里，容器一重建日志就没了 ——
    而"谁在什么时候改了什么"恰恰是最需要跨重启保留的东西。
    """
    return config_path().parent / "config_audit.log"


# ---------------------------------------------------------------- 校验

def _require(cond, msg):
    if not cond:
        raise ConfigError(msg)


def _as_list(v, what):
    _require(isinstance(v, list), "{} 必须是数组，收到 {}".format(what, type(v).__name__))
    return v


def _parse(raw: dict, defaults: dict) -> Config:
    """把原始 dict 解析成 Config；任何不合规都抛 ConfigError。"""
    _require(isinstance(raw, dict), "配置根节点必须是对象")
    ver = raw.get("version", SCHEMA_VERSION)
    _require(ver == SCHEMA_VERSION,
             "配置 version={} 不受支持（本程序只认 {}）".format(ver, SCHEMA_VERSION))

    known_personas = set(defaults.get("personas") or ())
    groups = []
    specs = {}
    for i, g in enumerate(_as_list(raw.get("groups", []), "groups")):
        where = "groups[{}]".format(i)
        _require(isinstance(g, dict), "{} 必须是对象".format(where))
        cid = str(g.get("cid") or "").strip()
        _require(cid, "{} 缺少 cid".format(where))
        _require(cid not in specs, "群 {} 重复出现（cid 必须唯一）".format(cid))
        persona = str(g.get("persona") or "").strip()
        if persona and known_personas:
            _require(persona in known_personas,
                     "{} 的 persona={!r} 不存在（可用：{}）".format(
                         where, persona, ", ".join(sorted(known_personas))))
        spec = GroupSpec(
            cid=cid,
            alias=str(g.get("alias") or "").strip(),
            enabled=bool(g.get("enabled", True)),
            mute=bool(g.get("mute", False)),
            screen=bool(g.get("screen", False)),
            push=bool(g.get("push", False)),
            persona=persona,
        )
        specs[cid] = spec
        # 语义与原来的 GROUPS/MUTE_CIDS 一致：静默群**仍在** GROUPS 里
        # （主循环会读它的消息、sync_all 会跳过它），只是不回复。
        # 所以这里只看 enabled，不看 mute。
        if spec.enabled:
            groups.append(cid)

    bots = []
    seen_keys = set()
    seen_cids = set()
    for i, b in enumerate(_as_list(raw.get("bots", []), "bots")):
        where = "bots[{}]".format(i)
        _require(isinstance(b, dict), "{} 必须是对象".format(where))
        key = str(b.get("key") or "").strip()
        _require(key, "{} 缺少 key".format(where))
        _require(key not in seen_keys, "Bot key {!r} 重复".format(key))
        cid = str(b.get("cid") or "").strip()
        _require(cid, "{} 缺少 cid（必须写明它绑定哪个群）".format(where))
        # P1 那条「Bot 绑定群 ≠ 目标群就拒绝启动」断言的推广：
        # 绑定的群必须真的在群清单里，否则就是误发风险。
        _require(cid in specs,
                 "{} 绑定的群 {} 不在 groups 里".format(where, cid[:8]))
        _require(cid not in seen_cids,
                 "两个 Bot 绑了同一个群 {}：无法决定走哪个通道".format(cid[:8]))
        persona = str(b.get("persona") or "").strip()
        if persona and known_personas:
            _require(persona in known_personas,
                     "{} 的 persona={!r} 不存在".format(where, persona))
        tokens = [bool((b.get("token") or "").strip()),
                  bool((b.get("token_env") or "").strip())]
        _require(any(tokens),
                 "{} 既没有 token 也没有 token_env。"
                 "推荐用 token_env 指向 .env 里的变量名（密钥不进配置文件）".format(where))
        seen_keys.add(key)
        seen_cids.add(cid)
        bots.append(BotSpec(
            key=key,
            name=str(b.get("name") or "").strip(),
            short=str(b.get("short") or "").strip(),
            cid=cid,
            token=str(b.get("token") or "").strip(),
            token_env=str(b.get("token_env") or "").strip(),
            persona=persona,
            enabled=bool(b.get("enabled", True)),
        ))

    mute = frozenset(cid for cid, s in specs.items() if s.mute)
    screen = frozenset(cid for cid, s in specs.items() if s.screen)
    push = tuple(cid for cid in groups if specs[cid].push)
    return Config(groups=tuple(groups), mute=mute, screen=screen, push=push,
                  group_specs=specs, bots=tuple(bots))


def _defaults_to_raw(defaults: dict) -> dict:
    """把代码里的默认值拼成与配置文件同构的 dict。

    这是"文件不存在就退回原行为"的实现基础，也是 `tools/config_gen.py`
    用来生成首份 config.json 的来源 —— 生成的配置必然与现状等价。

    ⚠️ 必须 **deepcopy**：返回的 dict 会被调用方拿去改（校验失败的测试、
    将来界面的"编辑后再保存"路径）。浅拷贝的话 `bots` 里那些 dict 仍是
    **调用方自己的对象**，改一处就把模块级默认值改掉了 —— 这类别名 bug
    非常隐蔽（测试里表现为"某个用例跑完，后面的用例全红"）。
    """
    import copy
    groups = list(defaults.get("groups") or ())
    mute = set(defaults.get("mute") or ())
    screen = set(defaults.get("screen") or ())
    push = set(defaults.get("push") or ())
    aliases = dict(defaults.get("aliases") or {})
    return {
        "version": SCHEMA_VERSION,
        "groups": [{"cid": cid, "alias": aliases.get(cid, ""), "enabled": True,
                    "mute": cid in mute, "screen": cid in screen,
                    "push": cid in push, "persona": ""}
                   for cid in groups],
        "bots": copy.deepcopy(list(defaults.get("bots") or ())),
    }


# ---------------------------------------------------------------- 读

_cache = None


def load(defaults: dict, force: bool = False) -> Config:
    """读配置。文件不存在就用 `defaults`（＝改动前的行为）。

    `defaults` 需要提供：`groups` / `mute` / `screen` / `push`，
    可选 `aliases` / `bots` / `personas`（用于校验 persona 名字是否存在）。
    """
    global _cache
    if _cache is not None and not force:
        return _cache
    p = config_path()
    if not p.is_file():
        cfg = _parse(_defaults_to_raw(defaults), defaults)
        cfg.source = "defaults"
        cfg.path = str(p)
        _cache = cfg
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                      # noqa: BLE001
        raise ConfigError("配置文件解析失败 {}：{}".format(p, e))
    cfg = _parse(raw, defaults)
    cfg.source = "file"
    cfg.path = str(p)
    _cache = cfg
    return cfg


def reload(defaults: dict) -> Config:
    return load(defaults, force=True)


# ---------------------------------------------------------------- 写（供后续界面用）

def save(raw: dict, defaults: dict) -> Config:
    """校验 → 备份 → 原子写。**校验不过一个字都不写。**

    写路径现在就先实现并测掉：免得界面做出来才发现"校验没地方放"。
    """
    cfg = _parse(raw, defaults)          # 先校验
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        bd = backup_dir()
        bd.mkdir(parents=True, exist_ok=True)
        dst = bd / "config-{}.json".format(time.strftime("%Y%m%d-%H%M%S"))
        try:
            shutil.copy2(str(p), str(dst))
        except Exception:                                       # noqa: BLE001
            pass
        for old in sorted(bd.glob("config-*.json"))[:-KEEP_BACKUPS]:
            try:
                old.unlink()
            except Exception:                                   # noqa: BLE001
                pass
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(str(tmp), str(p))         # 原子替换
    return reload(defaults)
