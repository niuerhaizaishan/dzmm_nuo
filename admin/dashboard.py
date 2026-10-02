#!/usr/bin/env python3
"""小小糯运维后台（P9）—— **只读**。

### 为什么要它

整套排查手段原来只有 SSH + `docker logs`。反复出现的几件事其实都能一眼看完：
出站队列有没有积压/重试、续期是不是一直在 SKIP、token 还有多久过期、
各群有哪些玩法卡在进行中、prompt 到底加载的是文件还是代码默认值。

### 三条设计约束（都是刻意的）

1. **只读，而且是结构性的只读。**
   只允许 GET/HEAD；所有 SQLite 一律以 `mode=ro` 打开；源码里不出现任何
   INSERT/UPDATE/DELETE/DROP（`tests/test_admin_dashboard.py` 会扫源码）。
   "运维后台写坏了线上数据"是这类工具最典型的翻车方式，从结构上掐掉。

2. **对外的边界由"登录 + 安全组白名单"两层组成。**
   容器内监听 `0.0.0.0:8090`（默认），`docker-compose.prod.yml` 把它发布到宿主机
   （`${DZMM_ADMIN_BIND:-0.0.0.0}:8090:8090`），所以**默认是对外可达的**。
   因此必须同时具备：

   a. **账号密码登录**（`DZMM_ADMIN_USER` / `DZMM_ADMIN_PASSWORD`）。
      声明对外发布却没有设密码 -> 本进程**拒绝启动**（fail closed）。
   b. **安全组只放行你自己的出口 IP** —— 这一条**不能省**，理由见第 3 条。

   ⚠️ 为什么容器内**不能**绑 127.0.0.1：docker 的端口发布是由 `docker-proxy`
   从**容器 IP**（不是回环）转发进来的，容器内绑回环会让发布出来的端口
   根本连不上 —— 第一次部署就是这么失败的（宿主机 curl 返回 000，
   而 `ss` 显示 docker-proxy 明明在 127.0.0.1:8090 上听着）。

   想彻底不开公网口：`.env` 里设 `DZMM_ADMIN_BIND=127.0.0.1`，改用 SSH 隧道
   （`ssh -L 8090:127.0.0.1:8090 root@host`）。

3. **这是 HTTP，不是 HTTPS —— 密码在链路上是明文的。**
   所以安全组白名单不是"锦上添花"，而是**让明文密码可以接受的前提**：
   只放行你自己的 IP，能截到密码的顶多是你自己那段接入网。
   （实测过：8090 在"只有密码、没有白名单"的情况下是**整个世界可连**的 ——
   用境外节点探测，拿到了登录页。）要真正上 HTTPS 需要给机器配域名 + 证书。

4. **跑在容器内，不新增容器。**
   容器内是 Python 3.11 且已有拆好的 `runtime/*`，能直接复用 bot 同款代码读状态，
   不会出现"后台照着 schema 抄一遍、然后漂移"的问题。宿主机只有 Python 3.6，
   而且 1.8G 内存已经被否决过一次"双容器"方案。

### 认证：为什么不是 URL 令牌

原来用 `?token=`，已换成正式登录。令牌的问题都是实际会遇到的：
会留在浏览器历史和链路日志里、**没法登出**、没有失败计数（可以无限试）、
而且它只是"持票入场"，不是"谁进来了"。

- 账号密码从环境变量读（`.env`，root 600），**不落盘任何凭据**
- 会话只存在**本进程内存**里：随机 32 字节 sid，`HttpOnly` + `SameSite=Strict`，
  12 小时过期；**重启即全部登出**（这正是想要的行为）
- 校验用 `hmac.compare_digest`，且账号与密码**都算完再比** ——
  否则"账号对不对"会从耗时上泄漏出去（用户名枚举）
- 连续失败 5 次开始锁定，5 分钟起逐次翻倍，上限 1 小时；失败与锁定都打日志
- 唯一允许的非 GET 路径是 `POST /login`：它只动内存会话表与 Cookie，
  **不碰数据库也不碰文件** —— 面板的"只读"性质没被破坏

### 开关

- `DZMM_ADMIN=off`  不启动（entrypoint 读这个）
- `DZMM_ADMIN_PORT` 默认 8090；`DZMM_ADMIN_HOST` 默认 `0.0.0.0`（见上面第 2 条）
- `DZMM_ADMIN_USER` 默认 `admin`
- `DZMM_ADMIN_PASSWORD` 登录密码（<8 位会被拒绝启动）
- `DZMM_ADMIN_PUBLIC=on` 声明"这条端口发布到了回环之外"
- `DZMM_ADMIN_BIND`（compose 用）发布到宿主机的地址，默认 `0.0.0.0`
"""
import html
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.store import SCOPE_GROUP  # noqa: E402  （只借一个常量名，避免抄 scope 字符串）

VERSION = "P9-3"
# 容器内绑 0.0.0.0：docker-proxy 是从**容器 IP** 转发进来的，容器内绑回环会让
# compose 发布出来的端口连不上（第一次部署就这么失败了）。
# 真正的安全边界是 compose 里的端口映射 —— 见下面 DZMM_ADMIN_PUBLIC 的说明。
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8090

BOT_LOG = ROOT / "bot.log"
HEALTH_LOG = ROOT / "check_health.log"
RENEW_LOG = ROOT / "renew_token.log"
# S1：与 token_store 同一套 DZMM_TOKEN_FILE 口径，双写期面板读 secrets/ 下的新文件
TOKEN_FILE = Path(os.environ.get("DZMM_TOKEN_FILE") or (ROOT / ".dzmm_token.json"))
SESSION_VERSION_FILE = ROOT / ".session_version.json"
OUTBOX_DB = ROOT / "data" / "outbox.db"
STORE_DB = ROOT / "data" / "store.db"

# 启动自证行：这几行没有，就说明对应能力没生效（P3~P8 各一条）
PROOF_MARKERS = ["bot路由", "outbox:", "P5:", "P7:", "P8:",
                 # P11/P12 的自证行（P12-F 补上）：看一眼面板就知道
                 # "配置来自文件还是默认值 / 开关参数人设覆盖了几个 / 热加载开没开"。
                 # 这些都是**每次启动都会打**的行，所以"全都在"是有意义的判据。
                 "P11: 配置=", "P11: 人设", "P12: 功能", "P12: 参数",
                 "P12: 人设文本", "P12: 热加载="]
# 错误识别：**宁窄勿宽**。误报比不报更糟 —— 一个天天喊着"有错误"的面板会被无视，
# 而真正出事那天也就没人看了。
# 刻意**不包含** "超时"/"失败" 这两个词：
#   * "👻 女鬼状态超时，自动恢复" 是正常自愈日志；
#   * "告警已发: 续期脚本失败" 是**通知**本身，不是失败现场。
ERROR_MARKERS = ("traceback", "exception", "critical", "❌", "error:",
                 "failed", "无法", "崩溃", "拒绝连接")
# 命中上面但其实是正常/通知的行，一律不算错误
ERROR_EXCLUDE = ("告警已发", "自动恢复", "SKIP", "无需写回", "已恢复", "重试成功")


# ---------------------------------------------------------------- 小工具

