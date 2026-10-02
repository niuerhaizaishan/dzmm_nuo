# P12-BM 方案：茶水间"引用她的消息"为什么触发不了（先不改，等拍板）

> 屋主 2026-09-30 17:2x："**茶水间的小小糯回复她发的消息不能触发她啊？你先别改，你看一下给我方案。**"

## 1. 结论

**平台侧一切正常，是那个开关关着。**

```
茶水间 config.json:
  at_only      = true      ← 群里只接"@ 到她的消息"
  quote_reply  = false     ← "引用回复我也接话"这个口子**显式关着**
  chat = true   slash_cmds = false   fortune = true
```

`bot.py:3513` 的 `at_only` 闸门只放行两种消息：

```python
_by_at    = is_at_me(t, cid)                       # @ 她
_by_quote = (not _by_at) and _feat_on(cid, "quote_reply") and _quotes_self(m)   # 引用她
if not (_by_at or _by_quote):
    continue          # ← 茶水间 quote_reply=False ⇒ 引用她的消息在这里被丢掉
```

于是后面那套"引用回复"的处理（`_via_quote`、把**被引用原文**摆进上下文，P12-AE）
**根本走不到**。

## 2. 证据（三条，都在线上取的）

| # | 证据 | 说明 |
|---|---|---|
| 1 | `probe_group_entry.py` 读线上 config.json | 茶水间 `at_only=true`、**`quote_reply=false`**（是显式 False，不是"没写"） |
| 2 | 对照组：上班群 `quote_reply=true` | 那边引用她会接话 ⇒ 差别只在这个开关 |
| 3 | 茶水间最近 **300 条里 34 条带引用**，含 `08:53:04 糯糯 引用了【她】 → 她发的吗=True` | **账号通道（无 Bot）也带 `content.reference`**，`_quotes_self()` 也认得出是她（`is_self` 覆盖账号 uid） |

⇒ 数据、识别、上下文全都就绪，**只差把闸门放开**。

## 3. 方案

### A. 打开茶水间的 `quote_reply`（**推荐**）

| | |
|---|---|
| 改动 | `features.quote_reply = true`（运维后台按群开关，或 config.json 一行） |
| 生效 | **热生效**（配置层，不用重启、不用部署） |
| 效果 | 引用她任意一条消息 = 触发她回复；P12-AE 会把被引用原文摆进上下文，"只回这一句"的收尾块照旧 |
| 触发量 | 最近 300 条里"引用**她**"的只有 **1 条** ⇒ 不会刷屏 |
| 回滚 | 关回去即恢复原状 |
| 代价 | 0 行代码 |

### B. 让 `at_only` 群里"引用她"**天然算触发**（顺手加固，1 行 + 回归）

把闸门里那个 `and _feat_on(cid, "quote_reply")` 拿掉（或改成"at_only 群默认放行引用"），
`quote_reply` 降级为"非 at_only 群的额外开关"。

- **理由**：`at_only` 的初衷是"别接别家 bot 的语法、别瞎搭话"；
  而**引用她**的消息语义上等价于 @，天然就是对她说的。
- **收益**：以后新群开 `at_only` 自带"引用触发"，不会再踩一次这个坑。
- **代价**：动核心闸门，要同步改两条既有测试的断言
  （`tests/test_gender_and_at_only.py:87`、`tests/test_quote_reply.py:91`）。

### C. 不改，只告诉大家"触发她要 @"

不推荐：手机上"引用回复"是最自然的操作，而且平台已经把引用数据给了我们。

## 4. 顺带两条（要不要一起做，你定）

- **D1 回复冷却**：现在 @ 触发也没有冷却。开了引用触发后，若担心有人连点刷屏，
  可以加一个按人/按群的 `chat.cooldown`（N 秒内只回一次）。
- **D2 "没 @ 她但在说她"她不会接**：例 08:58 小寧"那不得让小小糯来写一写彻和G老板"——
  她**不理**。这是 `at_only` 的既定设计（P12-AL，你当时定的：只 @ 才回），**不是 bug**。
  若想让她"被点名就接话"，那是另一条口子（提人检测 / `devil_roast`），要单独拍板。

## 5. 验收（做 A 或 B 的话）

1. **离线**：合成一条"引用她 + 正文"的消息，喂进闸门/`_quotes_self`，断言
   → 能进 @ 分支、`_via_quote=True`、被引用原文进上下文（`tools/probe_at_focus.py` 有现成骨架）；
2. **单测**：`tests/test_quote_reply.py` 已有 8 条（引用形状、识别、webhook 保留引用）；
   A 再加一条"茶水间配置生效后 `F.on(cid,'quote_reply') is True`"；
   B 则改 `test_gender_and_at_only.py` 里那条闸门断言；
3. **线上实测**：请屋主（或群友）在茶水间**引用她一条**消息说句话，看她回不回 ——
   我不在 792 人群里发测试消息；
4. **回滚**：A 关开关；B revert 一行。

## 6. 建议

**先做 A**（现在就能做，热生效、零风险），**再看要不要 B**（把"引用=触发"变成 at_only 群的默认语义，
避免以后新群再踩）。D1/D2 属于"要不要顺便"，可以分开定。

---

## 7. 实施记录（2026-09-30 17:02 · 屋主选 A）

**已做**：给茶水间打开 `quote_reply`（走现成工具 `tools/enable_quote_reply.py`，
它内部用 `config_store.save()` = 校验 + 备份 + 原子写 + reload）：

```
$ python /app/eqr.py --cid-prefix 4fc4a2fd --apply
目标群： 4fc4a2fd-465a-4436-9884-dbb5e60c6af2
要改的开关： ['quote_reply']
已写入。生效值：quote_reply = True
```

**热生效已确认**（不用重启、不用部署）：

```
17:02:31 P12: 热加载 功能 · 4fc4a2fd-465a-4436-9884-dbb5e60c6af2|quote_reply : 关 → 开
17:02:31 P12: 热加载完成 来源=auto 改动=1 项
```

**闸门验收**（`tools/verify_quote_gate.py`，拿**群里真实消息**跑的）：

```
quote_reply 生效 = True | at_only 生效 = True
最近 300 条里「引用她」的：1 条
  08:53:04 糯糯 正文='你怎么能乖乖趴好呢！' → 被@=False 被引用=True **放行=True**
对照（引用别人的消息，抽查 3 条，应当不放行）：全部 放行=False
合成用例（没 @、引用她）：放行 = True
```

**语义确认**：同时 @ 她又引用她 ⇒ `_by_at` 为真、`_via_quote=False`，**只回一条**（不会重复）。

**回滚**：同一个工具把 `features.quote_reply` 关回去（或后台按群开关一点），热生效。

**还没做的**：B（把"引用=触发"做成 at_only 群的默认语义）、D1（回复冷却）、D2（没 @ 但在说她时接话）。
线上实测等屋主在茶水间引用她一句 —— 触发时日志会打 `🔗 [4fc4a2fd] 这是引用回复（引用了 我自己）`。
