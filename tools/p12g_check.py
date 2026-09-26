#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P12-G 生产验收：文案模板页（40 个提示词可编辑，改坏不可能保存成功）。

三条要证明的事：

1. **能改**：C1 文案改一行 → 落盘 + 留历史 + 约 1 秒热加载生效（**不重启**）。
2. **改坏会被拒**：把形状要求/契约字面量删掉 → 400 + **文件逐字节不变** + 不留历史。
3. **这一页只写 prompts/**：config.json 全程逐字节不变（那一页的纪律不越界）。

改动挑**无害**的：`DEFEND_PROMPT`（盘点里标为死代码/未接线，改它不影响任何线上行为）。
C3 自测用 `MEMORY_FILTER_PROMPT`（第 40 个，样例很小）：真调一次模型、只验契约，不写文件。

用法（上传到 ECS 后）：python3 /tmp/p12g_check.py
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
PDIR = "/opt/dzmm/prompts"
BDIR = "/opt/dzmm/prompts/prompts_backups"
AUDIT = "/opt/dzmm/config/config_audit.log"
MARK = "\n（P12G 验收临时行，已撤回）"
C1 = "DEFEND_PROMPT"                 # 死代码：改了不影响线上
C2 = "FORTUNE_PROMPT"                # 形状敏感：用来验"删掉要求就被拒"
C3 = "MEMORY_FILTER_PROMPT"          # 第 40 个：用来验契约自测通路
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
        with OPENER.open(r, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def csrf_of(page):
    m = re.search(r"name='csrf' value='([^']*)'", page)
    return m.group(1) if m else ""


def started_at():
    return _sh("docker inspect -f '{{.State.StartedAt}}' dzmm-bot")[1].strip()


def hot():
    st, body = req("/api/summary")
    if st != 200:
        return {}
    try:
        return json.loads(body).get("hotload") or {}
    except Exception:                                          # noqa: BLE001
        return {}


def pfile(key):
    return os.path.join(PDIR, key + ".md")


def logs(n=120):
    return _sh("docker logs --tail {} dzmm-bot 2>&1".format(n))[1]


def pread(key):
    try:
        return open(pfile(key), "rb").read()
    except OSError:
        return None


def wait_for(fn, timeout=40.0, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def probe(code):
    cmd = ["docker", "run", "--rm", "--entrypoint", "python",
           "-e", "PYTHONPATH=/app", "-e", "PYTHONIOENCODING=utf-8",
           "-e", "DZMM_STORE_DB=/tmp/p12g_probe.db",
           "-v", "/opt/dzmm:/app:ro", "-w", "/tmp", "dzmm-bot:latest", "-c", code]
    return _sh(cmd, timeout=300)


print("=== 0) 前置 ===")
USER, PW = env("DZMM_ADMIN_USER"), env("DZMM_ADMIN_PASSWORD")
check("管理员口令已配置", bool(USER and PW))
T0 = started_at()
CFG_BEFORE = open(CFG, "rb").read()
C1_BEFORE = pread(C1)
print("  StartedAt = {}".format(T0))
print("  {} 当前大小 = {} 字节".format(C1, len(C1_BEFORE or b"")))
# 前置：40 个文件都在（上一轮验收若留下垃圾，这里先暴露出来）
_n0 = _sh("ls -1 {}/*.md 2>/dev/null | wc -l".format(PDIR))[1].strip()
check("前置：prompts/ 里 40 个文件都在", _n0 == "40", _n0)
check("对比用的两个提示词文件都在", bool(C1_BEFORE) and pread(C2) is not None)


def cleanup():
    """撤回验收改动。

    ⚠️ **不是删文件**：`prompts/` 里本来就齐着 40 个文件（默认值的载体），
    删掉会把"文件与登记册一一对应"这条不变量弄坏（而且线上那个文件就没了）。
    第 5 步已经把默认文本写回去了 ⇒ 正常情况下这里什么都不用做；
    只有脚本中途失败时，才需要把原内容写回去。
    """
    try:
        cur = pread(C1)
        if cur is not None and MARK.encode("utf-8") in cur:
            with open(pfile(C1), "wb") as f:
                f.write(C1_BEFORE)
            print("  （已把 {} 还原成验收前的内容）".format(C1))
    except Exception as e:                                     # noqa: BLE001
        print("  !! 清理失败：{}".format(e))


import atexit  # noqa: E402
atexit.register(cleanup)

print()
print("=== 1) 未登录 + 页面渲染 ===")
st, body = req("/prompts")
check("未登录只拿到登录页", st == 200 and "请登录" in body, st)
st, _ = req("/login", {"username": USER, "password": PW})
check("登录成功", st in (200, 303), st)
st, page = req("/prompts")
check("/prompts 200", st == 200, st)
keys = set(re.findall(r"name=([A-Z_]+_PROMPT)", page))
check("页面上 40 个提示词都在", len(keys) == 40, len(keys))
check("按三档分组", all(x in page for x in ("C1 · 自由文案", "C2 · 形状敏感",
                                          "C3 · 结构化输出")))
check("数量标注 23/11/6", "（23 个）" in page and "（11 个）" in page
      and "（6 个）" in page)
check("第 40 个在页面上", C3 in page)

print()
print("=== 2) 删掉形状要求必须被拒（一个字都不写） ===")
st, ed = req("/prompts?name={}".format(C2))
check("编辑器打得开", st == 200 and "不要换行" in ed, st)
C = csrf_of(ed)
C2_BEFORE = pread(C2)
st, bad = req("/prompts", {"csrf": C, "key": C2, "action": "save",
                           "text": "只写一段话，随便怎么写都行"})
check("被拒（400）", st == 400, st)
check("说清了缺什么", "不要换行" in bad, bad[:120])
check("文件逐字节没变", pread(C2) == C2_BEFORE)
_n_hist2 = _sh("ls -1 {} 2>/dev/null | grep -c '^{}-' || true".format(
    BDIR, C2))[1].strip()
check("没有留下历史版本（被拒就不该留）", _n_hist2 in ("0", ""), _n_hist2)

print()
print("=== 3) 改一句 C1 文案 → 落盘 + 历史 + 热加载 ===")
h0 = (hot().get("count") or 0)
md5_before = _sh("md5sum {}".format(pfile(C1)))[1].split()[0]
st, ed = req("/prompts?name={}".format(C1))
C = csrf_of(ed)
text = C1_BEFORE.decode("utf-8") + MARK
st, ok = req("/prompts", {"csrf": C, "key": C1, "action": "save", "text": text})
check("保存 200", st == 200, st)
check("页面报「已保存」且提热加载", "已保存" in ok and "热加载" in ok)
now = pread(C1)
md5_after = _sh("md5sum {}".format(pfile(C1)))[1].split()[0]
print("  {} : {} 字节 -> {} 字节；md5 {} -> {}".format(
    C1, len(C1_BEFORE or b""), len(now or b""), md5_before[:8], md5_after[:8]))
check("文件内容真的变了（md5 不同）", md5_before != md5_after,
      "{} / {}".format(md5_before, md5_after))
check("文件里含验收标记",
      now is not None and MARK.encode("utf-8") in now)
_n_hist1 = _sh("ls -1 {} 2>/dev/null | grep -c '^{}-' || true".format(
    BDIR, C1))[1].strip()
check("留了历史版本", _n_hist1.isdigit() and int(_n_hist1) >= 1, _n_hist1)
h1 = wait_for(lambda: (hot().get("count") or 0) > h0, timeout=30)
# ⚠️ 来源会是 `manual`：页面保存后写的是**热加载请求文件**（跨进程，见 runtime/hotload.py），
# 机器人消费它时记为 manual；`prompts` 那个来源是主循环自己按 mtime 发现的。
check("热加载发生了", bool(h1), hot())
check("日志里明确写了是哪个提示词",
      "热加载 提示词 · {}".format(C1) in logs(),
      [x for x in logs().splitlines() if "热加载 提示词" in x][-2:])
check("**没有重启容器**", started_at() == T0, started_at())
# 运行期证据：一次性容器里 `import brains` 走的是**生产同一条**路径
# （读同一份 prompts/ + 同一套 _p() 机制）。这里比 **md5**，不比打印的文本 ——
# 中文经过 SSH/终端两层转码后打印出来会缺字，比内容会被这种假象骗到。
rc, out = probe("import hashlib, brains;"
                "print('PROBE|' + hashlib.md5("
                "brains.DEFEND_PROMPT.encode('utf-8')).hexdigest())")
line = [x for x in out.splitlines() if x.startswith("PROBE|")]
_probe_md5 = line[-1].split("|")[1].strip() if line else ""
print("  磁盘 md5 = {} / 容器内 md5 = {}".format(md5_after, _probe_md5))
check("容器里读到的与磁盘上的**逐字节一致**",
      bool(line) and _probe_md5 == md5_after,
      "{} vs {}".format(_probe_md5, md5_after))

print()
print("=== 4) C3 契约自测通路（真调一次模型，不写文件） ===")
st, ed = req("/prompts?name={}".format(C3))
check("C3 编辑器打得开（并标明会自动自测）", st == 200 and "保存并自测" in ed, st)
C = csrf_of(ed)
c3_before = pread(C3)
st, pv = req("/prompts", {"csrf": C, "key": C3, "action": "preview",
                          "text": c3_before.decode("utf-8")})
check("预览返回 200", st == 200, st)
check("给出了契约判定", ("契约通过" in pv) or ("没通过" in pv), pv[:200])
check("给出了返回的字数/换行", "返回" in pv and "换行" in pv)
check("预览不写文件", pread(C3) == c3_before)

print()
print("=== 5) 恢复代码默认（把默认文本写回文件） ===")
st, ed = req("/prompts?name={}".format(C1))
C = csrf_of(ed)
h2 = (hot().get("count") or 0)
st, reset = req("/prompts/reset", {"csrf": C, "key": C1})
check("恢复 200", st == 200, st)
check("页面报「已恢复代码默认值」", "已恢复代码默认值" in reset)
_now = pread(C1)
check("文件已写回默认文本（不再有验收标记）",
      _now is not None and MARK.encode("utf-8") not in _now)
h3 = wait_for(lambda: (hot().get("count") or 0) > h2, timeout=30)
check("恢复也触发了热加载", bool(h3), hot())
md5_restored = _sh("md5sum {}".format(pfile(C1)))[1].split()[0]
rc, out = probe("import hashlib, brains;"
                "print('PROBE|' + hashlib.md5("
                "brains.DEFEND_PROMPT.encode('utf-8')).hexdigest())")
line = [x for x in out.splitlines() if x.startswith("PROBE|")]
_probe2 = line[-1].split("|")[1].strip() if line else ""
check("运行期也回到默认（磁盘与容器内 md5 一致且不是改过的那版）",
      bool(line) and _probe2 == md5_restored and _probe2 != md5_after,
      "{} vs {} (改过的那版 {})".format(_probe2, md5_restored, md5_after))

print()
print("=== 6) 这一页只写 prompts/ ===")
check("config.json 逐字节未变", open(CFG, "rb").read() == CFG_BEFORE)
check("审计里有文案模板的记录",
      "文案模板" in open(AUDIT, encoding="utf-8", errors="replace").read()[-6000:])
check("全程没有重启", started_at() == T0, started_at())
exc = _sh("docker logs --tail 400 dzmm-bot 2>&1 | "
          "grep -ciE 'traceback|exception' || true")[1].strip()
check("日志无异常", exc == "0", exc)

print()
print("=== 7) 最终状态 ===")
n_files = _sh("ls -1 {}/*.md 2>/dev/null | wc -l".format(PDIR))[1].strip()
n_backups = _sh("ls -1 {}/*.md 2>/dev/null | wc -l".format(BDIR))[1].strip()
print("  prompts/*.md = {} 个；历史版本 = {} 个".format(n_files, n_backups))
check("40 个提示词文件都在（恢复默认是写回默认文本，不是删文件）",
      n_files == "40", n_files)
check("历史版本目录已建立（备份策略在跑）", n_backups != "", n_backups)
for ln in _sh("docker logs --tail 60 dzmm-bot 2>&1 | grep -e 'P12:' | tail -6")[1].splitlines():
    print("  " + ln.strip())
st, _b = req("/healthz")
check("后台仍存活", st == 200, st)

print()
if FAILS:
    print("===> 文案模板页验收：**有失败项** ✗ {}".format(FAILS))
    sys.exit(1)
print("===> 文案模板页验收：全部通过 ✓")
