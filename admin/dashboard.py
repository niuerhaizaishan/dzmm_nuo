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

2. **对外的边界在 compose 的端口映射上，不在容器内。**
   容器内监听 `0.0.0.0:8090`（默认），而 `docker-compose.prod.yml` 里发布的是
   `127.0.0.1:8090:8090` —— **只有宿主机回环能连**，公网碰不到。
   访问方式是 SSH 隧道：
       ssh -L 8090:127.0.0.1:8090 root@47.237.152.187
   然后本地浏览器开 http://127.0.0.1:8090。

   ⚠️ 为什么容器内**不能**绑 127.0.0.1：docker 的端口发布是由 `docker-proxy`
   从**容器 IP**（不是回环）转发进来的，容器内绑回环会让发布出来的端口
   根本连不上 —— 第一次部署就是这么失败的（宿主机 curl 返回 000，
   而 `ss` 显示 docker-proxy 明明在 127.0.0.1:8090 上听着）。
   **真正的安全边界是那条映射**：绝不要把它改成 `8090:8090`。
   这台机器上 noVNC（6080）与 x11vnc（5900）目前仍是 0.0.0.0（P0-b 待办），
   在那种前提下再对外开一个后台端口是不负责任的。

3. **跑在容器内，不新增容器。**
   容器内是 Python 3.11 且已有拆好的 `runtime/*`，能直接复用 bot 同款代码读状态，
   不会出现"后台照着 schema 抄一遍、然后漂移"的问题。宿主机只有 Python 3.6，
   而且 1.8G 内存已经被否决过一次"双容器"方案。

### 开关

- `DZMM_ADMIN=off`  不启动（entrypoint 读这个）
- `DZMM_ADMIN_PORT` 默认 8090；`DZMM_ADMIN_HOST` 默认 `0.0.0.0`（见上面第 2 条）
- `DZMM_ADMIN_PUBLIC=on` 声明"这条端口发布到了回环之外"（即能被别人连到）
- `DZMM_ADMIN_TOKEN` 令牌。**`PUBLIC=on` 而没有令牌时，本进程拒绝启动** ——
  这样"忘了设令牌就把面板挂到公网"是一个启动失败，而不是一次静默裸奔。
