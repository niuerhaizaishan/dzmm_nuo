# 优化方案执行记录（Execution Log）

> 配套 [PLATFORM-OPTIMIZATION.md](PLATFORM-OPTIMIZATION.md) 与 [EXECUTION-PLAN.md](EXECUTION-PLAN.md)。
> 每执行完一个阶段追加一节：**做了什么 → 验证结果 → 意外发现 → 遗留**。
> 执行环境：`D:\Deepseek\dzmm_nuo`（dzmm_botapi 的副本，**尚未部署**）。

---

## 2026-10-01 · Phase 0（即时清理）+ Phase 1（L1 整理）

### 总览

| 阶段 | Step | 状态 | 备注 |
|---|---|---|---|
| Phase 0 | 0-1 Build Cache 清理 | ✅ | 实测无需清理 |
| Phase 0 | 0-2 deploy.ps1 keep-storage | ✅ | 3GB → 1.5GB |
| Phase 0 | 0-3 bot.log 轮转 | ✅ | 新配置 + 实测轮转成功 |
| Phase 0 | 0-4 pw_profile 缓存上限 | ✅ | 两处加 `--disk-cache-size` |
| Phase 0 | 0-5 在线验证 ②③④ | ⏸ 未执行 | 缺真实 @ 流量（非 bot 侧问题，见下文更正） |
| Phase 1 | 1-1 根目录清理 | ✅ | 22 个文件移入 content/ 与 logs/ |
| Phase 1 | 1-2 tools/ 归档 | ✅ | 200 个文件分四类，根目录 0 残留 |
| Phase 1 | 1-3 docs/ 索引 | ✅ | 68/68 篇全索引，0 失效链接 |

全量 `pytest` 在每个 Step 后都跑过，**最终绿**。

---

## Phase 0 明细

### 0-1 Build Cache 清理

```
清理前: Build Cache 71 项 / 1.107GB（RECLAIMABLE 1.107GB）
执行:   docker builder prune -f --keep-storage 1.5GB
结果:   Total: 0B（占用 1.107GB < 阈值 1.5GB ⇒ 无需清理）
```

**这不是失败**：`--keep-storage` 是"保留上限"，当前占用低于上限时本来就无可回收。
真正的膨胀发生在超过阈值之后——0-2 把阈值调低就是为了让它更早介入。

### 0-2 deploy.ps1 keep-storage

改动 `deploy.ps1` 两处（`compose` 与 `deploy` 两个 action 各一处）：

```diff
- docker builder prune -f --keep-storage 3GB
+ docker builder prune -f --keep-storage 1.5GB
```

同时更新了那段注释，写清为什么是 1.5GB：apt + pip + `playwright install chromium`
约 1.3GB，低于它会把"让重建变便宜的那层"也清掉（下次重建要重新下载 chromium）；
1.5GB 留一层昂贵层 + 最近的 COPY 层。并注明"实测占用只有 1.107GB，3GB 属于过量配置"。

**验证**：`grep keep-storage deploy.ps1` → 两处均为 `1.5GB`。

### 0-3 bot.log 轮转

**问题**：`bot.log` 是挂载到宿主机的独立文件，**不受 docker logging driver 管控**
（compose 里的 `max-size: 10m` 只管容器 stdout），长期运行会无限增长。

新建 `/etc/logrotate.d/dzmm-bot`：

```
/opt/dzmm/bot.log /opt/dzmm/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

`copytruncate` 是必须的：bot 进程一直持有该文件的写句柄，改名方案会让它继续写旧 inode。

**验证**：
```
logrotate -d /etc/logrotate.d/dzmm-bot   → 语法通过
logrotate -f /etc/logrotate.d/dzmm-bot   → 实测轮转
轮转前: bot.log 2.0M
轮转后: bot.log 0      bot.log.1 2.0M
```

### 0-4 Playwright profile 缓存上限

**问题**：`pw_profile` 114MB（本次实测 125MB）、`pw_login` 28MB、`pw_captcha` 14MB，
Chromium 磁盘缓存无上限。

改动两个文件，在 `launch_persistent_context` 的 `args` 里加 `--disk-cache-size=52428800`（50MB）：

- `bot.py:796` 附近的常驻上下文
- `captcha_novnc.py:122` 附近的验证码专用上下文

**验证**：`grep -n disk-cache-size` → 两处命中。
**注意**：这个上限只影响磁盘缓存，**不动 cookie 与登录态**。

### 0-5 在线验证 ②③④ —— 未执行（缺真实 @ 流量）

上班群这段时间**没有 @ 若瑾的消息**，所以三类新日志（`👥 双边上下文` / `⏳ @ 冷却中` /
`🪞 收尾像递话`）一次都没触发。等自然 @ 流量，或跑离线探针：

```bash
docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python /app/tools/probes/probe_bd_ab.py 3
```

> ⚠️ **更正一处早先的错误归因**：本节初稿写成"阿里云百炼 429 配额用尽导致阻塞"——
> **那是错的**。那个 `insufficient_quota` 是**本次施工所用的对话工具链（我这侧的模型调用）**
> 报的限流，与 dzmm bot 无关：**bot 用的是 DeepSeek API，不走百炼**。
> 0-5 没做成的真实原因只是"没有 @ 流量"，不该记在某个不属于 bot 的配额上。
> （教训：把外部工具的报错塞进被诊断系统的因果链之前，先确认它到底属于哪一侧。）

---

## Phase 1 明细

### 1-1 根目录清理

**先做了一次引用排查**，这一步救了后面很多事：

| 文件 | 代码引用 | 结论 |
|---|---|---|
| `profiles.json` | `state_store.py:32`、`profiles.py:20`、`check_bot_health.py:294` | ❌ 不动 |
| `state.json` | `state_store.py:31` | ❌ 不动 |
| `bot.log` | `runtime/obs.py:19`、`admin/dashboard.py:98` | ❌ 不动 |
| `renew_token.log` | `renew_token.py:219`、`admin/dashboard.py:100` | ❌ 不动 |
| `check_health.log` | `check_bot_health.py:57`、`admin/dashboard.py:99` | ❌ 不动 |
| `config_audit.log` | `runtime/config_store.py:263` | ❌ 不动 |
| 小说 / 使用说明 / 旧日志 | 无代码引用（仅注释提及） | ✅ 可移 |

**偏离计划的地方**：EXECUTION-PLAN 里写的是把 `profiles.json`/`state.json`/`bot.log`
一起移进 `data/` 与 `logs/`。实测发现它们被十几处以 `ROOT / "文件名"` 硬编码引用，
移一次要同步改十几处代码，**风险远大于收益**。所以这一步只移动**纯内容文件**，
数据文件与活跃日志留在原位（`bot.log` 的增长问题由 0-3 的 logrotate 解决）。

实际移动 22 个文件：

```
content/novels/   发骚整理_御褚的高跟鞋_20260825.md  苏若曦.md
                  高冷校花偷舔我的鞋.md  公演小说.md
content/docs/     优化分析报告.md  公演功能使用说明.md  妹子认证群-小小糯使用说明.md
                  玫瑰房-小小糯房规.md  玫瑰房-摘花规则.md  玫瑰房-星座运势说明.md
                  玫瑰房-贴群说明.md  给老板-今日功能汇报.md  给老板-功能更新汇报.md
                  更新记录_2026-08-15.md
logs/             compose-p8s3.log  compose-p9.log  upload-p8s3.log  upload-p8s4a.log
                  upload-p8s4b.log  upload-p8s4c.log  upload-p9.log  _deploy_p12t5.log
```

**验证**：三个目录内容齐备；`grep` 确认没有代码读这些 `.md`（只有注释里提到"公演小说"
这个**默认标题字符串**，不是文件路径）；全量 pytest 绿。

### 1-2 tools/ 归档分类

原状：144 个 `.py` + 54 个 `.sh` + 2 个夹具，**平铺在一个目录里**。

分类规则：

| 目录 | 规则 | 数量 |
|---|---|---|
| `active/` | 还在用的运维工具（`set_*`/`cfg_*`/`resolve_*`/`enable_*`/`config_gen`/`merge_json_*`…） | 43 |
| `probes/` | 探针/验证/分析/审计（`probe_*`/`verify_*`/`analyze_*`/`audit_*`/`watch_*`） | 82 |
| `archive/` | 一次性迁移与排查（`p8s4_*`/`p8_*`/`p9_*`/`p12*_check`/`migrate_*` + 54 个 `.sh`） | 73 |
| `fixtures/` | 测试夹具（`CMD_PROMPT_pre_p12ap.md`、`ruojin_extra_p12ah_before.txt`） | 2 |

**验证**：根目录 `.py` 残留 0；全量 pytest 绿。

### 1-3 docs/ 索引

新建 `docs/INDEX.md`：68 篇按 **8 个主题**分类（入门与路线 / 消息链路 / 续期与会话 /
人设与话术 / 功能设计 / 运维与施工 / Bot API / 中期施工），并附「施工记录模板」与两条写作纪律。

**验证**：脚本校验全部链接 → **68/68 已索引，0 失效链接**。

第一遍写完时有 4 个链接指向**从未写过的文档**（`P12-BE-双边上下文.md`、
`P12-BQ-回复冷却与递话兜底.md`、`P12-BP-硬编码提示走话术表.md`、
`P12-BO-验证码退避不再挡住升级链.md`）——这几篇的内容实际收编在
`P12-BP-BQ-BE-上线记录.md` 与 `P12-BL-续期链再检查.md` 里。已改指真实文件
（**不留死链**，也不假造文档）。

---

## 计划外发现的四个问题（都当场修掉了）

### 问题 1：BOM 回归 —— 我自己引入的

用 PowerShell 批量改 24 个工具脚本的 `ROOT` 时，`Set-Content -Encoding UTF8`
**写入了 BOM（U+FEFF）**。后果：`ast.parse` 直接报
`SyntaxError: invalid non-printable character U+FEFF`，24 个文件全挂。

修法：按字节检测 `EF BB BF` 并剥离（幂等），再逐个复验语法 → **0 个语法错误**。

> **教训**：批量改文件用 `[IO.File]::WriteAllBytes` / Python 的
> `encoding='utf-8'`（**不带** `-sig`）。PowerShell 的 `-Encoding UTF8` 在不同
> 版本/宿主下行为不一致，不能依赖。

### 问题 2：`ROOT` 深度 —— 移动文件必然踩到

24 个脚本用 `ROOT = Path(__file__).resolve().parent.parent` 定位仓库根。
从 `tools/` 移到 `tools/active/` 后多了一层，`ROOT` 变成 `tools/` ⇒ 全部算错。

修法：正则批量替换 `resolve().parent.parent` → `resolve().parent.parent.parent`
（用**负向前瞻** `(?!\.parent)` 避免把已修过的文件再叠一层）。

**但随即发现批量替换误伤了模板**：`tools/archive/p8s4_move.py` 里有 3 处
`ROOT = Path(...)`，其中 **2 处在 `header='''...'''` 生成代码模板里**——
那些模块生成到 `runtime/` 下，深度本来就是**两层**。
拿 ground truth 比对后确认（`runtime/obs.py` 实际用的是 `.parent.parent`），
把这 2 处单独回退，只留脚本自己那处（第 28 行）为三层。

> **教训**：批量正则替换**必须逐处看上下文**。这个文件里"同一条语句"有两种正确形态。

### 问题 3：测试引用 tools/ 路径

四个测试按路径读 `tools/` 下的文件，移动后全部失效：

| 测试 | 引用 | 改成 |
|---|---|---|
| `tests/test_wall_evidence.py` | `sys.path` 加 `tools/` | `tools/probes/` |
| `tests/test_persona_texts.py` | `sys.path` 加 `tools/`（import `cfg_persona`） | `tools/active/` |
| `tests/test_renew_hardening.py` | `tools/probe_ua_wall.py` | `tools/probes/…` |
| `tests/test_renew_backoff_escalation.py` | `tools/probe_session_layers.py` | `tools/probes/…` |

跨目录 import 也确认过：`probes/probe_bd_ab.py` 依赖同目录 `probe_solicit.py`、
`archive/p8s4_audit.py` 依赖同目录 `p8s4_move.py` —— **分类时特意让它们同目录**，
没拆散。

### 问题 4：app.js 用了 innerHTML（被既有测试拦下）

重写的 `admin/static/app.js` 在"未保存改动"提示条里用了 `box.innerHTML = '...<strong>'
+ n + '</strong>...'`，被 `tests/test_features_ui.py` 的防 XSS 断言拦下
（该测试要求脚本里不出现 `.innerHTML` / `insertAdjacentHTML`）。

改成 DOM API：

```js
while (box.firstChild) { box.removeChild(box.firstChild); }
var icon = document.createElement("span");
icon.className = "msg__icon";  icon.textContent = "⚠️";
var content = document.createElement("span");
content.className = "msg__content";
content.textContent = "有 " + dirtyCount + " 项改动还没保存";
box.appendChild(icon); box.appendChild(content);
```

> 这条**不是坏事**：说明既有测试确实在守着 CSP/XSS 边界，我新写的代码没绕过它。

**另外**：中途遇到过两次"跑全量时某个 admin 测试报 `ConnectionAbortedError (WinError 10053)`"，
单独跑就过 —— 那是 Windows 下服务端不读请求体就关连接发 RST 的老毛病（`dashboard.py`
注释里记过），**不是本次改动引入的**，复跑即绿。

---

## 本阶段新增/修改的文件清单

### 新增

| 文件 | 用途 |
|---|---|
| `tools/README.md` | 四类目录的用途 + "移动后要注意两件事"（ROOT 深度、跨目录 import）+ 测试引用路径对照表 |
| `docs/INDEX.md` | 68 篇按主题分类的文档索引 + 施工记录模板 |
| `docs/EXECUTION-LOG.md` | **本文件** |
| `/etc/logrotate.d/dzmm-bot`（服务器） | bot.log 轮转配置 |

### 修改

| 文件 | 改了什么 |
|---|---|
| `deploy.ps1` | `--keep-storage 3GB` → `1.5GB`（两处）+ 注释 |
| `bot.py` | Playwright `args` 加 `--disk-cache-size=52428800` |
| `captcha_novnc.py` | 同上 |
| `admin/static/app.js` | `innerHTML` → DOM API（过 XSS 测试） |
| `tools/**/*.py`（24 个） | `ROOT` 深度 +1；剥离 BOM |
| `tools/archive/p8s4_move.py` | 模板里 2 处深度**回退**为两层 |
| `tools/archive/p8s4_audit.py` / `p8s4_fix_headers.py` | `ROOT` 深度 + 内部路径 |
| `tests/test_wall_evidence.py` 等 4 个 | tools 路径同步 |

### 移动（未改内容）

- 22 个内容/旧日志文件 → `content/novels`、`content/docs`、`logs/`
- 200 个工具文件 → `tools/active`、`tools/probes`、`tools/archive`、`tools/fixtures`

---

## 遗留

1. **0-5 在线验证 ②③④** —— 等真实 @ 流量；或跑
   `tools/probes/probe_bd_ab.py` 离线验一部分（不发群消息）。
2. **Phase 2（S1 凭据挂目录）未开始** —— 计划里标注"需施工窗口"：
   改 compose + 宿主目录迁移 + 至少 15 分钟双写观察 + 24h 无错才收尾。
   ⚠️ 它还会连带解决 round-13 记下的四个附加问题（`token.emergency` 路径不匹配、
   多冷却叠加死锁、`.session.lock` 路径不统一）。
3. **本项目的改动尚未部署** —— `dzmm_nuo` 是副本，线上跑的还是 `dzmm_botapi` 那一份。

---

## 可复用的检查命令

```powershell
# 全量测试
python -m pytest -q

# 文档索引链接校验
cd docs
Select-String -Path INDEX.md -Pattern '\]\(([^)]+\.md)\)' -AllMatches |
  ForEach-Object { $_.Matches | ForEach-Object { $_.Groups[1].Value } } |
  ForEach-Object { if (-not (Test-Path $_)) { "❌ $_" } }

# 工具脚本语法自检
cd tools
Get-ChildItem active,probes,archive -File -Filter *.py |
  ForEach-Object { python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $_.FullName }

# 检查有没有 BOM（应为空）
Get-ChildItem -Recurse -File -Filter *.py |
  Where-Object { $b=[IO.File]::ReadAllBytes($_.FullName); $b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF } |
  Select-Object -ExpandProperty FullName

# 会话锁诊断（谁持有 / 能否立刻拿到）
docker exec dzmm-bot python3 /app/tools/probes/probe_session_lock.py
```

---

# 2026-10-01 10:20–10:33 · 运行状态检查：**发现 P0「持锁调子进程」缺陷**

> 起因：用户让"检查一下运行状态"。检查中发现会话死亡，且**自动恢复路径自己把自己锁死**。
> 这次**只诊断与手动恢复，未改生产代码**（改动待批准）。

## 时间线（从日志逐条对齐）

| 时刻 | 事件 |
|---|---|
| 10:20:04 | 会话开始坏（`bad_since`），业务探针 `unauth` |
| 10:20:05 | renew_token `SESSION_DEAD` → 写 `token.emergency`；**飞书告警 ✅ 已发** |
| 10:20:11 | 巡检告警：**进程挂了 + 会话失效 + 续期脚本失败**（三条合并告警） |
| 10:20（约） | entrypoint 检测到 `token.emergency` → 重启 bot.py |
| 10:25:05 | 巡检流程触发 `ESCALATE: 自动跑一次密码重登（headless_relogin.py）` |
| **10:28:05** | **`[relogin] SKIP: 另一个进程正在续期/重登`** ← 正好 180 秒后 |
| 10:28:06 | 落回"撞验证码退避中，保留 unauth 让巡检告警" |
| 10:32:59 | 人工介入（手动跑 `headless_relogin.py` → 11 秒成功）后：`OK: 续期恢复正常（本轮共坏 13 分钟）` |

## 根因：`renew_token` 拿着锁去调「也要拿同一把锁」的子进程

```python
# renew_token.py:843-859
def main() -> int:
    from session_guard import guard
    with guard("renew_token", timeout=20) as got:     # ← 父进程持有 flock（fd1）
        if not got: ...
        rc = _renew_locked()                          # ← 在锁内
            # → _try_password_relogin()（第 748 行）
            #   → subprocess.run([python, headless_relogin.py])（第 139 行）
            #       └ headless_relogin.py:220
            #         with guard("headless_relogin", timeout=180) as got:   # ← 再开 fd2 去 flock
            #             if not got: print("SKIP: 另一个进程正在续期/重登…")
```

`flock` 是 **per open-file-description** 的锁。子进程 `exec` 后虽然继承了 fd1，
但它自己又 `os.open` 了一次（fd2，**新的** OFD）⇒ 与父进程的排他锁冲突 ⇒
拿不到 ⇒ 死等 `timeout=180` ⇒ 打印 SKIP ⇒ 返回 1。

**⇒ 自动密码重登 100% 失败，每次白等 3 分钟。** 这条路径只有在人工干预时才走得通
（手动执行 `headless_relogin.py` 时没有父进程持锁，**11 秒**就完成）。

### 这与 8.8h 事故是**同一个**根因

2026-09-30 深夜那次（`docs/P12-BL` §2-3 记过）我当时的结论是
"`.session.lock` 是 stale lock，手动删掉就恢复了"。**那个结论只对了一半**：

- 锁文件**残留本身是正常的**（flock 模式刻意不删文件，内核在进程退出时释放锁）；
- 真正的原因是**父进程活着并且合法地持有锁** ⇒ 子进程永远拿不到。
  我"删锁"之所以有效，是因为删除后下次父进程重新 open 得到一个**新 inode**，
  绕开了冲突 —— 属于**误打误撞**，不是正确修法。

**验证方式**（本次用 `tools/probes/probe_session_lock.py` 实测）：
```
锁文件存在 = True
内容: renew_token pid=801 at=1790821505
能否立刻拿到 flock = True          ← 锁是自由的！PID 801 早已不存在
打开该文件的进程（0 个）
```
⇒ "锁文件存在" 与 "锁被持有" **是两件事**，之前把它们混为一谈导致误判。

## 顺带证实的两件事

1. **飞书告警修复已生效**：这次全程 `飞书告警: ✅ 已发` 出现 4 次
   （10:20:05 / 10:28:05 / 10:28:06 ×2 / 10:32:59）。对比 8.8h 那次全程静默，
   08:24 部署的"不再静默吞异常"改动确实起作用了。
2. **S1（凭据单文件挂载）仍在**：手动重登时日志照旧出现
   `原子替换失败（多半是单文件 bind mount），退化为就地写（EBUSY: '/app/.dzmm_token.json.tmp' -> '/app/.dzmm_token.json'）`。
   Phase 2 要修的就是它。

## 修法（**本次不实施，仅记录**——屋主 2026-10-01："写进文档就行了"）

**方案 A（推荐，最小改动，保持"唯一登录者"约束）**

父进程已经持锁，子进程**不需要再锁**——它是同一把锁保护的同一件事。
给 `subprocess.run` 传一个标记，让 `headless_relogin` 在标记存在时跳过错：

```python
# renew_token.py:_try_password_relogin
env={**os.environ, "PYTHONIOENCODING": "utf-8",
     "DZMM_RELOGIN_UNDER_PARENT_LOCK": "1"}      # ← 新增

# headless_relogin.py:main
under_parent = os.environ.get("DZMM_RELOGIN_UNDER_PARENT_LOCK") == "1"
cm = nullcontext(True) if under_parent else guard("headless_relogin", timeout=180)
with cm as got:
    if not got: print("SKIP: …"); return 1
```

不用 `DZMM_SESSION_LOCK=off`：那是**全局**开关，语义太宽（会让这次登录完全脱离锁保护）。

**方案 B**：把 `_try_password_relogin()` 移到锁**外**调用。
改动更大（`_renew_locked` 目前在锁内假设自己独占），**不推荐**。

**配套加固**（可与方案 A 一起做）：
- `guard()` 拿不到锁时的日志，打印**持锁者信息**（锁文件里就有 `reason pid=… at=…`），
  现在只说"另一个进程正在"，排障时还得自己去猜。
- `_try_password_relogin` 在 `subprocess.run` 前**先探测一次锁是否可获取**；
  若父进程自己持锁而子进程必然拿不到，应当**直接判定为设计错误并告警**，
  而不是白等 180 秒。

## 本次新增文件

| 文件 | 用途 |
|---|---|
| `tools/probes/probe_session_lock.py` | 诊断：锁文件内容、能否立刻拿到 flock、哪些进程开着它（只读） |

## 遗留

- **P0 缺陷（问题 E）本次不修，只记录** —— 屋主 2026-10-01 的决定："写进文档就行了"。
  根因、影响、两种修法、配套加固都已在本文与
  [PLATFORM-OPTIMIZATION.md](PLATFORM-OPTIMIZATION.md) §2-3E 里写清，
  将来要动时照那一节做即可（方案 A + 单测）。
- 不改的后果要留着：巡检能告警（这点已修好），但**自动恢复是坏的** ——
  这个缺陷会让 8.8h 那种长故障重演，**必须人工介入**。
  两次长故障（09-30 的 8.8h、10-01 的 13 分钟）都是靠手动才恢复的。
- **应急手册**（下次会话死时按这个走）：
  > ✅ **2026-10-01 21:5x 起 P0 已修**：`renew_token.py` 给子进程带
  > `DZMM_RELOGIN_UNDER_PARENT_LOCK=1`，`headless_relogin.py` 认这个变量 ⇒
  > 父进程持锁时子进程不再自锁。**第 1 步先确认自动路径是否已经生效**，
  > 只有它仍然 SKIP / FAIL 时才需要走人工。
  ```bash
  # 1. 看自动重登这一轮的结果
  #    修好后期望：`OK: 密码重登成功，会话已恢复`；**不该再出现 SKIP**
  tail -8 /opt/dzmm/renew_token.log

  # 2. 确认锁是否真的被持有（别急着删！）
  #    ⚠️ 镜像里**没有** tools/ 目录 —— 老文档写的
  #       `docker exec dzmm-bot python3 /app/tools/probes/probe_session_lock.py`
  #       必然报 "No such file or directory"。容器内改用这段内联探测（只读）：
  #       打印 LOCK_FREE ⇒ 没人持有，可以放心手动重登；
  #       抛 BlockingIOError ⇒ **有人正在登录，千万别动**。
  docker exec dzmm-bot python3 -c "import fcntl,os;fd=os.open('/app/.session.lock',os.O_CREAT|os.O_RDWR,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);print('LOCK_FREE')"
  # 谁在动凭据（注意用 -e 而不是 "a|b"，双引号会被 PowerShell/外层剥掉）：
  docker exec dzmm-bot ps -ef | grep -e renew_token -e headless_relogin -e bot.py

  # 3. 手动重登（通常 10 秒内完成）
  #    ⚠️ 它抢的是**和 renew_token 同一把锁**（.session.lock）—— 若此刻续期循环
  #    正在跑，会打印 SKIP。不用慌：续期每 300 秒一轮、持锁只有几秒，
  #    等几秒重跑即可。
  docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python headless_relogin.py

  # 4. 刷新心跳并确认恢复
  docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python renew_token.py
  docker exec dzmm-bot cat /app/data/session_state.json      # 期望 probe=ok
  tail -2 /opt/dzmm/check_health.log
  ```
  ⚠️ 第 2 步别跳过：**锁文件存在 ≠ 锁被持有**（flock 刻意不删文件）。
  盲目删锁在"确实有别的进程在登录"时会**破坏"唯一登录者"约束**，
  让两个端同时登录 → refresh_token 轮换互相作废。
- **P0 的正解（已实施）**：`guard()` 只有**一把锁文件**
  （`runtime/session.py` 的 `LOCK_FILE = .session.lock`），`reason` 参数只是写进
  锁文件的一句备注，**不是两把锁**。`tests/test_renew_escalation.py` 原先断言
  "重登走的是另一把锁，不会打架" —— 这条**错误认知就是 P0 的温床**，已更正。

---

## 2026-10-01 · Phase 2（S1 凭据挂目录）—— Step 2-1 + 2-2（代码侧，已完成）

### 做了什么

| Step | 内容 | 状态 |
|---|---|---|
| 2-1 | 凭据读口加环境变量覆盖（**6 个读口**，计划只列了 4 个） | ✅ 本地完成 |
| 2-2 | 生产 compose 进双写期（secrets/ 挂载 + env，老挂载保留） | ✅ 本地完成 |
| 2-3~2-5 | 宿主迁移 / 双写观察 / 清理老挂载 | ✅ 2-3 + 2-4 已施工（10-01 22:01，线上验 `ATOMIC_REPLACE_OK`）；2-5 待 24h 观察后撤老挂载 |

### 读口清单（相对计划的偏离）

| 文件 | 计划 | 实际改动 |
|---|---|---|
| `token_store.py:22` | DZMM_TOKEN_FILE | ✅ 同计划（唯一凭据写入点） |
| `admin/dashboard.py:101` | DZMM_TOKEN_FILE | ✅ 同计划（面板不读陈旧文件） |
| `monitor_recover.py:22` | DZMM_TOKEN_FILE（简单 fallback） | ✅ 改为 **env > secrets/存在探测 > 老路径**——三个时代（迁移前/双写期/清理后）都不用改宿主 cron；简单 fallback 在双写期会读到**陈旧的老文件** |
| `switch_node.py` | 计划写根目录 `:21` | 实际在 `tools/legacy/`（Phase 1 归档挪过），同 monitor_recover 处理 |
| `headless_relogin.py:27` | **计划遗漏** | ➕ DZMM_LOGIN_FILE —— 它是容器内密码重登链的读口，不改则 Step 2-5 撤老挂载后重登流程读不到账号密码 |
| `auto_relogin.py:24` | **计划遗漏** | ➕ 同上（macOS 遗留工具，一并改齐口径） |

### compose 变更（双写期）

`docker-compose.prod.yml` 两处：

- environment：`DZMM_TOKEN_FILE=/app/secrets/token.json`、`DZMM_LOGIN_FILE=/app/secrets/login.json`
  （都留 `${...:-}` 透传口，回滚 = 删这两行）；
- volumes：新增 `./secrets:/app/secrets` 目录挂载，老的单文件挂载**保留**（双写期供回滚）。

本地开发 compose（`docker-compose.yml`）**刻意不动**——它按隔离原则本就不挂凭据。

### 意外发现

- **§2-3A（token.emergency 路径不匹配）在本仓不成立**：`renew_token.py` 写
  `/app/token.emergency`（`ROOT` 相对），`bot.py:3357` 读同一个 `ROOT` —— 容器内读写自洽；
  `entrypoint.sh` 里没有宿主侧 watcher（它只轮询 `bot.paused`）。`PLATFORM-OPTIMIZATION.md §2-3A`
  描述的应是旧的手工部署布局。`renew_emergency_state.json` 生产 compose 已挂载。
  → Phase 2 连带问题清单**减一**（剩：多冷却叠加、`.session.lock` 路径，待 S1 收尾时一并处理）。
- 上一节"运行状态检查"（10:20–10:33）实测的 `原子替换失败（EBUSY）` 正是本阶段要修的问题，
  双写期部署后该日志应消失；**P0「持锁调子进程」（问题 E）不在本阶段范围**（屋主决定只记录）。

### 验证（全绿）

- 新增 `tests/test_token_path_env.py`：4 例（默认路径逐字一致 / token env 覆盖 / login env 覆盖 /
  monitor_recover env 优先 + 无 env 回退老路径）。单独跑 4/4 过。
- 全量 `pytest`：**收集 1488 = 1483 通过 + 5 跳过，0 失败**（本机 Windows 沙箱需
  `PYTHONDONTWRITEBYTECODE=1`，否则解释器写 stdlib `__pycache__` 会被沙箱拦成假失败）。
- 7 个改动文件 BOM 检查：**0 个 BOM**（吸取 Phase 1 教训，改完即查）。
- compose YAML 本机无法用 `docker compose config` 验证（本机无 docker，pyyaml 也没装）——
  改动只是两段局部插入、缩进对齐相邻条目；**留到部署机上 Step 2-3 前先跑
  `docker compose -f docker-compose.prod.yml config` 再迁移**。

### 本阶段改动文件清单

| 文件 | 改了什么 |
|---|---|
| `token_store.py` | `TOKEN_FILE` 支持 `DZMM_TOKEN_FILE` |
| `admin/dashboard.py` | `TOKEN_FILE` 同口径 |
| `monitor_recover.py` | `_resolve_token_file()`（env > secrets 探测 > 老路径） |
| `tools/legacy/switch_node.py` | 同上 |
| `headless_relogin.py` | `CONFIG` 支持 `DZMM_LOGIN_FILE` |
| `auto_relogin.py` | 同上 |
| `docker-compose.prod.yml` | secrets 挂载 + 两个 env（双写期） |
| `tests/test_token_path_env.py` | 新增，4 例 |
| `docs/EXECUTION-LOG.md` | 本节 |

### Step 2-3~2-5 执行记录（**2026-10-01 22:01 已施工 2-3 + 2-4**）

> ⚠️ 施工前先发现：**这份 compose 双写（Step 2-2）从未真正应用到服务器** ——
> 线上 `/opt/dzmm/docker-compose.yml` 与本地权威 `docker-compose.prod.yml` 的 md5
> 不一致，容器内 `DZMM_TOKEN_FILE`/`DZMM_LOGIN_FILE` 均未设置、`/app/secrets` 不存在，
> 21:45 的启动日志仍在报 `原子替换失败…'/app/.dzmm_token.json.tmp'`。
> 即"本地完成"被当成了"已生效"。所以本次是**补做 2-2 + 2-3 + 2-4**。

```bash
# 0) 先 diff 确认权威 compose 只比线上多 S1 那几行（不许夹带别的键）
#    实测：差异**只有** DZMM_TOKEN_FILE / DZMM_LOGIN_FILE / ./secrets:/app/secrets
#    + 两段 S1 注释。别的键（ADMIN_BIND/WEBHOOK_PORT/mem_limit…）线上线下一致。

