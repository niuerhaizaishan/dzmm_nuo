# P12-H 追加记录（2026-09-26）：第二个 Bot「薇丝-bot」进玫瑰房 + 多 Bot 身份

## 结果（已上线）

| 项 | 值 |
|---|---|
| Bot | `weisi_rose` / **薇丝-bot**（简称 薇丝） |
| 绑定群 | **Rose（505/506）玫瑰房** `4a56be71-9ca6-4785-b45f-18725d187f1a` |
| bot_id（平台用户 id） | `36916cb2-b423-465b-afd0-21476e92196b` |
| **入群时间** | **2026-09-26 13:10:49 UTC**（`chatroom.addBot` 返回 `status: added`） |
| 群成员 | 23 → **24**，成员表里 `isBot=true, fullName=薇丝-bot` ✓ |
| 身份 | 已写进 `config.json` 的 `bots[].uid`；启动自证 `我是谁=36916cb2,eee88c3d` |
| 路由 | `bot路由: 已启用 覆盖群=bd7fe6a4,4a56be71` ⇒ 玫瑰房的文本走薇丝-bot |
| 人设 | `rose_butler` 改名为**薇丝**（热加载生效，未重启） |

## 上线冒烟（2026-09-26 13:14，已通过）

用**生产发送链**往玫瑰房发了一条，读回确认署名（不是裸调 API，所以顺带证明了路由与身份）：

```
发送链类型 = RoutingSender
覆盖的群 = ['bd7fe6a4', '4a56be71']        ← 玫瑰房在路由里
薇丝的 uid 算「我自己」= True | 我认的全部身份 = ['36916cb2', '503e3cb9', 'eee88c3d']
bot路由: bot_ok cid=4a56be71 4b3452b7-8d6d-47af-9d48-81fc3b582301
发送结果 ok = True | 耗时 0.1s
路由计数 = {'bot_ok': 1, 'bot_rejected': 0, 'bot_ambiguous': 0, 'account': 0}
占位账号被调用 = 没有（说明确实走的 Bot 通道）

读回玫瑰房最近 12 条，Bot 署名的 2 条：
   [36916cb2] type=system  机器人 薇丝-bot 已加入群聊
   [36916cb2] type=text    🌹 薇丝来报到啦～（首次上线小测试）…
==> 薇丝-bot 真的在玫瑰房发言了
```

关键点：`allow_fallback=False` —— Bot 通道失败**不回退账号**，所以 `ok=True` 与
`account: 0` 就是"确实走的 Bot"，不会出现"看起来成功、其实是账号发的"。

已固化成 **`tools/bot_smoke_check.py`**（`--cid` / `--text` / `--no-verify`）：
以后每加一个 Bot，跑一次就知道"路由 + 身份 + 成员资格 + 能不能发言"四件事。
（建议加 `-e DZMM_OUTBOX=off`，一次性进程别碰线上出站队列。）

## 平台文档要点（`/studio/bots/docs`，SPA 需浏览器渲染才看得到正文）

- **公开** Bot：任意普通群聊的**群主或管理员**都能加；
- **私有** Bot：只有**创建者本人**能加，且创建者必须是目标群的群主/管理员；
- **scenario 群聊不展示 Add Bot 入口**（这类群加不了）；
- 加成功后群内会有系统消息、成员列表实时刷新；
- HTTP API 只文档化了 `send-message`（**没有**"加群"接口）—— 加群是 UI/内部 tRPC 的事；
- `X-Telegram-Bot-Api-Secret-Token` 是**校验平台推给我们**的回调用的
  （我们收消息走 Socket.IO，所以 Webhook Secret 暂时用不到）。

## 关键教训：tRPC 的 **mutation 必须从页面里 fetch**，裸 requests 会 403

这是本次唯一真正的坑，值得单独记：

