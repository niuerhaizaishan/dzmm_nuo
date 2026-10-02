#!/usr/bin/env python3
"""DZMM token 续期：调 /api/auth/token 拿新 access_token，写回 token JSON。

方案 A（热更新）：每 5 分钟跑一次（entrypoint 循环），提前 600 秒续期，
续期成功后只写回 token JSON，**不再杀 bot 重启**——bot 的 send() 会在发消息前
热更新 cookie（从 JSON 读最新值 add_cookies），实现零中断。

兜底：连续 3 次续期 FAIL 时写 token.emergency 信号文件，bot 主循环检测到后
主动退出，交给 entrypoint 重启（中断约 20 秒）。

原理：access_token 1 小时过期，但 refresh_token（12 字符）长期有效（约 400 天）。
/api/auth/token 会用 refresh_token 换新 access_token，但只返回响应、不更新存储。
本脚本把新 access_token 写回 token JSON（token_store），让读消息和发消息都用新 token。

用法：
    python renew_token.py
"""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import env_config

ROOT = Path(__file__).parent
PROXY = env_config.get_proxies()
COOKIE_NAME = "sb-rls-auth-token"

# P12-BL：所有"非浏览器"的平台请求都用**浏览器 UA**。
# 依据：同一端点、同一时刻，`Python-urllib/3.11` 被 WAF 回 403，而 Chrome / DZMM-Agent
# UA 都是 200（`tools/probe_ua_wall.py` 实测）。requests 默认 UA 那次也是 200，
# 但它是典型爬虫签名，换掉是零成本保险。
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# 最近一次 /api/auth/token 的**原始**响应（P12-BL：给失败日志用，别再只听我们的翻译）
LAST_HTTP = {"status": 0, "body": ""}

RENEW_AHEAD_SEC = 600          # 提前 10 分钟续期（消除过期窗口）
EMERGENCY_FILE = ROOT / "token.emergency"       # 连续 FAIL 信号文件（bot 消费后退出）
FAIL_STATE_FILE = ROOT / "renew_fail_state.json"  # 连续 FAIL 计数（跨进程持久化）
EMERGENCY_COOLDOWN_SEC = 3600  # emergency 冷却：1 小时内不重复写（防"refresh失效+撞验证码"反复重启）
EMERGENCY_STATE_FILE = ROOT / "renew_emergency_state.json"  # 上次 emergency 时间戳
SESSION_STATE_FILE = env_config.data_dir() / "session_state.json"   # 业务探针结论（P12-AJ）

# P12-AS：把"业务调用的失败信息"里哪些算**未授权**认全。
# 2026-09-29 的事故：平台把 401 回成 `Invalid authentication credentials`，
# 而旧分类器只认 未授权/unauthorized/401 → 判成"探针自己失败" → "不下结论、不重登"
# → 会话从 15:49 死到 18:11（142 分钟），茶水间彻底没反应，巡检还一路"正常"。
UNAUTH_MARKS = ("未授权", "unauthorized", "401", "invalid authentication",
                "invalid credentials", "authentication credentials",
                "jwt expired", "token has expired", "invalid_grant",
                "session not found", "not authenticated", "forbidden", "403")

# P12-AS：**"不下结论"必须有期限**。连续这么多分钟没验证通过（探针一直 error/异常），
# 就按"会话不可用"处理（先浏览器续期，再不行写 emergency 重登）。
# 为什么要它：宁可多重登一次，也不能像今天那样永远躺着等人发现。
RELOGIN_AFTER_MIN = 20


# ---------------------------------------------------------------- P12-BG：升级到"密码重登"
#
# 屋主 2026-09-30："升级"。
# 现场：12:23 会话**真死**（业务调用 `Invalid authentication credentials`），
# `renew_token`（每 5 分钟）只做"浏览器刷新"，而浏览器刷新**救不活已死的会话**；
# 它接着写 `token.emergency` —— 但那个信号只让 **bot.py 进程**重启，
# entrypoint 里唯一的密码登录（`headless_relogin.py`）**只在容器启动时跑**。
# 结果：会话死了 64 分钟没人救（"妹子群怎么不自动踢人了"就是这么来的），
# 直到人工进容器跑了一次 `headless_relogin.py` 才恢复。
#
# 现在把这条链接上：会话**真死**（unauth）或连续 `RELOGIN_AFTER_MIN` 分钟没能验证 →
# **自动跑一次密码重登**，成功后清退避、立刻让 Socket.IO 重连，并且**不重启 bot**
# （保住常驻页面与出站队列）；失败才落回原来的 emergency 重启路径。
# 撞验证码时 `headless_relogin.py` 自己会拉起 noVNC 人工辅助并发微信通知。
LOGIN_SCRIPT = ROOT / "headless_relogin.py"
RELOGIN_STATE_FILE = env_config.data_dir() / "renew_relogin_state.json"
RELOGIN_COOLDOWN_SEC = 1800     # 密码重登最多 30 分钟一次（别把风控刷起来）
RELOGIN_MIN_BAD_MIN = 2.0       # 会话连续坏 ≥2 分钟才升级（一次抖动别就重登）
# ⚠️ 2026-10-02 从 5.0 降到 2.0（屋主批准）。原值 5.0 的代价是**固定 5 分钟掉线**：
# 实测 02:46:33 写 emergency → 02:51:34 才恢复（"共坏 5 分钟"）、05:46:44 → 05:51:45
# 同样是 5 分钟 —— 恢复时长**恰好等于这个门槛**，说明瓶颈就是它，而不是登录本身
# （登录只要几秒）。2 分钟仍能挡掉"一次瞬时抖动"，但把掉线窗口砍掉一半以上。
# 治本的那一刀在 `runtime/mainloop.py` 的**定时抄回 token**（见那里的注释）。
RELOGIN_TIMEOUT_SEC = 300       # headless 登录最长跑多久


