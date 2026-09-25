# 小小糯 Bot 迁移方案：会话自动化 → 官方 Bot API（Webhook）

> 依据：《Bot 接入指南》（laopo.ai/studio/bots/docs）
> 现状代码基线：`dzmm_cloud/`（bot.py、brains.py、personas.py、dzmm.py、sio_subscribe.py、Sender）
> 撰写日期：2026-09-22

---

## 一、结论先行

**官方 Bot API 能替掉的是"运维痛点"，不是"业务逻辑"。**

| | 内容 |
|---|---|
| **能替掉（当前 80% 故障源）** | cookie/登录态、token 续期、撞验证码、noVNC 远程桌面、Playwright 常驻页面池发送、Socket.IO 长连接与节点故障切换 |
| **替不掉的（文档未覆盖）** | 读历史、成员列表与昵称、成员资料（性别/简介/签名）、踢人、认证状态、私聊、入群事件、scenario 群 |
| **必须原样保留** | `bot.py` 约 6 千行业务逻辑、`brains.py` 数十个 prompt、`personas.py` 分群人设、`profiles.json`/`state.json` |

**所以不能整体平移。** 建议路线：**能力探针 → 传输层抽象 → Bot API 通道影子运行 → 特权能力处置 → 逐群切换**。
其中**特权能力**（认证群筛查、查档案、骰子私聊）是本次迁移的真正难点，且其可行性取决于平台是否提供文档之外的端点——这必须先探明，再定方案。

---

## 一·补、已定决策（2026-09-22）

| 议题 | 决策 | 含义 |
|---|---|---|
| 特权能力 | **方案 A：保留账号侧车** | 消息收发全走 Bot API；另留一个账号侧车专做读历史/查资料/踢人/认证/私聊。cookie 依赖不消除，但不再承担"发送"这一最大痛点 |
| 公网 HTTPS | **有域名，走 Caddy 自动签证书** | 子域名 A 记录指向 `47.237.152.187`，放行 80/443，Caddy 自动申请续期 Let's Encrypt |
| 试点范围 | **直接主认证群**（`bd7fe6a4-…`） | 见效最快，但风险最高 → 见第十一节《主群直切专项设计》 |
| Bot 数量 | **一群一个私有 Bot（N bot / 1 服务 / 1 代码）** | 身份按群隔离，但**不按群拆部署**；详见第十节 |

---

## 二、现状实现（已核实）

| 能力 | 现在的实现 | 位置 |
|---|---|---|
| 收消息 | Socket.IO 长连接监听 `message:new`（主通道）+ 600s HTTP 兜底对账 | `sio_subscribe.SioSubscriber`、`bot.py:4270-4276` |
| 发消息 | **Playwright 常驻 page 池**，每群一个 page（LRU 上限 3），另有 `_fallback_send` | `bot.Sender`（`bot.py:134`、`send()` 351、`_fallback_send` 462） |
| 拉历史 | tRPC `chatroom.getMessages`（`get_messages` / `_all` / `_since`） | `dzmm.py:37-87` |
| 成员昵称 | tRPC `chatroom.getMembers` → `{uid: fullName}` | `dzmm.py:101` |
| 成员资料 | tRPC `user.getChatroomUser`（性别/简介/签名） | `dzmm.py:90` |
| 踢人 | tRPC `groupChat.removeMember`（POST） | `dzmm.py:143` |
| 认证状态 | tRPC `verification.identity` / `identityBulk` | `dzmm.py:159-176` |
| 私聊 | `chat.listAll` 里 `chatType=one_on_one` → 骰子发牌、`/看骰` | `dzmm.py:126`、`bot.py:4334` |
| 登录态 | `token_store` + `renew_token.py`(5min) + `headless_relogin`/`auto_recover`/`manual_login` + `captcha_novnc`（noVNC 人工过验证码） | 多文件 |
| 部署 | 阿里云 ECS `47.237.152.187`，Docker 单容器，`entrypoint.sh` 拉起 5 个常驻进程 | `docker-compose.yml`、`README.md` |

