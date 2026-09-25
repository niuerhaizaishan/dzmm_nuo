# 参考 DDZM-bot 重构小小糯 · 方案

> 参考对象：`D:\Deepseek\DDZM` —— **摸鱼公司群机器人**（`rule.md` 第 1 行：「摸鱼公司群机器人：全局规则」）
> 权威依据：`DDZM/docs/DZMM_ACCOUNT_AND_MESSAGE_INTEGRATION_GUIDE.md`（2026-09-10，以当前仓库实现为准）
> 历史资料：`DDZM/docs/BOT_MESSAGE_TRANSPORT_HANDOFF.md`（自述为历史交接，长消息路由描述已过时）
> 被重构对象：`dzmm_cloud/`（小小糯，bot.py 6364 行单体）
> 撰写日期：2026-09-22

---

## 0. 先纠正一件事

我此前把"摸鱼公司系统"错认成 `摸鱼工作台`（`wb_*.py`，本地 8899 的 UI 工具）。
**"摸鱼公司群机器人"就是 `DDZM/`**，`DDZM/docs` 里的是它自己的规范。两者是不同的系统：

| | 摸鱼工作台 | DDZM-bot（摸鱼公司群机器人） |
|---|---|---|
| 形态 | 本地 Windows 单进程 UI（8899） | 服务器多服务 + PostgreSQL |
| 角色 | 运营者的**辅助工具**（我看数据、生成台词） | **群内机器人本体**（自己收发消息、跑玩法） |
| 参考价值 | 低（架构目标不同） | **高（就是小小糯要变成的样子）** |

下面全部以 `DDZM/` 为准。

---

## 1. DDZM-bot 架构速览（已核实）

```
src/dzmm_bot/
├─ core/       业务：app.py(153KB) commands.py(208KB) repository.py(1.28MB!)
│              schema.py(150KB) service.py reply_templates.py(97KB)
│              + 玩法模块：texas_holdem / number_bomb / birthday / dark_market
│                company_lottery / red_packet / shop_cards / performance …
├─ admin/      FastAPI 管理后台：app.py(116KB) + static/admin.js(268KB) + index.html(104KB)
├─ ai/         AI worker：worker / memory_worker / impressions / social_context
├─ browser/    平台连接：aikda_socket(26KB) session(15.5KB) worker(33.7KB)
│              core_client(20KB) bot_api(1.6KB ← 官方 Bot API 客户端)
└─ runtime/    契约层：contracts.py(冻结 dataclass) outbound.py(长消息阈值)
               settings.py(env 校验式配置)
```

| 维度 | DDZM-bot 的做法 |
|---|---|
| 数据 | PostgreSQL + SQLAlchemy 2.0 + **Alembic 40+ 迁移**（`migrations/versions/`） |
| 进程 | **systemd 五服务**：core / browser-worker / ai-worker / ai-memory-worker / admin-web |
| 部署 | `deploy/scripts/{deploy,provision}.sh` + `deploy/env/dzmm.example.env`（生产密钥在 `/etc/dzmm/dzmm.env`，600） |
| 契约 | `runtime/contracts.py`：`InboundMessage` / `OutboundMessage` / `WorkerHeartbeat` / `LoginState` |
| 配置 | `Settings.from_environment()` 冻结 dataclass + 逐项校验（必填/端口/范围） |
| 测试 | pytest，`tests/{core,admin,ai,browser}/` 与 src 同构 |
| 文档 | `docs/superpowers/plans/` **一功能一方案**（30+ 篇，带日期） |

**注意（不要照抄的地方）**：DDZM-bot 自己也有巨型文件——`core/repository.py` **1.28 MB**、`commands.py` 208 KB、`admin.js` 268 KB。
"参考"要学的是它的**分层、契约、出站队列、迁移、部署、测试纪律**，不是它的文件体量。

---

## 2. 三条硬约束（DDZM-bot 已实测验证，直接决定小小糯怎么改）

这三条来自集成指南 §8.3/§8.5/§9，是整个方案的地基。

