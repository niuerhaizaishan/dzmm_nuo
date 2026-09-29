#!/usr/bin/env python3
"""DZMM bot 健康巡检：每 5 分钟检查进程/资源/token/续期/代理/Socket.IO，异常发微信告警。

定位：补 bot 内部「事件驱动告警」的盲区——进程挂/双进程、CPU卡死、内存泄漏、
token 续期失败、代理断、Socket.IO 断线（这些 bot 自己感知不到或报不了）。

告警策略（安静=正常）：
- 明确异常：首次出现立即告警，持续期间去重不刷屏，恢复后发「已恢复」通知
- CPU：连续 3 次采样（15 分钟）>50% 才告警（防瞬时峰值误报）
- 状态持久化到 check_health_state.json，跨次去重/去抖

用法：
    python check_bot_health.py          # 正常静默，异常发微信
"""
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import env_config

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "check_health_state.json"
PROXY = env_config.get_proxies()
TZ8 = timezone(timedelta(hours=8))

CPU_HIGH_THRESHOLD = 50.0       # CPU% 阈值
CPU_HIGH_COUNT_ALERT = 3        # 连续 N 次高才告警（N×5min）
MEM_THRESHOLD_KB = 500000       # 内存 RSS 阈值 500MB
ACCESS_EXPIRE_ALERT_SEC = -300  # access_token 过期超过 5 分钟（续期没续上）
REFRESH_WARN_DAYS = 7           # refresh_token 剩余 < 7 天预告警（到期前留出重登窗口）
REFRESH_TOKEN_TTL_SEC = 30 * 86400  # Supabase refresh_token 默认有效期 30 天（从 last_sign_in_at 起算）
PROFILES_MAX_KB = 5120          # profiles.json 大小阈值 5MB，超过提示优化档案存储
OUTBOX_PENDING_MAX = 20         # 出站队列积压阈值（条）——超过说明发送通道可能卡住


def log(msg: str, to_stdout: bool = False):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(ROOT / "check_health.log", "a") as f:
        f.write(line + "\n")
    if to_stdout:
        print(msg)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_alert(msg: str):
    """发飞书告警（静默失败）。"""
    try:
        sys.path.insert(0, str(ROOT))
        from wx_alert import send_weixin
        send_weixin(msg)
    except Exception:
        pass


