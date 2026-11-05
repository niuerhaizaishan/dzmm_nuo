# DZMM Bot（dzmm_nuo）

dzmm.ai 平台的多 Bot 聊天机器人，按群绑定人设（小小糯 / 薇丝 / 若瑾 / Λʌ），
单容器部署（Python 3.11 + Playwright chromium），阿里云 ECS `/opt/dzmm`。
代码仓库：<https://github.com/niuerhaizaishan/dzmm_nuo>（私有）。

## 茶水间双人办公室剧情

新增 `#允许潜规则`、`#拒绝潜规则`、`#潜规则 @目标 [方向]`、`#潜规则继续`、`#潜规则结束` 与 `#潜规则状态`，续写也支持 `#继续 [方向]`，结束也支持 `#结束`，兼容全角 `＃`。引用对方的本群消息发送 `#潜规则 [方向]` 时，以核验后的原消息作者为目标。成员默认允许，主动发送 `#拒绝潜规则` 后停止接受且结束参与中的剧情；已有拒绝设置保留。职级严格更高者每天可发起一次，允许跨部门，部门资料缺失不拦截；正文以 400～600 字为目标，完整的 200～399 字也接受；整条引用指令发送，5 分钟无互动结束。2026-10-28 已上线指令、茶水间消息归档及员工接口，按用户确认的 11 档职级顺序比较；性别沿用档案/平台资料，未知使用中性称呼。真实员工资格核验及模型生成已通过，尚未在群中实测剧情投递。详见 [实现与接口说明](docs/茶水间双人剧情-本地实现-2026-10-21.md) 和 [上线验证记录](docs/茶水间井号指令与消息归档上线-2026-10-28.md)。

## 小小糯解梦（本地实现，默认关闭）

- 仅认证妹子群与顶层茶水间、有效人设为 `nuo` 时可用；认证群 `/解梦 梦境`，茶水间 `#解梦 梦境`，两群均支持 `@小小糯 解梦 梦境` 或明确的自然语言求解。
- 现有群配置中按群开启 `features.dream=true`；茶水间再配置 `tuning["dream.hash_cmd"]=1`，无需放开其他斜杠或 `#` 指令。未修改服务器配置或凭据。
- 单次文字回复，默认最多 230 字；茶水间最多 180 字（另加 `🤖 ` 前缀），沿用既有 Outbox 和发送路由。每人每群冷却 60 秒、输入最多 2000 字、最多 2 个在途任务。
- 使用现有模型服务处理梦境；不新增梦境档案，但既有消息、日志与 Outbox 仍可能留存。请先去掉姓名等个人细节。古籍仅为文化趣读，不预测现实；首版短引覆盖有限，无匹配时不引用。
- 配置、测试与来源说明见 `docs/解梦功能整体方案-2026-10-04.md`；实现不依赖外部 Skill、向量库或新服务。

## 会话取证（生产已开启，模块默认关闭）

设置 `DZMM_SESSION_OBS=on` 可记录凭据同步前后及自动恢复决策，保存脱敏快照和限量事件日志，不改变认证与恢复逻辑。2026-10-06 已部署并开启生产观测，生产 compose 显式设为 on；模块与本地开发环境仍默认关闭。字段、开销、回滚和验收说明见 `docs/会话观测补丁-2026-10-06.md`。

## 认证群与茶水间聊天归档

记录认证妹子群与顶层茶水间：独立 SQLite、消息 ID 去重、每条保留 UID 与昵称来源，支持手动导入历史 JSONL。后台 `/messages` 可选择群，提供时间、昵称、UID 和关键词只读查询。模块默认关闭，设置 `DZMM_CHAT_ARCHIVE=on` 后启用，生产已开启；不定时清理、不自动删除。2026-10-28 已验证茶水间真实消息入库及后台查询，历史消息尚未全量补录。

## 我的故事（本地实现，默认关闭）

仅认证妹子群的小小糯可用，按群配置 `features.personal_story=true` 后，发送 `/我的故事` 或 `@小小糯 我的故事`。也可用 `/我的故事 2026-08-01~2026-09-30` 指定北京时间日期范围。只读发起者本人的归档。成品是**开场白 + `一、XXX` 分段 + 收尾**：开场白由模型写（以本人在群里的名字开头、交代"故事从哪说起"），分段按发言的**时间先后和话题变化**分成 2～3 段、每段自取短标题、编号由程序加（`一、刚进群那阵`，**不用"第一阶段"这种写法**），**正文里讲事情时带少量具体日期**（全篇 3～5 处，照归档里的 day 写，"七月三十号那天"），叙述穿插核验后的原话，**约 500 字**，温情收尾。群里的涩涩／调教内容**按原样保留**（2026-10-07 起：取样追加 `_ADULT_KEYWORDS`、提示词改成「成人内容照写」），不净化、不替换成文雅说法。沿用现有模型和发送通道；未部署、未开启线上功能，详见 `docs/我的故事.md`。