### 约束 1：官方 Bot **不能替代**普通账号

| 要发什么 | 默认通道 |
|---|---|
| 普通群文本（已配置 Bot） | **官方 Bot API 优先**，失败才回退普通账号 Socket |
| **私聊文本** | 普通账号 Socket（**Bot 不处理私聊**） |
| **需要撤回的消息** | 普通账号 Socket（只有它能 `message:recall`） |
| **图片** | 普通账号 Socket + `chatroom.uploadImage` |
| **小说/故事集分享** | 普通账号 Socket，`content.type=share / shareType=novel` |

→ 小小糯的**骰子私聊**、**公演小说**、以及 `_rollback_last_id` 撤回逻辑，**都必须留在侧车**。

### 约束 2：普通账号有 **1000 字符 / 10 个换行** 限制

`runtime/outbound.py`：`BOT_GROUP_MAX_CHARS = 1000`、`BOT_GROUP_MAX_NEWLINES = 10`，`requires_bot_group_sender()` 判定超限。
Bot API 实测可发 20 行（19 换行）、单行 2000 字符。
→ 小小糯的长内容（公演小说、发骚场景、算命长文、公演点评）**必须走 Bot API，不能走账号 Socket**。

### 约束 3：Webhook **故意未接入**

集成指南 §7.1 与交接手册明确：

> 当前 DZMM 应用**没有 Webhook HTTP endpoint**……这是有意的：在没有幂等与路由职责改造之前，直接同时开启会导致重复处理。

若接入，必须二选一：**只让 Webhook 处理 Bot 所在群而浏览器排除该群**，或**两者写同一个幂等入口 + `message_id` 数据库唯一约束**。

---

## 3. 对前两版 Bot API 方案的修正（重要）

参考 DDZM-bot 后，我之前方案里**两个假设被推翻**，需要改正：

| 我之前写的 | 实际情况（DDZM-bot 已验证） | 修正 |
|---|---|---|
| ❌ 「Playwright/浏览器发送可以整体下线」 | 私聊 / 撤回 / 图片 / 分享**只能**走普通账号 Socket | 浏览器通道**必须保留**，改为承担"低频非实时"职责 |
| ❌ 「Webhook 作为主入站通道」 | DDZM-bot **故意未接入**，怕重复处理；且 §7.5 明确**不再依赖历史补拉**（怕额外调用触发限流/验证码） | 入站**保留 Socket 为主**；Webhook 降级为"完成幂等+职责划分后的可选增强" |
| ✅ 「Bot API 承担群消息」 | 与 DDZM-bot 一致，且是 **Bot 优先**（不是"仅长消息"） | 维持，并改为 Bot 优先 + 账号兜底 |
| ✅ 「账号侧车必须保留」 | 与 DDZM-bot 的 Browser Worker 完全一致 | 维持，且职责边界照抄上表 |

### 由此得到的关键推论

**目标不是"消灭登录态"，而是"让登录态变得不关键"。**

因为私聊/撤回/图片/分享注定要留在普通账号，所以：

- **高频、实时、易触发验证码**的部分 → 群文本收发 → 交给 Bot API
- **低频、非实时**的部分 → 私聊/撤回/图片/分享 → 留在侧车，验证码/deep 掉线时可容忍

这比"整体替换"务实得多，也是 DDZM-bot 用生产实践走出来的路。

---

## 4. 现状对照（小小糯 → DDZM-bot 范式）

