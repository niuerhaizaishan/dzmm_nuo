#!/usr/bin/env python3
"""撞验证码时的人工处理：headed 打开 dzmm.ai 显示到 noVNC，等人工过迷宫滑块。

由 bot.py 主循环撞 captcha 后后台拉起（Popen, start_new_session）。
职责：
1. 发飞书 noVNC 连接（URL + 密码）
2. 用 pw_captcha profile（独立于 bot 的 pw_profile）headed 打开 dzmm.ai，显示到宿主机 Xvfb :99
3. 窗口期（默认 30 分钟）内：人工可在 noVNC 过验证码，过完自动恢复
4. 窗口期无人处理：关浏览器，转后台探测（每 30 分钟一次，最长 3 小时），
   检测到 IP 风控自动解除 + API 恢复后，自动删 bot.paused 恢复 bot

用法：
    python captcha_novnc.py             # 打开页面 + 等人工/自动恢复
    python captcha_novnc.py --check     # 仅检测验证码是否解除（退出码 0=已解除）
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import env_config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

PROBE_INTERVAL = 1800   # 后台探测间隔：30 分钟
PROBE_MAX_MIN = 180     # 后台探测最长等待：3 小时


def notify(text: str) -> None:
    """发飞书告警，记录成功/失败结果到 stderr（不再静默吞异常）。"""
    import sys
    try:
        from wx_alert import send_weixin
        ok = send_weixin(text)
        # P12-BR-fix: 与 renew_token.py 一致，记录飞书发送结果
        print(f"[captcha_novnc] 飞书告警: {'✅ 已发' if ok else '❌ 失败'}", file=sys.stderr)
    except Exception as e:
        print(f"[captcha_novnc] 飞书告警异常: {type(e).__name__}: {str(e)[:60]}", file=sys.stderr)


def ip_captcha_cleared() -> bool:
    """IP 级风控是否解除：dzmm.ai 返回 200（非 418/验证码重定向）。"""
    try:
        import requests
        r = requests.get(env_config.get_dzmm_origin() + "/", proxies=env_config.get_proxies(),
                         timeout=15, allow_redirects=False, headers={"User-Agent": UA})
        if r.status_code == 200:
            return True
        loc = (r.headers.get("Location") or "").lower()
        return not ("captcha" in loc or r.status_code == 418)
    except Exception:
        return False


def api_works() -> bool:
    """API 是否恢复：能成功拉主群成员 = token 有效 + 无风控。"""
    try:
        import dzmm, bot
        dzmm.get_members(bot.PRIMARY_CID)
        return True
    except Exception:
        return False


def wait_recovery(interval: int = PROBE_INTERVAL, max_minutes: int = PROBE_MAX_MIN) -> bool:
    """每 interval 秒探测一次，最长 max_minutes 分钟，直到 IP 风控解除且 API 恢复。"""
    deadline = time.time() + max_minutes * 60
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        if ip_captcha_cleared() and api_works():
            print(f"探测第 {attempts} 次：已恢复", flush=True)
            return True
        print(f"探测第 {attempts} 次：尚未恢复，{interval // 60} 分钟后再试", flush=True)
        time.sleep(interval)
    return False


def auto_resume() -> None:
    """删除 bot.paused 暂停哨兵，让 entrypoint 自动拉起 bot。"""
    try:
        (ROOT / "bot.paused").unlink()
        print("已删除 bot.paused，bot 将自动恢复", flush=True)
    except Exception as e:
        print("删 bot.paused 失败:", str(e)[:60], flush=True)


def main() -> int:
    if "--check" in sys.argv:
        return 0 if ip_captcha_cleared() else 1

    vnc_url = os.environ.get("VNC_URL", "http://47.237.152.187:6080/vnc.html")
    vnc_pass = os.environ.get("VNC_PASS", "")
    try:
        wait_min = int(os.environ.get("CAPTCHA_WAIT_MIN", "30"))
    except Exception:
        wait_min = 30

    notify("🚨【小小糯bot】撞验证码，bot 已暂停\n"
           "请在电脑浏览器打开 noVNC 远程桌面，手动拖动迷宫滑块过验证码：\n"
           f"地址：{vnc_url}\n"
           f"密码：{vnc_pass}\n"
           f"（{wait_min} 分钟内有效；若无人处理，将自动等待风控解除并恢复）")

    # 清残留 Singleton 锁
    for lock in ROOT.glob("pw_captcha/Singleton*"):
        try:
            lock.unlink()
        except Exception:
            pass

    # 1. headed 浏览器窗口期：供人工在 noVNC 过验证码
    human_ok = False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            proxy_url = env_config.get_proxy_url()
            ctx = p.chromium.launch_persistent_context(
                str(ROOT / "pw_captcha"), channel=env_config.get_channel(), headless=False,
                viewport={"width": 1280, "height": 800},
                proxy={"server": proxy_url} if proxy_url else None, user_agent=UA,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu",
                      "--disk-cache-size=52428800"])
            page = ctx.new_page()
            try:
                page.goto(env_config.get_dzmm_origin() + "/", timeout=60000, wait_until="domcontentloaded")
            except Exception:
                pass
            # 轮询检测人工是否已过验证码（页面出现聊天输入框）
            deadline = time.time() + wait_min * 60
            while time.time() < deadline:
                try:
                    ta = page.query_selector('textarea[placeholder*="输入消息"]')
                    if ta and ta.is_visible():
                        human_ok = True
                        break
                except Exception:
                    pass
                page.wait_for_timeout(5000)
            try:
                ctx.close()
            except Exception:
                pass
    except Exception as e:
        print("headed 窗口异常:", str(e)[:80], flush=True)

    if human_ok:
        # 人工已过验证码 → 直接恢复
        auto_resume()
        notify("✅【小小糯bot】验证码已通过，bot 已恢复运行！")
        return 0

    # 2. 窗口期无人处理 → 转后台探测，等风控自动解除
    notify("⏳【小小糯bot】30 分钟内无人处理验证码，转为自动等待风控解除\n"
           "（每 30 分钟探测一次，最长 3 小时，解除后自动恢复，无需操作）")
    print("窗口期无人处理，开始后台探测等待风控自动解除...", flush=True)
    if wait_recovery():
        auto_resume()
        notify("✅【小小糯bot】风控已自动解除，bot 已恢复运行！")
        return 0

    notify("⚠️【小小糯bot】3 小时仍未自动恢复，请手动检查（可能需要处理验证码或换 IP）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