**服务范围**（`bot.py:11-47`）：5 个群（含 Rose 505 玫瑰房）；3 个群启用入群筛查；2 个群启用定时推送；玫瑰房有独立人设（`personas.ROOMS`）。

---

## 三、目标形态（官方 Bot API）

| 项 | 内容 |
|---|---|
| 认证 | `API Token`（请求头 `X-Bot-Token`）+ `Webhook Secret` |
| 收消息 | 平台 POST 到 Webhook，body 为 **Telegram Update 兼容结构**，头带 `X-Telegram-Bot-Api-Secret-Token` |
| 发消息 | `POST /api/bot/send-message`（`chatroom_id` + `content`，≤10000 字符） |
| 兼容接口 | `POST /api/bot/bot<token>/sendMessage`、`sendPhoto`（**仅 URL 图片，无 multipart**） |
| 置顶 | `POST /api/bot/pin-message` |
| 硬性要求 | Webhook 必须公网 **HTTPS**（拒绝 localhost/内网/非 HTTPS）；Bot 须由**群主或管理员**在**普通群聊**的 `+` 面板添加 |
| 硬性限制 | **scenario 群聊不展示 Add Bot 入口**；Bot 不在群内时接口返回 403 |

---

## 四、能力差异表（本方案的核心）

| 能力 | 现状 | 官方 Bot API | 影响的功能 |
|---|---|---|---|
| 收群消息 | Socket.IO 实时 | ✅ Webhook 推送 | 全部（可替换） |
| 发文本 | Playwright page | ✅ `send-message` | 全部（**最大收益**） |
| 发图片 | 页面 | ⚠️ 仅 URL 图片 | 若有本地图/文件发送需改造 |
| 置顶 | 页面 | ✅ | — |
| **读历史** | `getMessages` | ❌ 未文档化 | 发骚/羞辱的上下文、公演时间线、`我不在时群里发生了什么`、表现总结、随机吐槽取材 |
| **成员昵称** | `getMembers` | ❌ | @ 解析、`is_at_me`、排行、按名查人 |
| **成员资料** | `getChatroomUser` | ❌ | `/查`档案、`/注册`、XP 提炼、算命、**性别筛查** |
| **踢人** | `groupChat.removeMember` | ❌ | 认证群「踢游客/踢男性」 |
| **认证状态** | `verification.identity` | ❌ | 认证群「未认证提醒」 |
| **私聊** | `one_on_one` | ❌（文档限普通群聊） | 吹牛骰子私聊发牌、`/看骰` |
| **入群事件** | Socket.IO 成员事件 | ❌（文档只写 message） | 欢迎语、新人即时筛查 |
| 主动推送 | Sender | ✅ 定时任务自行调用 | 定时问候、冷清冒泡（**待探针确认无频率限制**） |
| scenario 群 | 玫瑰房在用 | ❌ 无入口 | **玫瑰房可能无法迁移**（待确认其群类型） |

> 备注：`discover_listall.json` 中已确认「认证妹子群」「裳姐姐和她的骚贱小母狗」为 `chatType=group`；平台另存在 `{title, persona, mode}` 形状的 scenario 会话。**玫瑰房属于哪一类尚未确认**——这是探针必查项。

---

## 五、迁移路线

### Phase 0 · 能力探针（0.5～1 天，**决定整个方案形状，必须先做**）

1. 在创作者中心建一个**测试 Bot**（私有），Webhook 指向临时公网 HTTPS 地址。
2. 用 Cloudflare Tunnel 之类临时打通公网回调（无需先买域名）。
3. 逐项验证并记录：
   - [ ] Webhook 是推送**全部群消息**，还是仅 @/指令？
   - [ ] 是否推送**入群/退群**事件？（决定欢迎语与即时筛查）
   - [ ] Update 里是否带 **mention 实体**？（决定 `is_at_me` 怎么重写）
   - [ ] 能否 **@ 人 / 引用回复**？（现有多处文案依赖 @）
   - [ ] 发送频率限制、失败重试语义、是否带 `update_id` 重放
   - [ ] **是否存在文档外的端点**：成员列表 / 成员资料 / 踢人 / 认证 / 入群事件
   - [ ] 玫瑰房（`4a56be71-…`）是 group 还是 scenario
   - [ ] Bot 实体是否有群管理员权限