def _trigger_relogin() -> None:
    """触发 headless_relogin 主动重登（后台，刷新 refresh_token 实现永续）。"""
    try:
        subprocess.Popen(
            [sys.executable, str(ROOT / "headless_relogin.py")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        log("已触发 headless_relogin 主动重登（refresh_token 到期前轮换）")
    except Exception as e:
        log("触发主动重登失败: " + str(e)[:60])


# ---------- 各监控项检查 ----------

def check_process() -> tuple:
    """返回 (进程数, pid列表)。排除 --greet/--sync 定时任务。"""
    r = subprocess.run(["pgrep", "-f", "bot.py"], capture_output=True, text=True)
    all_pids = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
    # 排除定时任务（--greet / --sync）
    pids = []
    for pid in all_pids:
        try:
            with open(f"/proc/{pid}/cmdline", "r") as f:
                cmdline = f.read().replace("\x00", " ")
        except Exception:
            pids.append(pid)  # 读不到 cmdline，保守保留
            continue
        if "--greet" not in cmdline and "--sync" not in cmdline:
            pids.append(pid)
    return len(pids), pids


def check_cpu_mem(pid: str) -> tuple:
    """返回 (cpu%, rss_kb)。"""
    r = subprocess.run(["ps", "-o", "%cpu=,rss=", "-p", pid], capture_output=True, text=True)
    parts = r.stdout.strip().split()
    if len(parts) >= 2:
        try:
            return float(parts[0]), int(parts[1])
        except ValueError:
            pass
    return None, None


def check_token() -> dict:
    """返回 {access_remain_sec, refresh_remain_days}。"""
    result = {"access_remain_sec": None, "refresh_remain_days": None}
    try:
        sys.path.insert(0, str(ROOT))
        import token_store
        import base64
        cookie = token_store.get_cookie("sb-rls-auth-token")
        if cookie:
            v = cookie.get("value", "")
            payload = v[len("base64-"):] if v.startswith("base64-") else v
            payload += "=" * (-len(payload) % 4)
            d = json.loads(base64.b64decode(payload))
            result["access_remain_sec"] = d.get("expires_at", 0) - time.time()
            # refresh_token 剩余：从 last_sign_in_at + Supabase 默认 30 天有效期估算。
            # 注意不能用 cookie.expires（那是 cookie 存活期 ~400 天，不是 refresh_token 有效期）。
            lsat = (d.get("user") or {}).get("last_sign_in_at", "")
            if lsat:
                try:
                    from datetime import datetime, timezone
                    t = datetime.fromisoformat(lsat.replace("Z", "+00:00"))
                    issued = t.timestamp()
                    result["refresh_remain_days"] = (issued + REFRESH_TOKEN_TTL_SEC - time.time()) / 86400
                except Exception:
                    pass
    except Exception:
        pass
    return result


def check_session_state() -> dict:
    """读业务探针结果（`renew_token.py` 写的 `data/session_state.json`）—— P12-AJ。

    返回 `{"dead": bool, "probe": str, "ago": 分钟}`：
      · `dead=True` 只在**探针说业务调用也 401** 时为真（会话真失效）；
      · 文件太旧（>30 分钟）或不存在 ⇒ 不下结论（dead=False），免得重启初期误报；
      · access_token 过期**不算** dead —— 那只是只读接口的事。
    """
    out = {"dead": False, "probe": "", "ago": 0.0}
    try:
        p = env_config.data_dir() / "session_state.json"
        if not p.exists():
            return out
        d = json.loads(p.read_text(encoding="utf-8"))
        ago = (time.time() - float(d.get("at") or 0)) / 60.0
        out["probe"] = str(d.get("probe") or "")
        out["ago"] = ago
        if ago <= 30 and d.get("alive") is False:
            out["dead"] = True
    except Exception:
        pass
    return out


def check_renew() -> bool:
    """renew_token.log 最近 15 分钟内是否有**真失败**。

    P12-AJ：`/api/auth/token` 的 401 现在只记 `NOTE:`（它不是续期接口，401 是常态），
    所以这里只看 `FAIL:`（真错误）与 `SESSION_DEAD:`（会话真死）—— 不再被 401 误报。
    """
    log_path = ROOT / "renew_token.log"
    if not log_path.exists():
        return False
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        now = time.time()
        for line in reversed(lines):
            if "FAIL" in line or "SESSION_DEAD" in line:
                # 解析时间戳（格式 YYYY-MM-DD HH:MM:SS）
                try:
                    ts = line[:19]
                    t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ8).timestamp()
                    if now - t < 900:  # 15 分钟内
                        return True
                except ValueError:
                    continue
                break
    except Exception:
        pass
    return False


def check_network() -> str:
    """返回网络状态：'ok' 正常 / 'captcha' 撞验证码 / 'down' 不通。
    容器/云端无 lsof，改为直测可达性 + 识别验证码（418 或重定向 __captcha）。"""
    try:
        import requests
        resp = requests.get(env_config.get_dzmm_origin() + "/", proxies=PROXY, timeout=10, allow_redirects=False)
        if resp.status_code == 200:
            return "ok"
        if resp.status_code == 418:
            return "captcha"
        if resp.status_code in (301, 302, 307, 308):
            loc = resp.headers.get("location", "")
            if "__captcha" in loc or "captcha" in loc.lower():
                return "captcha"
        return "down"
    except Exception:
        return "down"


def check_sio() -> bool:
    """bot.log 最近 5 分钟是否有 Socket.IO 连接失败。"""
    log_path = ROOT / "bot.log"
    if not log_path.exists():
        return False
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        now = time.time()
        # 解析最近几行的 [sio] 日志
        for line in reversed(lines[-200:]):
            if "[sio]" not in line:
                continue
            # 提取时间戳（HH:MM:SS）
            ts = line[:8]
            try:
                today = datetime.now(TZ8).strftime("%Y-%m-%d")
                t = datetime.strptime(f"{today} {ts}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ8).timestamp()
                if now - t < 300:  # 5 分钟内
                    if "连接失败" in line or "重试" in line:
                        return True
                else:
                    break  # 超出 5 分钟窗口，停止
            except ValueError:
                continue
    except Exception:
        pass
    return False


def check_profiles_size() -> int:
    """返回 profiles.json 大小（KB），不存在或读失败返回 0。"""
    p = ROOT / "profiles.json"
    try:
        if p.exists():
            return p.stat().st_size // 1024
    except Exception:
        pass
    return 0


def check_outbox() -> dict | None:
    """只读看一眼出站队列：`{pending, failed, last}`；读不到返回 None。

    P12-AB（2026-09-27 体检）：2026-09-26 有一条星象卡发送失败
    （`last_error = sender 返回 False`）之后就永远躺在 `failed` 里 ——
    没有轮询、没有告警、没人知道（巡检才是唯一会"定期看全量"的角色）。
    这里用 `mode=ro` 打开，绝不锁生产库、绝不写。
    """
    db = ROOT / "data" / "outbox.db"
    if not db.exists():
        return None
    con = None
    try:
        con = sqlite3.connect("file:{}?mode=ro".format(db.as_posix()), uri=True,
                              timeout=5)
        con.row_factory = sqlite3.Row
        failed = int(con.execute(
            "SELECT COUNT(*) n FROM outbound WHERE status='failed'").fetchone()["n"])
        pending = int(con.execute(
            "SELECT COUNT(*) n FROM outbound WHERE status IN ('pending','leased')"
        ).fetchone()["n"])
        row = con.execute(
            "SELECT created_at, chatroom_id, text, last_error FROM outbound"
            " WHERE status='failed' ORDER BY created_at DESC LIMIT 1").fetchone()
        return {"pending": pending, "failed": failed,
                "last": dict(row) if row else None}
    except Exception as e:                                     # noqa: BLE001
        log("出站队列读不到：" + str(e)[:60])
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:                                  # noqa: BLE001
                pass


# ---------- 主逻辑 ----------

def main():
    # P12-A：`health_check` = 巡检总开关（巡检/告警/去抖状态全用它）。
    # 关掉后**只跳过本次巡检**，不写状态、不发告警 —— 相当于"暂时不巡检"。
    from runtime.features import on as _feat_on
    if not _feat_on("", "health_check"):
        log("健康巡检已关闭（功能开关 health_check=off），跳过")
        return 0
    state = load_state()
    alerts = state.setdefault("alerts", {})
    now = time.time()

    new_alerts = []   # 本次新发现的异常
    recovered = []    # 本次恢复的项

    def transition(key: str, is_bad: bool, alert_msg: str, recover_msg: str):
        """处理一项监控：异常→告警（去重），恢复→通知。"""
        prev = alerts.get(key, {}).get("alerted", False)
        if is_bad and not prev:
            alerts.setdefault(key, {})["alerted"] = True
            alerts[key]["since"] = now
            new_alerts.append(alert_msg)
        elif not is_bad and prev:
            alerts.setdefault(key, {})["alerted"] = False
            recovered.append(recover_msg)

    # 1. 进程
    count, pids = check_process()
    if count == 1:
        transition("process", False, "", "进程恢复正常（单实例）")
        pid = pids[0]
    elif count == 0:
        transition("process", True, "进程挂了（bot.py 未运行）", "")
        pid = None
    else:
        transition("process", True, f"双进程异常（{count} 个 bot.py 实例）", "")
        pid = pids[0]

    # 2. CPU / 内存（需进程存活）
    if pid:
        cpu, rss = check_cpu_mem(pid)
        # CPU 去抖：连续 N 次高才告警
        cpu_state = alerts.setdefault("cpu", {})
        if cpu is not None:
            if cpu > CPU_HIGH_THRESHOLD:
                cpu_state["high_count"] = cpu_state.get("high_count", 0) + 1
            else:
                cpu_state["high_count"] = 0
            if cpu_state.get("high_count", 0) >= CPU_HIGH_COUNT_ALERT and not cpu_state.get("alerted"):
                cpu_state["alerted"] = True
                cpu_state["since"] = now
                new_alerts.append(f"CPU 持续偏高（{cpu:.0f}%，连续 {CPU_HIGH_COUNT_ALERT} 次采样）")
            elif cpu_state.get("high_count", 0) == 0 and cpu_state.get("alerted"):
                cpu_state["alerted"] = False
                recovered.append("CPU 恢复正常")
        # 内存：单次超阈值
        if rss is not None:
            mem_state = alerts.setdefault("mem", {})
            if rss > MEM_THRESHOLD_KB and not mem_state.get("alerted"):
                mem_state["alerted"] = True
                mem_state["since"] = now
                new_alerts.append(f"内存占用过高（{rss/1024:.0f}MB，阈值 500MB，疑似泄漏）")
            elif rss <= MEM_THRESHOLD_KB and mem_state.get("alerted"):
                mem_state["alerted"] = False
                recovered.append("内存恢复正常")

    # 3. token
    tk = check_token()
    access_remain = tk.get("access_remain_sec")
    refresh_days = tk.get("refresh_remain_days")
    # P12-A：`health_check` 是巡检总开关；`alert_token` 只管 token 类的两条告警。
    # 关掉 alert_token ⇒ 不再发 token 告警，但**续期/重登动作照常**（那是保命的，
    # 不该被"少发一条通知"牵连）。
    _alert_token = _feat_on("", "alert_token")
    # P12-AJ：**access_token 过期 ≠ 会话失效**。
    # `/api/auth/token` 只是把会话里当前的 access_token 读出来回显（实测：会话新鲜时
    # 返回的 token 与本地逐字节相同），过期后它必然 401 —— 而业务调用往往照常工作。
    # 以前按"过期 5 分钟"告警，是全项目最大的一条误报源（renew 日志里 1220 次
    # `SKIP: token 未变化`、80 次 emergency 让 bot 每小时自杀重启，多半由此而来）。
    # 现在改判**业务探针**结果（renew_token.py 每轮写 data/session_state.json）。
    sess = check_session_state()
    if _alert_token:
        transition("token", sess.get("dead", False),
                   "会话已失效（业务调用也未授权），已触发重登"
                   + (f"（探针 {sess.get('probe')}，{sess.get('ago', 0):.0f} 分钟前）"
                      if sess.get("probe") else ""),
                   "会话恢复正常（业务调用可用）")
    if refresh_days is not None and refresh_days < REFRESH_WARN_DAYS:
        was_alerted = alerts.get("refresh_token", {}).get("alerted", False)
        if _alert_token:
            transition("refresh_token", True,
                       f"refresh_token 即将到期（剩 {refresh_days:.0f} 天），需重新登录",
                       "")
        # 首次触发（剩余<7天）时主动重登，刷新 refresh_token 实现"永续"（避免到期失效）
        if not was_alerted:
            _trigger_relogin()

    # 4. 续期脚本
    transition("renew", check_renew(), "续期脚本失败（renew_token.log 出现 FAIL）",
               "续期脚本恢复正常")

    # 4.5 profiles.json 大小观察点（超过阈值提示优化，只告警不恢复）
    pkb = check_profiles_size()
    if pkb > PROFILES_MAX_KB:
        transition("profiles_size", True,
                   f"profiles.json 已涨到 {pkb/1024:.1f}MB（阈值 {PROFILES_MAX_KB//1024}MB），建议优化档案存储",
                   "")

    # 5. 网络 / 验证码
    net_state = check_network()
    mode = "代理" if env_config.get_proxy_url() else "直连"
    if net_state == "captcha":
        transition("captcha", True,
                   "撞验证码（迷宫滑块）——可能 DZMM 平台被攻击或本机 IP 被风控，建议先停手观察、别反复重试",
                   "")
        transition("proxy", False, "", "网络恢复正常")
    elif net_state == "ok":
        transition("captcha", False, "", "验证码解除，恢复正常")
        transition("proxy", False, "", "网络恢复正常")
    else:
        transition("proxy", True,
                   f"网络异常（{mode}访问 dzmm.ai 不通）",
                   "网络恢复正常")

    # 6. Socket.IO
    transition("sio", check_sio(), "Socket.IO 连接失败/重试", "Socket.IO 连接恢复正常")

    # 7. 出站队列（P12-AB）：**新增失败**就告警（单条也算），积压太多也告警。
    #    为什么按"新增"而不是"有失败"：失败的记录按设计不会自动重发，
    #    会长期留在表里 —— 只要有失败就告警会变成永久刷屏。
    ob = check_outbox()
    if ob is not None:
        ob_state = alerts.setdefault("outbox", {})
        prev_failed = int(ob_state.get("failed", 0))
        cur_failed = int(ob.get("failed") or 0)
        if cur_failed > prev_failed:
            last = ob.get("last") or {}
            new_alerts.append(
                "出站队列新增 {} 条发送失败（累计 {}）：{} [{}] {}".format(
                    cur_failed - prev_failed, cur_failed,
                    str(last.get("chatroom_id") or "")[:8],
                    len(str(last.get("text") or "")),
                    str(last.get("text") or "")[:40].replace("\n", " ")))
        ob_state["failed"] = cur_failed
        transition("outbox_pending", int(ob.get("pending") or 0) > OUTBOX_PENDING_MAX,
                   "出站队列积压 {} 条（发送通道可能卡住）".format(ob.get("pending")),
                   "出站队列恢复通畅")

    # 汇总发送
    if new_alerts or recovered:
        parts = []
        if new_alerts:
            parts.append("🚨【小小糯bot 健康巡检】\n" + "\n".join("• " + m for m in new_alerts))
        if recovered:
            parts.append("✅【小小糯bot 健康巡检】已恢复\n" + "\n".join("• " + m for m in recovered))
        msg = "\n\n".join(parts)
        send_alert(msg)
        log("告警已发: " + ("; ".join(new_alerts) if new_alerts else "") +
            (" | 恢复: " + "; ".join(recovered) if recovered else ""))
    else:
        log("巡检正常，无告警")

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
