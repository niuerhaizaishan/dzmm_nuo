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
           "SCHEMA_VERSION", "SUPPORTED_VERSIONS", "migrate_file",
           "default_bots_from_env"]

SCHEMA_VERSION = 4
KEEP_BACKUPS = 20

# 历史版本 → 升级方式。**只做加法**：新版必须有办法读旧版，
# 且升级过程要留备份（`migrate_file()` 负责）。
SUPPORTED_VERSIONS = (1, 2, 3, 4)

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
    # P12-C：这一群的**功能开关覆盖**与**参数覆盖**（只写与默认不同的）。
    # 用 dict（不是 tuple）是因为要按 key 查；约定**只读**，别就地改。
    features: dict = field(default_factory=dict)   # {"diaojiao": False}
    tuning: dict = field(default_factory=dict)     # {"heat.climax_threshold": 120}
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
    enabled: bool = True
    # P12-H（schema v4）：这个 Bot 在平台上的**用户 id**。
    # 用途只有一个但很关键 —— `runtime/identity.py` 要靠它把"我自己"认全，
    # 否则新 Bot 的发言会被当成群友（轻则统计污染，重则自问自答）。
    # 可以留空：留空时启动会用**成员表里的显示名**去认一次
    # （`identity.resolve_bot_uids()`），认不到还能在循环里靠名字兜底。
    uid: str = ""
    # 刻意**没有** `persona`：Bot 只是"把已经生成好的文本送进某个群"的**通道**，
    # 而人设是在生成那一刻、按**群**决定的（`personas.persona_key_for(cid)`）。
    # 曾经两边都留了 persona ⇒ Bot 上那份永远不生效，页面上却是可填的
    # （又一个"看起来能配、实际不生效"）。人设一律按群配 `groups[].persona`。

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
        # P12-H：env 里已经有这个 Bot 的平台 id 了，顺手带进默认值 ——
        # 于是"配置文件不存在"的部署也能正确认出自己是哪个身份。
        **({"uid": os.environ["DZMM_BOT_ID"].strip()}
           if (os.environ.get("DZMM_BOT_ID") or "").strip() else {}),
    }]


