# 小小糯 Bot API 迁移 · 实施计划

> 配套文档：`docs/BotAPI迁移方案.md`（决策、能力差异、多 Bot 架构、附录 A 重构清单）
> 代码基线：`dzmm_cloud/` @ 2026-09-22（`bot.py` 6364 行）
> 决策前提：一群一私有 Bot · 保留账号侧车 · Caddy 自动签证书 · 直接切主认证群

---

## 1. 范围

**做**
- 新增官方 Bot API 出入通道（webhook 收 / `send-message` 发 / `sendPhoto` / `pin-message`）
- 传输层抽象，使新旧通道可逐群切换、可秒级回滚
- 多 Bot（一群一 Bot）身份模型：配置、路由、身份显式传递
- 附录 A 的 `NUO_UID` 语义拆分与 40 处改造
- `profiles.json` → SQLite(WAL)（多写者安全）
- 主认证群切换 + 其余 4 群推广 + 旧通道下线

**不做（本期）**
- 重写业务逻辑：`brains.py`（数十个 prompt）、`personas.py`、游戏/投票/公演/算命等
- 改造 `apps/` 前端与 Web GUI
- 平台侧新端点开发（只做探针确认，缺什么由侧车兜）

---

## 2. 前置条件（Gate 0，全部满足才开工）

| # | 条件 | 责任 | 状态 |
|---|---|---|---|
| G1 | 建 1 个**测试**私有 Bot + 4 个正式私有 Bot，取得 `API Token` / `Webhook Secret` | 你（平台 UI） | ☐ |
| G2 | `bot.<你的域名>` A 记录 → `47.237.152.187`，安全组放行 80/443 | 你（DNS/云控制台） | ☐ |
| G3 | Caddy 部署 + 证书签发成功（`curl -I https://bot.<域名>/healthz` 返回 200 且证书有效） | 我 | ☐ |
| G4 | 确认糯糯在 5 个群均为**群主/管理员**（私有 Bot 添加权限硬要求） | 你 | ☐ |
| G5 | 确认玫瑰房（`4a56be71-…`）是 group 而非 scenario | 探针 W1 | ☐ |

> ⚠️ G4 不满足的群**无法添加私有 Bot**，只能该群保留旧通道。

---

## 3. 目标架构速览

```
                    ┌──────────────────────────────────────────┐
   平台 webhook ───▶│ webhook_server.py (aiohttp, Caddy 反代)   │
   POST /webhook/   │  1. 按 bot_key 取身份                     │
   <bot_key>        │  2. 校验 X-Telegram-Bot-Api-Secret-Token  │
                    │  3. update_id 幂等去重 (sqlite WAL)        │
                    │  4. 入 asyncio.Queue → 立即 200           │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ worker（复用现有 dispatch 业务逻辑）        │
                    │  Incoming + BotIdentity → 现有 handler     │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ Transport 接口                            │
                    │  ├─ BotApiTransport   (send-message)      │
                    │  └─ LegacyTransport   (Sender/Playwright) │
                    └──────────────────────────────────────────┘
                                    ▲
     账号侧车（只读+管理，禁发言）────┘  读历史/查资料/踢人/认证/私聊
```

**关键复用点（已核实，抽象面很小）**
- 所有发送汇聚到 `bot.py:6027 _send(sender, cid, text, dry)` + `Sender.send()`（`bot.py:351`）→ **只有 2 个发送口子**
- 接收侧接口固定：`SioSubscriber.drain(cid)` / `drain_private()` / `need_resync(cid)` / `clear_resync(cid)` / `is_connected`（`sio_subscribe.py:36-68`）
- **`requirements.txt` 已有 `aiohttp>=3.8`** → webhook 服务无需引入新框架

---

## 4. 新增模块与接口契约

### 4.1 `botmeta.py` — Bot 身份注册表

```python
@dataclass(frozen=True)
class BotIdentity:
    key: str        # "nuo_main"
    name: str       # "小小糯～"      # A.3：@我 判定用，不再查成员表
    short: str      # "小小糯"
    cid: str        # 绑定群（1:1）
    token: str      # X-Bot-Token（从环境变量注入）
    secret: str     # webhook secret
    persona: str    # personas 键
    sidecar_uid: str

REGISTRY: dict[str, BotIdentity]
def by_key(key) -> BotIdentity          # 未知 key → raise
def by_cid(cid) -> BotIdentity | None
def by_secret(secret) -> BotIdentity | None
# 启动自检：断言 1 bot : 1 cid；token/secret 非空；映射与 personas 一致
```