# 1) 宿主机：建目录 + 迁移 + 收紧权限
mkdir -p /opt/dzmm/secrets
cp -p /opt/dzmm/.dzmm_token.json /opt/dzmm/secrets/token.json
cp -p /opt/dzmm/.dzmm_login.json /opt/dzmm/secrets/login.json
chown -R 501:games /opt/dzmm/secrets && chmod 700 /opt/dzmm/secrets
chmod 600 /opt/dzmm/secrets/*.json

# 2) 安装权威 compose 并**先校验再应用**
install -m 644 docker-compose.prod.yml /opt/dzmm/docker-compose.yml
cd /opt/dzmm && docker compose config   # rc=0，解析出 /app/secrets/token.json

# 3) 重建 + 重启（一次到位）
docker compose build dzmm-bot && docker compose up -d dzmm-bot

# 4) 验证（决定性的一条：直接测 os.replace 能不能原子写）
docker exec dzmm-bot python /tmp/s1.py       # → ATOMIC_REPLACE_OK bytes=3107
docker exec dzmm-bot findmnt -T /app/secrets # → 目录挂载
```

验证结果（2026-10-01 22:01）：

| 检查 | 结果 |
|---|---|
| 容器 env | `DZMM_TOKEN_FILE=/app/secrets/token.json`、`DZMM_LOGIN_FILE=/app/secrets/login.json` |
| 挂载形态 | `/app/secrets` = **目录**挂载；`/app/.dzmm_token.json` = **单文件**挂载 |
| `os.replace` 原子写 | **`ATOMIC_REPLACE_OK bytes=3107`**（此前该操作 100% EBUSY） |
| 容器内 `Errno.16` | 本轮 **0** 次 |
| 会话 | `probe=ok`、`bad_since=null` |

> 验证脚本刻意用**逐字节相同**的内容做 replace：即使中途出问题也不会损坏凭据，
> 而 `os.replace` 本身就是原子的。用完即删（容器内 + 宿主机 + 本地三处都清了）。
>
> **Step 2-5 仍未做**（按计划要 ≥24h 无续期错误）：届时删掉 compose 里
> `./.dzmm_token.json` / `./.dzmm_login.json` 两行老挂载，宿主机把老文件 `mv` 成 `.old` 留观。

以下为原计划命令（存档）：

```bash
# 0) 部署机上先验 compose 语法
cd /opt/dzmm && docker compose -f docker-compose.prod.yml config -q

# 1) 宿主机：建目录 + 迁移（cp 不 mv，双写期不删老文件）
mkdir -p secrets \
  && cp -a .dzmm_token.json secrets/token.json \
  && cp -a .dzmm_login.json secrets/login.json \
  && chown 501:games secrets/*.json && chmod 600 secrets/*.json

# 2) 部署新 compose 后观察至少一个续期周期（5 分钟）：
tail -f /opt/dzmm/renew_token.log        # 应无"原子替换失败"
ls -lh /opt/dzmm/.dzmm_token.json /opt/dzmm/secrets/token.json   # mtime 应接近
diff /opt/dzmm/.dzmm_token.json /opt/dzmm/secrets/token.json     # 应无输出

# 3) Step 2-5（≥15 分钟无错 + 24h 稳定后）：撤老挂载，老文件 mv 成 .old 留观
```

### 遗留

1. **0-5 在线验证 ②③④** —— 等真实 @ 流量；或跑 `tools/probes/probe_bd_ab.py` 离线验。
2. **Phase 2 Step 2-3~2-5** —— 需 SSH 施工窗口；收尾后 **≥24h 无续期错误**才进 Phase 3。
3. `tools/legacy/` 是 Phase 1 分类时实际新增的第五个目录（EXECUTION-LOG 当时只记了
   active/probes/archive/fixtures 四类），`tools/README.md` 未提及 —— 下次整理时补。

---

## 2026-10-01 · Phase 3 Step 3-1 —— 抽出 `_handle_joins` → runtime/joins.py（本地完成）

> 前置说明：Phase 3 名义上依赖 Phase 2 完成，但那指的是**部署顺序**（Phase 3 的代码
> 别在 S1 的 24h 观察窗内部署）。Step 3-1 是纯本地重构，与线上凭据迁移互不影响，
> 故先做。**本阶段改动同样未部署。**

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/joins.py`（~250 行） | `is_join_msg` / `detect_joins` / `add_pending_screen` / `notify_join_delayed` / `kick_and_notify` / `screen_members`（原 `_handle_joins`） / `room_greet_text` / `handle_join_event`（原 main_loop 46 行分派块）+ 筛查话术常量 + `CAPTCHA_JOIN_NOTICE`，全部**逐字原样**搬入 |
| bot.py 瘦身 | 4908 → **4745 行**（-163）；删 7 个函数定义 + 2 组常量；main_loop 入群块缩成 1 个故事态闸门 + 1 次调用 |
| bot.py 保留的接缝 | `_handle_joins` 变成 **6 行注入壳**（welcome_of / `_captcha_active` 是 bot 侧状态）；`_detect_joins` / `_is_join_msg` / `_room_greet_text` 改为同名 re-export，几十个调用点零改动 |
| 新增 `tests/test_runtime_joins.py` | 15 例：检测（去重/排除bot/措辞）、筛查六路（游客踢/游客放/男踢/女认证放/未认证提醒/无性别提醒）、撞码延迟（pending_screen 落库 + 飞书）、分派四支（筛查/筛查关/房间迎客+立绘/女鬼静默）、两个欢迎语闸门 |

### 结构决策（相对计划的偏离）

1. **`_handle_joins` 没有整个消失，留了注入壳** —— 计划的接口草图假设它能自包含，
   实际它依赖两个 bot 侧状态：`_welcome_of`（话术常量在 bot.py，`test_chat_humanize`
   在钉）与 `_captcha_active`（进程内全局，发送路径还有 2 处读点，搬旗标风险大）。
   壳只做参数注入，`monkeypatch bot._handle_joins` 的既有接缝（test_recovery_ownership）原样可用。
2. **两个函数被迫改名**（p8s4 自动守卫禁止 bot 与 runtime 定义同名函数）：
   `_send` → `_send_text`；`_feat_on` → `_gate` + `_feat_on = _gate` 赋值别名 ——
   后者是为了让 test_feature_registry 的 `_feat_on\\(` 静态扫描继续数到 joins.py 的接线
   （SCAN_FILES 本来就含 runtime/*.py，实测已自动收录）。
3. **`add_pending_screen` 改用 P8 单键读写**（group_get/group_set）替代原来的
   "读全量 → 改 → 存单群"，语义等价、竞态窗口更小 —— 这是搬家的顺手收益，不是行为变更。

### 意外发现（三个，都是既有防线在起作用）

1. **扫描器连 docstring 都咬**：`test_every_gate_uses_a_registered_key` 把 joins.py
   注释里的字面量 `_feat_on(cid, "key")` 当成了真实接线，报"未注册 key: 'key'"。
   改成 `_feat_on(群, "键")` 即可。
2. **棘轮测试立功**：`test_silent_errors` 发现 runtime/ 静默吞异常 49 > 45 —— 新增的
   4 处全是 bot.py **原样**搬来的既定静默（告警/待筛持久化辅助层）。按测试自己给的
   路径把 RUNTIME_SILENT_MAX 调到 49 并逐条写明理由；这 4 处在 bot.py 时棘轮看不见，
   搬进 runtime/ 反而被管起来了 —— 搬家本身提升了纪律覆盖率。
3. **源码断言测试共 3 处要跟着搬**（比预想少）：test_feature_registry 的入群块区域断言、
   test_chat_humanize 的欢迎语调用点计数、test_intro_photo 的立绘挂点计数 —— 全部改为
   bot.py + joins.py 两个文件一起数/分区段，断言意图不变。

### 验证（全绿）

- 全量 `pytest`：**收集 1503 = 1498 通过 + 5 跳过，0 失败**（+15 新用例）。
- 定向先跑：test_runtime_joins / test_p8s4_split（结构守卫）/ test_recovery_ownership
  （接缝）/ test_commands / test_feature_registry / test_chat_humanize / test_intro_photo /
  test_rose_weisi_switch —— 全过。
- 7 个改动文件 BOM 检查：0 个 BOM。
- 本机沙箱仍需 `PYTHONDONTWRITEBYTECODE=1`（同 Phase 2 记录）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/joins.py` | 新增（入群链路全家） |
| `bot.py` | 删 7 函数 + 2 组常量；加 re-export；main_loop 入群块 → 闸门 + 调用；`_handle_joins` → 注入壳；4908 → 4745 行 |
| `tests/test_runtime_joins.py` | 新增，15 例 |
| `tests/test_feature_registry.py` | 区域断言指到 joins.py（意图不变） |
| `tests/test_chat_humanize.py` | 欢迎语调用点计数跨两文件 |
| `tests/test_intro_photo.py` | 立绘挂点计数跨两文件 |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 45 → 49（4 处搬入的既定静默，已写明理由） |

### 遗留

1. **部署顺序**：本阶段与 Phase 2 的代码都在本地，部署时**先 S1（Phase 2）观察 24h，
   再上 Phase 3** —— 别把重构和凭据迁移混进同一个窗口。
2. Step 3-2（抽出 `_dispatch_slash`，~300 行）未开始 —— 按 EXECUTION-PLAN 每个 Step
   一周节奏，等 3-1 部署验证后再动。

---

## 2026-10-01 · Phase 3 Step 3-2 —— 抽出斜杠指令分派链 → runtime/dispatch.py（本地完成）

> 前置说明：延续 3-1 的口径 —— 纯本地重构，与线上凭据迁移互不影响；**改动未部署**。
> 这是 Phase 3 最大的一刀：bot.py 单块最大的代码（`main_loop` 里的 `if cmd:` 分派链，
> 约 394 行、30+ 个指令分支）整体搬出。搬迁纪律同 3-1：**逻辑逐字不变，只动"住在哪"**。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/dispatch.py`（1291 行，LF） | 42 个函数原序逐字搬入（`get_group_name` / `register_user` / 羞辱家族 / `continue_last` / `summarize_chat` / `_is_stop_word` 等）+ `HELP_TEXT` / `INTRO_TEXT` + `_DICE_ALWAYS_CMDS` + 5 个骰子 `_gate_*` + `_CMD_FEATURE_ORDER` + `_cmd_owned_by_disabled` + **`handle_slash`（分派链本体，排在文件最后一个函数）** |
| bot.py 瘦身 | 5276 → **4211 行**（-1065）；`if cmd:` 块（原 3726-4118）缩成"记 `old_last_id` + 一次 `await _dispatch.handle_slash(...)`" |
| 依赖注入 | `handle_slash` 签名带 6 个 bot 侧依赖（`state` / `_save_group` / `_mark_inflight` / `_remember_turn` / `rank_summary` / `sync_profiles`）—— 调用点读当前全局，既有 `monkeypatch bot.X` 接缝全部不受影响（已逐一核对相关测试均走 `F.set_overrides` / `state_store` 全局层，无 bot 属性 patch 驶入链内） |
| 同名 re-export | import 区 `from runtime.dispatch import (…46 个名字…)` —— bot 内调用点与 tests/tools 的 `bot._extract_xp`、`bot._CMD_FEATURE_ORDER` 等外部引用零改动；46 处 `# P3-3-2：…已搬 runtime/dispatch.py` 指针注释 |
| 更新 11 个源码钉住测试 | 链内锚点改读 dispatch.py（详见"意外发现"第 3 条） |

### 结构决策（相对计划的偏离）

1. **没用计划草图的 `_dispatch_slash(...) -> (reply, group_state, handled)` 同步纯函数** ——
   实际链是 async、沿途要真发消息（私聊骰子、飞书告警、@ 回复）并就地写 `group_state`；
   拆成"返回新 dict"会引入大量行为变更风险，违背逐字搬迁纪律。实际为
   `async def handle_slash(sender, cid, cmd, cmd_sender, dry, group_state, msgs, _cmd_msg, state, …)`，
   `group_state` 按引用更新，链体一字未改（仅缩进 24 → 4）。
2. **第二次被迫改名**（p8s4 自动守卫禁止 bot 与 runtime 定义同名函数）：bot.py 的
   `_active_cid` 在 dispatch 侧叫 `_current_cid` + `_active_cid = _current_cid` 赋值别名
   （3-1 的 `_gate` / `_feat_on` 先例第二例；函数体内的 `_active_cid()` 调用字面不变）。
3. **`old_last_id` 回滚协议不动**（P12-BC）：链首那行随链搬走时**删除**，改在调用方先记；
   生成失败由链内 `raise RuntimeError` 传到 main_loop 外层 except 回滚 —— 语义零变化。
4. **chain-to-EOF 约定**：`handle_slash` 必须是 dispatch.py 最后一个函数，源码钉住测试用
   `src[src.index("# 吹牛骰子命令（优先）"):]` 即得链体（原来靠 `elif at_msg:` 做右界，
   dispatch 里没有这个右界）。

### 意外发现（四个）

1. **死代码上船**：`_insult_material` / `_insult_target_uid` 两个函数在全仓**零调用**
   （bot.py / tests / tools 都没有）。按逐字搬迁纪律原样搬入 dispatch.py，将来单独清理。
2. **棘轮 +5 而非预估的 +6**：`RUNTIME_SILENT_MAX` 49 → 54。`_ph` /
   `_cmd_owned_by_disabled` 的 except 带 `# noqa: BLE001` 尾注，棘轮正则（要求冒号后
   紧跟换行）不匹配。实际新计 5 处：`_extract_xp` / `_live_insult_text` /
   `_record_insult` / `continue_last` / `summarize_chat` —— 全是 bot.py 原样搬来的
   既定静默，已在测试注释里逐条写明理由（同 3-1：搬进 runtime/ 反而被棘轮管起来了）。
3. **源码钉住测试跟 11 个文件**（3-1 只有 3 个）：15 例读 bot.py 找链内锚点的断言改读
   dispatch.py。两处特别：test_feature_registry 的链提取改为 chain-to-EOF、stop_word
   测试的 else 缩进字面量 24 空格 → 4 空格（链体去缩进 24→4，已实测首处 4 空格 else
   即兜底聊天分支）；test_inflight_recovery 的 `_mark_inflight` 计数改为跨文件合计
   （bot.py 2 处 + dispatch.py 1 处 = 3）。
4. **symtable 自检的正确姿势**：模块 scope 里 def/import 的名字 `is_global()` 也为 True
   （模块级即全局），判定"未定义名"要用 `is_local()` —— 生成脚本首跑全名单误报后修正。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1503 收集 = 1497 通过 + 6 跳过，0 失败，退出码 0**
  （3-1 时为 5 跳过；多出的 1 个为环境性跳过，非本步引入 —— 本步只改既有断言的源文件
  指向，未动任何 skip 逻辑）。
- 结构守卫 test_p8s4_split 抓到 `_active_cid` 双定义（改名前），修正后全绿 —— 守卫
  在这一步真实拦了一次事故。
- 一次性脚本（分析 / 生成 / 后处理）用完即删，未入仓；生成脚本依赖的 bot.py 锚点已删，
  本就不可重跑。
- 沙箱仍需 `PYTHONDONTWRITEBYTECODE=1`（同前）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/dispatch.py` | 新增，1291 行（42 函数 + 2 常量组 + 骰子闸门 + 归属表 + `handle_slash`） |
| `bot.py` | 删 ~1065 行；`if cmd:` 块 → `old_last_id` + 一次调用；46 名字 re-export + 指针注释 |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 49 → 54（5 处搬入的既定静默，已逐条写明理由） |
| `tests/test_chat_transcript.py` | 发言记录断言改 bot.py + dispatch.py 合并计数 |
| `tests/test_chat_decor.py` | spice_emoji 断言改读 dispatch.py |
| `tests/test_chat_humanize.py` | spice 断言改读 dispatch.py（`_humanize_chat` 计数仍在 bot.py 不变） |
| `tests/test_spice_and_idle.py` | 2 例源码段改读 dispatch.py |
| `tests/test_ruojin_persona.py` | 前缀接线断言改读 dispatch.py |
| `tests/test_fortune_tea.py` | trend 分支锚点改在 dispatch 提取（@ 分支断言不动） |
| `tests/test_tea_room.py` | `_cmd_msg` 断言改读 dispatch.py |
| `tests/test_feature_registry.py` | 链提取 ×3 改 chain-to-EOF；stop_word 缩进字面量 24→4 空格 |
| `tests/test_inflight_recovery.py` | `_cmd_msg` 标记改查 dispatch.py；计数改 bot 2 + dispatch 1 |
| `tests/test_intro_photo.py` | `reason="指令"` 改查 dispatch.py；send 计数跨三文件 |

### 遗留

1. **部署顺序**（同 3-1）：先 S1（Phase 2）观察 24h，再上 Phase 3 —— 别把重构和凭据
   迁移混进同一个窗口。
2. **死代码清理**：`_insult_material` / `_insult_target_uid`（dispatch.py 内零调用）——
   本步按逐字搬迁纪律未删，建议并入后续 Step 的清理清单。
3. Step 3-3~3-6 未开始（按每 Step 一周节奏推进）。

---

## 2026-10-01 · Phase 3 Step 3-3 —— 抽出房间消息处理 → runtime/room.py（本地完成）

> 前置说明：延续 3-1/3-2 的口径 —— 纯本地重构，**改动未部署**。搬的是 `main_loop`
> 里 `elif room_hit:` 整块（166 行，玫瑰房管家回应全链）。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/room.py`（287 行，LF） | `handle_room`（主链：女鬼退场 → 女鬼期间 → 客人指令婉拒 → 男生摘花放鬼 → 屋主命令女鬼 → 固定自我介绍 → 星座运势 → 兜底 AI 管家对话含调戏计数）+ 房间族小件 `ROOM_OPEN_CMDS` / `is_open_room_cmd` / `_room_male` / `_strike_get` / `_strike_set` + `_gate`/`_feat_on`（joins.py 同款赋值别名） |
| bot.py 瘦身 | 4211 → **4012 行**（-199）；`elif room_hit:` 块缩成"记 `old_last_id` + 一次 `await _room_mod.handle_room(...)`"；3 处定义改指针注释 |
| 依赖注入 | 4 个 bot 侧依赖（`state` / `_save_group` / `_mark_inflight` / `_remember_turn`）按参数注入 —— 同 `handle_slash` 模式，`group_state` 就地更新 |
| 同名 re-export | 搬走的 5 个名字 import 区同名 re-export：`bot._room_cmd_refused`（留 bot，唯一调用点在 cmd 路径）与 `test_open_room_cmd` 的 `bot.is_open_room_cmd` 调用点零改动 |

### 结构决策（相对计划的偏离）

1. **计划草图的同步返回元组接口再次没用** —— 同 3-2 的理由：块是 async、沿途真发消息
   （回复/立绘/布局图/女鬼图）、就地写 `group_state`。实际为
   `async def handle_room(sender, cid, room_hit, _room_obj, msgs, dry, group_state, state, …)`。
2. **`old_last_id` 回滚协议同 3-2**（P12-BC）：块首那行删掉，调用方先记；块内 raise
   传到 main_loop 外层 except 回滚，语义零变化。
3. **`is_open_room_cmd` 必须搬**：块内引用它，而 runtime 不许 import bot；
   `_room_cmd_refused` **留下**（唯一调用点在 main_loop 的 cmd 路径，且依赖 bot 级
   `ROOM_CIDS`）。其余房间族助手（`_room_owner`/`_is_flower_grab`/`_sign_reply`）本就
   住在 runtime/sign.py、常量在 personas.py —— 这块的依赖面天然比 3-2 干净得多。
4. `handle_room` 排文件最后（沿用 dispatch 的 chain-to-EOF 习惯，便于源码级测试提取）。

### 意外发现（三个）

1. **棘轮 +1（54 → 55）**：`_room_male` 里"实时查到性别后顺手写回档案"的静默 ——
   bot.py 时代不在 runtime 棘轮视野内，搬家后纳入管理（同前两步：搬家本身提升纪律
   覆盖率）。已在测试注释里写明理由。
2. **源码钉住测试 9 处 / 9 文件**（3-2 是 15 处 / 11 文件）。两处特别：
   test_ghost_photo 的「命令女鬼上场」锚点被 room.py **模块 docstring 抢先命中**
   （docstring 的链路清单里也有这几个字）→ 改从 `async def handle_room` 之后找；
   test_open_room_cmd 的接线断言拆成两半 —— bot.py 查闸门调用、room.py 查婉拒分支。
3. **全仓唯一调用点验证**：`_room_male` / `_strike_get` / `_strike_set` 的调用点全部
   在块内，随块搬走零残留；`is_open_room_cmd` 仅多 bot.py 一处内部调用（re-export 覆盖）。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1503 收集 = 1497 通过 + 6 跳过，0 失败，退出码 0**
  —— 与 3-2 基线完全一致。
- 施工脚本（tests/_tmp_make_room.py）内建自检：p8s4 同名预检、symtable 未定义名
  （模块 scope 跳过，3-2 的教训直接复用）、纯 CRLF 保持、`_mark_inflight` 三路计数。
  首跑仅一处锚点差一行的小修，第二跑通过；用完即删。
- 沙箱仍需 `PYTHONDONTWRITEBYTECODE=1`（同前）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/room.py` | 新增，287 行（`handle_room` + 5 个房间族小件 + `_gate`/`_feat_on`） |
| `bot.py` | 删 ~199 行；`elif room_hit:` 块 → `old_last_id` + 一次调用；5 名字 re-export + 3 处指针注释 |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 54 → 55（`_room_male` 搬入的既定静默，已写明理由） |
| `tests/test_chat_transcript.py` | 发言记录断言改 bot + dispatch + room 三边合并计数 |
| `tests/test_group_state_isolation.py` | `_save_group` 计数加 room.py（房间块含 5 处本群落盘） |
| `tests/test_intro_photo.py` | `maybe_send` 计数加 room.py；「你是谁」固定介绍断言改读 room.py |
| `tests/test_inflight_recovery.py` | `_room_obj` 标记改查 room.py；三路各恰一处合计 3 |
| `tests/test_rose_weisi_switch.py` | `room.reply_max_tokens` 源码断言改读 room.py |
| `tests/test_room_layout.py` | 布局图挂点断言改读 room.py |
| `tests/test_ghost_photo.py` | 女鬼图挂点 ×2 改读 room.py；锚点绕开 docstring |
| `tests/test_open_room_cmd.py` | 接线断言拆 bot.py（闸门）+ room.py（婉拒分支） |

### 遗留

1. **部署顺序**（同前）：先 S1（Phase 2）观察 24h，再上 Phase 3。
2. Step 3-4（抽出 `_handle_at_chat`，~200 行 @ 闲聊全流程）未开始。
3. 死代码 `_insult_material` / `_insult_target_uid`（3-2 遗留）仍在 dispatch.py，待清理。


## 2026-10-01 · Phase 3 Step 3-4 —— 抽出 `_handle_at_chat` → runtime/at_chat.py（本地完成）

> 前置说明：延续 3-1/3-2/3-3 的口径 —— 纯本地重构，**改动未部署**。搬的是 `main_loop`
> 里 `elif at_msg:` 整块（216 行，@ 闲聊全流程：打断保护 → 记忆请求 / 大白话 recap /
> 大白话运势 / 个人表现查询 / 普通问答 → 递话重写兜底 → 装饰排版 → 发送 → 冷却记账 →
> 立绘补发）。这是 main_loop 四大分支的最后一刀，bot.py 回到 4000 行以内。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/at_chat.py`（438 行，LF） | `handle_at_chat`（@ 闲聊主链）+ @ 闲聊族小件 12 个：`_ctx_mode` / `_ctx_only_uid` / `_pending_line` / `_FRAME_LEN_RE` / `_apply_frame_length` / `_ref_of` / `_CHAT_LAST_REPLY` / `_chat_cooling` / `_chat_mark_replied` / `_SOLICIT_REDO` / `_strip_at` / `_active_play_hint` + `_gate`/`_feat_on`（joins.py 同款赋值别名） |
| bot.py 瘦身 | 4012 → **3675 行**（-337）；`elif at_msg:` 块缩成"记 `old_last_id` + 一次 `await _at_chat.handle_at_chat(...)`"；8 条指针注释（覆盖 12 个小件名，族内合并计数） |
| 依赖注入 | 9 个 bot 侧依赖：数据 5（`state` / `group_state` / `msgs` / `_at_msg_obj` / `_via_quote`）+ 行为 5（`_save_group` / `_mark_inflight` / `_remember_turn` / `_recent_turns` / `_seed_turns`），kw-only 传参 |
| 同名 re-export | 搬走的 10 个名字 import 区同名 re-export + 指针注释：`_strip_at` / `_pending_line` / `_apply_frame_length` / `_ref_of` / `_CHAT_LAST_REPLY` / `_chat_cooling` / `_chat_mark_replied` / `_ctx_mode` / `_ctx_only_uid` / `_active_play_hint`，bot 内部调用点零改动 |

### 结构决策

1. **棘轮决策：`_TURNS` 族留在 bot.py** —— `_recent_turns` / `_seed_turns` /
   `_remember_turn` 挂在 `_TURNS` 进程内状态上，还被批量记账与 dispatch/room 注入共用，
   且 test_chat_transcript / test_ctx_bilateral 靠 `monkeypatch bot.X` 行为测试；
   搬走会破坏接缝。只按参数注入，`monkeypatch bot._recent_turns` 仍生效（注入的是
   main_loop 闭包里的名字引用）。
2. **`_no_decor` 归宿**：原来在 main_loop 每批开头置 False，@ 块内两条卡片路径再置
   True —— 搬成函数后在函数体首行补 `_no_decor = False`（@ 分支每批至多走一次，语义
   等价）；bot.py 里那行读者全空，按死赋值**删掉**（grep 全文仅剩这一处后才动手）。
3. **`old_last_id` 回滚协议同 3-2/3-3**（P12-BC）：块首那行删掉，调用方先记；块内
   `raise RuntimeError("生成失败(空回复): @消息")` 传 main_loop 外层 except 回滚。
4. **测试 monkeypatch 接缝改到 at_chat**：`_chat_cooling`/`_ctx_only_uid` 读的是
   at_chat 模块自己的 `_tune` / `OWNER_UID` —— patch `bot._tune` 不再生效，
   test_chat_guard / test_ctx_bilateral 改 patch `at_chat._tune` / `at_chat.OWNER_UID`。

### 施工方式与意外发现

1. **锚点脚本化手术**（tests/_tmp_make_at.py，一次性，用完即删）：AST 提取 @ 块
   （`elif at_msg:` 去 20 空格缩进 + elif→if 包 def 再解析）→ 依赖盘点（symtable
   全量 Load 名字 → 模块级 vs main_loop 局部，精确得出 38 个模块级依赖）→ 生成
   at_chat.py → bot.py 自底向上 10 处编辑 → 自检 10 项（同名 def / 禁 import bot /
   未定义名 / CRLF 纯度 / ast.parse / import 冒烟 / re-export 同对象）。
   锚点失配 3 处全是**中文弯引号 vs ASCII 直引号**与多行字面量首行截断，逐字修正。
2. **自检抓到 1 个真问题**：at_chat.py 未定义名 `_remember`（块内记忆请求路径调用，
   盘点时漏了）→ imports 补 `from runtime.group_state import … _remember`。
   这正是 test_undefined_names 防的那类事故（P12-BB），脚本内静态检查先行拦下。
3. **源码钉住测试 14 文件 20 处**（3-3 是 9 文件）—— @ 分支是功能最密的一条路。
   典型改法：加 `SRC_A = …at_chat.py`、断言改读 SRC_A、monkeypatch 目标改 at_chat、
   多边计数改四边（bot+dispatch+room+at_chat）、字面量缩进 36→24 空格
   （test_tea_room）、`test_bot_source_has_no_prompt_text_duplication` 改双文件检查。
4. **bot.py 里 `_no_decor` 只剩 1 处**（每批初始化），`_ensure_decor(reply` 零残留；
   `if not _no_decor:` 与 P12-AH 装饰段整体在 at_chat.py —— test_chat_decor /
   test_fortune_tea 的锚点随之迁移。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1503 收集 = 1497 通过 + 6 跳过，0 失败**
  —— 与 3-2/3-3 基线完全一致。
- 施工脚本内建自检全过：bot.py 3675 行纯 CRLF（含事后删除 `_no_decor` 死赋值）、
  at_chat.py 438 行 LF、re-export 10 项与 at_chat 对应名字 `is` 同对象。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/at_chat.py` | 新增，438 行（`handle_at_chat` 主链 + 12 个 @ 闲聊族小件 + `_gate`/`_feat_on`） |
| `bot.py` | 删 ~336 行；`elif at_msg:` 块 → `old_last_id` + 一次调用；10 名字 re-export + 8 条指针注释；删 `_no_decor` 死赋值 |
| `tests/test_chat_humanize.py` | frame_length / 接线 / 卡片不碰 三处断言改读 at_chat.py |
| `tests/test_chat_transcript.py` | 发言记录断言改 bot + dispatch + room + at_chat 四边合并计数 |
| `tests/test_intro_photo.py` | `maybe_send` 计数加 at_chat.py；"@提问" reason 断言改读 at_chat.py |
| `tests/test_inflight_recovery.py` | `_at_msg_obj` 标记改查 at_chat.py；三 runtime 文件各恰一处 + bot.py 计数 0 |
| `tests/test_quote_reply.py` | 被引用原文进上下文断言改读 at_chat.py |
| `tests/test_chat_guard.py` | 冷却/递话接线锚点改读 at_chat.py；`_tune` 改 patch `at_chat._tune` |
| `tests/test_chat_context.py` | @ 分支读话术与转录断言改读 at_chat.py |
| `tests/test_ctx_bilateral.py` | 双边接线/记忆过滤锚点改读 at_chat.py；`_tune`/`OWNER_UID` 改 patch at_chat |
| `tests/test_at_focus.py` | `_bot_src()` 改读 at_chat.py；提示词不重复检查改双文件 |
| `tests/test_play_busy_phrases.py` | 硬编码扫描改双文件；`_active_play_hint` 锚点改读 at_chat.py |
| `tests/test_chat_decor.py` | P12-AH 装饰段锚点改读 at_chat.py |
| `tests/test_loop_blocking.py` | `_retrieve_memory` to_thread 断言迁到 at_chat.py（bot.py 保留高潮生成段） |
| `tests/test_tea_room.py` | `recap_absent` 传参字面量改读 at_chat.py（缩进 36→24 空格） |
| `tests/test_fortune_tea.py` | @ 分支运势入口 / `_no_decor` 守卫锚点改读 at_chat.py |

### 遗留

1. **部署顺序**（同前）：先 S1（Phase 2）观察 24h，再上 Phase 3。
2. Step 3-5（brains.py 提示词外置到 prompts/）未开始。
3. 死代码 `_insult_material` / `_insult_target_uid`（3-2 遗留）仍在 dispatch.py，待清理。

## 2026-10-01 · Phase 3 Step 3-5 —— brains.py 提示词出厂默认值搬进 prompt_defaults.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。计划书的字面目标是"brains.py 不再传默认值"，
> 但盘点发现它与 P8 已建成的「覆盖，不是搬家」安全设计直接冲突（见下），经屋主拍板走
> **方案 C**：默认值搬进纯数据模块，安全网语义一个不动。

### 盘点结论（计划书"当前状态"已过时）

- 计划书写的现状是"部分提示词已有 prompts/KEY.md，但未完全覆盖"；实测 **40/40 全部
  有 .md 且与代码默认值逐字节一致**（AST 逐字比对）——外置本体 P8/P12-G 早已完成，
  本步的真实缺口只有"brains.py 瘦身"。
- 字面目标（删默认值）会破坏 6 处架构依赖：`runtime/prompt_tiers.py:369` 的生产回退
  （`RP.load(key, RP.default_of(key))`）、`admin/dashboard.py` 的"已编辑"判定、
  `admin/prompts_ui.py`、`tools/active/deploy_gate.py` 部署闸门、`DZMM_PROMPTS=off`
  秒退开关语义（会变成"全部清空"）、`tests/test_prompt_tiers.py:85` 的"默认值必须
  非空"棘轮。且 runtime/prompts.py 设计文档明写「文件没了 → prompt 变空 → LLM 乱答」
  是这个设计要防的头号事故。

### 做了什么（方案 C）

| 动作 | 内容 |
|---|---|
| 新增 `prompt_defaults.py`（737 行，LF，纯数据） | `PD` 字典装 40 个出厂默认值，**逐字节**取自 brains.py 原字面量源文（`ast.get_source_segment` 切片，不做任何重排）；模块不许 import 任何东西 |
| brains.py 瘦身 | 1470 → **795 行**（-675）；40 个多行赋值 → 一行 `NAME = _p("NAME", PD["NAME"])`；import 区加 `from prompt_defaults import PD`；`_p` loader / `generate_*()` / 其余逻辑零改动 |
| 安全网语义 | **一个不动**：文件优先 + 缺失/空/读失败退回 PD 默认值、`DZMM_PROMPTS=off`、mtime 缓存、热加载 `refresh()` 写回 brains、registry 的 default 即 PD 值 |
| 钉住测试 | test_prompts.py `_assignments()` 改读 `PD["KEY"]` 形式（AST 钉住"出厂值只许住 PD，不许把字面量搬回来"）+ 新增 `test_pd_module_covers_exactly_brains_prompts`（PD 键与 40 常量一一对应）；原 `test_every_prompt_file_matches_code_default`（41 文件 vs registry default）连带钉住数据模块 |

### 施工方式与意外发现

1. **一次性脚本**（tests/_tmp_make_pd.py）：收集 40 个 `_p` 赋值 → 逐个断言与 .md
   逐字节一致（不一致即中止）→ 生成 PD 模块 → brains.py 自底向上收缩 40 段 →
   自检（赋值形状 / 无裸字面量 / LF 纯度 / ast.parse / 子进程 import 冒烟）。
2. **两个假设当场被纠**：① 以为 brains.py 是 CRLF（沿用 bot.py 纪律），实测纯 LF
   ——脚本首跑前 assert 拦下；② 冒烟第一跑 registry=40，漏了 `import runtime.memory`
   （第 41 个是 import 时才登记的，test_prompts.py 里也是显式 import）——脚本Bug，
   单独补跑通过。
3. **顺手修陈旧注释**：runtime/prompts.py 模块 docstring 还指着 P12-G 之前的
   `brains._PROMPT_DEFAULTS`（早已搬进中央登记册），更新为现状 + P3-5 去向。
4. brains.py 收缩后 795 行而非预期的 ~180 —— 余下全是 `generate_*()` 与逻辑体，
   prompt 文本实际占 ~675 行。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1504 收集 = 1498 通过 + 6 跳过，0 失败**
  —— 较 3-4 基线净增 1（新增 PD 覆盖钉测试），其余全数保持。
- 子进程 import 冒烟：40 个常量 == 磁盘 .md == PD 值；registry 41；模块归属正确。
- brains.py 保持纯 LF（1470 个 LF / 0 个 CR）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `prompt_defaults.py` | 新增，737 行（`PD` 字典：40 个出厂默认值，纯数据） |
| `brains.py` | 1470 → 795 行；40 个多行字面量赋值 → `PD["KEY"]` 一行式 + import |
| `tests/test_prompts.py` | `_assignments()` 改读 PD 形式 + 断言"不许写回字面量"；新增 PD 覆盖钉；docstring 更新 |
| `runtime/prompts.py` | docstring 陈旧引用（`brains._PROMPT_DEFAULTS`）更新为现状 |

### 遗留

1. **部署顺序**（同前）：先 S1（Phase 2）观察 24h，再上 Phase 3。**部署时务必带上
   `prompt_defaults.py`**（brains 顶层 import 它，漏了就是 ImportError）。
2. Step 3-6（personas.py 人设分离）未开始。
3. 死代码 `_insult_material` / `_insult_target_uid`（3-2 遗留）仍在 dispatch.py，待清理。

## 2026-10-01 · Phase 3 Step 3-6 —— personas.py 拆成 personas/ 包（本地完成）

> 前置说明：纯本地重构，**改动未部署**。搬的是 personas.py（1179 行，纯 LF）：
> 三个人设（nuo / rose_butler / ruojin）的人设正文、话术、房间文案、星座数据，
> 加上人设选择 / 覆盖层 / fill/p 等全部函数。Phase 3 最后一步。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `personas/` 包（5 文件，全 LF） | `__init__.py`（429 行：全部函数 + 状态 + contextvars）+ `nuo.py`（84：默认名 + 默认话术）+ `rose_butler.py`（315：薇丝 extra + 全部房间文案 ROOM_* + 星座数据 ROSE_* + 玫瑰房话术）+ `ruojin.py`（245：若瑾 extra/口头零件/话术）+ `registry.py`（152：PERSONAS/ROOMS/CID_PERSONA/PHRASES/PHRASE_LABELS 汇总） |
| 删除 `personas.py` | 与包同名不能共存；import 面（60+ 处 `from personas import X`）靠 `__init__` 按原名 re-import 全部兼容，**调用点零改动** |
| 新增 `tests/test_personas_split.py` | 14 条守卫：包结构 / 接口与 hotload 面 / 数据逐字（is 同对象 + 金丝雀文案）/ 纯数据纪律（nuo/rose_butler/ruojin 零 import）/ monkeypatch 接缝活着 |
| 更新 `tests/test_undefined_names.py` | TARGETS 的 `personas.py` → 包内 5 文件 |

### 结构决策（相对计划书的两处偏离，均为保接缝）

1. **函数与状态全部留在 `__init__.py`，没有 core.py** —— 盘点发现 9 个测试文件
   （chat_guard / chat_decor / chat_humanize / orientation_filter / play_busy_phrases /
   spice_and_idle / ruojin_persona 等）靠 `monkeypatch.setattr(P, "_CONFIG_PERSONA", …)`
   patch **包属性**；函数若搬去 core.py，读的是 core 自己的全局，patch 全部失效
   （P3-4 `_TURNS` 族同款教训）。数据与逻辑分层照旧成立：数据在子模块，逻辑在包口。
2. **没有 weisi.py、没有 defaults.py** —— 实际人设 key 是 `rose_butler`（薇丝是名字
   不是 key），按 key 命名；nuo 的 extra 为空、默认话术就是 nuo 的话术，defaults.py
   与 nuo.py 合一。另外计划书没预见的 **170 行房间文案 + 星座数据**（ROOM_*/ROSE_*，
   joins/room/sign 三个 runtime 模块在用）随玫瑰房进 rose_butler.py。