@dataclass
class Config:
    groups: tuple = ()                 # (cid, ...) —— 与原来的 GROUPS 同序同义
    mute: frozenset = frozenset()
    screen: frozenset = frozenset()
    push: tuple = ()
    group_specs: dict = field(default_factory=dict)   # cid -> GroupSpec
    bots: tuple = ()                   # (BotSpec, ...)
    # P12-C（schema v2）：这两块是"与代码默认不同"的**覆盖**，
    # 全局的放这里，按群的挂在各自的 GroupSpec 上。
    features: dict = field(default_factory=dict)      # {"diaojiao": False}
    tuning: dict = field(default_factory=dict)        # {"devil.base_prob": 0.2}
    scopes: dict = field(default_factory=dict)        # {"trend": ("cid", ...)}
    # P12-E（schema v3）：人设文本/话术的覆盖，同样是"与代码默认不同"才写。
    #   {"rose_butler": {"name_full": …, "extra": …, "phrases": {"mem_saved": …}}}
    personas: dict = field(default_factory=dict)
    migrated_from: int = 0             # 从哪一版升上来的（0 = 本来就是当前版本）
    source: str = "defaults"           # "file" | "defaults"
    path: str = ""

    def bot_for(self, cid: str):
        for b in self.bots:
            if b.cid == cid:
                return b
        return None

    def bots_for_identity(self) -> list:
        """给 `runtime.identity.register_bots()` 的形状：**每个启用的 Bot** 一条。

        运行期"我自己"有几个身份完全由这里决定 —— 少一个就会出现
        "机器人把自己的发言当群友"（重则自问自答）。
        """
        return [{"key": b.key, "name": b.name, "short": b.short,
                 "cid": b.cid, "uid": b.uid}
                for b in self.bots if b.enabled]

    def group(self, cid: str):
        return self.group_specs.get(cid)

    def feature_map(self) -> dict:
        """展平成 `runtime.features.set_overrides()` 要的形状。

        全局用 `"key"`，按群用 `"cid|key"` —— 群级优先由 features 层负责。
        顺序上先全局后按群（dict 后写覆盖先写，语义更直观）。
        """
        out = {k: bool(v) for k, v in (self.features or {}).items()}
        for cid, spec in (self.group_specs or {}).items():
            for k, v in (getattr(spec, "features", None) or {}).items():
                out["{}|{}".format(cid, k)] = bool(v)
        return out

    def tuning_map(self) -> dict:
        """同 `feature_map()`，给 `runtime.tuning.set_overrides()`。"""
        out = dict(self.tuning or {})
        for cid, spec in (self.group_specs or {}).items():
            for k, v in (getattr(spec, "tuning", None) or {}).items():
                out["{}|{}".format(cid, k)] = v
        return out

    def persona_texts(self) -> dict:
        """给 `runtime.personas.set_content_overrides()` 的形状（就是原样一份）。"""
        return {k: dict(v) for k, v in (self.personas or {}).items()}

    def as_dict(self) -> dict:
        """序列化回"配置文件的样子"。

        ⚠️ 群列表**必须是全部**配置项，不能只给 `self.groups`（那只是
        **生效**的那些，已按 `enabled` 过滤过）。踩过的坑：运维后台的
        `/edit` 是用这个方法渲染表单的，于是"取消勾选启用"一存，
        那个群就**从页面上消失**了 —— 再也勾不回来；更糟的是，
        这时候在页面上改任何别的东西再保存，配置里那几行会被
        **整行删掉**（别名、人设一起没）。所以这里与运行期语义分开：
        运行期看 `groups`，配置界面看 `group_specs`。

        空字典（没有任何覆盖）**不写进文件** —— 配置文件里"没有这一节"
        和"这一节是空的"是同一件事，少写点噪声。
        """
        out = {
            "version": SCHEMA_VERSION,
            "groups": [{"cid": g.cid, "alias": g.alias, "enabled": g.enabled,
                        "mute": g.mute, "screen": g.screen, "push": g.push,
                        "persona": g.persona,
                        **({"features": dict(g.features)}
                           if getattr(g, "features", None) else {}),
                        **({"tuning": dict(g.tuning)}
                           if getattr(g, "tuning", None) else {})}
                       for g in self.group_specs.values()],
            "bots": [{"key": b.key, "name": b.name, "short": b.short, "cid": b.cid,
                      "token_env": b.token_env, "enabled": b.enabled,
                      # 只写非空的 uid：它本来就可以空着（启动时按名字认）
                      **({"uid": b.uid} if getattr(b, "uid", "") else {}),
                      **({"token": b.token} if b.token else {})}
                     for b in self.bots],
        }
        if self.features:
            out["features"] = dict(self.features)
        if self.tuning:
            out["tuning"] = dict(self.tuning)
        if self.scopes:
            out["scopes"] = {k: list(v) for k, v in self.scopes.items()}
        if self.personas:
            out["personas"] = {k: _persona_out(v) for k, v in self.personas.items()}
        return out


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


def _strict_bool(v, where):
    """只认真正的 JSON 布尔。

    ⚠️ 不能再用 `bool(v)`：`bool("false") == True` —— 手写配置里写成字符串
    `"false"` 会被当成**打开**，而且没有任何提示。这类"静默反向"比报错糟得多。
    （页面上来的值由 `config_ui.parse_form` 转成真布尔，不受影响。）
    """
    _require(isinstance(v, bool), "{} 必须是 true/false（收到 {!r}）".format(where, v))
    return v


def _override_map(v, where, known, kind):
    """校验一张"覆盖表"：键必须在册，值合法。

    `kind` 是 `features` 或 `tuning`，只影响报错文案。
    允许两种写法：`{"key": true}` 与 `{"key": {"on": false}}`（后者为将来留扩展位）。
    """
    if v is None:
        return {}
    _require(isinstance(v, dict), "{} 必须是对象".format(where))
    out = {}
    for k, val in v.items():
        key = str(k).strip()
        _require(key, "{} 里有空 key".format(where))
        _require(key in known,
                 "{}.{} 不是已登记的{}（可用：{}）".format(
                     where, key, kind,
                     ", ".join(sorted(known)[:6]) + "…" if len(known) > 6 else
                     ", ".join(sorted(known))))
        if isinstance(val, dict):
            _require("on" in val, "{}.{} 写成对象时必须带 on".format(where, key))
            val = _strict_bool(val["on"], "{}.{}.on".format(where, key))
        if kind == "功能开关":
            out[key] = _strict_bool(val, "{}.{}".format(where, key))
        else:
            from runtime.tuning import PARAMS, _coerce, TuningError
            _param, _feat = PARAMS[key]
            try:
                out[key] = _coerce(_param, val, "{}.{}".format(where, key))
            except TuningError as e:
                raise ConfigError(str(e))
    return out


