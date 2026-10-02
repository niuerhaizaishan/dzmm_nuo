#!/usr/bin/env python3
"""临时：人工辅助登录（headed 模式，显示到 noVNC 远程桌面），用户网页过码后写回 token。"""
import sys
from pathlib import Path

sys.path.insert(0, "/app")
import headless_relogin as hr

email, password = hr.load_config()
print("=== 人工辅助登录启动（headed 模式，显示到 noVNC :99）===", flush=True)
print("请在浏览器打开 noVNC 网页过验证码，登录成功后自动写回 token", flush=True)
cookies = hr.login_manual_captcha(email, password, wait_minutes=25)
if not cookies:
    print("FAIL: 人工登录超时/失败", flush=True)
    sys.exit(1)
if not hr.write_to_json(cookies):
    print("FAIL: 写回 token JSON 失败", flush=True)
    sys.exit(1)
if hr.check_logged_in():
    print("OK: 人工登录成功，token 已写回", flush=True)
    sys.exit(0)
print("WARN: 写回后验证未通过", flush=True)
sys.exit(1)