### 4.2 `transport/base.py` — 传输抽象

```python
@dataclass(frozen=True)
class Incoming:
    bot_key: str; cid: str; msg_id: str
    sender_uid: str; sender_name: str
    text: str; ts: float
    is_mention_me: bool          # 探针确认有 mention 实体则精确判定，否则用 botmeta.name 匹配
    raw: dict

class Transport(Protocol):
    async def send_text(self, cid, text) -> bool
    async def send_photo(self, cid, url, caption="") -> bool
    async def pin(self, cid, msg_id) -> bool

class EventSource(Protocol):
    async def start(self) -> None
    async def stop(self) -> None
    def drain(self, cid) -> list[Incoming]
    def is_healthy(self) -> bool
```

### 4.3 `webhook_server.py` — 入站

```
POST /webhook/{bot_key}
  404  未知 bot_key
  401  secret 校验失败（hmac.compare_digest）
  200  {"ok":true}   ← 去重入队后立即返回，绝不等待业务处理
GET  /healthz       ← Caddy / 巡检探活
```

- 幂等表：`dedupe.db`，`CREATE TABLE seen(update_id TEXT PRIMARY KEY, ts REAL)` + `PRAGMA journal_mode=WAL`
- **必须立即 200**：公演/发骚/小说要跑几十秒，同步处理必然超时重投
- 同群串行：worker 内按 `cid` 分桶，保证单群内消息顺序（否则状态机错乱）

### 4.4 `context_store.py` — 每群环形缓冲

Bot API **无历史接口**，用它补"最近 N 条上下文"：
```sql
CREATE TABLE msg(cid TEXT, seq INTEGER, sender_uid TEXT, sender_name TEXT,
                 text TEXT, ts REAL, is_bot INTEGER, PRIMARY KEY(cid, seq));
CREATE TABLE cursor(cid TEXT PRIMARY KEY, next_seq INTEGER);
```
保留每群最近 200 条；`recent(cid, n)` 供 `get_messages_all` 的调用点替换。

### 4.5 `state_store.py` — `profiles.json` → SQLite(WAL)

`profiles.py` 的 `load_profiles/save_profiles/get_profile/upsert_profile` 四个函数改为 SQLite 实现，**保持函数签名不变**（调用点零改动）。原 JSON 首启动导入一次。

---

## 5. 工作分解（WBS）

### W1 · 探针与能力确认（1 天）— 阻塞后续设计

| # | 任务 | 交付/验收 |
|---|---|---|
| W1.1 | 最小 webhook 回显服务（aiohttp 单文件）+ Caddy 反代 | `curl` 打通，平台能投递 |
| W1.2 | 建测试私有 Bot，加入测试群 | 收到首个 Update |
| W1.3 | 跑 8 项验证（见 §6.1） | 能力矩阵落表 |
| W1.4 | 定 A.3 方案（mention 实体 or 名字匹配） | 接口冻结 |
| W1.5 | 确认玫瑰房群类型；确认批量建 Bot 是否触发风控 | 迁移清单定稿 |

### W2 · 传输层抽象（2～3 天）— 此阶段现网行为必须零变化

| # | 任务 | 涉及 | 验收 |
|---|---|---|---|
| W2.1 | 新增 `botmeta.py` + `bots.json` + 启动自检 | 新 | 1:1 断言生效，配置错误 fail fast |
| W2.2 | 新增 `transport/base.py`；`Sender` 包装为 `LegacyTransport`；`_send`(6027) 与 `Sender.send`(351) 收敛到接口 | bot.py | `SEND_TRANSPORT=legacy` 全功能回归通过 |
| W2.3 | `SioSubscriber` 抽为 `LegacyEventSource` | sio_subscribe.py | 主循环消费方式不变 |
| W2.4 | `context_store.py` 环形缓冲，替换 `get_messages_all` 的上下文调用点 | 新 + bot.py | 上下文长度/顺序正确 |
| W2.5 | **A.0 拆分**：`NUO_UID` → `SIDECAR_UID` + `ctx.bot_uid`；A.1/A.2 共 29 处机械替换 | bot.py | grep 无遗留裸 `NUO_UID` 判等 |
| W2.6 | A.3：`is_at_me`/`_strip_at`/`_detect_perf_query` 改读 `botmeta.name` | bot.py:568/578/786 | @ 我 与剥离正常 |
| W2.7 | A.5：转写标签 `"小小糯: "` → per-bot 显示名 | bot.py:5663 | 多 Bot 转录可区分 |
| W2.8 | `state_store.py` SQLite + JSON 导入 | profiles.py | 并发写压测无丢失 |
| W2.9 | 长消息分片（>10000 字符） | transport | 公演小说不截断 |