def _feature_keys():
    from runtime.features import keys as _keys
    return set(_keys())


def _param_keys():
    from runtime.tuning import PARAMS
    return set(PARAMS)


def _scope_names():
    from runtime.tuning import SCOPE_DEFAULTS
    return set(SCOPE_DEFAULTS)


# ---------------------------------------------------------------- 人设覆盖（P12-E）

_PERSONA_FIELDS = ("name_full", "name_short", "extra")


def _persona_out(v: dict) -> dict:
    """按固定字段顺序输出一条人设覆盖（文件好看、diff 稳定）。

    ⚠️ 用 `f in v` 而不是 `v.get(f)`：**空串是有意义的值**
    （`extra: ""` = 这个人设不要专属设定）。写成 `if v.get(f)` 会把它丢掉，
    于是一次"清空 extra"的保存会静默退回代码默认的长文本。
    """
    out = {}
    for f in _PERSONA_FIELDS:
        if f in v:
            out[f] = v[f]
    ph = v.get("phrases") or {}
    if ph:
        out["phrases"] = {k: ph[k] for k in sorted(ph)}
    return out


def _persona_map(v, where):
    """校验人设文本/话术覆盖，返回规范化后的 dict。

    规则（与页面上的提示**同源**，`runtime.personas.health()` 也是这套）：
    - persona key 必须是代码里登记过的人设；
    - 只认 name_full / name_short / extra / phrases 四个字段 —— 写错字段名报错，
      不静默忽略（"我改了没生效"是最难查的一类问题）；
    - **写了就是覆盖**（空串也算写了）：`extra: ""` = 不要专属设定，
      `phrases.x: ""` = 这条话术留空。不写 = 用代码默认值；
    - 名字不能为空、不能有换行（会进提示词的第一行、也要用来在群里自称）；
    - 话术是**要发出去的整条消息**，所以按平台限制卡长度与换行数。
    """
    if v is None:
        return {}
    _require(isinstance(v, dict), "{} 必须是对象".format(where))
    # personas 在仓库根（不在 runtime/ 里）—— 与 bot.py 同一个导入方式
    import personas as P
    out = {}
    for k, val in v.items():
        key = str(k).strip()
        _require(key, "{} 里有空的人设 key".format(where))
        _require(key in P.PERSONAS,
                 "{}.{} 不是已登记的人设（可用：{}）".format(
                     where, key, ", ".join(sorted(P.PERSONAS))))
        _require(isinstance(val, dict), "{}.{} 必须是对象".format(where, key))
        for f in val:
            _require(f in _PERSONA_FIELDS + ("phrases",),
                     "{}.{} 里不认识的字段 {!r}（可用：{}）".format(
                         where, key, f, " / ".join(_PERSONA_FIELDS + ("phrases",))))
        item = {}
        for f in _PERSONA_FIELDS:
            s = val.get(f)
            if s is None:
                continue
            _require(isinstance(s, str),
                     "{}.{}.{} 必须是字符串".format(where, key, f))
            if f == "extra":
                _require(len(s) <= P.EXTRA_MAX_CHARS,
                         "{}.{}.extra 有 {} 字，超过上限 {} 字".format(
                             where, key, len(s), P.EXTRA_MAX_CHARS))
            else:
                _require(s.strip(),
                         "{}.{}.{} 不能为空（群里要用它自称；"
                         "想恢复默认就把这个字段去掉）".format(where, key, f))
                _require("\n" not in s,
                         "{}.{}.{} 里不能有换行".format(where, key, f))
                _require(len(s) <= 40,
                         "{}.{}.{} 太长（最多 40 字）".format(where, key, f))
            item[f] = s
        ph = val.get("phrases")
        if ph is not None:
            _require(isinstance(ph, dict),
                     "{}.{}.phrases 必须是对象".format(where, key))
            known = set(P.known_phrase_keys(key))
            clean = {}
            for pk, pv in ph.items():
                pkey = str(pk).strip()
                _require(pkey in known,
                         "{}.{}.phrases.{} 不是已登记的话术（可用：{}）".format(
                             where, key, pkey,
                             ", ".join(sorted(known)[:6]) + "…" if len(known) > 6
                             else ", ".join(sorted(known))))
                _require(isinstance(pv, str),
                         "{}.{}.phrases.{} 必须是字符串".format(where, key, pkey))
                # 空串允许（= 这条话术留空），长度/换行仍然要卡
                _require(len(pv) <= P.MSG_MAX_CHARS,
                         "{}.{}.phrases.{} 有 {} 字，超过平台上限 {} 字".format(
                             where, key, pkey, len(pv), P.MSG_MAX_CHARS))
                _require(pv.count("\n") <= P.MSG_MAX_NEWLINES,
                         "{}.{}.phrases.{} 有 {} 个换行，超过平台上限 {} 行".format(
                             where, key, pkey, pv.count("\n"), P.MSG_MAX_NEWLINES))
                clean[pkey] = pv
            if clean:
                item["phrases"] = clean
        if item:
            out[key] = item
    return out