4. **产出**：能力矩阵 → 决定 Phase 3 走 A/B/C 哪条路。

### Phase 1 · 传输层抽象（2～3 天，业务逻辑零改动）

- 新增 `transport/` 层，定义统一接口：
  - 出：`send_text(cid, text)` / `send_photo(cid, url, caption)` / `pin(cid, msg_id)`
  - 入：`IncomingEvent`（cid, sender_uid, sender_name, text, msg_id, ts, is_mention_me）
- `bot.py` 中散落的 `_send(...)` / `sender.send(...)` 收敛到接口；`SioSubscriber` 收敛为 `EventSource` 的一个实现。
- **自建每群环形缓冲**（sqlite 或 jsonl），替代 `getMessages` 提供"最近 N 条上下文"——这是弥补历史接口缺失的关键。
- **按第十节落地多 Bot 形态**：引入 `BotIdentity` 显式参数 + `bot_key → cid` 配置表 + webhook 路由；把 `is_at_me`/`_strip_at` 从"查成员表取自己名字"改为"读配置名 / mention 实体"。
- **同步做附录 A 的 `NUO_UID` 拆分**：把"Bot 自己"与"侧车账号"两个语义彻底分开命名。
- 目标：新旧通道可切换，`brains.py`/`personas.py`/游戏逻辑**一行不改**。

### Phase 2 · Bot API 通道 + 影子运行（3～5 天）

- 新增 webhook 服务（FastAPI/aiohttp，独立进程或 sidecar）：
  1. 校验 `X-Telegram-Bot-Api-Secret-Token`（**不是** `X-Bot-Secret`）
  2. 按 `update_id` 幂等去重
  3. **立即返回 200**，事件入 `asyncio.Queue`
  4. worker 消费队列，复用现有 dispatch 逻辑
- **关键设计点**：公演/发骚/小说生成要几十秒到几分钟，绝不能在 webhook 里同步跑；必须"秒回 200 + 后台处理"。
- 影子模式：先只接**测试群**，或同一事件双通道只发一条，对比回复质量与延迟。

### Phase 3 · 特权能力处置（取决于 Phase 0 结论）

| 方案 | 做法 | 代价 |
|---|---|---|
| **A（推荐，若探针未发现对应端点）** | 保留一个**账号侧车**：只做读历史/查资料/踢人/认证/私聊等只读+管理操作；**消息收发全走 Bot API** | 侧车仍需 cookie，但它不再承担发送（最大痛点），失效影响面大幅缩小 |
| **B（最干净）** | 向平台要 Bot 侧端点（成员资料/踢人/入群事件），拿不到就退 A | 依赖平台排期，不可控 |
| **C** | 砍掉筛查/档案/私聊 | 认证群核心功能丢失，不推荐 |

### Phase 4 · 逐群切换 + 清理（2～3 天）

- 一次切一个群，**主群（认证群）最后**；保留 kill switch（环境变量回退旧通道）。
- 切完可下线：Socket.IO 接收、Sender/Playwright、`renew_token`/`headless_relogin`/`captcha_novnc`（走方案 B 时）。
- 保留：`brains.py`、`personas.py`、`profiles.json`/`state.json`、飞书告警、`check_bot_health`。

---

## 六、部署与网络改造

- ECS 已有公网 IP，但**需要 HTTPS 域名**（平台拒绝非 HTTPS）。二选一：
  - **Caddy + Let's Encrypt**：把一个（子）域名解析到 `47.237.152.187`，放行 80/443，自动签证书（最省事）
  - **Cloudflare Tunnel**：不想动域名/开端口时的替代
- 容器编排：新增 webhook 服务；`entrypoint.sh` 去掉对 cookie 的强依赖（若走方案 B）。
- 回调安全：只认 `X-Telegram-Bot-Api-Secret-Token`；`API Token` 与 `Webhook Secret` 走 compose 环境变量注入（沿用现有 `.env` 模式，不写死进镜像）。