### W3 · Bot API 通道（2～3 天）

| # | 任务 | 验收 |
|---|---|---|
| W3.1 | `webhook_server.py`（路由/secret/幂等/队列/healthz） | 401/404/200 语义正确 |
| W3.2 | `BotApiTransport`（send_text / send_photo / pin） | 三类发送实测成功 |
| W3.3 | worker：按 cid 串行 + 复用现有 dispatch | 单群顺序不乱 |
| W3.4 | 失败重试/退避 + 飞书告警 | 断网注入后能自愈并告警 |
| W3.5 | 压测：连发 100 条 | 不丢不重、顺序正确 |

### W4 · 影子运行（1～2 天）

| # | 任务 | 验收 |
|---|---|---|
| W4.1 | "只记不发"差异日志（Bot API 实发 + legacy 记录对比） | 差异报告可读 |
| W4.2 | 对比项：回复内容一致性、端到端延迟、失败率 | 达标（§6.3） |

### W5 · 切换主认证群（1 天）— 见 §7 Runbook

| # | 任务 | 验收 |
|---|---|---|
| W5.1 | 侧车"闭嘴"：代码层禁用侧车 send | 侧车零发言 |
| W5.2 | 单一发言人锁 + 启动断言 | 双链路同时可发言 → 拒绝启动 |
| W5.3 | `SEND_TRANSPORT=botapi`（仅主群） | 主群正常收发 |
| W5.4 | 观察 2 小时 | 无重复、无漏回 |

### W6 · 推广与收尾（2 天 + 7 天观察）

| # | 任务 | 验收 |
|---|---|---|
| W6.1 | 其余 4 群逐个切换（每群观察 30 分钟） | 全群 Bot API |
| W6.2 | 下线 Socket.IO 接收 / Playwright / `renew_token` 强依赖 | 容器内存显著下降 |
| W6.3 | 更新 `README.md`、运维手册、告警阈值 | 文档与实现一致 |
| W6.4 | 7 天观察期 + 复盘 | 无 P0/P1 |

**总计约 9～13 个工作日 + 7 天观察**

---

## 6. 测试与验收

### 6.1 W1 探针清单（8 项，逐项记录结论）

1. Webhook 推送**全部群消息**还是仅 @/指令？
2. 是否推送**入群/退群**事件？
3. Update 是否带 **mention 实体**？
4. 能否 **@ 人 / 引用回复**？
5. 发送**频率限制**、失败重试语义、`update_id` 是否重放？
6. 是否存在**文档外端点**（成员列表/资料/踢人/认证）？
7. 玫瑰房是 group 还是 scenario？
8. Bot 实体**是否有群管理员权限**？

### 6.2 回归基线（W2 必须全绿）

`SEND_TRANSPORT=legacy` 下，以下逐项与现网比对：收消息、回消息、@我、`/查`、`/发骚`、`/羞辱`、`/公演开始·结束`、`/投票`、`/算命`、`/骚度排行`、骰子（含私聊 `/看骰`）、定时问候、冷清冒泡、入群筛查、踢人、记忆读写。

### 6.3 影子达标线（W4 → 进 W5 的闸门）

| 指标 | 阈值 |
|---|---|
| 消息丢失率 | 0 |
| 重复回复 | 0 |
| 回复失败率 | < 1% |
| 端到端延迟 P95 | ≤ legacy 的 1.2 倍 |

---

## 7. 切换 Runbook（主认证群直切）

