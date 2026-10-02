# P12-BR：P3 旧账 + S1 + 清理 —— 施工清单（屋主 2026-09-30 批准"做"）

> 目标里的第 ⑤⑥⑦ 项。每一项都写清**落点**与**验收**，下一轮照单执行即可。
> （P12-BP/BQ/BE 已上线：①③④② 见 `docs/P12-BD-若瑾更像人-方案.md` 与本文件 §0。）

## 0. 已完成的（不要重做）

| 项 | 内容 | 证据 |
|---|---|---|
| P12-BP ① | 硬编码"进行中"提示 → 话术表（7 个新 key、`bot.py` 6 处接线） | `tests/test_play_busy_phrases.py` |
| P12-BQ ③ | `chat.reply_cooldown`（上班群 90s） | `tests/test_chat_guard.py` |
| P12-BQ ④e | `chat.solicit_guard`（递话 → 重写一次 → 删最后那句） | 同上 |
| P12-BE ② | `reply.ctx_mode` + uid + 滤别的 bot + 记忆按人过滤 + 【只跟他说】 | `tests/test_ctx_bilateral.py` |
| 帧上限 | 460 → 560（P12-BE 那一行 506 字） | `tests/test_at_focus.py` |

部署：`backup-code-20260930-214539.tar.gz`；热加载 21:46:45 生效（cooldown/solicit_guard/ctx_mode）。

## 1. ⑤ P3 旧账（五项）

### a. rank top-10 改成按群可调
- 落点：`brains.generate_rank()`（`brains.py:861`）与调用它的排行分支；榜行数现在是**写死的 10**。
- 做法：新增 `Param("rank.top_n", "排行榜取前几名", 10, "名", 1, 50)`，调用处传参；默认 10 = 零回归。
- 验收：单测钉默认值（`tests/test_tuning.py` 的对照表）+ 排行分支读的是 `_tune("rank.top_n", cid)`。

### b. 羞辱 / 调教的长度参数
- 落点：`generate_insult` / `generate_diaojiao`（`brains.py`）里各自的 `max_tokens`。
- 做法：`Param("insult.max_tokens", …, <现默认值>)`、`Param("diaojiao.max_tokens", …, <现默认值>)`，
  `bot.py` 调用处改成 `_tune(...)`。**先把现默认值读出来写进 Param**，否则就是行为变化。
- 验收：`_tune` 出现在两处调用点；默认值对照表补两行。

### c. "妹妹"自称泄漏
- 已知泄漏源：`brains.py:227`（羞辱）、`brains.py:235`（结尾钩子示例）、`brains.py:303`（发骚）
  写的是"自称妹妹、叫目标姐姐"；`personas.py:213` 有一条**覆盖声明**（若瑾用），但
  **玫瑰房管家 / 认证群那份没有对应声明** ⇒ 那两处仍会漏。
- 做法：把这三处提示词的"自称/称呼"改成**中性写法**（"自称由人设决定，默认叫对方名字/你"），
  再在 `personas.py` 需要旧行为的群里显式写回来（**旧行为要显式保留 = 零回归靠配置**）。
- 验收：`tests/test_orientation_filter.py` 扩展：三处提示词都不许出现硬编码"妹妹"自称；
  若瑾/管家两条路的 `apply_persona` 输出里没有"自称妹妹"。

### d. `main_loop` 拆分
- 现状：`bot.py` 单函数极长（分派链 3.5k~4.4k 行），改动风险集中在"共享分支/落空转发"。
- 做法（**分步、每步一次 deploy**）：先抽**纯函数块**（能单测、无状态）：
  ① `_dispatch_slash(cid, cmd, cmd_sender)`（斜杠指令那一整段）；
  ② `_handle_at(cid, at_msg, at_sender, via_quote, dry)`（@ 那条路）。
  ⚠️ 每抽一块跑全量 pytest（`test_p8s4_split.py` 会拦"搬走又留定义"）。
- 验收：块抽出后行为逐字不变（对比 `pre`/`post` 的 dispatch 单测），且 `bot.py` 行数下降。

### e. `#运势趋势` 在茶水间给的是**个人卡**（应该是群榜）
- 现状：`is_fortune_cmd` 是 `startswith` 语义 ⇒ `#运势趋势` 命中运势；茶水间 `trend` 关着，
  于是 trend 分支跳过、落到 fortune ⇒ **给个人卡**。`tests/test_fortune_tea.py:40-43` 记着这个边角。
- 做法：在 `#` 那一族里把 **趋势族**（`is_trend_cmd` / `is_trend_board_cmd`）优先接到
  `week_trend_board`；该群没开 `trend` 时回话术 `trend_off`（新增 key）——
  **宁可说"这个群没开运势趋势"，也不要给一张错的个人卡**。
- 验收：茶水间 `#运势趋势` 不再返回个人运势卡（单测钉住分派顺序）；认证群 `/运势趋势` 行为不变。

## 2. ⑥ S1：凭据挂目录（根治非原子写）

**动之前先看这四处读口**（2026-09-30 22:1x 在容器里 grep 出来的，**三个不同路径**）：

| 文件 | 读的路径 | 备注 |
|---|---|---|
| `token_store.py:22` | `Path(__file__).parent / ".dzmm_token.json"`（= `/app/.dzmm_token.json`） | 唯一写入点，容器内 |
| `admin/dashboard.py:101` | `ROOT / ".dzmm_token.json"` | 面板显示用 |
| `monitor_recover.py:22` | `/opt/dzmm/.dzmm_token.json`（**宿主路径**） | 宿主看护脚本 |
| `switch_node.py:21` | `/opt/dzmm/.dzmm_token.json`（**宿主路径**） | 换域名脚本 |