---

## 七、数据与状态

- **好消息**：Webhook 的 `chat.id` 就是现有 `chatroom_id` → `state.json` / `profiles.json` 的键可复用。
- **坏消息**：Bot 是**独立实体**，不再是 `NUO_UID` 用户号。所有「排除自己」的逻辑（排行榜排除、发情值排除、`is_at_me`、@ 解析）都要按新身份重写。
- 私聊相关状态（吹牛骰子）需重新安置。
- 过渡期会出现**双身份并存**，必须明确"谁是小小糯"，否则人格分裂 + 重复回复。

---

## 八、风险清单

| 风险 | 等级 | 对策 |
|---|---|---|
| 双通道重复回复 | **高** | 切群隔离 + 环境变量 kill switch |
| 踢人/查资料能力降级 | **高** | Phase 3 方案 A 侧车；认证群保留人工兜底流程 |
| 玫瑰房若是 scenario → 无法加 Bot | 中 | Phase 0 确认；必要时该群保留旧通道 |
| 10000 字符上限 | 中 | 公演小说/长文分片发送 |
| 历史接口缺失导致上下文质量下降 | 中 | Phase 1 自建环形缓冲 + 落盘 |
| Bot 无管理员权限 | 中 | Phase 0 确认 |
| 主动推送/定时问候受限 | 低 | Phase 0 确认 |

---

## 九、工作量估算

| 阶段 | 工期 |
|---|---|
| Phase 0 探针 | 0.5～1 天 |
| Phase 1 传输层抽象 | 2～3 天 |
| Phase 2 Bot API 通道 + 影子 | 3～5 天 |
| Phase 3 特权能力 | 0～5 天（看方案） |
| Phase 4 切换 + 清理 | 2～3 天 |
| **合计** | **约 2～3 周（单人）** |

---

## 十、目标架构：多 Bot（一群一 Bot）形态

**采用形态：N 个 Bot / 1 套服务 / 1 份代码。禁止按群 fork `bot.py`**（6 千行 fork 5 份 = 放弃可维护性）。
Bot 是**配置维度**，不是部署单元。

### 10.1 设计要点

| 维度 | 设计 |
|---|---|
| Bot 配置表 | `bot_key → {name, avatar, token, secret, 绑定 cid, persona, owners}` |
| 入站路由 | 单 webhook 端点 + 每 Bot 路径段 `/webhook/<bot_key>`，据此选 secret 校验与身份上下文 |
| 身份传递 | `BotIdentity` 作为**显式参数**贯穿业务逻辑；**废弃 `set_persona_cid` 的 contextvar 隐式人设**——同进程多 Bot 必然串台 |
| 绑定约束 | 断言 1 Bot : 1 cid；收到不匹配的 `chat.id` → 告警并忽略 |
| Bot 自身名字 | 改为**配置项**，不再走 `getMembers`（Bot API 无成员接口）；`is_at_me` 依赖它 |
| 跨群共享状态 | `profiles.json` → **SQLite(WAL)**（见 10.3） |
| 每群状态 | `state.json` 按 cid、记忆 `_get_memory(cid)`、发情值、排行、投票、公演状态——**原本就按群隔离，无需改** |
| 账号侧车 | 仍然只有 **1 个**，承担读历史/查资料/踢人/认证/私聊（方案 A） |

### 10.2 收益与代价

**收益**
- 每群独立**名字与头像**——当前单账号做不到（人设只能靠文本切换）
- 人设串台从"靠自觉"变成"结构上不可能"：`bot.py:4280-4284` 那段每轮 `set_persona_cid("")` 的补丁，是为 2026-09-17「研讨群早安变成玫瑰房管家口吻」事故打的
- 爆炸半径隔离：某 Bot 被踢/被限流/撞审查不波及其他群
- 私有 Bot 别人加不进别的群 → 天然锁群
- 走 Bot API 后无 chromium，README 里"1.8G 内存别多开页面"的约束消失，多实例边际成本极低