def _migrate(raw: dict) -> tuple:
    """把旧版配置**就地升级**成当前版本。返回 (raw, 原版本号)。

    v1 → v2 **只是版本号变化**：三块覆盖节（`features`/`tuning`/`scopes`）
    是**可选**的（没写 = 没有覆盖 = 用代码默认值），所以旧文件不需要补空节 ——
    补了反而会把一堆空对象写进文件（`save()` 是照 raw 原样写的）。
    v2 → v3 同理：新增的 `personas` 节也是可选的（没写 = 人设全用代码默认）。
    不认识的高版本**不猜**，交给 `_parse` 报错（宁可起不来，也不要按错的结构跑）。
    """
    ver = raw.get("version", SCHEMA_VERSION)
    if ver == SCHEMA_VERSION:
        return raw, 0
    if ver not in SUPPORTED_VERSIONS:
        return raw, 0                      # 让 _parse 报"不受支持"
    out = dict(raw)
    out["version"] = SCHEMA_VERSION
    return out, ver


def _parse(raw: dict, defaults: dict) -> Config:
    """把原始 dict 解析成 Config；任何不合规都抛 ConfigError。"""
    _require(isinstance(raw, dict), "配置根节点必须是对象")
    raw, migrated_from = _migrate(raw)
    ver = raw.get("version", SCHEMA_VERSION)
    _require(ver == SCHEMA_VERSION,
             "配置 version={} 不受支持（本程序支持 {}）".format(
                 raw.get("version"), " / ".join(str(v) for v in SUPPORTED_VERSIONS)))

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
            # P12-C：改用严格布尔 —— `bool("false")` 是 True，会把字符串
            # "false" 静默当成打开（这类"静默反向"比直接报错糟得多）
            enabled=_strict_bool(g.get("enabled", True), where + ".enabled"),
            mute=_strict_bool(g.get("mute", False), where + ".mute"),
            screen=_strict_bool(g.get("screen", False), where + ".screen"),
            push=_strict_bool(g.get("push", False), where + ".push"),
            persona=persona,
            features=_override_map(g.get("features"), where + ".features",
                                   _feature_keys(), "功能开关"),
            tuning=_override_map(g.get("tuning"), where + ".tuning",
                                 _param_keys(), "参数"),
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
        # 老配置里可能有 `persona` 键（Bot 上的那份从来没生效过）——
        # 忽略它即可（不报错，免得旧文件起不来）；人设一律看 groups[].persona。
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
            enabled=_strict_bool(b.get("enabled", True), where + ".enabled"),
            # P12-H：可选。空 = 启动时按成员表里的名字认（见 identity.resolve_bot_uids）
            uid=str(b.get("uid") or "").strip(),
        ))

    mute = frozenset(cid for cid, s in specs.items() if s.mute)
    screen = frozenset(cid for cid, s in specs.items() if s.screen)
    push = tuple(cid for cid in groups if specs[cid].push)

    # 全局覆盖（P12-C）
    feats = _override_map(raw.get("features"), "features",
                          _feature_keys(), "功能开关")
    tune = _override_map(raw.get("tuning"), "tuning", _param_keys(), "参数")
    scopes = {}
    raw_scopes = raw.get("scopes") or {}
    _require(isinstance(raw_scopes, dict), "scopes 必须是对象")
    for name, cids in raw_scopes.items():
        nm = str(name).strip()
        _require(nm in _scope_names(),
                 "scopes.{} 不是已登记的按群集合（可用：{}）".format(
                     nm, ", ".join(sorted(_scope_names()))))
        _require(isinstance(cids, list), "scopes.{} 必须是数组".format(nm))
        clean = []
        for c in cids:
            cidv = str(c or "").strip()
            # 集合里写了一个不存在的群 = 打错字 ⇒ 报错，否则它会永远不生效
            _require(cidv in specs,
                     "scopes.{} 里的 {} 不在 groups 里（拼错了？）".format(
                         nm, cidv[:8] or "(空)"))
            if cidv not in clean:
                clean.append(cidv)
        scopes[nm] = tuple(clean)

    # 人设文本/话术覆盖（P12-E）
    pers = _persona_map(raw.get("personas"), "personas")

    return Config(groups=tuple(groups), mute=mute, screen=screen, push=push,
                  group_specs=specs, bots=tuple(bots),
                  features=feats, tuning=tune, scopes=scopes, personas=pers,
                  migrated_from=migrated_from)


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


