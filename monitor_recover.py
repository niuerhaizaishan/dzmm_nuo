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


def _resolve_token_file() -> str:
    """S1：优先环境变量，其次 secrets/（新纪元），最后老路径（未迁移时）。

    三个时代都不用改宿主 cron：
      迁移前 secrets/ 不存在 → 老路径；
      双写期/清理后 secrets/token.json 存在且是唯一会被续期刷新的 → 优先它。
    """
    env = os.environ.get("DZMM_TOKEN_FILE", "").strip()
    if env:
        return env
    for p in ("/opt/dzmm/secrets/token.json", "/opt/dzmm/.dzmm_token.json"):
        if os.path.exists(p):
            return p
    return "/opt/dzmm/.dzmm_token.json"


TOKEN_FILE = _resolve_token_file()
INTERVAL = 300  # 5 分钟
ORIGIN = "https://" + (os.environ.get("DZMM_HOST", "www.dzmm.ai").strip() or "www.dzmm.ai")
UA = "Mozilla/5.0 DZMM-Agent/1.0"      # P12-BK：**别用 urllib 默认 UA**（会被 WAF 403）

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
    """首页 200 = 可达（418/403 = 被风控挑战）。

    ⚠️ P12-BK（2026-09-30）：**必须带 UA**。这里原来用 urllib 默认 UA
    （`Python-urllib/3.x`），而平台的 WAF 专门挑战这个 UA —— 实测同一时刻：
        requests 默认 UA → 200 ；`Mozilla/5.0 DZMM-Agent/1.0` → 200 ；Chrome UA → 200 ；
        `Python-urllib/3.11` → **403（WAF 拦截页）**
    于是这个脚本三周来一直记"首页=418"（6570 次），**它自己的探测就是被拦的那一类**，
    真正的"风控解除"它永远看不到。
    """
    try:
        req = urllib.request.Request(ORIGIN + "/", headers={"User-Agent": UA})
        r = urllib.request.urlopen(req, timeout=20, context=CTX)
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
            headers={"Cookie": cookie, "User-Agent": UA})
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


def session_heartbeat_stale_min() -> float:
    """容器里续期循环的心跳到多久没更新了（分钟）。读不到返回 -1。

    这是判断"容器里那套自动恢复是不是**整个停了**"的唯一依据（P12-BJ）。
    """
    try:
        d = json.loads(open("/opt/dzmm/data/session_state.json", encoding="utf-8").read())
        return (time.time() - float(d.get("at") or 0)) / 60.0
    except Exception:
        return -1.0


def main() -> None:
    log("监测启动（每 5 分钟一轮）")
    restart_cooldown = 0.0  # 重启后 10 分钟内不重复重启（给 headless_relogin 时间）
    while True:
        hp = homepage_ok()
        ts = auth_token_status()
        up = container_up()
        hb = session_heartbeat_stale_min()
        state = ("首页={} auth_token={} 容器={} 续期心跳={}".format(
            "OK" if hp else "418", ts, "Up" if up else "DOWN",
            "读不到" if hb < 0 else "{:.0f}分钟前".format(hb)))
        log(state)  # 每轮都 log，便于确认监测循环健康

        # P12-BJ（2026-09-30 屋主"换域名/收口"那一轮）：
        # **不再**因为"会话失效"就去 docker restart —— 那是上一代的做法，而且：
        #   · 容器里的 `renew_token.py` 现在自己会升级到密码重登（P12-BG，就地、不重启）；
        #   · 墙（首页 418）在的时候重启也过不去，只会丢在途消息；
        #   · 三次"会话死"窗口（09-23/27/28/29，4~8 小时）里这个重启**一次都没触发**
        #     （它要求首页 200，而那几个窗口首页一直是 418）。
        # 所以这里只保留它**唯一不可替代**的职责：容器真的不行了（Down 或续期心跳
        # 长时间不更新 = 容器里那套机制整个停了）→ 踢它一脚重启。
        stuck = (hb < 0 or hb > 25)
        if up and stuck and time.time() > restart_cooldown:
            log("容器在跑但**续期心跳已 {}**（容器内的自动恢复疑似整体停摆）→ 重启容器"
                .format("读不到" if hb < 0 else "{:.0f} 分钟未更新".format(hb)))
            try:
                subprocess.run(["docker", "restart", "dzmm-bot"], timeout=60)
                restart_cooldown = time.time() + 600
                log("已执行 docker restart，10 分钟内不重复重启")
            except Exception as e:
                log("docker restart 失败: " + str(e)[:120])
        elif not up and time.time() > restart_cooldown:
            log("容器不在运行 → 拉起")
            try:
                subprocess.run(["docker", "start", "dzmm-bot"], timeout=60)
                restart_cooldown = time.time() + 600
                log("已执行 docker start")
            except Exception as e:
                log("docker start 失败: " + str(e)[:120])
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