| 维度 | 小小糯现状 | DDZM-bot | 差距等级 |
|---|---|---|---|
| 代码形态 | `bot.py` 6364 行单体 + brains.py 75KB | `src` 包 + core/admin/ai/browser/runtime 分层 | **高** |
| 数据层 | `profiles.json`（单文件、**无锁非原子写**）/ `state.json` | PostgreSQL + SQLAlchemy + Alembic | **高** |
| 入站 | Socket.IO 为主 + **600s 历史对账** | Socket.IO 为主，**故意不做历史补拉** | 中 |
| 出站 | Playwright 页面池**直接发**，无队列无重试 | **Core 出站队列 + Worker 租约**（outbox） | **高** |
| 发送身份 | 仅普通账号 | 普通账号 + 官方 Bot 双身份，按内容类型路由 | **高** |
| 契约 | 裸 dict 到处传 | frozen dataclass 契约 | 中 |
| 配置 | `GROUPS`/`CIDS`/阈值**硬编码在 bot.py 顶部** | `Settings.from_environment()` 校验式 | 中 |
| 写入管控 | 谁都能调 `_send()`，无审计 | 能力表 + 出站队列 + 状态回写 | **高** |
| 进程 | 单容器 + `entrypoint.sh` 5 个 bash 循环 | systemd 五服务，各有 env 与重启策略 | 中 |
| 测试 | 4 个集成测试（核心逻辑零覆盖） | pytest 分域覆盖 | **高** |
| 运维界面 | 无（SSH + `docker logs`） | FastAPI 管理后台（群配置/人工登录/状态） | 中 |
| 文档纪律 | 散落 .md | `docs/plans/` 一功能一方案 | 低 |

---

## 5. 目标架构

```
小小糯（重构后）
├─ core/          业务：命令路由 / 状态机（发骚·羞辱·调教·公演·剧情·投票）/ 玩法
├─ runtime/       契约：InboundMessage / OutboundMessage / WorkerHeartbeat / Settings
├─ platform/      平台连接（＝ DDZM 的 browser/）
│   ├─ session.py        唯一登录者：浏览器 Profile / 取 Token / tRPC
│   ├─ socket_gw.py      Socket.IO 入站（收群消息 + 私聊）
│   ├─ sender_account.py 普通账号出站（私聊/撤回/图片/分享）
│   ├─ sender_bot.py     官方 Bot 出站（群文本优先）
│   └─ worker.py         出站租约 / 路由 / 限流
├─ ai/            LLM 网关（回退链 + 用量 + 自测）——可通过 prompt 外置与模型回退获益
├─ admin/         运维后台（群配置、登录状态、出站队列、最近错误）
├─ migrations/    Alembic
└─ tests/         pytest，分域
```

### 五个必做的机制（照抄 DDZM-bot 的设计意图）

| 机制 | 做法 | 解决的问题 |
|---|---|---|
| **出站队列（outbox）** | 业务只写 `outbound_messages`；Worker 领租约 → 按会话串行/限速发 → ACK 后回写 `platform_sent_id` | 多玩法抢连接、重复重试、消息乱序、频率超限、发没发出去不知道 |
| **双身份发送路由** | 群文本 → Bot 优先、账号兜底；私聊/撤回/图片/分享 → 账号 | DDZM 已验证的内容类型矩阵 |
| **入站幂等** | `(chatroom_id, platform_message_id)` 唯一约束后入库 | 重复投递不重复扣币/开局/发奖 |
| **唯一登录者** | 只有 `session.py` 能登录；其它模块**代码层面没有登录函数** | 相互 401 / 429 / 验证码风暴 |
| **令牌桶 + 冷却** | 普通账号令牌桶；遇「请稍后再试」→ 60s 冷却并释放任务 | 平台限流 |

### 不变的部分（红线）

- `brains.py` 的 **prompt 语义**、`personas.py` 人设、游戏规则 → **语义零变更**
- 数据文件路径（`state.json` / `profiles.json` / `novels/`）→ 迁移期不变，DB 化后保留导入/导出
- 已有 `state.json` / `profiles.json` 数据 → 首启动一次性导入

---

## 6. 实施路线

**核心排序原则（借用 DDZM 的"先换外壳、验证，最后动内层"）：**
先把**结构**做对并建立安全网，再动**传输**；且**不要打磨即将被删除的代码**。

