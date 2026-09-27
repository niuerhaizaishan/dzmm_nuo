# Bot API 能力矩阵（W1 探针产出）

> ⚠️ **本文档已过期（2026-09-27 全面体检标记）**：状态仍写「Q1/Q2/Q3 待 webhook 接入」，而 Webhook 接收端早已上线运行。
> 只作历史资料参考；当前事实以代码与 `docs/全面体检报告-2026-09-27.md` 为准。
> 体检时逐条核对过，过期点如上。

> 基线：`docs/BotAPI实施计划.md` §6.1
> 状态：**进行中** —— Q6 已定论；Q1/Q2/Q3 待 webhook 接入；Q4/Q5 待测试群；Q7/Q8 待人工
> 探针版本：`probe/api_probe.py`（含只读安全默认 + 响应体判定）、`probe/webhook_probe.py`

---

## 1. 结论摘要（最重要的一条）

**Q6 的答案是：文档之外没有可用端点。**
21 个候选只读端点（成员列表 / 成员资料 / 认证 / 历史 / webhook 信息 / getUpdates …）在两种调用形式下**全部**返回 `{"ok":false,"message":"Invalid method"}`；而同一批请求里，已知端点返回的是完全不同的形状（见 §3 对照）。判定逻辑经对照验证，不是误判。

**因此 Phase 3 锁定为方案 A：保留账号侧车。** 读历史 / 查资料 / 踢人 / 认证 / 私聊这些能力，官方 Bot API 一个都不提供，必须由侧车承担——这不再是"待定项"。

---

## 2. 八项验证结论

| # | 问题 | 结论 | 证据 |
|---|---|---|---|
| Q1 | 推送**全部群消息**还是仅 @/指令？ | ⏳ 待 webhook | — |
| Q2 | 是否推送**入群/退群**事件？ | ⏳ 待 webhook | — |
| Q3 | Update 是否带 **mention 实体**？ | ⏳ 待 webhook | — |
| Q4 | 能否 **@ 人 / 引用回复**？ | ⏳ 待测试群 | — |
| Q5 | **频率限制** / 重试语义 / `update_id` 重放？ | ⏳ 待测试群 | — |
| **Q6** | **是否存在文档外端点？** | ✅ **不存在** | §3 |
| Q7 | 玫瑰房是 group 还是 scenario？ | ⏳ 待人工 | — |
| Q8 | Bot 实体是否有群管理员权限？ | ⏳ 待人工 | — |

---

## 3. Q6 实测：端点扫描结果

**方法**：`POST /api/bot/<kebab>` + `X-Bot-Token`，以及 `POST /api/bot/bot<token>/<Camel>`，共 21 个候选。

### 对照（证明判定可靠）

用**已知端点 + 不存在的 chatroom_id**（全 0 UUID，安全、不会投递到任何群）取"方法存在"的响应形状：

| 端点 | HTTP | 响应 |
|---|---|---|
| `send-message` | **403** | `{"ok":false,"error":"Bot is not a member of this chatroom"}` |
| `pin-message` | **400** | `{"ok":false,"error":"Invalid request body","details":{"fieldErrors":{"message_id":["UUID 格式不正确"]}}}` |
| `bot<token>/sendMessage` | **200** | `{"ok":false,"message":"Bot not found or not in this chatroom"}` |

与上面形状不同的即不存在：

| 候选端点 | 结果 |
|---|---|
| `get-me` / `get-chat` / `get-members` / `get-chat-members` / `get-chat-member` / `get-chat-member-count` / `get-chat-administrators` / `get-user` / `get-updates` / `get-verified` / `get-profile` / `get-webhook-info` / `webhook-info` | 全部 `Invalid method` |
| `getMe` / `getChat` / `getChatMember` / `getChatMemberCount` / `getChatAdministrators` / `getUpdates` / `getWebhookInfo` / `getUserProfilePhotos` | 全部 `Invalid method` |

> 保留意见：我探的是 **21 个合情理的命名**。若有命名完全不同的端点，扫不出来。但结合《Bot 接入指南》只列 4 个接口，可以认为**接口面就是文档里那些**。

### 侧车接口清单（方案 A 定稿）

侧车（`503e3cb9-…` 账号）必须继续承担：

- [ ] 读历史 `chatroom.getMessages`
- [ ] 成员资料 `user.getChatroomUser`（性别/简介/签名）
- [ ] 成员列表 `chatroom.getMembers`
- [ ] 踢人 `groupChat.removeMember`
- [ ] 认证 `verification.identity` / `identityBulk`
- [ ] 私聊 `chat.listAll`（`chatType=one_on_one`）

---

## 4. 实测发现（API 行为）

| 发现 | 说明 | 对实现的影响 |
|---|---|---|
| **不能用 HTTP 状态码判存在性** | 未知方法返回 **200** + `{"ok":false,"message":"Invalid method"}` | 探针与 client 都要解析响应体 |
| **两套处理器形态不同** | A 形式 `/api/bot/<kebab>` 返回真实状态码（400/403）+ `error` 字段；B 形式 `/api/bot/bot<token>/<Camel>` 一律 **200** + `message` 字段 | `BotApiTransport` 要**分别**解析两种错误形状 |
| **`pin-message` 的 `message_id` 必须是 UUID** | Zod 校验：`{"fieldErrors":{"message_id":["UUID 格式不正确"]}}` | `Incoming.msg_id` 需按 UUID 透传，不能自造字符串 |
| **Token 有效且已认证成功** | 无 401；`send-message` 能正确报出"Bot is not a member" | 说明 `X-Bot-Token` 生效，Bot 身份可解析 |
| **403 语义与文档一致** | `Bot is not a member of this chatroom` | 与文档"Bot 必须已加入目标群，否则 403"吻合 |
| **未知方法不泄露信息** | 统一 `Invalid method`，不区分"存在但无权" | 安全上不错，但让我们无法枚举接口 |
| **本机必须走代理** | 直连 dzmm.ai / laopo.ai 遭 SNI 阻断（DNS 污染 + TLS RST） | 本地调试 `--proxy http://127.0.0.1:7890` |

---

## 5. Update schema 实录

⏳ 待 webhook 接入后由 `probe/webhook_probe.py` 的 `/report` 输出填入（用于写 `transport/base.py` 的 `Incoming` 映射）。

---

## 6. 待跟进

| 事项 | 阻塞什么 |
|---|---|
| 域名 + DNS 解析到 `47.237.152.187`（80/443 已放行） | Q1/Q2/Q3，以及整个 W3 |
| 测试群 chatroom_id，并把 Bot 加进去 | Q4/Q5（发送类测试） |
| 玫瑰房群类型确认 | Q7，决定该群能否迁移 |