⇒ 迁移时**四处都要跟**（或统一改成读 `DZMM_TOKEN_FILE` 环境变量，容器与宿主各写一份），
漏一处的表现是"bot 正常、但看护/换域名脚本读到旧文件或读不到"。

**建议的落地顺序**（每步可回滚）：
1. `token_store.py` 支持 `DZMM_TOKEN_FILE` 环境变量（**不设就还是老路径 ⇒ 零回归**）；
2. compose 加 `- ./secrets:/app/secrets` 与 `DZMM_TOKEN_FILE=/app/secrets/token.json`，
   **同时保留**老的 `.dzmm_token.json` 挂载一轮（双写期：老文件继续更新，便于秒回滚）；
3. 宿主 `secrets/token.json` 从老文件拷一份、`chown 501:games`、`chmod 600`；
4. 观察一次续期：日志里**没有** `原子替换失败` ⇒ 原子写回归；
5. 下掉老挂载与老文件，把 `monitor_recover.py` / `switch_node.py` 也切到新路径。

## 2.1 已核清：failed 确实清 0（那一行是**提交前**的快照）

直查容器里的库（2026-09-30 22:1x）：

```
PRAGMA journal_mode → wal
select status,count(*) group by status →
   cancelled=2   expired=3   sent=643      ← 没有任何 failed 行
```

⇒ 结案**生效了**；运行中进程 22:15:01 打的那行 `failed=3` 是它那次查询**早于**
工具提交的快照（WAL 下长连接读就是这样），下一轮那行 30 分钟的周期日志会显示 `failed=0`。

**两处要更正我自己的说法**：
1. 上一轮我说"状态机只有 pending/leased/sent/failed、没有终态"——**不准确**，
   库里确实还有 `cancelled`（2 条），说明**已有**一个终态语义（撤回了的消息）。
   我新加的 `expired` 与它语义不同（"放弃补发的陈旧失败" vs "被撤回"），保留；
   但如果只想要"一个终态"，复用它更省事 —— 这一点记在这里，别再重复发明。
2. "3 条 09-27 stale"实际是 3 条不同日期的陈旧记录：09-25 22:30、09-26 09:17、
   09-27 22:00（目标里说的"09-27 stale"是概称）。

- 现状（本轮新证据）：容器内 `/app/.dzmm_token.json.bak` 属主 root、**在容器可写层**（recreate 即丢），
  而且永远慢一代（token 轮换 ⇒ 回退拿到的是失效 token）。宿主上只有真文件 + 一份 09-26 手工备份。
- 改法：
  1. `docker-compose.prod.yml`：把 `- ./.dzmm_token.json:/app/.dzmm_token.json` 换成
     `- ./secrets:/app/secrets`（目录挂载），`token_store.TOKEN_FILE` 指到 `/app/secrets/token.json`；
  2. 迁移：宿主 `mkdir /opt/dzmm/secrets` → `cp -a .dzmm_token.json secrets/token.json` →
     `chown 501:games`、`chmod 600`（属主/权限要跟现在一致）；
  3. 宿主脚本路径：`monitor_recover.py` / `switch_node.py` / `check_health.py` 里读凭据的地方一起改；
  4. `-Action compose`（build + up -d）——**这一次 recreate 是必要的**。
- 验收：续期一次（`renew_token.log` 出现 `OK: token 续期成功`）且**没有** `原子替换失败` 警告；
  容器重建后 `.bak` 仍在宿主上；`token_store.save_cookies()` 走的是 `os.replace`。

## 3. ⑦ 清理与留证

- **那条假 failed**：出站队列 `09-30 16:55 4fc4a2fd 265`（"哥哥，一半就一半…"）实际**已投递**，
  只是进程重启打断、未确认 → 标成 `delivered`/`expired` 而不是 `failed`（`runtime/outbox.py` 的收尾报告 + 一条 SQL 修正）。
  同时 3 条 09-27 的 stale 一并处理。验收：启动日志里 `failed=` 归零或只剩真失败。
- **会话画像盘点工具**（S3 留证）：`tools/probe_session_profiles.py`（**已做**）—— 列出
  `pw_profile` / `pw_login` / `pw_captcha` 等 profile 里的登录态 cookie 与过期时间。
  **2026-09-30 22:0x 实测（第一次跑就有了证据）**：

  | profile | 提交的登录态 | 过期时间 |
  |---|---|---|
  | `pw_profile` | `sb-rls-auth-token` @ www.dzmm.ai | **11-04 20:26** |
  | `pw_login` | 同上 | **11-04 20:26** |
  | `pw_captcha` | 同上 | **11-03 21:21**（差一天） |

  ⇒ **三个 profile 都握着"有效"的登录态，而且至少是两个不同的 token**
  （`pw_captcha` 那份早一天）。这与 S3 的头号嫌疑一致：**多端 refresh_token 轮换互相踢**。
  下一步（要有证据才动）：① 把 `pw_captcha` 那份清掉/隔离（它只在人工过验证码时用）；
  ② 或者下次坏窗口时用本工具 + `renew_token.log` 的原始响应比对时间戳。
- **文档回填**：`P12-BP-进行中提示走话术表.md`、`P12-BQ-回复冷却与递话兜底.md`、
  `P12-BE-双边上下文.md`（这一篇可以把 P12-BD 附录 A 收编）。
