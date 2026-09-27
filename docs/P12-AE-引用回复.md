# P12-AE：别人右键引用回复我，也接话（+ 出站"挂引用"的探针结论）

> 起因（屋主 2026-09-27）：
> 1. "在用户第一次 @ 若瑾说话，若瑾回复后，如果用户在回复的内容上通过右键点击
>    回复后，若瑾应该自动回复。"
> 2. "若瑾回复别人的话应该在别人 @ 她或者回复她的那句话基础上回复，就是右键点击
>    回复的那种。"
>
> 上一期 P12-AD（`docs/P12-AD-…`）修的是"**答得连不连贯**"；这一期修的是
> "**什么时候该答**"和"**答复挂在哪**"。

---

## 1. 先探针：这两件事平台到底给不给做

`tools/probe_reply_capability.py`（零副作用；出站用全 0 UUID 当目标群，永远
403 not a member，不可能投递到任何真实群）。

### 入站 ✅ 有确凿依据

被"右键点击回复"的那条，平台留在入站消息里：

```json
"content": {
  "text": "你也是",
  "type": "text",
  "reference": {
    "id": "470750e7-38e5-4d1f-966f-553075047766",
    "sentBy": "9938211c-b245-4cad-a594-cd9a1eb4966a",
    "content": {"text": "被引用的原文", "type": "text"}
  }
}
```

- 线上实测 40 条里 3 条带 `reference`（`tools/probe_reply_capability.py --only in`）。
- ⚠️ 原文在 `reference.content.text`，**不是** `reference.text`（两层都认，
  免得形状一变就静默失灵）。
- 于是"他回复的是不是我"是**可判定**的（比"从文本里猜像不像在跟我说话"可靠）。

### 出站 ❌ 官方 Bot API 做不到

判定手法：候选字段填**非法值**。若该字段在 zod schema 里，校验会先失败并返回
`fieldErrors`（`pin-message` 就是这个形状）；不在 schema 里则被静默忽略。

对照（证明这套判定看得见校验）：

| 请求 | 结果 |
|---|---|
| `chatroom_id="not-a-uuid"` | **400** `{"error":"Invalid request body","details":{"fieldErrors":{"chatroom_id":["UUID 格式不正确"]}}}` |
| `chatroom_id=<全0UUID>`（基线） | 403 `Bot is not a member of this chatroom` |

15 个候选字段全部 **403**（= 没进 schema）：

```
reply_to_message_id  reply_message_id   quote_message_id    quoted_message_id
parent_message_id    reference_message_id  reply_to         reply_to_id
in_reply_to          message_id         reply               replyToMessageId
quoteMessageId       parentMessageId    replyTo
```

B 形态（Telegram 兼容 `bot<token>/sendMessage`）同样忽略
`reply_to_message_id` / `reply_parameters`。

**结论：把回复"挂"在某条消息上，官方 Bot 通道做不到。**

---

## 2. 本期实现（入站那半）

| 位置 | 改动 |
|---|---|
| `bot.py` | 新增 `_ref_of()` / `_quotes_self()`：从 `content.reference` 取出被引用那条（`id`/`sentBy`/`text`） |
| `bot.py` 分派扫描 | `@ 我` **或**（开关打开且）`引用回复了我` → 都进同一条问答分支；记 `_via_quote` |
| `bot.py` 问答分支 | 引用触发时，把**被引用的原文**也摆进上下文；已经在上下文里就**不重复塞**（上一期刚修过"同一句喂两遍"，别在新功能上再犯一次） |
| `webhook_server.py` | 以前只取 `content.type/text`，`reference` **被整个丢掉** ⇒ 走 webhook 那条路永远看不出"他在回复我"；现在原样透传 |
| `runtime/features.py` | 新功能 `quote_reply`，**默认关** ＝ 今天的行为（只有 @ 才答） |
| `tools/enable_quote_reply.py` | 按群打开（走 `config_store.save()`：校验 → 备份 → 原子写 → 热加载） |

为什么默认关：它是一条**新增的触入口**，不做就等于今天。默认打开会在所有群
多出一种回答时机，玫瑰房那套"谁能触发管家"的房间策略也会被绕过。
`tests/test_feature_registry.py` 里为此留了一份写明理由的例外清单
（`_DELIBERATE_OFF`），不让"默认关"悄悄变多。

### 一键按群打开

```bash
docker cp tools/enable_quote_reply.py dzmm-bot:/app/          # tools/ 不进镜像（.dockerignore）
docker exec dzmm-bot python /app/enable_quote_reply.py                       # dry-run
docker exec dzmm-bot python /app/enable_quote_reply.py --apply --cid-prefix f4ec4c21
```

---

## 3. 已上线与实测

```
P12: 功能 共=66 已接线=66 待接线=0 默认关=quote_reply 秒退=关
已写入。生效值：quote_reply = True        ← 上班群（f4ec4c21）
按群热加载后的实际取值：f4ec4c21…|quote_reply: True
```

`verify_p12ad.py --llm`（真调模型，证明上一期的上下文修复也在这版里生效）：

```
第 1 轮  用户： 我是大傻逼
         若瑾： 谁准你自己下结论的。要骂也是我说了算。你是什么、值不值，
                轮不到你抢答。站好，把刚才那句收回去，换个说法——说"我该听若瑾的"。
第 2 轮  用户： 你也是
         若瑾： 我是什么，也轮不到你定义。你现在有两件事没做完：收回那句蠢话，
                然后照我说的改口。别拿"你也是"糊弄过去，那是躲，不是答。
```

对照修之前线上那两句（"说两遍，是想让我记住…" / "我不太确定你在回应哪句话…
那话我还没说"）：`断片特征句：无`。

其它：容器 md5 与本地逐字节一致；`deploy_gate.py` = **ALL CHECKS PASSED**；
巡检 `巡检正常，无告警`；容器 `restarts=0`。

---

## 4. 屋主的决定（2026-09-27）

**第 2 条：保持若瑾-bot 身份，不做引用。** 理由就是上面那张表的代价：
为了"挂引用"改走账号，上班群里她的回复会从「若瑾」变成「小小糯～」，
代价大于收益。所以官方通道的这条限制**接受**，不再绕。
（将来若平台给 `send-message` 加上回复字段，`tools/probe_reply_capability.py`
一跑就知道 —— 探针留在仓库里就是为了那天。）

**开关范围：上班群 + 认证群。**

```
19:46:39 P12: 热加载 功能 · f4ec4c21…|quote_reply : 跟随默认 → 开   ← 上班群
19:51:25 P12: 热加载 功能 · bd7fe6a4…|quote_reply : 跟随默认 → 开   ← 认证群
```

玫瑰房**不开**：那个群的"谁能触发管家"是 `_is_room_trigger` + `_room_should_reply`
一套完整策略（客人只能被婉拒、只有主人能下命令），引用触发会绕过它。
要用就在那个群单独开，并先想清楚要不要保留那条婉拒规则。

---

## 5. 复盘

- **"能不能做"要先探，不要先写。** 出站那半如果在写代码之后才发现做不到，
  白改四个文件的发送链签名。
- **探针要有对照。** 15 个候选全 403 本身不能说明"字段不被支持"——也可能是
  探测手法无效；是那条 `chatroom_id` 的 400 fieldErrors 对照让结论站得住。
- **文档与代码同步钉住**：功能数 66、`people` 分类 12、默认关的例外清单
  `{quote_reply}` 都在测试里，改一边漏一边会当场红。