def migrate_file(defaults: dict) -> dict:
    """把磁盘上的旧版配置**升级并落盘**（先备份）。返回一份人话报告。

    为什么要单独一个动作、而不是在 `load()` 里顺手写：
    `load()` 是**读路径**（面板、闸门、一次性容器都在调它），
    读的时候顺手改文件是意外行为 —— 会出现"我只看了一眼，文件就被改了"。
    所以升级落盘放在**启动时显式调一次**（`bot.py`），并在日志里自证。

    返回：`{"migrated": bool, "from": int, "to": int, "backup": str|None, "error": str}`
    """
    out = {"migrated": False, "from": 0, "to": SCHEMA_VERSION,
           "backup": None, "error": ""}
    p = config_path()
    if not p.is_file():
        return out
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                      # noqa: BLE001
        out["error"] = "解析失败：{}".format(e)
        return out
    old_ver = int(raw.get("version", 0) or 0)
    if old_ver == SCHEMA_VERSION:
        return out
    migrated, _ = _migrate(raw)
    if migrated.get("version") != SCHEMA_VERSION:
        out["error"] = "version={} 不受支持，未升级".format(old_ver)
        return out
    backups = sorted(backup_dir().glob("config-*.json")) if backup_dir().is_dir() else []
    before = set(str(b) for b in backups)
    try:
        save(migrated, defaults)          # save 内部会先备份、再原子写
    except Exception as e:                                      # noqa: BLE001
        out["error"] = "升级失败（原文件未动）：{}".format(e)
        return out
    new_backups = [str(b) for b in sorted(backup_dir().glob("config-*.json"))
                   if str(b) not in before]
    out.update({"migrated": True, "from": old_ver,
                "backup": os.path.basename(new_backups[-1]) if new_backups else None})
    return out


def load(defaults: dict, force: bool = False) -> Config:
    """读配置。文件不存在就用 `defaults`（＝改动前的行为）。

    `defaults` 需要提供：`groups` / `mute` / `screen` / `push`，
    可选 `aliases` / `bots` / `personas`（用于校验 persona 名字是否存在）。

    ⚠️ 旧版文件在这里**只在内存里升级**（`_parse` 内部的 `_migrate`），
    落盘由 `migrate_file()` 显式做 —— 读路径不写文件。
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

def _warn_backup(err: str) -> None:
    """配置备份失败：每个原因只喊一遍（P12-AB）。"""
    key = "backup|" + err[:40]
    if key in _WARNED_BACKUP:
        return
    _WARNED_BACKUP.add(key)
    msg = ("⚠️ config_store: 配置备份失败（{}）—— 本次仍会写入新配置，"
           "但这一次没有可回滚的备份").format(err[:120])
    try:
        from runtime.obs import log as _log
        _log(msg)
    except Exception:                                          # noqa: BLE001
        print(msg)


_WARNED_BACKUP: set = set()


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
        except Exception as e:                                  # noqa: BLE001
            # P12-AB：备份失败**不能**静默 —— 紧接着就会覆盖 config.json，
            # 用户以为"每次保存都自动备份"，其实这一次没有（出事了才发现没得回滚）。
            _warn_backup(str(e))
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
