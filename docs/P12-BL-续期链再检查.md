# P12-BL：续期链再检查一轮（原子凭据写 · 原始响应留证 · 统一 UA）

> 屋主 2026-09-30 17:0x："**续期的问题再检查一下。**"

## 1. 这轮先看现状（结论：链是健康的）

```
① 首页            -> 200 ✅
② /api/auth/token -> 200，有 access_token ✅
③ 业务调用(读群)   -> ✅
session_state     -> {"alive": true, "probe": "ok", "bad_since": null}
退避文件           -> 不存在（干净）
renew_token.log   -> 16:33 OK；16:56 SKIP（token 未变化）
宿主看护           -> 首页=OK auth_token=ok 容器=Up 续期心跳=2分钟前
```

`renew_token.log` 里 15:31 → 16:23 那段"空白"**不是故障**：token 还新鲜时走快路径，
**本来就不打日志**（心跳照写，`session_state` 一直是 ok）。

## 2. 又查到三件事（前两件是真问题）

### ① 凭据文件是**非原子写**（真风险，已修）

`token_store.save_cookies()` 原来是 `TOKEN_FILE.write_text(...)`。
而**容器重建 = SIGKILL**（我今天部署了 10 次），正好可能落在这 3 KB 写的中途：

```
写坏 ⇒ load_cookies() 返回 [] ⇒ 所有平台调用一起未授权
      ⇒ renew_token 连 cookie 都读不到（"FAIL: 未读到 sb-rls-auth-token cookie"）
      ⇒ 只能人工重登
```

凭据是**单点**，必须原子。现在：**临时文件 → `fsync` → `os.replace`**（同目录 rename 在
Linux 上是原子的），失败时原文件保持完好。历史日志里还没有"未读到 cookie"这种字眼
（说明还没被咬到），但这属于"迟早会发生"的那类风险。

### ② 失败时**没有原始响应**（已补）

那几天 4~8 小时的"未授权"窗口，日志里**只有我们自己翻译过的词**
（"未授权"/"refresh_token 失效"）—— 平台到底回了什么（403 挑战页？401？429？）无从得知，
所以到现在也只能推断"平台侧风控"。现在失败那一轮会把原始响应打进日志：

```
原始响应: HTTP 403 body <html>...WAF...</html>
```

（`renew_token.get_new_token()` 把 status + body 前 240 字记进 `LAST_HTTP`。）

### ③ UA 不统一（已统一为浏览器 UA）

实测（`tools/probe_ua_wall.py`，同端点同一时刻）：`Python-urllib/3.11` → **403**，
Chrome / `Mozilla/5.0 DZMM-Agent/1.0` → 200 ⇒ **平台的 WAF 按 UA 挑战**。
`requests` 默认 UA（`python-requests/x.y`）是典型爬虫签名，属于"可能被挑战"的那一类。
`dzmm.api()`（所有 tRPC 调用）与 `renew_token.get_new_token()` 现在都带浏览器 UA。

## 3. 顺手做的生产验证（不只是单测）

| 验证 | 结果 |
|---|---|
| **浏览器续期路径**（每小时真的会走的那条） | 容器内实跑 `renew_token_browser.py` → **rc=0**，日志 `16:56:26 SKIP: token 未变化` ✅ |
| tRPC 带新 UA | 实跑 `dzmm.get_messages(...)` → **拿到 3 条** ✅（换 UA 没把接口换坏） |
| 凭据文件 | `-rw-------`（0600），**没有 `.dzmm_token.json.tmp` 残留** ✅ |
| 体检 | `check_bot_health.py` rc=0 ✅ |
| 部署 | `16:54:48`，`token_store.py` md5 `f195dd45`、`dzmm.py` `8bdf2fdd`、`renew_token.py` `17ee436d`（本地 = 容器） |

单测：`tests/test_renew_hardening.py`（7 条：原子写必须有 `fsync`+`os.replace` 且不许再
`write_text` 覆盖、写读往返/不留 `.tmp`/`update_cookie` 不动别的 cookie、
失败日志必须打原始响应、`get_new_token` 记录 status、两个模块都带浏览器 UA、
UA 实验脚本在仓库里）。

