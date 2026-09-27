# P12-AB：体检第三步 · 清洁与可观测（2026-09-27 夜）

> 对应 `docs/全面体检报告-2026-09-27.md` §10 第三步里"能安全落地"的四项
> （P2 清洁/可观测/文档）。**拆 `main_loop` 与"把源码断言改成行为断言"留作下一步** ——
> 理由见文末。

---

## 一、测试临时目录不再无限泄漏

**体检发现**：`tests/_tmp` 攒了 **2,202 个目录 / 46.4 MB**。
根因是 `dbdir` 的 `shutil.rmtree(..., ignore_errors=True)` —— 很多测试直接
`OutboxStore(dbdir / "o.db")` **不关连接**，Windows 上句柄还开着就删不掉，
错误又被静默忽略，于是没人发现。

**修法**（`tests/conftest.py`）：清理时先 `state_store.close()` + `gc.collect()`，
再带 `chmod` 重试 3 次；**删不掉要报出来**（不再 `ignore_errors`）；
另外每次 pytest 开跑前扫一遍上次残留。

**验证**：清空后跑完整套（1,100+ 用例）→ `tests/_tmp` 残留 **0**。

## 二、一次性开发脚本进"隔离区"

体检扫出**全仓 0 引用**的 8 个脚本（查消息/抓 socket/试发送/试刷新…），
用 `git mv` 挪到 `tools/legacy/` 并写了 `README.md` 说明"为什么它们在这里、
以后再变成生产工具该怎么办"。

**刻意没动**的：`batch_register.py`、`captcha_novnc.py`、`manual_login.py`、
`renew_token_browser.py`、`monitor_recover.py`、`regen_novel.py`、`diag_fortune.py`、
`online_status.py` 以及几个 `test_*.py` —— 它们虽然不是被别人 import 的，
但**文档/脚本里写了怎么手工跑**（`captcha_novnc.py` 还被 noVNC 启动链用着），
搬家要先改文档，属于另一个话题（体检报告里单列了）。

## 三、失败消息不再"静默消失"

**体检发现**：2026-09-26 一条星象卡发送失败（`last_error = sender 返回 False`）
之后就永远躺在 `failed` 里 —— 没有轮询、没有告警、没人知道，日志里只有当时一个 `❌`。

**修法**：
- `OutboxStore.failed_count()` / `recent_failed(n)`，并由 `OutboxSender` 暴露出来；
- 主循环每 30 分钟一行：
  `📤 出站队列：pending=x failed=y；最近失败 09-26 09:17 4a56be71 333字「🌹 今日星象…」— sender 返回 False [ambiguous]`；
- 健康巡检 `check_bot_health.py` 新增 `check_outbox()`：**只读**连接（`mode=ro`，
  绝不锁生产库）看 `data/outbox.db`，失败**条数增长**就告警（按增长而非"有失败"，
  否则失败的记录会永久刷屏），积压 > 20 条也告警。

**注意**：失败的记录按设计**不自动重发**（未 ACK 不重发，避免重复打扰群友）——
这次只是让它"看得见"，不是让它重发。

## 四、静默吞异常：要紧的几处补日志 + 棘轮

体检统计：全仓 197 处 `except: pass`（runtime/ 50 处）。这一次补的是
**"丢了用户会察觉"** 的 6 处：

| 位置 | 静默丢掉的后果 |
| --- | --- |
| `group_state._remember` | 群记忆没存上（用户以为记住了） |
| `group_state._set_last_action` | 上一段丢了 → `/继续` 说"没有上一段" |
| `config_store.save` 的备份 | 备份没做成却照样覆盖配置（以为有得回滚） |
| `outbox.retry_once` 的 `reclaim_expired` | 租约过期的记录永远回不到 pending（永不重试） |
| `identity._load_member_names` | 昵称缓存一直补不上，之后一直用降级名 |
| `store.import_json`（上一轮已修） | 迁移失败被永久标记成"完成" |

每个原因**只喊一遍**（模块级 `_WARNED` 集合），不刷屏。
并加**棘轮测试** `tests/test_silent_errors.py`：runtime/ 的静默 handler 数
不得超过 45（只许减不许增），外加"这六处必须真的写日志"的行为断言。

## 五、文档：7 篇过期文档加横幅 + 补上三篇缺失的

- 给 **7 篇**体检点名的过期文档在标题下插了横幅，写清**具体过期点**并指向体检报告：
  `小小糯总方案`、`功能与人设配置方案`、`盘点-群友功能`、`保全清单`、
  `BotAPI实施计划`、`BotAPI能力矩阵`、`剧情游戏-方案A`。
- 新增 `docs/存储-出站-部署说明.md`：把体检里"一行文档都没有"的三个子系统一次补齐
  —— 存储层（形状/三条硬规则/导入标记/回滚开关/对账工具）、
  出站队列（数据流/状态表/rejected vs ambiguous 铁律/可观测/边界）、
  部署（一条命令做了什么/镜像里有什么/**上线前后验证与回滚**）。
  末尾是"改这三块时的三条纪律"。

---

## 六、线上验证

- 本地全量测试全绿（含新增 4 + 4 + 3 + 2 = 13 条）
- 部署后：部署闸门 **ALL CHECKS PASSED（119 项 OK / 0 项 !!）**、启动 0 告警
- `tests/_tmp` 残留 0（本地）

## 七、为什么把"拆 `main_loop`"和"源码断言改行为断言"留到下一步

- 拆 `main_loop`（1,226 行 / 49 分支）是**热路径重构**：分派链里 49 个分支共享十几个
  局部变量（`group_state`/`state`/`pending`/`_no_prefix`/`old_last_id`…），抽函数必须先
  把这些状态显式化（一个 `Ctx` 结构），而现有测试对这条链**大多是源码断言而不是行为断言**
  —— 也就是说"改坏了它不一定红"。
- 所以正确顺序是：**先给分派链补行为测试**（把每条命令的"输入 → 该回什么/该改哪些状态"
  钉住），再动刀拆函数。这两件事一起做才安全，单独做哪一件都危险。
- 下一步建议：① 给分派链写行为测试（用假 sender + 内存 store，覆盖 49 个分支的
  代表性子集）→ ② 拆成 `dispatch_command(ctx)` + `tick_group(ctx)` 两个函数
  → ③ 把 `test_parallel_groups.py` 那 8 条源码断言换成真正起两个群循环的行为测试。
