# P12-S：Bot 改名适配（平台上的「薇丝-bot」→「薇丝」）

> 2026-09-27 屋主把平台上玫瑰房的 Bot 名从 **薇丝-bot** 改成了 **薇丝**（与人设名、发消息的身份彻底一致）。
> 本文记录代码/配置里所有依赖"我自己叫什么"的地方，以及这次的适配。

## 1. 名字参与哪三处判定

| # | 判定 | 代码 | 名字用在哪 |
| --- | --- | --- | --- |
| ① | 这条消息是不是在**叫我**（房间接不接话） | `runtime/identity.py::mentions_self` / `is_at_me` | 客人写的「薇丝」「@薇丝」 |
| ② | 把 **`@` 前缀剥掉**再送进 LLM prompt | `bot._strip_at` ← `self_names_longest_first` | `@薇丝 你好` → `你好` |
| ③ | 从群成员表里**认领 Bot 自己的 uid** | `identity._name_matches_bot` / `resolve_bot_uids` | 平台成员表里的显示名 |

③ 本来就容忍 `-bot` 后缀（`薇丝` / `薇丝-bot` / `薇丝bot` / `薇丝～` 都认）；
① ② 是直接从配置名建的集合，**改名后会漏**：`@薇丝-bot` 先剥「薇丝」会留下 `-bot`，
而这个残余会原样进 prompt（客人看不见、模型看得见）。

## 2. 改了什么

1. **配置（真源）**：`config/config.json` 的 `bots[weisi_rose].name`：`薇丝-bot` → **`薇丝`**
   （`short` 本来就是「薇丝」）。备份：`config.json.bak-20260927-125105`。
   ⚠️ Bot 清单只在**启动时**读，所以改完要重启（本次随部署一起重启）。
2. **代码兜底**：`runtime/identity.py` 新增 `_with_bot_variants()`，`self_names()` 对每个 Bot 名
   同时给出常见后缀/去后缀写法：
   `薇丝` / `薇丝-bot` / `薇丝bot` / `薇丝 bot` / `薇丝-机器人` / `薇丝机器人` …
   ⇒ **改名前后两种写法都认**（历史消息、客人的肌肉记忆都不会掉）。
3. 清理过时注释：`personas.py`、`identity.py` 里"Bot 名叫薇丝-bot"的说法。

## 3. 线上实测（容器内）

```
配置：key=weisi_rose name='薇丝' short='薇丝' uid=36916cb2…     ← 已改
bot_names() = ['小小糯', '小小糯-bot', '薇丝']                    ← 认证群那个没动
self_names() 含薇丝的 = 薇丝 / 薇丝-bot / 薇丝bot / 薇丝 bot / 薇丝-机器人 / 薇丝机器人 …

认不认「在叫我」：薇丝 你好 / 薇丝-bot 你好 / @薇丝 你好 / @薇丝-bot 你好 → 全部认
                  @路人甲 你好 → 不认（没误伤）

剥 @ 前缀：@薇丝-bot 你好呀 → '你好呀'
           @薇丝 你好呀     → '你好呀'
           @薇丝bot 你好呀  → '你好呀'      ← 没有 -bot 残余

自己发的消息：is_self(侧车账号) / is_self(小小糯-bot) / is_self(薇丝-bot) → 全部 True
启动行：P11: Bot key=weisi_rose name=薇丝 cid=4a56be71 uid=36916cb2…
```

## 4. 不需要改的地方（免得下次重复排查）

| 位置 | 为什么不用改 |
| --- | --- |
| 「这条消息是不是我发的」判据 | 走 **uid**（`is_self`），与显示名无关 |
| 房间里的自称 / 话术 / 欢迎语 | 用 `{me}` = **人设名**「薇丝」，本来就是这两个字 |
| 环境变量 `DZMM_BOT_NAME=小小糯-bot` | 那是**认证群**那个 Bot 的名字（未改名） |
| 平台成员表 / 昵称缓存 | Bot 不走成员接口；uid 由配置或 `learn_bot_uid` 认领 |
| `/查`、排行榜、档案 | 都用 `{me}` 或 uid，不碰显示名 |

## 5. 测试

`tests/test_multi_bot_identity.py` 新增 2 条（改名场景）：

- `test_renamed_bot_still_recognizes_old_and_new_writing`：配置成「薇丝」时，
  「薇丝」「薇丝-bot」「@薇丝」「@薇丝-bot」都要认，「@路人甲」不认；
- `test_renamed_bot_strips_the_at_prefix_cleanly`：`@薇丝-bot 你好呀` 必须剥成 `你好呀`（不留 `-bot`）。

全量 **1043 条绿**。

## 6. 回滚

改回配置里的 `name` 即可（代码两种写法都认，所以回滚不会掉识别能力）：

```sh
cp /opt/dzmm/config/config.json.bak-20260927-125105 /opt/dzmm/config/config.json
cd /opt/dzmm && docker compose up -d --force-recreate dzmm-bot   # Bot 清单要重启才重读
```