## 4. 仍然未知 / 下一步

1. **那几个 4~8 小时窗口的平台原始响应**：现在有了留证，**下一次出现就能定性**
   （是 WAF 挑战页 / 会话失效 / 限流）。这是目前唯一还缺的证据。
2. **自动换域名的数据**：`data/wall_probes.jsonl` 目前 **0 条**（这几小时没抓到墙窗口），
   按约定继续攒，够了用 `tools/analyze_wall_windows.py` 出结论。
3. 如果哪天又出现"未读到 sb-rls-auth-token cookie"，那就是**凭据文件已损坏**的形态；
   原子写已经把这条堵上，但值得在巡检里加一句识别（下次顺手做）。

---

## 5. 【事故】我自己引入的回归：原子写在 bind mount 上 EBUSY（17:40–17:52）

**这条比前面三条都重要，必须记下来。**

```
17:40:33 FAIL: 写回 token JSON 失败
17:45:33 FAIL: 写回 token JSON 失败
17:45:34 EMERGENCY: 连续 3 次续期失败，写 token.emergency 信号
17:50:35 FAIL: 写回 token JSON 失败
17:50:35 EMERGENCY: 连续 3 次续期失败，写 token.emergency 信号
```

### 根因

`docker-compose.yml:160` 把凭据文件挂成**单文件 bind mount**：

```
- ./.dzmm_token.json:/app/.dzmm_token.json
```

而在单文件 bind mount 上 **`os.replace()` 会 EBUSY**（rename 的目标是挂载点本身）。
我这一轮为了"防写坏"把 `save_cookies()` 改成 tmp + `os.replace`，正好踩中 ——
续期拿得到新 token、**却写不回去** ⇒ 连写失败 3 次 ⇒ EMERGENCY 重启 ⇒ 循环。
那 20 分钟里容器里的 token 一直停在 16:33 那份（等它过期后，平台调用会一起未授权）。

**这个坑仓库里早就写着**：`deploy.ps1` 关于 config.json 的注释跟它一模一样 ——
"单文件 bind mount 上 os.replace 会 EBUSY，原子替换写不成；目录内 rename 就没这个问题"。
我在另一个文件上原样犯了一遍。

### 修法（P12-BL-fix，17:51 上线）

1. **先原子**：tmp → `fsync` → `os.replace`（非挂载点上仍然是最好的做法）；
2. **退化**：`os.replace` 抛 `OSError` 时 → **就地截断写**（挂载点上唯一可行的写法），
   且写之前先把老文件拷一份 `.dzmm_token.json.bak`；
3. **兜底**：`load_cookies()` 在主文件读不出来时**回退 `.bak`** ——
   "没有原子性"不至于升级成"凭据没了、只能人工重登"；
4. 单测钉住：模拟 `os.replace` 抛 EBUSY → 必须能写出新值 + 有 `.bak` + 不留 `.tmp`；
   主文件半截 JSON → 必须能从 `.bak` 读回来。

### 验证（都已跑过）

```
部署 17:51:29 → token_store.py md5 f139e592（本地=容器）
手动 renew_token.py → rc=0；token 文件 mtime 17:52（此前一直停在 16:33）
① 首页 200 ② /api/auth/token 200 有 token ③ 读群消息 ✅
session_state: alive=true probe=ok；退避文件不存在；[sio] ✅ 已连接
宿主看护: 首页=OK auth_token=ok 续期心跳=2分钟前（此前 19 分钟前）
```

### 用户可见影响：**没有**

重建容器之前那段日志已丢，所以我**从群消息反查**了 17:30–17:55（三个群）：
茶水间那段时间全是别家 bot 的游戏（`/躲`、巡查）、认证群没消息、上班群是纯闲聊
—— **没有任何一条 @她**，所以这 20 分钟没有漏答。

### 顺带发现

出站队列 `failed=4`，新增那条是 `09-30 16:55 4fc4a2fd 265「🤖 哥哥，一半就一半…」
— 进程重启打断：未确认是否已投递` —— 但那条**其实投递成功了**
（16:55:27 在群里读得到）。属于"重启打断 ⇒ 不敢重发 ⇒ 记成 failed"的保守记账，
无害，但会让 failed 计数虚高（以后可以按 message_id 读回纠正）。

