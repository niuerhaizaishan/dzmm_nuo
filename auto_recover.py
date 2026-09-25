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

ROOT = Path(__file__).parent
HERMES_PY = sys.executable  # 当前解释器（容器/裸机通用）
PRIMARY_CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


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


def main():
    sys.path.insert(0, str(ROOT))
    import auto_relogin
    import headless_relogin

    email, password = auto_relogin.load_config()

    # 1. 登录（已登录则跳过；用 headless 的 check，兼容锁屏）
    if headless_relogin.check_logged_in():
        print("已登录，跳过登录步骤")
        ok = True
    else:
        print("检测到未登录，开始自动登录...")
        # 2. AppleScript 优先（锁屏下会抛异常/超时）
        ok = False
        try:
            print("尝试 AppleScript 登录...")
            ok = auto_relogin.login_flow(email, password)
            print("AppleScript 登录:", "成功" if ok else "失败")
        except Exception as e:
            print("AppleScript 登录异常（锁屏/TCC 等）:", str(e)[:80])
            ok = False
        # 3. headless 兜底（AppleScript 失败后走 Playwright 无头登录 + 写回 Chrome）
        if not ok:
            print("AppleScript 失败，改用 headless 兜底...")
            cookies = headless_relogin.headless_login(email, password)
            if cookies and headless_relogin.write_to_json(cookies):
                ok = headless_relogin.check_logged_in()
            print("headless 兜底:", "成功" if ok else "失败")
            # 4. headless 也失败（撞验证码 captcha_required）→ 转人工辅助登录（noVNC 远程桌面 + 微信发网址/密码）
            if not ok:
                print("headless 失败，转人工辅助登录（noVNC 远程桌面过验证码）...")
                cookies = headless_relogin.login_manual_captcha(email, password, wait_minutes=10)
                if cookies and headless_relogin.write_to_json(cookies):
                    ok = headless_relogin.check_logged_in()
                print("人工辅助登录:", "成功" if ok else "失败/超时")

    if not ok:
        notify("🚨【小小糯bot】自动登录失败（AppleScript + headless + noVNC 均失败），请手动登录 dzmm.ai")
        print("FAIL: 登录失败")
        return 1

    # 2. 重启 bot（让 Playwright 读新 cookie）
    print("登录成功，重启 bot...")
    restart_bot()

    # 3. 等 bot 启动
    time.sleep(20)

    # 4. 验证
    if verify():
        notify("✅【小小糯bot】登录态已自动恢复，bot 已重启并验证通过")
        print("OK: 恢复成功")
        return 0
    notify("⚠️【小小糯bot】登录已恢复，但读消息验证未通过，请检查")
    print("WARN: 验证未通过")
    return 1


if __name__ == "__main__":
    sys.exit(main())
