# DZMM Bot（dzmm_nuo）

dzmm.ai 平台的多 Bot 聊天机器人：**4 群 × 3 人设**（小小糯 / 薇丝 / 若瑾），
单容器部署（Python 3.11 + Playwright chromium），阿里云 ECS `/opt/dzmm`。
代码仓库：<https://github.com/niuerhaizaishan/dzmm_nuo>（私有）。

## 小小糯解梦（本地实现，默认关闭）

- 仅认证妹子群与顶层茶水间、有效人设为 `nuo` 时可用；认证群 `/解梦 梦境`，茶水间 `#解梦 梦境`，两群均支持 `@小小糯 解梦 梦境` 或明确的自然语言求解。
- 现有群配置中按群开启 `features.dream=true`；茶水间再配置 `tuning["dream.hash_cmd"]=1`，无需放开其他斜杠或 `#` 指令。未修改服务器配置或凭据。
- 单次文字回复，默认最多 230 字；茶水间最多 180 字（另加 `🤖 ` 前缀），沿用既有 Outbox 和发送路由。每人每群冷却 60 秒、输入最多 2000 字、最多 2 个在途任务。
- 使用现有模型服务处理梦境；不新增梦境档案，但既有消息、日志与 Outbox 仍可能留存。请先去掉姓名等个人细节。古籍仅为文化趣读，不预测现实；首版短引覆盖有限，无匹配时不引用。
- 配置、测试与来源说明见 `docs/解梦功能整体方案-2026-10-04.md`；实现不依赖外部 Skill、向量库或新服务。

## 一、运行架构

容器 `dzmm-bot` 由 `entrypoint.sh` 拉起以下进程（`bot.paused` 哨兵存在时全部暂停）：

```
entrypoint.sh
├─ headless_relogin.py        登录态检查（启动时；失效走 headless 重登）
├─ renew_token.py             token 续期循环（每 5 min；P12-BK 定时抄回浏览器 token）
├─ check_bot_health.py        健康巡检（每 5 min，异常发飞书告警）
├─ heartbeat.py               心跳（每 2 h 发运行状态到飞书）
├─ admin/dashboard.py         运维后台（:8090，账号密码登录，本仓库 admin/）
├─ bot.py --sync              档案同步（12:05 / 23:35）
└─ bot.py 主循环 × N          P12-I 按群并行：每群一个实例（崩溃 10s 自愈）
    └─ runtime/mainloop.py    主循环真身（B=bot 模块桥，见下）
```

出站消息经 **outbox 队列**（SQLite，同群串行 + 限流 + 重试）；收消息走
Webhook（平台直推）+ Socket.IO 双通道。

## 二、目录结构（谁在哪里、为什么）