**代价**
- 凭据 1 套 → N 套：`API Token` 只显示一次，**重新生成会使旧 token 立即失效** → 轮换会造成该群短暂中断，需一条轮换流程
- 需要 webhook 路由与每 Bot 独立限流/重试统计
- 共享状态需改造（10.3）
- `NUO_UID` 约 40 处重构（**附录 A**）——注：即便只上 1 个 Bot 也必须做，故此处边际成本≈0

### 10.3 必改：`profiles.json` 的并发写

`profiles.py` 自述"全局，按人整合"，且因挂载点上 `os.replace` 会 `Device busy`，只能 `write_text` **原地写、无锁**。单进程时没事；**拆成多进程/多写者后会丢更新甚至写坏**。
档案是跨群共享的（同一人可能在认证群与研讨群都注册过），**不能按群拆成 N 份**，必须换成 SQLite(WAL) 或集中到单一写进程。

### 10.4 待探针确认

- 同一账号批量创建多个 Bot 是否触发平台风控
- 私有 Bot 添加权限：文档要求"创建者本人 + 自己是目标群的群主或管理员"。5 个群需逐个确认糯糯的管理员身份——**尤其玫瑰房屋主是 Vv姐姐**，`personas.ROOMS` 里糯糯只是"半个主人"（那是 Bot 侧逻辑，不等于平台管理员权限）。确认不了就加不进去

---

## 十一、主群直切专项设计（因决策为"直接主认证群"）

主认证群是筛查最重、流量最大的群，直切必须额外做四件事：

### 1. 身份并存：侧车"闭嘴"
Bot 是独立实体，加入后群内会同时存在**两个小小糯**（原账号 + Bot）。约定：
- 原账号 → 降级为**静默侧车**：只提供读档案/踢人/认证/私聊接口，**禁止任何发言**（代码层禁用其 send 路径，而非靠配置自觉）。
- 建议把原账号改名为「小小糯·档案室」之类，避免群友 @ 错人。
- Bot 实体承担全部聊天与游戏。

### 2. 单一发言人锁（防重复回复，最高风险）
- 引入 `SEND_TRANSPORT=legacy|botapi` 环境变量，**逐群生效**。
- 主群任一时刻只允许一条发送链路；侧车在主群只放行数据类操作，`send` 直接拒绝并记日志。
- 启动时断言：若同一 cid 同时有两条链路可发言 → 拒绝启动（fail fast，不靠人记得）。

### 3. 秒级回滚
- 旧通道**常驻不删**。回滚 = 改 `SEND_TRANSPORT=legacy` + 重启容器，**不需要重新部署或重新登录**。
- 回滚判据提前写死：重复回复、筛查失效、Bot 被移出群、Webhook 连续失败超阈值 → 自动或人工回退。

### 4. 双跑验证改为"只记不发"
- 不并行发言。切换前先让 **Bot API 实发、旧通道仅记录差异日志**（对比同一消息两条链路的产出与延迟）。
- 观察 10～30 分钟无异常后再正式切的。

### 5. 筛查连续性（认证群特有）
踢游客/男性、认证提醒都依赖**侧车 cookie**。切群期间必须确认侧车 cookie 有效，否则认证群出现**筛查空窗**。
- 上线前跑一次 `verification.identity` + 一次干跑筛查，确认侧车链路可用；
- 保留人工兜底：筛查失效时立即群内告警（复用 `feishu_alert`），由管理员手工处理新成员。

---

## 十二、下一步

Phase 0 的**凭据无关部分我可以立即开工**（webhook 接收器骨架：secret 校验 → `update_id` 幂等 → 立即 200 → 队列 + worker），这部分不依赖 token。

需要你做的两件事：

1. **建测试 Bot**：创作者中心 `/studio/bots` 建一个**私有** Bot，记下 `API Token` 与 `Webhook Secret`（token 只显示一次）。
2. **配 DNS**：确定用于回调的子域名（例：`bot.你的域名`），A 记录指向 `47.237.152.187`，并确认安全组放行 80/443。

拿到这两项后按顺序推进：Phase 0 探针（8 项验证）→ 用结论锁定 Phase 3 侧车接口清单 → Phase 1 传输层抽象。

---