### 施工方式与意外发现

1. **一次性脚本**（tests/_tmp_split_personas.py）：68 个行号锚点逐个断言 → 数据块
   逐字切片 → 组装 5 文件 → 自检（ast.parse / LF 纯度 / 未定义名扫描）+ **等价探针**：
   before（旧模块按文件路径加载）vs after（新包）在两个子进程里跑同一份行为脚本 ——
   81 个公开名全覆盖、46 个数据名 repr 逐字节相等、25 项行为（选人设/覆盖层/fill/p/
   health/hotload 接口/**monkeypatch 接缝模拟**）逐项相等。
2. **探针自身踩了两个坑（都被 assert 拦住）**：① 脚本重跑时父进程 `import personas`
   拿到的是已落盘的包 —— 旧模块改用 `importlib.util.spec_from_file_location` 显式按
   路径加载；② `ROOMS["owners"]` 是 **set**，repr 跨进程乱序（PYTHONHASHSEED 随机）
   造成假性"数据不一致" —— 两个探针子进程统一 `PYTHONHASHSEED=0` 后真值相等。
   同进程单跑 diff 永远测不出这个（哈希种子相同）—— 跨进程比对必须固定种子。
3. **首次落盘即被自检拦下 1 个漏 re-export**：`ROSE_BUTLER_EXTRA` 没进 `__init__`
   的 import 块（公开名全覆盖校验报缺），补上后才落第二版 —— 机制起了作用。
4. **改前盘点确认 0 处源码级钉住 personas.py 正文**（test_rose_weisi_switch 的
   SCAN_FILES 只扫 bot+runtime；源码级引用只有 test_undefined_names 的 TARGETS），
   所以本步测试改动量是 3-1 以来最小（1 个 TARGETS + 1 个新文件）。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1522 收集 = 1516 通过 + 6 跳过，0 失败**
  —— 较 3-5 基线净增 18（+14 新守卫、TARGETS 拆出 +4 条目），其余全数保持。
- 等价探针：81 公开名 / 46 数据名 / 25 行为项全部相等（含接缝模拟）。
- 删除 personas.py 后二次 import 冒烟通过（消除"包与模块同名"的过渡态）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `personas/__init__.py` | 新增，429 行（docstring + 状态/contextvars + 全部函数 + 数据 re-import；函数留此的理由写在 INIT_NOTE 注释里） |
| `personas/nuo.py` / `rose_butler.py` / `ruojin.py` / `registry.py` | 新增，纯数据模块（84/315/245/152 行，逐字切片） |
| `personas.py` | **删除** |
| `tests/test_personas_split.py` | 新增，14 条守卫 |
| `tests/test_undefined_names.py` | TARGETS：personas.py → 包内 5 文件 |

### 遗留（Phase 3 收官盘点）

1. **部署顺序**（同前）：先 S1（Phase 2）观察 24h，再上 Phase 3。部署务必带齐：
   runtime/ 4 个新模块 + prompt_defaults.py + **personas/ 包（并删除线上 personas.py，
   否则同名共存行为未定义）**。
2. **验收清单第 1 条过时**：计划写「bot.py < 1500」，六步拆完实际停在 **3675 行**
   （3-5/3-6 不动 bot.py）； brains 795 / personas 包 1225。是否追加 3-7+ 继续拆
   main_loop 本体，待屋主决策。
3. 死代码 `_insult_material` / `_insult_target_uid`（3-2 遗留）仍在 dispatch.py，待清理。

## 2026-10-01 · Phase 3 Step 3-7 —— 抽出 Sender 发送引擎 → runtime/sender.py（本地完成）+ dispatch 死代码清理

> 前置说明：纯本地重构，**改动未部署**。本步是计划外的追加步（屋主批准）：3-6 收官时
> 确认「bot.py < 1500」的验收线达不到，追加 3-7+ 继续拆。本刀取最大的内聚单块
> —— `class Sender`（588 行）及其账号通道族。

### 顺带：dispatch.py 死代码清理（3-2 遗留清零）

`_insult_material` / `_insult_target_uid` 全仓零调用点（只有 bot.py 的 re-export 与
指针注释）→ 从 dispatch.py 删除 30 行，bot.py import 行同步收窄，指针注释合并。
3-2 的遗留事项清零。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/sender.py`（723 行，LF） | `class Sender`（Playwright 账号通道发送：打字 + Socket.IO 长消息 + 读回验证 + 回退切分 + token 收割/刷新）+ `get_cached_cookie`/`_cookie_cache` + `_split_for_account` + `ACCOUNT_MSG_MAX_CHARS`/`PAGE_CACHE_MAX`/`PAGE_MAX_AGE_SEC`/`SIO_LONG_*`/`TOKEN_HARVEST_SEC` + `_gate`/`_feat_on`（同款别名） |
| bot.py 瘦身 | 3674 → **3019 行**（-655）；import 区同名 re-export（`bot.Sender` 与真身是**同一个类对象**，`monkeypatch.setattr(bot.Sender, "方法", …)` 类属性 patch 不受搬家影响） |
| runtime/ctx.py | 新增 `_captcha_active` 真源 + `captcha_active()`/`set_captcha_active()`（撞验证码旗标是**进程级**，不是任务局部） |

### 共享状态归宿（本步三个关键决策）

1. **`_LIVE_SIO`**：真身搬 sender.py；bot `main()` 唯一写点改为
   `_sender_mod._LIVE_SIO = subscriber`（模块引用写）；bot 侧**不留镜像全局**
   （test 钉住 "global _LIVE_SIO not in bot.py"）。
2. **`_captcha_active`**：真源进 runtime/ctx.py；main_loop 3 个写点 →
   `_ctx.set_captcha_active(...)`；Sender 2 个读点 → `_ctx.captcha_active()`；
   joins 的注入 lambda 同步改读 ctx。bot 侧零残留（词边界断言钉住）。
3. **`GROUPS`**：`_page_for` 的页缓存分组键改**构造注入**（`groups=GROUPS`，
   bot 3 个构造点传入）；类级默认空集 = `__new__` 出来的测试实例全按 scratch 页。

### 施工方式与意外发现

1. **接缝成本前置评分**：候选四族（Sender 588 / main_loop 711 / 后台任务 ~355 /
   档案同步 ~291）按"测试引用文件数 + 方法内 bot 全局依赖数"评分。Sender 虽有
   `asyncio` 整模块替换、`_LIVE_SIO`×4 等 ~15 处 patch，但类属性 patch（
   `setattr(bot.Sender, "方法")`）天然免疫搬家 —— 比想象的干净。
2. **一次性脚本**（tests/_tmp_make_sender.py）：68 个行号锚点 → 数据块切片 →
   bot 手术（4 段删除 + 9 处改写）→ 子进程等价冒烟 + 未定义名扫描。
3. **脚本调试的教训（+1 条项目纪律）**：`sub1()` 助手返回了**字符串**而非 list，
   赋回 `keep` 后所有子串搜索悄悄失效（`"\n".join(字符串)` 按字符拼接）——报错
   "count=0" 与直觉完全相反，靠"逐步打印各阶段实际内容"定位。**手术脚本里跨阶段
   传递容器时，类型必须一致**；断言失败信息与根因相距两层时，先打印中间态。
4. **冒烟断言两处修正**：`_split_for_account` 跨段边界会吃掉换行（原实现语义，
   测试假设错）；`_page_for` 是 async 且依赖浏览器页 —— 冒烟只验数据面
   （`_known_groups` 注入），行为面由 test_account_long_socket 覆盖。
5. **RUNTIME_SILENT_MAX 55 → 65**：Sender 类搬入 runtime 扫描视野，+10 处既定
   静默（token 抄回/刷新、页面重载、长消息重试、读回验证 —— 全部自带日志或明确
   返回 False，失败不影响发送主链），理由已写进测试注释。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1523 收集 = 1517 通过 + 6 跳过，0 失败**
  —— 较 3-6 基线净增 1（TARGETS 新增 runtime/sender.py 条目）。
- 子进程冒烟：re-export 同一性 / 切分等价 / ctx 旗标 / `_known_groups` 注入全过。
- 未定义名扫描：bot.py / runtime/sender.py / ctx.py 全零。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/sender.py` | 新增，723 行（Sender 类 + 账号通道族 + 6 常量 + `_gate`/`_feat_on`） |
| `runtime/ctx.py` | +26 行（`_captcha_active` 真源 + 读写函数） |
| `bot.py` | 3674 → 3019 行；4 段删除 + import 区 re-export + 写点/构造点改写 |
| `runtime/dispatch.py` | 删死代码 `_insult_material`/`_insult_target_uid`（30 行） |
| `tests/test_account_long_socket.py` | 11 处 patch 目标 bot.→S.；source-pin ×4 改读 sender.py |
| `tests/test_account_msg_limit.py` | 1 处 patch + source-pin ×2 改读 sender.py |
| `tests/test_token_harvest.py` | 2 处 log patch 改 S.；source-pin 改读 sender.py |
| `tests/test_renew_backoff_escalation.py` | `_harvest_token` 锚点改读 sender.py |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 55 → 65（+10 理由入注） |
| `tests/test_undefined_names.py` | TARGETS + runtime/sender.py |

### 遗留

1. **bot.py 3019 行**，距 <1500 还差 ~1500：下一刀候选 = main_loop 本体（711，
   注入面最大）或后台任务族（roast/push/scan ~355，接缝最少）。待屋主定顺序。
2. **部署清单更新**：Phase 3 上线需带 runtime/ 5 个新模块（joins/dispatch/room/
   at_chat/sender）+ prompt_defaults.py + personas/ 包（删线上 personas.py）+
   runtime/ctx.py 增量；仍按"先 S1 观察 24h"。
3. 3-2 死代码遗留已清零；其余遗留同前。

## 2026-10-01 · Phase 3 Step 3-8 —— 恶魔吐槽族 → runtime/roast.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。追加步"后台任务族先易后难"的第一刀：
> 选**测试引用数为 0** 的 roast 双函数（132 行），行为测试零改动。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/roast.py`（171 行，LF） | `_maybe_devil_roast`（热闹/敏感词触发，只改内存 group_state）+ `_maybe_devil_cold_roast`（冷场主动毒舌，自包含 load/save）+ 4 个 roast 自有常量（`DEVIL_DAILY_MAX`/`DEVIL_HOT_MIN_SPEAKERS`/`DEVIL_SENSITIVE_WORDS`/`DEVIL_HOT_WINDOW`） |
| bot.py 瘦身 | 3019 → **2882 行**（-137）；import 区同名 re-export，hot roast 调用点零改动 |
| 接缝 | cold roast 的 `load_state`/`_save_group` → kw 注入（main_loop 调用点传真身名字，`monkeypatch bot.load_state` 接缝不变）；调用点 2400 一处加传参 |
| 概率读取 | 概率/阈值全部走 `_tune`（P12-0 读口唯一），搬家不碰参数层 |

### 常量去留决策（受 test_tuning 双清单约束）

- **搬走** 4 个：仅 roast 函数消费、不在 watch（禁 bot 读）清单、不在
  `bot.DEVIL_*` 对照清单 —— 纯 roast 自有。
- **留下** 8 个（BASE_PROB/SENSITIVE_PROB/HOT_PROB/COOLDOWN/COLD_*）：test_tuning
  钉 `bot.DEVIL_*` 与 tuning 默认值对照，且 watch 禁 bot 消费点直读 ——
  本搬后 bot.py 对它们的读点**清零**（原先唯一消费者就是 roast 函数），
  反而让 `test_no_consumption_site_reads_the_old_constants` 更干净。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1524 收集 = 1518 通过 + 6 跳过，0 失败**
  —— 较 3-7 基线净增 1（TARGETS 新增 runtime/roast.py）。
- 行为冒烟（密封：patch random/generate/_tune/_get_*_state，不打 AI）：
  开关关→空串 / 无上下文→空串 / 深夜静默→空串 / roll 不中→写回 devil_cold_last
  （注入的 _save_group 被调）/ roll 中→出文案且 daily_count+1。
- RUNTIME_SILENT_MAX 65 → 66（cold roast 的生成失败静默：返回空串+写回重试周期，
  "静默的是这轮不吐，不是错误状态"——理由入注）。
- 未定义名扫描：bot.py / runtime/roast.py 全零。

### 意外发现

1. **冒烟初次被真实状态库拦住**：互斥检查 `_get_*_state("c1")` 读真实 store，
   未密封时行为不确定 —— 冒烟对"读环境"的依赖必须显式 patch（同 3-7 的
   PYTHONHASHSEED 教训：测试密封性要主动做，不能赌环境干净）。
2. bot.py 里的 `# 恶魔吐槽参数` 注释块随 4 常量搬走部分行后，留下 8 个常量与
   原注释 —— 注释语义仍成立（它们确实是"参数层对照物"），未额外改写。

### 遗留（后台任务族剩余）

1. 下一刀候选（按接缝从易到难）：`_log_runtime_status`(78) + `_reconcile_members`/
   `_rescreen_pending`(60) → 档案/名册族；`_scheduled_pushes`(60，test_feature_registry
   5 处 patch 需迁) + `_maybe_*` 已清；`_daily_profile_scan`(85)/`sync_profiles`(83)。
2. bot.py **2882 行**；距 <1500 还差 ~1400（大头仍是 main_loop 711 + Sender 已清）。
3. 部署清单：runtime/ 新模块累计 6 个（+roast）；其余同 3-7。

## 2026-10-01 · Phase 3 Step 3-9 —— 高潮结算族 → runtime/climax.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。后台任务族第二刀：高潮/发情值结算 6 函数
> （155 行，AST 定位切片——免除 3-7/3-8 踩过的行号漂移问题）。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/climax.py`（192 行，LF） | `_climax_hits`（满 100 判定+扣减）/ `_parse_judge`（结算单 JSON 解析）/ `_judge_pending`（待评拉取+AI 评定）/ `_build_climax_context`/`_build_climax_texts`（高潮正文）/ `_publish_memory_report`（记忆汇报） |
| bot.py 瘦身 | 2882 → **2731 行**（-151）；import 区同名 re-export；`_handle_climax`（发送循环）与 `asyncio.to_thread(_build_climax_texts, …)` 调用点留在 bot.py，test_loop_blocking 的源码钉点零改动 |
| 接缝 | `_judge_pending` 的 `load_state`/`_save_group` → kw 注入（调用点 2229 传参）；`_active_cid()` → 直读 `runtime/ctx` 真源（bot 镜像回退在按群并行下本就不可靠，语义等价，docstring 已注明） |

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1525 收集 = 1519 通过 + 6 跳过，0 失败**
  —— 较 3-8 基线净增 1（TARGETS 新增 runtime/climax.py）。
- 行为冒烟（密封）：`_parse_judge` 四例（纯 JSON/代码围栏/字符串 delta/垃圾输入）/
  `_climax_hits` 阈值触发+扣减+事件计数 / `_judge_pending` 注入接缝+写回
  （heat 应用、heat_pending 清空、heat_last_judge 落值）/ re-export 同一性 ×6。
- RUNTIME_SILENT_MAX 66 → 67（`_judge_pending` 的 AI 评定失败按空结果处理——
  heat 不动、写回判定时间，下个 2 小时周期重试；理由入注）。

### 意外发现

1. **冒烟三连修**：状态形状假设错了两次（`heat` vs 真实键 `heat_value`/`heat_climax`；
   `_judge_pending` 读 `heat_pending` 且返回**原始评定文本**而非格式化结果）——
   冒烟前先读实现再写断言，别凭依赖清单猜形状。
2. `test_group_state_isolation` 的 `_save_group(cid,` 计数断言（bot+room ≥20）——
   3-8/3-9 把部分调用点搬进 roast/climax，计数范围扩展（运行时模块合并计数），
   这类"跨文件合计断言"每搬一刀都要复核清单。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/climax.py` | 新增，192 行（6 函数 + ctx 真源读点） |
| `bot.py` | 2882 → 2731 行；2 段删除 + import 区 re-export + 调用点 2229 注入传参 |
| `tests/test_group_state_isolation.py` | `_save_group` 计数范围扩展（+roast/climax） |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 66 → 67 |
| `tests/test_undefined_names.py` | TARGETS + runtime/climax.py |

### 遗留（后台任务族剩余）

1. 下一刀候选：`_log_runtime_status`(78) + `_reconcile_members`/`_rescreen_pending`
   (60)；`_scheduled_pushes`(60，test_feature_registry 5 处 patch 需迁)；
   `_daily_profile_scan`(85)/`sync_profiles`(83)/`rank_summary`(39)。
2. bot.py **2731 行**；距 <1500 还差 ~1230（大头 main_loop ~700）。
3. 部署清单：runtime/ 新模块累计 7 个（+climax）；其余同前。

## 2026-10-01 · Phase 3 Step 3-10 —— 启动自证 + 成员名册族 → runtime/status.py / roster.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。后台任务族第三刀：`_log_runtime_status`
> (78 行) + 名册族 `_reconcile_members`(45)/`_rescreen_pending`(15)，AST 定位切片。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/status.py`（96 行，LF） | `log_runtime_status`（P5/P7/P8/P11/P12 启动自证行）。**纯只读**：群集合/配置对象/各注册表（features/tuning/personas/bot_uids/config_personas）全部 kw-only 注入；session_guard/throttle_guard/state_store/runtime.prompts 保持**函数内 import**（缺哪层 try/except 才能兜住并如实打印） |
| 新增 `runtime/roster.py`（94 行，LF） | `reconcile_members`（成员快照对账：首跑只建快照、差集筛查、快照只增不减）/ `rescreen_pending`（风控解除后补筛 pending_screen）+ `KNOWN_MEMBERS_KEY` 真身。筛查链/发送/欢迎话术/昵称/群状态读写全部 kw-only 注入 |
| bot.py 瘦身 | 2731 → **2625 行**（-106）；三个函数改**注入壳**；import 区 +`_status`/`_roster`；`KNOWN_MEMBERS_KEY` re-export 保 `bot.KNOWN_MEMBERS_KEY` 接缝（test_recovery_ownership） |
| 不动的 | `P12: 热加载=` 自证行仍在 bot.py（属热加载块非 status 族）；`_handle_joins` 注入壳留在 bot.py（welcome_of/captcha_active 是 bot 侧状态） |

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1527 收集 = 1521 通过 + 6 跳过，0 失败**
  —— 较 3-9 基线净增 2（TARGETS 新增 runtime/status.py、runtime/roster.py）。
- 施工脚本自检：三文件 AST 解析 + 纯 LF + `undefined_from_source` 零未定义名
  （新模块 import 面在写入前静态验证）。
- RUNTIME_SILENT_MAX **保持 67**：新模块 except 首行全是 log，无新增静默；
  `_save_group` 计数断言范围扩展（+roster，按 `save_group(cid,` 计入 1 处）。

### 意外发现

1. **tests/__pycache__ 陈旧 pyc 事故**：`test_hotload` 首跑失败，traceback 路径竟指向
   `..\dzmm_botapi\tests\test_hotload.py` —— 该测试文件当初从 dzmm_botapi 整份同步
   （两份 MD5 相同），`tests/__pycache__/test_hotload.cpython-314-pytest-9.1.1.pyc`
   （9/26）的 co_filename 还带着来源路径，pytest 按 mtime/size 校验通过直接复用旧码。
   已删缓存。教训：**从别的项目拷来的测试文件，施工后清一次 `tests/__pycache__`**；
   fsutil hardlink list 可快速排除"硬链接共享文件"的疑虑。
2. test_hotload 面板自证钉点（P11/P12 字面量必须在启动日志）：五个字面量已随
   status.py 搬走，钉点改为 bot.py + runtime/status.py 合并源码查（`P12: 热加载=`
   仍在 bot.py，一个判据覆盖两文件）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/status.py` | 新增，96 行（log_runtime_status + 注入面） |
| `runtime/roster.py` | 新增，94 行（reconcile/rescreen + KNOWN_MEMBERS_KEY 真身） |
| `bot.py` | 2731 → 2625 行；3 段函数改注入壳 + import 区 2 行 + KNOWN_MEMBERS_KEY re-export |
| `tests/test_hotload.py` | 面板自证钉点改查 bot.py+status.py 合并源码 |
| `tests/test_undefined_names.py` | TARGETS + status.py、roster.py |
| `tests/test_group_state_isolation.py` | `_save_group` 计数范围 +roster |

### 遗留（后台任务族剩余）

1. 下一刀候选：`_scheduled_pushes`(60，test_feature_registry 5 处 patch 需迁到新模块)；
   `rank_summary`(39)；`_daily_profile_scan`(85)/`sync_profiles`(83)（档案同步族，
   依赖最多，建议配 `sync_all`/`greet_once` 等辅助一起走）。
2. bot.py **2625 行**；距 <1500 还差 ~1125（大头 main_loop ~700）。
3. 部署清单：runtime/ 新模块累计 **9 个**（+status、roster）；其余同 3-9
   （上线还须带 prompt_defaults.py + personas/ 包并删线上 personas.py）。

## 2026-10-01 · Phase 3 Step 3-11 —— 定时问候/记忆汇报 → runtime/pushes.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。后台任务族第四刀：`_scheduled_pushes`
> （60 行，AST 定位切片），含 `_GREET_FEATURES` 登记表随迁。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/pushes.py`（97 行，LF） | `scheduled_pushes`（早/中/晚定时问候 + 午间记忆汇报；90 分钟补发窗口；每群独立打点）+ `_GREET_FEATURES` 登记表真身随迁（静态登记表扫描对 runtime/*.py 仍生效） |
| bot.py 瘦身 | 2625 → **2572 行**（-53）；注入壳 + import 行 `from runtime import pushes as _pushes`；main_loop 调用点 `await _scheduled_pushes(sender, dry)` 零改动 |
| 注入面 | kw-only：`push_cids`（bot.PUSH_CIDS）/ `load_state` / `save_group`（bot._save_group）/ `_feat_on`（bot 唯一入口包装器，带 log+放行回退——行为不能绕开）；叶子工具 generate_greeting（brains）/ _tune（tuning）/ _publish_memory_report（climax）/ set_persona_cid（personas）/ datetime 本模块直接 import |
| 测试 patch 迁移（test_feature_registry） | 行为测试 `test_greeting_gate_is_actually_consulted` 的 generate_greeting / datetime×2 / _publish_memory_report 4 处 setattr 改到 pushes 模块；`_feat_on`/`PUSH_CIDS` **保持 bot 接缝不变**（经注入壳传入） |

### 意外发现（重要）

1. **闸门注入参数必须沿用 `_feat_on` 名**：首跑 `test_wired_stage_matches_actual_gates`
   红 —— 静态扫描正则只认 `_feat_on(` 字面量调用，参数改名 `feat_on` 后
   `memory_report` 立刻"被判定未接线"（greet 三键因 `_GREET_FEATURES` 登记表在
   才没漂）。改回 `_feat_on` 后全绿。**教训：跨模块搬闸门调用点，名字就是扫描
   契约的一部分，注入参数不能随意重命名。**
2. `except` 后首行是 log 的 handler 不算静默（SILENT 正则只认 pass/return/continue
   开头）→ RUNTIME_SILENT_MAX **保持 67**；`_save_group(c, gs)` 用变量 `c` 不匹配
   `_save_group(cid,` 计数字面量 → test_group_state_isolation 无需动。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1528 收集 = 1522 通过 + 6 跳过，0 失败**
  —— 较 3-10 基线净增 1（TARGETS 新增 runtime/pushes.py）。
- 施工脚本自检：两文件 AST 解析 + 纯 LF + `undefined_from_source` 零未定义名
  + bot.py 无 `_GREET_FEATURES` 残留 + 壳唯一调用点。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/pushes.py` | 新增，97 行（scheduled_pushes + _GREET_FEATURES 真身） |
| `bot.py` | 2625 → 2572 行；函数改注入壳 + import 区 1 行 + _GREET_FEATURES 块删除 |
| `tests/test_feature_registry.py` | 行为测试 4 处 patch 迁到 pushes 模块（_feat_on/PUSH_CIDS 仍钉 bot） |
| `tests/test_undefined_names.py` | TARGETS + runtime/pushes.py |

### 遗留（后台任务族剩余）

1. 下一刀候选：`rank_summary`(39；deps: _active_cid/_load_member_names/get_name/
   load_state/self_uids，注入面很小)；`_daily_profile_scan`(85)/`sync_profiles`(83)
   （档案同步族，依赖最多，建议配 `sync_all`/`greet_once` 等辅助一起走）；
   最终大目标 main_loop 本体（~700 行）。
2. bot.py **2572 行**；距 <1500 还差 ~1070（大头 main_loop ~700）。
3. 部署清单：runtime/ 新模块累计 **10 个**（+pushes）；其余同 3-10
   （上线还须带 prompt_defaults.py + personas/ 包并删线上 personas.py）。

## 2026-10-01 · Phase 3 Step 3-12 —— 骚度排行 → runtime/rank.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。后台任务族第五刀：`rank_summary`
> （39 行，纯读 helper，注入面最小）。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/rank.py`（58 行，LF） | `build_rank_summary`（按 今日高潮 > 总高潮 > 发情值 > 活跃度 排序，活跃度只展示；排除 bot 自己；只列前 N 名） |
| bot.py 瘦身 | 2572 → **2542 行**（-30）；注入壳 `rank_summary(top=10)` 同签名 + import 行 `from runtime import rank as _rank` |
| 调用点零改动 | `_publish_rank` 内 `rank_summary()`；main_loop 传给 `_dispatch.handle_slash` 的 `rank_summary=rank_summary` kwarg（dispatch 内部 `top=int(_tune("rank.top_n", cid))` 经壳透传） |
| 注入面 | `load_state` kw-only 注入（conftest.state 接缝不变）；当前群 cid **直读 ctx 真源**（test_long_output 用 `ctx.set_active_cid` 定群，语义一致）；`self_uids`/`_load_member_names`/`get_name` 从 runtime.identity 直接 import |

### 意外发现

1. **runtime 函数不许与 bot 壳同名**：首跑 `test_p8s4_split::`
   `test_no_function_is_defined_in_both_bot_and_runtime` 红 —— 前几刀都遵守了
   "bot 壳 `_xxx` / runtime 真身 `xxx`" 的命名差（log_runtime_status/
   reconcile_members/scheduled_pushes），这刀偷懒同名 `rank_summary` 立刻被抓。
   改名 `build_rank_summary` 后全绿。这条全自动守卫比逐个列名字强，继续白嫖。
2. **两个项目测试同源**：dzmm_nuo/tests 有 55 个文件与 dzmm_botapi/tests
   MD5 相同（含 conftest.py）——两项目同源、测试曾整体同步。`__pycache__`
   里同内容文件的陈旧 pyc 会把 traceback 路径显示成 `..\dzmm_botapi\...`
   （cosmetic，但持续误导排查）。已**清空整个 tests/__pycache__**（纯缓存；
   PYTHONDONTWRITEBYTECODE=1 下不会重建），一劳永逸。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1529 收集 = 1523 通过 + 6 跳过，0 失败**
  —— 较 3-11 基线净增 1（TARGETS 新增 runtime/rank.py）。
- 施工脚本自检：两文件 AST 解析 + 纯 LF + `undefined_from_source` 零未定义名
  + 壳唯一调用点。RUNTIME_SILENT_MAX 保持 67（函数体无 try/except）；
  test_group_state_isolation 无需动（无 _save_group 调用）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/rank.py` | 新增，58 行（build_rank_summary；load_state 注入 + ctx 真源读点） |
| `bot.py` | 2572 → 2542 行；函数改注入壳 + import 区 1 行 |
| `tests/test_undefined_names.py` | TARGETS + runtime/rank.py |
| `tests/__pycache__` | 整目录清空（陈旧 co_filename 根治） |

### 遗留（后台任务族剩余）

1. 下一刀候选：`_daily_profile_scan`(85)/`sync_profiles`(83)（档案同步族，依赖
   最多，建议配 `sync_all`/`greet_once`/`memory_report_once`/`_build_profile_text`
   等辅助一起走）；之后是最终大目标 main_loop 本体（~700 行）。
2. bot.py **2542 行**；距 <1500 还差 ~1040（大头 main_loop ~700）。
3. 部署清单：runtime/ 新模块累计 **11 个**（+rank）；其余同 3-11
   （上线还须带 prompt_defaults.py + personas/ 包并删线上 personas.py）。

## 2026-10-01 · Phase 3 Step 3-13 —— 档案同步族 → runtime/profile_sync.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。后台任务族第六刀（本阶段最大一刀）：
> `_daily_profile_scan`(85) + `sync_profiles`(83) + `sync_all`(12) + `greet_once`(36)
> + `memory_report_once`(13) + `_build_profile_text`(15) + `_to_local` +
> `_scan_load`/`_scan_save` + PROFILE_* 常量。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/profile_sync.py`（329 行，LF） | `daily_profile_scan`（每日全群扫昵称/简介/签名/性别 + 改名播报；基线 profile_scan.json）/ `run_sync_profiles`（增量拉消息→提取发言→刷 bio→滚动总结→推进游标）/ `run_sync_all` / `run_greet_once` / `run_memory_report_once`（CLI 一次性模式，独立 Playwright profile）/ `_build_profile_text`/`_to_local`/`scan_load`/`scan_save`/PROFILE_* 常量随迁 |
| bot.py 瘦身 | 2542 → **2298 行**（**-244**）；5 个注入壳 + PROFILE_SCAN_HOUR/PROFILE_SCAN_MAX_API/PROFILE_NOTICE_MAX 三个 re-export（test_tuning 的 `bot.PROFILE_SCAN_*` 对照钉住；re-export 行以 `NAME =` 开头，test_no_consumption_site 的 is_def 放行规则安全） |
| 调用点零改动 | main_loop `await asyncio.to_thread(_daily_profile_scan)`；dispatch kwarg `sync_profiles=sync_profiles`（内部 `to_thread(sync_profiles)` 无参）；`main()` 的 `--sync`/`--greet`/`--memory-report` CLI；唯一破例：main_loop L1422 `_scan_load()` → `_profile_sync.scan_load()`（纯叶子读 JSON、无测试接缝） |
| 注入面 | daily_profile_scan：groups/mute_cids/room_cids/primary_cid/`_feat_on`；run_sync_profiles：`_feat_on`/`_save_group`/`load_state`；run_sync_all：groups/mute_cids/`sync_profiles`（传 bot 壳）；run_greet_once/run_memory_report_once：groups/push_cids。其余（dzmm 平台读口、profiles、brains、identity/memory/text 叶子、personas、Sender、ctx）直接 import；`_set_active_cid(cid)` → `ctx.set_active_cid(cid)`（真源写点） |

### 意外发现

1. **wb 词边界替换的前瞻陷阱**：`wb(body, "_scan_save(", …)` 命中 0 ——
   `(?![A-Za-z0-9_])` 前瞻检查的是 old **末尾** `(` 之后的字符，
   `_scan_save(snap)` 的 `(` 后是 `s` → 被拒；`_scan_load()` 后是 `)` 所以通过。
   教训：**带 `(` 结尾的替换不能走词边界函数**（前瞻会看括号后面），用精确
   `str.replace` + 计数断言。此前 3-10~3-12 全是纯标识符替换没踩到。
2. RUNTIME_SILENT_MAX 67 → **74**（+7）：搬进 runtime 视野的既有静默 ——
   scan_load return {} / scan_save pass（基线 JSON 尽力而为）/
   daily_profile_scan get_members continue（单群拉不到跳过）/
   _to_local return 原样（老消息格式）/ run_sync_profiles pass ×2（单成员级降级）/
   run_greet_once 读回 pass（读回失败当已发出，防补发两遍午安）。理由已入注。

### 验证（全绿，一次过）

- 全量 `pytest`（junitxml 统计）：**1530 收集 = 1524 通过 + 6 跳过，0 失败**
  —— 较 3-12 基线净增 1（TARGETS 新增 runtime/profile_sync.py）。
- 施工脚本自检：两文件 AST 解析 + 纯 LF + `undefined_from_source` 零未定义名
  + **同名函数守卫预检**（3-12 的教训直接内置进脚本：bot defs ∩ profile_sync defs = ∅）。
- test_group_state_isolation 复核：bot 11 + room 7 + roast 3 + climax 1 + roster 1
  = 23 ≥ 20，无需动。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/profile_sync.py` | 新增，329 行（档案同步族 + 常量 + 基线读写器） |
| `bot.py` | 2542 → 2298 行；5 个注入壳 + 3 个 re-export + import 行 + L1422 直连 scan_load |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 67 → 74（+7 逐条理由） |
| `tests/test_undefined_names.py` | TARGETS + runtime/profile_sync.py |

### 遗留（后台任务族剩余）

1. **后台任务族清零**。下一刀 = 最终大目标 **main_loop 本体**（~700 行，注入面
   最大、收益最高），或先清 `_publish_rank`(17)/`_resolve_bot_uids` 等零散块。
2. bot.py **2298 行**；距 <1500 还差 ~800。
3. 部署清单：runtime/ 新模块累计 **12 个**（+profile_sync）；其余同 3-12
   （上线还须带 prompt_defaults.py + personas/ 包并删线上 personas.py）。

## 2026-10-01 · Phase 3 Step 3-14 —— inflight 族 → runtime/inflight.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。后台任务族收尾第七刀：
> `_rollback_last_id`(7) + `_mark_inflight`(12) + `_clear_inflight`(11) +
> `_recover_inflight`(50) + INFLIGHT_* 常量（含 P12-BC 事故注释块）。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/inflight.py`（120 行，LF） | `rollback_last_id` / `mark_inflight` / `clear_inflight` / `recover_inflight`（重启重放：标记在 + 没人回过 → last_id 退回重放）+ INFLIGHT_KEY/STALE_SEC/PROBE_MSGS 真身 |
| bot.py 瘦身 | 2298 → **2229 行**（-69）；4 个注入壳 + 常量 re-export（test_inflight 钉 `bot.INFLIGHT_KEY`）+ import 行 |
| 调用点零改动 | main_loop `_clear_inflight(cid)          # P12-BC` / `_rollback_last_id(cid, old_last_id)`、_run_all `_recover_inflight(state, GROUPS)` 全部被 test_inflight_recovery 源码钉点钉住，文本原样；dispatch/at_chat/room 的 `_mark_inflight=_mark_inflight` 注入不变 |
| 注入面 | load_state/_save_group（conftest 接缝）+ **get_messages/is_self/log**（test_inflight patch 的是 `bot.get_messages`/`bot.is_self`/`bot.log`，直连 runtime 会绕开接缝）——3-11 `_feat_on` 教训的推广：**凡是测试 patch 的 bot 名字（哪怕叶子）都必须注入** |

### 意外发现

1. **log 也是接缝**：首跑 3 个 red —— 测试 patch `bot.log` 断言日志内容
   （"滚出窗口"/"标记太旧"/"恢复失败"三条路径），runtime 直绑 runtime.obs.log
   绕开了接缝。补 `log` kw-only 注入后全绿。注入面清单从此写进 docstring。
2. `_save_group(cid,` 计数棘轮（≥20）复核：bot 6 + room 7 + roast 3 + climax 1
   + roster 1 = 18 < 20 —— inflight 又搬走 4 处！test_group_state_isolation
   计数范围扩为 roster/inflight 合并按 `save_group(cid,` 计 → 22 ≥ 20。
   **"每搬一刀都要复核计数清单"第三次应验。**

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1531 收集 = 1525 通过 + 6 跳过，0 失败**
  —— 较 3-13 基线净增 1（TARGETS 新增 runtime/inflight.py）。
- 施工脚本自检：AST + 纯 LF + undefined_from_source 零未定义名 + 同名函数预检。
- RUNTIME_SILENT_MAX 保持 74（inflight 的 except 首行全是 log，不算静默）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/inflight.py` | 新增，120 行（4 函数 + 3 常量 + P12-BC 注释块随迁） |
| `bot.py` | 2298 → 2229 行；4 个注入壳 + 3 个常量 re-export + import 行 |
| `tests/test_group_state_isolation.py` | 计数范围 +inflight（roster/inflight 合并 injected 项） |
| `tests/test_undefined_names.py` | TARGETS + runtime/inflight.py |

### 遗留

1. 剩余大块：main_loop(711) + _run_all(139) + 发送链组装族
   （_bot_senders/_wrap_with_bot_routing/_wrap_with_outbox/_pick_leftover 等 ~146）
   + 消息历史族（_remember_turn/_recent_turns/_seed_turns/should_reply 等 ~120）。
2. bot.py **2229 行**；距 <1500 还差 ~730。
3. 部署清单：runtime/ 新模块累计 **13 个**（+inflight）；其余同 3-13。

## 2026-10-01 · Phase 3 Step 3-15 —— 对话轮次族 → runtime/turns.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。第八刀（接缝最密的一族）：
> `_remember_turn`(25) + `_recent_turns`(19) + `_seed_turns`(23) +
> `_other_bot_uids`(30) + `_platform_api`(8) + `_TURNS` 状态族（5 个可变对象）。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/turns.py`（149 行，LF） | `remember_turn`（滚动上下文 + mid 去重 + 回读并条）/ `recent_turns`（N 条截取 + 双边 only_uid + 别家 bot 过滤）/ `seed_turns`（首用补平台历史，每群每进程一次）/ `other_bot_uids`（isBot > 已知 Bot > 昵称兜底，按群缓存 ttl）/ `platform_api` 薄封装；状态真源 `_TURNS`/`_TURN_MAX`/`_TURN_SEEN`/`_TURN_SEEDED`/`_GROUP_BOTS` |
| bot.py 瘦身 | 2229 → **2157 行**（-72）；状态**同一对象** re-export（tests 直操 `bot._TURNS`/`bot._GROUP_BOTS` 与 runtime 是同一份）+ 5 个注入壳 + import 行 |
| 注入面 | `recent_turns(*, other_bot_uids, is_self)` —— test_ctx_bilateral patch `bot._other_bot_uids`，runtime 直调模块内函数会绕开；`seed_turns(*, get_messages, is_self, get_name, read_timeout)` —— test_chat_transcript patch `bot.get_messages/is_self/get_name`；`PLATFORM_READ_TIMEOUT` 保留 bot（main_loop 多处用）注入 read_timeout |
| 调用点零改动 | main_loop 的 `_remember_turn(`/`_recent_turns(cid, _ctxn, exclude_mid=_ask_mid,`/`_seed_turns` 被 test_chat_transcript/test_chat_context/test_at_focus 源码钉住；dispatch/at_chat 的 `_remember_turn=_remember_turn` 注入不变 |

### 意外发现

1. **前瞻陷阱二次踩**：`wb(s, "_remember_turn(", …)` 又命中 0 —— old 以 `(` 结尾时
   `(?![A-Za-z0-9_])` 检查的是括号**后面**的字符（`cid` 的 c）。3-13 刚踩过
   `_scan_save(`，两次教训合并成规则：**替换目标带 `(` 结尾一律用精确
   str.replace + 计数断言，不用 wb**。
2. **状态同对象 re-export 是 tests 直操模块状态的正解**：`bot._TURNS = turns._TURNS`
   （可变 dict/set 引用同一份），test 的 clear/赋值/in 操作与 runtime 状态天然同步；
   不可变标量（`_TURN_MAX`）只要 tests 不写就安全 re-export。
3. RUNTIME_SILENT_MAX 74 → **75**（+1：other_bot_uids 的 bot_uids() 降级 pass，
   后面还有平台 isBot/昵称两层兜底）。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1532 收集 = 1526 通过 + 6 跳过，0 失败**
  —— 较 3-14 基线净增 1（TARGETS 新增 runtime/turns.py）。
- 施工脚本自检：AST + 纯 LF + undefined_from_source 零未定义名 + 同名函数预检。
- test_group_state_isolation 复核：无 _save_group 调用，无需动。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/turns.py` | 新增，149 行（轮次族 5 函数 + 5 个状态对象真源） |
| `bot.py` | 2229 → 2157 行；状态 re-export + 5 个注入壳 + import 行 |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 74 → 75 |
| `tests/test_undefined_names.py` | TARGETS + runtime/turns.py |

### 遗留

1. 剩余大块：main_loop(711) + _run_all(139) + 发送链组装族
   （_bot_senders/_wrap_with_bot_routing/_wrap_with_outbox/_pick_leftover ~146）
   + 回复判定小块（_quotes_self/_quote_reply_on/should_reply/message_lag ~47）。
2. bot.py **2157 行**；距 <1500 还差 ~660。
3. 部署清单：runtime/ 新模块累计 **14 个**（+turns）；其余同 3-14。

## 2026-10-01 · Phase 3 Step 3-16 —— 发送链组装族 → runtime/sender_chain.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。第九刀：`_truthy_env`(2) +
> `_outbox_enabled`(4) + `_log_route`(5) + `_bot_senders`(31) +
> `_wrap_with_bot_routing`(38) + `_wrap_with_outbox`(36) + `_pick_leftover`(30)
> + `REQUEUE_MAX` 常量。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/sender_chain.py`（193 行，LF） | `wrap_with_outbox`（OutboxSender(RoutingSender(账号))，中断记录清理）/ `wrap_with_bot_routing`（DZMM_BOT_ROUTING 闸门 + env 单 Bot 退回）/ `bot_senders`（按 _CFG.bots 注册表造发送器）/ `pick_leftover`（放回队列兜底）/ `truthy_env`/`outbox_enabled`/`log_route` + REQUEUE_MAX 真身 |
| bot.py 瘦身 | 2157 → **2054 行**（-103）；7 个注入壳 + REQUEUE_MAX re-export + import 行 |
| 注入面 | `bot_senders/wrap_with_bot_routing/wrap_with_outbox`：cfg（_CFG）/origin（ORIGIN）/dzmm_bot_sender_cls（**测试 patch `bot.DzmmBotSender`**）/runtime_import_ok（**测试 patch `bot._RUNTIME_IMPORT_OK`**）/runtime_import_err kw-only 注入；模块内链式调用把注入一路透传；RoutingSender/OutboxSender/OutboxStore/INTERRUPTED_NOTE 无测试 patch → 模块直连 |
| 调用点零改动 | _run_all 的 `_wrap_with_outbox(`（test_parallel_groups 钉 count==1）、main_loop 的 `_pick_leftover(`（test_quote_reply 钉 index）文本不变 |

### 意外发现

1. **REQUEUE_MAX 是 bot 本地常量**（不是 outbox import 的）且被 test_leftover_requeue
   以 `bot.REQUEUE_MAX` 引用 —— 首轮 grep 测试接缝时漏了这个名字，全量跑红 1 个。
   修复 = bot 侧 re-export（同 INFLIGHT_* 模式）。**教训：常量也要进接缝盘点清单，
   不能只 grep 函数名。**
2. 防御性导入块（L933-941 try/except + _RUNTIME_IMPORT_OK/ERR）**保留在 bot**：
   它是"deploy 漏传 runtime/ 时静默退回直发"的启动期探测，语义属于宿主；
   runtime/sender_chain 只消费标志值（注入），自己不做 try-import。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1533 收集 = 1527 通过 + 6 跳过，0 失败**
  —— 较 3-15 基线净增 1（TARGETS 新增 runtime/sender_chain.py）。
- RUNTIME_SILENT_MAX 保持 75（族内 except 首行全是 log）；计数棘轮无 _save_group 不动。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/sender_chain.py` | 新增，193 行（发送链组装 7 函数 + REQUEUE_MAX 真身） |
| `bot.py` | 2157 → 2054 行；7 个注入壳 + REQUEUE_MAX re-export + import 行 |
| `tests/test_undefined_names.py` | TARGETS + runtime/sender_chain.py |

### 遗留

1. 剩余：main_loop(711) + _run_all(139) + 回复判定小块
   （_quotes_self/_quote_reply_on/should_reply/message_lag ~47）+ 零散
   （_recover? 已搬/_welcome_of/_room_cmd_refused 等）。
2. bot.py **2054 行**；距 <1500 还差 ~555。
3. 部署清单：runtime/ 新模块累计 **15 个**（+sender_chain）；其余同 3-15。

## 2026-10-01 · Phase 3 Step 3-17 —— 配置生命周期族 → runtime/config_boot.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。第十刀：`apply_config_overrides`(22) +
> `_live_structural`(13) + `hot_reload_check`(18) + `_log_hotload`(21) +
> `migrate_config_file`(15) + `_load_root_module`(20) + `_HOT_CAT` 常量。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/config_boot.py`（158 行，LF） | `run_apply_config_overrides`（启动接线，hotload 不可用退回逐项注入）/ `run_hot_reload_check`（主循环热加载检查，丢线程）/ `log_hotload`（自证行 + _HOT_CAT）/ `run_migrate_config_file`（旧配置升级落盘）/ `live_structural`（hotload 的结构性快照）/ `load_root_module`（按路径加载根模块，绕开 sio_subscribe 的 sys.path 副作用） |
| bot.py 瘦身 | 2054 → **1980 行**（-74）；6 个注入壳 + import 行；启动序列调用点（L291/L309/313/317）与 main_loop 调用点零改动 |
| 注入面 | `apply_config_overrides` 的 cfg=None 在**壳层**解析为 _CFG；`live_structural` 注入 cfg/groups/mute_cids/screen_cids/push_cids（只读 bot 运行期镜像是它的要点）；`migrate_config_file` 注入 default_kw（`_FEATURES_DEFAULT_KW` 聚合 bot 默认常量，**留 bot**）；`hot_reload_check` 无注入 —— test_loop_blocking patch 的是 `hotload.maybe_reload`（模块属性），`_hotload.maybe_reload` 动态查找穿透，壳保持 async（钉 iscoroutinefunction） |

### 意外发现

1. **施工脚本不幂等的代价**：修 import 别名（`_cfgboot` → `_config_boot` 打错）时
   误跑了一次 pytest——暴露脚本其实**已在上一轮成功写盘**（1980 版），随后的
   重跑在"函数已搬走"的锚点上 count=0 失败。教训：**施工脚本写盘后立即自检
   失败会留下半成品状态** —— 恢复顺序 = 先确认盘上版本的完整性（undefined 检查
   + import 冒烟），再决定续作还是回滚；锚点类脚本应开头检测"是否已施工过"。
2. **runtime 子包内不能裸 `import config_store`**：它只在 runtime/ 下（根目录的
   是 state_store.py）——bot 里的名字来自 `from runtime import config_store`
   re-export。半成品盘上文件的这个裸 import 直接让 conftest ImportError。
   修复 = `from runtime import config_store`（同 perf.py 的 personas 先例）。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1534 收集 = 1528 通过 + 6 跳过，0 失败**
  —— 较 3-16 基线净增 1（TARGETS 新增 runtime/config_boot.py）。
- import 冒烟：bot 可导入，启动序列（身份注册/热加载配置）正常运行。
- RUNTIME_SILENT_MAX 保持 75（族内 except 首行全是 log）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/config_boot.py` | 新增，158 行（配置生命周期 6 函数 + _HOT_CAT） |
| `bot.py` | 2054 → 1980 行；6 个注入壳 + import 行 |
| `tests/test_undefined_names.py` | TARGETS + runtime/config_boot.py |

### 遗留

1. 剩余：main_loop(711) + _run_all(139) + 零散（_welcome_of 21/`_room_cmd_refused`
   11/profile_summary 6/defend_nuo 6/_next_interval 6/_max_interval 6 等）。
2. bot.py **1980 行**；距 <1500 还差 ~480。
3. 部署清单：runtime/ 新模块累计 **16 个**（+config_boot）；其余同 3-16。

## 2026-10-01 · Phase 3 Step 3-19 —— main_loop + _run_all 整体搬迁 → runtime/mainloop.py（本地完成，达成 <1500 验收线）

> 前置说明：纯本地重构，**改动未部署**。**最终大手术**：main_loop(711) +
> _run_all(139) 整体搬迁，循环体**逐字未动**（tokenize 级改写）。

### 方案：bot 模块桥 `B`

main_loop 自由名 102 个 / _run_all 30 个 —— 逐个 kw-only 注入不现实。
采用**bot 模块桥**：体里所有 bot 侧名字经 `B.` 访问（B = kw-only 注入的
bot 模块对象），**运行期属性查找** → monkeypatch `bot.X` 的全部既有接缝
（conftest.state、各测试 patch）与搬迁前语义完全一致。改写用 **tokenize 级
替换**（保注释/字符串/缩进），AST scoping 收集自由名。

| 动作 | 内容 |
|---|---|
| 新增 `runtime/mainloop.py`（876 行，LF） | `run_main_loop`（主循环体，B. 化 168 处）/ `run_all`（唯一共享资源调度，B. 化 22 处）；`global _ACTIVE_CID, _captcha_notified` 语句删除——`_captcha_notified` 经 `B._captcha_notified` 属性链写回 bot 命名空间（行为等价），`_ACTIVE_CID` 体里无写点 |
| bot.py 瘦身 | 1939 → **1095 行**（**-844，达成 <1500 验收线**）；两个注入壳（签名/协程保持）+ `_parallel_groups_on` 恢复保留 + import 行 |
| 直连名单 | asyncio/datetime/os/subprocess/time/log（+builtins）不加 B.——log 无主循环路径的日志断言测试 |
| 钉点迁移 | 14 个源码钉点测试同步：getsource/源文件改指 mainloop（test_parallel_groups/test_outbox_retry/test_webhook_server/test_chat_guard/test_fortune_tea/test_gender_and_at_only/test_tea_room/test_open_room_cmd/test_inflight_recovery/test_recovery_ownership/test_chat_transcript/test_loop_blocking/test_feature_registry/test_account_long_socket）；**markers 是子串匹配，`B._scheduled_pushes(sender, dry)` ⊃ 原文本，markers 本体不用改** |

### 意外发现

1. **_parallel_groups_on 被整块替换吞掉**：它正好在 main_loop 与 _run_all
   之间的空隙里，范围替换把它删了（test_parallel_flag_defaults_off 红出
   AttributeError）。从 `git show HEAD:bot.py` 取回原文补回。
   **教训：范围替换前检查两锚点之间的"夹层"内容。**
2. **tokenize 改写的三个边界**：① except as 局部名（`_re`）不在 ast.arg——
   scoping 必须收 ExceptHandler.name，否则被误加前缀；② 函数内
   `import ... as _cdrem` 的别名绑定同理；③ kwargs 键（`rank_summary=...`）
   用 lookahead `=` 跳过。def 行里保留的 `_cdrem` 绑定让体内调用零改动。
3. **壳 docstring 不能复述钉点文本**（3-18 规则再次应验）+ **窗口钉点搬家时
   断言文本里的名字也要跟 B. 前缀走**（`_lag = B.message_lag(msgs)`、
   `state = B.load_state()`、`_wh.collect_secrets(B._CFG.bots)`、
   `_recover_inflight(state, B.GROUPS)`、`if not B._get_story_state(...)`）。
4. RUNTIME_SILENT_MAX 76 → **78**（+2：主循环体两处"尽力而为"静默随体入
   runtime 视野）；`_save_group(cid,` 计数棘轮：bot 0 + mainloop 12 → 计数
   范围扩 +mainloop，合计 28 ≥ 20。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1536 收集 = 1530 通过 + 6 跳过，0 失败**
  —— 较 3-18 基线净增 1（TARGETS 新增 runtime/mainloop.py）。
- import 冒烟：bot/mainloop 可导入；启动序列正常；壳签名（5 参数无默认）
  与 markers（B. 前缀）逐项验证。
- 施工自检：AST + 纯 LF + undefined_from_source（mainloop 仅 B/直连 import）
  + 同名预检（bot defs ∩ mainloop defs = ∅）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/mainloop.py` | 新增，876 行（主循环体 + _run_all 体，bot 桥 B） |
| `bot.py` | 1939 → **1095 行**；2 个注入壳 + `_parallel_groups_on` 保留 + import 行 |
| 14 个钉点测试 | getsource/源文件改指 mainloop；断言文本随 B. 前缀更新 |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 76 → 78 |
| `tests/test_group_state_isolation.py` | 计数范围 +mainloop |
| `tests/test_undefined_names.py` | TARGETS + runtime/mainloop.py |

### 遗留

1. **<1500 验收线达成**（bot.py 1095 行）。剩余零散（_welcome_of 21/
   _room_cmd_refused 11/profile_summary 6/defend_nuo 6/_next_interval 6/
   _max_interval 6 等 ~80 行）可清可不清——建议屋主定夺是否继续。
2. 部署清单：runtime/ 新模块累计 **18 个**（+mainloop）；上线必须整体带上
   runtime/ 全目录 + prompt_defaults.py + personas/ 包（并删线上 personas.py）
   + bot.py + runtime/mainloop.py。**先完成 Phase 2（S1 凭据挂目录）并观察
   24h，再上 Phase 3**——部署顺序铁律不变。

## 2026-10-01 · Phase 3 Step 3-20 —— _welcome_of + 欢迎语常量 → runtime/joins.py（本地完成）

> 前置说明：纯本地重构，**改动未部署**。小刀（屋主点名）：`_welcome_of`(9) +
> `WELCOME_TEXT`/`WELCOME_TEXT_PLAIN` 常量真源 → runtime/joins.py。

### 做了什么

| 动作 | 内容 |
|---|---|
| `runtime/joins.py` 追加（322 行） | `build_welcome`（真身；phrase_p/两份常量 kw-only 注入）+ WELCOME_TEXT/WELCOME_TEXT_PLAIN **常量真源**随迁（与 room_greet_text 同属欢迎话术，内聚） |
| bot.py（1092 行） | `_welcome_of` 注入壳：常量经壳**传值**（`welcome_text=WELCOME_TEXT, ...`）——test_recovery_ownership patch `bot.WELCOME_TEXT` 时壳读 bot 全局，接缝保持；常量 re-export 保 `bot.WELCOME_TEXT`/`bot.WELCOME_TEXT_PLAIN`（test_rose_weisi_switch 值对照） |
| 钉点同步 | test_feature_registry：兜底表达式断言改查 joins.py 的 `welcome_text_plain if plain else welcome_text` |

### 接缝核对（全绿）

- test_chat_humanize：`bot._welcome_of(...)` 行为用例 ✓；"joins 不碰话术常量"
  断言 ✓（真身用注入参数名 welcome_text，`WELCOME_TEXT, cid` 子串零出现）；
  `_welcome_of(` + `welcome_of(` 计数 ≥6 ✓。
- test_recovery_ownership patch `bot.WELCOME_TEXT` ✓（壳传值）；test_rose_weisi_switch
  值对照 ✓（re-export）。
- RUNTIME_SILENT_MAX 78 不变；TARGETS 不变（joins 已在）。

### 验证（全绿，一次过）

- 全量 `pytest`：**1536 收集 = 1530 通过 + 6 跳过，0 失败**。
- bot.py **1092 行**；joins.py 322 行。

### 遗留

1. 剩余零散（_room_cmd_refused 11/profile_summary 6/defend_nuo 6/_next_interval 6/
   _max_interval 6 等 ~50 行）可清可不清。
2. 部署清单同 3-19（runtime/ 全目录 + prompt_defaults.py + personas/ 包；
   先 Phase 2 观察 24h 再上 Phase 3）。

### 追加（同日）：deploy.ps1 半棵树硬门槛扩容 + 全日体检

- **体检（3-10~3-20 十一刀）**：全量 pytest 1536=1530+6 全绿；12 文件静态检查
  （AST/纯 LF/undefined 零）；22 模块 import 冒烟；20 个接缝钉点逐一核对；
  SILENT 实测 76≤78；_save_group 合计 22≥20；临时脚本清理干净。
- **deploy.ps1 `$need` 从 4 项扩到 26 项**：P3 拆分后"半棵树 = 启动 ImportError"，
  门槛覆盖全部 18 个 runtime/ 新模块 + prompt_defaults.py + personas 包双文件
  （原 bot.py/brains.py/obs/prompts/.dockerignore 保留）。已验证：PSParser 0 错、
  ASCII-only 0 违规、26 个门槛文件本地全部存在（模拟门槛可过）。
- ⚠️ 上线手工动作不变：**删除线上旧 personas.py**（与 personas/ 包同名冲突，
  deploy.ps1 不会替你删——已写进 $need 上方注释）。

## 2026-10-01 · Phase 3 线上部署（含一次事故与回滚预案）

> 19:13 首次部署 → 19:14 发现主循环崩溃循环 → 19:20 修复重发 → 19:21 业务
> 真实链路验证通过。**线上现状 = P3 版**（bot.py 1092 行 + runtime/ 18 模块）。

### 部署时间线

| 时间 | 动作 |
|---|---|
| 19:13 | `deploy.ps1 -Action deploy`：全树备份 `backup-code-20261001-191339.tar.gz`（= **部署前旧版，主回滚点**）→ 167 文件上传（$need 26 项门槛通过）→ 镜像重建 → 容器启动 |
| 19:14 | 启动自证行全部正常（P5/P7/P8/P11/P12），但 **main_loop 崩溃循环**：壳调 `_mainloop.main_loop` 而真身已改名 `run_main_loop`（3-19 的 p8s4 同名守卫改名），AttributeError ×4 实例，10s 重启循环 |
| 19:15 | 本地修复（bot.py 壳一行）+ **新增系统性核验**：AST 扫 bot.py 全部 15 组 / 46 个 `_module.func` 壳调用逐一 hasattr（其余零悬空）+ main_loop/_run_all **真调用** dispatch 冒烟（B 桥 = bot 模块本身）；全量 pytest 1536 全绿 |
| 19:20 | 二次部署（备份 `backup-code-20261001-192008.tar.gz` = **坏版，勿用于回滚**）→ 容器启动 |
| 19:21 | **业务真实验证**：按群并行 4 实例全部 start；茶水间真实 @「我不在的时候发生了什么」→ 回顾生成（223 条窗口）→ 发送成功。restarts=0，巡检正常 |

### 事故根因与教训

1. **本地测试全是 import/getsource/签名级**，没有一个真调用 `bot.main_loop`——
   壳→真身的改名不同步只有真调用才炸。教训：**入口级改壳必须配 dispatch 真调用
   冒烟**（本次已沉淀为 `tests/_tmp_dispatch_smoke.py` 的核验模式：46 个壳调用
   hasattr + dispatch 链验证；建议下次重构时把它固化成常驻测试）。
2. **rollback 的包遮蔽陷阱**（已在 deploy.ps1 修复）：tar 解包不会删除不在包里的
   文件——回滚后旧 personas.py 被还原，但新 personas/ 包仍在并**遮蔽**同名模块
   （Python 包优先）。rollback 动作已加 `rm -rf personas`。

### 回滚预案（拿去即用）

```powershell
# 一条命令回滚到"最近一次备份"（现已会自动 rm -rf personas）
.\deploy.ps1 -Action rollback

# ⚠️ 但 2026-10-01 19:20 后，"最近备份"= 坏版 192008！必须指定部署前那个：
.\deploy.ps1 -Action exec "cd /opt/dzmm && tar xzf backup-code-20261001-191339.tar.gz && rm -rf personas && docker compose build dzmm-bot && docker compose up -d dzmm-bot"
```

- 判定"要不要回滚"：`.\deploy.ps1 -Action logs` 看启动自证行 + 有无 Traceback；
  `.\deploy.ps1 -Action health` 看容器/进程/登录态/巡检。
- 备份链：191339 = 部署前旧版（**金标准回滚点**）；192008 = 坏版（勿用）；
  下一次部署会产生新的"当前版"备份。tar 排除凭据/状态（.env/token/state/profiles），
  回滚不会动它们。
- 巡检/心跳/出站重试都在旧路径，回滚零数据损失（sqlite store.db 是宿主挂载）。

### 事故二（19:29 修复）：datetime 模块遮蔽类

- 19:23 日志发现 `loop[4fc4a2fd]: module 'datetime' has no attribute 'now'`
  反复出现——**3-19 把 `datetime` 放进了直连名单**，但 bot 命名空间的 datetime
  是 `from datetime import datetime` 的**类**，mainloop 顶部 `import datetime`
  引入模块 → 直连后 L592 `datetime.now()` 炸。
- 连锁：运势回复**已发出**但后续处理炸 → 回滚 last_id → 下轮**双发**风险。
- 修复：mainloop 顶部改 `from datetime import datetime`（与 bot 命名空间对齐，
  已加注释防止回退）；本地验证 `mainloop.datetime is datetime.datetime` ✓；
  其余直连名（asyncio/os/subprocess/time/log）bot 侧同为模块/函数，无此陷阱。
- 19:29 第三次部署（备份 192853 = 坏版二），启动干净：4 实例 start / sio 连接 /
  出站 0 / 无 Traceback / restarts=0 / 巡检正常。

### 备份链最终状态（回滚以此为准）

| 备份 | 内容 | 用途 |
|---|---|---|
| `191339` | **部署前旧版（P12-AN）** | 金标准回滚点 |
| `192008` | 坏版一（main_loop 改名漏网） | 勿用 |
| `192853` | 坏版二（datetime 模块遮蔽） | 勿用 |
| （下一次 deploy 产生） | 当前修复版 | 之后正常滚动 |

回滚命令（指定金标准）：
`.\deploy.ps1 -Action exec "cd /opt/dzmm && tar xzf backup-code-20261001-191339.tar.gz && rm -rf personas && docker compose build dzmm-bot && docker compose up -d dzmm-bot"`

### 部署后观察清单（前 24h）

- 每天巡检日志（check_health.log）是否有告警；restarts 是否 >0
- 群里功能抽检：`/说明`、@闲聊、`/查`、排行、定时问候（明早 8 点档）
- P11/P12 自证行：配置来源=file、prompt 41/41 来自文件、功能 68 已接线
- 上线手工项确认：线上旧 personas.py 仍在（被 personas/ 包遮蔽，无害；
  回滚时靠它还原——不要删）

## 2026-10-01 · Phase 3 Step 3-18 —— 回复判定族 → runtime/reply_gate.py + rank.py 追加（本地完成）

> 前置说明：纯本地重构，**改动未部署**。第十一刀：`_quotes_self`(4) +
> `_quote_reply_on`(12) + `should_reply`(7) + `message_lag`(24) → reply_gate.py；
> `_rank_hour_set`(12) + `_publish_rank`(10) → 追加进 runtime/rank.py。

### 做了什么

| 动作 | 内容 |
|---|---|
| 新增 `runtime/reply_gate.py`（80 行，LF） | `quotes_self`（引用我说的话判定，_ref_of 从 at_chat 直连）/ `quote_reply_on`（quote_reply 开关 **或** at_only 天然放行，P12-BM B）/ `find_reply_command`（上下文最近 3 条的未处理指令）/ `calc_message_lag`（群友消息投递延迟，UTC+8 折算） |
| `runtime/rank.py` 追加 | `rank_hour_set`（schedule.rank_hours 解析，配错退回 12/18）+ `publish_rank`（发布骚度排行）；补 _tune/log import |
| bot.py 瘦身 | 1980 → **1939 行**（-41）；6 个注入壳 + import 行 |
| 注入面 | quotes_self 注入 is_self（test_quote_reply patch `bot.is_self`）；quote_reply_on 注入 `_feat_on`（⚠️ 闸门参数沿用名）；message_lag/find_reply_command/rank_hour_set 无测试 patch → _tune/is_self 直连；publish_rank 注入 rank_summary（bot 壳） |
| 调用点零改动 | main_loop 的 `_quotes_self(m)`/`_quote_reply_on(cid)`/`_rank_hour_set()`/`_publish_rank(sender, cid, dry)`/`message_lag(msgs)`/`should_reply(` 全部被源码钉点钉住 |

### 意外发现

1. **壳 docstring 不能复述被钉的调用点文本**：message_lag 壳 docstring 写了
   `_lag = message_lag(msgs)`（本想说明"调用点被钉"），test_chat_transcript 的
   `src.index("_lag = message_lag(msgs)")` 先命中 docstring → 源码窗口错位 →
   `_remember_turn(` 断言红。规则：**壳 docstring 描述"被钉"这一事实即可，
   逐字复述钉点文本会让 index 命中错位**。
2. **窗口钉点会越过短函数**：`seg.split("return")[-1]` 原本取 900 字符窗口内
   最后一个 return —— 真身搬家后窗口越过 quote_reply_on 抓到了下一个函数的
   return。修复 = 在本函数段内（第一个 `\n\n\n` 之前）取尾。**窗口钉点搬家时
   要重算窗口语义，不能只换文件路径。**

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1535 收集 = 1529 通过 + 6 跳过，0 失败**
  —— 较 3-17 基线净增 1（TARGETS 新增 runtime/reply_gate.py）。
- RUNTIME_SILENT_MAX 75 → **76**（calc_message_lag 的单条时间戳解析失败跳过，
  老消息格式不一是正常过滤）。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/reply_gate.py` | 新增，80 行（回复判定 4 函数） |
| `runtime/rank.py` | 追加 rank_hour_set/publish_rank（86 行）+ _tune/log import |
| `bot.py` | 1980 → 1939 行；6 个注入壳 + import 行 |
| `tests/test_quote_reply.py` | 窗口钉点改查 reply_gate.py 真身 + 段内取尾 |
| `tests/test_silent_errors.py` | RUNTIME_SILENT_MAX 75 → 76 |
| `tests/test_undefined_names.py` | TARGETS + runtime/reply_gate.py |

### 遗留

1. 剩余：main_loop(711) + _run_all(139) + 零散（_welcome_of 21/`_room_cmd_refused`
   11/profile_summary 6/defend_nuo 6/_next_interval 6/_max_interval 6 等）。
2. bot.py **1939 行**；距 <1500 还差 ~440。
3. 部署清单：runtime/ 新模块累计 **17 个**（+reply_gate）；其余同 3-17。

---

## 2026-10-01 · Phase 4A 刀 1–6 —— 运维后台前端接线（本地完成，未部署）

设计事件与决策见 [P4-运维后台前端设计.md](P4-运维后台前端设计.md)；
本节只记施工。**5 个页面壳全部接上 `admin.shell.page`，内联 `_CSS` 全部消灭。**

### 做法（每页一刀，模式完全一致）

1. 删整块内联 `_CSS`（样式漂移的根因），壳/CSS 三件套/app.js 由
   `admin/shell.py` 统一注入；页面只产出 content 片段。
2. 类名对齐共享组件（旧 → 新）：
   - `dim` → `text-dim`；`err` → `text-bad`；`badge ok/bad/warn/c1/c2/c3`
     → `badge--ok/--bad/--warn`（C1/C2/C3 颜色语义原样保留）
   - `msg ok/bad/warn/chg` → `msg--ok/--bad/--warn/--info`
   - 裸 `<button>` / `ghost` / `tiny` → `btn btn--primary` / `btn btn--ghost`
     / `btn--sm`；裸 `<table>` → `table table--compact`
   - config 页输入框 → 共享 `.input` / `.input--narrow`
   - dashboard 总览页 `<section>` 卡片 → 共享 `.card/.card__header/.card__body`
     + `.section-grid` 网格（原 9 卡布局不变）
3. 页面真特有的样式留最小 `extra_head`（prompts 的 `pre.reply/pre.diff`、
   config 的 checkbox 放大、备份差异页的 add/del/hunk 高亮色）。

### 特殊页

- **登录页不进壳**：`/static` 路由在会话检查**之后**，未登录拉不到共享 CSS
  ——样式必须自包含。dashboard.py 的 `_CSS` 改名 `_LOGIN_CSS`（登录所需子集），
  msg 类名顺手对齐。
- `_broken_config_page`（配置不合法横幅页）接壳，不再依赖 `config_ui._CSS`。
- `status_block` 是三页共用横幅，类名对齐后三页同时受益。

### 刀 6 收尾

- 钉点测试同步 1 处：`test_personas_ui.py` 的"零 script"断言 → **只许外链**
  `src='/static/app.js'`（CSP `script-src 'self'` 本来就允许同源外链），
  内联脚本与内联事件处理器仍然禁止。**教训：pytest 对长字符串 containment
  的"contained here"定位是误导的——onsubmit 根本不在页面里，真凶是 `<script`**；
  用 HTTP 链路 dump 页面数计数才定位到。
- deploy.ps1 `$need` 26 → **36 项**：+admin 5 个 UI 文件 + shell.py +
  static 四件（base/components/dashboard.css + app.js），防新增文件漏传；
  ASCII 0 字节 + PSParser 0 错。
- 临时脚本 `_tmp_cssdiff.py` / `_tmp_render_smoke.py` 用完即删。

### 验证（全绿）

- 全量 `pytest`（junitxml 统计）：**1536 收集 = 1530 通过 + 6 跳过，0 失败**
- 每页渲染冒烟（外壳/三件套/导航高亮/无内联大 CSS/主题切换 + 页面特有断言）
  全过；五页 + 差异页 + 备份详情页 + 登录页均覆盖。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `admin/shell.py` | 新增，统一外壳（三组侧边栏 + header + 主题切换） |
| `admin/features_ui.py` | 删 `_CSS`、换壳、类名对齐（刀 1） |
| `admin/personas_ui.py` | 同上（刀 2，补漏 4 处） |
| `admin/prompts_ui.py` | 同上 + `_tier_badge` 映射 + 双页（主页面/render_history）接壳（刀 3） |
| `admin/config_ui.py` | 同上 + status_block + render_backup_detail 接壳（刀 4） |
| `admin/dashboard.py` | 总览页卡片化接壳 + 登录页自包含 + broken 页接壳（刀 5a） |
| `admin/static/components.css` | +表格工具类（feat/grp/r/cat-row）+ .msg ul |
| `admin/static/base.css` | +`.content h2/h3` 正文标题层级 |
| `admin/static/preview.html` | 设计原型（不上线：.html 不在部署扩展名单） |
| `tests/test_personas_ui.py` | 零 script 断言 → 只许外链 app.js |
| `deploy.ps1` | $need 26 → 36 项 |

### 遗留（4B 候选，单独做）

1. **§3 功能配置简化**（以群为主视图 + 高频白名单 toggle）——刀 1a 只接线未动布局。
2. **§2.2 总览页信息层级**（状态卡格/快捷操作/告警条/群主视图）——刀 5a 只接线。
3. 部署：4A 只动了本地，**观察期（至 2026-10-02）过后**再单独部署目检；
   回滚锚点 = 部署前 tar 备份。

---

## 2026-10-01 21:29 · 线上掉线事故 —— P0 自锁首次拿到生产实锤（只读观测）

> 本节是**观测记录**，不含任何线上改动。P0 修法按用户指示仍只归档、不施工。

### 时间线（renew_token.log 原文）

| 时刻 | 事件 |
|---|---|
| 21:24:31 | `SKIP: token 未变化，无需写回` —— 常态，快路径静默 |
| 21:29:32 | `原始响应: HTTP 401 body {"error":"未授权"}` —— 只读接口 401 |
| 21:29:39 | `浏览器续期 FAIL: fetch status=401` → 退避 30 分钟 |
| 21:29:41 | `SESSION_DEAD`（业务未授权、首页通）→ `EMERGENCY: 已写 token.emergency` + 飞书已发 |
| 21:29:41 | bot.py：`⚠️ 检测到 token.emergency 信号，主动退出交给 entrypoint 重启` |
| 21:34:43 | `ESCALATE: 会话不可用 → 自动跑一次密码重登（headless_relogin.py）` |
| **21:37:43** | **`[relogin] SKIP: 另一个进程正在续期/重登，放弃本次登录`**（rc=1） |
| 21:37:43 | `撞验证码退避中，且业务调用未授权（会话真死）→ 保留 unauth 让巡警报` |

### 关键结论：自动恢复路径 100% 失效，且失效方式与 P0 文档完全一致

- 触发到放弃**整整 180 秒**（21:34:43 → 21:37:43），正是 `headless_relogin.py:220`
  的 `guard(..., timeout=180)` 上限 —— 父进程 `renew_token.py:851` 握着
  `guard("renew_token")` 又同步 `subprocess.run` 子进程，子进程永远拿不到锁。
- 计数变化：`SELFLOCK 18 → 19`、`ESCALATE 20 → 21`；`FAIL` 557、`EMERGENCY` 126
  不变（说明这轮没有再写 emergency，纯粹卡在锁上）。
- **这是 2026-09-30（8.8h）与 2026-10-01 10:20（13min）两次长时间掉线的同一根因**：
  不是登录本身失败，是"能救的那条路径被自己锁死"。

### 排除项：本次**没有** crash loop

- 21:32 实测 `token.emergency` **已不存在**（容器内与宿主机均无），bot.py 单进程
  （PID 835），真实启动次数 30 分钟窗口 = **1 次**。
- 所以"检测到 emergency 主动退出"只发生了设计中的那一次，entrypoint 重启后进入
  正常但**无会话**状态：`[sio] 连接失败: token 获取失败` 按 10/20/40/80/160/320s 退避。
- 会话心跳：`{"alive": false, "probe": "unauth", "bad_since": 1790861379.81}`（21:29:39 起）。

### 顺带修掉的两个**监控自身**缺陷（tools/active/watch_bot.ps1，非线上）

1. **`crash_loop` 误报**：探针原先 `grep -c P11:`，而一次启动会打 **15 行**含 `P11:`
   （身份 dump / Bot key / 配置 / 人设）。实测全量 `P11:`=30 而真实启动=2，于是健康的
   bot 被判成"5 分钟启动 15 次"。改用 `我的身份数=`（每次启动恰好 1 行，全量=2 与启动
   次数一一对应）。
2. **首轮即退**：原逻辑"首轮只要有已存在问题就 exit 通知"，导致 (a) 永远观察不到
   "恢复"这一最该通知的变化，(b) 故障期间 relay 重启会 exit→relaunch→exit 空转。
   改为**首轮只记基线**，仅在基线之后出现"新增/清除"时才退出。

> 陷阱记录：`edit`/`write` 工具会**剥掉 UTF-8 BOM**。本文件是含中文/emoji 锚点的
> `.ps1`，PS 5.1 无 BOM 会按 ANSI 读并损坏锚点 —— 每次编辑后必须用
> `WriteAllText($p,$t,[Text.UTF8Encoding]::new($true))` 补回，再校验前三字节
> `239,187,191` 与 `Parser::ParseFile` 零错。

---

## 2026-10-01 21:50 · **P0 修复并上线**（自动重登自锁）

> 屋主在本次掉线后拍板："先修 P0 再靠自动路径恢复"。本节记修复、部署与验证。

### 根因（一句话）

`guard()` 只有**一把锁文件**（`runtime/session.py` 的 `LOCK_FILE = .session.lock`），
`reason` 参数只是写进锁文件的一句备注 —— **根本没有"两把锁"**。

```
renew_token.py:851  with guard("renew_token", timeout=20):   ← 父进程持锁
  └ _renew_locked() → _try_password_relogin()
      └ subprocess.run([python, headless_relogin.py])        ← 同步等待，父锁不放
          └ headless_relogin.py  with guard("headless_relogin", timeout=180)
                                 ↑ 同一把锁 ⇒ 必然等满 180s ⇒ SKIP ⇒ rc=1
```

**`tests/test_renew_escalation.py:191` 原先断言"重登走的是另一把锁，不会打架"——
这条错误认知被测试钉死，正是 P0 长期没被发现的原因。已连同修复一并更正。**

### 改了什么（2 个文件）

| 文件 | 改动 |
|---|---|
| `renew_token.py:139-152` | 派生重登子进程时，env 里加 `DZMM_RELOGIN_UNDER_PARENT_LOCK=1`（显式声明"锁已由父持有"） |
| `headless_relogin.py:201-231` | `main()` 内加 `_guard()` 包装：该变量为 `1` 时用 `nullcontext(True)`（视为已持锁），否则照常 `guard()` |

⚠️ 只认**显式声明**，绝不默认放开 —— 真并发的两个登录仍会被锁挡住，
否则会互相作废轮换制 `refresh_token`。
`--manual-captcha` 那条 Popen 路径**不是** P0：父进程 Popen 后立刻返回并释放锁，
子进程是轮询等锁（毫秒级拿到），未改动。

### 部署方式（刻意**不用** `deploy.ps1 -Action deploy`）

`-Action deploy/upload` 会推送**整棵代码树**，而本地还压着 Phase 3-3 / 3-4 /
Phase 4A 等一批**故意未部署**的改动（既定纪律：先 S1 观察 24h 再上 Phase 3）。
故只做最小投放：

```powershell
# 1. 远端备份（回滚锚点，保留）
ssh ... 'mkdir -p /opt/dzmm/.p0fix_backup_20261001 && cp -p \
         /opt/dzmm/renew_token.py /opt/dzmm/headless_relogin.py /opt/dzmm/.p0fix_backup_20261001/'
# 2. 只推这两个文件
scp renew_token.py headless_relogin.py root@47.237.152.187:/opt/dzmm/
# 3. 校验哈希 + 语法（只读挂载写不了 __pycache__，故给个可写 pyc 前缀）
ssh ... 'docker run --rm -v /opt/dzmm:/src:ro -e PYTHONPYCACHEPREFIX=/tmp/pyc \
         python:3.11-slim python -m py_compile /src/renew_token.py /src/headless_relogin.py'
# 4. 重建镜像 + 重启
ssh ... 'cd /opt/dzmm && docker compose build dzmm-bot && docker compose up -d dzmm-bot'
```

**意外收益**：重建容器会重跑 `entrypoint.sh:20` 的 `headless_relogin.py`——
那条路径**不在任何父锁内、也不受 renew 的 30 分钟冷却限制**，
所以重启即完成一次干净重登，会话当场恢复。

### 验证（三层）

1. **单测**（新增 2 条，`tests/test_session_lock.py`）：
   - `test_relogin_child_does_not_self_lock_when_parent_declares_lock` —— **真的**持住
     锁，带变量调用 `main()`，必须立刻返回 0（而不是 180s 后返回 1）。
   - `test_relogin_child_still_acquires_lock_without_the_env` —— 反面：没有该变量时
     必须照常去抢 `headless_relogin` 锁（防止"默认放开"这种更危险的修法）。
   - 全量 `pytest`：**100% 通过，退出码 0**。
2. **线上 A/B 对照**（真实持锁进程 + `timeout` 包裹，容器内实测）：

   | 组 | 条件 | 结果 |
   |---|---|---|
   | A | 不带变量 | `rc=124`、**零输出** —— 被锁卡死（= 线上 180s SKIP 的同一模式） |
   | B | `DZMM_RELOGIN_UNDER_PARENT_LOCK=1` | `OK: 已登录（token 有效，锁内复查）`、`rc=0`、**立刻返回** |

3. **镜像内容核对**：`docker exec dzmm-bot grep -c DZMM_RELOGIN_UNDER_PARENT_LOCK`
   → `renew_token.py`=1、`headless_relogin.py`=1（确认修复真在运行的镜像里）。

### 本次掉线时间线与恢复

| 时刻 | 事件 |
|---|---|
| 21:29:39 | 会话真死 → `EMERGENCY` 写 `token.emergency` → bot.py 主动退出 → entrypoint 10s 后重启 |
| 21:34:43 | `ESCALATE` 自动密码重登（**旧镜像，注定失败**） |
| 21:37:43 | `SKIP`（整 180s）—— 排除了"真登录失败"，坐实 P0 |
| 21:42:44 | 第二次尝试被 30 分钟冷却挡下 |
| 21:45:10 | 修复上线，容器重建 → entrypoint 独立重登**成功** |
| 21:45:24 | `OK: 续期恢复正常（本轮共坏 16 分钟）`；心跳 `probe=ok`、`bad_since=null` |

**本次掉线 21:29:39 → 21:45:24，共 15 分 45 秒。**
监控脚本按下述修好后，于 21:46:55 独立捕捉到 `STATE CHANGE: RECOVERED` 并退出通知。

### 回滚方式

```bash
cp -p /opt/dzmm/.p0fix_backup_20261001/{renew_token.py,headless_relogin.py} /opt/dzmm/
cd /opt/dzmm && docker compose build dzmm-bot && docker compose up -d dzmm-bot
```

### 遗留

1. 本修复**尚未在"下一次会话真死"时被自然触发验证过** —— 但线上 A/B 已用真实持锁
   进程覆盖了同一条代码路径。
2. `tools/probes/probe_session_lock.py` 仍未进镜像（`/app/tools/` 不存在），
   应急手册已改为可用的内联探测写法。


---

## 2026-10-01 · Phase 4B §3 —— 功能配置页简化（本地完成，未部署）

按 [P4-运维后台前端设计.md](P4-运维后台前端设计.md) §3 实施：**以群为主视图 +
高频白名单 + 长尾折叠**。纯展示层重组 —— **表单字段名一个没变**，
`parse_features` / `features_diff` 与全部解析、端到端测试零改动。

### 新页面结构

1. **高频开关（全局）**：10 个白名单键（`WHITELIST`：at_only / slash_cmds /
   quote_reply / devil_roast / devil_cold / fortune / trend / recap /
   profile_scan / memory_report）直接铺在页首，全局三态整页只渲染这一次
   （同 name 的 radio 渲染两处会被浏览器当同一组互踩）。
2. **按群调整（CSS-only tabs）**：每群一个 pane（radio + 兄弟选择器切换，
   无 JavaScript 也能用；切换规则按群数动态生成、走 extra_head）。
   pane 内：摘要徽章（启用 X/68 · 本群覆盖功能 Y · 参数 Z · 群停用警示）
   + 白名单键的本群三态（带"现在 开/关（来源）"）+ **已覆盖参数表**
   （清空 = 取消覆盖）+「新增本群参数覆盖」折叠 + 该群强制复位按钮。
3. **高级折叠区**（原生 details）：全部功能矩阵（非白名单 58 键 × 全局+各群）、
   新增全局参数覆盖（全量）、按群集合（scopes）。
4. **参数只显示已覆盖**（§3.1.4）：全局与每群的参数区默认只列配置里
   真的写了覆盖的键。

### 防双渲染纪律（本次新踩的坑）

同名文本框（`gtune[key]` / `tune[cid][key]`）在"已覆盖表"与"新增折叠"
各渲染一次的话会提交两份 —— 解析取第一个，第二个只会误导。
两处都改为**新增折叠里跳过已覆盖键**，并用计数断言钉住
（`gtune[devil.base_prob]` 恰好 1 次）。

### app.js 两处脏检查修复（浏览器目检抓出来的）

1. **页签 radio 误入脏计数**：点页签就弹"有 N 项改动还没保存"。
   约定 `data-no-dirty` 属性（通用机制：纯 UI 控件标记），脏检查跳过。
2. **radio 组切换计 2**：同一组失选+新选各计一次，"保存这 2 项"
   其实只有 1 项语义改动 → recount 按 name 分组，同组变化只计 1。

### 验证（全绿）

- 冒烟 20 项（白名单/pane/双渲染计数/CSS 规则/无内联 JS）全过；
- `test_features_ui.py` 56 个全过（钉点：每键 `gfeat[key]` 在页、
  参数框 data-lo/hi、scopes、无内联脚本 —— 全部天然满足，零改动）；
- **浏览器目检两轮**（本地 8777 静态服务 + 预览 HTML）：
  4 页签切换 / 停用群红徽章 / details 折叠 / 暗亮主题 / 修复复检 4/4 通过；
- 全量 pytest：**1538 收集 = 1532 通过 + 6 跳过，0 失败**。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `admin/features_ui.py` | §3 重构：WHITELIST + 全局白名单表 + 群 pane + 高级折叠；docstring 更新 |
| `admin/static/app.js` | 脏检查：`data-no-dirty` 排除 + radio 按组计数 |
| 临时文件 | 预览生成/冒烟脚本用完即删；漏网的 `_tmp_personas_page_http.html` 已删 |

### 遗留

1. **§2.2 总览页信息层级**（状态卡格/快捷操作/告警条/群主视图）——下一刀（4B）。
2. 高级区搜索框（§3.1.3）：app.js 尚无表格搜索实现，留 4C。
3. 主题首屏跟随系统（prefers-color-scheme），非 bug；屋主要"默认暗色"再改。

---

## 2026-10-01 22:01 · 续期/恢复链专项优化（6 项，已上线）

> 起因：屋主问"token 续期还有什么需要优化的？"。只读排查出 8 项，屋主圈定 5 组落地。
> 本节记改动 + 验证。**P0-2 是本轮最重要的一个**：它解释了 21:29 那次为什么
> "两条恢复链同时失效"。

### ① 【P0-2】会话死的"第一症状"不触发自动恢复

**根因**：`runtime/mainloop.py` 的读消息失败是**裸 except**，只打一行日志 + `msgs = []`，
既不判 unauthorized、也不累加 `_unauth_streak`、更不拉起 `auto_recover.py`。
而 `auto_recover` 只在**生成/发送**异常路径可达，那条路径需要"先成功读到一条消息"
才会执行 ⇒ **会话一死它就永远不可达**，"两条恢复链"实际只剩 renew_token 那条
（而它当时正被 P0 自锁卡死）。

**证据**（这次掉线窗口逐字统计）：

| 日志 | `平台读消息失败` | `UNAUTHORIZED` | `触发自动登录恢复` |
|---|---|---|---|
| `bot.log`（含 21:29–21:45 掉线） | **16** | **0** | **0** |
| `bot.log.1`（正常时期，对照） | 865 | 86 | 81 |

**改法**：
- 新增模块级 `_is_unauth_error()`（与 `renew_token.UNAUTH_MARKS` 同口径），
  读失败与生成失败**共用同一套判定**；
- 新增 `run_main_loop` 内的 `_on_unauthorized()`，把"计数 → 微信告警（防重）→
  拉起 auto_recover"收敛成**唯一一份**实现，两条路径都调它（源码级测试钉死恰好 2 处）；
- 读失败命中 unauthorized 时设**短**冷却 `READ_UNAUTH_COOLDOWN_SEC = 120`，
  **刻意不套生成路径那个 1800 秒**：
  · 读很便宜，2 分钟一次谈不上刷风控（正常轮询是 0.8 秒一次）；
  · `captcha_cooldown_until` 只会被时间清掉、**没有"恢复即清"机制**，套 1800 秒
    会让 bot 在 auto_recover 修好之后**继续瞎半小时**；
  · 但仍必须设冷却 —— 否则本轮末尾的"恢复正常"分支会把 `_recovering` 当轮清零，
    下轮重复拉起（见下一条）。

**连带修的隐患（必须一起改，否则 ① 会制造新事故）**：
按群并行（P12-I）下主循环有 **4 个实例**，各有自己的 `_recovering`；而会话一死是
**4 个群同时读失败** ⇒ 4 个实例各拉起一个 auto_recover。而
`auto_recover._recover_locked()` 结尾**无条件 `restart_bot()`** ⇒ 变成连续重启 bot 4 次。
故新增 `_spawn_auto_recover()`：**进程级**去重（`AUTO_RECOVER_MIN_GAP = 300` 秒）。

### ③ auto_recover 白等 300 秒 + 误发告警

`guard("auto_recover", timeout=300)` 改成 `AUTO_RECOVER_LOCK_WAIT_SEC = 20.0`：
renew_token 重登期间最长持锁 300 秒，等满就是白等 5 分钟（那时 renew 那条链早干完了）。
并且**抢不到锁不再发微信** —— 4 个实例抢锁只有 1 个能进，另外 3 个各发一条
"另一个进程正在登录"是纯噪音；屋主要的是"恢复没恢复"的结论。

### ④ 一次"零工作"的尝试吃掉 30 分钟恢复窗口

`_stamp_relogin()` 原先在起子进程**之前**调用。21:29 事故里 21:34:43 那次尝试
**根本没开始登录**（P0 自锁，子进程被挡在 180 秒外打 SKIP），却把冷却打到 22:04，
于是 21:42:44 的第二次尝试直接被"冷却期内"挡掉。

改为**只在确认真试过之后**才计冷却：
- 子进程起不来 / 输出含 `SKIP:` ⇒ **不计**，下一轮可立刻重试；
- 跑满超时 / 子进程确实开始登录（不论成败）⇒ 计冷却，别把风控刷起来。

### ⑤ 退避文案与实际常量不符（误导排障）

`BROWSER_BACKOFF_SEC = 3600`（1 小时），但两处日志手写"30 分钟"。
21:29 排障就被它带偏过（我当时据此以为只退避 30 分钟）。三处改为常量插值 +
一条测试钉住"不许再手写"。

### ⑥ 日志轮转覆盖补齐

线上 `/etc/logrotate.d/dzmm-bot` 的 pattern 是 `/opt/dzmm/bot.log /opt/dzmm/logs/*.log`，
而 **`/opt/dzmm/logs/` 目录根本不存在** ⇒ `renew_token.log`(289KB) 与
`check_health.log`(456KB) **永不轮转**（append-only、无界增长）。
现覆盖 `bot.log / renew_token.log / check_health.log / monitor_recover.log`，
`copytruncate`（这些文件都被常驻进程 append 持有，用 create 会换 inode ⇒ 日志静默丢失）。
配置存进仓库 `logrotate.dzmm-bot` 以便复现；旧配置备份在
`/etc/logrotate.d/dzmm-bot.bak_20261001`，`logrotate -d` 干跑校验通过。

### ⑦ 监控自身的假阳性

renew 抢不到锁时会写 `probe="locked"`（**人工过验证码**期间最长 10 分钟是正常的），
而 `watch_bot.ps1` 按 `probe != 'ok'` 一律判 `session_bad` ⇒ 每次人工过验证码都误报，
真正该报的 `unauth`/`error` 反被噪音淹没。改为 `ok|locked` 都算正常。

### 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `runtime/mainloop.py` | `_is_unauth_error` / `_spawn_auto_recover`（进程级去重）/ `READ_UNAUTH_COOLDOWN_SEC`；`run_main_loop` 内 `_on_unauthorized`；读失败路径接入 |
| `auto_recover.py` | `AUTO_RECOVER_LOCK_WAIT_SEC = 20`；抢不到锁不再发微信 |
| `renew_token.py` | `_stamp_relogin()` 挪到确认真尝试之后（超时分支显式补计）；三处退避文案改常量插值 |
| `tools/active/watch_bot.ps1` | `probe='locked'` 不再判为 `session_bad` |
| `tests/test_unauth_recovery_paths.py` | 新增 10 项回归测试 |
| `logrotate.dzmm-bot` | 新增（服务器配置入仓） |
| `docs/PLATFORM-OPTIMIZATION.md` | S1 三条 + logrotate 标记已修；新增 4 条问题行 |

### 验证

- 新增 10 项回归测试全过；**全量 pytest 通过，退出码 0**（含前一轮 P0 的 2 项）。
  其中两条专门防"更危险的修法"：`_is_unauth_error` 不许误判普通网络错误；
  `_spawn_auto_recover` 去重不许变成"只救一次"。
- 测试期间抓到自己的一个真错误：源码断言用裸 `"平台读消息失败"` 做锚点，命中了
  文件顶部新写的 P0-2 注释而非代码 —— 已改用日志语句做锚点（注释里写明了这个坑）。
- 镜像内容核对：`_is_unauth_error`=3、`_on_unauthorized`=3、`AUTO_RECOVER_MIN_GAP`=2、
  `AUTO_RECOVER_LOCK_WAIT_SEC`=2、`DZMM_RELOGIN_UNDER_PARENT_LOCK`=1，全部命中。
- ⚠️ **① 的"会话真死"路径未做线上主动验证** —— 那需要故意把生产会话打死。
  目前由单元测试（判定口径 + 两处接线计数 + 去重行为）+ 镜像内容核对覆盖。

### 回滚锚点

`/opt/dzmm/.opt_backup_20261001_2158/`（含 `runtime/mainloop.py`、`auto_recover.py`、
`renew_token.py`、`headless_relogin.py`、`docker-compose.yml`）。

### 遗留

1. **Phase 2 Step 2-5**：≥24h 无续期错误后撤掉 `.dzmm_token.json` / `.dzmm_login.json`
   两行老挂载。
2. `token.emergency` 仍在 `/app/token.emergency`（不在任何挂载上，容器重建即丢）——
   本轮未动，属 §2-3A。
3. `monitor_recover.py` 的首页探测曾三周因 urllib 默认 UA 被 WAF 挡（一直记 `首页=418`，
   6570 次）而**静默失效**；现日志已恢复 `首页=OK`，但"连续 N 次 418 就告警"仍未加，
   下次静默失效还是没人知道。

---

## 2026-10-01 · 人设世界观泄漏：上班群为什么说出"认证群"的话（**只记录，未修改**）

> 屋主问："工作群为什么发送了认证妹子群的人设欢迎语？"
> 结论：**不是欢迎语被误发，是"人设世界观"泄漏**。屋主当场决定**只记录、先不改**。

### 现场（bot 在上班群 f4ec4c21 的真实输出）

```
22:01:53 📥 webhook 收到 [f4ec4c21] @若瑾 你会给我踢了嘛
22:01:57 💬 🤖 踢你干嘛，我又不是管理员。认证的事找糯糯姐，我不管这个！！❤️
22:02:36 📥 webhook 收到 [f4ec4c21] @若瑾 糯糯是誰
22:02:38 💬 🤖 糯糯姐是这儿的老板，群和玩法都是她弄的。认证那事也是她管，我插不上手！！❤️
```

注意"**这儿**的老板"——它把认证群的组织架构直接套到了上班群上。

### 根因链

1. `personas/ruojin.py:9` 记着屋主当初的口径：
   **"人设基本参考小小糯，名字改为若瑾，同时纯 S 属性、异性恋。"**
   ⇒ 若瑾的提示词 = **认证群「小小糯」的 `CMD_PROMPT`** + 后追加的 `RUOJIN_EXTRA`。
2. `personas/__init__.py:302-312` 的 `apply_persona()` **只做两件事**：
   换名字（`DEFAULT_NAME_FULL/SHORT` → 本群人设名）+ 追加 `extra`，**其余原样保留**。
3. `RUOJIN_EXTRA` 的覆盖声明（`personas/ruojin.py:26-28`）只列了
   **外貌、性格、亲密观、说话方式**（"软糯撒娇""尾音～""叫姐姐/哥哥""被引导""百合"）
   —— **从未覆盖"糯糯姐是谁""这个群是哪个群""谁说了算"这类世界事实**。

### 实测量化（本地拼出若瑾的最终系统提示词）

| 关键词 | 出现次数 |
|---|---|
| `糯糯姐` | **10** |
| `糯糯` | 15 |
| `小小糯` | **0**（已被换名） |
| `若瑾` | 7 |
| `认证` | **0** |

**「认证」在提示词里出现 0 次** —— 所以"认证那事也是她管"是模型基于
"糯糯姐是创始人 / 你是她的私人秘书"**自己推**出来的。
**泄漏的是世界观，不只是几个字。**

名字换得掉、事实换不掉。仍留在提示词里、且**每个字都没被覆盖声明作废**的句子：

- 开篇第一句：`你是「若瑾」，19岁，糯糯文化有限公司创始人糯糯～的私人秘书。`
- `【性格】…对喜欢的女孩子（尤其糯糯姐和群里的姐姐们）主动黏人。`
- `- 叫糯糯叫"糯糯姐"；自称"若瑾"（不要自称"糯糯"，糯糯是糯糯姐）`
- few-shot 示例：`你: 我是若瑾～，糯糯姐的私人秘书呀，有什么事要我做嘛～`
- `标着"记忆"的那一段…记忆里替糯糯姐留的待办，只有糯糯姐问的时候才能讲`

### 排查中排除的三条路（免得下次重复走）

| 假设 | 证据 | 结论 |
|---|---|---|
| 认证群入群欢迎语被误发到上班群 | `grep -c 姐姐加入 /opt/dzmm/bot.log` = **0**；`WELCOME_TEXT`（含"本群是妹子认证群"）一次都没发出去过 | ❌ 排除 |
| 若瑾的欢迎语/自我介绍配错了 | `PHRASES['ruojin']` 65 条含 `welcome_text_plain`（`🎉 欢迎 {name} 加入。我是{me_full}。`）；`/你是谁` 走 `p("intro_text", cid)` = "我是若瑾。这里我说了算" | ❌ 两条路径都对 |
| 定时问候/主动冒泡把软糯人设发出来了 | 线上 config 里上班群 `greet_morning/noon/evening`、`rank_push`、`profile_scan` **全部显式 false**；`bot.log` 里 `早安`=0、`晚安`=0 | ❌ 排除 |

### 影响面（比这两句大）

`_call_llm` 对**每一次** LLM 调用都套 `apply_persona`，所以凡走模型的地方都带这套世界观。
另有三个提示词**硬编码"你是小小糯～"**、内容同样含认证群世界：
`GREETING_PROMPT`（"给群里的姐姐们发定时问候"）、`CUTE_ROAST_PROMPT`
（"自称小小糯…糯糯"糯糯姐""）、`DEFEND_PROMPT`（"维护你的主人糯糯姐"）。
它们会被 `apply_persona` **换名**，但**内容照旧**——上班群这几个开关目前是关的，所以还没炸出来。

> 附带澄清：`cute_roast` **不是**一个真实 Feature（全仓 `.py` 里搜不到这个键），
> 排查时不要把它当开关查。可爱吐槽走的是 `devil_roast` 分支里的 `CUTE_ROAST_PROMPT`。

### 候选修法（屋主 2026-10-01 决定**先不动**，留档）

- **A（推荐，最轻）**：在 `personas.ruojin.extra` 里补一段**世界事实覆盖** ——
  明确写"你不认识糯糯姐、这里不是认证群、认证/糯糯文化与你无关、
  这个群叫《上班如上坟，下班如还魂》且你说了算"。
  ⚠️ **便利条件**：`config.json` 的 `personas.<key>.extra` 会**覆盖**代码默认值，
  且线上 `P12: 热加载=启用`（改完配置/提示词就地生效）
  ⇒ 这条**不用部署、不用重启**就能生效；代码仓的 `personas/ruojin.py` 再同步改一份保持一致。
- **B（结构性）**：不再拿小小糯的 `CMD_PROMPT` 当底座，给若瑾一份**独立基础提示词**。
  从根上断开，但 `brains._call_llm` 目前写死用全局 `CMD_PROMPT`，要动提示词分层，改动面大。
- **C**：只记录（本次选择）。

### 复盘：为什么会有这类问题

`RUOJIN_EXTRA` 的覆盖声明是**按"维度"写的**（外貌/性格/亲密观/说话方式），
而泄漏发生在**没被列进任何维度的"事实/世界设定"**上。
换句话说：**"基本参考小小糯"这句话，当初只被理解成"口吻和属性参考"，
没有被理解成"连同她的世界观一起继承"** —— 而提示词是整体继承的。
下次新增派生人设时，覆盖声明应当**显式包含"世界事实"这一类**。

---

## 2026-10-01 23:14 · 上班群「同一人 90 秒回复冷却」已关闭（配置改动，无部署）

> 屋主问："`@若瑾 我是糯糯姐` 这个怎么没回复呀？"
> 结论：**不是故障，是 P12-BQ 特意配的冷却**。屋主当场决定**关掉**。

### 现场

```
23:02:47  👋 @消息: @若瑾 糯糯不是你的主人嘛
23:02:49  💬 🤖 糯糯姐是老板，不是我主人。这两个词差得远了，哥！！❤️     ← 答了
23:03:12  ⏳ @ 冷却中（还剩 67s，同一个人）→ 这条不接
23:03:12  📥 @若瑾 我是糯糯姐                                          ← 被挡
```

`90 - 23 = 67` 秒，与 `chat.reply_cooldown = 90` 严丝合缝。

### 出处

`tools/active/set_chat_tuning.py:47-58`（P12-BQ 参数组），注释原文：
> `chat.reply_cooldown: 90` —— **"同一个人连着 @ 她不必条条秒回（真人不会）；90 秒够她把话说完"**

同文件记着量化依据：**"上班群若瑾 15 条 vs 真人 515 条：中位长度 116 vs 7 字、
空行分段 93% vs 7%、❤️ 80% vs 1%、派活收尾 67% vs 2%"** —— 当初是为了让她更像真人。

⚠️ 这个值是**脚本直接写 config** 的，所以 `config/config_audit.log` 里**查不到**
（审计日志只记运维界面的改动）。排查"这值是谁改的"要看 `set_chat_tuning.py` 这类脚本，
或 `config/config_backups/`。

### ⚠️ 顺带发现一处文档与行为不一致（**未修，仅记录**）

| 位置 | 说法 |
|---|---|
| `runtime/at_chat.py:158`（`_chat_cooling` docstring） | "只是'这一轮不接话'，**不是把消息从队列里抹掉**" |
| `runtime/mainloop.py:512-513`（调用处注释） | "消息照旧被消费、**`last_id` 照旧往前走，不会反复重放**" |

**实测是后者 = 永久丢弃**：冷却 23:04:17 就到期，但到 23:05:19 那条
`@若瑾 我是糯糯姐` 仍然没有任何回复。
（本次按屋主选择只关冷却，**没有**去动这两处注释；记在这里免得上当。）

### 边界（下次先看这三条）

1. **只挡 @ 闲聊**。指令类不受影响 —— 22:46:26 的 `@若瑾 注册` 正常答了。
2. **只有上班群设了这个 key**。认证群 / 玫瑰房 / 茶水间都没设 ⇒ 用代码默认 `0`
   （`runtime/features.py:216`：默认 0，注释"默认必须每条都答（零回归）"）。
3. 冷却按 **(群, 人)** 记在**内存**里（`_CHAT_LAST_REPLY[cid|uid]`），**重启即清空**。

### 做了什么

一次性脚本走与 `set_chat_tuning.py` **完全相同**的写入路径
（`config_store.save()` = 校验 + 备份 + 原子写 + reload），只改这一个键：

```
23:14:24 P12: 热加载 参数 · f4ec4c21|chat.reply_cooldown : 90 → 0
23:14:24 P12: 热加载完成 来源=auto 改动=1 项 生效 功能=98 参数=15 集合=1 人设=1 prompts=0
```

**没有部署、没有重启**（配置热加载）。用完即删（容器内 / 宿主机 / 本地三处都清了）。

### 回滚

```bash
# 改前手动快照（最稳）
cp -p /opt/dzmm/config/config.json.bak_before_cooldown_231410 /opt/dzmm/config/config.json
# 或改为 90 再存一次
```

⚠️ `config_store` 自己也留了 `config_backups/config-20261001-231424.json`，
但它与改动**同秒**落盘 —— 取值前先 `diff` 确认是改前还是改后，别想当然。

---

## 2026-10-01 23:26 · 监控新增**自动修复**（权限扩展：只读 → 可自救）

> 屋主原话：**"你的监测新增一条，如果发现异常或续期失败这种，你就自己修复。"**

这是本项目**第一次**把"动生产"的权限交给脚本，所以下面把纪律写在最前面。
实现落在 `tools/active/watch_bot.ps1` 的 `-AutoRepair`（**默认关**，显式 opt-in
—— 别的调用方可能依赖它是严格只读的）。

### 六条纪律（写死在代码里，不是口头约定）

1. **绝不删锁。** 应急手册写明"锁文件存在 ≠ 锁被持有"（flock 刻意不删文件）。
   盲删会破坏"唯一登录者"约束，让两端并发登录把**轮换制** refresh_token 互相作废
   —— 那比原故障更糟。
   ⇒ 修复一律**走脚本本身**（`renew_token.py` / `headless_relogin.py`）：
   它们自己会正确地抢锁、抢不到就 SKIP。**互斥交给唯一权威实现，本脚本不越权。**
2. **不重启还活着的 bot。** 会话类问题只走 renew → relogin；只有容器**真的不在**
   （`container_down`）或**真在崩溃循环**（`crash_loop`）才 `docker compose up -d`。
   否则一次误判就是一次无谓停机。
3. **限流**：同问题连续 `RepairGraceRounds`(=3, ≈6min) 轮才动手；
   两次修复间隔 ≥ `RepairCooldownSec`(=300s)；一小时最多 `MaxRepairsPerHour`(=3) 次。
   修复风暴比原故障更糟，而且会把平台风控刷起来。
4. **两个"别动"哨兵**：
   - 容器里 `/app/bot.paused` —— 有人正在 noVNC 过验证码，动手会把人家搅黄
   - 宿主机 `/opt/dzmm/.no_autorepair` —— **屋主主动停服维护时先建这个文件**。
     否则脚本会把你故意停掉的容器又拉起来（compose 是 `restart: unless-stopped`，
     手动 `docker stop` 是"粘"的，被自动 `up -d` 顶掉就违背你的意图）
5. **留痕**：每个动作追加到 `tools/active/watch_bot.repairs.log`，报告里也逐条列出。
6. **修不好就退出通知**（`exit 1`）交给屋主 —— 不自作主张反复试。

### 决策表

| 问题 | 自动修？ | 动作 |
|---|---|---|
| `session_bad` / `heartbeat_stale` / `cred_write_failed` | ✅ 满 3 轮后 | `renew_token.py` → 仍坏则 `headless_relogin.py` |
| `renew_failure` / `renew_relogin_selflocked` / `renew_emergency` / `renew_escalate` | ✅ 满 3 轮后 | 同上 |
| `container_down` / `crash_loop` | ✅ **无门槛**（客观事实，无判断空间） | `docker compose up -d dzmm-bot` |
| `loop_error_traceback` / `loop_error_datetime` / `loop_error_rollback` | ❌ 只报告 | 重启治不好代码 bug，也不该拿重启去试 |
| `stuck_no_outbound` | ❌ 只报告 | 语义含糊（可能只是这段没人 @ 她） |
| `session_unreadable` / `ssh_fail` | ❌ 只报告 | — |

修好后**继续盯着**（不吵屋主）；修不好才退出通知。

### 为什么门槛是 3 轮（≈6 分钟）而不是更快

容器**内置**的恢复路径在会话坏满 **5 分钟**时才升级到密码重登
（`renew_token.RELOGIN_MIN_BAD_MIN = 5.0`）。本脚本是**兜底、不是主力** ——
门槛设在它之后：先让它自己救，只有它没救回来我才动手。
抢在它前面只会和它撞在同一把锁上。

### 验证

**1) `-SelfTest`：12 项全过**（用合成输入跑决策逻辑，不碰生产、不发 ssh）

```
[self-test] 决策逻辑（不碰生产）  grace=3 cooldown=300s cap=3/h
  [PASS] 无问题 -> 不修
  [PASS] 不可修复类(loop_error_traceback) -> 不修
  [PASS] 会话问题首次出现 -> 不修（观察）
  [PASS] 会话问题持续 3 轮 -> 修
  [PASS]   修复动作 = renew_token.py
  [PASS] 容器不在 -> 立即修（无 grace）
  [PASS]   修复动作 = docker compose up -d
  [PASS]   容器问题**不**走 renew_token
  [PASS] 冷却期内不重复修
  [PASS] 一小时达上限 -> 停手交人
  [PASS] 哨兵 bot.paused 存在 -> 不修
  [PASS] 哨兵 .no_autorepair 存在 -> 不修
  [self-test] PASS=12 FAIL=0
```

> 自检**当场抓到我自己一个 bug**：SelfTest 里只设了 `$DryRun` 却没设 `$AutoRepair`，
> 于是 `Invoke-Repair` 在第一个门槛就返回、7 项假失败。这也说明"能在真机上跑的自检"
> 比"读代码确认"可靠。**这类自检脚手架自身的坑要记一笔。**
>
> 紧接着又抓到**第二个**：SelfTest 在 `$DryRun` 下仍会调 `Write-Audit`，把 8 行**测试噪声
> 写进了真实的 `watch_bot.repairs.log`**。审计日志必须只含"真的动过生产"的记录，
> 否则日后翻它反而会被误导。已改为自检写临时文件；并给 DryRun 的条目加 `[DRYRUN]` 前缀。
> （教训：**"只记录不执行"的 DryRun 仍会写文件** —— DryRun 不等于零副作用。）

**2) 修复命令在生产上实测**（健康状态下，确认安全且返回码正确）

| 命令 | 结果 |
|---|---|
| `test -f /opt/dzmm/.no_autorepair; echo GUARD=$?` | `GUARD=1`（不存在 = 正常） |
| `test -f /app/bot.paused; echo PAUSED=$?` | `PAUSED=1` |
| `docker exec ... python renew_token.py` | rc=0（快路径静默，符合设计） |
| `docker exec ... python headless_relogin.py` | `OK: 已登录（token 有效，锁内复查）`，rc=0 —— **已登录时是安全空操作** |

### ⚠️ 能力边界（必须知道的局限）

- **只在我这个会话活着时有效**。监控是 DSH 后台任务，不是服务器上的常驻守护；
  会话结束 = 监控和自动修复一起消失。
  （屋主此前明确"不是让你装到服务器"，所以没有做宿主 cron。）
- **只做"让它重新活着"这一件事**：不碰配置、不碰代码、不改业务逻辑。
  那类改动仍然必须先问过屋主。
- 会话类的最大自救时长约 6 分钟（门槛） + 最长 240s（重登）+ 15s（复探）≈ **10 分钟**。

### 本轮改动文件

| 文件 | 改了什么 |
|---|---|
| `tools/active/watch_bot.ps1` | 新增 `-AutoRepair` / `-DryRun` / `-SelfTest` / `-RepairGraceRounds` / `-RepairCooldownSec` / `-MaxRepairsPerHour` / `-RemoteDir`；新增 `Invoke-Repair` / `Get-LightIssues` / `Test-IssueRepairable` / `Get-IssueGrace` / `Write-Audit` / `Invoke-SshCmd`；主循环在"退出通知"之前先自救 |

---

## 2026-10-02 00:48 · 自动修复**首次实战**：暴露我两个 bug（已修）

> 上一节刚上线的自动修复，一小时内就撞上真故障 —— 而且是它**没修好**。
> 这次实战的价值远大于自检：它暴露的是 `-SelfTest` 覆盖不到的"**分支选错**"。

### 事故时间线

| 时刻 | 事件 |
|---|---|
| 00:46:17 | 只读接口 401 → 浏览器续期 FAIL → `退避 60 分钟` → `SESSION_DEAD` → `EMERGENCY`（计数 126→**127**） |
| 00:46:24 | bot.py 消费 `token.emergency` 主动退出；00:46:25 重启；**00:47:12 又退一次** |
| 00:48:02 | 我的监控判定 `crash_loop` → **走了容器分支** → `docker compose up -d dzmm-bot` |
| 00:48:05 | 输出 `Container dzmm-bot Running` ⇒ **纯空转，什么也没做** |
| 00:48:22 | 复探仍 `unauth` → 判定"修复失败" → `exit 1` 通知屋主 |

### 但线上其实**自己好了** —— 靠的是我此前没算到的**第三条恢复链**

```
2026-10-02 00:46:41 巡检告警已发: 会话已失效（业务调用也未授权），已触发重登（探针 unauth，0 分钟前）
/opt/dzmm/secrets/token.json   mtime = 00:47:12   ← 重登写回新 token，会话就此恢复
```

⇒ 会话恢复的真实功臣是 **`check_bot_health.py` 的主动重登**（`_trigger_relogin()`），
不是 renewal 的 ESCALATE、也不是 bot 的 `auto_recover`。

**这是一条我此前漏掉的恢复链。** 已知的三条：

| # | 触发者 | 机制 | 本次是否生效 |
|---|---|---|---|
| 1 | `renew_token.py` | 401 → 浏览器刷新 → ESCALATE 密码重登 | ❌ 浏览器 401，且重登被 5 分钟门槛挡下（"会话才坏 0 分钟"） |
| 2 | **`check_bot_health.py`** | 巡检发现会话失效 → `_trigger_relogin()` 后台拉 `headless_relogin.py` | ✅ **就是它救的** |
| 3 | `bot.py` → `auto_recover.py` | 生成/发送路径遇 unauthorized | ➖ 本次未触发（bot 在读消息阶段就死了） |

⚠️ 第 3 条的"不可达"是我在 10-01 修过的（读消息失败路径没接 unauthorized 判定）。
本次它仍未触发，值得后续复查：**会话死时 bot 到底走到哪一步**。

### 我的两个 bug

**bug 1（严重）：`crash_loop` 归类错了，导致修复完全空转**

原代码是 `if (容器问题) {...} else {会话阶梯}` —— **二选一**。
`crash_loop` 被归到"容器问题"，于是只跑了 `compose up -d`；而容器本来就在跑，
这条命令是幂等空操作。**根因（会话死了）从头到尾没被碰过。**

`crash_loop` 的绝大多数成因恰恰是会话死：bot.py 见到 `token.emergency`（会话死时写的）
就主动退出、交给 entrypoint 重启。**它是症状，不是病。**

修法：
- `crash_loop` 移进 `RepairableSession`；`RepairableContainer` 只剩 `container_down`
  （只有"容器真的不在"才是重启能解决的事）。
- 动作顺序改为**先根因后表象**：会话阶梯 → 再看容器是否真的不在。

**bug 2：复探读的是**陈旧**状态**

`data/session_state.json` 由 renew 循环**每 5 分钟**才写一次。我在修复后 15 秒读它，
读到的是**修复前**的值（00:46:24 的 unauth），而真实会话 00:47:12 就好了。

修法：判定前**强制刷新一次状态** —— 跑一次 `renew_token.py` 让它落地，再读。
容器分支额外等 30 秒（让 entrypoint 的 `headless_relogin` + bot 启动跑完）。

### 验证

`-SelfTest` 从 12 项扩到 **16 项，全过**，新增的 4 项专门钉这次的教训：

```
  [PASS] crash_loop+session_bad 首轮 -> 仍未满 grace，不动
  [PASS] crash_loop+session_bad 满 grace -> 修
  [PASS]   动作里必须有 renew_token.py（修根因）
  [PASS]   动作里不许出现 compose（那是空转）
```

### 复盘（两条通用的）

1. **"执行成功"不等于"修好了"。** 我的容器分支 `rc=0`、命令正常返回，
   但**毫无作用**。如果只看返回码，会以为修复生效。
   ⇒ 判断修复是否有效，必须看**目标状态**（会话探针），不能看**命令返回码**。
2. **监控自身的 bug 只有真故障才暴露得出来。** `-SelfTest` 能覆盖"该不该修"，
   但覆盖不到"**该修哪个**" —— 那需要真实的故障组合。
   ⇒ 自动修复上线后必须有一段"人工盯梢期"，不能当它已经可靠。

### 本次改动

| 文件 | 改了什么 |
|---|---|
| `tools/active/watch_bot.ps1` | `crash_loop` 移入会话类；动作顺序改为"先根因后表象"；复探前强制刷新状态；容器分支多等 30s；SelfTest +4 项 |

### 回滚

无需回滚（本次只改了监控脚本，未动生产）。生产在 00:47:12 已由内置链自愈，
00:48:55 人工复探确认 `probe=ok`。

### 附带：00:53 又暴露**第四个**监控缺陷（已修）

修完上面两条后重启监控，它 2 分钟后又退了一次：

```
[watch] round 2 -> STATE CHANGE: RECOVERED  CLEARED: crash_loop   (00:53:22)
```

而 `crash_loop` **根本没被修过** —— 它从第 1 轮基线里就有，是**启动残留**：
监控 00:51:19 启动时，5 分钟窗口里还留着 00:46:35 / 00:47:28 那两次重启；
窗口一滑过去（00:52:28 之后）它就"自动消失"了。**白报一条恢复通知并把监控退掉。**

**修法**（思路比代码重要）：任何**新**问题出现时 `newKeys>0` 会**立刻退出通知**，
所以能走到"只有 `goneKeys`"这一步的，**必然都是基线里就带着的**问题。
⇒ 规则：**只报清除、且被清除的每个问题都只存在过 1 轮 → 判为启动/窗口残留，
不作为恢复通知，继续盯着。**
（存在过 ≥2 轮的基线问题真的恢复了，仍然照常通知。）

抽成纯函数 `Test-FlashOnly` 以便自检直接钉住（这段最容易差一），自检从 16 项扩到
**22 项，全过**：

```
  [PASS] 清除且只开过 1 轮 -> 判为残留，不通知
  [PASS] 清除且开过 2 轮 -> 是真恢复，要通知
  [PASS] 有新问题 -> 绝不是残留
  [PASS] 什么都没变 -> 不是残留（也就不用通知）
  [PASS] 两个一起清、其中开过 3 轮 -> 要通知
  [PASS] 两个一起清、都只 1 轮 -> 残留
```

### ⚠️ 一个必须正视的模式

**自动修复上线 30 分钟内，在监控这一层连带发现 4 个缺陷**：

| # | 缺陷 | 是谁发现的 |
|---|---|---|
| 1 | `crash_loop` 数 `P11:` 行（一次启动 15 行）⇒ 健康 bot 判成崩溃循环 | 人工核对 |
| 2 | 首轮即退 ⇒ 永远看不到"恢复"，故障期 relay 空转 | 人工推理 |
| 3 | `crash_loop` 归类到容器 ⇒ 修复空转，根因会话没被碰 | **实战** |
| 4 | "只闪一轮"的窗口残留被当成恢复通知 | **实战** |

前两个靠人想出来，后两个**只有真故障才暴露得出来**。结论很直白：
**"监控/自愈"这一层是整套系统里测试覆盖最差的代码** —— 它不在 pytest 里，
平时又不产生可见输出。上这类东西之后必须有一段**人工盯梢期**，
不能因为"自检 22 项全过"就当它可靠。

---

## 2026-10-02 09:45 · **找到周期性掉线的真正病根并治本**（已上线）

### 隔夜数据把一个规律摆了出来

```
10-01 01:32:51 / 02:34:07 / 03:35:22 / 04:41:37 / 05:47:52 / 06:54:07 / 08:00:23  EMERGENCY
→ 08:18:43 OK: 续期恢复正常（本轮共坏 529 分钟）
```

**7 次会话死亡，几乎每小时一次**，其中一次坏 8.8 小时。三个统计（全量日志）：

| 指标 | 次数 |
|---|---|
| 浏览器续期 **FAIL** | **24** |
| 浏览器续期**成功** | **1** |
| 从浏览器**抄回** token | **1** |

### 病根：`refresh_token` 轮换造成的**单向缺口**，而抄回只在"发消息时"发生

- 平台 `refresh_token` 是**轮换制**；常驻页面的 SPA 自己也会刷，刷完把**新的**留在
  浏览器里，我们 JSON 那份就成了**旧的**（P12-BK 早就写明，但只修了一半）。
- `_harvest_token()`（走 Playwright 从页面抄回）**只在发送路径**被调
  （`runtime/sender.py` 的 `_sync_cookies`，注释"先看浏览器那边有没有更新"）。
- ⇒ **群一安静 / 深夜没人说话 ⇒ 不发送 ⇒ 不抄回 ⇒ JSON 越来越旧**
  ⇒ 续期 401 ⇒ 浏览器也刷不动（它手里的 refresh_token 同样过期）⇒ 判"会话真死"
  ⇒ emergency 重启，而唯一能救的密码重登被 `RELOGIN_MIN_BAD_MIN` 卡着
  ⇒ **每次固定坏 5 分钟**（02:46:33→02:51:34、05:46:44→05:51:45，恢复时长恰等于门槛）。

### ⚠️ 顺带否掉一条"纸上正确"的旧建议

`tools/probes/probe_cookie_freshness.py` 写着：
"⇒ 续期前应**先从浏览器 profile 抄回 cookie**，而不是直接判死重登。"

**实测这条路不可行**：探针假设 cookie 在 sqlite 里是 `base64-...` 明文，而
Chromium 在 Linux 上**加密存储** cookie —— 读出的是
`Expecting value: line 1 column 1 (char 0)`（四个 profile 全一样）。
**这大概就是它躺了这么久没被实施的真正原因。**
能用的是 Playwright 那条（会解密），机制早就在，**只是调用时机不对**。

### 治本 + 止血（都已上线，2026-10-02 09:44:48）

| # | 改动 | 文件 |
|---|---|---|
| **A 治本** | 主循环 `is_primary` 块里**定时抄回**（每 `TOKEN_HARVEST_INTERVAL = 600` 秒），不再只在发送时抄。包在 try 里，异常不影响收发；`_harvest_token()` 内部仍有 60 秒节流、抢锁 `timeout=0`（拿不到就跳过） | `runtime/mainloop.py` |
| **B 止血** | `RELOGIN_MIN_BAD_MIN` **5.0 → 2.0**：原值的代价是**固定 5 分钟掉线**（恢复时长恰等于门槛，说明瓶颈是门槛而不是登录本身 —— 登录只要几秒） | `renew_token.py` |

`_harvest_at = [0.0]` ⇒ 启动后**第一轮就会抄一次**（`time.time() - 0` 必然超过间隔），
所以容器重建后立刻补一次，不必等 10 分钟。

### 验证

- 新增 3 项回归测试（`tests/test_token_harvest.py`）：定时抄回接线、必须在
  `is_primary` 块里、门槛已缩短。**全量 pytest 退出码 0。**
- 镜像内容核对：`TOKEN_HARVEST_INTERVAL`、`sender._harvest_token()`、
  `RELOGIN_MIN_BAD_MIN = 2.0` 全部命中。
- 部署时 entrypoint 报 `token 失效，开始 headless 登录... OK` —— 说明当时手里那份
  **确实已经失效**，正好是这条链要解决的问题的现场。

> ⚠️ **真正的验收是"接下来几小时不再出现 EMERGENCY"**，不是测试通过。
> 上线时间 09:44:48，需观察到至少跨过一次 token 过期周期（约 1 小时）。
> 所以这次**必须回访**，不能部署完就当完成。

### 顺带修的监控第 5、6 个缺陷

| # | 缺陷 | 现象 |
|---|---|---|
| 5 | **门槛与"退出通知"时序冲突** | `Invoke-Repair` 说"未满 N 轮"，紧接着 `$changed` 判定就把监控退掉 ⇒ **门槛永远等不到 ⇒ 会话类自动修复从来没真正跑过**；而 relay 重启还会清空 `IssueFirstSeen`，门槛每轮从 1 重来。修法：只是在等门槛时**留下来等**，不退出。 |
| 6 | 容器类 grace 0 ⇒ **会把我自己的部署当故障** | `docker compose up -d` 重建容器有 ~15 秒 `docker ps` 看不到 Up 的窗口。改为留 1 轮（≈2 分钟）缓冲。 |

自检 22 → **27 项全过**。

### 关于"我的监控"的能力边界（屋主已知情并选择保持现状）

```
round 45  02:24:47
round 47  08:55:44     ← 中间 6.5 小时
```

我的机器休眠，监控被冻结 —— **"我自己修"的硬边界是我不在线就没有监控也没有自愈**。
那一夜是**容器内置链**兜住的（两次各 5 分钟）。屋主明确选择"就这样"，
所以没做服务器端守护进程。但这意味着：**内置链才是主力，我的层是补充。**

### 本轮改动文件

| 文件 | 改了什么 |
|---|---|
| `runtime/mainloop.py` | `TOKEN_HARVEST_INTERVAL`；`is_primary` 块内定时抄回（含长注释解释病根） |
| `renew_token.py` | `RELOGIN_MIN_BAD_MIN` 5.0 → 2.0 |
| `tests/test_token_harvest.py` | +3 项回归（定时抄回接线 / 位置 / 门槛） |
| `tools/active/watch_bot.ps1` | 修第 5、6 个缺陷；自检 27 项 |

### 回滚

```bash
cp -p /opt/dzmm/.harvest_backup_20261002/runtime/mainloop.py /opt/dzmm/runtime/
cp -p /opt/dzmm/.harvest_backup_20261002/renew_token.py /opt/dzmm/
cd /opt/dzmm && docker compose build dzmm-bot && docker compose up -d dzmm-bot
```

---

## 2026-10-02 10:07 · 两起事故：**假闸门**（上班群被当认证群踢人）+ **噪音漏过滤**（记忆污染）

> 屋主报："① 上班群的人设怎么变成小小糯了？还自动筛查新入群的人并且踢人？
> ② 茶水间/记忆考核怎么还触发了小小糯记忆的功能？"

两处**根因完全不同**，但都属于"同一个判定在两个地方各写一遍，只有一个是对的"。

---

### 事故一：`screen=False` 的上班群被按认证群规矩筛查并踢人

#### 现场（`bot.log` 逐字）

```
08:43:14 🎉 [f4ec4c21] 新成员加入 1 人，只发欢迎语     ← 入群事件，闸门**正确**
08:46:09 P12: 热加载=启用 …                            ← 一次容器重启
08:46:14   📇 [f4ec4c21] 成员对账：发现 1 名没见过的成员（可能是断线期间入群的），开始筛查
08:46:16 🚫 已移出 30932a9a
```

配置本身没坏（`f4ec4c21 persona='ruojin' screen=False`），同期 `/help` 也**正常返回若瑾的话术**。

#### 为什么看起来"人设变成了小小糯"

筛查链（`joins.screen_members`）用的是**认证群版文案**：
`welcome_of(cid)`（**没传 `plain=True`**）⇒ 若瑾只覆盖了 `welcome_text_plain`、
没有 `welcome_text` ⇒ 回落到常量 `WELCOME_TEXT`（认证群那份，含"姐姐""小秘书""本群是妹子认证群"）；
踢人文案也是硬编码的 `JOIN_DONE_MALE = "🚫 已移出「{name}」（原因：男性），本群是妹子认证群～"`。

⇒ 上班群出现的是"**名字换成若瑾、话术整段是认证群**"的消息 —— 看起来就是人设串成了小小糯。

#### 根因：**闸门是死的，不是漏装**

我最初以为"第二个调用点忘了装闸门"，**不对**。`mainloop.py:884` 的闸门**在两个版本里都有**：

```python
    for _cid in B.GROUPS:
        if not B._feat_on(_cid, "screen"):
            continue
        await B._reconcile_members(sender, _cid, dry)
```

真正的问题是 **`_feat_on(cid, "screen")` 对每个群都返回 True**：

| 环节 | 事实 |
|---|---|
| `config_store.feature_map()` | 原先**只导出 `groups[].features` 子表** |
| 群级字段 `screen` | 是**独立字段**（`config_store.py:78`），不在子表里 |
| ⇒ 覆盖表 | 从来没有 `"<cid>|screen"` |
| `Feature("screen", …)` | **没写 `default=`** ⇒ 默认 **True** |
| ⇒ `features.on()` | 回落默认值 ⇒ **永远 True** |

⇒ 对账闸门永远不 `continue` ⇒ **每次容器重启都对所有群做对账**。

而入群事件那条路侥幸没出事，**只因为它多查了一个** `cid in SCREEN_CIDS`
（那个集合来自群级字段 `bot.py:154 SCREEN_CIDS = set(_CFG.screen)`，是对的）。
**两个调用点、两套判定、只有一个是对的** —— 这就是事故的形状。

#### ⚠️ 为什么测试没拦住（值得单独记一笔）

`tests/test_recovery_ownership.py:223` 有这条：

```python
assert '_feat_on(_cid, "screen")' in seg, "只在开了筛查的群里做"
```

它是**源码文本断言** —— 断言的是"代码长这样"，不是"行为对"。
闸门代码确实在，但它读的开关是死的。
**教训：能用行为断言的地方，别用源码断言。**
（本项目源码断言已经踩过三次锚点坑，这次是更本质的一类：断言了形式，没断言语义。）

#### 改了四处

| # | 改动 | 文件 |
|---|---|---|
| 1 | **`feature_map()` 把群级 `screen` 桥进覆盖表**（根治） | `runtime/config_store.py` |
| 2 | **闸门下沉进 `screen_members` 自己**（第三个调用点也不可能再漏）：未开筛查就"不动成员、全部放行" | `runtime/joins.py` |
| 3 | `remember_members()`：**入群事件也要写成员快照**。原先快照只有对账会写 ⇒ 正常入群的人下次重启仍算"没见过"，被再处理一遍 | `runtime/roster.py` + `runtime/mainloop.py` |
| 4 | 两处精确断言的测试同步（它们钉的是 `feature_map()` 的**精确内容**） | `tests/test_config_schema_v2.py`、`tests/test_hotload.py` |

#### 生产验证

```
SCREEN_CIDS = ['bd7fe6a4']
OK  4a56be71  feat_on=False  in_SCREEN_CIDS=False
OK  4fc4a2fd  feat_on=False  in_SCREEN_CIDS=False
OK  bd7fe6a4  feat_on=True   in_SCREEN_CIDS=True
OK  f4ec4c21  feat_on=False  in_SCREEN_CIDS=False
BRIDGE_OK
```

**修前 `feat_on` 对所有群都是 True。** 现在它与 `SCREEN_CIDS` 完全一致。

---

### 事故二：茶水间把别家 bot 的"记忆考核"当成我们的记忆指令

```
09:21:56 👋 [4fc4a2fd] @消息: 【记忆考核·第 1 级】请记住：mTrOE
09:21:56 💬 🤖 记好啦～小小糯会记住的～♡
```

`runtime/memory.py:32` 的 `MEMORY_TRIGGERS` 里有**裸的"记住"**，而别家 bot 的开场白
恰好写着"**请记住**：" ⇒ 被当成一条 `/记住` 指令，`mTrOE` 进了记忆库。

#### 关键是：这类噪音**早就认识，但只堵了一半**

`recap.ignore_kw`（茶水间配了"打卡,摸鱼,躲藏,记忆考核,摸鱼币…"）+ `recap._is_noise`
是一套现成的过滤，**但它只保护 recap 的输入** —— **触发层没有这道过滤**。

> 澄清一处：茶水间 `memory_recall/memory_remember` 是**显式开着**的，
> 所以回"记好啦"本身**符合配置**，不是人设或开关串了。问题是它**吃了别家 bot 的游戏消息**。

#### 改了两处（双保险）

1. **触发层**（治本）：`mainloop` 决定"接不接这条 @"之前，先复用 recap 那张表判定；
   命中就只在日志留一行"这条不接"，**刻意不用 `continue`** —— 那条消息后面还有记账/上下文，
   跳过整个循环体是另一类隐性变化。
2. **记忆提取处**：`at_chat` 再挡一次。记忆库被污染是**不可逆**的（要人工清），
   而漏挡的代价只是"这一条没被当指令"。**宁可漏记，不可错记。**

---

### 验证与回滚

- 新增 **10 项**回归测试（`tests/test_screen_gate_and_bot_noise.py`），
  含反面用例（"开了筛查的群必须照常筛"，别把闸门做成"一律不筛"）与
  "正常聊天/真正的记忆请求不许被误伤"。
- **全量 pytest 退出码 0。**
- 部署前按规矩建了 `/opt/dzmm/.no_autorepair` 哨兵（防自己的监控把重建当故障），部署后撤掉。

回滚：

```bash
cp -p /opt/dzmm/.screen_gate_backup_20261002/bot.py /opt/dzmm/
cp -p /opt/dzmm/.screen_gate_backup_20261002/runtime/*.py /opt/dzmm/runtime/
cd /opt/dzmm && docker compose build dzmm-bot && docker compose up -d dzmm-bot
```

### 遗留

1. **`mute` / `push` 没有同名 Feature**，所以没桥进覆盖表（桥了会造出假覆盖）。
   如果以后有代码写 `_feat_on(cid, "mute")`，会是同一个坑 —— 建议加一条静态检查：
   `_feat_on` 用到的 key 必须在注册表里（`test_feature_registry.py` 已有类似扫描，可扩到"群级字段是否都有桥"）。
2. 事故一里**被误踢的 30932a9a**（08:46 踢出上班群）没有自动拉回 ——
   踢人是平台侧动作，我们这边没有"拉回"接口。需要人工请回。

---

## 2026-10-01 · Phase 4B §2.2 —— 总览页信息层级（本地完成，未部署）

按设计文档 §2.2 把总览页从「9 卡平铺」重组为五段层级
（[dashboard.py](../admin/dashboard.py) 的 `render_html`）：

1. **告警条**（仅异常时出现）：组合 7 类信号 —— bot 未运行 / 热加载配置
   不合法 / bot.paused / token 过期 / config.warnings / 自证行缺失 /
   出站队列有发失败记录，`alert-banner--bad/--warn` 两级。
2. **状态卡格**：4 张 `.stat-card`（容器进程 / 登录态 / 群与 Bot / 出站队列），
   每卡带 `--ok/--warn/--bad` 状态色（如 token 未设、source=defaults → 黄）。
3. **快捷操作 + 启动自证双列**：本面板**只读**，POST /reload 需要 CSRF 且
   巡检/重登路由不存在 —— 快捷操作卡诚实做成导航入口，**不放假按钮**；
   启动自证卡 = "X / 11 行" meta + 逐行表格（缺行红字）。
4. **群状态表**（以群为主视图）：config.groups 为主行，列 = 群 / 生效徽章 /
   人设 / Bot 通道 / 特殊开关（静默·筛查·推送）/ 进行中玩法（groups 段）/
   发情人数 / 状态键数 —— 配置 × 运行状态在一张表里对齐。
5. **详细监控**：原 9 段逐卡渲染（`body(key)` 原样保留）收进 `<details>`，
   细节不丢。

### 兼容性纪律

- `render_html` 全程 `data.get(...)` + `sec()` 安全取段 ——
  段缺失/段坏时显示"—/（缺）"，一坏不白（钉点测试同款输入验证过）；
- XSS：新代码所有动态值一律走 `_e()`；
- 群状态表空表给"（没读到配置）"，骰子局空给"无进行中"。

### 验证（全绿）

- 冒烟：真实 collect() 渲染五段结构 + 空 sections 容错 + 4 类告警条注入 +
  群别名 XSS 转义，全过；
- `test_admin_dashboard.py` 57 个全过；
- 浏览器目检 8 项：7 过 1 项为预览脚本注入位置问题（真实告警条本来就在
  页面最顶，产品代码无需改）；暗亮双主题、10 张详细卡折叠、无布局破碎；
- 全量 pytest：**1548 收集 = 1542 通过 + 6 跳过，0 失败**
  （+10 来自另一会话的续期/会话锁收尾：test_renew_escalation 12、
  test_unauth_recovery_paths 10、test_session_lock 增改 —— 与本刀无关且兼容）。

### 遗留

1. 高级区搜索框（§3.1.3）——app.js 无表格搜索实现，留 4C。
2. 4A+4B 累计改动等观察期过后一起部署目检（回滚锚点 = 部署前 tar 备份）。

---

## 2026-10-01 · Phase 4B §2.2.4 —— 群状态表补齐"功能摘要 + 最近活动"两列

上一刀的群状态表缺设计文档 §2.2.4 要求的"功能摘要"与"最近活动"，本轮补齐。

### 数据层（均走既有只读通道，无新路由）

- `section_outbox` +`last_activity`：`select chatroom_id,
  max(coalesce(sent_at, created_at)) group by chatroom_id`
  —— 走现成的 `(chatroom_id, created_at)` 索引，`mode=ro` 连接不变。
- `section_config` 的每群条目 +`feat_on/feat_total/feat_diffs`：
  生效值三级回退（本群覆盖 > 全局覆盖 > 注册表默认），
  与代码默认不同的项进 `feat_diffs`。`import runtime.features` 是纯声明式
  注册表（无 playwright/网络），不违反该函数"不 import bot"的钉点纪律
  （`test_config_section_reads_bot_constants_without_importing_bot` 实测仍绿）。

### 渲染层

- 功能摘要列：`N/68 启用` 徽章（0 启用 = 红）+ diffs 前 4 项
  （"X 关"红 / "X 开"绿，title 带 key）+ 剩余 "+n 项"；无 diff = "全部跟随默认"。
- 最近活动列：`_ago()` 人类化时长；语义 = **最近一次出站**（发出优先、
  入队兜底），**不含入站消息** —— 页脚注明，不冒充"群活跃度"。
- 表格 8 → 10 列，`table-wrap` 自带横向滚动。

### 验证

- 冒烟：真实 collect() 两段新数据存在 + 合成数据（66/68 + 6 diffs →
  截断规则、最近活动"1小时30分"、全默认提示、XSS 全转义）全过；
- `test_admin_dashboard.py` 57 个全过；全量 **1548 收集 0 失败**；
- 浏览器目检省略：布局主体上一轮已目检，本轮仅表格加列（table-wrap 可滚动）。

---

## 2026-10-01 · Phase 4C —— 高级区表格搜索框（本地完成，未部署）

§3.1.3 的最后一项。设计文档写"app.js 的表格搜索已具备"是误记 ——
实际只有排序没有搜索，本轮补实现。

### 实现（渐进增强，零内联 JS）

- [app.js](../admin/static/app.js) `initTableSearch()`：`input[data-table-search]`
  → 目标表格行过滤（textContent 大小写不敏感）；**分类头跟随**——
  行内含 `<h3>` 视为分类头，只在组内有命中行时显示（搜"骰子"只剩
  互动游戏头 + 2 行）；清空 = 全部恢复。app.js init 时给 `<html>` 加 `.js` 类。
- [base.css](../admin/static/base.css) 工具类 `.js-search{display:none}` +
  `.js .js-search{display:block}` —— **无 JS 时搜索框整体隐藏**：
  搜索是查找辅助不是操作路径，操作路径（表单本身）永远可用。
- [features_ui.py](../admin/features_ui.py)：两个高级表加 id（f-params / f-matrix）
  + details 内各一个搜索框（`autocomplete='off'`，无任何内联事件属性，
  CSP 纪律不变）。

### 验证

- 冒烟 8 项（搜索框/表 id/无内联事件/app.js 无危险 API/css 规则）全过；
- `test_features_ui.py` 56 个全过（无内联脚本钉点天然满足）；
- **浏览器目检 7/7**：搜"骰子"→ 3 行可见（分类头+2）；清空恢复 66 行；
  英文 key（dice）参与匹配；无命中不报错；参数表搜 cooldown 命中 5 行；
  `<html class="js">` + `.js .js-search` 生效；
- 全量 pytest：**1551 收集 0 失败**（+3 仍为另一会话的续期测试）。

### 4B/4C 状态：前端重构全部完成

4A（接线）+ 4B（§3 简化 / §2.2 层级 / 群状态表两列）+ 4C（搜索）收口。
剩余动作 = **观察期结束后的部署目检**（回滚锚点 = 部署前 tar 备份）。

---

## 2026-10-02 · screen 双真源归一（审查 DeepSeek 热修后发现的缺陷，方案 A）

**背景**：DeepSeek harness 修了三起线上事故（8.8h token 掉线 / screen 死闸门踢人 /
茶水间接别家 bot 的记忆指令，见 `test_token_harvest.py`、
`test_screen_gate_and_bot_noise.py` 的文档串）。审查其 `feature_map()` 桥接时
发现遗留缺陷：**screen 从此有两个可写真源** —— /edit 页写群级 `screen` 字段、
features 页矩阵三态行写 `groups[].features.screen` 子表；桥接顺序是
「群级字段后写覆盖」，**features 页写的 screen 静默失效**
（探针实测：子表 False + 群级 True → `cid|screen: True`）——
正是其注释里点名的"看起来配了、实际不生效"。

### 归一（方案 A，[config_store.py](../runtime/config_store.py) `_parse`）

读入时把子表里的 `screen` **吸收进群级字段**（子表赢 —— 矩阵里的显式覆盖
操作）并 `pop` 出子表；`as_dict()` 只序列化群级字段 ⇒ 配置文件里的
`features.screen` 在下一次保存后自然消失，双真源逐步收敛。
`feature_map()` 桥接保持不变（群级 → `cid|screen`）。

### 验证

- 回归测试 3 个（子表赢且吸收 / 只写群级字段原样 / 往返序列化不再回流）；
- `test_screen_gate_and_bot_noise.py` + schema v2 + hotload 共 63 个全过；
- 全量 pytest：**1564 收集 0 失败**（DeepSeek 侧仍在并行加测试）。

---

## 2026-10-02 10:31 · Phase 4A/4B/4C + DeepSeek 热修 —— 一起部署上线

**变更集**（全量 1564 测试 0 失败后部署）：
1. 4A/4B/4C 运维后台重构（统一外壳 / §3 简化 / §2.2 层级 / 搜索框）
2. DeepSeek harness 两批热修（P12-BK token 定时抄回 / screen 死闸门 /
   茶水间噪音过滤）
3. screen 双真源归一（本会话，方案 A）

**回滚锚点**：`/opt/dzmm/backup-code-20261002-103139.tar.gz`
（部署前全树；rollback 命令同 Phase 3 记录，注意 rm -rf personas 那步）。

### 部署后验证（全绿）

- 168 文件上传、镜像重建、容器 Recreated+Started，**restarts=0**
- 启动自证行全齐：P5 锁/限流、P7 sqlite、P8 prompt 41/41、
  P11 配置=file 群=4 Bot=3 身份数=4、P12 功能 68/68 接线 参数已覆盖=15
  人设体检全过
- Bot 路由 3 条就绪（uid 全认到）；**4 并行实例 start**；
  首轮 `⏱️ 投递 + 本轮处理` 收尾行出现；outbox pending=0
- 2 分钟稳定观察：restarts=0，traceback/EMERGENCY/未授权 计数 **0**；
  期间一条真实入群事件（茶水间）闸门正确（只发欢迎语）
- 面板：登录页 200（新版自包含样式）；token 定时抄回间隔 600s，
  首次触发在容器起 10 分钟后（验证窗口内未到，属预期）

### 记录在案（非回归）

- `/healthz` 需登录才能拿到 "ok"（未登录得登录页 200）——
  `_require_session` 未配密码时放行（回环模式）是既有行为，测试环境依赖它；
  compose 无 docker healthcheck 块，探活不受影响。
  若要 healthz 免认证（探活惯例），属后端改动，另行决策。

---

## 2026-10-02 12:47 · 热修：/static 三个 CSS 未进 `_STATIC` 白名单（线上裸 HTML）

**现象**：部署后用户截图——页面结构是新的但完全无样式（裸 HTML）。

**根因**：`dashboard.py` 的 `_STATIC` 白名单**硬编码**且只有 `app.js`；
4A 新增的 base/components/dashboard.css 被 shell 引用但没登记 → 服务器 404。

**为什么漏网**（三层验证全部绕过了这个路由）：
- 渲染冒烟只查 HTML 里有 `href='/static/base.css'` 字符串；
- 浏览器目检走本地 http.server 直读文件系统，不经 dashboard 的 `_STATIC`；
- `test_static_js_is_served_and_whitelisted` 只钉了 app.js 200 + 陌生路径 404
  （404 是预期安全行为，正好把 bug 藏住了）。

**修复**：`_STATIC` 补三个 CSS（text/css）+ 新钉点
`test_every_referenced_static_asset_is_served`——从 shell.py 源码**提取**
全部 /static/ 引用逐个 GET（200 + 非空 + CSS content-type 抽查），
以后新增资产忘登记会当场红。

**验证**：dashboard 58 测试全过；重新部署（锚点
backup-code-20261002-125125.tar.gz）后带会话实测四资产
200 + text/css / application/javascript；restarts=0。

**遗留改进**（另行决策）：/static 在会话墙内——登录页因此必须自包含；
若把 static 挪到会话检查之前，登录页可复用共享 CSS、探活工具可拉资产。

---

## 2026-10-02 13:17 · 热修之二：CSP `style-src` 缺 `'self'`（外链样式被浏览器拒载）

**现象**：`_STATIC` 修复部署后用户截图仍是裸 HTML——服务器侧 CSS 200 +
text/css 完全正常，但浏览器不应用。

**根因**：CSP 一直是 `default-src 'none'; style-src 'unsafe-inline'`。
P12 时代 CSS 全部**内联** `<style>`，`'unsafe-inline'` 放行 → 有样式；
P4-4A 起 CSS 改为**外链** `/static/*.css` —— 外链样式表按**源**匹配，
`'unsafe-inline'` 不放行、也没有 `'self'` → 浏览器 CSP **静默拒载**。
服务器侧（状态码/类型/内容）全部正常，唯独浏览器不渲染——
**响应头级 bug 的典型形状**。

**为什么又漏了**：本地目检走 http.server（无 CSP 头）；HTTP dump 只查
字符串与状态码；CSP 钉点只断言了 script-src/default-src 的存在性，
没断言"引用的外链资产被 CSP 允许"。

**修复**：CSP `style-src 'self' 'unsafe-inline'`（'self' 放行同源外链，
'unsafe-inline' 保留给页面 extra_head 的内联 <style>）；钉点
`test_csp_allows_only_self_script` 补 `style-src 'self'` 断言。

**验证**：五页测试全过；重部署（锚点 backup-code-20261002-131746.tar.gz）后
线上响应头确认 `style-src 'self' 'unsafe-inline'`；restarts=0。

**教训（进 project_memory）**：改"内联资源 → 外链资源"这类结构迁移时，
必须同步审 CSP 的对应 src 指令；带响应头的本地目检（或至少 dump 响应头
断言引用资产被 CSP 允许）才能兜住这类只在浏览器行为里显形的问题。

---

## 2026-10-02 13:30 · 人设页按人设 tab 化（屋主要求：不要平铺）

复用功能页的 CSS-only tabs 机制（radio + 兄弟选择器，无 JS 可用）：

- 每个人设一个 pane：A 段人设文本 + B 段话术表 + 体检 + "恢复该人设默认"按钮；
  tab 标签带改动徽章（一眼看出哪个人设动过）。
- **表单嵌套的解法**：主表单不能嵌套，pane 里的恢复按钮用 HTML5
  `button form='pf-reset-{key}'` 引用页面底部的隐藏小表单
  （纯 HTML 属性、无 JS；`class='sr-only'` 对屏幕阅读器也友好）；
  底部保留"恢复全部人设的默认"。
- **解析契约零改动**：`pseen/ptext/pphrase` 字段名原样（CSS 隐藏不影响提交，
  所有 pane 的字段都在 DOM 里），`parse_personas`/`personas_diff` 与
  端到端测试不动。
- tab radio 带 `data-no-dirty`（点页签不触发脏计数，同功能页）。

### 验证

- 冒烟 13 项（tab 结构/parse 契约/form= 引用/转义/无内联事件）全过；
- `test_personas_ui.py` + `test_persona_texts.py` 50 个全过；全量 **1565 收集 0 失败**；
- 部署（锚点 backup-code-20261002-133057.tar.gz），容器内确认新代码，restarts=0。

---

## 2026-10-02 14:17 · 提示词页按 tier tab 化（同屋主"不要平铺"要求）

三个 tier（C1 自由文案 23 / C2 形状敏感 12 / C3 结构化输出 6）各一个页签：
- 清单表收进所属页签；tab 标签带条目计数（如"C1 · 自由文案（23）"）。
- **selected（编辑中）的编辑器 / 预览 / 历史放进它所属 tier 的 pane**，
  并由服务端渲染 `checked` 让该页签默认激活——点列表里"编辑"跳转后
  直接落在编辑器所在的页签上。
- CSS-only 切换（radio + 兄弟选择器，无 JS）；radio 带 `data-no-dirty`；
  页面特有 CSS（pre.reply / pre.diff）经 extra_head 原样注入。
- 无表单契约变化（清单是纯链接跳转，编辑器是独立 form，pane 只是容器），
  `render_prompts` 签名与 `render_history` 不动。

### 验证

- 冒烟 18 项（无 selected / selected C1 / selected C3 三种状态 + 截断规则）
  全过；`test_prompts_ui.py` 21 个全过；全量 **1565 收集 0 失败**；
- 部署（锚点 backup-code-20261002-141702.tar.gz），容器内确认新代码，
  restarts=0。


## 2026-10-02 22:35 · P4-4C 收尾三件套 + GitHub Actions CI 上线并调平

收尾（commit d666763）：
- **P1 dispatch 真调用冒烟**：`tests/test_dispatch_smoke.py` 真调 `handle_slash()`
  （/帮助 / /吐槽开|关 / 未知指令），断言发送文本 + last_id 推进 + _save_group 落盘 +
  inflight 标记 —— 补上"getsource 测试抓不住分派链改名/死闸门"的盲区。
- **P2 CI**：requirements-dev.txt（pytest==9.1.1）+ `.github/workflows/test.yml`
  （ubuntu-latest / py3.11 / push+PR 触发 / junitxml 摘要）。
- **P3** features_ui.py docstring `_WHITELIST`→`WHITELIST` 笔误。

CI 首跑 33 连挂（commit 47120e6 调平，本地绿 ≠ CI 绿的六类根因）：
1. **代理污染（25 个）**：`sio_subscribe` 被 import 时调 `set_process_proxy_env()`
   把 HTTP(S)_PROXY 写进进程环境，之后所有裸 `urlopen` 全被拽去 127.0.0.1:7890 ——
   本机 Clash 活着测不出来，CI 直接 Connection refused。
   修：conftest 加 `_no_proxy_env` autouse 夹具，每用例前后清代理变量。
2. **段缺白屏变无响应（2 个）**：`section_groups()` 在 STORE_DB 不存在时返回
   `{"present": False}`，`body("groups")` 的 `d["groups"]` 直接 KeyError →
   handler 无响应。修：`d.get("groups") or {}`（"段缺显示（缺）"约定的漏网点）。
3. **审计假绿（1 个）**：audit_log 写 config 同目录，测试却读仓库根 ——
   本机真审计文件喂出假绿。修：读 `cfgp.parent / AUDIT_NAME`。
4. **patch 没打在真读口（1 个）**：`_detect_joins` 走 `identity.is_self → self_uids`，
   测试 patch 的 `bot.BOT_UID` 是壳 re-export 不在链上。修：patch `identity.BOT_UID`。
5. **环境值闸门（1 个）**：`self_names()` 只在 `BOT_UID` 非空时加 Bot 名变体，
   CI 无 DZMM_BOT_ID → `@小小糯-bot` 被短名剥成 `-bot` 残余。修：测试补 patch。
6. **平台差异（3 个）**：Windows 反斜杠路径字面量（Linux 上是文件名一部分）；
   日志时间戳按本机时区写、却按东八区解析（UTC CI 上差 8 小时）。

### 验证

- 本地全量 **1562 passed / 6 skipped**；CI 第二跑 **tests=1568 failures=0 errors=0**
  （1m48s，本地与 CI 的 skip 数差 3 个是环境相关 skip，正常）。
- 教训入库：测试断言要打在**真读口**；环境敏感值（代理/时区/真实数据文件）
  必须在夹具里显式钉死或清空。


## 2026-10-03 11:50 · P12-BT：会话真死先走内联重登热恢复，不许同秒降级 emergency

复盘 2026-10-03 09:13 那次 SESSION_DEAD（5 分钟自愈，但重启了 bot 三实例）：
`_try_password_relogin` 被**耐心闸**（`RELOGIN_MIN_BAD_MIN=2`，当时坏 0 分钟）拦下后，
调用方**同一秒**落 emergency 重启 bot —— "等 2 分钟别急着重登"的耐心只保护了重登，
却没保护更重的重启。改法：

- `_try_password_relogin` 返回三态：`"ok"` / `"skip"`（这轮没真试：耐心闸/冷却/锁）/
  `"fail"`（真试过没成）；
- unauth 主分支：`skip` ⇒ 本轮到此为止（session_state 保持 unauth 让巡检可见），
  等下一轮 —— 坏时长过闸 ⇒ 内联重登热恢复（不重启、出站队列保住）；平台瞬时抖动
  ⇒ 下一轮探针自己变 ok，什么都不用做；`fail` ⇒ 照旧落 emergency（兜底不变）；
- 退避分支与长未验证分支只改 `== "ok"` 判断，语义不变。

### 验证

- 新增钉点：skip 不写 emergency（09:13 事故形状）、fail 照旧落 emergency、三态各分支、
  unauth 分支接线次序；全量 **1564 passed / 6 skipped**，CI 绿（37093590127）；
- 部署（锚点 backup-code-20261003-114217.tar.gz），容器内 renew_token.py 含 P12-BT，
  11:42 三 Bot 路由就绪、outbox pending=0 failed=0。
- 遗留：`tests/test_session_lock.py` 单独与 renew 系测试同跑时 import
  headless_relogin/auto_recover 报 ModuleNotFoundError（存量测试顺序依赖，全量跑正常）。
