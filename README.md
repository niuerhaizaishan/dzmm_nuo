# DZMM Bot 运维手册（Bot API 迁移开发副本）

> 🚧 **本目录是 `dzmm_cloud` 的开发副本 `dzmm_botapi`，用于"会话自动化 → 官方 Bot API"迁移。**
> 迁移设计见 `docs/BotAPI迁移方案.md`，实施步骤见 `docs/BotAPI实施计划.md`，当前进度见 `docs/BotAPI能力矩阵.md`。
>
> **与生产隔离（重要）**
>
> | 项 | 生产 | 本副本 |
> |---|---|---|
> | 容器 / 镜像 | `dzmm-bot` | `dzmm-botapi` |
> | 云端目录 | `/opt/dzmm` | `/opt/dzmm-botapi` |
> | 会话凭据 | 挂载 `.dzmm_token.json` / `.dzmm_login.json` | **刻意不挂载**（两处同时续期会互相踢掉 session） |
> | 告警通道 | 飞书 + 微信 | **已置空**（防本地调试误推生产） |
>
> ⚠️ `.env` 里的 `FEISHU_*` / `WX_*` 在本副本中被置空。**部署为生产前必须从 `../dzmm_cloud/.env` 恢复**，否则线上无告警。
>
> **进度**：W1 探针工具包已就绪（`probe/`），等待平台侧的测试 Bot + 公网 HTTPS 回调。

> 原始说明（仍适用于遗留通道部分）：dzmm.ai 平台的自动化聊天机器人，运行在阿里云 Docker 实例 `47.237.152.187`。
> `dzmm_cloud` 是云端 `/opt/dzmm` 的全量镜像（通过 SSH 免密 scp 同步）。

## 一、架构

单容器 `dzmm-bot`（`dzmm-bot:latest`，Python 3.11-slim + Playwright chromium），`restart: unless-stopped`，时区 `Asia/Shanghai`。

```
本机 Windows (D:\Deepseek\dzmm_cloud)
        │  SSH 免密 (id_ed25519)
        ▼
阿里云 ECS 47.237.152.187  /opt/dzmm
        │  docker compose build / up -d
        ▼
容器 dzmm-bot ── entrypoint.sh ──┬─ headless_relogin.py   登录态检查（启动时）
                                 ├─ renew_token.py        token 续期（每 5 min）
                                 ├─ check_bot_health.py   健康巡检（每 5 min，异常发告警）
                                 ├─ heartbeat.py          心跳（每 2 h）
                                 ├─ bot.py --greet/--sync 定时问候 08/12/22 点 + 同步 12:05/23:35
                                 └─ bot.py 主循环          （崩溃 10s 后自愈重启）
```

**数据持久化**（compose 卷挂载，重建容器不清空）：
`state.json`、`profiles.json`、`novels/`、`bot.log`、`check_health.log`、`renew_token.log`、`.dzmm_token.json`、`.dzmm_login.json`

## 二、目录结构

| 类别 | 文件 |
|---|---|
| 主程序 | `bot.py`（主循环）、`brains.py`（AI 生成）、`dzmm.py`（平台 API） |
| 认证/续期 | `token_store.py`、`renew_token.py`、`auto_recover.py`、`auto_relogin.py`、`headless_relogin.py`、`manual_login.py` |
| 监控/告警 | `check_bot_health.py`、`heartbeat.py`、`feishu_alert.py`、`wx_alert.py`、`online_status.py` |
| 通信 | `socketio.py`、`sio_subscribe.py` |
| 档案/其他 | `profiles.py`、`batch_register.py`、`regen_novel.py`、`env_config.py` |
| 部署 | `docker-compose.yml`、`Dockerfile`、`entrypoint.sh`、`start_vnc.sh`、`dzmm-vnc.service`、`requirements.txt` |
| 文档 | `docs/`（方案A/B、更新日志、自建VPS步骤）、多篇 .md |
| 数据/日志 | `profiles.json`、`state.json`、`*.log`、`novels/` |

## 三、部署

统一用 `deploy.ps1`（需已配好 SSH 免密）：

| 命令 | 作用 |
|---|---|
| `.\deploy.ps1` | 备份远端代码 → scp 上传 → 云端 `docker compose build + up -d` |
| `.\deploy.ps1 -Action status` | 容器状态 + 重启次数 + 巡检日志 |
| `.\deploy.ps1 -Action logs` | bot 日志（默认 50 行） |
| `.\deploy.ps1 -Action health` | 全面体检：容器/资源/进程/登录态/巡检/日志（4 段输出） |
| `.\deploy.ps1 -Action diff` | 本地 vs 云端代码 md5 对比 |
| `.\deploy.ps1 -Action rollback` | 回滚到最近一次代码备份 |
| `.\deploy.ps1 -Action sync` | 从云端全量拉取到本地 |

> 手动流程等价于：`scp bot.py root@47.237.152.187:/opt/dzmm/` → `ssh root@47.237.152.187 "cd /opt/dzmm && docker compose build dzmm-bot && docker compose up -d dzmm-bot"`

## 四、运维命令（SSH）

```powershell
# 看容器 / 资源
ssh root@47.237.152.187 "docker ps -a; docker stats --no-stream dzmm-bot"

# 看日志
ssh root@47.237.152.187 "docker logs --tail 100 dzmm-bot"
ssh root@47.237.152.187 "tail -30 /opt/dzmm/check_health.log"
ssh root@47.237.152.187 "tail -30 /opt/dzmm/renew_token.log"

# 进入容器
ssh root@47.237.152.187 "docker exec -it dzmm-bot bash"
```

## 五、监控与告警

- **心跳**：每 2 小时发一条运行状态到飞书（首次启动立即发）。
- **健康巡检**（`check_bot_health.py`，每 5 分钟）：进程数/双进程、CPU（连续 3 次 >50%）、内存 RSS >500MB、access_token 过期、refresh_token 剩余 <30 天、代理、Socket.IO 断线；异常即时告警 + 恢复通知，去重防刷屏。
- **告警通道**：飞书 webhook（主通道）、微信。
- **token 续期**：每 5 分钟，续期失败连 3 次写 `token.emergency` 信号触发自愈。

## 六、安全注意

- `.env`、`.dzmm_token.json`、`.dzmm_login.json`、`profiles.json` **含敏感信息**，不要提交到公开仓库、不要对外分享。
- `DEEPSEEK_API_KEY`、飞书 webhook 等通过 compose 环境变量从 `.env` 注入，**不写死进镜像**。
- 本目录与云端 `/opt/dzmm` 是同一份数据，本地改动经 `deploy.ps1` 才会上线。

## 七、回滚

1. 代码回滚：`.\deploy.ps1 -Action rollback`（用云端 `backup-code-*.tar.gz`）。
2. 手动回滚：`ssh root@47.237.152.187 "cp /opt/dzmm/bot.py.bak-dsh-20260831 /opt/dzmm/bot.py && cd /opt/dzmm && docker compose build dzmm-bot && docker compose up -d dzmm-bot"`