```powershell
# 0) 切前检查：侧车 cookie 有效 + 筛查干跑（认证群特有，防筛查空窗）
ssh root@47.237.152.187 "docker exec dzmm-bot python -c \"import dzmm;print(dzmm.get_verified('a56cbd67-3a1e-439a-8beb-d1557dbeff79'))\""

# 1) 上线新代码（含侧车闭嘴 + 单一发言人锁），先全群保持 legacy
.\deploy.ps1

# 2) 确认启动断言通过、webhook 健康
ssh root@47.237.152.187 "docker logs --tail 50 dzmm-bot | grep -E 'botmeta|SEND_TRANSPORT|assert'"
curl -I https://bot.<域名>/healthz

# 3) 仅主群切 Bot API
ssh root@47.237.152.187 "cd /opt/dzmm && sed -i 's/^SEND_TRANSPORT=.*/SEND_TRANSPORT=botapi_bd7fe6a4/' .env && docker compose up -d dzmm-bot"

# 4) 观察 2 小时：重复回复 / 漏回 / 筛查
ssh root@47.237.152.187 "docker logs -f --tail 100 dzmm-bot"
```

> `SEND_TRANSPORT` 支持 `legacy` / `botapi_<cid8>` / `botapi`，实现逐群灰度。

---

## 8. 回滚 Runbook

| 层级 | 动作 | 耗时 |
|---|---|---|
| **L1 换通道** | `SEND_TRANSPORT=legacy` + `docker compose up -d` | ~30 秒 |
| **L2 代码回滚** | `.\deploy.ps1 -Action rollback` | ~2 分钟 |
| **L3 平台侧** | 群里移除 Bot（不影响侧车与旧账号） | 手动 |

**自动回退判据**（写进健康巡检）：Webhook 连续失败 ≥ 5 次 / 重复回复告警触发 / 发送失败率 > 10% / Bot 被移出群 → 自动切回 `legacy` 并发飞书告警。

---

## 9. 上线后观察指标

| 指标 | 来源 | 告警线 |
|---|---|---|
| webhook 2xx 率 | 服务日志 | < 99% |
| 队列积压深度 | 服务指标 | > 50 |
| 派发延迟 P95 | 服务指标 | > 30s |
| 发送失败率 | transport | > 1% |
| 幂等去重命中数 | dedupe.db | 突增 |
| 筛查失败/空窗 | 侧车 | 任何一次 |
| 容器内存 RSS | check_bot_health | > 500MB（预期大幅下降） |

---

## 10. 风险登记册（本期）

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| 双通道重复回复 | 中 | 高 | 单一发言人锁 + 启动断言 + 逐群灰度 |
| G4 不满足 → 某群加不进 Bot | 中 | 中 | 该群保留 legacy；先跑 G4 确认 |
| 玫瑰房为 scenario | 中 | 中 | 探针 W1.5；该群保留 legacy |
| 无历史接口致上下文退化 | 高 | 中 | W2.4 环形缓冲 + 侧车补历史 |
| 侧车 cookie 失效 → 筛查空窗 | 中 | 高 | 切前干跑 + 告警 + 人工兜底 |
| 10000 字符上限 | 中 | 低 | W2.9 分片 |
| Bot 无管理员权限 | 中 | 中 | 探针 W1.8；权限动作全留侧车 |
| 批量建 Bot 触发风控 | 低 | 中 | 探针 W1.5；分批创建 |

---

## 11. 交付物清单

- [ ] `docs/BotAPI实施计划.md`（本文）
- [ ] `docs/BotAPI能力矩阵.md`（W1 产出）
- [ ] `botmeta.py` / `bots.json`（脱敏模板）
- [ ] `transport/{base,botapi,legacy}.py`
- [ ] `webhook_server.py`
- [ ] `context_store.py` / `state_store.py`
- [ ] `webhook_diff.py`（W4 影子对比）
- [ ] Caddyfile + 部署脚本更新
- [ ] 更新后的 `README.md` 运维手册
- [ ] 切换/回滚 Runbook 实操记录

---

## 12. 需要你确认或执行的项

| # | 事项 | 归属 |
|---|---|---|
| 1 | 建 5 个私有 Bot 并交付 token/secret（**先给测试 Bot 即可开工 W1**） | 你 |
| 2 | DNS A 记录 + 安全组 80/443 | 你 |
| 3 | 确认糯糯在 5 个群均为群主/管理员 | 你 |
| 4 | 确认玫瑰房是 group（或在群里试加 Bot） | 你 / W1 |
| 5 | 观察期内的重复回复/异常反馈 | 你 |