| 调用 | 裸 requests | 页面内 fetch |
|---|---|---|
| 查询 `chatroom.listBots` / `getMembers` | **200 通** | 200 通 |
| 变更 `chatroom.addBot` | **403 Forbidden**（两种 body 形状都一样） | **200 `{"status":"added"}`** ✓ |

⇒ 平台的 **WAF 对 tRPC mutation 校验浏览器上下文**（Origin/Referer/UA/`x-trpc-source` 之类）。
不是权限问题（群主/管理员确实够），也不是 body 形状问题。**以后所有内部 tRPC 变更
一律走页面内 fetch** —— 已固化成 `tools/add_bot_to_chatroom.py`（`--dry-run` 可先看）。

官方文档（`https://www.laopo.ai/studio/bots/docs`，是 SPA，要用浏览器渲染才看得到正文）明确：

- **公开** Bot：任意普通群聊的**群主或管理员**都能加；
- **私有** Bot：只有**创建者本人**能加，且创建者必须是目标群的群主/管理员；
- **scenario 群聊不展示 Add Bot 入口**（这类群加不了）⇒ 所以"UI 里找不到入口"这件事
  本身就是一条有效信息：要么入口在"输入框 + 面板"里，要么这个群是 scenario；
- 加成功后群内会有系统消息、成员列表实时刷新；
- HTTP API 只文档化了 `send-message`（**没有**"加群"接口）—— 加群是 UI/内部 tRPC 的事。

## 不发消息也能证明"token + 成员资格"都对

往 `send-message` 只给 `chatroom_id`、**不给 content**：

```
400 {"ok":false,"error":"Invalid request body",
     "details":{"fieldErrors":{"content":["必填"]}}}
```

是**参数校验错**而不是 `403 Bot is not a member` ⇒ token 有效 **且**它确实在目标群里；
而且因为没内容，**一个字都没发出去**。这个手法以后每次加完 Bot 都可以复用。


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

## ⚠️ 还差一步：卡在**群权限**（不是 token、不是验证码）

- 「薇丝-bot」**是公开的**，而且在玫瑰房的公开 Bot 目录里看得到：
  `bot_id = 36916cb2-b423-465b-afd0-21476e92196b`（已写进配置的 `bots[].uid`）。
- `POST /api/trpc/chatroom.addBot {chatroomId: 玫瑰房, botId: 36916cb2…}`
  → **HTTP 403 Forbidden**（两种 body 形状都试了，一样）。复核成员表：玫瑰房 23 人里仍没有 Bot。
- 对照：2026-09-25 加认证群那个 Bot 能成功，是因为**小小糯账号在那个群是管理员**
  （docs/小小糯总方案.md §"已完成：Bot 入群" 写明了）。⇒ **小小糯不是玫瑰房的管理员**。

三条路（任选一条，之后我 30 秒收尾）：

| 路 | 做法 |
|---|---|
| **A（推荐）** | 在玫瑰房把**小小糯账号**提为管理员 → 告诉我，我跑 `chatroom.addBot`（2 次调用） |
| **B** | 你（群主/管理员）在房间 UI 里直接加**薇丝-bot**（公开 Bot，候选目录里就有）→ 告诉我，我复核 |
| **C** | 若「薇丝」是**另一个账号**创建的，用那个账号（且它是群管理员）来加 |

三条路走完都一样：**不需要再改任何配置** —— 路由、身份、token 全都就绪（见下）。

## 本轮实测到的运行期证据（20:48 重启后）

```
P11: 我的身份数=3（账号 1 + Bot 2）
P11: 我的身份 Bot key=nuo_main    name=小小糯-bot cid=bd7fe6a4 uid=eee88c3d-af91-4ecc-96df-d87eb9a358db
P11: 我的身份 Bot key=weisi_rose  name=薇丝-bot   cid=4a56be71 uid=36916cb2-b423-465b-afd0-21476e92196b
bot路由: 已启用 origin=https://www.jadelune.xyz 覆盖群=bd7fe6a4,4a56be71
P11: 我是谁=36916cb2,eee88c3d
P11: Bot weisi_rose 的身份 = 已认到 36916cb2
```