"""
import html
import hmac
import json
import os
import re
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
COOKIE_NAME = "dzmm_admin_token"

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
    """群清单/静默群/筛查群。**不 import bot** —— 那会拉起 playwright。

    `bot.py` 里这几张表是源码常量，直接按 AST 取字面量，既不执行代码也不会漂。

    ⚠️ set 要转成 **list**：JSON 没有 set，`json.dumps(default=str)` 会把它变成
    `"{'a', 'b'}"` 这样的字符串 —— 长度、内容全错，而且**看起来还挺像那么回事**。
    第一次就是这么把 `SCREEN_CIDS`（3 个群）显示成长度 120 的。
    """
    import ast
    out = {"source": str(ROOT / "bot.py"), "unresolved": []}
    try:
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": "解析 bot.py 失败: {}".format(e)}
    wanted = ("GROUPS", "MUTE_CIDS", "SCREEN_CIDS", "PUSH_CIDS", "PRIMARY_CID",
              "ROOM_CIDS")
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name) or tgt.id not in wanted:
            continue
        try:
            val = ast.literal_eval(node.value)
        except Exception:
            # 例如 `ROOM_CIDS = set(ROOMS.keys())` —— 是调用不是字面量。
            # 老实报告"没求值"并给出表达式，不要装作读到了。
            out["unresolved"].append(
                {"name": tgt.id, "expr": ast.unparse(node.value)[:80]})
            continue
        out[tgt.id] = sorted(val) if isinstance(val, (set, frozenset)) else val
    out["group_count"] = len(out.get("GROUPS") or [])
    out["muted_count"] = len(out.get("MUTE_CIDS") or [])
    out["screen_count"] = len(out.get("SCREEN_CIDS") or [])
    out["push_count"] = len(out.get("PUSH_CIDS") or [])
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
            "<span style='margin-left:auto'><a href='/api/summary' "
            "style='color:#7aa2f7'>JSON</a> · <a href='/' style='color:#7aa2f7'>刷新</a>"
            "</span></header><main>{}</main>"
            "<footer>{} · 只绑 127.0.0.1，请用 SSH 隧道访问 · "
            "ssh -L 8090:127.0.0.1:8090 root@&lt;host&gt;</footer>"
            "</body></html>").format(_CSS, _e(data["generated_at"]), "".join(cards),
                                     _e(VERSION))


# ---------------------------------------------------------------- HTTP

def _env_token():
    return (os.environ.get("DZMM_ADMIN_TOKEN") or "").strip()


def _truthy(v):
    return (v or "").strip().lower() in ("on", "1", "yes", "true")


def _token_from_cookie(handler):
    raw = handler.headers.get("Cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME and v:
            return v
    return ""


def _token_ok(handler, query):
    """令牌校验。未配置令牌 = 只读面板当作"仅回环可达"（由启动闸门保证）。"""
    want = _env_token()
    if not want:
        return True
    got = (handler.headers.get("X-Admin-Token")
           or (query.get("token") or [""])[0]
           or _token_from_cookie(handler))
    return hmac.compare_digest(str(got), want)


def _token_just_supplied(handler, query):
    """这次请求是不是**用查询串**带对了令牌 —— 是的话要种 cookie。

    没有这一步，页面里那些站内链接（刷新 / JSON）不会带上令牌，点一下就 403。
    """
    want = _env_token()
    return bool(want) and hmac.compare_digest(
        str((query.get("token") or [""])[0]), want)


class Handler(BaseHTTPRequestHandler):
    server_version = "dzmm-admin/" + VERSION

    def _send(self, code, body, ctype, extra_headers=()):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # 令牌可能出现在查询串里 —— 别让它顺着 Referer 漏给第三方
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not _token_ok(self, q):
            self._send(403, "forbidden（缺 DZMM_ADMIN_TOKEN 令牌）\n",
                       "text/plain; charset=utf-8")
            return
        headers = ()
        if _token_just_supplied(self, q):
            # HttpOnly：JS 读不到；SameSite=Lax：跨站请求不带。
            # 不开 Secure —— 这套是 http 明文，开了 cookie 就发不出去（见 README 的说明）。
            headers = (("Set-Cookie",
                        "{}={}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
                        .format(COOKIE_NAME, _env_token())),)
        if u.path in ("/healthz", "/health"):
            self._send(200, "ok\n", "text/plain; charset=utf-8", headers)
            return
        try:
            data = collect()
        except Exception as e:                                  # noqa: BLE001
            self._send(500, "collect failed: {}\n".format(e),
                       "text/plain; charset=utf-8")
            return
        if u.path == "/api/summary":
            self._send(200, json.dumps(data, ensure_ascii=False, default=str),
                       "application/json; charset=utf-8", headers)
        elif u.path in ("/", "/index.html"):
            self._send(200, render_html(data), "text/html; charset=utf-8", headers)
        else:
            self._send(404, "not found\n", "text/plain; charset=utf-8")

    def _refuse(self):
        self._send(405, "read-only：只接受 GET/HEAD\n", "text/plain; charset=utf-8")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

    def log_message(self, fmt, *args):
        pass                                                    # 不刷日志


_STARTED = time.time()


def main(argv=None):
    if (os.environ.get("DZMM_ADMIN") or "on").strip().lower() in ("off", "0", "no", "false"):
        print("[admin] DZMM_ADMIN=off，不启动运维后台")
        return 0
    host = os.environ.get("DZMM_ADMIN_HOST") or DEFAULT_HOST
    port = int(os.environ.get("DZMM_ADMIN_PORT") or DEFAULT_PORT)
    public = _truthy(os.environ.get("DZMM_ADMIN_PUBLIC"))
    token = _env_token()

    # 失效关闭（fail closed）：声称"发布到回环之外"却没有令牌 -> 直接不启动。
    # 这一条把"忘了设令牌就把只读面板挂到公网"变成一次**启动失败**，
    # 而不是一次静默裸奔。容器日志里会留下这行，一眼能看见。
    if public and not token:
        print("[admin] !! 拒绝启动：DZMM_ADMIN_PUBLIC=on（会对外可达）"
              "但没有设置 DZMM_ADMIN_TOKEN。")
        print("[admin]    要么在 .env 里给一个随机令牌，要么把端口映射改回 127.0.0.1。")
        return 2

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print("[admin] 运维后台已启动 http://{}:{}  只读 / 令牌={}".format(
        host, port, "开" if token else "关"))
    if public:
        print("[admin] 对外发布：DZMM_ADMIN_PUBLIC=on（已经要求令牌，且应配合安全组"
              "只放行你自己的出口 IP）。")
    else:
        print("[admin] 对外边界 = compose 的端口映射（应为 127.0.0.1:{}:{}）。"
              "不要在没设令牌的情况下改成对外映射。".format(port, port))
    if host in ("127.0.0.1", "localhost", "::1"):
        print("[admin] ⚠️ 绑的是回环：compose 发布会连不上（docker-proxy 从容器 IP 转发）。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
