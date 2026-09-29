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
import sys
import time
from pathlib import Path
import env_config

ROOT = Path(__file__).parent
PROXY = env_config.get_proxies()
COOKIE_NAME = "sb-rls-auth-token"

RENEW_AHEAD_SEC = 600          # 提前 10 分钟续期（消除过期窗口）
EMERGENCY_FILE = ROOT / "token.emergency"       # 连续 FAIL 信号文件（bot 消费后退出）
FAIL_STATE_FILE = ROOT / "renew_fail_state.json"  # 连续 FAIL 计数（跨进程持久化）
EMERGENCY_COOLDOWN_SEC = 3600  # emergency 冷却：1 小时内不重复写（防"refresh失效+撞验证码"反复重启）
EMERGENCY_STATE_FILE = ROOT / "renew_emergency_state.json"  # 上次 emergency 时间戳


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
    r = requests.get(env_config.get_dzmm_origin() + "/api/auth/token", cookies=cookies,
                     timeout=15, proxies=PROXY)
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
            send_weixin("🚨【小小糯bot 续期】" + msg)
        except Exception:
            pass


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
SESSION_STATE_FILE = env_config.data_dir() / "session_state.json"


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
        if "未授权" in msg or "unauthorized" in low or "401" in low:
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
    """
    try:
        SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_STATE_FILE.write_text(json.dumps(
            {"at": time.time(), "alive": probe == "ok", "probe": probe}),
            encoding="utf-8")
    except Exception:
        pass


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

    # 2.5 撞验证码退避检查：浏览器续期失败后 30 分钟内直接跳过，不打续期接口
    #（避免撞验证码期间每 5 分钟 requests 打一次 /api/auth/token 加剧风控）
    if not _browser_backoff_ok():
        log("撞验证码退避中，跳过续期（不打接口，避免加剧风控）")
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
        keys = list(new.keys())
        if "location" in keys:
            # 撞验证码（captcha_required）：切浏览器续期，不写 emergency、不停 bot
            log("撞验证码（" + str(new.get("error", ""))[:40] + "），切浏览器续期")
            if not _browser_backoff_ok():
                log("  浏览器续期退避中（30 分钟内已失败过），跳过")
                return 1
            rc = _browser_renew()
            if rc == 0:
                record_success()
                return 0
            log("  浏览器续期也失败，退避 1 小时（不停 bot，收发通道不受影响）")
            _browser_backoff()
            _notify_captcha()
            return 1
        else:
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
                log("SESSION_DEAD: 业务调用也未授权（会话真失效），写 emergency 触发重登",
                    to_stdout=True)
                if _emergency_ok():
                    _write_emergency()
                    log("EMERGENCY: 已写 token.emergency 信号（触发重登）", to_stdout=True)
                else:
                    log("  emergency 冷却中（1 小时内已写过），跳过")
                return 1
            log("NOTE: 只读接口未授权，业务探针本身失败（" + probe + "）→ 不下结论、不重登")
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
            return 0
        return _renew_locked()


if __name__ == "__main__":
    sys.exit(main())