def _tail(path: Path, n: int = 400):
    """读文件尾 n 行（大文件不整读）。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            block = min(size, 256 * 1024)
            f.seek(max(0, size - block))
            data = f.read().decode("utf-8", "replace")
        return data.splitlines()[-n:]
    except Exception:
        return []


def _size_h(path: Path):
    try:
        n = path.stat().st_size
    except Exception:
        return 0, "-"
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return n, "{:.0f}{}".format(n, unit) if unit == "B" else "{:.1f}{}".format(n, unit)
        n /= 1024.0


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _fmt_ts(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _ago(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        return "-"
    if seconds < 0:
        return "（已过期 {}）".format(_ago(-seconds))
    if seconds < 60:
        return "{}秒".format(seconds)
    if seconds < 3600:
        return "{}分{}秒".format(seconds // 60, seconds % 60)
    return "{}小时{}分".format(seconds // 3600, (seconds % 3600) // 60)


# ---------------------------------------------------------------- 1) 进程与内存

def find_bot_pid():
    """在 /proc 里找 bot.py 进程（容器内看不到 docker inspect）。"""
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open("/proc/{}/cmdline".format(entry), "rb") as f:
                    cmd = f.read().decode("utf-8", "replace")
            except Exception:
                continue
            if "bot.py" in cmd and "--sync" not in cmd:
                return int(entry)
    except Exception:
        pass
    return None


def proc_info(pid):
    """(RSS 字节, 启动时刻 epoch 或 None)。"""
    rss, started = None, None
    try:
        with open("/proc/{}/status".format(pid), encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                    break
    except Exception:
        pass
    try:
        # /proc/<pid>/stat 的 comm 可能含空格/括号 → 从最后一个 ')' 之后开始切
        raw = Path("/proc/{}/stat".format(pid)).read_text(encoding="utf-8")
        fields = raw[raw.rfind(")") + 2:].split()
        starttime_ticks = int(fields[19])          # 第 22 字段（从 1 数）
        hz = os.sysconf("SC_CLK_TCK")
        btime = 0
        with open("/proc/stat", encoding="utf-8") as f:
            for line in f:
                if line.startswith("btime"):
                    btime = int(line.split()[1])
        started = btime + starttime_ticks / float(hz)
    except Exception:
        pass
    return rss, started


def meminfo():
    out = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                if k in ("MemTotal", "MemAvailable"):
                    out[k] = int(v.split()[0]) * 1024
    except Exception:
        pass
    return out


def section_process():
    pid = find_bot_pid()
    rss, started = proc_info(pid) if pid else (None, None)
    mi = meminfo()
    return {
        "bot_running": pid is not None,
        "bot_pid": pid,
        "bot_rss": rss,
        "bot_started_at": _fmt_ts(started) if started else "-",
        # 原始 epoch：配置编辑页拿它判断"配置文件是否比进程新"（＝改动未生效）
        "bot_started_epoch": started,
        "bot_uptime": _ago(time.time() - started) if started else "-",
        "self_pid": os.getpid(),
        "self_rss": proc_info(os.getpid())[0],
        "self_uptime": _ago(time.time() - _STARTED),
        "mem_total": mi.get("MemTotal"),
        "mem_available": mi.get("MemAvailable"),
        "dashboard_version": VERSION,
    }


# ---------------------------------------------------------------- 2) 启动自证行

def section_boot_proof():
    lines = _tail(BOT_LOG, 400)
    out = {m: None for m in PROOF_MARKERS}
    for ln in lines:
        for m in PROOF_MARKERS:
            if m in ln:
                out[m] = ln.strip()
    return {
        "lines": out,
        "all_present": all(out.values()),
        "bot_log_lines": len(lines),
        "bot_log_last": lines[-1].strip() if lines else "",
    }


# ---------------------------------------------------------------- 3) 登录态

COOKIE_KEYS = ("sb-rls-auth-token", "sb-rls-refresh-token")


def section_auth():
    tok = _read_json(TOKEN_FILE) or {}
    cookies = tok.get("cookies") or []
    now = time.time()
    rows = []
    for c in cookies:
        if c.get("name") not in COOKIE_KEYS:
            continue
        exp = c.get("expires")
        try:
            exp = float(exp) if exp else None
        except Exception:
            exp = None
        rows.append({
            "name": c.get("name"),
            "expires_at": _fmt_ts(exp) if exp else "会话级",
            "remain": _ago(exp - now) if exp else "-",
            "expired": bool(exp and exp < now),
            "len": len(c.get("value") or ""),
        })
    renew = [ln.strip() for ln in _tail(RENEW_LOG, 60) if ln.strip()][-6:]
    return {
        "token_file_present": TOKEN_FILE.exists(),
        "cookies": rows,
        "session_version": _read_json(SESSION_VERSION_FILE),
        "renew_fail": _read_json(ROOT / "renew_fail_state.json"),
        "renew_emergency": _read_json(ROOT / "renew_emergency_state.json"),
        "renew_tail": renew,
        "bot_paused": (ROOT / "bot.paused").exists(),
    }


# ---------------------------------------------------------------- 4) 出站队列

def _ro_conn(path: Path):
    """只读打开。URI 里带 mode=ro，物理上写不进去。"""
    return sqlite3.connect("file:{}?mode=ro".format(path), uri=True, timeout=3)


def section_outbox():
    if not OUTBOX_DB.exists():
        return {"present": False}
    with _ro_conn(OUTBOX_DB) as c:
        by_status = dict(c.execute(
            "select status, count(*) from outbound group by status").fetchall())
        retried = c.execute(
            "select count(*) from outbound where attempt_count > 1").fetchone()[0]
        total = c.execute("select count(*) from outbound").fetchone()[0]
        # 有 last_error 的行最值得看：那是"发失败过"的直接证据
        with_err = c.execute(
            "select count(*) from outbound where last_error is not null "
            "and last_error != ''").fetchone()[0]
        recent = c.execute(
            "select status, attempt_count, substr(text, 1, 40), rowid, "
            "coalesce(last_error, '') from outbound order by rowid desc limit 8"
        ).fetchall()
        pending = c.execute(
            "select count(*) from outbound where status in ('pending','leased')"
        ).fetchone()[0]
        oldest = c.execute(
            "select min(created_at) from outbound "
            "where status in ('pending','leased')").fetchone()[0]
        errs = c.execute(
            "select substr(text, 1, 30), last_error from outbound "
            "where last_error is not null and last_error != '' "
            "order by rowid desc limit 5").fetchall()
        # §2.2.4：每群最近一次出站活动（发出优先，没发过就取入队时刻）。
        # 有 (chatroom_id, created_at) 索引，group by 走得动；只读连接。
        last_by_cid = {r[0]: r[1] for r in c.execute(
            "select chatroom_id, max(coalesce(sent_at, created_at)) "
            "from outbound group by chatroom_id")}
    return {
        "present": True,
        "by_status": by_status,
        "total": total,
        "retried": retried,
        "with_error": with_err,
        "pending_or_leased": pending,
        "oldest_pending_age": _ago(time.time() - oldest) if oldest else "-",
        "recent": [{"status": r[0], "attempts": r[1], "text": r[2], "rowid": r[3],
                    "last_error": r[4][:120]} for r in recent],
        "errors": [{"text": e[0], "error": (e[1] or "")[:160]} for e in errs],
        "size": _size_h(OUTBOX_DB)[1],
        "last_activity": last_by_cid,
    }


# ---------------------------------------------------------------- 5) 最近错误

def section_errors(limit=15):
    out = {}
    for name, path in (("bot.log", BOT_LOG), ("check_health.log", HEALTH_LOG),
                       ("renew_token.log", RENEW_LOG)):
        hits = []
        for ln in _tail(path, 500):
            low = ln.lower()
            if not any(m in low for m in ERROR_MARKERS):
                continue
            if any(x in ln for x in ERROR_EXCLUDE):
                continue
            hits.append(ln.strip()[:200])
        out[name] = {"count": len(hits), "recent": hits[-limit:]}
    out["_note"] = ("只认真正的失败信号（traceback/exception/❌…）；"
                    "「XX超时，自动恢复」「告警已发」这类正常自愈与通知不算错误")
    return out


# ---------------------------------------------------------------- 6) prompt 与存储

def section_prompts():
    """prompt 外置的生效情况。

    ⚠️ `stats()` 反映的是**当前进程**的 import 状态：`_sources` 是在 `brains`
    import 时通过 `_p(...)` 注册进去的。后台是**独立进程**，不 import 就等于
    这里永远返回 `0/0` —— 一个"看着正常但毫无意义"的数字。
    这是 P9 上线前在生产镜像里预检时抓到的（本地测试当时只看了字段存在，没看数值）。
    """
    import brains  # noqa: F401  仅为触发 prompt 注册（无网络副作用）
    import runtime.memory  # noqa: F401  P12-G：第 40 个提示词的持有模块
    import runtime.prompts as prompts
    st = prompts.stats()
    if not st.get("total"):
        st["warning"] = "prompts 注册为空（brains 未成功 import）—— 下面的数字不可信"
    elif st.get("files_on_disk") != st.get("total"):
        # P12-G：这一条正是这一段存在的意义 —— "文件与登记册对不上"就是
        # "有人加了文件但没人读"（或反之），静默漂移最难发现。
        st["warning"] = ("prompts/ 里 {} 个文件，登记册里 {} 个 —— "
                         "有文件没人读或反之").format(
                             st.get("files_on_disk"), st.get("total"))
    st["sample"] = sorted(prompts.sources().items())[:5]
    return st


def section_storage():
    out = {}
    for name, p in (("store.db", STORE_DB), ("outbox.db", OUTBOX_DB)):
        out[name] = {
            "size": _size_h(p)[1],
            "wal": _size_h(Path(str(p) + "-wal"))[1],
            "shm": _size_h(Path(str(p) + "-shm"))[1],
            "present": p.exists(),
        }
    return out


# ---------------------------------------------------------------- 7) 群状态

def section_groups():
    """每个群的玩法状态概览。**只读**。

    刻意**不**用 `state_store.load_state()`：那个入口是读写打开，
    库不存在时会**建库** —— 一个"运维后台"顺手改动了线上存储是最典型的翻车方式。
    这里直接以 `mode=ro` 读 `kv` 表（schema 由 `runtime/store.py` 定义，
    结构漂移由 `tests/test_admin_dashboard.py` 比对 `_SCHEMA` 兜住）。
    """
    if not STORE_DB.exists():
        return {"present": False}
    with _ro_conn(STORE_DB) as c:
        rows = c.execute(
            "select key, value from kv where scope = ?", (SCOPE_GROUP,)).fetchall()
    groups = {}
    active = {
        "fasao": lambda g: bool((g.get("fasao") or {}).get("active")),
        "diaojiao": lambda g: bool((g.get("diaojiao") or {}).get("active")),
        "perf": lambda g: bool((g.get("perf") or {}).get("active")),
        "vote": lambda g: bool((g.get("vote") or {}).get("active")),
        "story": lambda g: bool((g.get("story") or {}).get("active")),
    }
    dice = None
    for key, value in rows:
        try:
            g = json.loads(value)
        except Exception:
            continue
        if not isinstance(g, dict):
            continue
        if key == "dice_game":
            dice = {"active": bool(g.get("active")), "phase": g.get("phase"),
                    "players": len(g.get("players") or [])}
            continue
        heat = g.get("heat_value") or {}
        top = sorted(heat.items(), key=lambda kv: -kv[1])[:3]
        groups[key] = {
            "buckets": sorted(k for k in g if k != "heat_value"),
            "heat_people": len(heat),
            "heat_top": top,
            "playing": [k for k, f in active.items() if f(g)],
            "memory": len(g.get("memory") or []),
        }
    return {
        "present": True,
        "primary": _primary_cid(),
        "group_count": len(groups),
        "groups": groups,
        "dice": dice,
    }


def _primary_cid():
    try:
        from runtime.ctx import PRIMARY_CID
        return PRIMARY_CID
    except Exception:
        return ""


# ---------------------------------------------------------------- 8) 群配置

def section_config():
    """群清单 / Bot 注册表 —— **显示真正生效的那一份**。

    P11 A 期之后，真源是 `config.json`（不存在则退回 `bot.py` 里的 `_*_DEFAULT`）。
    所以这里不再 AST 抓 `GROUPS` 之类（那些名字现在是运行期算出来的），
    而是：AST 抓 `_*_DEFAULT` 字面量当兜底，再交给 `runtime.config_store` 解析 ——
    于是页面上看到的就是**启动时会读到的那一份**（含 source=file/defaults）。

    仍然**不 import bot**：那会把 playwright 一起拉起来（几秒 + 若干内存）。
    每次都用 `reload` 而不是 `load`，这样在文件改动之后、重启之前，
    面板就能先看到"重启后会生效的内容"。

    ⚠️ set 要转成 **list**：JSON 没有 set，`json.dumps(default=str)` 会把它变成
    `"{'a', 'b'}"` 这样的字符串 —— 长度、内容全错，而且**看起来还挺像那么回事**。
    第一次就是这么把 `SCREEN_CIDS`（3 个群）显示成长度 120 的。
    """
    import ast
    from runtime import config_store

    out = {"defaults_source": str(ROOT / "bot.py"), "unresolved": []}
    try:
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": "解析 bot.py 失败: {}".format(e)}
    wanted = {"_GROUPS_DEFAULT": "groups", "_MUTE_CIDS_DEFAULT": "mute",
              "_SCREEN_CIDS_DEFAULT": "screen", "_PUSH_CIDS_DEFAULT": "push",
              "_GROUP_ALIASES": "aliases", "_BOTS_DEFAULT": "bots"}
    defaults = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name) or tgt.id not in wanted:
            continue
        try:
            defaults[wanted[tgt.id]] = ast.literal_eval(node.value)
        except Exception:
            # 例如 `ROOM_CIDS = set(ROOMS.keys())` —— 是调用不是字面量。
            # 老实报告"没求值"并给出表达式，不要装作读到了。
            out["unresolved"].append(
                {"name": tgt.id, "expr": ast.unparse(node.value)[:80]})

    defaults.setdefault("personas", ())
    # `_BOTS_DEFAULT` 是 `config_store.default_bots_from_env(...)` 的调用（不是字面量），
    # AST 求不出来 —— 那就**照 bot.py 同样的方式**从环境变量补一份，
    # 而不是让面板在"没有 config.json"时显示 0 个 Bot。
    if "bots" not in defaults:
        _g = defaults.get("groups") or [""]
        defaults["bots"] = config_store.default_bots_from_env(_g[0])
    try:
        cfg = config_store.reload(defaults)
    except Exception as e:                                      # noqa: BLE001
        out["error"] = "配置不合法：{}".format(e)
        return out

    out["source"] = cfg.source            # "file" | "defaults"
    out["path"] = cfg.path
    # P12-C：schema 版本与"是不是刚升上来的"，面板上要看得见
    out["schema_version"] = config_store.SCHEMA_VERSION
    out["migrated_from"] = cfg.migrated_from
    out["feature_overrides"] = len(cfg.feature_map())
    out["param_overrides"] = len(cfg.tuning_map())
    out["scopes"] = {k: list(v) for k, v in (cfg.scopes or {}).items()}
    # Docker 在 bind mount 的源文件不存在时会建一个**目录** —— 那种情况下
    # config_store 读不到配置（静默退回默认值），而界面写它会失败。
    # 这种"看起来配了、其实没配"的状态必须显式报出来。
    try:
        _p = Path(cfg.path)
        out["path_is_dir"] = _p.is_dir()
    except Exception:                                          # noqa: BLE001
        out["path_is_dir"] = False
    out["note"] = ("source=defaults 表示没有 config.json，用的是 bot.py 里的默认值；"
                   "功能/参数/人设改完会自动热加载，**群与 Bot 需要重启容器**")
    if out["path_is_dir"]:
        out["note"] = ("⚠️ {} 存在但**是个目录**（bind mount 的源文件当初不存在，"
                       "Docker 建了目录）：配置其实没生效。删掉这个目录、"
                       "用 tools/config_gen.py 生成文件再重启。".format(cfg.path))
    out["GROUPS"] = list(cfg.groups)
    out["MUTE_CIDS"] = sorted(cfg.mute)
    out["SCREEN_CIDS"] = sorted(cfg.screen)
    out["PUSH_CIDS"] = list(cfg.push)
    out["group_count"] = len(cfg.groups)          # **生效**的群数
    out["configured_count"] = len(cfg.group_specs)  # 配置文件里写了几个群
    out["muted_count"] = len(cfg.mute)
    out["screen_count"] = len(cfg.screen)
    out["push_count"] = len(cfg.push)
    # 「这个群走哪个 Bot」从 bots 侧**派生**，不在群上重复存一份
    # （两个真源迟早不一致）
    cid2bot = {b.cid: b.key for b in cfg.bots}
    # §2.2.4：每群**功能摘要**——启用计数 + 与代码默认不同的生效项。
    # 生效值三级回退：本群覆盖 > 全局覆盖 > 注册表默认。
    # runtime.features 是纯声明式注册表（os/time/dataclasses，无 playwright
    # 无网络副作用），import 它不违反本函数"不 import bot"的纪律。
    from runtime import features as _fm
    _reg = {f.key: f for f in _fm.FEATURES}
    _glob = cfg.features or {}
    # 列出**全部**配置项（含 enabled=false 的）：面板要能看到"我关掉了哪些群"，
    # 只列生效的会让被关掉的群在界面上凭空消失（配置界面踩过这个坑）。
    out["groups"] = []
    for s in cfg.group_specs.values():
        n_on, diffs = 0, []
        for k, f in _reg.items():
            ov = (s.features or {}).get(k)
            gv = _glob.get(k)
            eff = ov if ov is not None else (gv if gv is not None else f.default)
            if eff:
                n_on += 1
            if eff != f.default:
                diffs.append({"key": k, "label": f.label, "on": bool(eff)})
        out["groups"].append(
            {"cid": s.cid, "alias": s.alias, "enabled": s.enabled,
             "mute": s.mute, "screen": s.screen, "push": s.push,
             "persona": s.persona, "active": s.cid in cfg.groups,
             "bot": cid2bot.get(s.cid, ""),
             "feat_on": n_on, "feat_total": len(_reg), "feat_diffs": diffs})
    out["bots"] = [
        {"key": b.key, "name": b.name, "short": b.short, "cid": b.cid,
         "enabled": b.enabled, "token_env": b.token_env,
         # token 只报"有没有"，**绝不回显值**
         "token_set": bool(b.resolve_token())}
        for b in cfg.bots]

    # 警告：这些是"看起来配了、其实有问题"的状态，必须显式报出来
    warns = []
    if out.get("path_is_dir"):
        warns.append("config.json 是个**目录**而不是文件 —— 配置没生效")
    if cfg.source == "defaults":
        warns.append("没有 config.json，正在用 bot.py 里的默认值")
    env_name = (os.environ.get("DZMM_BOT_NAME") or "").strip()
    for b in cfg.bots:
        if b.token_env and not (os.environ.get(b.token_env) or "").strip():
            warns.append("Bot {} 的 token_env={} 在环境里是空的".format(
                b.key, b.token_env))
        # P11 A 期的**过渡态**：Bot 名字暂时有两个来源（identity.py 读 env、
        # config.json 里也有一份）。本期 config 还不生效，但两边不一致时
        # 必须看得见 —— 否则第 B 期"改走 config"的那一刻会突然换成另一个名字。
        if env_name and b.name and b.name != env_name:
            warns.append(
                "Bot {} 的名字不一致：config={!r} 而 env DZMM_BOT_NAME={!r}"
                "（A 期以 env 为准；B 期起以 config 为准）".format(
                    b.key, b.name, env_name))
    out["warnings"] = warns
    return out


def _hot_load_json():
    """热加载状态（P12-F）：`/api/summary` 里带一份，验收脚本可以机械地断言。

    只放**能公开**的东西（时间/次数/改动条数/是否需要重启），
    不放人设原文 —— 那份内容在配置页上，不该从只读接口漏出去。
    """
    try:
        from runtime import hotload
        defaults = _config_defaults()
        hotload.set_defaults(defaults)
        st = hotload.page_state(defaults) or {}
        return {
            "enabled": st.get("enabled"),
            "configured": hotload.configured(),
            "count": st.get("count") or 0,
            "at": st.get("at_text") or "",
            "error": st.get("error") or "",
            "invalid": bool(st.get("invalid")),
            "pending": bool(st.get("pending")),
            "out_of_process": bool(st.get("out_of_process")),
            "last_source": st.get("source") or "",
            "last_changes": sum(len(v or [])
                                for v in (st.get("last_changes") or {}).values()),
            "structural": st.get("structural") or [],
        }
    except Exception as e:                                      # noqa: BLE001
        return {"error": "{}: {}".format(type(e).__name__, e)}


SECTIONS = [
    ("process", "进程与内存", section_process),
    ("boot", "启动自证行", section_boot_proof),
    ("auth", "登录态与续期", section_auth),
    ("outbox", "出站队列", section_outbox),
    ("errors", "最近错误", section_errors),
    ("prompts", "prompt 外置", section_prompts),
    ("storage", "存储与 WAL", section_storage),
    ("groups", "群状态", section_groups),
    ("config", "群配置", section_config),
    ("hotload", "配置热加载", _hot_load_json),
]


def collect():
    """跑所有 section。**每段单独 try** —— 一段坏了不该让整页变白。"""
    result = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "version": VERSION,
              "hotload": _hot_load_json(),
              "sections": {}}
    for key, title, fn in SECTIONS:
        t0 = time.time()
        try:
            result["sections"][key] = {"title": title, "ok": True, "data": fn()}
        except Exception as e:                                  # noqa: BLE001
            result["sections"][key] = {"title": title, "ok": False,
                                       "error": "{}: {}".format(type(e).__name__, e)}
        result["sections"][key]["ms"] = int((time.time() - t0) * 1000)
    return result


# ---------------------------------------------------------------- 渲染

# P4-4A：总览页样式已删（壳走 admin.shell.page，卡片走共享 .card/.section-grid，
# 状态色走共享 badge--/text-* 变体）。登录页**不进壳**——/static 要登录才能
# 拉到（do_GET 里会话检查在 static 之前），所以它必须自包含样式。
_LOGIN_CSS = """
body{font:14px/1.6 -apple-system,Segoe UI,Roboto,'Noto Sans CJK SC',sans-serif;
margin:0;background:#12141a;color:#d8dee9}
.box{max-width:330px;margin:12vh auto;background:#181c25;
border:1px solid #262c39;border-radius:9px;padding:20px}
h1{font-size:15px;margin:0 0 4px}.sub{color:#6c7686;font-size:12px}
label{display:block;color:#8b95a7;margin:10px 0 4px}
input{width:100%;box-sizing:border-box;background:#0f1218;color:#d8dee9;
border:1px solid #2b3140;border-radius:6px;padding:8px;font-size:14px}
button{width:100%;margin-top:16px;padding:9px;border:0;border-radius:6px;
background:#2f6feb;color:#fff;font-size:14px;cursor:pointer}
.msg{padding:8px 10px;border-radius:6px;margin-bottom:10px;font-size:12px}
.msg--bad{background:#4a1f27;color:#ff8b9a}
.msg--ok{background:#1f3d2b;color:#7ee2a8}
"""


def _e(x):
    return html.escape(str(x))


def _kv_table(d, skip=()):
    rows = []
    for k, v in d.items():
        if k in skip:
            continue
        if isinstance(v, (dict, list, tuple)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append("<tr><th>{}</th><td>{}</td></tr>".format(_e(k), _e(v)))
    return "<table class='table table--compact'>{}</table>".format("".join(rows))


def _list_block(items, cls="mono"):
    if not items:
        return '<span class="text-dim">（无）</span>'
    return "<pre>{}</pre>".format(_e("\n".join(str(i) for i in items)))


def render_html(data):
    secs = data.get("sections") or {}

    def sec(key):
        """安全取段数据：段缺失 / 那段坏了都给 {}（一坏不白）。"""
        s = secs.get(key) or {}
        return (s.get("data") or {}) if s.get("ok", True) else {}

    def body(key):
        s = secs.get(key)
        if not s:
            return '<span class="text-dim">（缺）</span>'
        if not s["ok"]:
            return '<span class="text-bad">{}</span>'.format(_e(s.get("error")))
        d = s["data"]
        if key == "process":
            bad = not d["bot_running"]
            return ('<span class="badge {}">{}</span>'.format(
                        "badge--bad" if bad else "badge--ok",
                        "bot.py 未在运行" if bad else "bot.py 运行中")
                    + _kv_table(d, skip=("bot_running",)))
        if key == "boot":
            badge = ('<span class="badge {}">{}/{} 自证行</span>'.format(
                "badge--ok" if d["all_present"] else "badge--bad",
                sum(1 for v in d["lines"].values() if v), len(d["lines"])))
            rows = "".join(
                "<tr><th>{}</th><td class='mono {}'>{}</td></tr>".format(
                    _e(k), "" if v else "text-bad", _e(v or "（缺）"))
                for k, v in d["lines"].items())
            return badge + "<table class='table table--compact'>{}</table>".format(rows)
        if key == "auth":
            parts = []
            if d.get("bot_paused"):
                parts.append('<span class="badge badge--bad">bot.paused 存在（已暂停）</span>')
            rows = "".join(
                "<tr><td class='mono'>{}</td><td>{}</td><td class='{}'>{}</td></tr>".format(
                    _e(c["name"]), _e(c["expires_at"]),
                    "text-bad" if c["expired"] else "", _e(c["remain"]))
                for c in d.get("cookies") or [])
            parts.append("<table class='table table--compact'>"
                         "<tr><th>cookie</th><th>到期</th><th>剩余</th></tr>{}</table>"
                         .format(rows or "<tr><td colspan=3 class='text-dim'>没读到 token cookie</td></tr>"))
            parts.append("<div class='text-dim'>session_version: <span class='mono'>{}</span></div>"
                         .format(_e(json.dumps(d.get("session_version"), ensure_ascii=False))))
            parts.append("<div class='text-dim'>续期日志尾部</div>" + _list_block(d.get("renew_tail")))
            return "".join(parts)
        if key == "outbox":
            if not d.get("present"):
                return '<span class="badge badge--warn">outbox.db 不存在</span>'
            head = ('<span class="badge {}">pending/leased {}</span>'
                    '<span class="badge {}">重试&gt;1 {}</span>'
                    '<span class="badge {}">有 last_error {}</span>'
                    '<span class="text-dim"> 共 {} 条 / {} / 最老待发 {}</span>').format(
                "badge--warn" if d["pending_or_leased"] else "badge--ok", d["pending_or_leased"],
                "badge--bad" if d["retried"] else "badge--ok", d["retried"],
                "badge--bad" if d["with_error"] else "badge--ok", d["with_error"],
                d["total"], d["size"], d["oldest_pending_age"])
            rows = "".join(
                "<tr><td>{}</td><td>{}</td><td class='mono'>{}</td><td class='text-bad mono'>{}</td></tr>"
                .format(_e(r["status"]), _e(r["attempts"]), _e(r["text"]),
                        _e(r["last_error"]))
                for r in d["recent"])
            errs = ""
            if d.get("errors"):
                errs = "<div class='text-dim'>发失败过的记录</div>" + _list_block(
                    ["{} ← {}".format(e["text"], e["error"]) for e in d["errors"]])
            return (head + "<table class='table table--compact'><tr><th>status</th><th>次数</th><th>文本</th>"
                    "<th>last_error</th></tr>{}</table>".format(rows) + errs
                    + "<div class='text-dim'>{}</div>".format(_e(d["by_status"])))
        if key == "errors":
            parts = []
            # 跳过 `_note` 这类以下划线开头的说明字段（不是日志名）
            files = {k: v for k, v in d.items() if not k.startswith("_")}
            for name, v in files.items():
                cls = "badge--warn" if v["count"] else "badge--ok"
                parts.append('<span class="badge {}">{}: {}</span> '.format(
                    cls, _e(name), v["count"]))
            for name, v in files.items():
                if v["recent"]:
                    parts.append("<div class='text-dim'>{}</div>{}".format(
                        _e(name), _list_block(v["recent"])))
            if d.get("_note"):
                parts.append("<div class='text-dim'>{}</div>".format(_e(d["_note"])))
            return "".join(parts)
        if key in ("prompts", "storage", "config", "groups"):
            if key == "groups":
                rows = "".join(
                    "<tr><td class='mono'>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>"
                    .format(_e(cid[:8]), _e(len(g["buckets"])), _e(g["heat_people"]),
                            _e(",".join(g["playing"]) or "-"),
                            _e(", ".join("{}={}".format(u[:6], s) for u, s in g["heat_top"])))
                for cid, g in sorted((d.get("groups") or {}).items()))
                return ("<div class='text-dim'>共 {} 个群；主群 {}</div>"
                        "<table class='table table--compact'>"
                        "<tr><th>群</th><th>状态键</th><th>发情人数</th>"
                        "<th>进行中</th><th>发情值 top3</th></tr>{}</table>"
                        "<div class='text-dim'>骰子局: {}</div>"
                        .format(d.get("group_count", 0), _e((d.get("primary") or "")[:8]),
                                rows, _e(json.dumps(d.get("dice"), ensure_ascii=False))))
            return _kv_table(d)
        return _kv_table(d)

    # ---------------------------------------------------------------
    # §2.2 信息层级（P4-4B）：告警条 → 状态卡格 → 快捷操作+启动自证 →
    # 群状态表 → 详细监控（折叠）。原 9 段的逐段渲染 body(key) 原样保留，
    # 收进折叠区，细节不丢。
    # ---------------------------------------------------------------

    process = sec("process")
    auth = sec("auth")
    outbox = sec("outbox")
    config = sec("config")
    boot = sec("boot")
    hot = data.get("hotload") or {}

    # ---- 1) 告警条：只有异常时才出现（设计文档 §2.2.1）----
    alerts = []
    if process and not process.get("bot_running", True):
        alerts.append(("bad", "bot.py 未在运行 —— 群里已经没有人在回消息"))
    if hot.get("invalid"):
        alerts.append(("bad", "热加载：磁盘上的配置不合法（运行期保持上一次成功的）"))
    if auth.get("bot_paused"):
        alerts.append(("warn", "bot.paused 存在 —— 机器人已暂停"))
    exp_cookies = [c.get("name") or "?" for c in (auth.get("cookies") or [])
                   if c.get("expired")]
    if exp_cookies:
        alerts.append(("bad", "token 已过期：{}".format("、".join(exp_cookies))))
    if config.get("warnings"):
        for w in config["warnings"]:
            alerts.append(("warn", w))
    if boot and boot.get("lines") and not boot.get("all_present"):
        missing = [k for k, v in (boot.get("lines") or {}).items() if not v]
        alerts.append(("warn", "启动自证行不全：缺 {}".format("、".join(missing))))
    if outbox.get("present") and outbox.get("with_error"):
        alerts.append(("warn", "出站队列有 {} 条发失败过的记录".format(
            outbox["with_error"])))
    alert_html = "".join(
        "<div class='alert-banner alert-banner--{lv}'>"
        "<span class='alert-banner__icon'>{ic}</span><span>{tx}</span></div>".format(
            lv=lv, ic="⛔" if lv == "bad" else "⚠️", tx=_e(tx))
        for lv, tx in alerts)

    # ---- 2) 状态卡格（4 张 stat-card）----
    def stat(label, value, sub, cls=""):
        return ("<div class='card stat-card {cls}'>"
                "<span class='stat-card__label'>{l}</span>"
                "<span class='stat-card__value'>{v}</span>"
                "<span class='stat-card__sub'>{s}</span></div>").format(
            cls=cls, l=_e(label), v=value, s=_e(sub))

    bot_running = process.get("bot_running")
    if bot_running is None:
        stat_process = stat("容器 / 进程", "—", "读不到进程信息")
    else:
        stat_process = stat(
            "容器 / 进程", "运行中" if bot_running else "未运行",
            "PID {pid} · 内存 {rss} · 已运行 {up}".format(
                pid=process.get("bot_pid") or "-",
                rss=process.get("bot_rss") or "-",
                up=process.get("bot_uptime") or "-"),
            "" if bot_running else "stat-card--bad")

    cookies = auth.get("cookies") or []
    n_ok = sum(1 for c in cookies if not c.get("expired"))
    if not cookies:
        stat_auth = stat("登录态", "—", "没读到 token cookie", "stat-card--warn")
    else:
        stat_auth = stat(
            "登录态", "{}/{} 有效".format(n_ok, len(cookies)),
            "续期失败 {}".format(
                "是" if auth.get("renew_fail") else "无")
            if auth.get("renew_fail") is not None else "—",
            "" if n_ok == len(cookies) else "stat-card--bad")

    bots = config.get("bots") or []
    token_missing = [b.get("key") for b in bots if not b.get("token_set")]
    stat_groups = stat(
        "群与 Bot", "{} 群 / {} Bot".format(
            config.get("group_count", "-"), len(bots)),
        "配置来源 {src} · 覆盖：功能 {f} 参数 {p}{tok}".format(
            src=config.get("source") or "-",
            f=config.get("feature_overrides", 0),
            p=config.get("param_overrides", 0),
            tok="" if not token_missing else
            " · ⚠️ token 未设：{}".format("、".join(token_missing))),
        "stat-card--warn" if (config.get("source") == "defaults"
                              or token_missing) else "")

    pending = outbox.get("pending_or_leased")
    stat_outbox = stat(
        "出站队列", str(pending) if pending is not None else "—",
        "重试>1 {r} · 有错误 {e} · 共 {t} 条".format(
            r=outbox.get("retried", "-"), e=outbox.get("with_error", "-"),
            t=outbox.get("total", "-")),
        "stat-card--bad" if (outbox.get("with_error") or 0) else
        ("stat-card--warn" if (pending or 0) else ""))
    stat_cards = ("<div class='status-grid'>{}</div>").format(
        stat_process + stat_auth + stat_groups + stat_outbox)

    # ---- 3) 快捷操作 + 启动自证（双列）----
    # 本面板**只读**：唯一的写操作（热加载）在配置页，这里不放假按钮。
    shortcuts = (
        "<div class='card'><div class='card__header'>"
        "<span class='card__title'>快捷操作</span>"
        "<span class='card__meta'>全部写入操作在配置页</span></div>"
        "<div class='card__body'><div class='flex flex-col' style='gap:8px'>"
        "<a class='btn btn--primary' href='/features'>🎚️ 功能与参数（保存即热加载）</a>"
        "<a class='btn btn--ghost' href='/edit'>👥 群与 Bot · 备份回滚</a>"
        "<a class='btn btn--ghost' href='/personas'>🎭 人设与话术</a>"
        "<a class='btn btn--ghost' href='/prompts'>📝 文案模板</a>"
        "<a href='/api/summary'>JSON（/api/summary）</a> · "
        "<a href='/healthz'>healthz</a>"
        "</div></div></div>")
    proof = (
        "<div class='card'><div class='card__header'>"
        "<span class='card__title'>启动自证</span>"
        "<span class='card__meta'>{done}</span></div>"
        "<div class='card__body card__body--compact'>{table}</div></div>").format(
        done="{} / {} 行".format(
            sum(1 for v in (boot.get("lines") or {}).values() if v),
            len(boot.get("lines") or {})) if boot else "—",
        table=body("boot"))
    duo = ("<div class='section-grid'>{}</div>").format(shortcuts + proof)

    # ---- 4) 群状态表（以群为主视图，§2.2.4：功能摘要 + 最近活动）----
    gstate = sec("groups")
    outbox_sec = secs.get("outbox") or {}
    last_act = (outbox_sec.get("data") or {}).get("last_activity") or {}
    g_rows = []
    for g in (config.get("groups") or []):
        cid = g.get("cid") or ""
        st = (gstate.get("groups") or {}).get(cid) or {}
        playing = _e("、".join(st.get("playing") or []) or "—")
        # 功能摘要：启用计数 + 与默认不同的项（超出 4 个折叠成 "+n"）
        diffs = g.get("feat_diffs") or []
        summary = ("<span class='badge badge--{}'>{}/{} 启用</span> ".format(
            "ok" if g.get("feat_on") else "bad",
            g.get("feat_on", "-"), g.get("feat_total", "-")))
        summary += "".join(
            "<span class='badge badge--{cls}' title='{k}'>{l} {s}</span> ".format(
                cls="bad" if not d["on"] else "ok",
                k=_e(d["key"]), l=_e(d["label"]), s="开" if d["on"] else "关")
            for d in diffs[:4])
        if len(diffs) > 4:
            summary += "<span class='text-dim'>+{} 项</span>".format(len(diffs) - 4)
        if not diffs:
            summary += "<span class='text-dim'>全部跟随默认</span>"
        # 最近活动：最近一次出站（发出优先，入队兜底）；没有出站记录就是"—"
        la = last_act.get(cid)
        last_txt = _ago(time.time() - la) if la else "—"
        g_rows.append(
            ("<tr class='{rowcls}'>"
             "<td class='feat'>{a}<br><span class='mono text-dim'>{c8}</span></td>"
             "<td>{active}</td>"
             "<td class='mono'>{persona}</td>"
             "<td class='mono'>{bot}</td>"
             "<td>{summary}</td>"
             "<td>{flags}</td>"
             "<td>{playing}</td>"
             "<td>{heat}</td>"
             "<td class='text-dim'>{last}</td>"
             "<td class='text-dim'>{nkeys} 键</td></tr>").format(
                rowcls="group-row" if g.get("active") else "",
                a=_e(g.get("alias") or cid[:8]), c8=_e(cid[:8]),
                active=("<span class='badge badge--ok'>生效</span>"
                        if g.get("active") else
                        "<span class='badge badge--bad'>停用</span>"),
                persona=_e(g.get("persona") or "（默认）"),
                bot=_e(g.get("bot") or "—"),
                summary=summary,
                flags="".join(
                    "<span class='badge badge--neutral'>{}</span> ".format(t)
                    for t, on_ in (("静默", g.get("mute")),
                                   ("筛查", g.get("screen")),
                                   ("推送", g.get("push"))) if on_)
                or "—",
                playing=playing,
                heat=_e(st.get("heat_people", "-") if st else "-"),
                last=_e(last_txt),
                nkeys=len(st.get("buckets") or []) if st else "-"))
    groups_table = (
        "<div class='card'><div class='card__header'>"
        "<span class='card__title'>群状态</span>"
        "<span class='card__meta'>配置 × 运行状态，以群为主视图</span></div>"
        "<div class='card__body'>"
        "<div class='table-wrap'><table class='table table--compact'>"
        "<tr><th>群</th><th>状态</th><th>人设</th><th>Bot 通道</th>"
        "<th>功能摘要</th><th>特殊开关</th><th>进行中玩法</th>"
        "<th>发情人数</th><th>最近活动</th><th>状态键</th></tr>"
        "{rows}</table></div>"
        "<div class='text-dim' style='margin-top:8px'>功能摘要 = 与代码默认不同的"
        "生效项（本群覆盖 / 全局覆盖都已折算）；最近活动 = 最近一次出站发送"
        "（不含入站消息）。玩法状态每轮变化，这里是只读快照。</div>"
        "</div></div>").format(
        rows="".join(g_rows) or "<tr><td colspan=10 class='text-dim'>（没读到配置）</td></tr>")

    # ---- 5) 详细监控（原 9 段收进折叠区）----
    detail_cards = []
    for key, title, _ in SECTIONS:
        s = secs.get(key, {})
        detail_cards.append(
            "<div class='card'><div class='card__header'>"
            "<span class='card__title'>{t}</span>"
            "<span class='card__meta'>{ms} ms{fail}</span></div>"
            "<div class='card__body card__body--scroll'>{b}</div></div>"
            .format(t=_e(title), ms=s.get("ms", "-"), b=body(key),
                    fail="" if s.get("ok", True) else " · 失败"))
    details = ("<details><summary>详细监控（原始各段：进程 / 自证 / 登录态 / "
               "队列 / 错误 / prompt / 存储 / 群玩法 / 配置 / 热加载）</summary>"
               "<div class='section-grid'>{}</div></details>").format(
        "".join(detail_cards))

    # P4-4A：页面外壳（侧边栏/header/主题切换/CSS）由 admin.shell 统一提供，
    # 这里只产出 content 片段。
    from admin import shell
    content = ("{alert_html}"
               "<div class='text-dim mb-2'>生成于 {at} · 只读面板 · "
               "<a href='/api/summary'>JSON</a> · <a href='/'>刷新</a></div>"
               "{stat_cards}{duo}{groups_table}{details}"
               "<footer class='footer'>{ver} · 只读面板 · "
               "所有数据均来自本机文件，页面不接受任何写入</footer>").format(
        alert_html=alert_html, at=_e(data.get("generated_at") or "-"),
        stat_cards=stat_cards, duo=duo, groups_table=groups_table,
        details=details, ver=_e(data.get("version") or VERSION))
    return shell.page(active="/", title="总览",
                      breadcrumb="监控 / 总览",
                      content=content)


def render_login(error="", notice="", locked_for=0, user=""):
    """登录页。**不带任何数据** —— 没登录的人不该看到一个字节的面板内容。

    P4-4A：唯一**不进壳**的页面——/static 要登录才能拉到（会话检查在
    static 路由之前），共享 CSS 帮不上它，样式必须自包含（_LOGIN_CSS）。
    """
    if locked_for > 0:
        msg = ('<div class="msg msg--bad">尝试次数过多，请 {} 后再试。</div>'
               .format(_ago(locked_for)))
    elif error:
        msg = '<div class="msg msg--bad">{}</div>'.format(_e(error))
    elif notice:
        msg = '<div class="msg msg--ok">{}</div>'.format(_e(notice))
    else:
        msg = ""
    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>登录 · 小小糯 运维后台</title><style>{css}</style></head>"
            "<body><div class='box'>"
            "<h1>小小糯 运维后台</h1>"
            "<div class='sub'>只读面板 · 请登录</div>"
            "{msg}"
            "<form method='POST' action='/login' autocomplete='off'>"
            "<label for='u'>账号</label>"
            "<input id='u' name='username' value='{user}' autocomplete='username' autofocus>"
            "<label for='p'>密码</label>"
            "<input id='p' name='password' type='password' autocomplete='current-password'>"
            "<button type='submit'>登录</button>"
            "</form></div></body></html>").format(css=_LOGIN_CSS, msg=msg, user=_e(user))


# ---------------------------------------------------------------- HTTP


# ---------------------------------------------------------------- 认证
#
# 原来是"URL 里带 ?token="，换成了正式登录。原因（都是实际问题）：
#   * 令牌会留在浏览器历史 / 访问日志 / 别人转发给你的链接里 —— 没法登出；
#   * 没有失败计数 -> 可以无限试；
#   * 它只是"持票入场"，不是"谁进来了"。
#
# 仍然**不落盘任何凭据**：账号密码从环境变量读（`.env`，root 600），
# 会话只存在本进程内存里 —— 重启即全部失效，这正好是我们想要的行为。

SESSION_COOKIE = "dzmm_admin_sid"
SESSION_TTL = 12 * 3600          # 12 小时
LOCK_AFTER = 5                   # 连续失败几次开始锁
LOCK_BASE = 300                  # 首次锁 5 分钟，之后翻倍
LOCK_MAX = 3600                  # 上限 1 小时
MAX_BODY = 4096                  # 登录表单体积上限
# 其它页面（人设/功能/文案模板）是**整页回传**：全部人设文本 + 全部话术 + 全部开关，
# URL 编码后中文一字占 9 字节。2026-09-27 实测人设页已经到 60KB+，
# 原来 `MAX_BODY * 16 = 64KB` 的线一碰就 400（报的还是"请求格式不对"，很难查）。
MAX_FORM_BODY = MAX_BODY * 256   # 1MB —— 留足余量，同时仍然拒绝异常大的请求

_sessions = {}                   # sid -> 过期时间戳
_fails = {}                      # ip -> [失败次数, 解锁时间戳]
_csrf_tokens = {}                # sid -> (过期时间戳, token)


def _cfg_user():
    return (os.environ.get("DZMM_ADMIN_USER") or "admin").strip()


def _cfg_password():
    return os.environ.get("DZMM_ADMIN_PASSWORD") or ""


def _creds_configured():
    return bool(_cfg_password())


def _truthy(v):
    return (v or "").strip().lower() in ("on", "1", "yes", "true")


def _client_ip(handler):
    return (handler.client_address[0] if handler.client_address else "?") or "?"


def _prune():
    now = time.time()
    for sid in [s for s, exp in _sessions.items() if exp <= now]:
        _sessions.pop(sid, None)
    for ip in [i for i, (_, until) in _fails.items() if until and until <= now
               and _fails[i][0] == 0]:
        _fails.pop(ip, None)


def lock_remaining(ip):
    rec = _fails.get(ip)
    if not rec:
        return 0
    return max(0, int(rec[1] - time.time()))


def note_failure(ip):
    """记一次失败并按次数加锁。返回剩余锁定时长（秒）。"""
    rec = _fails.get(ip) or [0, 0.0]
    rec[0] += 1
    if rec[0] >= LOCK_AFTER:
        over = rec[0] - LOCK_AFTER
        wait = min(LOCK_BASE * (2 ** over), LOCK_MAX)
        rec[1] = time.time() + wait
    _fails[ip] = rec
    # 上限保护：IP 数量不该无限增长（正常情况下安全组只放行一个 IP）
    if len(_fails) > 512:
        for k in sorted(_fails, key=lambda k: _fails[k][0])[:256]:
            _fails.pop(k, None)
    return lock_remaining(ip)


def clear_failures(ip):
    _fails.pop(ip, None)


def new_session():
    _prune()
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = time.time() + SESSION_TTL
    return sid


def session_valid(sid):
    if not sid:
        return False
    exp = _sessions.get(sid)
    if exp is None:
        return False
    if exp <= time.time():
        _sessions.pop(sid, None)
        return False
    return True


def drop_session(sid):
    _sessions.pop(sid, None)


def check_credentials(user, password):
    """账号密码校验。

    两处都用 `hmac.compare_digest` 且**都算完再比**：
    否则"账号对不对"会从耗时上泄漏出去（用户名枚举）。
    """
    want_user, want_pw = _cfg_user(), _cfg_password()
    if not want_pw:
        return False
    u_ok = hmac.compare_digest(str(user or ""), want_user)
    p_ok = hmac.compare_digest(str(password or ""), want_pw)
    return bool(u_ok and p_ok)


def _cookie_sid(handler):
    raw = handler.headers.get("Cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == SESSION_COOKIE and v:
            return v
    return ""


# ---------------------------------------------------------------- 配置编辑（唯一允许写的地方）
#
# 只读面板的那些 section 仍然**一处写操作都没有**；写只允许走 /edit，
# 而且只写 config.json 一个文件（+ 它的备份 + 审计日志）。
# 边界这么划是为了让"只读"这条性质仍然可验证：源码扫描那两条测试
# （每处 sqlite3.connect 都带 mode=ro、不得出现写库语句）继续成立。

def _hot_status():
    """运行期生效状态（P12-F），**两个进程都能用**：

    - 面板进程（生产就是这个）→ `hotload` 没接管运行期，`page_state()` 会读
      机器人写的 `config/hotload_status.json`；
    - 万一面板和机器人同进程 → 直接读内存。

    返回 None ⇒ 页面退回旧的"要重启"提示（宁可保守，也不能谎报已生效）。
    """
    try:
        from runtime import hotload
        defaults = _config_defaults()
        hotload.set_defaults(defaults)      # 只给解析用；不接管应用
        return hotload.page_state(defaults)
    except Exception:                                           # noqa: BLE001
        return None


def _persona_effective(raw):
    """每一群"留空时实际会用哪个人设"（来自代码的 ROOMS/默认）。

    只在**这一格是空的**时候才有意义 —— 页面上把它放进 placeholder，
    免得"空着"变成"不知道会怎样"。
    """
    try:
        from personas import persona_key_for
        return {str(g.get("cid")): persona_key_for(str(g.get("cid")))
                for g in (raw.get("groups") or []) if g.get("cid")}
    except Exception:                                           # noqa: BLE001
        return {}


def _persona_names():
    """可用的人设名（唯一真源 = `personas.PERSONAS`）。

    页面上要把它列出来，否则用户只能靠猜；而**猜错的名字会被静默回落成默认人设**
    （看起来配了、其实没生效）。校验也用这一份，见 `_config_defaults()`。
    """
    try:
        from personas import PERSONAS
        return tuple(PERSONAS)
    except Exception:                                           # noqa: BLE001
        return ()


def _config_defaults():
    """给 config_store 用的默认值：AST 取 `_*_DEFAULT`，不 import bot。"""
    import ast
    from runtime import config_store
    out = {"personas": _persona_names(), "unresolved": []}
    try:
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    except Exception as e:                                      # noqa: BLE001
        out["error"] = "解析 bot.py 失败: {}".format(e)
        return out
    wanted = {"_GROUPS_DEFAULT": "groups", "_MUTE_CIDS_DEFAULT": "mute",
              "_SCREEN_CIDS_DEFAULT": "screen", "_PUSH_CIDS_DEFAULT": "push",
              "_GROUP_ALIASES": "aliases"}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if isinstance(tgt, ast.Name) and tgt.id in wanted:
            try:
                out[wanted[tgt.id]] = ast.literal_eval(node.value)
            except Exception:                                   # noqa: BLE001
                out["unresolved"].append(tgt.id)
    if "groups" not in out:
        out["error"] = "bot.py 里找不到 _GROUPS_DEFAULT"
        return out
    out["bots"] = config_store.default_bots_from_env(out["groups"][0])
    return out


def _raw_from_store(defaults):
    """当前生效的配置（读文件；没有就取默认值）→ 可编辑的 raw dict。"""
    from runtime import config_store
    cfg = config_store.reload(defaults)
    return json.loads(json.dumps(cfg.as_dict(), ensure_ascii=False))


def _token_unsupported_notes(raw, defaults):
    """哪些改动**暂时**不会生效 —— 页面必须说清楚，不能装作都会生效。"""
    from runtime import config_store
    notes = []
    env_tokens = {}
    try:
        old = config_store.reload(defaults)
        old_keys = [b.key for b in old.bots]
    except Exception:                                           # noqa: BLE001
        old_keys = []
    new_keys = [b.get("key") for b in (raw.get("bots") or [])]
    if set(new_keys) != set(old_keys):
        notes.append(
            "Bot 的增删（{} → {}）：多 Bot 的**运行期**支持还没做（P11 B 期）。"
            "配置会正确存下来，但只有环境变量里的主 Bot 会被实际使用。"
            .format(old_keys or "无", new_keys or "无"))
    for b in raw.get("bots") or []:
        te = (b.get("token_env") or "").strip()
        if te and not (os.environ.get(te) or "").strip():
            notes.append("Bot {} 的 token_env={} 在环境里是空的（发送会失败）"
                         .format(b.get("key"), te))
    return notes


class Handler(BaseHTTPRequestHandler):
    server_version = "dzmm-admin/" + VERSION

    def _send(self, code, body, ctype, extra_headers=()):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # P4-4A 起有同源外链资源（/static/*.css + app.js）；顺手把 Referer 掐掉
        self.send_header("Referrer-Policy", "no-referrer")
        # 自包含页面：无 CDN、无内联脚本；脚本只允许**同源文件**（P12-D 起）。
        # script-src 'self' 是 P12-D 放开的（矩阵页没有 JS 会很难用），
        # 但内联脚本、CDN、eval 仍然全禁 —— 这是 XSS 的主要入口。
        # ⚠️ style-src 必须带 'self'（2026-10-02 线上事故）：P4-4A 起 CSS 从
        # 内联 <style> 改为外链 /static/*.css —— 'unsafe-inline' 只放行内联，
        # 外链样式表按源匹配，没有 'self' 就被浏览器 CSP 静默拒载（整站裸 HTML，
        # 服务器侧 200 完全看不出来）。'unsafe-inline' 保留：页面的 extra_head
        # 仍是内联 <style>。
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; "
                         "style-src 'self' 'unsafe-inline'; "
                         "script-src 'self'; form-action 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'")
        # 老浏览器兜底（CSP 的 frame-ancestors 已经覆盖现代浏览器）
        self.send_header("X-Frame-Options", "DENY")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            # P12-BO 杂项：客户端（多半是扫描器）在响应写完前断开 ⇒ wfile.write 抛
            # ConnectionResetError/BrokenPipeError，之前会把整段 traceback 打进日志
            # （线上 6 小时 1 条，纯噪声）。这里吞掉它 —— 它对功能没有任何影响。
            try:
                self.wfile.write(raw)
            except (ConnectionResetError, BrokenPipeError):
                pass

    def _redirect(self, location, extra_headers=()):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        # 重定向响应也要带安全头（原来漏了 —— 303 是最常见的一跳）
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()

    # 静态资源**白名单**：只服务这几个文件，不做目录遍历。
    # ⚠️ shell.page 引用的每一个 /static/ 路径都必须在这里登记，否则线上
    # 404、页面裸 HTML（2026-10-02 踩过：4A 新增三个 CSS 忘登记，本地目检走
    # http.server 直读文件系统没经过这个路由，漏到了线上）。防漂移钉点：
    # test_every_referenced_static_asset_is_served 从 shell.py 提取全部引用逐个 GET。
    _STATIC = {
        "/static/app.js": ("application/javascript; charset=utf-8", "app.js"),
        "/static/base.css": ("text/css; charset=utf-8", "base.css"),
        "/static/components.css": ("text/css; charset=utf-8", "components.css"),
        "/static/dashboard.css": ("text/css; charset=utf-8", "dashboard.css"),
    }

    def _send_static(self, path):
        got = self._STATIC.get(path)
        if not got:
            self._send(404, "not found\n", "text/plain; charset=utf-8")
            return
        ctype, name = got
        p = Path(__file__).resolve().parent / "static" / name
        try:
            body = p.read_bytes()
        except Exception as e:                                  # noqa: BLE001
            self._send(404, "static missing: {}\n".format(e),
                       "text/plain; charset=utf-8")
            return
        self._send(200, body, ctype)

    def _require_session(self):
        """没登录就渲染登录页并返回 False。

        未配置密码时**不要求登录** —— 那是"仅回环可达"模式（SSH 隧道用）。
        它之所以安全，是因为启动闸门保证了：声称对外发布（PUBLIC=on）却没有密码时
        进程根本不会起来。所以"不要求登录"只可能发生在回环 / 未对外声明的场景。
        """
        if not _creds_configured():
            return True
        ip = _client_ip(self)
        if session_valid(_cookie_sid(self)):
            return True
        self._send(200, render_login(locked_for=lock_remaining(ip)),
                   "text/html; charset=utf-8")
        return False

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        _prune()
        u = urlparse(self.path)
        if u.path == "/logout":
            drop_session(_cookie_sid(self))
            self._redirect("/", (("Set-Cookie",
                                  "{}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
                                  .format(SESSION_COOKIE)),))
            return
        if not self._require_session():
            return
        if u.path in ("/edit", "/config"):
            self._send(200, self._render_edit_page(), "text/html; charset=utf-8")
            return
        if u.path == "/features":
            self._send(200, self._render_features_page(),
                       "text/html; charset=utf-8")
            return
        if u.path == "/personas":
            self._send(200, self._render_personas_page(),
                       "text/html; charset=utf-8")
            return
        if u.path == "/prompts":
            self._send(200, self._render_prompts_page(
                name=(parse_qs(u.query or "").get("name") or [""])[0]),
                "text/html; charset=utf-8")
            return
        if u.path == "/prompts/history":
            q = parse_qs(u.query or "")
            self._send(200, self._render_prompt_history(
                (q.get("name") or [""])[0], (q.get("version") or [""])[0]),
                "text/html; charset=utf-8")
            return
        if u.path.startswith("/static/"):
            self._send_static(u.path)
            return
        if u.path == "/edit/backup":
            self._send(200, self._render_backup_page(u.query),
                       "text/html; charset=utf-8")
            return
        try:
            data = collect()
        except Exception as e:                                  # noqa: BLE001
            self._send(500, "collect failed: {}\n".format(e),
                       "text/plain; charset=utf-8")
            return
        if u.path == "/api/summary":
            self._send(200, json.dumps(data, ensure_ascii=False, default=str),
                       "application/json; charset=utf-8")
        elif u.path == "/healthz":
            self._send(200, "ok\n", "text/plain; charset=utf-8")
        elif u.path in ("/", "/index.html"):
            self._send(200, render_html(data), "text/html; charset=utf-8")
        else:
            self._send(404, "not found\n", "text/plain; charset=utf-8")

    # ------------------------------------------------ 配置编辑
    def _csrf_key(self):
        """CSRF token 挂在哪个键上：有会话用 sid，没会话用 "-"。

        ⚠️ **键怎么算只能有这一处**。原来渲染表单时存的是 `sid or "-"`，
        校验时查的却是 `sid` —— 在"未配密码"的模式下（`_require_session()`
        直接放行，所以永远没有 session cookie，`sid` 恒为空串），两边键对不上：
        配置页**能打开，但保存和回滚一律 400「CSRF 校验失败」**。
        生产环境配了密码所以从没暴露；隧道/回环模式一改就撞。
        """
        return _cookie_sid(self) or "-"

    def _csrf(self):
        """与会话绑定的 CSRF token（没有会话时用一个短命的一次性 token）。"""
        key = self._csrf_key()
        rec = _csrf_tokens.get(key)
        if not rec or rec[0] < time.time():
            tok = secrets.token_urlsafe(24)
            _csrf_tokens[key] = (time.time() + 3600, tok)
            return tok
        return rec[1]

    def _csrf_ok(self, form) -> bool:
        """校验表单里的 csrf 是否与本会话（或本次进程）的 token 一致。"""
        want = (_csrf_tokens.get(self._csrf_key()) or (0, ""))[1]
        got = (form.get("csrf") or [""])[0]
        return bool(want) and hmac.compare_digest(got, want)

    def _drain(self, length: int, cap: int = 64 * 1024) -> None:
        """有上限地把请求体读掉再回响应（防"关连接发 RST"把客户端打断）。

        只在我们**不打算处理**这个请求时才需要（比如 body 超限）。
        上限 `cap` 保证恶意客户端不能用超大 body 让服务端一直读。
        """
        try:
            remain = min(int(length or 0), cap)
            while remain > 0:
                chunk = self.rfile.read(min(remain, 8192))
                if not chunk:
                    break
                remain -= len(chunk)
        except Exception:                                          # noqa: BLE001
            pass

    def _render_backup_page(self, query=""):
        """单份备份的详情：人话摘要 + 逐行差异 + 回滚按钮。"""
        from admin import config_ui
        from runtime import config_store
        name = (parse_qs(query or "").get("name") or [""])[0]
        defaults = _config_defaults()
        try:
            old_raw = config_ui.restore_backup(config_store.backup_dir(), name)
            old_text = config_ui.backup_path(
                config_store.backup_dir(), name).read_text(encoding="utf-8")
            cur_raw = _raw_from_store(defaults)
            p = config_store.config_path()
            cur_text = p.read_text(encoding="utf-8") if p.is_file() else ""
        except Exception as e:                                  # noqa: BLE001
            return self._render_edit_page(error="打不开备份：{}".format(e))
        return config_ui.render_backup_detail(
            name, old_raw, cur_raw, old_text, cur_text, self._csrf())

    def _broken_config_page(self, err):
        """磁盘上的配置**现在不合法**时，三个页面都要说清楚同一件事。

        早先这里直接回一个 `<pre>读配置失败</pre>` —— 用户看不出"运行期其实
        还在正常跑、只是这次改坏了"（P12-F 之后尤其重要：热加载会拒绝坏配置，
        bot 一点都不受影响）。所以做成一页带红色横幅的说明 + 去备份页的链接。
        """
        from admin import config_ui, shell
        body = config_ui.status_block(
            {"invalid": True, "error": str(err)}, csrf=self._csrf())
        content = (body
                   + "<p class='text-dim'>机器人<b>没有受影响</b>：热加载遇到不合法的配置会"
                   "拒绝应用，运行期继续用上一次成功的配置。改回去（或从备份回滚）"
                   "就会自动恢复。</p>"
                   "<p><a class='btn btn--ghost btn--sm' href='/edit/backup'>备份与回滚</a></p>")
        return shell.page(active="/", title="配置读不了",
                          breadcrumb="监控 / 配置读不了",
                          content=content)

    def _render_features_page(self, error="", notice="", changes=(), reset_note=""):
        """功能与参数页（P12-D）。"""
        from admin import config_ui, features_ui
        from runtime import config_store, features as feats, tuning as tune
        defaults = _config_defaults()
        try:
            raw = _raw_from_store(defaults)
        except Exception as e:                                  # noqa: BLE001
            return self._broken_config_page(e)
        pending = config_ui.pending_change(
            config_store.config_path(), section_process().get("bot_started_epoch"))
        return features_ui.render_features(
            raw, feats.registry(), tune.registry(),
            tuple(tune.SCOPE_DEFAULTS.items()), feats.CATEGORIES,
            feats, tune, error=error, notice=notice, changes=changes,
            csrf=self._csrf(), pending=pending, reset_note=reset_note,
            status=_hot_status())

    def _render_personas_page(self, error="", notice="", changes=(), reset_note="",
                              preview=None, preview_cid="", preview_msg="",
                              shown=None):
        """人设与话术页（P12-E）。

        `shown` = 要**画回表单**的 personas 节（预览时用页面草稿，否则读文件）。
        """
        from admin import config_ui, personas_ui
        from runtime import config_store
        defaults = _config_defaults()
        try:
            raw = _raw_from_store(defaults)
        except Exception as e:                                  # noqa: BLE001
            return self._broken_config_page(e)
        if shown is not None:
            raw = json.loads(json.dumps(raw, ensure_ascii=False))
            if shown:
                raw["personas"] = shown
            else:
                raw.pop("personas", None)
        pending = config_ui.pending_change(
            config_store.config_path(), section_process().get("bot_started_epoch"))
        if not preview_cid:
            gs = raw.get("groups") or []
            preview_cid = (gs[0].get("cid") if gs else "") or ""
        return personas_ui.render_personas(
            raw, raw.get("groups") or [], csrf=self._csrf(), error=error,
            notice=notice, changes=changes, pending=pending,
            reset_note=reset_note, preview=preview, preview_cid=preview_cid,
            preview_msg=preview_msg, status=_hot_status())

    def _render_edit_page(self, error="", notice="", raw=None, changes=()):
        from admin import config_ui
        defaults = _config_defaults()
        if defaults.get("error"):
            return ("<pre>配置默认值读取失败：{}</pre>"
                    .format(_e(defaults["error"])))
        try:
            raw = raw if raw is not None else _raw_from_store(defaults)
        except Exception as e:                                  # noqa: BLE001
            return self._broken_config_page(e)
        from runtime import config_store
        pending = config_ui.pending_change(
            config_store.config_path(), section_process().get("bot_started_epoch"))
        return config_ui.render_edit(
            raw, error=error, notice=notice, csrf=self._csrf(),
            pending=pending,
            backups=config_ui.list_backups(config_store.backup_dir()),
            audit=config_ui.read_audit(config_store.audit_path()),
            unsupported=_token_unsupported_notes(raw, defaults),
            changes=changes,
            persona_names=_persona_names(),
            persona_effective=_persona_effective(raw),
            status=_hot_status())

    def _read_form(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return None
        if length > MAX_FORM_BODY:
            # 2026-09-27 实测：人设页要**整页回传**（3 个人设的文本 + 全部话术），
            # URL 编码后中文一字 9 字节，加了 19 条话术后就越过原来 64KB 的线，
            # 结果保存直接 400「请求格式不对」—— 页面看起来坏了，真因却是体积上限。
            print("[admin] 表单被拒：{} 字节超过上限 {}".format(length, MAX_FORM_BODY),
                  flush=True)
            return None
        body = self.rfile.read(length).decode("utf-8", "replace")
        return parse_qs(body, keep_blank_values=True)

    def _post_edit(self):
        from admin import config_ui
        from runtime import config_store
        form = self._read_form()
        if form is None:
            return self._send(400, self._render_edit_page(error="请求格式不对"),
                              "text/html; charset=utf-8")
        sid = _cookie_sid(self)
        if not self._csrf_ok(form):
            print("[admin] 配置保存被拒：CSRF token 不匹配", flush=True)
            return self._send(400, self._render_edit_page(error="CSRF 校验失败，请重新打开页面"),
                              "text/html; charset=utf-8")

        defaults = _config_defaults()
        old = _raw_from_store(defaults)
        # `base=old`：把当前配置里的 v2 三节（features/tuning/scopes）带过去 ——
        # 这一版表单页还画不了它们，不继承就会被"改个别名"顺手抹掉。
        new = config_ui.parse_form(form, base=old)
        # 先挡住"旧页面把配置整行删掉"：旧页面里没有的条目，提交上来就会丢。
        # 只在**配置文件真的存在**时才查 —— 没有文件时用的是 bot.py 里的默认值，
        # 删不掉任何东西，没必要拦。
        # （页面渲染时会写隐藏字段记着当时有哪些群/Bot，这里拿它比对）
        if config_store.config_path().is_file():
            unseen = config_ui.unseen_deletions(old, new, form)
            if unseen:
                print("[admin] 拒绝保存：提交的是旧页面，缺少 {}".format(unseen), flush=True)
                return self._send(400, self._render_edit_page(
                    error=("这个页面是**旧的一份**，里面没有 {}。直接保存会把配置里这几行"
                           "整行删掉（别名、人设一起没），所以已拒绝。"
                           "请刷新页面（或换回最新那个标签页）后重新修改。"
                           .format("、".join(unseen))),
                    raw=new), "text/html; charset=utf-8")
        summary = config_ui.diff_summary(old, new)
        if not summary:
            return self._send(200, self._render_edit_page(notice="没有检测到改动"),
                              "text/html; charset=utf-8")
        try:
            config_store.save(new, defaults)          # 校验不过这里会抛，一个字都不写
        except Exception as e:                        # noqa: BLE001
            print("[admin] 配置保存被拒：{}".format(str(e)[:120]), flush=True)
            return self._send(400,
                              self._render_edit_page(error="配置不合法，未写入：{}".format(e),
                                                     raw=new),
                              "text/html; charset=utf-8")
        config_ui.audit_log(config_store.audit_path(), _client_ip(self), summary)
        print("[admin] 配置已保存 来源 {} 改动 {} 项".format(
            _client_ip(self), len(summary)), flush=True)
        return self._send(200, self._render_edit_page(
            notice=("已保存（{} 项改动）。人设选择会自动热加载；"
                    "**群清单 / Bot 注册表 / 静默·筛查·推送需要重启容器**"
                    ).format(len(summary)),
            changes=summary),
            "text/html; charset=utf-8")

    def _post_features(self):
        """保存功能/参数/按群集合三节。

        **只覆盖这三节**（不碰 groups / bots）：所以这一页的表单再旧，
        也不可能删掉群或 Bot —— 爆炸半径比 `/edit` 小一截。
        """
        from admin import config_ui, features_ui
        from runtime import config_store, features as feats, tuning as tune
        form = self._read_form()
        if form is None:
            return self._send(400, self._render_features_page(error="请求格式不对"),
                              "text/html; charset=utf-8")
        if not self._csrf_ok(form):
            print("[admin] 功能保存被拒：CSRF token 不匹配", flush=True)
            return self._send(400, self._render_features_page(error="CSRF 校验失败，请重新打开页面"),
                              "text/html; charset=utf-8")
        defaults = _config_defaults()
        old = _raw_from_store(defaults)
        parsed = features_ui.parse_features(
            form, old, feats.registry(), tuple(tune.SCOPE_DEFAULTS.items()),
            tune.registry())
        summary = features_ui.features_diff(
            old, parsed, feats.registry(), tuple(tune.SCOPE_DEFAULTS.items()))
        if not summary:
            return self._send(200, self._render_features_page(notice="没有检测到改动"),
                              "text/html; charset=utf-8")
        # 合成新配置：三节 + 每个群的覆盖；groups/bots 原样保留
        new = json.loads(json.dumps(old, ensure_ascii=False))
        new["features"] = parsed["features"]
        new["tuning"] = parsed["tuning"]
        new["scopes"] = parsed["scopes"]
        for g in new.get("groups") or []:
            cid = g.get("cid")
            gf = parsed["group_features"].get(cid) or {}
            gt = parsed["group_tuning"].get(cid) or {}
            if gf:
                g["features"] = gf
            else:
                g.pop("features", None)
            if gt:
                g["tuning"] = gt
            else:
                g.pop("tuning", None)
        try:
            config_store.save(new, defaults)      # 校验不过一个字都不写
        except Exception as e:                    # noqa: BLE001
            print("[admin] 功能保存被拒：{}".format(str(e)[:120]), flush=True)
            return self._send(400, self._render_features_page(
                error="配置不合法，未写入：{}".format(e)),
                "text/html; charset=utf-8")
        config_ui.audit_log(config_store.audit_path(), _client_ip(self), summary)
        print("[admin] 功能/参数已保存 来源 {} 改动 {} 项".format(
            _client_ip(self), len(summary)), flush=True)
        return self._send(200, self._render_features_page(
            notice="已保存（{} 项改动），约 1 秒后自动生效（热加载，不用重启）".format(
                len(summary)),
            changes=summary), "text/html; charset=utf-8")

    def _post_reset(self):
        """强制复位：清掉某个功能在这个群的「进行中」状态（**不改配置**）。"""
        from admin import config_ui
        from runtime import features as feats
        form = self._read_form()
        if form is None or not self._csrf_ok(form):
            return self._send(400, self._render_features_page(error="CSRF 校验失败"),
                              "text/html; charset=utf-8")
        cid = (form.get("cid") or [""])[0]
        key = (form.get("key") or [""])[0]
        try:
            done = feats.reset_feature(cid, key)
        except Exception as e:                                  # noqa: BLE001
            return self._send(400, self._render_features_page(
                error="复位失败：{}".format(e)), "text/html; charset=utf-8")
        note = ("已复位 {key} @ {cid}：{done}".format(
            key=key, cid=(cid or "")[:8],
            done="、".join(done) if done else "本来就没有进行中的状态"))
        print("[admin] " + note + " 来源 " + _client_ip(self), flush=True)
        config_ui.audit_log(__import__("runtime.config_store", fromlist=["audit_path"]).audit_path(),
                            _client_ip(self), [note])
        return self._send(200, self._render_features_page(reset_note=note),
                          "text/html; charset=utf-8")

    def _post_reload(self):
        """立即热加载（P12-F）：让**机器人进程**把 config.json + prompts 重读一遍。

        **不做任何写操作** —— 它只让运行期跟上磁盘上的配置，所以
        "谁在什么时候改了什么"仍然只由配置页写的那几次决定（审计还在那边）。

        ⚠️ 面板和机器人是**两个进程**：面板改不了机器人进程的内存。所以
        - 在机器人进程里（CLI/测试）→ 直接 `reload_now()`；
        - 在面板进程里 → 写一个请求文件（`config/hotload_request`），
          主循环下一轮（≤1 秒）消费掉并强制重读。所以按钮是**异步**的，
          页面上也如实这么写。
        """
        from runtime import hotload
        form = self._read_form()
        if form is None or not self._csrf_ok(form):
            return self._send(400, self._render_edit_page(error="CSRF 校验失败"),
                              "text/html; charset=utf-8")
        if hotload.configured():
            try:
                rep = hotload.reload_now()
            except Exception as e:                              # noqa: BLE001
                return self._send(500, self._render_edit_page(
                    error="热加载失败：{}".format(e)), "text/html; charset=utf-8")
            n = sum(len(v or []) for v in (rep.get("changes") or {}).values())
            if rep.get("invalid") or not rep.get("ok", True):
                note = "热加载**未生效**（配置不合法，运行期保持原样）：{}".format(
                    "；".join(rep.get("errors") or [])[:200])
            elif n:
                note = "已热加载（{} 项改动生效，**没有重启**）。".format(n)
            else:
                note = "已热加载：磁盘上的配置与运行期一致，没有变化。"
            struct = rep.get("structural") or []
            if struct:
                note += " ⚠️ 另有需要重启的部分：" + "；".join(struct)
        else:
            ok = hotload.request_reload()
            note = ("已请求机器人进程立即热加载（约 1 秒内生效，**不用重启**）。"
                    if ok else "请求失败：写不进 config/ 目录，请看容器日志。")
        print("[admin] 热加载请求 来源 {} 就地={}".format(
            _client_ip(self), hotload.configured()), flush=True)
        return self._send(200, self._render_edit_page(notice=note),
                          "text/html; charset=utf-8")

    def _post_personas(self):
        """保存 / 预览人设与话术。

        **只覆盖 `personas` 一节**（不碰 groups / bots / features / tuning）：
        这一页的表单再旧，也不可能删掉群或 Bot。

        `action=preview` ⇒ **一个字都不写**，只用草稿渲染一遍（可选真调一次模型）。
        """
        from admin import config_ui, personas_ui
        from runtime import config_store
        form = self._read_form()
        if form is None:
            return self._send(400, self._render_personas_page(error="请求格式不对"),
                              "text/html; charset=utf-8")
        if not self._csrf_ok(form):
            print("[admin] 人设保存被拒：CSRF token 不匹配", flush=True)
            return self._send(400,
                              self._render_personas_page(error="CSRF 校验失败，请重新打开页面"),
                              "text/html; charset=utf-8")
        defaults = _config_defaults()
        old = _raw_from_store(defaults)
        parsed = personas_ui.parse_personas(form, old)
        action = (form.get("action") or [""])[0]
        cid = (form.get("preview_cid") or [""])[0]
        msg = (form.get("preview_msg") or [""])[0]
        if action == "preview":
            try_msg = msg.strip() if (form.get("preview_try") or [""])[0] else ""
            pv = personas_ui.persona_preview(
                {"personas": parsed["personas"]}, cid, try_msg)
            print("[admin] 人设预览 群={} 人设={} 试说={}".format(
                (cid or "")[:8], pv.get("key"), "是" if try_msg else "否"), flush=True)
            return self._send(200, self._render_personas_page(
                preview=pv, preview_cid=cid, preview_msg=msg,
                shown=parsed["personas"]), "text/html; charset=utf-8")
        summary = personas_ui.personas_diff(old, parsed)
        if not summary:
            return self._send(200, self._render_personas_page(notice="没有检测到改动"),
                              "text/html; charset=utf-8")
        new = json.loads(json.dumps(old, ensure_ascii=False))
        if parsed["personas"]:
            new["personas"] = parsed["personas"]
        else:
            new.pop("personas", None)
        try:
            config_store.save(new, defaults)      # 校验不过一个字都不写
        except Exception as e:                    # noqa: BLE001
            print("[admin] 人设保存被拒：{}".format(str(e)[:120]), flush=True)
            return self._send(400, self._render_personas_page(
                error="配置不合法，未写入：{}".format(e),
                shown=parsed["personas"]), "text/html; charset=utf-8")
        config_ui.audit_log(config_store.audit_path(), _client_ip(self), summary)
        print("[admin] 人设/话术已保存 来源 {} 改动 {} 项".format(
            _client_ip(self), len(summary)), flush=True)
        return self._send(200, self._render_personas_page(
            notice="已保存（{} 项改动），约 1 秒后自动生效（热加载，不用重启）".format(
                len(summary)),
            changes=summary), "text/html; charset=utf-8")

    def _post_personas_reset(self):
        """恢复默认：清掉一个人设（或全部）的文本/话术覆盖。"""
        from admin import config_ui
        from runtime import config_store
        form = self._read_form()
        if form is None or not self._csrf_ok(form):
            return self._send(400,
                              self._render_personas_page(error="CSRF 校验失败"),
                              "text/html; charset=utf-8")
        who = (form.get("persona") or [""])[0]
        defaults = _config_defaults()
        old = _raw_from_store(defaults)
        cur = dict(old.get("personas") or {})
        if who == "*":
            if not cur:
                return self._send(200, self._render_personas_page(
                    reset_note="本来就都是默认值，没有要清的改动"),
                    "text/html; charset=utf-8")
            note = "已恢复全部人设的默认（清掉 {} 个人设的改动）".format(len(cur))
            cur = {}
        else:
            if who not in cur:
                return self._send(200, self._render_personas_page(
                    reset_note="人设 {} 本来就都是默认值".format(who or "(空)")),
                    "text/html; charset=utf-8")
            cur.pop(who, None)
            note = "已恢复人设 {} 的默认".format(who)
        new = json.loads(json.dumps(old, ensure_ascii=False))
        if cur:
            new["personas"] = cur
        else:
            new.pop("personas", None)
        try:
            config_store.save(new, defaults)
        except Exception as e:                                  # noqa: BLE001
            return self._send(400, self._render_personas_page(
                error="恢复默认失败：{}".format(e)), "text/html; charset=utf-8")
        config_ui.audit_log(config_store.audit_path(), _client_ip(self), [note])
        print("[admin] " + note + " 来源 " + _client_ip(self), flush=True)
        return self._send(200, self._render_personas_page(
            reset_note=note + "。约 1 秒后自动生效（热加载）"),
            "text/html; charset=utf-8")

    def _render_prompts_page(self, name="", error="", notice="", changes=(),
                             preview=None, preview_user="", draft=None):
        """文案模板页（P12-G）。

        `draft`：校验/自测失败时要把**用户刚打的字**画回去（不然一次失败白改）。
        """
        from admin import prompts_ui
        from runtime import prompt_tiers as PT
        from runtime import prompts as RP
        rows = []
        for s in PT.all_specs():
            text = RP.load(s.key, RP.default_of(s.key))
            rows.append({
                "key": s.key, "tier": s.tier, "title": s.title, "note": s.note,
                "source": RP.registry().get(s.key, {}).get("source", "code"),
                "chars": len(text), "lines": text.count("\n") + 1,
                "edited": text != RP.default_of(s.key),
            })
        selected = None
        for s in PT.all_specs():
            if s.key == name:
                default = RP.default_of(s.key)
                text = draft if draft is not None else RP.load(s.key, default)
                selected = {
                    "spec": s, "text": text, "default_len": len(default),
                    "source": ("file" if draft is not None else
                               RP.registry().get(s.key, {}).get("source", "code")),
                }
                break
        st = _hot_status() or {}
        status_line = ""
        if st.get("at_text"):
            status_line = "最近一次热加载 {}（来源 {}）—— 保存后约 1 秒生效，不用重启".format(
                st["at_text"], st.get("source") or "?")
        return prompts_ui.render_prompts(
            rows, selected=selected, error=error, notice=notice, changes=changes,
            preview=preview, preview_user=preview_user,
            status_line=status_line, csrf=self._csrf(),
            history=(prompts_ui.list_backups(name) if name else ()))

    def _post_prompts(self):
        """保存 / 预览一个文案模板。

        **只写 `prompts/<KEY>.md` 一个文件**（+ 它的历史版本），不碰 config.json，
        更不碰代码。校验（静态 + C3 自动契约自测）不过 ⇒ **一个字都不写**。
        """
        from admin import config_ui, prompts_ui
        from runtime import hotload
        from runtime import prompt_tiers as PT
        form = self._read_form()
        if form is None:
            return self._send(400, self._render_prompts_page(error="请求格式不对"),
                              "text/html; charset=utf-8")
        if not self._csrf_ok(form):
            return self._send(400, self._render_prompts_page(error="CSRF 校验失败，请重新打开页面"),
                              "text/html; charset=utf-8")
        key = (form.get("key") or [""])[0]
        text = (form.get("text") or [""])[0].replace("\r\n", "\n").replace("\r", "\n")
        user = ((form.get("user") or [""])[0] or "").strip()
        action = (form.get("action") or [""])[0]
        spec = PT.spec(key)
        if spec is None:
            return self._send(400, self._render_prompts_page(error="没有这个提示词：{}".format(key)),
                              "text/html; charset=utf-8")
        issues = PT.validate(key, text)
        if action == "preview":
            pv = PT.run_selftest(key, text=text, user=user or None)
            pv["saved"] = False
            print("[admin] 文案预览 {} 通过={}".format(key, pv.get("ok")), flush=True)
            return self._send(200, self._render_prompts_page(
                name=key, preview=pv, preview_user=user, draft=text),
                "text/html; charset=utf-8")
        if issues:
            return self._send(400, self._render_prompts_page(
                name=key, draft=text, preview_user=user,
                error="校验没通过，一个字都没写：{}".format("；".join(issues))),
                "text/html; charset=utf-8")
        # C3（结构化输出）默认**保存即自测**：契约坏了不是难看，是功能报错
        need_test = (action == "savetest") or (spec.tier == "C3" and action != "force")
        pv = None
        if need_test:
            pv = PT.run_selftest(key, text=text, user=user or None)
            if not pv.get("ok") and not pv.get("skipped"):
                pv["saved"] = False
                print("[admin] 文案保存被拒（自测不过）{}".format(key), flush=True)
                return self._send(400, self._render_prompts_page(
                    name=key, draft=text, preview_user=user, preview=pv,
                    error=("自测没通过，**没有写入**。看下面的返回内容确认一下；"
                           "确实要用这一版就点「强制保存」（会记在审计里）。")),
                    "text/html; charset=utf-8")
        old = ""
        try:
            p = prompts_ui.prompt_file(key)
            old = p.read_text(encoding="utf-8") if p.is_file() else ""
        except Exception:                                       # noqa: BLE001
            old = ""
        try:
            prompts_ui.write_prompt(key, text)
        except Exception as e:                                  # noqa: BLE001
            return self._send(500, self._render_prompts_page(
                name=key, draft=text, error="写入失败：{}".format(e)),
                "text/html; charset=utf-8")
        # 立刻请机器人进程重算 prompt（面板进程只写请求文件，见 runtime/hotload.py）
        try:
            hotload.request_reload()
        except Exception:                                       # noqa: BLE001
            pass
        note = ("已保存 prompts/{}.md（{} 字{}{}）。约 1 秒自动生效（热加载，不用重启）。"
                ).format(key, len(text),
                         "，历史版本已留" if old else "",
                         "，原内容 {} 字".format(len(old)) if old else "")
        config_ui.audit_log(
            __import__("runtime.config_store", fromlist=["audit_path"]).audit_path(),
            _client_ip(self),
            ["文案模板 {} 已更新（{} 字{}）".format(
                key, len(text), "，自测通过" if (pv and pv.get("ok")) else "")])
        print("[admin] 文案已保存 {} 来源 {}".format(key, _client_ip(self)), flush=True)
        if pv is not None:
            pv["saved"] = True
        return self._send(200, self._render_prompts_page(
            name=key, notice=note, preview=pv, preview_user=user),
            "text/html; charset=utf-8")

    def _post_prompts_reset(self):
        """恢复代码默认值 = 删掉覆盖文件（先留一份历史）。"""
        from admin import config_ui, prompts_ui
        from runtime import hotload
        form = self._read_form()
        if form is None or not self._csrf_ok(form):
            return self._send(400, self._render_prompts_page(error="CSRF 校验失败"),
                              "text/html; charset=utf-8")
        key = (form.get("key") or [""])[0]
        try:
            done = prompts_ui.reset_prompt(key)
        except Exception as e:                                  # noqa: BLE001
            return self._send(400, self._render_prompts_page(
                name=key, error="恢复默认失败：{}".format(e)), "text/html; charset=utf-8")
        try:
            hotload.request_reload()
        except Exception:                                       # noqa: BLE001
            pass
        note = ("已恢复代码默认值（写回 prompts/{}.md 的默认文本）".format(key) if done
                else "本来就是代码默认值，没有改动")
        config_ui.audit_log(
            __import__("runtime.config_store", fromlist=["audit_path"]).audit_path(),
            _client_ip(self), ["文案模板 {} {}".format(key, note)])
        return self._send(200, self._render_prompts_page(name=key, notice=note),
                          "text/html; charset=utf-8")

    def _render_prompt_history(self, key, version):
        from admin import prompts_ui
        from runtime import prompt_tiers as PT
        spec = PT.spec(key)
        if spec is None:
            return self._render_prompts_page(error="没有这个提示词：{}".format(key))
        try:
            old = (prompts_ui.backup_dir() / version).read_text(encoding="utf-8")
        except Exception as e:                                  # noqa: BLE001
            return self._render_prompts_page(name=key, error="打不开历史版本：{}".format(e))
        p = prompts_ui.prompt_file(key)
        cur = p.read_text(encoding="utf-8") if p.is_file() else ""
        return prompts_ui.render_history(key, spec.title, version, old, cur,
                                         csrf=self._csrf())

    def _post_prompts_rollback(self):
        from admin import config_ui, prompts_ui
        from runtime import hotload
        form = self._read_form()
        if form is None or not self._csrf_ok(form):
            return self._send(400, self._render_prompts_page(error="CSRF 校验失败"),
                              "text/html; charset=utf-8")
        key = (form.get("key") or [""])[0]
        version = (form.get("version") or [""])[0]
        try:
            prompts_ui.rollback_prompt(key, version)
        except Exception as e:                                  # noqa: BLE001
            return self._send(400, self._render_prompts_page(
                name=key, error="回滚失败：{}".format(e)), "text/html; charset=utf-8")
        try:
            hotload.request_reload()
        except Exception:                                       # noqa: BLE001
            pass
        note = "已回滚到历史版本 {}。约 1 秒自动生效。".format(version)
        config_ui.audit_log(
            __import__("runtime.config_store", fromlist=["audit_path"]).audit_path(),
            _client_ip(self), ["文案模板 {} 回滚到 {}".format(key, version)])
        return self._send(200, self._render_prompts_page(name=key, notice=note),
                          "text/html; charset=utf-8")

    def _post_rollback(self):
        from admin import config_ui
        from runtime import config_store
        form = self._read_form()
        if form is None:
            return self._send(400, "bad request\n", "text/plain; charset=utf-8")
        if not self._csrf_ok(form):
            return self._send(400, self._render_edit_page(error="CSRF 校验失败"),
                              "text/html; charset=utf-8")
        name = (form.get("name") or [""])[0]
        defaults = _config_defaults()
        try:
            raw = config_ui.restore_backup(config_store.backup_dir(), name)
            old = _raw_from_store(defaults)
            config_store.save(raw, defaults)          # 备份也要过校验
        except Exception as e:                        # noqa: BLE001
            return self._send(400, self._render_edit_page(
                error="回滚失败：{}".format(e)), "text/html; charset=utf-8")
        summary = ["回滚到备份 {}".format(name)] + config_ui.diff_summary(old, raw)
        config_ui.audit_log(config_store.audit_path(), _client_ip(self), summary)
        print("[admin] 配置已回滚到 {} 来源 {}".format(name, _client_ip(self)), flush=True)
        return self._send(200, self._render_edit_page(
            notice=("已回滚到 {name}。功能/参数/人设会自动热加载；"
                    "**群与 Bot 需要重启容器**").format(name=name),
            changes=summary),
            "text/html; charset=utf-8")

    def do_POST(self):
        """非 GET 只有两条：`/login`（登录）和配置编辑（`/edit`、`/edit/rollback`）。

        只读面板的那些 section 仍然一处写操作都没有；写只发生在
        `config_store.save()` 里，而它**只写 config.json 一个文件**（+ 备份 + 审计）。
        `tests/test_admin_dashboard.py` 钉住这条边界。
        """
        _prune()
        u = urlparse(self.path)
        ip = _client_ip(self)

        if u.path == "/edit/rollback":
            if not self._require_session():
                return
            return self._post_rollback()
        if u.path == "/features":
            if not self._require_session():
                return
            return self._post_features()
        if u.path == "/reload":
            if not self._require_session():
                return
            return self._post_reload()
        if u.path == "/personas":
            if not self._require_session():
                return
            return self._post_personas()
        if u.path == "/personas/reset":
            if not self._require_session():
                return
            return self._post_personas_reset()
        if u.path == "/prompts":
            if not self._require_session():
                return
            return self._post_prompts()
        if u.path == "/prompts/reset":
            if not self._require_session():
                return
            return self._post_prompts_reset()
        if u.path == "/prompts/rollback":
            if not self._require_session():
                return
            return self._post_prompts_rollback()
        if u.path == "/edit/reset":
            if not self._require_session():
                return
            return self._post_reset()
        if u.path == "/edit":
            if not self._require_session():
                return
            return self._post_edit()
        if u.path != "/login":
            self._send(405, "只接受 GET/HEAD（登录 POST /login；配置 POST /edit）\n",
                       "text/plain; charset=utf-8")
            return

        left = lock_remaining(ip)
        if left > 0:
            print("[admin] 登录被拒（锁定中 {}s）来源 {}".format(left, ip), flush=True)
            self._send(429, render_login(locked_for=left), "text/html; charset=utf-8")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0 or length > MAX_BODY:
            # ⚠️ 拒绝之前要**把请求体读掉**（有上限地丢弃）。
            # 不读就回 400 然后关连接，Windows 会发 RST，客户端正在写 body 时
            # 收到 `ConnectionAbortedError (WinError 10053)` ——
            # 表现是"测试偶发红"，根子是服务端不礼貌的关闭方式。
            self._drain(length)
            self._send(400, render_login(error="请求格式不对"),
                       "text/html; charset=utf-8")
            return
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = parse_qs(raw)
        user = (form.get("username") or [""])[0]
        password = (form.get("password") or [""])[0]

        if check_credentials(user, password):
            clear_failures(ip)
            sid = new_session()
            print("[admin] 登录成功 来源 {}".format(ip), flush=True)
            self._redirect("/", (("Set-Cookie",
                                  "{}={}; Path=/; HttpOnly; SameSite=Strict; Max-Age={}"
                                  .format(SESSION_COOKIE, sid, SESSION_TTL)),))
            return

        wait = note_failure(ip)
        print("[admin] 登录失败 来源 {} 账号={!r} 累计第 {} 次{}".format(
            ip, user[:32], (_fails.get(ip) or [0])[0],
            "，已锁定 {}s".format(wait) if wait else ""), flush=True)
        self._send(401, render_login(error="账号或密码不对", locked_for=wait, user=user),
                   "text/html; charset=utf-8")

    def do_PUT(self):
        self._refuse()

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT

    def _refuse(self):
        self._send(405, "只接受 GET/HEAD（登录 POST /login；配置 POST /edit）\n",
                   "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass                                                    # 不刷访问日志


_STARTED = time.time()


def main(argv=None):
    if (os.environ.get("DZMM_ADMIN") or "on").strip().lower() in ("off", "0", "no", "false"):
        print("[admin] DZMM_ADMIN=off，不启动运维后台")
        return 0
    host = os.environ.get("DZMM_ADMIN_HOST") or DEFAULT_HOST
    port = int(os.environ.get("DZMM_ADMIN_PORT") or DEFAULT_PORT)
    public = _truthy(os.environ.get("DZMM_ADMIN_PUBLIC"))
    user, pw = _cfg_user(), _cfg_password()

    # 失效关闭（fail closed）：声称"发布到回环之外"却没有账号密码 -> 直接不启动。
    # 把"忘了设密码就把只读面板挂到公网"变成一次**启动失败**，
    # 而不是一次静默裸奔。容器日志里会留下这行，一眼能看见。
    if public and not pw:
        print("[admin] !! 拒绝启动：DZMM_ADMIN_PUBLIC=on（会对外可达）"
              "但没有设置 DZMM_ADMIN_PASSWORD。")
        print("[admin]    要么在 .env 里设账号密码，要么把端口映射改回 127.0.0.1。")
        return 2
    if pw and len(pw) < 8:
        print("[admin] !! 拒绝启动：DZMM_ADMIN_PASSWORD 太短（<8 位），"
              "对外可达的面板不能用弱密码。")
        return 2

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print("[admin] 运维后台已启动 http://{}:{}  只读 / 登录={}".format(
        host, port, "开（账号 {}）".format(user) if pw else "关（未设密码，视为仅回环）"))
    if public:
        print("[admin] 对外发布：DZMM_ADMIN_PUBLIC=on。**必须**配合安全组只放行"
              "你自己的出口 IP —— 这套是 http 明文，密码在链路上是可见的。")
    else:
        print("[admin] 对外边界 = compose 的端口映射（应为 127.0.0.1:{}:{}）。"
              "不要在没设密码的情况下改成对外映射。".format(port, port))
    if host in ("127.0.0.1", "localhost", "::1"):
        print("[admin] ⚠️ 绑的是回环：compose 发布会连不上（docker-proxy 从容器 IP 转发）。")
    print("[admin] 会话只存在内存里：重启即全部登出。连续登录失败 {} 次起锁定。"
          .format(LOCK_AFTER))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
