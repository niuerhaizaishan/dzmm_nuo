# P12-AA：体检第二步 · 事件循环与跨群状态（2026-09-27 夜）

> 对应 `docs/全面体检报告-2026-09-27.md` §10 第二步（三条 P1）。
> 全部**已上线并实测验证**：部署闸门 119 项全过、启动 0 告警、三项行为自检通过。

---

## 一、① 事件循环不再被阻塞（体检点名 8 处，已修 6 类）

主循环是单线程 asyncio：任何"同步 HTTP / 读几十个文件 / 调模型"跑在上面，
都会把**那个群**这一轮完全卡住（表现就是"发指令十几秒没反应"）。

| 位置 | 原来 | 现在 |
| --- | --- | --- |
| `bot.py::_handle_climax` | 平台读 `get_messages_all` + **直接调 LLM** `generate_heat_climax`（每个命中一次） | 整段抽成**同步**助手 `_build_climax_texts`，`await asyncio.to_thread(...)`；发送仍留在循环里 |
| `bot.py::_handle_review` | 小说写盘 `open(w)`；`send_weixin`（`requests timeout=15 × retries=2`，**最长 ~45 秒**）；审核通过读文件 | 三处分别走 `_write_text_file` / `to_thread(send_weixin, …)` / `_read_text_file` |
| `bot.py` 记忆注入 | `_retrieve_memory(...)` 内部两级精筛**会调模型**，却没包（紧邻的 `generate_reply` 反而包了） | `await asyncio.to_thread(_retrieve_memory, cid, q, limit)` |
| `runtime/identity.get_full_name` | 缓存未命中时**直接发 HTTP** `get_user`，而它被 `trend/recap/sign/dice` 的 async 函数直接调用 | **事件循环上不发 HTTP**：退化成"档案名 → uid 前缀"并把 uid 记进 `_pending_names`（刻意不写缓存，免得把降级值固化）；工作线程里行为不变。配套：主循环每 10 分钟 `await asyncio.to_thread(_load_member_names, cid)` 批量补真名 |
| `bot.py::hot_reload_check` | 同步函数，配置/提示词变过时要读 `config.json` + 最多 39 个 prompt 文件 | 改成**协程**，`maybe_reload` 整体走 `to_thread`；调用点 `await` |
| `runtime/perf.py::_handle_perf_end` | 小说写盘 + `send_weixin` 都在 async 函数里同步做 | 写盘 → `_write_novel` 走 to_thread；告警 → `to_thread(send_weixin, …)` |

> 没动的一处：`throttle.acquire` 会在调用线程里 sleep —— 它只在 `to_thread` 里的
> `dzmm.api()` 调用链上跑（体检标注 unverified），这次不动它，留在报告里继续观察。

## 二、② 并行下不再跨群丢状态

**问题**：按群并行（P12-I）下三个任务共用**同一份** `state` 字典，而
`save_state(state)` 是 `save_scope("group", 整份映射)` —— **整 scope 重写**。
任务 A 手里那份 `state` 还带着群 B/C 的旧快照，写下去就把 B/C 这一轮的新数据盖回旧值。

**修法**：新增 `bot._save_group(cid, group_state)` =
`state_store.save_state({cid: 本群})`（`save_scope` 只 upsert 传进去的键），
语义与原实现一致，只是不再碰别的群。24 处调用点按"前一行 `state[cid] = <变量>`"
机械替换（脚本 `tools/p12aa_group_save.py`，带 `--check` 与数量校验），
另外修了 `_scheduled_pushes` 里两处 `save_state(st)` —— 它**一次给多个群打点**，
是最容易覆盖别人的地方。

**线上实证**（真库）：写回若瑾群那一行后，认证群那一行的 md5 逐字节不变：
```
认证群那一行 md5: be4d4d35ffcf -> be4d4d35ffcf      没被改动： True
```

## 三、③ 存储 / 队列完整性

| 位置 | 问题 | 修法 |
| --- | --- | --- |
| `runtime/store.py::import_json` | 两个 `try` 都是 `except: pass`，然后**无条件**写 `IMPORT_MARKER` ⇒ 瞬时读失败被**永久**标记成"迁移完成"，旧数据静默留在 JSON 里、再也不重试（与 P12-U 分叉事故同族） | 只有两份都读过（或文件不存在）才落标记；失败**不写标记**并吼一声，下次启动重试 |
| `runtime/outbox.py::OutboxStore` | 无锁 + 默认 `check_same_thread=True`（`store.Store` 早已修过同款） | `check_same_thread=False` + `threading.RLock`，12 个数据方法全部加锁 |

**线上实证**（临时库，不碰生产队列）：
```
线程里入队/读回： 线程里入队 | pending = 1
主线程看到状态： sent        有锁： True
```

## 四、昵称守卫的线上实证

```
预载后缓存条数： 55 / 待补： 0
事件循环上取一个不存在的 uid：'00000000'  耗时 0 ms  在循环上=True
待补集合里有没有它： True    耗时远小于一次 HTTP（<300ms）： True
```

---

## 五、验证与回滚

```bash
# 1) 部署闸门（最全面）
docker cp /tmp/dg.py dzmm-bot:/tmp/dg.py     # 本地 scp tools/deploy_gate.py 到宿主机 /tmp
docker exec dzmm-bot sh -c 'cd /app && GATE_ROOT=/app TEMP=/tmp python /tmp/dg.py | tail -2'
# 期望：ALL CHECKS PASSED（本次 119 项 OK、0 项 !!）

# 2) 行为自检（本文件里的三项）
#    tools/verify_p12aa.py 走容器内运行，见附录命令

# 3) 本地全量测试
$env:PYTHONPATH='D:\Deepseek\DDZM\.pylibs'; python -m pytest tests -q
```

**回滚**：`git revert <commit>` + `.\deploy.ps1 -Action deploy`
（服务器另有整树备份 `backup-code-20260927-181255.tar.gz`）。

## 六、副作用面（须知）

- `get_full_name` 在事件循环上**可能短暂返回"档案名/uid 前缀"**：新成员第一次发言时，
  若批量预载还没跑到，@ 与展示会先用档案名（通常就是群昵称），最多 10 分钟后被真名覆盖。
  这是**刻意的**取舍：宁可名字晚 10 分钟，也不要卡住整个群。
- `_save_group` 只写本群：任何"在一轮里同时改多个群"的新代码都必须**逐群**调用它
  （`_scheduled_pushes` 已是这个写法）。测试 `tests/test_group_state_isolation.py`
  会拦住退回整 scope 的写法。
- `hot_reload_check` 变成协程：任何新调用点必须 `await`。

## 七、遗留（体检报告 §10 第三步）

1. **P2** 拆 `main_loop`（1,226 行 / 49 分支）
2. **P2** 静默吞异常逐个补日志（全仓 197 处）
3. **P2** `tests/_tmp` 残留 2,117 个目录 / 44.7 MB；根目录 24 个无人引用的脚本
4. **P2** 6 份过期文档加"已过期"横幅；补存储/出站/部署三篇
5. **P2** 11% 的测试是"读源码断言"（优先改并行那批）
6. **P3** 单条发送失败无告警（09-26 星象卡丢了一条）
7. **P3** `throttle.acquire` 的调用链是否全部在 `to_thread` 里（体检标注 unverified）
