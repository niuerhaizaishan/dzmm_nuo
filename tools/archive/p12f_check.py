#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P12-F 生产验收：热加载（改完**不重启**就生效，且边界诚实）。

这一步要证明的东西有三条，缺一条都不算过：

1. **真的不用重启**：全程 `docker inspect .State.StartedAt` **一个字都不变**，
   而配置/提示词的改动确实生效了（运行期自己报的状态 + 页面显示"已生效"）。
2. **坏配置不许半途生效**：把 config.json 改坏 → 日志明确说"未生效"，
   运行期**保持原样**，机器人继续跑（没有异常、没有重启），
   把文件改回去 → 自动恢复。
3. **结构性改动如实说"要重启"**：群清单/Bot 这类改动只报告、不应用。

改动都用**无害**的：
- 一句"只在该指令被主动触发时才会发出去"的话术（`no_play`）；
- 一个参数（`devil.cooldown`，主动吐槽默认关着）;
- 一个**冷门**提示词（/赋诗 之类，只在有人主动用那个功能时才读）；
- 结构性的那个只用**已停用**的群做（把它 enabled 翻成 true 再翻回来）。

脚本开头会把 config.json 逐字节备份到 /tmp，结束时恢复并**校验逐字节相同**。

用法（上传到 ECS 后）：python3 /tmp/p12f_check.py
"""
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
CFG = "/opt/dzmm/config/config.json"
BACKUP = "/tmp/p12f_config_backup.json"
AUDIT = "/opt/dzmm/config/config_audit.log"
PROMPT_DIR = "/opt/dzmm/prompts"
CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
DEAD_CID = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"     # 已停用的群（验收用）
MARK = "【P12F验收】"
PHRASE = "no_play"
# 优先挑冷门提示词（只在主动用那个功能时才读），尽可能不影响群里
PROMPT_PREFER = ["POEM_PROMPT", "FORTUNE_PROMPT", "HOROSCOPE_PROMPT",
                 "MEMORY_FILTER_PROMPT", "RECAP_PROMPT"]
FAILS = []


def _sh(cmd, timeout=300):
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


JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))


def req(path, data=None):
    body = urllib.parse.urlencode(data, doseq=True).encode("utf-8") if data else None
    r = urllib.request.Request(BASE + path, data=body)
    if body:
        r.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with OPENER.open(r, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def login(user, pw):
    JAR.clear()
    return req("/login", {"username": user, "password": pw})


def csrf_of(page):
    m = re.search(r"name='csrf' value='([^']*)'", page)
    return m.group(1) if m else ""


def started_at():
    return _sh("docker inspect -f '{{.State.StartedAt}}' dzmm-bot")[1].strip()


def logs(n=200):
    return _sh("docker logs --tail {} dzmm-bot 2>&1".format(n))[1]


def summary():
    st, body = req("/api/summary")
    if st != 200:
        return {}
    try:
        return json.loads(body)
    except Exception:                                          # noqa: BLE001
        return {}


def hot():
    return (summary().get("hotload") or {})


def load_cfg():
    return json.load(open(CFG, encoding="utf-8"))


def save_cfg(d):
    tmp = CFG + ".p12f"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    shutil.move(tmp, CFG)


def wait_for(fn, timeout=30.0, step=0.5):
    """等到 fn() 为真（返回它的值），超时返回 None。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def hot_count():
    return (hot().get("count") or 0)


def wait_count_above(n, timeout=30.0):
    return wait_for(lambda: hot_count() > n, timeout)


print("=== 0) 前置 ===")
USER, PW = env("DZMM_ADMIN_USER"), env("DZMM_ADMIN_PASSWORD")
check("管理员口令已配置", bool(USER and PW))
shutil.copy2(CFG, BACKUP)
BEFORE_BYTES = open(CFG, "rb").read()
T0 = started_at()
print("  容器 StartedAt = {}".format(T0))
print("  配置已备份到 {}".format(BACKUP))


def restore():
    """无论怎么退出，都要把配置恢复成逐字节相同（结构性验收会改文件）。"""
    try:
        shutil.copy2(BACKUP, CFG)
    except Exception as e:                                     # noqa: BLE001
        print("  !! 恢复配置失败：{}（备份在 {}）".format(e, BACKUP))