| 阶段 | 内容 | 验收 | 回滚 |
|---|---|---|---|
| **R0** | **立即做，与重构无关**：noVNC 公网暴露+弱密码加固；`profiles.json`/`state.json`/`novels/` 数据备份 | 6080 不再公网可达；备份可还原 | — |
| **R1** | 抽取**契约层** `runtime/contracts.py` + `Settings.from_environment()` 统一配置 | 启动即校验，配置错误 fail fast | 保留旧常量 |
| **R2** | **建立安全网**：保全清单（逐项列全功能）+ pytest 冒烟（命令识别 / 发情值结算 / 状态机流转） | 清单可逐项打勾；冒烟全绿 | — |
| **R3** | **出站队列**：业务改写出站表，Worker 统一发送（先只接旧通道） | 发消息行为与现网一致；无重复 | 关闭队列走直发 |
| **R4** | **双身份发送**：接官方 Bot（群文本优先、账号兜底）；长消息阈值 1000/10 | 测试群短/长/私聊/撤回四类各验一次 | `BOT_ENABLED=0` |
| **R5** | **唯一登录者 + 令牌桶 + 冷却**：收敛现有 5 处可登录路径 | 只一处登录；无相互 401 | 退回各自登录 |
| **R6** | 数据层 DB 化（PostgreSQL + Alembic），JSON 一次性导入 | 档案/状态读写等价；并发写不丢 | JSON 仍在，可切回 |
| **R7** | 业务拆分：`bot.py` 按域拆 `core/` 模块（低风险顺序：状态存取器 → 魔数集中 → prompt 外置 → 状态机独立） | 每拆一步回归全绿 | 按模块回退 |
| **R8** | 运维后台（群配置、登录状态、出站队列、最近错误） | 不再依赖 SSH 排查 | 后台可停 |
| **R9** | **可选**：Webhook 入站（仅在完成幂等 + 群职责划分后） | 同一 `message_id` 重投不重复执行 | 关闭 Webhook |

体量：R1–R5 是主干（约 2–3 周），R6–R8 是结构治理（约 2–3 周），R9 可选。

> R9 的**前置闸门**：`(chatroom_id, message_id)` 唯一约束已在库；且明确"哪些群由 Webhook 负责、哪些由 Socket 负责"。不满足就不接——这与 DDZM-bot 的判断一致。

---

## 7. 风险

| 风险 | 等级 | 对策 |
|---|---|---|
| 群文本双身份**重复发送** | **高** | 出站队列 + 平台未明确 ACK 时**不换通道重发**（DDZM 铁律） |
| 私聊/撤回误走 Bot → 直接失效 | **高** | 路由表按内容类型硬编码；Bot 客户端**不该有**私聊/撤回接口 |
| 数据 DB 化迁移丢档 | 高 | 一次性导入 + 校验条数 + JSON 保留 |
| 入站开 Webhook 后重复处理 | 高 | R9 前置闸门；不满足不接 |
| 拆分 `bot.py` 引入回归 | 中 | R2 安全网先行；每步可回退 |
| 出站队列改造期间消息积压 | 中 | 队列深度告警；保留直发开关 |
| 照抄 DDZM 导致同样巨型文件 | 中 | 只学分层/契约/纪律，不学 1.28MB 的 repository.py |

---

## 8. 待你确认

1. **R0 是否立刻做**（noVNC 暴露 + 零备份，是**现行**生产风险，不依赖任何重构）
2. **架构落点**：跟 DDZM-bot 一样上 **PostgreSQL + systemd**，还是保持 **Docker 单容器**（改动小、但拿不到多服务的好处）？
3. **两张计划的关系**：本方案（结构重构）与 `BotAPI实施计划.md`（传输迁移）合并成一个program，还是先做哪个？
   - 我的建议：**R0 → R1 → R2 → R3/R4（＝Bot API 迁移的 W2/W3）→ R5 → R6+**，即"先建安全网、再换传输、最后治理结构"。
4. **是否复用 DDZM-bot 现成代码**：`browser/bot_api.py`（45 行，Bot API 客户端）可以直接移植；`runtime/outbound.py`（长消息阈值/分片）也可以。要不要移？