---

## 6. 续期还剩哪些隐患（2026-09-30 18:0x 盘点）

按"会不会真出事 / 出了事多大 / 有没有兜底"排序。**S1、S2 是今天这轮改动直接相关的。**

| # | 隐患 | 现状 / 证据 | 已有兜底 | 建议 |
|---|---|---|---|---|
| **S1** | **凭据写没有原子性**：`.dzmm_token.json` 是**单文件 bind mount**，`os.replace` 必然 EBUSY，只能就地截断写 | `docker-compose.yml:160`；17:35–17:52 实测 `FAIL: 写回 token JSON 失败` | 写前拷 `.bak` + `load_cookies()` 回退 `.bak` + 会话死自动重登 | **改成挂目录**（`config.json` 就是这么解决的）⇒ 原子写回归，`.bak` 变冗余。要改 compose + 迁移 + 注意属主 |
| **S2** | **四个写者，只有三个持锁**：`bot.py::_harvest_token()`（今天新加的"从浏览器抄回 token"）**不持锁** | 写者：`renew_token`（持锁）/ `renew_token_browser`（在 renew 锁内）/ `headless_relogin`（**同一把锁**，guard 共用 `.session.lock`）/ **bot 的抄回（无锁）** | `.bak` + 自动重登 | 给它套 `guard("token_harvest", timeout=0)`，拿不到锁就跳过这一轮（它本来就是机会主义行为）。**3 行改动** |
| **S3** | "未授权"窗口的**成因仍未定论**：09-23/27/28/29 各一次 4~8 小时 | 窗口内同一 cookie 后来又能用 ⇒ 多半是平台侧；也可能是**多端 refresh_token 轮换冲突**（`pw_profile`/`pw_renew`/`pw_login`/`pw_captcha` 四个 profile 都握着会话） | 升级链（墙判定 + 自动重登）把窗口从小时级压到分钟级；**原始响应留证已就位** | 等下一次样本定性；同时考虑**收敛多端**（清空/统一那几个 profile，或把它们也纳入抄回） |
| **S4** | **撞验证码必须人工** | 09-07、09-28 有 418 记录；`headless_relogin` 撞码会拉 noVNC + 发微信 | 人工滑滑块（10 分钟窗口） | 无自动解（平台风控）；只能减少触发：少登录、UA 统一、别频繁重登 |
| **S5** | `token.emergency` **只重启 bot.py 进程**，不做密码登录 | 今天 17:45/17:50 两次重启都没用（根因是写失败） | 升级链先试重登，emergency 是最后一道 | 可考虑"写 emergency 时也排一次重登"；注意别和升级链重复刷风控 |
| **S6** | `_home_status()` 只认 **200/418** | 实测 WAF 还会回 **403**（`Python-urllib/3.11` 那次） | 判 `err` ⇒ 20 分钟后走浏览器/重登 | 把 403 也算"墙"、5xx 算平台故障（分开记），少绕一圈 |
| **S7** | 快路径**不打日志**（日志安静 50 分钟属正常） | `expires_at-now>600` 直接 return | 心跳 `session_state.at` + 巡检"循环停摆"告警 + 宿主看护 25 分钟阈值 | 保持；**排障先看心跳，别看日志** |
| **S8** | 若改目录挂载，**属主/权限**要跟上 | 现在主文件属主 `501:dialout` 0600，`.bak` 是 root 0600 | — | 改挂载时注意 uid 一致（宿主侧也有脚本要读它） |
| **S9** | 磁盘/quota 写失败 | 与 EBUSY 同一分支，有日志 | 现在 9.8G/40G（27%） | 无 |

**最该先做的两件**：

1. **S2（3 行）** —— 给 bot 的 token 抄回加锁，消掉"非原子写 + 无锁并发"的唯一组合；
2. **S1 根治** —— 把凭据改成**挂目录**（compose 一行 + 迁移），原子写恢复 ⇒ 连 `.bak` 兜底都不再需要。

其余（S3 留证、S4 人工、S5/S6 加固）等下一个真实样本再动，避免在没证据时加复杂度。
