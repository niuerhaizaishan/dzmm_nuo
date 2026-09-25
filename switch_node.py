#!/usr/bin/env python3
"""节点切换：当前节点 Socket.IO 故障时，自动测候选节点并切换。

触发条件（严格区分撞验证码）：
    - 首页 200（不是撞验证码） 且  /ws/matching 非 200（Socket.IO 故障）
    - 连续 3 次采样（15 分钟）都故障才切换（防偶发抖动）
排除：首页 418（撞验证码/IP 风控）→ 不切（切换无用，等风控解除）

用法：nohup python3 /opt/dzmm/switch_node.py >> /opt/dzmm/switch_node.log 2>&1 &
"""
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request

LOG = "/opt/dzmm/switch_node.log"
ENV_FILE = "/opt/dzmm/.env"
TOKEN_FILE = "/opt/dzmm/.dzmm_token.json"
STATE_FILE = "/opt/dzmm/switch_node_state.json"
INTERVAL = 300  # 5 分钟
FAIL_THRESHOLD = 3  # 连续 3 次才切

CANDIDATES = [
    "www.dzmm.ai", "www.brimvale.xyz", "www.cindorel.xyz", "www.dawnmere.xyz",
    "www.echolore.xyz", "www.emberloft.xyz", "www.fernquill.xyz", "www.glimvane.xyz",
    "www.ivorune.xyz", "www.jadelune.xyz", "www.kestrove.xyz", "www.lumivale.xyz",
    "www.quovin.xyz",
]

UA = "Mozilla/5.0 DZMM-Agent/1.0"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def get_current_host():
    try:
        for l in open(ENV_FILE, encoding="utf-8"):
            l = l.strip()
            if l.startswith("DZMM_HOST="):
                return l.split("=", 1)[1].strip()
    except Exception:
        pass
    return "www.dzmm.ai"


def http_status(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        r = urllib.request.urlopen(req, timeout=10, context=CTX)
        return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def homepage_ok(host):
    return http_status("https://" + host + "/") == 200


def sio_ok(host):
    ts = str(int(time.time() * 1000))
    url = "https://" + host + "/ws/matching/?EIO=4&transport=polling&t=" + ts
    return http_status(url) == 200


def token_ok(host):
    """带当前 token 调该节点 auth/token，返回是否拿到 access_token。"""
    try:
        d = json.load(open(TOKEN_FILE))
        cookie = "; ".join(c["name"] + "=" + c["value"] for c in d["cookies"])
        req = urllib.request.Request(
            "https://" + host + "/api/auth/token",
            headers={"Cookie": cookie, "User-Agent": UA})
        r = urllib.request.urlopen(req, timeout=12, context=CTX)
        j = json.loads(r.read().decode())
        return bool(j.get("access_token"))
    except Exception:
        return False


def find_working_node(current):
    """测候选节点，返回第一个「首页200 + sio 200 + token 通用」的节点。"""
    for h in CANDIDATES:
        if h == current:
            continue
        if homepage_ok(h) and sio_ok(h) and token_ok(h):
            return h
    return None


def switch_to(new_host):
    """改 .env DZMM_HOST + token cookie domain + 重启容器 + 飞书告警。"""
    log("切换节点: -> " + new_host)
    # 1. 改 .env
    try:
        lines = open(ENV_FILE, encoding="utf-8").read().splitlines()
        for i, l in enumerate(lines):
            if l.strip().startswith("DZMM_HOST="):
                lines[i] = "DZMM_HOST=" + new_host
        open(ENV_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    except Exception as e:
        log("改 .env 失败: " + str(e)[:80])
        return False
    # 2. 改 token cookie domain
    try:
        d = json.load(open(TOKEN_FILE))
        for c in d.get("cookies", []):
            c["domain"] = new_host
        json.dump(d, open(TOKEN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        log("改 token domain 失败: " + str(e)[:80])
        return False
    # 3. 重启容器（重新加载 DZMM_HOST）
    try:
        subprocess.run(["docker", "restart", "dzmm-bot"], timeout=90)
    except Exception as e:
        log("docker restart 失败: " + str(e)[:80])
        return False
    # 4. 飞书告警
    try:
        feishu = read_env("FEISHU_WEBHOOK")
        if feishu:
            data = json.dumps({"msg_type": "text", "content": {"text":
                              "[小小糯bot] Socket.IO 节点故障，已自动切换到 " + new_host}}).encode("utf-8")
            req = urllib.request.Request(feishu, data=data,
                                        headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10, context=CTX)
    except Exception:
        pass
    log("切换完成: " + new_host)
    return True


def read_env(key):
    try:
        for l in open(ENV_FILE, encoding="utf-8"):
            l = l.strip()
            if l.startswith(key + "="):
                return l.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def load_fail_count():
    try:
        return json.load(open(STATE_FILE)).get("fail", 0)
    except Exception:
        return 0


def save_fail_count(n):
    try:
        json.dump({"fail": n}, open(STATE_FILE, "w"))
    except Exception:
        pass


def main():
    log("节点切换守护启动（每 %d 分钟检查）" % (INTERVAL // 60))
    while True:
        host = get_current_host()
        hp = http_status("https://" + host + "/")
        if hp == 200:
            sio = sio_ok(host)
            if sio:
                save_fail_count(0)
                # 静默：当前节点正常
            else:
                n = load_fail_count() + 1
                save_fail_count(n)
                log("当前节点 %s Socket.IO 故障（连续 %d 次）" % (host, n))
                if n >= FAIL_THRESHOLD:
                    log("连续 %d 次故障，开始测候选节点..." % n)
                    new_host = find_working_node(host)
                    if new_host:
                        if switch_to(new_host):
                            save_fail_count(0)
                        else:
                            log("切换失败，保留当前节点")
                    else:
                        log("无可用候选节点，保持当前节点")
        elif hp == 418:
            # 撞验证码（IP 风控）：不切，等风控解除
            save_fail_count(0)
            # 静默：撞验证码期间不动作
        else:
            # 首页不通（网络/节点故障）
            save_fail_count(0)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