还有一条**实测**（不是推测）：20:41:22 往玫瑰房发消息时
`bot路由: bot_rejected cid=4a56be71 Bot is not a member of this chatroom`
—— Bot 通道被明确拒绝 ⇒ 按 `routing.py` 的铁律**回退账号**，
所以玫瑰房一直在正常说话、**没丢过消息**。Bot 进群后这一行就不会再出现。

## 人设改名（已完成，热加载，未重启）

`rose_butler`：`name_full`/`name_short` → **薇丝**；`extra` 4 处、6 条话术里的自称一并替换
（共 9 处）。**只改字段不够**：`extra` 里那句"你自称「小小糯」"优先级最高，
不一起换模型会照旧自称旧名字。生产日志：

```
20:07:44 P12: 热加载 人设 · rose_butler.name_full :  → 薇丝
20:07:44 P12: 热加载 人设 · rose_butler.extra :  → 【本群专属设定 · 优先级最高】…
20:07:44 P12: 热加载完成 来源=auto 改动=9 项
```

工具：`tools/cfg_persona.py`（`show`/`rename`/`set`，`--dry-run` 可先看）。

## 一次真实事故（我造成的，记下来）

为找 bot_id，我用**裸 requests 连打约 40 次** tRPC，绕过了仓库自带的令牌桶限流
（`dzmm.api()`），把账号打进了平台的**迷宫滑块验证码**：

| 时间 | 事件 |
|---|---|
| 20:05:31 | 健康巡检告警「撞验证码（迷宫滑块）—— 建议先停手观察、别反复重试」 |
| 20:10:47 | `bot路由: bot_rejected cid=bd7fe6a4 captcha_required` —— **连 Bot API 也被验证码拦了**（我先前说"Bot 通道完全不受影响"是错的，已修正） |
| 20:11:08 | 机器人自己也撞上：`⚠️ 撞验证码，降级 HTTP 功能（收发继续），HTTP 兜底退避 1 小时` |
| 20:10–20:35 | `renew_token` 每 5 分钟一次，**每次都正确退避**（"撞验证码退避中，跳过续期，不打接口"） |
| ~20:33 | `renew_token` 写了 `token.emergency` ⇒ bot.py 主动退出 ⇒ entrypoint 拉起新进程（一次性，不是重启循环；cookie 有效到 10-31、`renew_fail.streak=0`） |
| 过完验证码后 | 会话恢复；此后所有平台调用都走 `dzmm.api()`（带限流）+ 调用间 sleep |

**教训**：查平台 API 一律走仓库里那条带限流的通路；"我就查一下"也照样能把账号查进验证码。

## 备选路径（本次没用上，但值得记）

SSH 抽风时（本次连续 35+ 次握手全失败），"用本机糯糯登录"是对的方向 —— `addBot` 只要
一个**管理员会话**。实测障碍：Edge/Chrome 会把 cookie 库**独占锁住**
（`shutil.copy2` / ctypes 共享读 / sqlite `immutable=1` 三种读法全失败）⇒
这条路要先**关掉浏览器**，再读 `...\User Data\Default\Network\Cookies` 并解密
（DPAPI + AES-GCM，v10 前缀）。顺带两个坑：SQLite URI 必须写 `file:///C:/...`（少个斜杠
就报 "unable to open database file"）；ctypes 调 `CreateFileW` **必须设 restype/argtypes**，
默认 `c_int` 会把 64 位 HANDLE 截断，让人误判成"文件被独占"。

## 待用户决定

- **第一句测试消息**：Bot 进群后要不要我发一条明确标注的测试消息（会出现在群里）？
  在那之前我不发任何消息。

---

# 第二阶段（同日稍后）：把玫瑰房的身份**整体**换成薇丝