| 类别 | 位置 | 说明 |
|---|---|---|
| **主程序** | `bot.py` | 入口 + 主循环壳 + 常量默认值。**1115 行**——逻辑已全部迁出（P3） |
| | `runtime/`（18 模块） | 主循环真身/分派链/发送引擎/名册/推送/排行/档案同步/热加载… 真身在 runtime、`bot.py` 留壳注入状态（monkeypatch 接缝即契约，见 `tests/test_undefined_names.py`） |
| | `brains.py` | AI 生成（DeepSeek 调用、提示词组装） |
| | `personas/` + `prompts/` | 人设包（代码默认 + `config.json` 覆盖）与 40 个提示词（`prompt_defaults.py` 是出厂默认值） |
| | `dzmm.py` `socketio.py` `sio_subscribe.py` | 平台 API / Socket.IO 传输 |
| | `dice_game.py` `state_store.py` `profiles.py` `batch_register.py` `regen_novel.py` `diag_fortune.py` | 骰子 / 群状态 KV / 档案（注意：后三个被运行链 import，名字像一次性脚本但不是） |
| | `session_guard.py` `throttle_guard.py` `env_config.py` `token_store.py` | 会话锁 / 限流 / 环境配置 / token 存储 |
| **运维后台** | `admin/` | `shell.py`（五页统一外壳）+ dashboard/features/personas/prompts/config 五个 UI 模块 + `static/`（设计系统三件套 + app.js） |
| **容器后台进程** | `renew_token.py` `check_bot_health.py` `heartbeat.py` `headless_relogin.py` | entrypoint 循环拉起的四件套（细节见上） |
| | `auto_recover.py` `auto_relogin.py` `monitor_recover.py` `captcha_novnc.py` | 会话自愈族（mainloop 调用） |
| | `webhook_server.py` `feishu_alert.py` `wx_alert.py` | Webhook 接收端 / 告警通道 |
| **部署** | `deploy.ps1` | 备份远端 → 上传 → 重建容器；`-Action status/logs/health/rollback/diff/sync` |
| | `Dockerfile` `docker-compose*.yml` `entrypoint.sh` `requirements.txt` `config.example.json` | 容器定义（凭据从 `.env` 注入，不进镜像） |
| **宿主机运维** | `tools/ops/` | start_vnc.sh、dzmm-vnc.service（systemd）、logrotate 配置、拉取小说.ps1 |
| | `resume.sh` `admin_tunnel.ps1` | bot.paused 恢复 / 面板 SSH 隧道 |
| **测试** | `tests/` | pytest 唯一收集目录（1565 个；`pytest.ini` testpaths=tests）。跑法见下 |
| **一次性/历史** | `tools/diag/` | 根目录历史手工脚本（test_fmt.py 等 8 个） |
| | `tools/archive/` | 死代码坟场（含 `online_status.py`、`manual_login.py`） |
| | `tools/probes/` `tools/active/` `tools/legacy/` | 探针 / 活跃工具 / 遗留 |
| **文档** | `docs/` | 各 Phase 施工记录（EXECUTION-LOG.md）、事故复盘、设计文档 |
| **数据（不入库）** | `.env` `.dzmm_token.json` `config/` `data/` `state.json` `profiles.json` `novels/` `content/novels/` `pw_profile/` | 全部在 `.gitignore`（凭据/PII/运行数据） |

## 三、本地开发与测试

```powershell
# pytest vendor 在 DDZM 项目的 .pylibs（本机 pip 装不上）
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH='D:\Deepseek\DDZM\.pylibs'
python -m pytest tests/ -q -p no:cacheprovider
```

- 当前基线：**1565 收集，0 失败**（6 跳过）。
- 两个关键棘轮守卫：`test_silent_errors.py`（RUNTIME_SILENT_MAX，静默失败只许变少）
  与 `test_undefined_names.py`（TARGETS：bot 壳调用与 runtime 模块双向一致）。

## 四、部署与验证

```powershell
.\deploy.ps1                 # 备份远端全树 -> tar 上传 -> docker 重建（自动）
.\deploy.ps1 -Action status  # 容器状态 + restarts
.\deploy.ps1 -Action health  # 全面体检（容器/进程/登录态/巡检/日志）
.\deploy.ps1 -Action rollback
```

- 部署后验证清单：restarts=0、启动自证行（P5/P7/P8/P11/P12）、
  `start ... groups=1` ×4（并行实例）、`⏱️ 投递 + 本轮处理` 收尾行、
  traceback/EMERGENCY 计数。
- **回滚**：`.\deploy.ps1 -Action rollback`（用 `/opt/dzmm/backup-code-*.tar.gz`；
  注意 rollback 后要 `rm -rf /opt/dzmm/personas` 防包遮蔽——tar 解包不删新文件）。

## 五、监控与告警

- **心跳**：每 2 h 飞书运行状态。
- **健康巡检**：进程数 / CPU / 内存 / token 过期 / 代理 / Socket.IO 断线；去重防刷屏。
- **token 生命周期**（P12-BK，2026-10-02）：renew 每 5 min + **主循环每 10 min
  从浏览器抄回最新 token**（平台 refresh_token 是轮换制，安静期不发送也会轮换）；
  重登门槛 2.0（连续坏分钟数），紧急时写 `token.emergency` 自愈。

## 六、安全注意

- `.env`、`.dzmm_token.json`、`.dzmm_login.json`、`profiles.json`、`state.json`
  含凭据/PII，**已在 .gitignore**，不要提交、不要对外分享。
- 服务器 IP 出现在 `deploy.ps1` / `admin_tunnel.ps1` —— 仓库是私有的，转公有前必须清洗。
- 运维后台走账号密码（`DZMM_ADMIN_USER/PASSWORD`）+ 安全组白名单出口 IP。

## 七、Git

- 远端 `origin` = <https://github.com/niuerhaizaishan/dzmm_nuo>（私有）。
- push 需走本地代理：`git -c http.proxy=http://127.0.0.1:7890 push`。
