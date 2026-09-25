# W1 探针工具包

对应 `docs/BotAPI实施计划.md` 的 **W1 · 探针与能力确认**。
目的：在动任何业务代码之前，用**真实推送**把平台 Bot API 的实际能力测清楚——因为整个迁移方案的形状取决于 8 个问题的答案。

## 文件

| 文件 | 作用 | 回答哪几问 |
|---|---|---|
| `webhook_probe.py` | 接收并落盘平台推送；`/report` 自动判定 | Q1 全量消息 / Q2 入群事件 / Q3 mention 实体 / Q5 update_id 重放 |
| `api_probe.py` | 出站接口实测 + 文档外端点扫描 | Q4 能否 @ 人 / Q5 频率限制 / Q6 隐藏端点 |
| `captured/` | 抓取到的原始 update（**已 gitignore**，含真实群消息） | 证据留档 |

---

## 一、本机跑起来（先验证工具本身）

```powershell
cd D:\Deepseek\dzmm_botapi
python probe\webhook_probe.py --bot-key test --port 8081
```

自测（另开一个终端）：

```powershell
# 模拟平台投递一条 update
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8081/webhook/test `
  -Headers @{'X-Telegram-Bot-Api-Secret-Token'='dummy'} `
  -ContentType 'application/json' `
  -Body '{"update_id":1,"message":{"message_id":"m1","chat":{"id":"c1","type":"group","title":"测试群"},"from":{"id":"u1","is_bot":false,"first_name":"阿猫"},"date":1780000000,"text":"@bot 你好"}}'

Invoke-RestMethod http://127.0.0.1:8081/report
```

`/report` 应输出能力矩阵的自动判定结果。

## 二、暴露到公网（平台要求 HTTPS）

平台**拒绝 localhost / 内网 / 非 HTTPS**，所以必须有个公网入口。两条路：

**A. 部署到 ECS + Caddy（与最终生产形态一致，推荐）**
```bash
# ECS 上：Caddyfile
bot.<你的域名> {
    reverse_proxy 127.0.0.1:8081
}
```
→ `https://bot.<你的域名>/webhook/test`

**B. 临时隧道（不想先动服务器时）**
```powershell
cloudflared tunnel --url http://127.0.0.1:8081
# 拿到 https://xxxx.trycloudflare.com → 拼成 /webhook/test
```

## 三、平台侧配置

1. `/studio/bots` 建 **私有** Bot，Webhook URL 填 `https://bot.<域名>/webhook/test`
2. 记下 `API Token`（只显示一次）与 `Webhook Secret`
3. 把 Bot 加进**测试群**（用 `+` 面板 → Add Bot）
4. 在测试群里发几条消息：@bot 的、不 @ 的、含 @别人的、普通闲聊

## 四、跑探针

```powershell
# 收：看自动判定
curl https://bot.<域名>/report          # 或 Invoke-RestMethod

# 发 + 端点扫描（⚠️ 只对测试群用，会真的发消息）
python probe\api_probe.py --token <API Token> --chatroom-id <测试群 cid> `
  --origin https://www.dzmm.ai --proxy http://127.0.0.1:7890
```

> **本机直连 dzmm.ai / laopo.ai 会被 SNI 阻断**（DNS 被污染 / TLS 握手 RST），本地跑 `api_probe.py` 必须带 `--proxy`（默认 `127.0.0.1:7890`，即你正在跑的 FlClash）。

## 五、8 项验证清单 → 结论写进 `docs/BotAPI能力矩阵.md`

| # | 问题 | 判定方式 | 自动? |
|---|---|---|---|
| Q1 | 推送全部群消息还是仅 @/指令 | `/report` 的文本条数 vs 含 @ 条数 + 人工确认几条闲聊 | 半自动 |
| Q2 | 是否推送入群/退群事件 | `/report` 的 update 顶层类型分布 + message 内成员字段 | ✅ |
| Q3 | Update 是否带 mention 实体 | `/report` 的 entities 统计 | ✅ |
| Q4 | 能否 @ 人 / 引用回复 | `api_probe` 探针2，人工看群里是否真 @ 到 | 半自动 |
| Q5 | 频率限制 / 重试 / update_id 重放 | `/report` 重放检测 + `api_probe` 连发 | ✅ |
| Q6 | 文档外端点 | `api_probe` 两个形式扫描，★ 项人工确认 | 半自动 |
| q7 | 玫瑰房是 group 还是 scenario | 平台 UI 试加 Bot；`chat.listAll` 看是否有 `{persona, mode}` | 手动 |
| Q8 | Bot 是否有群管理员权限 | 平台 UI / 尝试管理动作 | 手动 |

## 六、注意

- `captured/` 里是**真实群消息**，已在 `.gitignore` 中排除，不要提交、不要外发。
- `webhook_probe.py` 是**探针**，不是最终服务；正式实现见实施计划 W3 的 `webhook_server.py`（校验 → 幂等 → 立即 200 → 队列 → 复用业务 dispatch）。
- 探针**不做任何业务处理**，收到即存即回 200。
