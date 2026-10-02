# DDZM Bot 平台完整优化方案

> 2026-10-01 · 基于 12 轮施工记录 + 项目结构审计 + 多群/多人设/多功能维度分析 + 后台代码逐行审查 + 8.8h事故复盘

---

## 目录

- [一、平台全景](#一平台全景)
- [二、问题诊断（六维度）](#二问题诊断六维度)
- [三、优化方案总览](#三优化方案总览)
- [四、Phase 0 — 即时清理](#四phase-0--即时清理)
- [五、Phase 1 — L1 整理](#五phase-1--l1-整理)
- [六、Phase 2 — S1 凭据挂目录](#六phase-2--s1-凭据挂目录)
- [七、Phase 3 — L2 bot.py 拆分](#七phase-3--l2-botpy-拆分)
- [八、Phase 4 — 运维后台重构](#八phase-4--运维后台重构)
- [九、Phase 5 — L3 架构重构](#九phase-5--l3-架构重构)
- [十、执行路线图](#十执行路线图)
- [十一、风险矩阵](#十一风险矩阵)
- [十二、成功指标](#十二成功指标)
- [附录](#附录)

---

## 一、平台全景

### 1.1 服务架构

```
┌─────────────────────────────────────────────────────────┐
│                    阿里云 ECS (1.87G RAM, 40G Disk)      │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  dzmm-bot    │  │  Caddy       │  │  Xvfb :99     │  │
│  │  (Docker)    │  │  (TLS终止)   │  │  (虚拟显示)    │  │
│  │              │  │              │  │               │  │
│  │ · bot.py     │  │ · :443 → 平台│  │ · noVNC :6080 │  │
│  │ · renew_token│  │ · :8090 面板 │  │ · chromium    │  │
│  │ · check_health│ │ · :8091 whk  │  │               │  │
│  │ · monitor    │  │              │  │               │  │
│  │ · admin:8090 │  │              │  │               │  │
│  └──────┬───────┘  └──────────────┘  └───────────────┘  │
│         │                                                │
│  ┌──────┴──────────────────────────────────────────────┐ │
│  │              /opt/dzmm (宿主持久化)                   │ │
│  │  .dzmm_token.json  .dzmm_login.json                 │ │
│  │  config/config.json  data/*.db  state.json           │ │
│  │  profiles.json  pw_profile/  pw_login/  pw_captcha/ │ │
│  │  novels/  logs/  backups/                            │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
         │                              ▲
         ▼                              │
┌─────────────────┐          ┌──────────────────┐
│  dzmm.ai 平台    │          │  DeepSeek API    │
│  · Socket.IO    │          │  · 对话生成       │
│  · Bot Webhook  │          │  · 记忆精筛       │
│  · 成员/消息 API │          │  · 运势/诗/总结   │
│  · 验证码/WAF   │          │                  │
└─────────────────┘          └──────────────────┘
```

### 1.2 四个群 × 三个人设

| 群 | CID | 人设 | 自称 | 核心特征 | 特殊机制 |
|---|---|---|---|---|---|
| **认证妹子群** | `bd7fe6a4` | 小小糯 (nuo) | 小小糯/妹妹 | 软糯撒娇、♡、～、姐姐们 | 入群筛查（踢游客/踢男/认证）、发情值排行、公演小说 |
| **玫瑰房** | `4a56be71` | 薇丝 (weisi) | 薇丝/管家 | 优雅从容、花瓣变物、女鬼模式 | 房间触发词、布局切换、屋主权限、迎客/拒客、花瓣招待品 |
| **上班群** | `f4ec4c21` | 若瑾 (ruojin) | 若瑾/我 | 纯S、低稳慢、陈述句、❤️(密度可控) | 羞辱/发骚/调教全开、说完就停、双边上下文、回复冷却 |
| **茶水间** | `4fc4a2fd` | 小小糯 (nuo) | 小小糯 | 同认证群但更克制 | at_only、quote_reply、无slash_cmds、fortune+memory+recap |

### 1.3 功能矩阵（68个Feature）

| 功能 | Feature Key | 认证群 | 玫瑰房 | 上班群 | 茶水间 | 说明 |
|---|---|---|---|---|---|---|
| @ 闲聊 | `chat` | ✅ | ✅ | ✅ | ✅ | P12-BE双边/P12-BQ冷却兜底/P12-BD排版 |
| 引用回复 | `quote_reply` | ❌ | ❌ | ✅ | ✅ | P12-BM |
| 仅@触发 | `at_only` | ❌ | ❌ | ❌ | ✅ | 过滤非@消息 |
| 斜杠指令 | `slash_cmds` | ✅ | ✅ | ✅ | ❌ | 茶水间屏蔽别家bot语法 |
| 运势 | `fortune` | ✅ | ❌ | ❌ | ✅ | #运势/#算命/卜一卦 |
| 运势趋势 | `trend` | ✅ | ❌ | ❌ | ❌ | /运势趋势 → 群榜(P12-BR⑤e修复) |
| 发情值排行 | `rank_heat` | ✅ | ❌ | ❌ | ❌ | /排行 top_n可调(P12-BR⑤a) |
| 羞辱 | `insult` | ✅ | ❌ | ✅ | ❌ | max_tokens可调(P12-BR⑤b) |
| 发骚 | `scene` | ✅ | ❌ | ✅ | ❌ | 场景×体位×附加玩法 |
| 调教 | `diaojiao` | ✅ | ❌ | ✅ | ❌ | max_tokens可调(P12-BR⑤b) |
| 公演 | `perf` | ✅ | ❌ | ❌ | ❌ | 小说+互动+排名 |
| 剧情 | `story` | ✅ | ❌ | ❌ | ❌ | 投票推进 |
| 投票 | `vote` | ✅ | ❌ | ❌ | ❌ | /投票 /选 |
| 写诗 | `poem` | ✅ | ❌ | ❌ | ❌ | 风格×主题 |
| 回顾 | `recap` | ✅ | ❌ | ❌ | ✅ | "我不在时发生了什么" |
| 记忆 | `memory_recall/remember` | ✅ | ❌ | ✅ | ✅ | /记住 /回忆 |
| 档案 | `query/register` | ✅ | ✅ | ✅ | ✅ | /查 /注册 |
| 入群筛查 | `screen` | ✅ | ❌ | ❌ | ❌ | 踢游客/踢男/提醒未认证 |
| 房间迎客 | `room_welcome` | ❌ | ✅ | ❌ | ❌ | 花瓣+问好+立绘 |
| 女鬼模式 | `room_ghost` | ❌ | ✅ | ❌ | ❌ | 屋主离开→女鬼接管 |
| 恶魔吐槽 | `devil_roast` | ❌ | ❌ | ❌ | ❌ | 随机毒舌(全关) |
| 自我介绍配图 | `intro_photo` | ✅ | ✅ | ✅ | ✅ | 立绘自动发送 |

### 1.4 消息处理链路（当前）

```
入站消息 (Socket.IO / Webhook / HTTP拉取)
    │
    ▼
drain() → pending 队列
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ main_loop 单次迭代 (~3500行 if/elif 链)                  │
│                                                         │
│  1. 拉消息 get_messages_since(last_id)                   │
│  2. 去重 (_TURN_SEEN / message_id)                       │
│  3. # 运势白名单闸门                                     │
│  4. at_only 闸门 (茶水间)                                │
│  5. / 斜杠指令识别                                       │
│  6. @ / quote 检测                                       │
│  7. 房间触发词检测                                        │
│  8. _pick_leftover (同批多条只处理一条)                    │
│  9. 分派:                                                │
│     ├─ 房间命令拒绝 (_room_cmd_refused)                   │
│     ├─ 斜杠指令 (/运势 /羞辱 /调教 /发骚 /投票 /剧情 ...) │
│     ├─ @ 闲聊 (上下文+记忆+生成+装饰+发送)                │
│     ├─ 房间消息 (管家回应/女鬼/布局/花瓣)                  │
│     └─ 兜底 (unknown_cmd / dj_idle_unknown)              │
│  10. 更新 last_id / _remember_turn                        │
│  11. maybe_reload() (热加载检查)                          │
└─────────────────────────────────────────────────────────┘
    │
    ▼
OutboxSender → Account通道(typing/split) / Bot API / Socket.IO直发
    │
    ▼
_sio_send_long (>190字走live socket → HTTP握手 → typed split)
    │
    ▼
_verify_landed (read-back确认)
```

### 1.5 续期与会话维护链路

```
renew_token.py (每5分钟 cron)
    │
    ├─ 快路径: expires_at - now > 600s → SKIP (不打日志)
    │
    ├─ 浏览器续期: _browser_renew()
    │   ├─ Playwright 加载首页 → 读 cookie
    │   ├─ 撞验证码 → backoff 1h + noVNC告警
    │   └─ 成功 → save_cookies() → record_success()
    │
    ├─ /api/auth/token 探测 (只读echo，不续期)
    │   ├─ 200 + token变化 → save
    │   ├─ 200 + 未变化 → SKIP
    │   └─ 401 → _api_probe() 业务探测
    │       ├─ ok → NOTE (会话没死)
    │       ├─ unauth → _home_status()
    │       │   ├─ wall(418/403) → WALL + bad_window
    │       │   ├─ err → 不下结论
    │       │   └─ ok → _try_password_relogin()
    │       │       ├─ 成功 → record_success
    │       │       └─ 失败 → SESSION_DEAD → emergency
    │       └─ error → 不下结论 (超RELOGIN_AFTER_MIN才升级)
    │
    └─ 心跳: session_state.json (probe/alive/at/bad_since)

monitor_recover.py (宿主 cron)
    ├─ 容器 Down → docker restart
    ├─ 心跳 >25min stale → 告警
    └─ 不再因会话问题重启 (P12-BJ)

check_bot_health.py (宿主 cron)
    ├─ 进程数检查
    ├─ outbox failed 增长检查
    ├─ session_state 检查
    └─ 飞书告警 (按窗口去噪 P12-BJ)
```

### 1.6 状态存储分布

| 数据 | 位置 | 格式 | 写入方 | 读取方 | 备注 |
|---|---|---|---|---|---|
| Token/Cookie | `.dzmm_token.json` | JSON | token_store / headless_relogin | token_store / dashboard / monitor / switch_node | **单文件挂载→EBUSY(S1待修)** |
| Login State | `.dzmm_login.json` | JSON | headless_relogin | headless_relogin | 同上 |
| 群状态 | `state.json` | JSON | bot._save_group | bot / check_health | last_id / heat / dice / ... |
| 用户档案 | `profiles.json` | JSON | profiles.upsert | profiles.get / 各generate_* | 78条 |
| 对话历史 | 内存 `_TURNS` | dict | _remember_turn | _recent_turns | 重启丢失，_seed_turns补 |
| 出站队列 | `data/outbox.db` | SQLite | OutboxStore | OutboxSender / _log_outbox_status | pending/leased/sent/failed/expired/cancelled |
| 通用KV | `data/store.db` | SQLite | state_store | 各模块 | group/profile/memory/poem/... |
| 会话探针 | `data/session_state.json` | JSON | renew_token | check_health / monitor | probe/alive/at |
| 续期退避 | `data/renew_browser_backoff.json` | JSON | renew_token | renew_token | captcha cooldown |
| 重登冷却 | `data/renew_relogin_state.json` | JSON | renew_token | renew_token | 30min cooldown |
| 坏窗口 | `data/renew_bad_window.json` | JSON | renew_token | renew_token | 告警去噪 |
| 紧急冷却 | `renew_emergency_state.json` | JSON | renew_token | renew_token | 1h cooldown |
| 失败计数 | `renew_fail_state.json` | JSON | renew_token | renew_token | consecutive fails |
| PW Profile | `pw_profile/` (114MB) | Chromium dir | Playwright | Playwright | 常驻群页缓存 |
| PW Login | `pw_login/` (26MB) | Chromium dir | headless_relogin | headless_relogin | 登录态缓存 |
| PW Captcha | `pw_captcha/` (14MB) | Chromium dir | 人工过码 | headless_relogin | 验证码专用 |
| 小说 | `novels/` (168KB) | .md files | perf.novel | 公演输出 | |
| 配置 | `config/config.json` | JSON | dashboard / tools | config_store / hotload | 群/Bot/功能/参数/人设覆盖 |
| 配置备份 | `config/config_backups/` | JSON | config_store | rollback | 原子写 |
| 代码备份 | `backup-code-*.tar.gz` (10×28MB) | tar.gz | deploy.ps1 | rollback | 保留10份 |

### 1.7 人设系统架构

```
personas.py (1174行)
    │
    ├── PERSONAS = {
    │     "nuo":    {name, name_short, name_full, extra(~2800字), ...},
    │     "ruojin": {name, name_short, name_full, extra(~3900字), ...},
    │     "weisi":  {name, name_short, name_full, extra(~2200字), ...},
    │   }
    │
    ├── PHRASES = {
    │     "nuo":    {chat_decor, mem_saved, dj_hint, play_busy_*, welcome_text, ...},
    │     "ruojin": {chat_decor:"❤️", welcome_text_plain, dj_*, play_busy_*(短版), ...},
    │     "weisi":  {dj_*, play_busy_*, ...},
    │   }
    │
    ├── PHRASE_LABELS = {key: 中文标签}   ← UI显示用，新增key必须补
    │
    ├── persona_for(cid) → 本群人设dict
    │     优先级: config覆盖 > PERSONAS[persona_key]
    │
    ├── phrases_for(key) → 本群话术dict
    │     优先级: config覆盖 > PHRASES[persona_key] > PHRASES["nuo"]
    │
    ├── fill(text, cid) → 替换{me}/{me_full}/默认名
    │
    └── p(key, cid) → fill(phrases_for(key)[key], cid)

brains.py (1463行)
    │
    ├── _p(key, default) → prompts/key.md 存在则读文件，否则用default
    │
    ├── CMD_PROMPT        ← @闲聊系统提示词
    ├── AT_FRAME_PROMPT   ← 收尾块(谁/叫什么/这句/手上事/怎么回/写多长/只跟他说) ≤560字
    ├── INSULT_PROMPT     ← 羞辱(含"自称妹妹"←认证群专用)
    ├── DIAOJIAO_PROMPT   ← 调教
    ├── SCENE_PROMPT      ← 发骚
    ├── FORTUNE_PROMPT    ← 运势卡
    ├── TREND_PROMPT      ← 运势趋势回顾段
    ├── POEM_PROMPT       ← 写诗
    ├── RECAP_PROMPT      ← 回顾
    ├── SUMMARY_PROMPT    ← 群聊总结
    ├── MEMORY_FILTER     ← 记忆精筛
    └── generate_*()      ← 各功能的LLM调用封装

runtime/features.py
    │
    └── FEATURES = [Feature(..., params=[Param(...)])]  ← 68个Feature, ~30个Param

runtime/tuning.py
    │
    ├── PARAMS = {key: (Param, feature_key)}   ← import时从FEATURES构建
    ├── get(key, cid) → 每群覆盖 > 全局覆盖 > 代码默认
    └── set_overrides(mapping) ← hotload调用
```

### 1.8 发送通道

```
OutboxSender(RoutingSender(account, {cid: DzmmBotSender}))
    │
    ├── Account通道 (无Bot的群: 茶水间)
    │   ├─ typing path: ≤190字 → Playwright逐字输入
    │   └─ socket path: >190字 → _sio_send_long
    │       ├─ live subscriber socket (优先, ~2.2s)
    │       ├─ HTTP handshake loop (8次/25s)
    │       └─ typed split (最后手段)
    │
    ├── Bot API通道 (有Bot的群: 认证/玫瑰/上班)
    │   └─ POST /bot/message → 平台直推
    │
    └── _verify_landed (read-back 20条窗口, UTC sent_at ≥ now-10s)
```

### 1.9 运维后台架构

```
admin/
├── dashboard.py      2091行  HTTP服务+数据采集+HTML渲染+认证 四合一
├── config_ui.py       580行  群/Bot配置编辑页（独立_CSS）
├── features_ui.py     515行  功能矩阵+参数页（独立_CSS）
├── personas_ui.py     508行  人设话术编辑页（独立_CSS）
├── prompts_ui.py      405行  提示词模板编辑页（独立_CSS）
└── static/
    ├── base.css        ~8KB  ✅ 新建：Design Tokens + Reset + App Shell + 响应式
    ├── components.css ~10KB  ✅ 新建：15种UI组件
    ├── dashboard.css   ~3KB  ✅ 新建：仪表盘专用样式
    └── app.js          ~7KB  ✅ 重写：8个渐进增强模块
```

---

## 二、问题诊断（六维度）

### 2.1 跨群维度

| 问题 | 影响群 | 根因 | 状态 |
|---|---|---|---|
| 硬编码"进行中"提示漏旧口吻 | 玫瑰房/上班群 | bot.py 6处裸字符串绕过话术表 | ✅ P12-BP |
| `#运势趋势`给个人卡 | 茶水间 | is_fortune_cmd startswith语义+trend关 | ✅ P12-BR⑤e |
| 管家自称"妹妹" | 玫瑰房 | WEISI_EXTRA缺覆盖声明 | ✅ P12-BR⑤c |
| 男生被叫姐姐 | 上班群/茶水间 | _gender_label只看档案→未知→标签省掉 | ✅ P12-BN A1 |
| 茶水间@没反应 | 茶水间 | is_fortune_ask未import→NameError杀全群 | ✅ P12-BB |
| 茶水间引用不触发 | 茶水间 | quote_reply=false+at_only闸门 | ✅ P12-BM |
| 容器重建吃消息 | 所有群 | last_id先于处理推进→重启丢inflight | ✅ P12-BC |
| 长消息分两段 | 茶水间 | account typing cap 200字 | ✅ P12-AZ |
| 若瑾太像机器人 | 上班群 | 递话率67%/空行93%/❤️80%/长度116字 | ✅ P12-BD/BQ |
| 她一一批判别人 | 上班群 | 上下文=全群滚动记录 | ✅ P12-BE |
| S1凭据非原子写 | 所有群 | 单文件bind mount→EBUSY | ❌ 待修 |
| 多端refresh轮换 | 所有群 | 3个profile各握有效token | ⚠️ 已留证 |

### 2.2 人设维度

| 问题 | 影响人设 | 根因 | 状态 |
|---|---|---|---|
| 若瑾每句递话 | ruojin | 人设示例全是派活+提示词缺"说完就停" | ✅ P12-BD |
| 若瑾❤️密度过高 | ruojin | "每条都带❤️"写死人设,decor_prob无效 | ✅ 改人设密度 |
| 管家漏妹妹声明 | weisi | WEISI_EXTRA缺覆盖 | ✅ P12-BR⑤c |
| 欢迎语串味 | ruojin | WELCOME_TEXT_PLAIN是软糯口吻+功能菜单 | ✅ welcome_text_plain |
| 参与提示漏旧口吻 | ruojin/weisi | bot.py硬编码"哦～"绕过话术表 | ✅ P12-BP |
| nuo人设耦合在brains.py | nuo | INSULT_PROMPT等写死"自称妹妹叫姐姐" | ⚠️ 靠覆盖兜底 |
| 人设extra过长 | ruojin | 3904字,接近4000警告线 | ⚠️ 监控中 |
| **若瑾带出"认证群世界观"** | ruojin | 若瑾提示词 = 小小糯 `CMD_PROMPT` + `extra`；**覆盖声明只列口吻/属性，没覆盖"糯糯姐/认证群/谁是老板"这类世界事实**（实测最终提示词残留「糯糯姐」×10、「糯糯」×15） | 🔴 **未修**（屋主 2026-10-01 决定只记录；详见 [EXECUTION-LOG.md](EXECUTION-LOG.md) 同日"人设世界观泄漏"一节） |

### 2.3 功能维度

| 问题 | 影响功能 | 根因 | 状态 |
|---|---|---|---|
| 运势卡改点评句 | fortune | _ensure_decor bang半段改最后一个。 | ✅ _no_decor |
| 运势卡分两段 | fortune | account typing cap 200 | ✅ P12-AZ |
| 卜一卦不识别 | fortune | _FORTUNE_STRONG/RE缺"卜" | ✅ P12-BB |
| 调教参与提示重复 | diaojiao | 每轮拼DJ_PARTICIPATE_HINT | ✅ P12-BD① |
| 公演/剧情进行中硬编码 | perf/story | bot.py裸字符串 | ✅ P12-BP |
| rank写死top10 | rank_heat | rank_summary(top=10)调用不传参 | ✅ P12-BR⑤a |
| 羞辱/调教长度写死 | insult/diaojiao | brains.py max_tokens=600 | ✅ P12-BR⑤b |
| 假failed永远挂着 | outbox | 无expired终态 | ✅ P12-BR⑦ |
| 记忆检索堵主循环 | memory | _retrieve_memory调模型未放线程 | ✅ P12-AA |
| 热加载不重建PARAMS | tuning | import时构建,hotload只刷覆盖值 | ⚠️ 新参数需restart |
| main_loop单体 | 全局 | 4908行if/elif链 | ❌ 待拆(P3-⑤d) |

### 2.4 基础设施维度

| 问题 | 根因 | 严重度 | 状态 |
|---|---|---|---|
| os.replace EBUSY | 单文件bind mount | 🔴 | ✅ **已修**(S1 目录挂载 10-01 22:01 上线，容器内实测 `ATOMIC_REPLACE_OK`) |
| .bak在容器层,recreate即丢 | 同上 | 🔴 | ✅ **已修**(随 S1：`.bak`/`.tmp` 是 token 的同级文件，现在落在宿主 `./secrets/`) |
| .bak慢一代(token轮换后失效) | 同上 | 🔴 | ✅ 随 S1（且原子写生效后走主路径，`.bak` 只在降级分支才写） |
| token.emergency路径不在挂载上 | renew写/app/, entrypoint看/opt/dzmm/ | 🔴 | ❌ S1待修(§2-3A) |
| 多冷却文件叠加死锁 | relogin+browser+captcha三个独立冷却 | 🟡 | ❌ 待合并(§2-3C) |
| .session.lock路径不统一 | /app/.session.lock vs /app/data/ | 🟡 | ❌ S1时一并修(§2-3D) |
| bot.log无轮转 | 挂载文件不受docker logging管控 | 🟡 | ✅ **已修**(10-01 22:00 logrotate 覆盖 bot/renew_token/check_health/monitor_recover) |
| Build Cache 1GB | 密集deploy累积 | 🟢 | ❌ 一条命令清 |
| pw_profile 114MB无上限 | Chromium缓存膨胀 | 🟡 | ❌ 待加disk-cache-size |
| 飞书告警静默吞异常 | except pass无日志(renew_token/feishu_alert/captcha_novnc三处) | 🟡 | ✅ 已修(§2-3B, 08:24部署，10-01 实测 ✅ 已发) |
| **自动重登被自己锁死（P0）** | renew_token 持锁调 headless_relogin，后者再拿同锁 → 死等180s → SKIP | ✅ | ✅ **已修**(§2-3E，10-01 21:50 上线 + 线上 A/B 验证) |
| **会话死的"第一症状"不触发自动恢复（P0-2）** | 读消息失败是裸 except，只打日志；`auto_recover` 只在生成/发送路径可达 | 🔴 | ✅ **已修**(10-01 22:01 上线 + 10 项回归测试) |
| auto_recover 白等 300s + 误发告警 | 兄弟进程抢同一把锁，超时后 SKIP 还发微信 | 🟡 | ✅ **已修**(超时 300s→20s，SKIP 不再告警) |
| 零工作尝试吃掉 30 分钟重登冷却 | `_stamp_relogin()` 在起子进程**之前**调用 | 🟡 | ✅ **已修**(挪到"确认真试过之后"，SKIP 不消耗冷却) |
| 退避文案与实际常量不符 | 文案手写"30 分钟"，常量是 3600 秒 | 🟢 | ✅ **已修**(改为常量插值 + 测试钉住) |
| 测试污染(relogin state) | fixture未隔离 | 🟡 | ✅ 已修 |
| 巡检误报(418=urllib UA) | WAF是UA-based | 🟡 | ✅ P12-BL |
| captcha backoff阻塞升级链 | early return挡了probe | 🟡 | ✅ P12-BO |

### 2.5 代码结构维度

| 问题 | 现状 | 目标 |
|---|---|---|
| bot.py上帝文件 | 4908行,一切串联 | <1500行骨架+4-5独立模块 |
| brains.py混杂 | 提示词+调用逻辑 | 提示词全外置,brains只做调用 |
| personas.py臃肿 | 1174行,数据+逻辑 | 按人设拆文件+逻辑独立 |
| tools/垃圾抽屉 | 142文件无分类 | active/probes/archive/fixtures |
| docs/无索引 | 65篇按时间堆 | INDEX.md按主题分类 |
| 根目录混乱 | .py/.md/.json/.log混放 | content/data/logs分离 |
| 状态散落7+处 | 无统一接口 | StateStore协议 |
| 发送通道隐式 | _sio_send_long嵌在bot.py | outbound模块独立 |

### 2.6 运维后台维度

| 问题 | 现状 | 目标 |
|---|---|---|
| CSS复制粘贴五份 | 各UI文件独立_CSS,已漂移 | 3份共享CSS(base+components+page) |
| HTML用Python拼接 | 2091行format字符串 | 组件函数或模板引擎 |
| 无响应式导航 | 顶部一行链接 | 侧边栏+移动端汉堡菜单 |
| 首页信息密度失控 | 10个section平铺 | 分层仪表盘(告警→stat→表格→折叠) |
| 表格无交互 | 不能排序/筛选/搜索 | 点击排序+URL参数筛选 |
| 编辑页无即时反馈 | 整页刷新回顶部 | dirty追踪+diff预览+保存锚点+Toast |
| 无实时数据 | 手动刷新 | 30s自动刷新stat卡片 |
| 暗色主题唯一 | 无切换 | 亮/暗双主题+跟随系统 |
| dashboard.py 2091行 | 四类职责混杂 | 拆分至~800行 |
| API只有/api/summary | 全量~50KB | 拆分轻量端点 |

---

## 三、优化方案总览

```
Phase 0  即时清理          ← 零风险, <1小时
    │
Phase 1  L1 整理           ← 零生产风险, 1-2小时
    │
Phase 2  S1 凭据挂目录      ← 中等风险, 需施工窗口
    │
Phase 3  L2 bot.py 拆分    ← 中等风险, 4个施工窗口
    │
Phase 4  运维后台重构       ← 中等风险, 4-6天
    │
Phase 5  L3 架构重构       ← 高风险, 长期规划
```

---

## 四、Phase 0 — 即时清理

> 零风险，<1小时，立即执行

| # | 动作 | 命令/文件 | 收益 |
|---|---|---|---|
| 0-1 | Build Cache清理 | `docker builder prune -f --keep-storage 1.5GB` | 释放~1GB |
| 0-2 | deploy.ps1 keep-storage | 3GB→1.5GB | 防复发 |
| 0-3 | bot.log轮转 | 宿主logrotate配置(copytruncate) | 防磁盘爆 |
| 0-4 | pw_profile缓存限制 | Playwright启动参数`--disk-cache-size=52428800` | 控114MB膨胀 |
| 0-5 | 在线验证②③④ | 等@流量或跑`probe_bd_ab.py` | 闭环12轮施工 |

---

## 五、Phase 1 — L1 整理

> 零生产风险，1-2小时，纯移动/归类/索引

### 1-1. 根目录清理

```bash
mkdir -p content/novels content/lore logs
mv 发骚整理_*.md 苏若曦.md 高冷校花*.md 公演小说.md content/novels/
mv profiles.json state.json data/
mv bot.log compose-p9.log config_audit.log logs/
# .dzmm_token.json / .dzmm_login.json 等S1一起迁
```

### 1-2. tools/ 归档分类

```
tools/
├── active/           ← 当前可用的运维工具
│   ├── set_chat_tuning.py
│   ├── resolve_outbox.py
│   ├── probe_session_profiles.py
│   ├── probe_bd_ab.py
│   ├── enable_quote_reply.py
│   └── cfg_*.py
├── probes/           ← 可复用的探针
│   ├── probe_solicit.py
│   ├── probe_ctx_scope.py
│   ├── probe_quotes.py
│   └── ...
├── archive/          ← 已完成的一次性脚本
│   ├── p8s4_*.py     ← P8 迁移脚本
│   ├── p12y_*.py     ← P12Y 验证
│   └── ...
└── fixtures/         ← 测试夹具/快照
    └── CMD_PROMPT_pre_p12ap.md
```

### 1-3. docs/ 索引

创建 `docs/INDEX.md`，按主题分类：

```markdown
# DDZM Bot 文档索引

## 架构与入门
- [平台完整优化方案](PLATFORM-OPTIMIZATION.md) ← 本文档
- [后台优化方案](ADMIN-OPTIMIZATION.md)
- [部署指南](DEPLOY.md) ← 新建

## 消息处理链路
- [@回复链路汇总](P12-AP-AQ-AU-AZ-BD-BE-BQ-summary.md) ← 合并摘要
- [引用回复](P12-BM-茶水间引用触发-方案.md)
- [若瑾更像人](P12-BD-若瑾更像人-方案.md)
- [男生被叫姐姐](P12-BN-男生被叫姐姐-方案.md)
- [茶水间@没反应](P12-BB-茶水间@没反应-未定义名字.md)
- [长消息一条发](P12-AZ-账号长消息一条发.md)
- [容器重建吃消息](P12-BC-容器重建会吃掉正在处理的消息.md)

## 续期与会话
- [续期链全景](P12-BL-续期链再检查.md)
- [风控墙vs真死](P12-BI-风控墙与会话真死分开.md)
- [密码重登](P12-BG-会话真死自动重登.md)
- [告警去噪](P12-BJ-恢复收口与告警去噪.md)

## 人设
- [运势按性别](P12-BA-运势按性别称呼.md)

## 运维与施工
- [P3旧账清单](P12-BR-P3旧账施工清单.md)
- [BP/BQ/BE上线记录](P12-BP-BQ-BE-上线记录.md)
```

---

## 六、Phase 2 — S1 凭据挂目录

> 中等风险，需施工窗口，方案已在P12-BR文档§2细化

### 2-1. 四个读口（已确认）

| 文件 | 当前路径 | 改法 |
|---|---|---|
| `token_store.py:22` | `Path(__file__).parent / ".dzmm_token.json"` | `os.environ.get("DZMM_TOKEN_FILE") or 原值` |
| `admin/dashboard.py:101` | `ROOT / ".dzmm_token.json"` | 同上 |
| `monitor_recover.py:22` | `/opt/dzmm/.dzmm_token.json` | `os.environ.get(...) or 原值` |
| `switch_node.py:21` | `/opt/dzmm/.dzmm_token.json` | 同上 |

### 2-2. 五步执行（每步可回滚）

1. **代码准备**：四处加env var fallback，不设=老路径（零回归）
2. **compose变更**：加`- ./secrets:/app/secrets` + `DZMM_TOKEN_FILE=/app/secrets/token.json`，**保留老挂载一轮**
3. **宿主迁移**：`mkdir secrets && cp -a .dzmm_token.json secrets/token.json && chown 501:games && chmod 600`
4. **观察**：一次续期日志无`原子替换失败` ⇒ 原子写回归
5. **清理**：下老挂载、删老文件、宿主脚本切新路径

### 2-3. 8.8小时事故暴露的附加问题（2026-10-01 实锤）

> 2026-10-01 00:00~08:18 会话死亡 529 分钟，根因链：token过期 → 浏览器续期401 → 密码重登撞验证码 → 多冷却叠加卡死

#### 问题 A：`token.emergency` 路径不在 bind mount 上

| 位置 | 路径 | 挂载 | 后果 |
|---|---|---|---|
| renew_token.py 写入 | `/app/token.emergency` | ❌ 容器内（非挂载） | entrypoint watcher 看不到信号 |
| entrypoint.sh 监听 | `/opt/dzmm/token.emergency` | ✅ 宿主机 | 两边路径不一致，emergency 写了但没人捡 |

**修复**：S1 迁移时统一将 `token.emergency` 也放到 `./secrets/` 下，或单独挂载 `/app/token.emergency`。

#### 问题 B：飞书告警静默吞异常

三处 `except Exception: pass` 导致所有飞书告警无法确认是否发出：

| 文件 | 行号 | 调用场景 |
|---|---|---|
| `renew_token.py` | 226 | EMERGENCY / 续期失败告警 |
| `feishu_alert.py` | 65-68 | 底层发送函数，所有告警的出口 |
| `captcha_novnc.py` | 37 | 撞验证码时发 noVNC 连接信息（URL + 密码） |

**已修复并部署（2026-10-01 08:24）**：
- `renew_token.py` log() 函数：飞书发送结果写入 `renew_token.log`（`飞书告警: ✅ 已发` / `❌ 失败` / `异常: ...`）
- `feishu_alert.py` send_feishu()：失败时打印具体原因到 stderr（`http_401` / `code=19001 msg=...` / `ConnectionError: ...`）
- `captcha_novnc.py` notify()：飞书发送结果打印到 stderr（`[captcha_novnc] 飞书告警: ✅ 已发` / `❌ 失败` / `异常: ...`）

**验证**：部署后手动测试 `send_feishu("test")` 返回 True，飞书群收到消息。

> **备注**：`captcha_novnc.py` 通过 `wx_alert.send_weixin`（即 `feishu_alert.send_feishu` 的别名）发送 noVNC 连接。撞验证码时会发送 4 条飞书消息：①撞码通知+noVNC地址 ②无人处理时转后台探测通知 ③恢复通知 ④超时未恢复通知。

#### 问题 C：多冷却文件叠加导致死锁

| 冷却文件 | 触发条件 | 冷却时长 | 位置 |
|---|---|---|---|
| `renew_relogin_state.json` | 密码重登失败 | 30 min | `/app/data/` |
| `renew_browser_backoff.json` | 浏览器续期失败 | 30 min | `/app/data/` |
| `renew_browser_backoff.json`（captcha） | 撞验证码 | **60 min** | 同上（ts 字段区分） |

三个冷却叠加时，系统完全卡死：浏览器续期在退避、密码重登在退避、验证码退避 1h。唯一的恢复路径是手动删除冷却文件。

**建议修复**：
1. 合并为单一 `renew_cooldown.json`，记录 `{type, ts, reason}`
2. 撞验证码时不叠加浏览器退避（captcha 本身就是更强的退避）
3. 添加 `tools/clear_renew_cooldown.py` 一键清除（已存在但需确认路径正确）

#### 问题 D：`.session.lock` 路径不统一

| 锁文件 | 路径 | 备注 |
|---|---|---|
| `_session.lock` | `/app/.session.lock` | renew_token 用的 |
| 期望位置 | `/app/data/.session.lock` | 与其他状态文件一致 |

**修复**：S1 迁移时统一锁文件路径到 `./data/` 或 `./secrets/`。

#### 问题 E（🔴 P0）：`renew_token` 持锁调「也要拿锁」的子进程 ⇒ 自动重登 100% 失败

> 2026-10-01 10:20 实测复现。详见 [EXECUTION-LOG.md](EXECUTION-LOG.md) 的同日一节。

```python
# renew_token.py:851
with guard("renew_token", timeout=20) as got:   # ← 父进程持有 flock（fd1）
    rc = _renew_locked()
        └→ _try_password_relogin()
            └→ subprocess.run([python, headless_relogin.py])
                └ headless_relogin.py:220  with guard("headless_relogin", timeout=180)
                                            # ← 自己再 os.open 得 fd2（新 OFD）→ 冲突
                                            # → 死等 180s → print("SKIP: 另一个进程…")
```

`flock` 是 **per open-file-description** 的锁：子进程 `exec` 后虽继承了父的 fd1，
但它**自己又 open 一次**（fd2），与父的排他锁冲突 ⇒ 永远拿不到。
日志上表现为 `ESCALATE` 之后**正好 180 秒**出现 SKIP（10:25:05 → 10:28:05）。

**影响**：巡检能告警（已修好），但**自动恢复是坏的** —— 两次长故障
（09-30 的 8.8h、10-01 的 13 分钟）**都是靠人工才恢复的**。

**⚠️ 同时更正一个此前的误判**：09-30 那次我判断为"stale lock，删掉即可"。
实测（`tools/probes/probe_session_lock.py`）证明：**锁文件残留是正常的**
（flock 刻意不删文件，内核在进程退出时释放锁），当时的 `能否立刻拿到 flock = True`
且**没有任何进程持有它**。真正原因是"父进程活着并且合法持锁"。
"删锁"之所以有效，只是让下次 open 得到新 inode 绕开了冲突 —— **误打误撞**。

**修法（方案 A，推荐）** —— ✅ **2026-10-01 21:50 已按此实施并上线**：
父进程已持锁，子进程不需要再锁：

```python
# renew_token.py:_try_password_relogin
env={**os.environ, "DZMM_RELOGIN_UNDER_PARENT_LOCK": "1"}

# headless_relogin.py:main
under_parent = os.environ.get("DZMM_RELOGIN_UNDER_PARENT_LOCK") == "1"
cm = nullcontext(True) if under_parent else guard("headless_relogin", timeout=180)
```

**实施与验证**（详见 [EXECUTION-LOG.md](EXECUTION-LOG.md) 同日"P0 修复并上线"一节）：

- 落地形态比上面草图略进一步：`main()` 内用 `_guard()` 包装函数，
  这样 `--manual-captcha`（600s）与普通重登（180s）两条路径统一走同一个判定。
- 单测 2 条（正/反两面，反向那条专门防止"默认放开"这种更危险的改法）+ 全量
  `pytest` 100% 通过。
- **线上 A/B 对照**（真实持锁进程）：不带变量 `rc=124` 零输出（被卡死）；
  带上变量 `rc=0` 且立刻 `OK: 已登录`。
- 部署只推了这 2 个文件 + 重建镜像，**没有**顺带把本地未部署的 Phase 3/4A 推上线。

**不要**用 `DZMM_SESSION_LOCK=off`：那是全局开关，语义太宽。

**配套加固**（仍未做）：① `guard()` 拿不到锁时打印**持锁者信息**（锁文件里就写着
`reason pid=… at=…`）；② `_try_password_relogin` 在起子进程前**先探测锁是否可获取**，
若"父自己持锁"应当**直接告警**而不是白等 180 秒。

**⚠️ 认知更正**：`tests/test_renew_escalation.py:191` 原先断言"重登走的是**另一把锁**，
不会和自己持有的 renew_token 锁打架" —— **这条断言是错的**（`guard()` 只有一把锁文件，
`reason` 只是写进锁里的一句备注），它把 P0 藏了整整两天。已连同修复一并更正。

---

## 七、Phase 3 — L2 bot.py 拆分

> 中等风险，4个施工窗口，每步全量pytest+deploy验证

### 3-1. 拆分路线图

```
bot.py (4908行)
    │
    ├── _handle_joins(cid, joins, sender, dry)         ← ~100行 (Step 1, 最独立)
    │   入群筛查+欢迎语
    │
    ├── _dispatch_slash(cid, cmd, cmd_sender, dry, ...) ← ~300行 (Step 2)
    │   斜杠指令分派(/运势/羞辱/调教/发骚/投票/剧情/...)
    │
    ├── _handle_room(sender, cid, room_hit, msgs, ...)  ← ~250行 (Step 3)
    │   房间消息处理(管家回应/女鬼/布局/花瓣)
    │
    ├── _handle_at_chat(sender, cid, at_msg, ...)       ← ~200行 (Step 4, 最复杂)
    │   @闲聊全流程(上下文+记忆+生成+装饰+冷却+兜底+发送)
    │
    └── main_loop()                                     ← ~500行 (骨架)
        只做: 拉消息 → 过滤 → 路由到上面四个函数 → 更新last_id
```

### 3-2. 每步验收标准

```
□ 全量 pytest 绿
□ test_p8s4_split.py 通过 (搬走的函数不在bot.py留定义)
□ deploy + 巡检正常
□ 线上对应功能手动验证
□ git tag 标记里程碑
```

### 3-3. brains.py 提示词分离

将每个`_p("KEY", "...")`的默认值提取到`prompts/KEY.md`，brains.py只保留`generate_*()` + `_call_llm()` + `_p()` loader。测试钉住byte-identical。

### 3-4. personas.py 人设分离

```
personas/
├── __init__.py      ← fill/p/persona_for/phrases_for
├── registry.py      ← PERSONAS/PHRASES/PHRASE_LABELS汇总
├── nuo.py           ← 小小糯 extra + phrases
├── ruojin.py        ← 若瑾 extra + phrases
├── weisi.py         ← 薇丝 extra + phrases
└── defaults.py      ← 默认话术(PHRASES["nuo"])
```

---

## 八、Phase 4 — 运维后台重构

> 中等风险，4-6天，详见 [ADMIN-OPTIMIZATION.md](ADMIN-OPTIMIZATION.md)

### 4-1. 已交付的前端资产

| 文件 | 大小 | 内容 |
|---|---|---|
| `admin/static/base.css` | ~8KB | Design Tokens(暗/亮双主题) + Reset + App Shell + 响应式 + 动画 |
| `admin/static/components.css` | ~10KB | 15种UI组件(Badge/Card/Table/Msg/Button/Input/Tabs/Toast/Skeleton/...) |
| `admin/static/dashboard.css` | ~3KB | 仪表盘专用(status-grid/alert-banner/log-viewer/proof-item/heat-bar) |
| `admin/static/app.js` | ~7KB | 8个渐进增强模块(主题/侧边栏/dirty/校验/自动刷新/排序/折叠/Tabs) |

### 4-2. 子阶段

| 子阶段 | 内容 | 工期 |
|---|---|---|
| 4A 样式层升级 | 注入新CSS/JS → 逐个UI文件替换_CSS → 删除冗余内联CSS | 1天 |
| 4B 布局重构 | App Shell结构 → 侧边栏导航 → 首页分层仪表盘 → 登录页重设计 → 移动端适配 | 1-2天 |
| 4C 渲染引擎 | 组件函数库 → section注册制 → dashboard.py瘦身(2091→~800行) | 2-3天 |
| 4D API+实时 | 拆分轻量API端点 → 30s自动刷新 → outbox筛选分页 → 日志SSE | 2天 |
| 4E 编辑体验 | diff预览 → 滑块调参 → 字符计数 → 保存锚点 → Toast通知 | 2天 |
| 4F 新功能 | 日志查看器 / 审计可视化 / 群详情 / 在线测试台 / 导出导入 | 按需 |

### 4-3. 安全约束不变

结构性只读 / CSRF / 无JS可用 / CSP script-src 'self' / 登录session锁定 — 全部保留。

---

## 九、Phase 5 — L3 架构重构

> 高风险，长期规划，等L2完成后评估

### 5-1. 消息管道抽象

```python
class MessagePipeline:
    filters: list[Filter]      # at_only, quote_reply, slash_cmds, cooldown
    routers: list[Router]      # fortune, insult, chat, room, recap, ...
    post_processors: list[PP]  # decor, humanize, solicit_guard, spice

    async def process(self, msg: InboundMessage) -> OutboundMessage | None
```

新增指令类型 = 加一个Router handler，不改main_loop。

### 5-2. 统一状态存储接口

```python
class StateStore(Protocol):
    def get_turns(cid, n, only_uid=None, drop_bots=True) -> list[Turn]
    def remember_turn(cid, turn: Turn)
    def get_group_state(cid) -> dict
    def save_group_state(cid, state: dict)
    def get_memory(cid, query, n) -> list[MemoryEntry]
    def get_profile(uid) -> dict | None
    def upsert_profile(uid, data: dict)
    def enqueue(outbound: OutboundMessage)
    def mark_sent(id, platform_id)
    def mark_failed(id, reason)
    def failed_count() -> int
```

### 5-3. 发送通道抽象

```python
class SendChannel(Protocol):
    async def send(self, text: str, cid: str) -> bool

class AccountChannel(SendChannel): ...   # typing + socket + split
class BotApiChannel(SendChannel): ...    # POST /bot/message
class RoutingSender(SendChannel): ...    # 按cid路由
```

---

## 十、执行路线图

```
Week 1: Phase 0 + Phase 1
  Day 1: build cache清理 + logrotate + pw_profile限制 + 根目录清理
  Day 2: tools/归档 + docs/索引
  Day 3: 在线验证②③④ (probe_bd_ab + 等@流量)

Week 2: Phase 2 (S1凭据)
  Day 1: 代码准备(4处env var)
  Day 2: compose变更 + 宿主迁移 + 双写期部署
  Day 3: 观察续期 + 确认原子写回归 + 清理老挂载

Week 3-4: Phase 3 Step 1-2 (bot.py拆分前半)
  Week 3: _handle_joins 抽出 + 全量测试 + deploy验证
  Week 4: _dispatch_slash 抽出 + 全量测试 + deploy验证

Week 5-6: Phase 3 Step 3-4 + brains/personas分离
  Week 5: _handle_room + _handle_at_chat 抽出
  Week 6: brains.py提示词外置 + personas.py按人设拆文件

Week 7-10: Phase 4 (后台重构)
  Week 7: 4A 样式层升级
  Week 8: 4B 布局重构
  Week 9: 4C 渲染引擎 + 4D API
  Week 10: 4E 编辑体验 + 4F 新功能(按需)

Quarter 2+: Phase 5 (按需)
  消息管道 / 统一状态存储 / 发送通道抽象
```

---

## 十一、风险矩阵

| 阶段 | 风险 | 缓解措施 |
|---|---|---|
| Phase 0 | 零 | — |
| Phase 1 | 移动文件导致import断裂 | grep验证所有import路径; 全量pytest |
| Phase 2 S1 | 凭据写错→掉线 | 双写期保留老挂载; env var unset=老路径; 观察一次续期再清理 |
| Phase 3 拆分 | 共享状态遗漏→功能回归 | 每步全量pytest+deploy+线上手验; git tag可秒回滚 |
| Phase 3 brains | 提示词内容变化→生成质量降 | byte-identical测试钉住; A/B对比 |
| Phase 3 personas | import路径变化→启动失败 | 全量pytest; 灰度(先一个群) |
| Phase 4A-C | CSS覆盖不完全→样式错乱 | 追加不替换; 旧_CSS作fallback; 逐页视觉对比 |
| Phase 4D | 新API暴露敏感数据 | 所有新端点走_require_session(); 只读; 测试钉住 |
| Phase 5 | 架构级回归 | 设计评审; feature flag; 渐进切换 |

---

## 十二、成功指标

| 指标 | 当前 | Phase 0-1后 | Phase 2-3后 | Phase 4后 | Phase 5后 |
|---|---|---|---|---|---|
| bot.py行数 | 4908 | 4908 | <1500 | <1500 | <800 |
| dashboard.py行数 | 2091 | 2091 | 2091 | ~800 | ~800 |
| 改一个功能需读文件数 | 1-3 | 1-3 | 1 | 1 | 1 |
| 新人上手时间 | 数天 | 半天(有索引) | 半天 | 半天 | 2小时 |
| 提示词修改碰Python | 是 | 是 | 否 | 否 | 否 |
| 续期原子写 | ❌ EBUSY | ❌ | ✅ | ✅ | ✅ |
| 状态文件位置 | 7+处 | 7+处 | 6处(secrets/) | 6处 | 统一接口 |
| tools找工具 | grep盲搜 | 按目录浏览 | 同左 | 同左 | 同左 |
| Build Cache | 1GB | <500MB | <500MB | <500MB | <500MB |
| bot.log大小 | 无限增长 | <10MB轮转 | 同左 | 同左 | 同左 |
| 后台CSS维护点 | 5份 | 5份 | 5份 | 3份 | 3份 |
| 后台亮色主题 | ❌ | ❌ | ❌ | ✅ | ✅ |
| 后台首页到关键信息 | 翻找10卡片 | 翻找10卡片 | 翻找10卡片 | 3个大数字 | 3个大数字+自动刷新 |
| 测试覆盖 | 88文件 | 88+索引测试 | 90+(拆分单测) | 95+(后台单测) | 100+(管道单测) |

---

## 附录

### A. 12轮施工完成清单

| 轮次 | 完成项 | 上线时间 | md5验证 |
|---|---|---|---|
| R1 | ①硬编码提示→话术表 + ③冷却 + ④e兜底 + ②双边上下文 | 21:46 | 热加载 |
| R2 | ⑤e #运势趋势路由修复 | 21:54 | ce0cf015 |
| R3 | ⑤b 羞辱/调教max_tokens参数 | 21:58 | c8b82920/d0d1bd67 |
| R4 | ⑤a rank.top_n参数 | 22:03 | 228a5d7c |
| R5 | ⑤c 管家妹妹自称泄漏修复 | 22:08 | 6ec286c5 |
| R6 | ⑦会话画像盘点工具(S3实证) | 22:1x | — |
| R7 | ⑦文档回填(P12-BP-BQ-BE上线记录) | 22:1x | — |
| R8 | ⑦假failed结案(expired终态) | 22:13 | 685a583d |
| R9 | 核实failed=0 + S1读口调研 | 22:1x | DB实查 |
| R10 | 前端资产(base/components/dashboard.css + app.js v2) | 22:2x | — |
| R11 | ADMIN-OPTIMIZATION.md + PLATFORM-OPTIMIZATION.md | 22:2x | — |
| R12 | 本文档(完整版) | 22:2x | — |
| R13 | 8.8h事故恢复 + 飞书告警静默修复(renew_token + feishu_alert + captcha_novnc三处) | 08:24 | 已部署验证 |

### B. 相关文档索引

- [ADMIN-OPTIMIZATION.md](ADMIN-OPTIMIZATION.md) — 后台优化详细方案
- [P12-BR-P3旧账施工清单.md](P12-BR-P3旧账施工清单.md) — ⑤⑥⑦落点+验收+S1五步法
- [P12-BP-BQ-BE-上线记录.md](P12-BP-BQ-BE-上线记录.md) — 最近一批改动留档+零回归清单
- [P12-BL-续期链再检查.md](P12-BL-续期链再检查.md) — S1隐患清单(S1-S9)
- [P12-BD-若瑾更像人-方案.md](P12-BD-若瑾更像人-方案.md) — 递话率治理+A/B实测

### C. Design Token速查

见 `admin/static/base.css` 的 `:root` 块。暗色/亮色双主题，通过 `[data-theme="light"]` 切换。

### D. 组件使用示例

见 `admin/static/components.css` 头部注释及各组件CSS类名。15种组件均支持暗/亮主题自动适配。
