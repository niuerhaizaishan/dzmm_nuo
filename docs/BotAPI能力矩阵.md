# Bot API 能力矩阵（W1 探针产出）

> 状态：**待填**（跑完 `probe/` 后回填）
> 基线：`docs/BotAPI实施计划.md` §6.1
> 这份表的结论会直接决定：A.3 mention 方案、Phase 3 侧车接口清单、玫瑰房能否迁移

---

## 1. 八项验证结论

| # | 问题 | 探针结论 | 证据 | 对方案的影响 |
|---|---|---|---|---|
| Q1 | 推送**全部群消息**还是仅 @/指令？ | 待填 | `/report` | 决定上下文从何而来（全量→自建缓冲即可；仅@→必须靠侧车补历史） |
| Q2 | 是否推送**入群/退群**事件？ | 待填 | `/report` 类型分布 | 决定欢迎语与**新人即时筛查**能否保留 |
| Q3 | Update 是否带 **mention 实体**？ | 待填 | `/report` entities | 有→`is_at_me` 精确判定；无→退化为按配置名匹配 |
| Q4 | 能否 **@ 人 / 引用回复**？ | 待填 | `api_probe` 探针2/3 | 影响大量含 @ 的文案 |
| Q5 | **频率限制** / 重试语义 / `update_id` 重放？ | 待填 | `/report` + `api_probe` | 决定退避与幂等策略强度 |
| Q6 | 是否存在**文档外端点**（成员/资料/踢人/认证）？ | 待填 | `api_probe` 扫描 ★ 项 | **决定 Phase 3 走 A 还是 B** |
| Q7 | 玫瑰房是 group 还是 scenario？ | 待填 | 平台 UI | scenario → 该群必须保留旧通道 |
| Q8 | Bot 实体**是否有群管理员权限**？ | 待填 | 平台 UI | 决定踢人等管理动作是否必须留在侧车 |

---

## 2. 隐藏端点扫描结果（Q6）

跑 `python probe/api_probe.py --skip-send` 后回填。**非 404 即可能存在**。

### A 形式 `POST /api/bot/<kebab>` + `X-Bot-Token`

| 端点 | 状态 | 说明 |
|---|---|---|
| `send-message` | 200（已知） | 对照组 |
| `pin-message` | | |
| | | |

### B 形式 `POST /api/bot/bot<token>/<Camel>`

| 端点 | 状态 | 说明 |
|---|---|---|
| `sendMessage` | | |
| `sendPhoto` | | |
| `getMe` | | |
| `getChatMember` | | |
| `getChatAdministrators` | | |
| `getUpdates` | | |
| `deleteMessage` | | |
| `leaveChat` | | |
| `banChatMember` | | |

**结论**：若 `getChatMember` / `getChatAdministrators` / 踢人类端点为 404 → Phase 3 必须走**方案 A（侧车）**，侧车接口清单如下：

- [ ] 读历史 `chatroom.getMessages`
- [ ] 成员资料 `user.getChatroomUser`
- [ ] 成员列表 `chatroom.getMembers`
- [ ] 踢人 `groupChat.removeMember`
- [ ] 认证 `verification.identity` / `identityBulk`
- [ ] 私聊 `chat.listAll`（`chatType=one_on_one`）

---

## 3. Update schema 实录（首条样本）

```json
（粘贴 /report 输出的 [样本] 段）
```

用于写 `transport/base.py` 的 `Incoming` 映射。

---

## 4. 意外发现 / 待跟进

| 发现 | 影响 | 跟进 |
|---|---|---|
| | | |
