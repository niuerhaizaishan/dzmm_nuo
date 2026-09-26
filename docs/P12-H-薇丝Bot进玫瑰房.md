# P12-H 追加记录（2026-09-26）：第二个 Bot「薇丝-bot」进玫瑰房 + 多 Bot 身份

## 做了什么

| 项 | 内容 |
|---|---|
| **新 Bot** | `weisi_rose`（薇丝-bot / 简称 薇丝），绑定 **Rose（505）玫瑰房**（`4a56be71…`） |
| Token | 写进服务器 `/opt/dzmm/.env` 的 `DZMM_BOT_TOKEN_ROSE`（**不进配置文件、不进镜像**） |
| Webhook Secret | 同样存进 `.env` 的 `DZMM_BOT_SECRET_ROSE`。**当前用不到**：出站只认 `X-Bot-Token`，而 webhook 是"平台往我们推事件"用的，本项目收消息走 Socket.IO/账号（`runtime/contracts.py::BotIdentity.secret` 是给 P10 预留的字段） |
| compose | `docker-compose.prod.yml` **逐键透传**（没有 `env_file`）⇒ 新增的 token 必须在 compose 里显式列一行，否则 `.env` 里配了也进不了容器 |
| 配置 | `config.json` 的 `bots[]` 增加一条；`schema v3 → v4`（v4 只加了可选的 `bots[].uid`） |
| 工具 | `tools/cfg_bots.py`：list / add / set-uid / enable / disable / rm，一律走 `config_store.save()`（校验 → 备份 → 原子写） |

## 顺手补掉的两个真实缺口（不然这个 Bot 根本加不进来）

1. **`_wrap_with_bot_routing()` 只读环境变量**（`DZMM_BOT_TOKEN` / `DZMM_BOT_CID`）。
   也就是说 P11 做的 `bots[]` 注册表**运行期从来没人读** —— 页面上能加 Bot、
   配置里也存得住，但发送链只会用"env 里那一个"。这正是 P12 一直在清的那类
   "看起来能配、实际不生效"。现在唯一真源是 `_CFG.bots`：每个 `enabled` 且能解析到
   token 的 Bot 各拿一个 `DzmmBotSender`，绑到它自己的 `cid`；配置里一个都没有时
   才退回 env 那条老路（兼容没挂 config.json 的旧部署）。
2. **"我自己"只认一个 Bot**（`runtime/identity.py` 的 `BOT_UID` 来自单个 env）。
   两个 Bot 之后，少认一个的后果是：新 Bot 在自己群里发的消息被当成群友 ——
   轻则统计污染，**重则自问自答**（兜底聊天会把 Bot 的问候当用户输入）。
   现在 `identity.register_bots(所有启用的 Bot)`：uid 进 `self_uids()`、名字进 `self_names()`
   （所以 `@薇丝-bot` 也算 @ 我）。配置里没写 uid 时有三层兜底：
   - 启动时按**成员表显示名**认一次（`resolve_bot_uids`）；
   - 循环里用昵称缓存兜底（`learn_bot_uid`，不发请求）；
   - 平台侧填了 uid 就写进配置，最确定。

   匹配规则刻意收紧（`薇丝` / `薇丝-bot` / `薇丝bot` / `薇丝～` 算；`薇丝的小号`、
   `薇丝（505房）` 不算）。**还必须跳过已经是自己的 uid** —— 侧车账号叫「小小糯～」、
   Bot 简称叫「小小糯」，第一版就真的把账号认成了 Bot（日志
   `Bot nuo_main 的身份 = 已认到 503e3cb9`），虽然危害不大但会让日志说谎，已修 + 有测试。

## 生产验收（本次）

```
1) Token 有效性（安全探测：send-message 打一个不存在的 chatroom_id）
   → HTTP 403 {"ok":false,"error":"Bot is not a member of this chatroom"}
   = 平台认得这个 token（无效会 401），且**没有发出任何消息**
2) 前置事实：Bot 会出现在群成员表里（认证群里 DZMM_BOT_ID = eee88c3d '小小糯-bot' ✓）
   ⇒ 玫瑰房成员表 23 人里没有薇丝-bot ⇒ **平台侧还没把它拉进房间**
3) 部署：upload → build → 闸门（ALL CHECKS PASSED）→ compose（备份 + validate + recreate）
   → 容器内 `DZMM_BOT_TOKEN_ROSE` 已注入
4) 加 Bot：tools/cfg_bots.py add … → 校验通过、已备份、原子写；顺手把小糯 bot 的
   平台 uid（eee88c3d…）写进配置（不再依赖 env）
5) 重启后自证：
   P11: 我的身份数=3（账号 1 + Bot 2）
   P11: 我的身份 Bot key=weisi_rose name=薇丝-bot cid=4a56be71 uid=**未配置**
   bot路由: Bot weisi_rose(薇丝-bot) -> 群 4a56be71 已就绪（uid=未配置）
   bot路由: 已启用 覆盖群=bd7fe6a4,4a56be71      ← 玫瑰房已进路由
   P11: Bot weisi_rose name=薇丝-bot cid=4a56be71 token=已设置
   P11: Bot weisi_rose 的身份 = **没认到**（Bot 可能还没被拉进群；进群后发第一条消息时会自动认）
```

## ⚠️ 还差一步（在平台上做，我做不了）

**把「薇丝-bot」拉进「Rose（505）玫瑰房」**。在此之前：

- 群成员表里没有它（已核实），所以身份"没认到"是对的；
- 往玫瑰房发消息时会拿到 `403 Bot is not a member of this chatroom`；
  按 `runtime/routing.py` 的铁律，这属于**明确拒绝 ⇒ 安全回退账号**
  ⇒ 房间照常由侧车账号发言，**不会丢消息**（只是每次多一次失败的 Bot 请求 + 一行日志）。
- 拉进群之后：无需再改任何配置，第一条发往玫瑰房的消息就会走薇丝-bot；
  它发出第一条消息时，`learn_bot_uid` 会按成员名把 uid 认出来（日志会写
  `P11: 认出一个 Bot 身份（按名字）`），此后判定就精确了。也可以用
  `tools/cfg_bots.py set-uid --key weisi_rose --uid <平台id>` 直接写死。

## 待用户决定

- **人设自称**：玫瑰房现在用的是 `rose_butler` 人设，其 `name_full/name_short`
  还是「小小糯～／小小糯」。房间里的发言者变成了「薇丝-bot」，但人设还在教模型
  自称小小糯 —— 要不要在 `/personas` 里把这个人设的名字改成薇丝？（一句话的事，
  但会改变房间里机器人怎么自称，所以不擅自改。）
- **第一句测试消息**：等 Bot 进群后，要不要我发一条明确标注的测试消息（会出现在群里）？
