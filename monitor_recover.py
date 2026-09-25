#!/usr/bin/env python3
"""持续监测：撞验证码解除 + token 恢复，风控解除且 token 仍失效时自动重启容器触发重登。

宿主机运行（root），每 5 分钟检查一次（避免频繁打接口加剧风控）：
    nohup python3 /opt/dzmm/monitor_recover.py >> /opt/dzmm/monitor_recover.log 2>&1 &

判断逻辑：
    首页 418 = 撞验证码还在 → 等待
    首页 200 + auth/token 返回 access_token = 已恢复 → 记录，不再动作
    首页 200 + auth/token 无 access_token = 风控解除但 refresh_token 仍失效 → docker restart 触发重登
"""
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

LOG = "/opt/dzmm/monitor_recover.log"
TOKEN_FILE = "/opt/dzmm/.dzmm_token.json"
INTERVAL = 300  # 5 分钟
ORIGIN = "https://" + (os.environ.get("DZMM_HOST", "www.dzmm.ai").strip() or "www.dzmm.ai")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def homepage_ok() -> bool:
    """首页 200 = 风控解除（418 = 撞验证码）。"""
    try:
        r = urllib.request.urlopen(ORIGIN + "/", timeout=20, context=CTX)
        return r.status == 200
    except urllib.error.HTTPError as e:
        return e.code == 200
    except Exception:
        return False


def auth_token_status() -> str:
    """返回 'ok'（token 有效）/ 'captcha'（撞验证码）/ 'invalid'（refresh 失效）/ 'err'。"""
    try:
        d = json.load(open(TOKEN_FILE))
        cookie = "; ".join(c["name"] + "=" + c["value"] for c in d["cookies"])
        req = urllib.request.Request(
            ORIGIN + "/api/auth/token",
            headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0 DZMM-Agent/1.0"})
        r = urllib.request.urlopen(req, timeout=20, context=CTX)
        j = json.loads(r.read().decode())
        return "ok" if j.get("access_token") else "invalid"
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return "captcha" if "captcha" in body else "invalid"
    except Exception:
        return "err"


def container_up() -> bool:
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}} {{.Status}}"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=10)
        return any(line.startswith("dzmm-bot") and "Up" in line
                   for line in out.stdout.splitlines())
    except Exception:
        return False


def main() -> None:
    log("监测启动（每 5 分钟一轮）")
    restart_cooldown = 0.0  # 重启后 10 分钟内不重复重启（给 headless_relogin 时间）
    while True:
        hp = homepage_ok()
        ts = auth_token_status()
        up = container_up()
        state = f"首页={'OK' if hp else '418'} auth_token={ts} 容器={'Up' if up else 'DOWN'}"
        log(state)  # 每轮都 log，便于确认监测循环健康

        if hp and ts == "invalid" and up and time.time() > restart_cooldown:
            log("风控已解除但 refresh_token 仍失效，重启容器触发 headless_relogin 重登...")
            try:
                subprocess.run(["docker", "restart", "dzmm-bot"], timeout=60)
                restart_cooldown = time.time() + 600
                log("已执行 docker restart，10 分钟内不重复重启")
            except Exception as e:
                log("docker restart 失败: " + str(e)[:120])
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
