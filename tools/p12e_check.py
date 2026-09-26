#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P12-E 生产验收：人设与话术页（渲染 / 预览不写 / 保存真生效 / 恢复默认）。

用**无害的**改动做真实验收：把 `no_play`（"现在没有进行中的玩法哦～"）改掉。
为什么无害：这条话术只在用户**主动**发了一个玩法指令、而当前没有进行中的玩法时
才会发出去，不会自己冒出来；但它是一次**真的写入**（走完整校验 + 备份 + 审计 +
重启生效）。

运行期自证用**一次性容器**（`docker run --rm` + 只读挂载 /opt/dzmm）：
容器里 `import bot` 走的是与生产启动**同一条**路径（load config → 注入
personas → 打印自证），所以它打印出来的那句话就是线上会发的那句话。
（不往群里发真消息：那会打扰群友，而且这条话术只有"主动触发"才看得到。）

用法（在 ECS 上传到 /tmp 后）：python3 /tmp/p12e_check.py

⚠️ 宿主机是 Python **3.6.8**：`subprocess` 只能用 `stdout=PIPE` +
`universal_newlines=True`（`capture_output=` / `text=` 都是 3.7+，直接 TypeError）。
"""
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
CFG = "/opt/dzmm/config/config.json"
AUDIT = "/opt/dzmm/config/config_audit.log"
CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"      # 认证妹子群（Bot 通道）
CID_B = "4a56be71-9ca6-4785-b45f-18725d187f1a"      # Rose（505）玫瑰房
MARK = "【P12E验收】"
PHRASE = "no_play"
FAILS = []


def _sh(cmd, timeout=300):
    """跑一条命令，返回 (returncode, stdout+stderr)。Python 3.6 兼容。

    `cmd` 既可以是字符串（走 shell，支持 `&&` / 管道）也可以是列表。
    """
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=timeout,
                       shell=isinstance(cmd, str))
    return p.returncode, p.stdout or ""


def check(name, ok, detail=""):
    print("  [{}] {}{}".format("OK" if ok else "!!", name,
                               (" -- " + str(detail)) if detail else ""))
    if not ok:
        FAILS.append(name)


def env(key):
    txt = open("/opt/dzmm/.env", encoding="utf-8", errors="replace").read()
    m = re.search(r"^{}=(.*)$".format(key), txt, re.M)
    return (m.group(1).strip().strip('"').strip("'") if m else "")


USER, PW = env("DZMM_ADMIN_USER"), env("DZMM_ADMIN_PASSWORD")
JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))


def req(path, data=None, method=None):
    body = urllib.parse.urlencode(data, doseq=True).encode("utf-8") if data else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    if body:
        r.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with OPENER.open(r, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def csrf(page):
    m = re.search(r"name='csrf' value='([^']*)'", page)
    return m.group(1) if m else ""


def login():
    JAR.clear()
    return req("/login", {"username": USER, "password": PW})


def cfg():
    return json.load(open(CFG, encoding="utf-8"))


def restart():
    _sh("cd /opt/dzmm && docker compose restart dzmm-bot", timeout=300)
    time.sleep(16)


def logs(n=80):
    return _sh("docker logs --tail {} dzmm-bot 2>&1".format(n))[1]


def probe(code):
    """一次性容器里跑一段代码（只读挂载 + 独立的状态库）。"""
    cmd = ["docker", "run", "--rm", "--entrypoint", "python",
           "-e", "PYTHONPATH=/app", "-e", "PYTHONIOENCODING=utf-8",
           "-e", "DZMM_STORE_DB=/tmp/p12e_probe.db",
           "-v", "/opt/dzmm:/app:ro", "-w", "/tmp", "dzmm-bot:latest", "-c", code]
    return _sh(cmd, timeout=300)


def form(page, **over):
    """按页面会提交的样子构造表单：先把每一格填成**当前生效的文本**。

    这一步不能省：解析器是"逐格与代码默认比对"，缺格 = 空串 = 一次真的改动
    （比如 `extra` 变成空 = 不要专属设定）。所以表单必须在**生产容器**
    里按生产代码算出来（`personas` 层），而不是在宿主机上猜。

    ⚠️ 不能用"一串 or 连着几个列表推导"来塞字段：第一个推导返回的
    非空列表是真值，会把后面的短路掉 —— 于是话术整批没提交 = 整批被清空。
    """
    code = (
        "import json, personas as P\n"
        "d = json.load(open('/app/config/config.json', encoding='utf-8'))\n"
        "ov = d.get('personas') or {}\n"
        "def eff(key, f):\n"
        "    if key in ov and f in ov[key]:\n"
        "        return ov[key][f]\n"
        "    return P.PERSONAS[key].get(f) or ''\n"
        "def pph(key):\n"
        "    m = dict(P.PHRASES['nuo'])\n"
        "    m.update(P.PHRASES.get(key) or {})\n"
        "    m.update((ov.get(key) or {}).get('phrases') or {})\n"
        "    return m\n"
        "f = {'persona_form': '1', 'csrf': 'CSRF'}\n"
        "for k in P.PERSONAS:\n"
        "    f['pseen[%s]' % k] = '1'\n"
        "    for fl in ('name_full', 'name_short', 'extra'):\n"
        "        f['ptext[%s][%s]' % (k, fl)] = eff(k, fl)\n"
        "    m = pph(k)\n"
        "    for pk in P.known_phrase_keys(k):\n"
        "        f['pphrase[%s][%s]' % (k, pk)] = m.get(pk) or ''\n"
        "print(json.dumps(f, ensure_ascii=False))\n"
    ).replace("CSRF", csrf(page))
    rc, out = probe(code)
    if rc != 0:
        raise RuntimeError("构造表单失败：" + out[-400:])
    f = json.loads(out.strip().splitlines()[-1])
    f.update(over)
    return f


print("=== 0) 前置 ===")
check("管理员口令已配置", bool(USER and PW))
check("配置文件存在", os.path.isfile(CFG), CFG)
BEFORE = cfg()
print("  当前 personas 节：",
      json.dumps(BEFORE.get("personas") or {}, ensure_ascii=False)[:200])

print()
print("=== 1) 未登录访问 /personas ===")
st, body = req("/personas")
check("未登录给登录页（不泄漏）", st == 200 and "请登录" in body, st)

print()
print("=== 2) 登录 + 页面渲染 ===")
st, _ = login()
# urllib 会自动跟 303 跳转 ⇒ 这里看到的是跳转后的 200。两种都算成功，
# 真正的判据是下一步能不能拿到登录后的页面（下面那几条）。
check("登录成功（200/303 均可）", st in (200, 303), st)
st, page = req("/personas")
check("/personas 200", st == 200, st)
n_text = len(set(re.findall(r"ptext\[(\w+)\]\[(name_full|name_short|extra)\]", page)))
n_phrase = len(set(re.findall(r"pphrase\[(\w+)\]\[(\w+)\]", page)))
n_pseen = len(set(re.findall(r"pseen\[(\w+)\]", page)))
check("两个人设都在", n_pseen == 2, n_pseen)
check("话术条目齐全（2×19=38）", n_phrase == 38, n_phrase)
check("三段文本都在（2×3=6）", n_text == 6, n_text)
check("有'这一节渲染过'标记", "name='pseen[" in page or "name=pseen[" in page)
check("没有内联事件处理器（会被 CSP 拦掉）", "onsubmit" not in page)
check("有预览按钮", "value='preview'" in page)
check("有恢复默认入口", "action='/personas/reset'" in page)

print()
print("=== 3) CSP / 安全头 ===")
r = urllib.request.Request(BASE + "/personas")
with OPENER.open(r, timeout=30) as resp:
    hdr = dict(resp.headers)
check("CSP 收紧", "script-src 'self'" in (hdr.get("Content-Security-Policy") or ""),
      hdr.get("Content-Security-Policy"))
check("X-Frame-Options: DENY", hdr.get("X-Frame-Options") == "DENY",
      hdr.get("X-Frame-Options"))

print()
print("=== 4) 预览：用草稿渲染，一个字都不写 ===")
before_txt = open(CFG, encoding="utf-8").read()
st, pv = req("/personas", form(page, action="preview", preview_cid=CID_B,
                               **{"ptext[rose_butler][name_short]": "验收草稿名"}))
check("预览 200", st == 200, st)
check("页面里有预览块", "预览 · 人设" in pv)
check("草稿名字画回了表单", "验收草稿名" in pv)
check("配置一个字都没写", open(CFG, encoding="utf-8").read() == before_txt)

print()
print("=== 5) 越界要被拒（1001 字的话术） ===")
st, bad = req("/personas", form(page, action="save",
                                **{"pphrase[nuo][{}]".format(PHRASE): "啊" * 1001}))
check("被拒且报明原因", st == 400 and "平台上限" in bad, st)
check("拒绝时文件没动", open(CFG, encoding="utf-8").read() == before_txt)

print()
print("=== 6) 保存一条无害的话术覆盖（{}）===".format(PHRASE))
st, saved = req("/personas", form(page, action="save",
                                  **{"pphrase[nuo][{}]".format(PHRASE):
                                     MARK + "现在没有进行中的玩法哦～"}))
check("保存 200", st == 200, st)
check("页面报「已保存」", "已保存" in saved)
check("列出了改动清单", "本次改动" in saved)
d = cfg()
got = ((d.get("personas") or {}).get("nuo") or {}).get("phrases", {}).get(PHRASE, "")
check("值已落盘", got.startswith(MARK), got)
check("只动了 personas 一节", set(d) - set(BEFORE) <= {"personas"})
check("群与 Bot 没被动", [g["cid"] for g in d["groups"]] == [g["cid"] for g in BEFORE["groups"]]
      and d["bots"][0]["key"] == BEFORE["bots"][0]["key"])
check("审计有记录", "人设" in open(AUDIT, encoding="utf-8", errors="replace").read()[-4000:])

print()
print("=== 7) 重启 → 运行期真的生效 ===")
restart()
lg = logs(120)
for line in lg.splitlines():
    if "P12: 人设文本" in line or "P12: 人设体检" in line:
        print("  " + line.strip())
check("自证行显示 1 处话术覆盖", "覆盖人设=1" in lg and "话术=1" in lg)
check("体检通过", "体检=全部通过" in lg)
rc, out = probe("import bot, personas;"
                "print('PROBE|' + str(personas.p('{}', '{}')) + '|' +"
                " personas.persona_key_for('{}'))".format(PHRASE, CID_A, CID_A))
check("一次性容器能起（配置合法）", rc == 0, out[-300:] if rc else "")
line = [x for x in out.splitlines() if x.startswith("PROBE|")]
check("打印出了探针行", bool(line), out[-200:])
if line:
    print("  " + line[-1])
    check("运行期读到的是新话术", line[-1].split("|")[1].startswith(MARK))
    check("该群人设仍是默认", line[-1].rstrip().endswith("|nuo"))

print()
print("=== 8) 恢复默认（第二次重启前必须重新登录：会话只在内存里） ===")
login()
st, page2 = req("/personas")
check("重登后页面正常", st == 200 and "persona_form" in page2, st)
st, done = req("/personas/reset", {"csrf": csrf(page2), "persona": "nuo"})
check("恢复默认 200", st == 200, st)
check("页面报「已恢复」", "已恢复人设 nuo 的默认" in done)
d2 = cfg()
check("配置里已没有 personas 节", "personas" not in d2 or not d2["personas"],
      json.dumps(d2.get("personas") or {}, ensure_ascii=False))
check("群/Bot/功能节完好",
      [g["cid"] for g in d2["groups"]] == [g["cid"] for g in BEFORE["groups"]]
      and d2["bots"][0]["key"] == BEFORE["bots"][0]["key"])

restart()
lg2 = logs(120)
for line in lg2.splitlines():
    if "P12: 人设文本" in line:
        print("  " + line.strip())
check("自证行回到 0 覆盖", "覆盖人设=0" in lg2)
rc, out = probe("import bot, personas;"
                "print('PROBE|' + str(personas.p('{}', '{}')))".format(PHRASE, CID_A))
line = [x for x in out.splitlines() if x.startswith("PROBE|")]
check("运行期回到默认话术", bool(line) and not line[-1].startswith("PROBE|" + MARK),
      line[-1] if line else "")
exc = _sh("docker logs --tail 400 dzmm-bot 2>&1 | "
          "grep -ciE 'traceback|exception' || true")[1].strip()
check("日志无异常", exc == "0", exc)

print()
print("=== 9) 最终状态 ===")
for line in logs(120).splitlines():
    if any(k in line for k in ("P11: 配置=", "P12: 功能", "P12: 参数",
                               "P12: 人设文本", "start  dry")):
        print("  " + line.strip())
st, _ = req("/healthz")
check("后台仍存活", st == 200, st)

print()
if FAILS:
    print("===> 人设与话术页验收：**有失败项** ✗ {}".format(FAILS))
    sys.exit(1)
print("===> 人设与话术页验收：全部通过 ✓")