## 我的画像（本地实现，默认关闭）

仅认证妹子群的小小糯可用，按群配置 `features.personal_portrait=true` 后，发送 `/我的画像` 或 `@小小糯 我的画像`，也可追加与“我的故事”相同的日期范围。根据本人历史发言选取兴趣、交流、做事或生活节奏等不同维度，通常 3 个维度、**约 500 字**，结合核验后的原话，温情收尾。涩涩／调教同样**按原样保留**并单独占一个维度（2026-10-07 起）。画像仅描述群聊中呈现的侧面；未部署、未开启线上功能，详见 `docs/我的画像.md`。

## 致某某（一封信，本地实现，默认关闭）

仅认证妹子群的小小糯可用，按群配置 `features.personal_letter=true` 后，发送 `/致 某某`（或 `/致某某`、`@小小糯 给某某写封信`）。**代发起者**写给某位群友的一封信，可带与“我的故事”相同的日期范围。读的是**收信人**的归档（不是发起者的），全文用发起人的口吻（「我」=发起人、「你」=收信人）。**按信的格式**：称呼和落款由程序生成（称呼按性别，落款写明代谁的笔），模型只写 3 段正文 + 寄语，**约 500 字**，表达熟悉、欣赏与关心，温情收尾；**开场白每次随机换一个切入角度**（不会封封都是"我其实很早就注意到你了"）。涩涩／调教按原样保留。目标解析**不猜**——同名多命中会回问；不想被写的人可用 `DZMM_LETTER_OPTOUT` 列出；目标级冷却默认关。沿用现有模型和发送通道；未部署、未开启线上功能，详见 `docs/致某某.md`。

## Λʌ 两群人设与酒馆功能（本地实现）

瑞雯酒馆与 mr.橘群独立绑定 `lambda` 人设，自称 Λʌ，使用 🌹 文字标识；ROSE 维持原样。
仅瑞雯酒馆增加北京时间 08:00 / 00:00 固定问候、Vv 专属 `@Λʌ 回到我身上！` / `@Λʌ 分离！`。
休眠状态持久保存，停止本群业务回复并取消旧待发消息。未部署、未修改平台账号显示名，详见
[范围、开关与验证说明](docs/Λʌ两群人设与酒馆功能-2026-10-21.md)。

## 视频提示词优化

认证妹子群开启 `features.prompt_optimize=true` 且 `slash_cmds=true` 后，可直接发送：

```text
/提示词 视频 雨夜街头，一只猫慢慢回头，镜头推进
```

小小糯会提示排队情况，调用 `gpt-6.1-sol` 整理为一段适合 Grok 的视频提示词。回复一张图片发送 `/提示词 视频 轻轻回头微笑`，会结合原图的主体、姿态和构图设计动作与运镜；没有引用时按文字优化。图片通过平台回查核验后读取，支持本群 JPG、PNG、WebP 静态图片及此前生成的图片；引用失效、引用非图片或原图读取失败时明确提示，不会降为纯文字猜图。原图最多 10 MB、4000 万像素，送入模型前校正方向、缩小至最长边 1536 像素并移除元数据，以图文形式发给所配置的提示词服务。

本命令只生成文字。要继续生成视频，请**再次回复原图**发送 `/生视频 优化后的提示词`；原图需要一并交给 Grok，仅复制文字不会自动带上原图。

服务使用 `PROMPT_OPTIMIZER_API_KEY`、`PROMPT_OPTIMIZER_BASE_URL`（默认 `https://www.kurumiai.net/v1`）、`PROMPT_OPTIMIZER_MODEL`（默认 `gpt-6.1-sol`）。密钥仅通过服务器 `.env` 注入 A 容器，不写入代码。原始想法最多 2000 字，每人限一个在途请求，最多同时处理两个请求，模型请求最多等待 120 秒、建立连接最多 15 秒。任务状态与结果保存到数据目录的 `prompt_optimizer.db`，重复收到同条指令不会再次调用模型；重启中断的任务不自动重试，发送结果不明时保留结果供管理员核查。

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