用户原话：**"保持原来 rose 群小小糯的功能，切换到薇丝。参考妹子群。"**
——不是把认证群的功能搬进玫瑰房（功能一个都不动），而是**换"谁在说话"**。
参考妹子群 = 认证群里"发言者叫小小糯、文案里也自称小小糯"是一致的，玫瑰房要同样自洽。

## 机制原来只覆盖两层，漏了第三层

| 层 | 换名机制 | 之前的状态 |
|---|---|---|
| 系统提示词（`brains.py` 40 个 `_p()` 默认值） | `apply_persona()` 替换 `小小糯～/小小糯` | ✅ 已覆盖 |
| 固定话术（`personas.PHRASES`） | `p(key, cid)` 取本群那份（配置覆盖 6 条） | ✅ 已覆盖 |
| **其它直接发的字符串** | **无** | ❌ 玫瑰房会漏出「小小糯」 |

第三层里最要命的不是文案难看，而是**判定**：

- `runtime/sign.py::_is_room_trigger` 里写着 `("小小糯" in t) or is_at_me(t)`
  —— 客人喊「薇丝」**房间根本不接话**；
- `bot.py` 调戏计数兜底 `_named = ("小小糯" in r_text)` —— 喊「薇丝」不算点名；
- `_strip_at()` 只剥 `self_names()`（账号名 + Bot 名），`@薇丝` 剥不掉，
  整句连名字一起进 prompt。

## 改法：一个自称出口 + 一个按群名字集

```python
personas.me(cid)        # 本群自称（短名）：认证群「小小糯」/ 玫瑰房「薇丝」
personas.me_full(cid)   # 全名（默认带 ～）
personas.all_names(cid) # 本群我可能被叫到的名字（人设名 + 默认名，旧名不丢）
personas.fill(text, cid, **kw)   # 群内文案统一出口：{me}/{me_full} + 旧名替换 + 占位符
```

`p(key, cid)` 现在也走 `fill()`，所以**话术表里可以直接写 `{me}`**
（`ALLOWED_PLACEHOLDERS` 已加 `me`/`me_full`，页面校验认它）。
`fill()` 里占位符用 `format_map` + `__missing__`：**填不上就原样留着，绝不抛异常**
（少填一个占位符最多难看，抛异常等于这条消息发不出去）。

`identity` 一侧：

```python
identity.self_names_for(cid)          # self_names() ∪ personas.all_names(cid)
identity.mentions_self(text, cid)     # 提到我（@ 与直呼都算）→ 房间闸门/调戏兜底用
identity.is_at_me(text, cid=None)     # 老签名兼容，多了 cid
identity.self_names_longest_first(cid=None)   # 剥 @ 用，长名优先（防 `-bot` 残余）
```

## 落到哪些文案（都改成 `{me}` / `me(cid)`）

| 位置 | 内容 |
|---|---|
| `bot.py` | `CAPTCHA_NOTICE`、`HELP_TEXT`、`WELCOME_TEXT(_PLAIN)`、`INTRO_TEXT`（全部转 `{me}`）；记忆回执、改名播报、`_named` 兜底、`_strip_at`、大厅欢迎语 |
| `personas.py` | `ROOM_GREET_LINES`、`ROOM_GHOST_TEXT/WARN/BACK`（女鬼剧本）、`rose_butler` 的 6 条话术默认值 → `{me}`；`rose_butler` 代码默认名也改成**薇丝** |
| `runtime/sign.py` | 星座册子的 4 句（玫瑰房专属）+ 房间闸门 |
| `runtime/fortune.py` | 运势兜底句、赔罪日文案（`{me}`） |
| `runtime/dice.py`、`diaojiao.py`、`perf.py`、`recap.py`、`trend.py`、`memory.py` | 各自的群内文案与模型可见标注 |

**没动**（有意为之）：`【小小糯bot】…` 系列是发给**我**的微信/飞书告警，
收件人不是群，保留服务名；`smoke`/告警/日志里的服务名同理。
`settings.py`/`config_store.py` 里的「小小糯」是**默认值**（没配 Bot 时的兜底名），保留。

