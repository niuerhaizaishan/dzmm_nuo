# tools/ —— 工具与探针

> 2026-10-01 归类（PLATFORM-OPTIMIZATION Phase 1 Step 1-2）。
> 原来 144 个文件平铺在一个目录里，找东西只能靠 grep；现在按**用途**分四类。

## 目录

```
tools/
├── active/     43 个  当前还在用的运维工具（改配置、结案、套参数）
├── probes/     82 个  探针/验证/分析/审计（取证用，随时可重跑）
├── archive/    73 个  一次性迁移与排查脚本（P8/P9/P12 的施工脚本 + .sh）
├── fixtures/    2 个  测试夹具（A/B 对照的基准文本）
└── legacy/            P8 之前的旧脚本
```

## active/ —— 现在还会用的

| 脚本 | 用途 |
|---|---|
| `set_chat_tuning.py` | 上班群那套"更像人"参数（装饰概率/排版/长度/冷却/双边） |
| `resolve_outbox.py` | 出站队列结案（`--apply` 写库，默认 dry-run） |
| `enable_quote_reply.py` | 给某个群打开"引用回复也接话" |
| `cfg_persona.py` | 改人设名（连带改所有自指） |
| `cfg_bots.py` / `cfg_scope.py` | Bot 注册表、按群集合 |
| `config_gen.py` | 由代码生成首份 config.json |
| `merge_json_into_store.py` | JSON 兜底文件对账回 SQLite |

## probes/ —— 取证用

- `probe_solicit.py` 收尾递话率（她 vs 真人）
- `probe_bd_ab.py` 上线 A/B（线上真实流量 + 离线生成对照）
- `probe_ctx_scope.py` 喂进模型的上下文范围
- `probe_session_profiles.py` 会话画像盘点（几个端握着登录态）
- `analyze_wall_windows.py` / `analyze_renew_windows.py` 续期坏窗口统计
- `audit_project.py` / `audit_deadcode.py` / `audit_hardcoded_name.py` 项目体检

## archive/ —— 已跑完的一次性脚本

`p8s4_*.py`（P8 第 4 步模块拆分，逐字比对保证行为不变）、
`p8_*.py`、`p9_*.py`、`p12*_check.py`（各期的验收脚本）、
以及 54 个 `.sh` 手工排查脚本。

**这些默认不会再跑**；留着是因为它们记录了"当时是怎么做的"（施工证据）。

## fixtures/ —— 测试夹具

- `CMD_PROMPT_pre_p12ap.md`（P12-AP 之前的提示词，A/B 用）
- `ruojin_extra_p12ah_before.txt`（P12-AH 之前的若瑾人设）

## ⚠️ 移动后要注意的两件事

1. **`ROOT` 的深度**：脚本里 `ROOT = Path(__file__).resolve().parent.parent.parent`
   是**三层**（因为多了一层子目录）。新加脚本照抄同目录的写法即可。
   ⚠️ 但 `archive/p8s4_move.py` 里 `header='''...'''` 那些**生成代码模板**
   仍是**两层**——那些模块生成到 `runtime/` 下，深度不同。别一起改。
2. **跨目录 import**：`probes/probe_bd_ab.py` 依赖同目录的 `probe_solicit.py`；
   `archive/p8s4_audit.py` 依赖同目录的 `p8s4_move.py`。别把它们拆到不同目录。

## 测试引用的路径

以下测试会按路径读 tools/ 里的文件，改目录时必须同步改：

| 测试 | 引用 |
|---|---|
| `tests/test_wall_evidence.py` | `tools/probes/analyze_wall_windows.py`（sys.path） |
| `tests/test_renew_hardening.py` | `tools/probes/probe_ua_wall.py` |
| `tests/test_renew_backoff_escalation.py` | `tools/probes/probe_session_layers.py` |
| `tests/test_persona_texts.py` | `tools/active/cfg_persona.py`（sys.path） |
| `tests/test_ruojin_persona.py` | `tools/fixtures/ruojin_extra_p12ah_before.txt` |