## 附录 A、`NUO_UID` 重构清单

`bot.py` 中 `NUO_UID` 共约 40 处引用（含 1 处 import、1 处定义），按语义分 6 类。**行号为撰写时基线**。

### A.0 最重要的一条：必须拆成两个概念

当前 `NUO_UID` **同时**承担了两个语义，迁移后它们会分叉：

| 概念 | 含义 | 迁移后 |
|---|---|---|
| `BOT_UID` | "小小糯"**自己发的消息**（回声过滤、排除自己） | 变成 **Bot 实体**的 id，**每群不同** → 从 webhook `from.id` / Bot 配置取 |
| `SIDECAR_UID` | 那个**干脏活的登录账号** | 仍是原 `503e3cb9-…`，**不再发言** |

> ⚠️ 两者不分开命名，是最容易埋隐性 bug 的地方（例如把侧车的历史消息当成"自己刚说的"过滤掉，或把 Bot 自己的消息当成群友统计进发情值）。

### A.1 回声过滤：跳过自己发的消息（15 处，机械替换）

改为 `uid == ctx.bot_uid`（建议同时过滤 `ctx.sidecar_uid`，防止侧车历史消息污染上下文）：

`453`（发送探活）、`2686`（随机吐槽取材）、`3763`、`3790`（公演时间线，⚠️含 `🤖` 前缀剥离与非剧情过滤逻辑，非纯替换）、`3950`（防自引用循环）、`4191`、`4339`（私聊）、`4420`（`had_new` 判定）、`4488`（房间回复，防 self-echo）、`4841`、`4929`、`5206`（`_room_should_reply`）、`5640`（recap）、`6018`（入群扫描）、`6310`（问候去重）

### A.2 从聚合中排除自己（14 处，机械替换）

改为 `uid != ctx.bot_uid`：

`1010`（骚度排行）、`1036`（`_add_heat`）、`1174`（发情值增量）、`2652`（随机抽人）、`3424`（投票名单）、`3521`、`3524`（投票加热）、`3600`、`3611`（剧情主角解析）、`3705`、`3707`（剧情结束加热）、`3831`（公演 M 方判定，同时排除 `OWNER_UID`，**该处 OWNER_UID 保留**）、`4431`（活跃度/发言统计）、`5040`（高潮判定）

### A.3 @我 检测与剥离（3 个函数 / 5 处，**必须重新设计**）

`is_at_me`（`568`、`569`、`571`）、`_strip_at`（`578`）、`_detect_perf_query`（`786`）

当前实现是 `get_full_name(NUO_UID)` / `get_name(NUO_UID)` —— **从成员表缓存里查自己的名字**。Bot API 下**拿不到成员表**，所以：
- 名字改为**每 Bot 配置项**（`bot_key → name`）；
- 若探针确认 Update 带 **mention 实体**，优先用它做精确判定（比字符串匹配可靠）；
- 多 Bot 同进程时，这里必须读 `ctx` 的 Bot 名字，不能读全局。

### A.4 发送身份（1 处 + `Sender` 内部）

`472`：`d.send_message(cid, NUO_UID, text, cookie)` —— legacy Socket 发送，以账号身份发出 → **整体被 Bot API 的 `API Token` 发送取代**（`Sender` 类及其 Playwright page 池一并退役）。

### A.5 转写标签硬编码（1 处）

`5663`：`lines.append("小小糯: " + t[:100])` —— 给 AI 的转录里标注"这是 Bot 说的" → 改为 per-Bot 显示名（否则多 Bot 时转录全叫"小小糯"，AI 无法区分）。

### A.6 保持不动：这些是真人 uid

`OWNER_UID`（`951` 代注册、`4559` 公演审核、`4701`、`3831` 中的部分）、`NUONUO_UID` / `VV_UID`（`4920`、`5450` 房间屋主与称谓）。
这些与 Bot 身份无关，**不要顺手替换**。

### A.7 定义处

`50`：`NUO_UID = "503e3cb9-…"` → 重命名为 `SIDECAR_UID`（新语义：登录账号），并新增每群 `BOT_UID`。