# 中途被打断（异常/^C/SSH 掉线后进程被收走）也必须把配置放回去 ——
# 第 8 步会把一个已停用群的 enabled 翻成 true，留在磁盘上就会影响**下次重启**。
import atexit  # noqa: E402
atexit.register(restore)


print()
print("=== 1) 未登录不能热加载 ===")
st, body = req("/reload", {"csrf": "x"})
check("未登录只拿到登录页", st == 200 and "请登录" in body, st)
st, page = login(USER, PW)
check("登录成功", st in (200, 303), st)

print()
print("=== 2) 页面状态：现在应当是「已生效」 ===")
st, page = req("/features")
check("/features 200", st == 200, st)
check("显示已生效", "已生效" in page)
check("没有悬挂的「需要重启」", "需要重启" not in page)
check("有「立即热加载」按钮", "action='/reload'" in page)
h0 = hot()
# ⚠️ 面板与机器人是**两个进程**：面板里 `configured` 必然是 False，
# 它读的是机器人写的 config/hotload_status.json（所以 out_of_process 为真）。
check("面板读到了机器人写的状态（跨进程）", h0.get("out_of_process") is True, h0)
check("状态里有时间与次数", bool(h0.get("at")) and (h0.get("count") or 0) >= 0, h0)
print("  起始 hotload: count={} at={} enabled={}".format(
    h0.get("count"), h0.get("at"), h0.get("enabled")))
check("已启用热加载", h0.get("enabled") is True)

print()
print("=== 3) 改配置 → 不重启就生效 ===")
d = load_cfg()
personas = d.setdefault("personas", {}).setdefault("nuo", {})
personas.setdefault("phrases", {})[PHRASE] = MARK + "现在没有进行中的玩法哦～"
tuning = d.setdefault("tuning", {})
old_cd = tuning.get("devil.cooldown")
tuning["devil.cooldown"] = 601 if old_cd != 601 else 602
save_cfg(d)
print("  写入：话术 {} + 参数 devil.cooldown={}".format(PHRASE, tuning["devil.cooldown"]))
n1 = wait_count_above(h0.get("count") or 0, timeout=40)
check("自动热加载发生了（不用点按钮）", bool(n1), hot())
check("来源是 auto（主循环发现的）", hot().get("last_source") == "auto", hot())
check("报告了改动条数 ≥2", (hot().get("last_changes") or 0) >= 2, hot())
check("**没有重启容器**", started_at() == T0, started_at())
lines = logs(200)
check("日志里有热加载自证行", "P12: 热加载完成" in lines)
for ln in lines.splitlines():
    if "P12: 热加载" in ln:
        print("  " + ln.strip())
st, page = req("/features")
check("页面回到「已生效」", "已生效" in page)

# 运行期真的用上了新值 —— 用一次性容器读**磁盘+运行期同一条路**之外，
# 这里读的是**活着的进程自己报的状态**（count/at 来自内存）。
check("运行期状态里记下了最后来源", bool(hot().get("at")), hot())

print()
print("=== 4) 坏配置：拒绝应用 + 保持原样 + 不重启 ===")
d_bad = load_cfg()
d_bad["features"] = {"no_such_feature_p12f": True}
save_cfg(d_bad)
n_before = hot_count()
time.sleep(8)
h_bad = hot()
check("状态里记下了错误", bool(h_bad.get("error")), h_bad.get("error"))
check("计数没有增加（没应用）", hot_count() == n_before, hot_count())
check("机器人仍然活着且没重启", started_at() == T0, started_at())
bad_lines = logs(120)
check("日志明说「未生效」", "热加载 **未生效**" in bad_lines)
st, page = req("/features")
check("页面给出红色横幅", "不合法" in page and "运行期保持原样" in page, st)

print()
print("=== 5) 改回去 → 自动恢复 ===")
save_cfg(d)                       # 恢复成第 3 步那份（仍然是合法的新配置）
n2 = wait_count_above(n_before, timeout=40)
check("恢复正常配置后再次热加载", bool(n2), hot())
check("错误已清空", not hot().get("error"), hot().get("error"))
st, page = req("/features")
check("页面回到「已生效」", "已生效" in page and "不合法" not in page)