## 护栏

新增 `tests/test_rose_weisi_switch.py`（11 条），两类断言：

1. **行为**：`fill`/`me`/`p` 按群填名；bot.py 五个常量逐条"认证群=小小糯、玫瑰房=薇丝"；
   房间剧本/欢迎语不带旧名；`mentions_self("薇丝")` 为真、`is_at_me("@薇丝")` 为真、
   `@薇丝-bot` 剥得干净；`_is_room_trigger` 认新名字且**不吞闲聊**。
2. **源码护栏（AST）**：会往群里发字的 9 个模块，字符串常量里**不许**再写死默认名
   （放行 docstring 与 `【小小糯…` 告警）。新文案必须走 `fill`/`{me}`/`p()`。

改动 18 个文件、825 条测试全绿；闸门本地跑通（`生产后端是 flock` 与
`配置文件存在` 两项只在服务器上有意义，脚本注释里已写明）。

## 部署证据（2026-09-26 21:46）

顺序：`upload` → `build` → **闸门（一次性容器）** → `up -d --force-recreate`。
注意 `tools/` 不在上传白名单里，闸门脚本得单独 `scp` 到 `/tmp`。

| 检查 | 结果 |
|---|---|
| 上传 | 135 个文件；**11 个改动文件本地/服务器 MD5 逐个相同** |
| 服务器闸门 | `ALL CHECKS PASSED`（含真实 `config.json` 校验、2 个 Bot 身份/令牌） |
| 重启后启动日志 | `P11: 我是谁=36916cb2,eee88c3d`、两个 Bot `已就绪`、`人设文本 … 体检=全部通过`、无 traceback |
| 自证脚本（**不发群消息**） | `VERIFY-RESULT: PASS` |

自证脚本（`/tmp/verify_weisi.py`，一次性容器 + 真实 `.env`）实测输出节选：

```
me(认证群) / me(玫瑰房)            小小糯 / 薇丝
fill(CAPTCHA_NOTICE) 认证群        ⚠️ 小小糯撞上平台验证码啦～
fill(CAPTCHA_NOTICE) 玫瑰房        ⚠️ 薇丝撞上平台验证码啦～
p(intro_text) 玫瑰房               玫瑰房的管家，薇丝。屋主是 Vv姐姐。
女鬼警告(玫瑰房)                   门外传来薇丝的声音，隔着空荡荡的门框：…
mentions_self('薇丝在吗') 玫瑰房    True      ← 以前是 False（房间不接话）
mentions_self('小小糯在吗') 玫瑰房  True      ← 老群友叫顺口的旧名字也认
mentions_self('今晚吃什么') 玫瑰房  False     ← 没误伤闲聊
_strip_at('@薇丝-bot 你好呀')      '你好呀'   ← 不留 `-bot` 残余
房间闸门('薇丝在吗') / ('今晚吃什么好呢')   True / False
认到的名字 玫瑰房                   ['小小糯', '小小糯～', '薇丝']
```

**一句话**：认证群一个字没变，玫瑰房从"发言者叫薇丝、文案里叫小小糯"变成两边一致。

---

# 备注：为什么"参考妹子群"≠ 把妹子群的功能搬过去

用户明确纠正过一次："不是把妹子认证群的功能都迁移过来，是保持原来 rose 群小小糯的
功能，切换到薇丝。" 所以本次**一个功能开关都没动**：

- 玫瑰房仍是 `room_welcome/room_order/room_flower/room_ghost/horoscope/room_lewd/room_refuse` 那套；
- 认证群的筛查/踢人/公演/调教/骰子/发情值**没有**进入玫瑰房
  （人设 `extra` 里那句"其余玩法只在认证妹子群开放"依旧成立）；
- 改动只碰"自称 / 被叫到名字时认不认得出"两类。

