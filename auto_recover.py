#!/usr/bin/env python3
"""自动恢复：cookie 失效后自动登录 + 重启 bot + 验证 + 微信通知。

由 bot 检测到连续 unauthorized 后调起（后台），或手动运行：
    python auto_recover.py
"""
import subprocess
import sys
import time
from pathlib import Path
import env_config
from runtime import session_obs as _session_obs

ROOT = Path(__file__).parent
HERMES_PY = sys.executable  # 当前解释器（容器/裸机通用）
PRIMARY_CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"

# 抢会话锁的最长等待（2026-10-01 调整，原为 300s）。
# renew_token 在重登期间最长持锁 `RELOGIN_TIMEOUT_SEC=300` 秒，等满 300 秒就是
# "白等 5 分钟"——那时候 renew 那条链早把活干完了。20 秒足够区分"只是短暂重叠"
# 与"确实有别人正在做"。
AUTO_RECOVER_LOCK_WAIT_SEC = 20.0


def notify(text: str):
    """飞书通知（静默失败）。"""
    try:
        sys.path.insert(0, str(ROOT))
        from wx_alert import send_weixin
        send_weixin(text)
    except Exception:
        pass


def restart_bot():
    """停旧 bot + 清 Singleton + 后台启动新 bot（detach）。"""
    _session_obs.event("bot_stop_requested", result="container" if env_config.in_container() else "local")
    subprocess.run(["pkill", "-f", "bot.py"], capture_output=True)
    time.sleep(2)
    subprocess.run(["pkill", "-9", "-f", "bot.py"], capture_output=True)
    time.sleep(1)
    # 清理 Playwright Singleton 锁
    for p in ROOT.glob("pw_profile*/Singleton*"):
        try:
            p.unlink()
        except Exception:
            pass
    time.sleep(1)
    if env_config.in_container():
        # 容器模式：entrypoint 的 while 循环会自动重启 bot，这里只负责杀旧进程
        return
    # 后台启动（start_new_session 脱离父进程）
    subprocess.Popen(
        [HERMES_PY, "bot.py"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def verify() -> bool:
    """验证读消息正常（不抛异常 = cookie 有效）。"""
    try:
        sys.path.insert(0, str(ROOT))
        from bot import get_messages
        get_messages(PRIMARY_CID, 3)
        return True
    except Exception:
        return False


def _recover_locked() -> int:
    """真正的恢复流程；由 `main()` 在**持有会话锁**的前提下调用。"""
    sys.path.insert(0, str(ROOT))
    import auto_relogin
    import headless_relogin

    started_at = time.monotonic()
    _session_obs.event("recovery_started")
    email, password = auto_relogin.load_config()

    # 1. 登录（已登录则跳过；用 headless 的 check，兼容锁屏）
    if headless_relogin.check_logged_in():
        login_mode = "already_logged_in"
        _session_obs.event("recovery_login_check", result="already_logged_in")
        print("已登录，跳过登录步骤")
        ok = True
    else:
        login_mode = "applescript"
        _session_obs.event("recovery_login_check", result="not_logged_in")
        print("检测到未登录，开始自动登录...")
        # 2. AppleScript 优先（锁屏下会抛异常/超时）
        ok = False
        try:
            print("尝试 AppleScript 登录...")
            ok = auto_relogin.login_flow(email, password)
            _session_obs.event("recovery_login_result", login_mode=login_mode,
                               result="success" if ok else "failed")
            print("AppleScript 登录:", "成功" if ok else "失败")
        except Exception as e:
            _session_obs.event("recovery_login_result", login_mode=login_mode,
                               result="error", error_type=type(e).__name__)
            print("AppleScript 登录异常（锁屏/TCC 等）:", str(e)[:80])
            ok = False
        # 3. headless 兜底（AppleScript 失败后走 Playwright 无头登录 + 写回 Chrome）
        if not ok:
            login_mode = "headless"
            print("AppleScript 失败，改用 headless 兜底...")
            cookies = headless_relogin.headless_login(email, password)
            if cookies and headless_relogin.write_to_json(cookies):
                ok = headless_relogin.check_logged_in()
            _session_obs.event("recovery_login_result", login_mode=login_mode,
                               result="success" if ok else "failed")
            print("headless 兜底:", "成功" if ok else "失败")
            # 4. headless 也失败（撞验证码 captcha_required）→ 转人工辅助登录（noVNC 远程桌面 + 微信发网址/密码）
            if not ok:
                login_mode = "manual_captcha"
                print("headless 失败，转人工辅助登录（noVNC 远程桌面过验证码）...")
                cookies = headless_relogin.login_manual_captcha(email, password, wait_minutes=10)
                if cookies and headless_relogin.write_to_json(cookies):
                    ok = headless_relogin.check_logged_in()
                _session_obs.event("recovery_login_result", login_mode=login_mode,
                                   result="success" if ok else "failed")
                print("人工辅助登录:", "成功" if ok else "失败/超时")

    if not ok:
        _session_obs.event("recovery_finished", result="login_failed", login_mode=login_mode,
                           elapsed=time.monotonic() - started_at)
        notify("🚨【小小糯bot】自动登录失败（AppleScript + headless + noVNC 均失败），请手动登录 dzmm.ai")
        print("FAIL: 登录失败")
        return 1

    # 2. 重启 bot（让 Playwright 读新 cookie）
    print("登录成功，重启 bot...")
    _session_obs.event("recovery_restart_decision", result="requested", login_mode=login_mode)
    restart_bot()
    _session_obs.event("recovery_restart_returned", login_mode=login_mode)

    # 3. 等 bot 启动
    time.sleep(20)

    # 4. 验证
    if verify():
        _session_obs.event("recovery_finished", result="verified", login_mode=login_mode,
                           elapsed=time.monotonic() - started_at)
        notify("✅【小小糯bot】登录态已自动恢复，bot 已重启并验证通过")
        print("OK: 恢复成功")
        return 0
    _session_obs.event("recovery_finished", result="verification_failed", login_mode=login_mode,
                       elapsed=time.monotonic() - started_at)
    notify("⚠️【小小糯bot】登录已恢复，但读消息验证未通过，请检查")
    print("WARN: 验证未通过")
    return 1


def main() -> int:
    """入口：先取得「唯一登录者」锁（P5）。

    本函数会走完整的登录阶梯（AppleScript → headless → noVNC 人工），
    最长可能几分钟。与 renew_token / 容器启动时的 headless_relogin 并发时，
    **只允许一个真正去登录** —— 否则 refresh_token 轮换会让彼此作废。
    """
    from session_guard import guard
    _session_obs.event("recovery_lock_wait")
    with guard("auto_recover", timeout=AUTO_RECOVER_LOCK_WAIT_SEC) as got:
        if not got:
            _session_obs.event("recovery_finished", result="lock_busy")
            # ⚠️ 刻意**不发微信告警**（2026-10-01 调整）：
            # 按群并行（P12-I）下有 4 个主循环实例，会话一死是 4 个群**同时**读失败
            # ⇒ 4 个 auto_recover 一起抢锁，只有 1 个能进，另外 3 个若各发一条
            # "另一个进程正在登录"就是纯噪音。屋主要的是"恢复没恢复"的结论，
            # 不是恢复过程的每一步 —— 真正的结论由 `_recover_locked()` 末尾发。
            print("SKIP: 另一个进程正在续期/重登，放弃本次恢复（不发告警）")
            return 1
        _session_obs.event("recovery_lock_acquired")
        try:
            return _recover_locked()
        except Exception as error:
            _session_obs.event("recovery_finished", result="error", error_type=type(error).__name__)
            raise


if __name__ == "__main__":
    sys.exit(main())