print()
print("=== 6) 提示词热加载 ===")
name = None
for cand in PROMPT_PREFER:
    if os.path.isfile(os.path.join(PROMPT_DIR, cand + ".md")):
        name = cand
        break
if name is None:
    files = sorted(f for f in os.listdir(PROMPT_DIR) if f.endswith(".md"))
    name = files[0][:-3] if files else None
print("  选用提示词：{}".format(name))
if name:
    path = os.path.join(PROMPT_DIR, name + ".md")
    original = open(path, encoding="utf-8").read()
    try:
        n3 = hot_count()
        with open(path, "w", encoding="utf-8") as f:
            f.write(original.rstrip("\n") + "\n" + MARK + "（临时行）\n")
        got = wait_for(lambda: hot_count() > n3 and
                       (hot().get("last_source") or "") == "prompts", timeout=40)
        check("提示词改动被自动热加载", bool(got), hot())
        check("**没有重启容器**", started_at() == T0, started_at())
    finally:
        # 立刻改回去（这一行只在"有人刚好用到这个功能"时才可能被读到）
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
    n4 = hot_count()
    got2 = wait_for(lambda: hot_count() > n4, timeout=40)
    check("提示词改回去也被热加载", bool(got2), hot())
    check("文件内容已还原", open(path, encoding="utf-8").read() == original)

print()
print("=== 7) 手动按钮（POST /reload）===")
st, page = req("/features")
C = csrf_of(page)
n_btn = hot_count()
st, body = req("/reload", {"csrf": C})
check("按钮返回 200", st == 200, st)
check("页面如实说「已请求机器人进程」", "已请求机器人进程立即热加载" in body)
got = wait_count_above(n_btn, timeout=20)
check("机器人进程真的被叫醒并重读", bool(got), hot())
check("来源记为 manual", hot().get("last_source") == "manual", hot())
check("**没有重启容器**", started_at() == T0, started_at())
st, _b = req("/reload", {"csrf": "wrong"})
check("错的 csrf 被拒", st == 400, st)

print()
print("=== 8) 结构性改动：只报告，不应用 ===")
d3 = load_cfg()
hit = None
for g in d3.get("groups") or []:
    if g.get("cid") == DEAD_CID:
        hit = g
        break
if hit is None:
    check("找到已停用的群（验收用）", False, DEAD_CID[:8])
else:
    hit["enabled"] = True
    save_cfg(d3)
    n5 = hot_count()
    got = wait_count_above(n5, timeout=40)
    check("结构性改动也触发了热加载检查", bool(got), hot())
    check("报告里说了「需要重启」", bool(hot().get("structural")), hot().get("structural"))
    check("**仍然没有重启**", started_at() == T0, started_at())
    lines = logs(120)
    check("日志里有「需要重启才生效」", "需要重启才生效" in lines)
    st, page = req("/features")
    check("页面提示需要重启", "需要重启" in page)

print()
print("=== 9) 恢复配置 + 最终状态 ===")
restore()
time.sleep(8)
check("配置已恢复成逐字节相同", open(CFG, "rb").read() == BEFORE_BYTES)
check("全程没有重启（StartedAt 未变）", started_at() == T0, started_at())
exc = _sh("docker logs --tail 300 dzmm-bot 2>&1 | grep -ciE 'traceback|exception' "
          "|| true")[1].strip()
check("日志无异常", exc == "0", exc)
h9 = hot()
print("  最终 hotload: count={} at={} source={} error={!r} structural={}".format(
    h9.get("count"), h9.get("at"), h9.get("last_source"), h9.get("error"),
    h9.get("structural")))
check("最终没有待处理错误", not h9.get("error"), h9.get("error"))
st, _b = req("/healthz")
check("后台仍存活", st == 200, st)
print("  （备份留在 {}，可人工核对）".format(BACKUP))

print()
if FAILS:
    print("===> 热加载验收：**有失败项** ✗ {}".format(FAILS))
    sys.exit(1)
print("===> 热加载验收：全部通过 ✓（全程零重启）")
