# P12-AJ：`/api/auth/token` 不是续期接口 —— "经常续期失效"是误判 + 自伤

> 屋主 2026-09-29："为啥现在经常 token 续期失效呀？" → "你再好好检查一下" → "改吧"。
>
> ⚠️ 本文档记录的是**推翻我自己 09-27 结论**的过程。上一版我说"平台不认 refresh_token、
> 只能重登"，实测证明那个判断是错的：**问题主要在我们自己的判定逻辑**。

---

## 1. 决定性实测：那个接口只是"读"token

会话新鲜时（access_token 还剩 3434 秒）直接问平台：

```
== 裸 HTTP GET /api/auth/token ==
HTTP = 200
返回的 access fp = eyJhbGciOi/6effc75b
和本地是同一个吗  = True          ← 原样把我们手里的 token 回显回来
返回 refresh     = None           ← 压根没有 refresh 这回事
```

浏览器身份（`renew_token_browser.py` 同法）也一样：

```
09:46:55 OK: 浏览器续期成功（expires_at=1790649824 …）   ← 和原来**一模一样**
浏览器里 JWT fp = 54c2bc6d9f ；和 JSON 一样吗 = True      ← 加载页面 7 秒，零轮换
浏览器里 access 剩余 = 3399 | refresh = '6audwn5orxbh'   ← 同一个 refresh_token
```

日志统计把这件事钉死：

| 日志行 | 次数 | 真实含义 |
|---|---|---|
| `SKIP: token 未变化` | **1220** | 平台回的永远是手里那个 —— 它没在续期 |
| `OK: token 续期成功` | 689 | 只是**抄到了服务端较新的 token**（浏览器刷新过），不是我们续的 |
| `OK: 浏览器续期成功` | 1 | 假成功：只因我们重新编码后字符串不同 |

**结论：`/api/auth/token` 是只读回显接口。access_token 一过期（登录后 1 小时），它必然 401。**

## 2. 于是有了一个"自伤循环"

```
renew_token.py:  400/401 → 判成 "refresh_token 失效" → 写 token.emergency   （累计 80 次）
bot.py:          ⚠️ 检测到 token.emergency 信号，主动退出交给 entrypoint 重启
                 20:05 → 21:05 → 22:06 → 23:06 → 00:06 → … → 07:22 → 09:37（每小时一条）
entrypoint:      headless_relogin.py 真登录一次 → 好一小时 → 再来一遍
check_health:    "access_token 过期且续期失败" 告警（09-08 起反复出现）
```

**而同期业务调用几乎没报过 unauthorized**（自动登录恢复全程只有 6 次）。
也就是说：**机器人被自己每小时干掉重启一次，还顺带每小时做一次账号密码登录** ——
01:21 那次"撞验证码（迷宫滑块）"就是这种高频登录攒出来的风控。

（有一环我没有直接证据：04:00–07:37 那几小时群里**没有任何出站流量**，
所以"当时会话其实可用"这句话当时没法打包票 —— 这正是下面第 1 条探针要解决的。）

## 3. 改了什么

| 位置 | 改动 |
|---|---|
| `renew_token.py` | 新增 `_api_probe()`：401 之后用**真实业务调用**（`chatroom.getMessages`）再问一次。业务正常 ⇒ 记 `NOTE:`、`record_success()`、**不写 emergency、不重启**；业务也 401 ⇒ 记 `SESSION_DEAD:` 才触发重登；探针本身失败 ⇒ 不下结论 |
| `renew_token.py` | 新增 `data/session_state.json`（`{at, alive, probe}`）：**"会话活没活"从此只有一个真源** |
| `renew_token.py` | 那段日志**禁用 "FAIL" 字样** —— 巡检 `check_renew()` 按它告警，这就是误报源 |
| `check_bot_health.py` | `transition("token", …)` 改判 `check_session_state()`（探针说会话死了才告警），**不再拿"access_token 过期 5 分钟"当告警**；`check_renew()` 同时认 `FAIL:`/`SESSION_DEAD:`，忽略 `NOTE:` |
| `sio_subscribe.py` | `_token_stale()` 不再解 JWT 的 `expires_at`（那会把 Socket.IO 重连退避顶到 **3600 秒**，一小时里读消息只能靠 HTTP 兜底），改看探针结果 |
| `renew_token.py` | `log()` 显式 `encoding="utf-8"`（巡检按 UTF-8 读；Windows 默认 GBK，测试里当场复现过 `UnicodeDecodeError`） |

## 4. 怎么验收

1. **单元测试** `tests/test_renew_session_probe.py`（12 条）：
   - 探针对三种结果的映射；`_probe_cid()` 读配置/兜底；
   - **只读接口 401 + 业务正常 ⇒ 不写 emergency、返回 0、日志无 FAIL**；
   - 业务也 401 ⇒ 写 emergency + `SESSION_DEAD`；
   - 探针失败 ⇒ 不下结论；
   - 巡检按探针判死、忽略过期文件、`NOTE:` 不触发续期告警；
   - SIO 退避只看探针（源码级钉住 `b64decode` 已从 `_token_stale` 里消失）。
   全量 **1201 收集 / 0 失败 / 4 跳过**。
2. **线上观察窗**：部署后记下 `access_token 到期时刻 10:43:44`，跨越这个点看：
   - 该出现的：`NOTE: 只读接口未授权…会话没死，不重登`；
   - 不该出现的：`token.emergency` 文件、bot.log 里新的"主动退出"、巡检的新告警、容器重启。

## 5. 复盘

- **"接口名"不等于"接口语义"。** `auth/token` 听起来就是换 token 的，实测它只是读；
  我们围绕这个名字写了 689 次"续期成功"的日志和 80 次自杀重启。**能实测就别靠名字推断。**
- **一次"看起来成功"的日志，可能是编码差异。** 浏览器路径那条 `OK` 之所以是假的，
  就是因为 `expires_at` 一字未变、只是我们重新序列化后字符串不同 —— 判断"真的变了吗"
  要比"我写回去了吗"严格。
- **自愈机制会变成自伤**：每小时强杀自己 + 真登录，观感是"一直在失效"，
  实际是**告警把能用的系统拖垮**（顺带攒风控）。所以**判定"失效"的依据必须是最靠近业务的信号**。
- **同一份错误前提会被复制到多处**：`check_bot_health` 和 `sio_subscribe` 都在拿
  `access_token.exp` 当"会话是否有效"。改的时候必须把**所有**消费点一起改，
  否则修一处、另一处继续把重连退避顶到一小时。