def _relogin_allowed() -> bool:
    """冷却检查：`RELOGIN_COOLDOWN_SEC` 内试过就不再试。"""
    try:
        d = json.loads(RELOGIN_STATE_FILE.read_text(encoding="utf-8"))
        return time.time() - float(d.get("ts") or 0) > RELOGIN_COOLDOWN_SEC
    except Exception:
        return True


def _stamp_relogin() -> None:
    try:
        RELOGIN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RELOGIN_STATE_FILE.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    except Exception as e:
        log("  写重登时间戳失败: " + str(e)[:50])


def _clear_backoffs() -> None:
    """清掉浏览器续期退避文件。

    登录成功后还留着它的话，`sio_subscribe._token_stale()` 会继续把重连退避
    顶到最长 2560 秒 —— 会话明明好了，消息却还读不到（实测白等十几分钟）。
    """
    for f in {BROWSER_BACKOFF_FILE, env_config.renew_backoff_file()}:
        try:
            Path(f).unlink()
            log("  已清退避文件: " + str(f))
        except FileNotFoundError:
            pass
        except Exception as e:                                 # noqa: BLE001
            log("  清退避文件失败 {}: {}".format(f, str(e)[:40]))


def _try_password_relogin() -> bool:
    """升级链：跑一次**密码登录**并用业务调用验证。成功返回 True。

    为什么必须是密码登录：`/api/auth/token` 是只读回显；浏览器刷新只在 refresh_token
    还有效时管用。会话真死时**只有重新登录**能救回来 —— 这正是今天人工做的那一步。
    """
    if _unverified_min() < RELOGIN_MIN_BAD_MIN:
        log("  （会话才坏 {:.0f} 分钟，到 {:.0f} 分钟再自动重登）".format(
            _unverified_min(), RELOGIN_MIN_BAD_MIN))
        return False
    if not _relogin_allowed():
        log("  密码重登在冷却期内（{} 分钟内试过一次），跳过".format(
            RELOGIN_COOLDOWN_SEC // 60))
        return False
    if not LOGIN_SCRIPT.exists():
        log("  找不到 " + str(LOGIN_SCRIPT) + "，无法自动重登")
        return False
    # ⚠️ 2026-10-01：`_stamp_relogin()` 从"起子进程**之前**"挪到"确认**真的试过**
    # 之后"（见下面两处调用）。原先无论子进程干了什么，都先把 30 分钟冷却打上 ——
    # 21:29 事故里 21:34:43 那次尝试**根本没开始登录**（P0 自锁，子进程被挡在
    # 180 秒外打印 SKIP），却把冷却打到 22:04，于是 21:42:44 的第二次尝试直接被
    # "冷却期内"挡掉。**一次"零工作"的尝试不该消耗恢复窗口。**
    # 规则：真试过（跑满超时 / 子进程确实开始登录）⇒ 计冷却（别刷风控）；
    #       没试过（起不来 / 被锁跳过）⇒ 不计，下一轮立刻可重试。
    # ⚠️ 这一行**故意不用 `to_stdout=True`**：那个参数会发微信告警，而"开始重登"
    # 不是用户需要立刻知道的事（结果才要）。用 print 保证它出现在 docker logs 里。
    log("ESCALATE: 会话不可用 → 自动跑一次密码重登（headless_relogin.py）")
    print("ESCALATE: 会话不可用 → 自动跑一次密码重登（headless_relogin.py）", flush=True)
    try:
        p = subprocess.run(
            [sys.executable, str(LOGIN_SCRIPT)], cwd=str(ROOT),
            capture_output=True, text=True, timeout=RELOGIN_TIMEOUT_SEC,
            # ⚠️ P0（2026-10-01 实锤）：`guard()` 只有**一把锁**
            # （runtime/session.py 的 LOCK_FILE = .session.lock），`reason` 只是写进
            # 锁文件的备注，不是两把锁。本函数是在 `guard("renew_token")` **内部**被
            # 调用的，子进程若再去抢同一把锁，必然等满 headless_relogin 的
            # timeout=180 然后打印 "SKIP: 另一个进程正在续期/重登" 放弃 ——
            # 这就是 2026-09-30（8.8h）与 2026-10-01 21:29 两次掉线都只能人工救的原因。
            # 显式告诉子进程"锁已由父进程持有"，它就不必（也不能）再抢。
            env={**os.environ, "PYTHONIOENCODING": "utf-8",
                 "DZMM_RELOGIN_UNDER_PARENT_LOCK": "1"})
    except subprocess.TimeoutExpired:
        _stamp_relogin()          # 跑满 300 秒 = 确实在尝试，必须计冷却
        log("  密码重登超时（{}s）".format(RELOGIN_TIMEOUT_SEC), to_stdout=True)
        return False
    except Exception as e:                                     # noqa: BLE001
        log("  密码重登起不来: " + str(e)[:80], to_stdout=True)
        return False
    for ln in (p.stdout or "").strip().splitlines()[-5:]:
        log("  [relogin] " + ln[:120])
    if (p.stderr or "").strip():
        log("  [relogin:err] " + (p.stderr or "").strip().splitlines()[-1][:120])
    if any("SKIP:" in ln for ln in (p.stdout or "").splitlines()):
        log("  密码重登被跳过（子进程没真正开始登录，锁被别人占着）→ "
            "不消耗 {} 分钟冷却，下一轮可立刻重试".format(
                RELOGIN_COOLDOWN_SEC // 60), to_stdout=True)
        return False
    _stamp_relogin()              # 真的试过了（不论成败）⇒ 计冷却，别把风控刷起来
    if p.returncode != 0:
        log("  密码重登没成（rc={}；撞验证码时它会自己拉 noVNC 并发微信）".format(
            p.returncode), to_stdout=True)
        return False
    probe = _api_probe()
    _write_session_state(probe)
    if probe != "ok":
        log("  密码重登说成功，但业务探针仍是 {} → 不算恢复".format(probe), to_stdout=True)
        return False
    _clear_backoffs()
    log("OK: 密码重登成功，会话已恢复（未重启 bot，退避已清）", to_stdout=True)
    return True


def read_token_cookie():
    """读 token JSON 里 sb-rls-auth-token 的 cookie 对象 + 解码后的 JSON。"""
    sys.path.insert(0, str(ROOT))
    import token_store
    cookie = token_store.get_cookie(COOKIE_NAME)
    if not cookie:
        return None, None
    v = cookie.get("value", "")
    payload = v[len("base64-"):] if v.startswith("base64-") else v
    payload += "=" * (-len(payload) % 4)
    try:
        d = json.loads(base64.b64decode(payload))
    except Exception:
        return None, None
    return cookie, d


def get_new_token():
    """调 /api/auth/token，用 refresh_token 换新 access_token。"""
    sys.path.insert(0, str(ROOT))
    import token_store
    import requests
    cookies = token_store.get_cookie_dict()
    # P12-BL：带上 **浏览器 UA**。实测（2026-09-30，`tools/probe_ua_wall.py`，同一端点同一时刻）：
    #     requests 默认 UA → 200 ；Mozilla/5.0 DZMM-Agent/1.0 → 200 ；Chrome UA → 200 ；
    #     `Python-urllib/3.11` → **403（WAF 拦截页）**
    # ⇒ 平台的 WAF 确实按 UA 挑战。`requests` 默认的 `python-requests/x.y` 是典型爬虫签名，
    # 属于"随时可能被挑战"的那一类；换成浏览器 UA 是零成本保险。
    r = requests.get(env_config.get_dzmm_origin() + "/api/auth/token", cookies=cookies,
                     timeout=15, proxies=PROXY, headers={"User-Agent": BROWSER_UA})
    # P12-BL：把**原始响应**留下来 —— 这几天 4~8 小时的"未授权"窗口我们一直不知道
    # 平台到底回了什么（只看到我们自己翻译过的"未授权"）。下次再出，日志里就有原文了。
    LAST_HTTP["status"] = getattr(r, "status_code", 0)
    LAST_HTTP["body"] = (getattr(r, "text", "") or "")[:240]
    return r.json()


def write_token_cookie(new_value: str) -> bool:
    """更新 token JSON 里 sb-rls-auth-token 的值（保留 domain/expires 等其他字段）。"""
    sys.path.insert(0, str(ROOT))
    import token_store
    return token_store.update_cookie(COOKIE_NAME, new_value)


def log(msg: str, to_stdout: bool = False):
    """写日志到 renew_token.log；to_stdout=True 时发飞书告警（云端无 cron deliver，脚本内告警）。

    ⚠️ 必须显式 `encoding="utf-8"`：巡检 `check_bot_health.check_renew()` 是按 UTF-8 读这个
    文件的，而 Windows 上 `open()` 默认是 GBK —— 不写死就会出现"日志读不出来"的假故障
    （测试里当场复现过：`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xce`）。
    """
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(ROOT / "renew_token.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if to_stdout:
        print(msg)
        try:
            from wx_alert import send_weixin
            ok = send_weixin("🚨【小小糯bot 续期】" + msg)
            # P12-BR-fix: 记录飞书发送结果（原来静默吞异常，8.8h 事故期间完全不知道发没发出去）
            with open(ROOT / "renew_token.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}   飞书告警: {'✅ 已发' if ok else '❌ 失败'}\n")
        except Exception as e:
            with open(ROOT / "renew_token.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}   飞书告警异常: {type(e).__name__}: {str(e)[:60]}\n")


def load_fail_streak() -> int:
    try:
        return json.loads(FAIL_STATE_FILE.read_text(encoding="utf-8")).get("streak", 0)
    except Exception:
        return 0


def save_fail_streak(streak: int):
    FAIL_STATE_FILE.write_text(json.dumps({"streak": streak}), encoding="utf-8")


def record_fail() -> bool:
    """记录一次续期失败。连续 3 次返回 True（触发 emergency 信号）。"""
    streak = load_fail_streak() + 1
    save_fail_streak(streak)
    if streak >= 3:
        try:
            EMERGENCY_FILE.write_text("renew 连续失败，请重启 bot\n", encoding="utf-8")
        except Exception:
            pass
        return True
    return False


def record_success():
    save_fail_streak(0)


def _emergency_ok() -> bool:
    """emergency 冷却检查：1 小时内已写过则返回 False（防反复触发重登）。"""
    try:
        data = json.loads(EMERGENCY_STATE_FILE.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) < EMERGENCY_COOLDOWN_SEC:
            return False
    except Exception:
        pass
    return True


def _write_emergency() -> None:
    """写 emergency 信号 + 记录时间戳（供冷却判断）。"""
    try:
        EMERGENCY_FILE.write_text("refresh 失效，请重新登录\n", encoding="utf-8")
    except Exception:
        pass
    try:
        EMERGENCY_STATE_FILE.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    except Exception:
        pass


BROWSER_BACKOFF_FILE = env_config.renew_backoff_file()   # P12-AI：放挂载的 data/ 下（重建不丢）
BROWSER_BACKOFF_SEC = 3600  # 浏览器续期失败后 1 小时不再试（每 1 小时试一次，避免反复打风控接口）
# P12-AJ：业务探针结果（会话活没活）。巡检与 Socket.IO 退避都看它，不再看 access_token 的 exp。
# （定义在文件上方常量区，这里不再重复定义 —— 两处同名常量会让人以为改了上面那处就生效。）


def _browser_backoff_ok() -> bool:
    """浏览器续期退避检查：失败后 1 小时内返回 False（不再试）。"""
    try:
        data = json.loads(BROWSER_BACKOFF_FILE.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) < BROWSER_BACKOFF_SEC:
            return False
    except Exception:
        pass
    return True


def _browser_backoff() -> None:
    """记录浏览器续期失败时间（写退避状态）。"""
    try:
        BROWSER_BACKOFF_FILE.parent.mkdir(parents=True, exist_ok=True)
        BROWSER_BACKOFF_FILE.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    except Exception:
        pass


def _browser_renew() -> int:
    """调 renew_token_browser.py 浏览器续期，返回退出码（0=成功）。"""
    import subprocess
    try:
        p = subprocess.run(
            [sys.executable, str(ROOT / "renew_token_browser.py")],
            cwd=str(ROOT), timeout=120, capture_output=True, text=True)
        if p.stdout.strip():
            log("  浏览器续期输出: " + p.stdout.strip()[-200:])
        return p.returncode
    except Exception as e:
        log("  浏览器续期调用异常: " + str(e)[:80])
        return 1


# ---------------------------------------------------------------- P12-BJ：告警去噪 + 节点探测
#
# 屋主 2026-09-30："（续期失效）这几天经常……"，查完发现：
#   · 每次坏都是**一个 4~8 小时的窗口**（87 个窗口，最大的 461 分钟）；
#   · 窗口里每 5 分钟一条日志、每小时一条 EMERGENCY 微信 —— 一个窗口能刷十几条；
#   · 而窗口的成因多半是**平台风控墙**（首页 418），不是会话死。
# 所以：告警按**窗口**算（进窗一条、出窗一条），窗口内只写日志；
# 并在进窗那一刻探一次 13 个镜像域名 —— 如果有一个在墙期间还能用，
# 就能把"等 4~8 小时"变成"换个域名几分钟"（`switch_node.py` 里本来就有这份清单，
# 但它自己把 418 排除了、所以从没触发过）。
BAD_WINDOW_FILE = env_config.data_dir() / "renew_bad_window.json"
# P12-BJ：每个墙窗口探一遍镜像域名的结果（JSONL）——"要不要自动换域名"靠它定
WALL_PROBE_FILE = env_config.data_dir() / "wall_probes.jsonl"

NODE_CANDIDATES = (
    "www.brimvale.xyz", "www.cindorel.xyz", "www.dawnmere.xyz", "www.echolore.xyz",
    "www.emberloft.xyz", "www.fernquill.xyz", "www.glimvane.xyz", "www.ivorune.xyz",
    "www.jadelune.xyz", "www.kestrove.xyz", "www.lumivale.xyz", "www.quovin.xyz",
)   # 与 switch_node.py 的清单一致（去掉当前在用的 www.dzmm.ai）


def _bad_window_start() -> float:
    try:
        return float(json.loads(BAD_WINDOW_FILE.read_text(encoding="utf-8")).get("started") or 0)
    except Exception:
        return 0.0


def _open_bad_window() -> bool:
    """坏窗口开始：返回 True = **这是本轮窗口第一次**（该发告警）。"""
    if _bad_window_start():
        return False
    try:
        BAD_WINDOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        BAD_WINDOW_FILE.write_text(json.dumps({"started": time.time()}), encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        pass
    return True


def _close_bad_window() -> float:
    """恢复：返回**坏了多少分钟**（0 = 本来就没坏过，不用报）。"""
    started = _bad_window_start()
    if not started:
        return 0.0
    try:
        BAD_WINDOW_FILE.unlink()
    except Exception:                                          # noqa: BLE001
        pass
    return (time.time() - started) / 60.0


def _probe_nodes(timeout: float = 4) -> list:
    """墙在的时候探一遍镜像域名：`[(host, 首页状态, 我们这个 cookie 能不能换到 token)]`。

    只读、不切换（切换要改 .env + 重启，得先有证据）。
    ⚠️ `timeout` 压到 4 秒：12 个域名 × 2 个请求，最坏也要 ~96 秒 —— 这段是在
    **持有会话锁**的情况下跑的（别人这轮会跳过续期，无害，但别拖太久）。
    """
    import requests
    import token_store
    cookies = token_store.get_cookie_dict()
    out = []
    t0 = time.time()
    for host in NODE_CANDIDATES:
        base = "https://" + host
        home = "?"
        try:
            r = requests.get(base + "/", timeout=timeout, proxies=PROXY,
                             headers={"User-Agent": "Mozilla/5.0 DZMM-Agent/1.0"})
            home = str(r.status_code)
        except Exception:                                      # noqa: BLE001
            home = "err"
        tok = "?"
        try:
            r2 = requests.get(base + "/api/auth/token", timeout=timeout, proxies=PROXY,
                              cookies=cookies)
            tok = "token-ok" if (r2.status_code == 200 and
                                 (r2.json() or {}).get("access_token")) else str(r2.status_code)
        except Exception:                                      # noqa: BLE001
            tok = "err"
        out.append((host, home, tok))
    # P12-BJ：把结果**落盘成结构化记录** —— "要不要自动换域名"要靠几个墙窗口的
    # 真实数据来定，光有日志行不好统计（`tools/analyze_wall_windows.py` 读它）。
    try:
        rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "origin_home": _home_status(),
               "elapsed": round(time.time() - t0, 1),
               "nodes": [{"host": h, "home": hm, "token": tk} for h, hm, tk in out]}
        WALL_PROBE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WALL_PROBE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:                                     # noqa: BLE001
        log("  写墙窗口记录失败（不影响探测）: " + str(e)[:60])
    return out


def _home_status() -> str:
    """首页状态：`"ok"`(200) / `"wall"`(418 或跳验证码) / `"err"`。

    ## P12-BI：为什么要分清"风控墙"和"会话真死"

    把几天的日志按窗口聚合（`tools/analyze_renew_windows.py`）之后真相很刺眼：
    那些"续期失效"窗口**全是 4.5~7.7 小时**、每 5 分钟失败一次，而且**都是自己好的**
    —— 恢复那一刻是 `OK: token 续期成功`（**同一份 cookie**，中间没登录过）。

    同一时间宿主机 `monitor_recover.log` 记的是**首页=418**（验证码墙，6570 次）。
    也就是说：**那几小时里会话根本没死，是平台把我们挡在风控墙外**；
    `未授权` 是墙挡出来的，不是 refresh_token 失效。旧日志把它写成
    "refresh_token 失效 → 写 emergency → 重启 bot 进程"，全是无用功（重启也过不了墙）。

    所以升级链必须**先看墙**：墙在 → 别重登（密码登录也过不去，只会白撞风控 + 刷告警）；
    墙不在 + 业务 401 → 才是真的会话死了，这时 P12-BG 的密码重登才有意义。
    """
    try:
        import requests
        r = requests.get(env_config.get_dzmm_origin() + "/", timeout=20, proxies=PROXY,
                         headers={"User-Agent": "Mozilla/5.0 DZMM-Agent/1.0"},
                         allow_redirects=True)
        # ⚠️ 先看验证码特征再判 200：有些路径会把验证码页**以 200 返回**，
        # 只看状态码就会把墙当成"通"（P12-BI 的测试专门钉了这一条）。
        # P12-BO：**403 也算墙** —— 实测平台的 WAF 对不被接受的 UA 就是回 403
        # （`tools/probe_ua_wall.py`：`Python-urllib/3.11` → 403，Chrome/DZMM-Agent → 200）。
        # 把它当"err（不下结论）"会白绕 20 分钟再去试重登，而重登同样过不去。
        if r.status_code in (418, 403) or "captcha" in (r.text or "").lower()[:400]:
            return "wall"
        if r.status_code == 200:
            return "ok"
        log("  首页状态异常: HTTP {}（既不是 200/418/403）".format(r.status_code))
        return "err"
    except Exception as e:                                     # noqa: BLE001
        log("  首页探测失败: " + str(e)[:60])
        return "err"


def _api_probe() -> str:
    """P12-AJ：业务探针 —— 用**真实业务调用**判断会话到底死没死。

    ## 为什么需要它（2026-09-29 实测推翻旧结论）

    `/api/auth/token` **不是续期接口** —— 它只是把会话里当前的 access_token
    读出来回声给我们：

      · 会话新鲜时（剩 3434 秒）调它 → 200，返回的 access_token 与本地**逐字节相同**；
      · 日志统计：`SKIP: token 未变化` **1220 次**、`OK: token 续期成功` 689 次
        （那 689 次是"抄到了服务端较新的 token"，不是我们续的）；
      · access_token 一到期（登录后 1 小时）→ 立刻 **401「未授权」**；
      · 浏览器身份访问也一样（页面加载 7 秒，cookie 与 refresh_token **零轮换**）。

    于是旧逻辑把"这个接口 401"误判成"refresh_token 失效" →
    每小时写一次 `token.emergency` → **bot 主动退出重启**（日志里 EMERGENCY 累计 80 次、
    bot.log 每小时一条"检测到 token.emergency 信号"）→ 重启又触发一次真登录 →
    攒风控、撞验证码（01:21 那次"迷宫滑块"）。而**业务调用几乎没报过 unauthorized**。

    所以：401 之后必须再问一次业务接口，才能决定要不要重登。

    返回 `"ok"`（业务可用）/ `"unauth"`（业务也 401，会话真死）/ `"error"`（探针本身失败）。
    """
    try:
        import token_store
        import dzmm
        cid = _probe_cid()
        if not cid:
            return "error"
        dzmm.api("chatroom.getMessages",
                 {"json": {"chatroomId": cid, "limit": 1, "before": None},
                  "meta": {"values": {"before": ["undefined"]}, "v": 1}},
                 token_store.get_cookie_dict(), timeout=15)
        return "ok"
    except Exception as e:                                     # noqa: BLE001
        msg = str(e)
        low = msg.lower()
        if any(k in low for k in UNAUTH_MARKS):
            return "unauth"
        return "error"


def _probe_cid() -> str:
    """业务探针打哪个群：配置里第一个启用的群；读不到就用已知的认证群兜底。"""
    try:
        import os as _os
        cfg = _os.environ.get("DZMM_CONFIG") or str(ROOT / "config" / "config.json")
        raw = json.loads(Path(cfg).read_text(encoding="utf-8"))
        for g in raw.get("groups") or []:
            if g.get("enabled", True) and g.get("cid"):
                return str(g["cid"])
    except Exception:
        pass
    return "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


def _write_session_state(probe: str) -> None:
    """把"会话活没活"落盘，供**巡检**与 **Socket.IO 退避**判用（P12-AJ）。

    以前这两处都拿 access_token 的 `expires_at` 当"会话是否有效"，
    而那个值过期并不代表会话失效（只读接口会 401，业务照常）——
    于是巡检误报、SIO 把重连退避拖到 1 小时。现在统一看这个文件。

    ⚠️ P12-AR（2026-09-29）：`probe` 现在不止两种取值 ——
        "ok"       会话验证过、活着
        "unauth"   业务调用也 401，**这才是会话真死**
        "error"    探针自己失败，不作结论
        "backoff"  撞验证码退避中：**循环活着，但这一轮没验证会话**
        "locked"   抢不到"唯一登录者"锁（别人在重登）：同上
        "captcha"  P12-BI：首页 418（平台风控墙）——那几小时的"未授权"是墙挡出来的，
                   **会话多半没死**，别重登、别重启，等墙解除
    所以读它的**三处**（巡检 `check_session_state`、SIO `_token_stale`、体检探针）
    都必须**只认 "unauth" 为死**。这条不是洁癖：`alive == False` 还把
    "backoff" 也算成死的话，一次验证码退避就会把 SIO 的重连退避顶到 1 小时
    —— 正是 P12-AJ 刚修掉的那个坑。

    ⚠️ P12-AS：另外维护两个时间戳 ——
        `ok_at`     最近一次"验证通过"的时刻
        `bad_since` 本轮"一直没通过"的起点（恢复 ok 时清空）
    `bad_since` 是给"不下结论必须有期限"用的：2026-09-29 15:49~18:11 的真实事故里，
    探针一直返回 `error`（平台回的是 "Invalid authentication credentials"，
    旧分类器认不出来）→ 一直"不下结论、不重登" → **会话死了 142 分钟没人管**。
    """
    try:
        SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        old_ok = old_bad = None
        try:
            old = json.loads(SESSION_STATE_FILE.read_text(encoding="utf-8"))
            old_ok = old.get("ok_at")
            if old_ok is None and old.get("probe") == "ok":
                old_ok = old.get("at")
            old_bad = old.get("bad_since")
        except Exception:
            pass
        now = time.time()
        if probe == "ok":
            ok_at, bad_since = now, None
        else:
            ok_at, bad_since = old_ok, (old_bad or now)
        SESSION_STATE_FILE.write_text(json.dumps(
            {"at": now, "alive": probe == "ok", "probe": probe,
             "ok_at": ok_at, "bad_since": bad_since}),
            encoding="utf-8")
    except Exception:
        pass


def _unverified_min() -> float:
    """连续多少分钟没"验证通过"了（0 = 刚验证过/还没有坏记录）。

    P12-AS：`_write_session_state` 在 probe != "ok" 时记下 `bad_since`，
    恢复 ok 时清空。这个数就是"不下结论"的计时器 —— 超过 `RELOGIN_AFTER_MIN`
    就必须动手，不能永远躺平（2026-09-29 的 142 分钟静默宕机就是这么来的）。
    """
    try:
        d = json.loads(SESSION_STATE_FILE.read_text(encoding="utf-8"))
        bs = d.get("bad_since")
        if not bs:
            return 0.0
        return (time.time() - float(bs)) / 60.0
    except Exception:
        return 0.0


def _notify_captcha() -> None:
    """撞验证码且浏览器续期也失败（可能 IP 级风控）→ 飞书告警 + 拉 noVNC 人工验证。"""
    try:
        from wx_alert import send_weixin
        ok = send_weixin("🚨【小小糯bot】撞验证码且浏览器续期也失败，可能 IP 级风控，"
                         "已拉起 noVNC 人工验证（收发通道不受影响，bot 不中断）")
        log("  飞书告警: " + ("✅ 已发" if ok else "❌ 失败"))
    except Exception as e:
        log("  飞书告警异常: " + str(e)[:60])
    try:
        import subprocess
        subprocess.Popen(
            [sys.executable, str(ROOT / "captcha_novnc.py")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        log("  已拉起 captcha_novnc.py（noVNC 人工验证）")
    except Exception:
        pass


def _renew_locked() -> int:
    """真正的续期逻辑；由 `main()` 在**持有会话锁**的前提下调用。"""
    # 1. 读现有 cookie
    cookie, d = read_token_cookie()
    if not cookie or not d:
        log("FAIL: 未读到 sb-rls-auth-token cookie", to_stdout=True)
        if record_fail():
            log("EMERGENCY: 连续 3 次续期失败，写 token.emergency 信号", to_stdout=True)
        return 1

    # 2. 提前"抄一手"：token 剩余 > 600 秒就记一笔"会话活着"后退出。
    # ⚠️ P12-AJ：这个接口**不会续期**（见 `_api_probe` 的说明），所以这里只是顺带
    # 刷新一下 session_state.json，让巡检/SIO 知道会话是好的。
    if d.get("expires_at", 0) - time.time() > RENEW_AHEAD_SEC:
        _write_session_state("ok")
        record_success()
        return 0  # token 还很新鲜，无需续期

    # 2.5 撞验证码退避检查：浏览器续期失败后 1 小时内跳过浏览器那条路
    #（避免撞验证码期间每 5 分钟开一次浏览器加剧风控）
    if not _browser_backoff_ok():
        # P12-BO（2026-09-30 19:52 事故）：退避**只该挡"浏览器续期"这一次尝试**，
        # 不该把整条升级链一起挡掉。那天的形状：
        #   19:52 浏览器续期撞验证码 → 写 1 小时退避
        #   → 之后每轮都在这里 return ⇒ 业务探针、墙判定、密码重登**全都走不到**
        #   → 会话真死 + 首页正常（本该重登）却瞎了 34 分钟；
        #     而心跳写的是 "backoff"、巡检只认 "unauth" 为死 ⇒ 谁都没告警。
        # 现在：退避期间**仍打一次业务探针**（很便宜），据此决定还能做什么。
        probe = _api_probe()
        if probe == "ok":
            log("撞验证码退避中，但业务探针正常 → 会话没死（刷心跳）")
            _write_session_state("ok")
            record_success()
            return 0
        if probe == "unauth":
            # ⚠️ 先写 unauth：这一轮结束时**不许**再用 backoff 盖掉它，
            # 否则巡检（只认 unauth 为死）又看不到会话已经真死。
            _write_session_state("unauth")
            if _home_status() == "ok" and _try_password_relogin():
                # 真死 + 没有风控墙 ⇒ 密码重登（它自带 30 分钟冷却，不会刷风控）
                _write_session_state("ok")       # 立刻把"活着"写回去（别等 main 那层）
                record_success()
                return 0
            log("撞验证码退避中，且业务调用未授权（会话真死）→ 保留 unauth 让巡警报", to_stdout=True)
            return 1
        log("撞验证码退避中，跳过续期（不打接口，避免加剧风控）")
        # P12-AR：这一轮没验证会话，但**循环是活的** —— 写一个 "backoff" 心跳。
        # 不写的话，退避期内 `session_state.json` 会一路变旧，
        # 巡检新加的"循环停摆"告警就会误报（实测踩过：续期路径不写心跳，
        # 15.9 分钟就被判"疑似停摆"）。"backoff" 不会被当成会话失效（只认 "unauth"）。
        _write_session_state("backoff")
        return 1

    # 3. 读一眼当前 token（`/api/auth/token` 是**只读回显**接口，不是续期接口）
    try:
        new = get_new_token()
    except Exception as e:
        log("FAIL: /api/auth/token 请求异常: " + str(e)[:80], to_stdout=True)
        if record_fail():
            log("EMERGENCY: 连续 3 次续期失败，写 token.emergency 信号", to_stdout=True)
        return 1
    new_at = new.get("access_token", "")
    if not new_at:
        # P12-BL：先把**原始响应**打出来（我们翻译出来的"未授权"不足以下结论）。
        log("  原始响应: HTTP {} body {}".format(
            LAST_HTTP.get("status"), (LAST_HTTP.get("body") or "").replace("\n", " ")))
        keys = list(new.keys())
        if "location" in keys:
            # 撞验证码（captcha_required）：切浏览器续期，不写 emergency、不停 bot
            log("撞验证码（" + str(new.get("error", ""))[:40] + "），切浏览器续期")
            if not _browser_backoff_ok():
                log("  浏览器续期退避中（{} 分钟内已失败过），跳过".format(
                    BROWSER_BACKOFF_SEC // 60))
                _write_session_state("backoff")     # P12-AR：循环活着，会话这轮没验证
                return 1
            rc = _browser_renew()
            if rc == 0:
                record_success()
                return 0
            log("  浏览器续期也失败，退避 {} 小时（不停 bot，收发通道不受影响）".format(
                BROWSER_BACKOFF_SEC // 3600))
            _browser_backoff()
            _notify_captcha()
            return 1
        else:
            # P12-AT（屋主 2026-09-29 拍板"要"）：**401 的第一反应是"用浏览器刷一次"**，
            # 而不是直接走重登。
            #
            # 为什么放在探针之前、放在这里：
            #   · 回显接口 401 = 手里的 access_token 过期了。**前端**这时才肯用
            #     refresh_token 去换新的（实测：token 还有 35 分钟有效期时，
            #     页面加载完照样原样返回 —— 所以"提前 10 分钟主动续"是死的，
            #     见上面 SKIP 分支的注释）；
            #   · 换成功 ⇒ **不重登、不重启**：bot 的常驻页面/出站队列全都保住，
            #     token JSON 一写回，bot 下次发消息就是新的（热更新）；
            #   · 2026-09-29 09:46 的 `OK: 浏览器续期成功（expires_at=…）` 就是这么来的；
            #   · 换不动 ⇒ 落回原来的判断（探针 → NOTE / SESSION_DEAD）。
            # 退避：失败会写 `BROWSER_BACKOFF_SEC` 秒退避，别每 5 分钟开一次浏览器。
            if _browser_backoff_ok():
                _before = d.get("access_token", "")
                log("只读接口 401（access_token 过期）→ 先用浏览器刷一次（刷得动就不用重登）")
                rc = _browser_renew()
                if rc == 0:
                    _c2, d2 = read_token_cookie()
                    if (d2 or {}).get("access_token", "") not in ("", _before):
                        log("NOTE: 浏览器刷新成功，会话继续（未重登、未重启）")
                        record_success()
                        _write_session_state("ok")
                        return 0
                    log("  浏览器刷完 token 没变（可能平台也没新 token）")
                else:
                    log("  浏览器刷新没成 → 退避 {} 分钟，落回业务探针判断".format(
                        BROWSER_BACKOFF_SEC // 60))
                    _browser_backoff()
            else:
                log("  浏览器刷新在退避期内，跳过")
            # P12-AJ：**别急着判死**。只读接口不认我们的 access_token（它就是过期了），
            # 但会话很可能还活着 —— 用业务调用再问一次才能下结论。
            # ⚠️ 这一段的日志**不许出现 "FAIL"**：巡检的 check_renew() 按它告警，
            #    而这里 401 是常态（每小时都会来），写 FAIL 就是误报源。
            probe = _api_probe()
            _write_session_state(probe)
            if probe == "ok":
                log("NOTE: 只读接口未授权（" + str(new.get("error", ""))[:20]
                    + "），但业务调用正常 → 会话没死，不重登")
                record_success()
                return 0
            if probe == "unauth":
                # P12-BI：先分清"平台风控墙"和"会话真死" —— 墙在的时候重登也没用。
                wall = _home_status()
                if wall == "wall":
                    # P12-BJ：告警按**窗口**算（进窗一条、出窗一条），窗口内只写日志。
                    if _open_bad_window():
                        nodes = _probe_nodes()
                        log("  镜像域名探测: " + " ".join(
                            "{}=home{}/{}".format(h, hm, tk) for h, hm, tk in nodes))
                        usable = [h for h, hm, tk in nodes if tk == "token-ok"]
                        log("WALL: 平台风控墙（首页 418）挡住了续期；会话本身未必死"
                            + ("；候选域名能换到 token：{}".format(usable) if usable else
                               "；候选域名也都不通（只能等）"), to_stdout=True)
                    else:
                        log("  首页=418（验证码墙）未解除；窗口内不重复告警")
                    _write_session_state("captcha")
                    return 1
                if wall == "err":
                    log("  首页探测失败 → 不下结论（等下一轮再判）")
                    _write_session_state("error")
                    return 1
                # P12-BG：墙是通的 ⇒ 会话真死，**先升级到密码重登**（唯一能救活的手段）。
                # 成了就不写 emergency、不重启 bot —— 常驻页面和出站队列都保住。
                if _try_password_relogin():
                    record_success()
                    return 0
                log("SESSION_DEAD: 业务调用未授权、首页通（会话真死），密码重登也没救回来"
                    + ("" if _open_bad_window() else "（窗口内，不重复告警）"))
                log("  落回 emergency 重启路径")
                if _emergency_ok():
                    _write_emergency()
                    log("EMERGENCY: 已写 token.emergency 信号（触发重登）", to_stdout=True)
                else:
                    log("  emergency 冷却中（1 小时内已写过），跳过")
                return 1
            log("NOTE: 只读接口未授权，业务探针本身失败（" + probe + "）→ 不下结论、不重登")
            # P12-AS：**"不下结论"有期限**。已经连续 `RELOGIN_AFTER_MIN` 分钟没能
            # 验证通过（探针一直 error），就别再等了 —— 用户看到的是"机器人不回话"，
            # 而日志里只有一行 NOTE。先试浏览器续期，再不行写 emergency 让 entrypoint 重登。
            # （2026-09-29 15:49~18:11：平台回 "Invalid authentication credentials"，
            #   旧分类器认不出来 → 一路 error → 一路"不下结论" → 142 分钟没人管。）
            if _unverified_min() > RELOGIN_AFTER_MIN:
                log("NOTE: 已连续 {:.0f} 分钟没验证通过 → 按会话不可用处理，尝试重登"
                    .format(_unverified_min()))
                # P12-BI：先看墙。墙在的时候浏览器的续期请求会被 418 挡回来
                # （日志里 `浏览器续期 FAIL: fetch status=418 captcha_required` 就是它），
                # 重登同样过不去 —— 那时只该等（并把退避拉长，别每 5 分钟撞一次）。
                if _home_status() == "wall":
                    if _open_bad_window():
                        nodes = _probe_nodes()
                        log("  镜像域名探测: " + " ".join(
                            "{}=home{}/{}".format(h, hm, tk) for h, hm, tk in nodes))
                        log("WALL: 平台风控墙（首页 418）→ 这轮不可恢复，等墙解除"
                            "（不重登、不重启）", to_stdout=True)
                    else:
                        log("  首页=418（验证码墙）未解除；窗口内不重复告警")
                    _write_session_state("captcha")
                    return 1
                rc = _browser_renew()
                if rc == 0:
                    _write_session_state("ok")
                    record_success()
                    return 0
                # P12-BG：浏览器刷新救不活"已死的会话" —— 再升级到密码重登。
                if _try_password_relogin():
                    record_success()
                    return 0
                log("  浏览器续期 + 密码重登都没成 → 写 emergency 交给 entrypoint 重登")
                if _emergency_ok():
                    _write_emergency()
                    log("EMERGENCY: 已写 token.emergency 信号（触发重登）", to_stdout=True)
                else:
                    log("  emergency 冷却中（1 小时内已写过），跳过")
            return 1

    # 4. 更新 JSON（保留 user/token_type，换 access_token + expires；rotation 时同步新 refresh_token）
    old_exp = d.get("expires_at", 0)
    d["access_token"] = new_at
    d["expires_at"] = new.get("expires_at", old_exp)
    d["expires_in"] = new.get("expires_in", d.get("expires_in", 3600))
    new_refresh = new.get("refresh_token", "")
    if new_refresh and new_refresh != d.get("refresh_token", ""):
        d["refresh_token"] = new_refresh
        log("  已同步新 refresh_token（rotation）")

    # 5. 重新编码
    new_value = "base64-" + base64.b64encode(
        json.dumps(d, separators=(",", ":")).encode("utf-8")).decode()
    if new_value == cookie.get("value", ""):
        # ⚠️ P12-AT 实测（2026-09-29 18:35）：**窗口内提前开浏览器没用**。
        # 日志证据：
        #   18:35:14 SKIP: 回显接口没给新 token（它不是续期接口）
        #   18:35:14   → 窗口内，主动走浏览器续期（别等它过期）
        #   18:35:20 SKIP: token 未变化，无需写回     ← 页面开了 6 秒，前端没刷
        # 原因：平台前端只在 **token 临近/已经过期** 时才用 refresh_token 换新的；
        # 手里那个 access_token 还有 35 分钟有效期时，页面加载完照样原样返回。
        # 所以"提前 10 分钟主动续"这条路是死的 —— 真正有用的是**在 401 那一刻**
        # 先用浏览器刷一次（见下面 `if not new_at:` 分支），刷得动就不用重登、
        # 也不用重启 bot。这里退回成一行 SKIP 就够了。
        log("SKIP: token 未变化，无需写回")
        record_success()
        return 0

    # 6. 写回 token JSON
    if not write_token_cookie(new_value):
        log("FAIL: 写回 token JSON 失败", to_stdout=True)
        if record_fail():
            log("EMERGENCY: 连续 3 次续期失败，写 token.emergency 信号", to_stdout=True)
        return 1
    record_success()
    log("OK: token 续期成功，已写回 token JSON（bot 发消息前热更新，无需重启）")
    return 0


def main() -> int:
    """入口：先取得「唯一登录者」锁（P5）。

    平台的 refresh_token 是**轮换制** —— 两个进程同时续期会互相作废，
    表现为随机 401 / 被踢下线。拿不到锁说明别人正在做，**本次跳过**即可
    （token 有 600 秒提前续期窗口，跳过一次不会过期）。
    """
    from session_guard import guard
    with guard("renew_token", timeout=20) as got:
        if not got:
            log("另一个进程正在续期/重登，本次跳过（避免 refresh_token 轮换互相作废）")
            # P12-AR：心跳的语义是"**循环跑到了**"，不是"会话验证过了"。
            # 抢不到锁说明别人正在重登（人工过验证码时可能持续很久），
            # 这期间不写心跳的话又会被"停摆"告警误报。"locked" 不会被当会话失效。
            _write_session_state("locked")
            return 0
        rc = _renew_locked()
        # P12-AR：**任何成功的一轮都刷新心跳**（`data/session_state.json`）。
        # `SKIP: token 未变化`（echo 接口 200，会话明明活着）和
        # `OK: token 续期成功` 两条真续期路径**都不写** —— 于是每次续期窗口
        # （token 剩余 ≤ 10 分钟那一段）心跳必然变旧，最坏 ~16 分钟。
        # 实测踩到：13:50 的体检显示"心跳 15.9 分钟前 → 疑似停摆"（假的）。
        # 巡检马上要按这个心跳做"续期循环停摆"告警，所以它必须每轮都被刷新。
        # 注意只在 rc == 0 时写：SESSION_DEAD（unauth）与各种失败**不许**覆盖成"活着"。
        if rc == 0:
            _write_session_state("ok")
            # P12-BJ：恢复时**只发一条**"好了"，并带上坏了多久（窗口告警的收尾）。
            mins = _close_bad_window()
            if mins >= 5:
                log("OK: 续期恢复正常（本轮共坏 {:.0f} 分钟）".format(mins), to_stdout=True)
        return rc


if __name__ == "__main__":
    sys.exit(main())
