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
    """写日志到 renew_token.log；to_stdout=True 时发飞书告警（云端无 cron deliver，脚本内告警）。"""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(ROOT / "renew_token.log", "a") as f:
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


BROWSER_BACKOFF_FILE = ROOT / "renew_browser_backoff.json"
BROWSER_BACKOFF_SEC = 3600  # 浏览器续期失败后 1 小时不再试（每 1 小时试一次，避免反复打风控接口）


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

    # 2. 提前续期判断：token 剩余 > 600 秒则静默退出（消除过期窗口）
    if d.get("expires_at", 0) - time.time() > RENEW_AHEAD_SEC:
        record_success()
        return 0  # token 还很新鲜，无需续期

    # 2.5 撞验证码退避检查：浏览器续期失败后 30 分钟内直接跳过，不打续期接口
    #（避免撞验证码期间每 5 分钟 requests 打一次 /api/auth/token 加剧风控）
    if not _browser_backoff_ok():
        log("撞验证码退避中，跳过续期（不打接口，避免加剧风控）")
        return 1

    # 3. 续期（token 剩余不足 600 秒，/api/auth/token 用 refresh_token 换新 token）
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
            # ['error'] = refresh_token 真失效 → 需要重新登录
            # 立即写 emergency（缩短恢复时间），加冷却防"refresh失效+撞验证码"反复重启
            log("FAIL: refresh_token 失效（" + str(new.get("error", ""))[:40]
                + "），写 emergency 触发重登", to_stdout=True)
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
