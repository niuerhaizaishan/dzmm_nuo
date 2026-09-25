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
TOKEN_FILE = ROOT / ".dzmm_token.json"
SESSION_VERSION_FILE = ROOT / ".session_version.json"
OUTBOX_DB = ROOT / "data" / "outbox.db"
STORE_DB = ROOT / "data" / "store.db"

# 启动自证行：这几行没有，就说明对应能力没生效（P3~P8 各一条）
PROOF_MARKERS = ["bot路由", "outbox:", "P5:", "P7:", "P8:"]
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
    import runtime.prompts as prompts
    st = prompts.stats()
    if not st.get("total"):
        st["warning"] = "prompts 注册为空（brains 未成功 import）—— 下面的数字不可信"
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
    # Docker 在 bind mount 的源文件不存在时会建一个**目录** —— 那种情况下
    # config_store 读不到配置（静默退回默认值），而界面写它会失败。
    # 这种"看起来配了、其实没配"的状态必须显式报出来。
    try:
        _p = Path(cfg.path)
        out["path_is_dir"] = _p.is_dir()
    except Exception:                                          # noqa: BLE001
        out["path_is_dir"] = False
    out["note"] = ("source=defaults 表示没有 config.json，用的是 bot.py 里的默认值；"
                   "配置改动**需要重启容器**才生效")
    if out["path_is_dir"]:
        out["note"] = ("⚠️ {} 存在但**是个目录**（bind mount 的源文件当初不存在，"
                       "Docker 建了目录）：配置其实没生效。删掉这个目录、"
                       "用 tools/config_gen.py 生成文件再重启。".format(cfg.path))
    out["GROUPS"] = list(cfg.groups)
    out["MUTE_CIDS"] = sorted(cfg.mute)
    out["SCREEN_CIDS"] = sorted(cfg.screen)
    out["PUSH_CIDS"] = list(cfg.push)
    out["group_count"] = len(cfg.groups)
    out["muted_count"] = len(cfg.mute)
    out["screen_count"] = len(cfg.screen)
    out["push_count"] = len(cfg.push)
    # 「这个群走哪个 Bot」从 bots 侧**派生**，不在群上重复存一份
    # （两个真源迟早不一致）
    cid2bot = {b.cid: b.key for b in cfg.bots}
    out["groups"] = [
        {"cid": c, "alias": cfg.group(c).alias, "enabled": cfg.group(c).enabled,
         "mute": cfg.group(c).mute, "screen": cfg.group(c).screen,
         "push": cfg.group(c).push, "persona": cfg.group(c).persona,
         "bot": cid2bot.get(c, "")}
        for c in cfg.groups]
    out["bots"] = [
        {"key": b.key, "name": b.name, "short": b.short, "cid": b.cid,
         "persona": b.persona, "enabled": b.enabled,
         "token_env": b.token_env,
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
]


def collect():
    """跑所有 section。**每段单独 try** —— 一段坏了不该让整页变白。"""
    result = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "version": VERSION,
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

_CSS = """
body{font:13px/1.6 -apple-system,Segoe UI,Roboto,'Noto Sans CJK SC',sans-serif;
margin:0;background:#12141a;color:#d8dee9}
header{padding:10px 16px;background:#1b1f2a;border-bottom:1px solid #2b3140;
display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600}
.badge{padding:1px 7px;border-radius:9px;font-size:11px}
.ok{background:#1f3d2b;color:#7ee2a8}.bad{background:#4a1f27;color:#ff8b9a}
.warn{background:#463a1c;color:#ffd479}
main{padding:12px 16px;display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(370px,1fr))}
section{background:#181c25;border:1px solid #262c39;border-radius:7px;overflow:hidden}
section>h2{font-size:13px;margin:0;padding:7px 11px;background:#1e2331;
border-bottom:1px solid #262c39;display:flex;justify-content:space-between}
section>h2 small{color:#6c7686;font-weight:400}
.body{padding:9px 11px;overflow:auto;max-height:460px}
table{border-collapse:collapse;width:100%}
td,th{padding:2px 6px;text-align:left;vertical-align:top;border-bottom:1px solid #232935}
th{color:#8b95a7;font-weight:500}
code,.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
pre{margin:0;white-space:pre-wrap;word-break:break-all;font-size:12px;
background:#0f1218;padding:6px;border-radius:5px;color:#a9b6c9}
.err{color:#ff8b9a}.dim{color:#6c7686}.hi{color:#ffd479}
footer{padding:8px 16px;color:#6c7686;font-size:11px}
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
    return "<table>{}</table>".format("".join(rows))


def _list_block(items, cls="mono"):
    if not items:
        return '<span class="dim">（无）</span>'
    return "<pre>{}</pre>".format(_e("\n".join(str(i) for i in items)))


def render_html(data):
    secs = data["sections"]

    def body(key):
        s = secs.get(key)
        if not s:
            return '<span class="dim">（缺）</span>'
        if not s["ok"]:
            return '<span class="err">{}</span>'.format(_e(s.get("error")))
        d = s["data"]
        if key == "process":
            bad = not d["bot_running"]
            return ('<span class="badge {}">{}</span>'.format(
                        "bad" if bad else "ok",
                        "bot.py 未在运行" if bad else "bot.py 运行中")
                    + _kv_table(d, skip=("bot_running",)))
        if key == "boot":
            badge = ('<span class="badge {}">{}/{} 自证行</span>'.format(
                "ok" if d["all_present"] else "bad",
                sum(1 for v in d["lines"].values() if v), len(d["lines"])))
            rows = "".join(
                "<tr><th>{}</th><td class='mono {}'>{}</td></tr>".format(
                    _e(k), "" if v else "err", _e(v or "（缺）"))
                for k, v in d["lines"].items())
            return badge + "<table>{}</table>".format(rows)
        if key == "auth":
            parts = []
            if d.get("bot_paused"):
                parts.append('<span class="badge bad">bot.paused 存在（已暂停）</span>')
            rows = "".join(
                "<tr><td class='mono'>{}</td><td>{}</td><td class='{}'>{}</td></tr>".format(
                    _e(c["name"]), _e(c["expires_at"]),
                    "err" if c["expired"] else "", _e(c["remain"]))
                for c in d.get("cookies") or [])
            parts.append("<table><tr><th>cookie</th><th>到期</th><th>剩余</th></tr>{}</table>"
                         .format(rows or "<tr><td colspan=3 class='dim'>没读到 token cookie</td></tr>"))
            parts.append("<div class='dim'>session_version: <span class='mono'>{}</span></div>"
                         .format(_e(json.dumps(d.get("session_version"), ensure_ascii=False))))
            parts.append("<div class='dim'>续期日志尾部</div>" + _list_block(d.get("renew_tail")))
            return "".join(parts)
        if key == "outbox":
            if not d.get("present"):
                return '<span class="warn">outbox.db 不存在</span>'
            head = ('<span class="badge {}">pending/leased {}</span>'
                    '<span class="badge {}">重试&gt;1 {}</span>'
                    '<span class="badge {}">有 last_error {}</span>'
                    '<span class="dim"> 共 {} 条 / {} / 最老待发 {}</span>').format(
                "warn" if d["pending_or_leased"] else "ok", d["pending_or_leased"],
                "bad" if d["retried"] else "ok", d["retried"],
                "bad" if d["with_error"] else "ok", d["with_error"],
                d["total"], d["size"], d["oldest_pending_age"])
            rows = "".join(
                "<tr><td>{}</td><td>{}</td><td class='mono'>{}</td><td class='err mono'>{}</td></tr>"
                .format(_e(r["status"]), _e(r["attempts"]), _e(r["text"]),
                        _e(r["last_error"]))
                for r in d["recent"])
            errs = ""
            if d.get("errors"):
                errs = "<div class='dim'>发失败过的记录</div>" + _list_block(
                    ["{} ← {}".format(e["text"], e["error"]) for e in d["errors"]])
            return (head + "<table><tr><th>status</th><th>次数</th><th>文本</th>"
                    "<th>last_error</th></tr>{}</table>".format(rows) + errs
                    + "<div class='dim'>{}</div>".format(_e(d["by_status"])))
        if key == "errors":
            parts = []
            # 跳过 `_note` 这类以下划线开头的说明字段（不是日志名）
            files = {k: v for k, v in d.items() if not k.startswith("_")}
            for name, v in files.items():
                cls = "warn" if v["count"] else "ok"
                parts.append('<span class="badge {}">{}: {}</span> '.format(
                    cls, _e(name), v["count"]))
            for name, v in files.items():
                if v["recent"]:
                    parts.append("<div class='dim'>{}</div>{}".format(
                        _e(name), _list_block(v["recent"])))
            if d.get("_note"):
                parts.append("<div class='dim'>{}</div>".format(_e(d["_note"])))
            return "".join(parts)
        if key in ("prompts", "storage", "config", "groups"):
            if key == "groups":
                rows = "".join(
                    "<tr><td class='mono'>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>"
                    .format(_e(cid[:8]), _e(len(g["buckets"])), _e(g["heat_people"]),
                            _e(",".join(g["playing"]) or "-"),
                            _e(", ".join("{}={}".format(u[:6], s) for u, s in g["heat_top"])))
                    for cid, g in sorted(d["groups"].items()))
                return ("<div class='dim'>共 {} 个群；主群 {}</div>"
                        "<table><tr><th>群</th><th>状态键</th><th>发情人数</th>"
                        "<th>进行中</th><th>发情值 top3</th></tr>{}</table>"
                        "<div class='dim'>骰子局: {}</div>"
                        .format(d["group_count"], _e((d.get("primary") or "")[:8]),
                                rows, _e(json.dumps(d.get("dice"), ensure_ascii=False))))
            return _kv_table(d)
        return _kv_table(d)

    cards = []
    for key, title, _ in SECTIONS:
        s = secs.get(key, {})
        cards.append(
            "<section><h2>{}<small>{} ms{}</small></h2><div class='body'>{}</div></section>"
            .format(_e(title), s.get("ms", "-"),
                    "" if s.get("ok", True) else " · 失败", body(key)))
    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>小小糯 运维后台</title><style>{}</style></head><body>"
            "<header><h1>小小糯 运维后台</h1>"
            "<span class='dim'>{}</span>"
            "<span class='dim'>只读</span>"
            "<span style='margin-left:auto'>"
            "<a href='/api/summary' style='color:#7aa2f7'>JSON</a> · "
            "<a href='/' style='color:#7aa2f7'>刷新</a> · "
            "<a href='/logout' style='color:#7aa2f7'>退出</a>"
            "</span></header><main>{}</main>"
            "<footer>{} · 只读面板 · 所有数据均来自本机文件，页面不接受任何写入</footer>"
            "</body></html>").format(_CSS, _e(data["generated_at"]), "".join(cards),
                                     _e(VERSION))


def render_login(error="", notice="", locked_for=0, user=""):
    """登录页。**不带任何数据** —— 没登录的人不该看到一个字节的面板内容。"""
    if locked_for > 0:
        msg = ('<div class="msg bad">尝试次数过多，请 {} 后再试。</div>'
               .format(_ago(locked_for)))
    elif error:
        msg = '<div class="msg bad">{}</div>'.format(_e(error))
    elif notice:
        msg = '<div class="msg ok">{}</div>'.format(_e(notice))
    else:
        msg = ""
    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>登录 · 小小糯 运维后台</title><style>{css}"
            ".box{{max-width:330px;margin:12vh auto;background:#181c25;"
            "border:1px solid #262c39;border-radius:9px;padding:20px}}"
            "label{{display:block;color:#8b95a7;margin:10px 0 4px}}"
            "input{{width:100%;box-sizing:border-box;background:#0f1218;color:#d8dee9;"
            "border:1px solid #2b3140;border-radius:6px;padding:8px;font-size:14px}}"
            "button{{width:100%;margin-top:16px;padding:9px;border:0;border-radius:6px;"
            "background:#2f6feb;color:#fff;font-size:14px;cursor:pointer}}"
            ".msg{{padding:8px 10px;border-radius:6px;margin-bottom:10px;font-size:12px}}"
            ".msg.bad{{background:#4a1f27;color:#ff8b9a}}"
            ".msg.ok{{background:#1f3d2b;color:#7ee2a8}}"
            "h1{{font-size:15px;margin:0 0 4px}}.sub{{color:#6c7686;font-size:12px}}"
            "</style></head><body><div class='box'>"
            "<h1>小小糯 运维后台</h1>"
            "<div class='sub'>只读面板 · 请登录</div>"
            "{msg}"
            "<form method='POST' action='/login' autocomplete='off'>"
            "<label for='u'>账号</label>"
            "<input id='u' name='username' value='{user}' autocomplete='username' autofocus>"
            "<label for='p'>密码</label>"
            "<input id='p' name='password' type='password' autocomplete='current-password'>"
            "<button type='submit'>登录</button>"
            "</form></div></body></html>").format(css=_CSS, msg=msg, user=_e(user))


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

_sessions = {}                   # sid -> 过期时间戳
_fails = {}                      # ip -> [失败次数, 解锁时间戳]


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


class Handler(BaseHTTPRequestHandler):
    server_version = "dzmm-admin/" + VERSION

    def _send(self, code, body, ctype, extra_headers=()):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # 没有外链资源；顺手把 Referer 掐掉
        self.send_header("Referrer-Policy", "no-referrer")
        # 纯自包含页面（无 CDN、无内联 JS），可以直接上严格 CSP
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _redirect(self, location, extra_headers=()):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()

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

    def do_POST(self):
        """**唯一**允许的非 GET 路径是 /login。

        面板本身仍然一处写操作都没有：登录只动进程内存里的会话表和 Cookie，
        既不碰数据库也不碰文件。`tests/test_admin_dashboard.py` 里
        `test_only_login_accepts_post` 钉住这条。
        """
        _prune()
        u = urlparse(self.path)
        ip = _client_ip(self)
        if u.path != "/login":
            self._send(405, "read-only：只接受 GET/HEAD（登录用 POST /login）\n",
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
            self._send(400, render_login(error="请求格式不对"), "text/html; charset=utf-8")
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
        self._send(405, "read-only：只接受 GET/HEAD（登录用 POST /login）\n",
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
