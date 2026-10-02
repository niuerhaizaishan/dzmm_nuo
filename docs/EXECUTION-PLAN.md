# DDZM Bot 平台优化执行计划

> 基于 [PLATFORM-OPTIMIZATION.md](PLATFORM-OPTIMIZATION.md) 的详细实施计划  
> 创建时间：2026-10-01  
> 状态：待执行

---

## 目录

- [执行总览](#执行总览)
- [Phase 0: 即时清理](#phase-0-即时清理)
- [Phase 1: L1 整理](#phase-1-l1-整理)
- [Phase 2: S1 凭据挂目录](#phase-2-s1-凭据挂目录)
- [Phase 3: L2 bot.py 拆分](#phase-3-l2-botpy-拆分)
- [Phase 4: 运维后台重构](#phase-4-运维后台重构)
- [Phase 5: L3 架构重构](#phase-5-l3-架构重构)
- [测试策略](#测试策略)
- [部署策略](#部署策略)
- [回滚程序](#回滚程序)
- [风险评估](#风险评估)

---

## 执行总览

### 阶段概览

| 阶段 | 风险等级 | 预计耗时 | 依赖 | 核心目标 |
|---|---|---|---|---|
| Phase 0 | 🟢 零风险 | <1小时 | 无 | 清理环境，验证当前系统 |
| Phase 1 | 🟢 零风险 | 1-2小时 | Phase 0 | 文件整理，创建索引 |
| Phase 2 | 🟡 中风险 | 2-3天 | Phase 1 | 凭据文件目录化，解决EBUSY |
| Phase 3 | 🟡 中风险 | 4-6周 | Phase 2 | bot.py拆分，brains/personas分离 |
| Phase 4 | 🟡 中风险 | 4-6周 | Phase 3 | 运维后台现代化 |
| Phase 5 | 🔴 高风险 | 2-3月 | Phase 4 | 架构抽象，长期演进 |

### 执行顺序图

```
Phase 0 (即时清理)
    ↓
Phase 1 (L1整理)
    ↓
Phase 2 (S1凭据) ← 需要施工窗口
    ↓
Phase 3 Step 1 (_handle_joins抽出)
    ↓
Phase 3 Step 2 (_dispatch_slash抽出)
    ↓
Phase 3 Step 3 (_handle_room抽出)
    ↓
Phase 3 Step 4 (_handle_at_chat抽出)
    ↓
Phase 3 Step 5 (brains.py提示词外置)
    ↓
Phase 3 Step 6 (personas.py人设分离)
    ↓
Phase 4A (样式层升级)
    ↓
Phase 4B (布局重构)
    ↓
Phase 4C (渲染引擎)
    ↓
Phase 4D (API+实时)
    ↓
Phase 4E (编辑体验)
    ↓
Phase 4F (新功能按需)
    ↓
Phase 5 (架构重构 - 长期)
```

### 关键约束

1. **不停机原则**：Phase 0-4 所有操作必须保证线上服务不中断
2. **双写期**：Phase 2 凭据迁移必须经历至少一个完整续期周期（5分钟）的双写验证
3. **测试先行**：每个 Phase 必须有完整的 pytest 套件覆盖后才能部署
4. **回滚就绪**：每个 Phase 必须有明确的回滚步骤，回滚时间 < 5分钟
5. **灰度发布**：Phase 3-4 的改动优先在茶水间（最小群）验证，再推广到其他群

---

## Phase 0: 即时清理

> **目标**：清理环境，释放资源，验证当前系统健康状态  
> **风险**：🟢 零风险（纯清理操作，不涉及业务逻辑）  
> **预计耗时**：< 1小时  
> **依赖**：无

### Step 0-1: Build Cache 清理

**目标**：释放 Docker Build Cache 占用的 1GB 空间

**执行命令**：
```bash
# 查看当前 Build Cache 占用
docker system df

# 清理 Build Cache，保留 1.5GB（确保常用层不被清除）
docker builder prune -f --keep-storage 1.5GB

# 验证清理结果
docker system df
```

**验证标准**：
- Build Cache 占用从 1GB 降至 < 500MB
- 下次 deploy 仍能正常构建（缓存命中关键层）

**回滚**：无需回滚（清理操作不可逆，但下次构建会自动重建缓存）

**预计耗时**：5分钟

---

### Step 0-2: deploy.ps1 keep-storage 参数调整

**目标**：防止 Build Cache 再次膨胀到 1GB

**修改文件**：`deploy.ps1`

**修改内容**：
```powershell
# 找到 docker builder prune 命令（约第 390 行）
# 原代码：
docker builder prune -f --keep-storage 3GB

# 修改为：
docker builder prune -f --keep-storage 1.5GB
```

**验证标准**：
- `grep "keep-storage" deploy.ps1` 显示 `1.5GB`
- 下次 deploy 后 Build Cache 占用 < 1.5GB

**回滚**：
```powershell
# 如果 1.5GB 导致频繁重建，改回 2GB
sed -i 's/keep-storage 1.5GB/keep-storage 2GB/' deploy.ps1
```

**预计耗时**：2分钟

---

### Step 0-3: bot.log 轮转配置

**目标**：防止 bot.log 无限增长撑爆磁盘

**问题背景**：
- `bot.log` 是挂载文件，不受 Docker logging driver 管控
- 当前无轮转机制，长期运行后可能达到数 GB

**执行步骤**：

1. **创建 logrotate 配置文件**：
```bash
sudo tee /etc/logrotate.d/dzmm-bot << 'EOF'
/opt/dzmm/bot.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF
```

2. **验证配置**：
```bash
# 检查配置语法
sudo logrotate -d /etc/logrotate.d/dzmm-bot

# 手动执行一次轮转（测试用）
sudo logrotate -f /etc/logrotate.d/dzmm-bot
```

3. **确认轮转结果**：
```bash
ls -lh /opt/dzmm/bot.log*
# 应该看到 bot.log, bot.log.1, bot.log.2.gz 等
```

**验证标准**：
- `/etc/logrotate.d/dzmm-bot` 文件存在且语法正确
- 手动轮转后生成 `bot.log.1`
- `logrotate --version` 确认 logrotate 已安装

**回滚**：
```bash
sudo rm /etc/logrotate.d/dzmm-bot
# 恢复为无轮转状态
```

**预计耗时**：10分钟

---

### Step 0-4: Playwright Profile 缓存限制

**目标**：控制 pw_profile 目录膨胀（当前 114MB）

**问题背景**：
- Chromium 浏览器缓存无上限，长期运行后可能达到数 GB
- 缓存文件不影响业务逻辑，但占用磁盘空间

**修改文件**：
- `bot.py`（Playwright 启动参数）
- `captcha_novnc.py`（同上）

**修改内容**：

1. **bot.py 中添加 `--disk-cache-size` 参数**：
```python
# 找到 Playwright 启动代码（约第 1200 行）
# 在 launch_persistent_context 的 args 列表中添加：
args=[
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--no-sandbox",
    "--disk-cache-size=52428800",  # 新增：限制磁盘缓存为 50MB
    # ... 其他参数
]
```

2. **captcha_novnc.py 中同样添加**：
```python
# 找到 Playwright 启动代码（约第 118 行）
args=[
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-gpu",
    "--disk-cache-size=52428800",  # 新增
    # ... 其他参数
]
```

**验证标准**：
- `grep "disk-cache-size" bot.py captcha_novnc.py` 显示两处
- 部署后运行一段时间，`du -sh /opt/dzmm/pw_profile` 增长放缓

**回滚**：
```bash
# 移除 --disk-cache-size 参数
sed -i '/disk-cache-size/d' bot.py captcha_novnc.py
```

**预计耗时**：15分钟

---

### Step 0-5: 在线验证 ②③④ 功能

**目标**：闭环验证之前 12 轮施工的 ②③④ 功能是否正常

**验证项目**：

1. **② 双边上下文（P12-BE）**：
   - 在上班群发送 `@若瑾 你今天怎么样`
   - 检查日志：`docker logs dzmm-bot | grep "双边上下文"`
   - 预期：日志显示 "双边上下文已应用，排除了 X 条非双边消息"

2. **③ 回复冷却（P12-BQ）**：
   - 在上班群快速发送两条 `@若瑾 在吗`
   - 检查日志：`docker logs dzmm-bot | grep "冷却"`
   - 预期：第二条消息被冷却机制拦截，日志显示 "冷却中，跳过"

3. **④ 收尾兜底（P12-BQ）**：
   - 在上班群发送 `@若瑾 你觉得呢`（诱导递话）
   - 检查回复是否以陈述句结尾，而非反问句
   - 检查日志：`docker logs dzmm-bot | grep "solicit_guard"`
   - 预期：日志显示 "solicit_guard 触发，重写回复"

**执行方法**：

```bash
# 方法 1：等待自然流量（推荐，不影响线上）
# 观察 docker logs，等待 @若瑾 的消息，检查上述日志

# 方法 2：使用 probe_bd_ab.py 离线验证（不发送消息）
docker exec dzmm-bot python /app/probe_bd_ab.py

# 方法 3：手动触发（谨慎，会发送消息到线上）
# 不推荐在生产环境使用
```

**验证标准**：
- 三条日志关键词至少出现一次
- 回复内容符合预期（双边、非递话、无冷却冲突）

**回滚**：无需回滚（纯验证操作）

**预计耗时**：30分钟（等待自然流量）或 5分钟（离线验证）

---

### Phase 0 总结

**总耗时**：< 1小时

**验收检查清单**：
- [ ] Build Cache < 500MB
- [ ] deploy.ps1 keep-storage = 1.5GB
- [ ] logrotate 配置已生效
- [ ] Playwright disk-cache-size 已添加
- [ ] ②③④ 功能验证通过

**进入 Phase 1 的前提**：
- 所有 Step 的验证标准均已满足
- `pytest` 全量测试通过（确保清理操作未引入回归）

---

## Phase 1: L1 整理

> **目标**：整理文件结构，创建索引，提升可维护性  
> **风险**：🟢 零风险（纯文件移动，不涉及业务逻辑）  
> **预计耗时**：1-2小时  
> **依赖**：Phase 0 完成

### Step 1-1: 根目录清理

**目标**：将根目录下的非代码文件移动到合适的子目录

**当前状态**：
```
/opt/dzmm/
├── 发骚整理_*.md          # 小说/角色设定
├── 苏若曦.md              # 角色设定
├── 高冷校花*.md           # 小说
├── 公演小说.md            # 小说
├── profiles.json          # 用户档案数据
├── state.json             # 群状态数据
├── bot.log                # 日志
├── compose-p9.log         # 旧日志
├── config_audit.log       # 审计日志
└── ...
```

**目标状态**：
```
/opt/dzmm/
├── content/
│   ├── novels/            # 小说
│   │   ├── 发骚整理_*.md
│   │   ├── 苏若曦.md
│   │   ├── 高冷校花*.md
│   │   └── 公演小说.md
│   └── lore/              # 世界观/角色设定
├── data/                  # 数据文件
│   ├── profiles.json
│   ├── state.json
│   ├── outbox.db
│   ├── store.db
│   └── session_state.json
├── logs/                  # 日志文件
│   ├── bot.log
│   ├── compose-p9.log
│   └── config_audit.log
└── ...
```

**执行步骤**：

1. **创建目录结构**：
```bash
cd /opt/dzmm
mkdir -p content/novels content/lore logs
```

2. **移动小说/角色设定文件**：
```bash
mv 发骚整理_*.md content/novels/
mv 苏若曦.md content/novels/
mv 高冷校花*.md content/novels/
mv 公演小说.md content/novels/
```

3. **移动数据文件**（如果不在 data/ 下）：
```bash
# 检查当前状态
ls -la profiles.json state.json 2>/dev/null

# 如果存在，移动到 data/
if [ -f profiles.json ]; then mv profiles.json data/; fi
if [ -f state.json ]; then mv state.json data/; fi
```

4. **移动日志文件**：
```bash
mv bot.log logs/ 2>/dev/null || echo "bot.log 已在正确位置"
mv compose-p9.log logs/ 2>/dev/null || echo "compose-p9.log 不存在"
mv config_audit.log logs/ 2>/dev/null || echo "config_audit.log 不存在"
```

5. **更新 logrotate 配置**（如果 bot.log 移动了）：
```bash
# 如果 bot.log 从 /opt/dzmm/bot.log 移动到了 /opt/dzmm/logs/bot.log
sudo sed -i 's|/opt/dzmm/bot.log|/opt/dzmm/logs/bot.log|' /etc/logrotate.d/dzmm-bot
```

6. **更新代码中的路径引用**：
```bash
# 搜索代码中引用这些文件的位置
grep -r "profiles.json" --include="*.py" .
grep -r "state.json" --include="*.py" .
grep -r "bot.log" --include="*.py" .

# 根据搜索结果更新路径（如果有）
# 例如：profiles.py 中可能是 "./profiles.json" → "./data/profiles.json"
```

**验证标准**：
- `ls content/novels/` 显示所有小说文件
- `ls data/` 显示所有数据文件
- `ls logs/` 显示所有日志文件
- 根目录无残留的非代码文件（除了 .env, docker-compose.yml 等配置文件）
- `pytest` 全量测试通过（确保路径更新正确）

**回滚**：
```bash
# 将文件移回原位
mv content/novels/*.md .
mv data/profiles.json . 2>/dev/null
mv data/state.json . 2>/dev/null
mv logs/bot.log . 2>/dev/null
rmdir content/novels content/lore logs
rmdir content

# 恢复 logrotate 配置
sudo sed -i 's|/opt/dzmm/logs/bot.log|/opt/dzmm/bot.log|' /etc/logrotate.d/dzmm-bot
```

**预计耗时**：30分钟

---

### Step 1-2: tools/ 归档分类

**目标**：将 142 个工具脚本分类整理，提升可发现性

**当前状态**：
```
tools/
├── p8s4_*.py              # P8 迁移脚本（已完成）
├── p12y_*.py              # P12Y 验证脚本（已完成）
├── probe_*.py             # 探针脚本
├── verify_*.py            # 验证脚本
├── set_chat_tuning.py     # 当前可用
├── resolve_outbox.py      # 当前可用
├── probe_session_profiles.py  # 当前可用
├── probe_bd_ab.py         # 当前可用
├── enable_quote_reply.py  # 当前可用
├── cfg_*.py               # 配置工具
└── ... (共 142 个文件)
```

**目标状态**：
```
tools/
├── active/                # 当前可用的运维工具
│   ├── set_chat_tuning.py
│   ├── resolve_outbox.py
│   ├── probe_session_profiles.py
│   ├── probe_bd_ab.py
│   ├── enable_quote_reply.py
│   └── cfg_*.py
├── probes/                # 可复用的探针脚本
│   ├── probe_solicit.py
│   ├── probe_ctx_scope.py
│   ├── probe_quotes.py
│   └── ...
├── archive/               # 已完成的一次性脚本
│   ├── p8s4_*.py
│   ├── p12y_*.py
│   └── ...
└── fixtures/              # 测试夹具/快照
    └── CMD_PROMPT_pre_p12ap.md
```

**执行步骤**：

1. **创建目录结构**：
```bash
cd tools
mkdir -p active probes archive fixtures
```

2. **移动当前可用工具**：
```bash
mv set_chat_tuning.py active/
mv resolve_outbox.py active/
mv probe_session_profiles.py active/
mv probe_bd_ab.py active/
mv enable_quote_reply.py active/
mv cfg_*.py active/
```

3. **移动探针脚本**：
```bash
mv probe_solicit.py probes/
mv probe_ctx_scope.py probes/
mv probe_quotes.py probes/
# ... 其他 probe_*.py（除了已移到 active/ 的）
```

4. **移动归档脚本**：
```bash
mv p8s4_*.py archive/
mv p12y_*.py archive/
# ... 其他已完成的一次性脚本
```

5. **移动测试夹具**：
```bash
# 如果有测试用的 fixture 文件
mv CMD_PROMPT_pre_p12ap.md fixtures/ 2>/dev/null
```

6. **更新代码中的引用**：
```bash
# 搜索代码中引用 tools/ 下脚本的位置
grep -r "tools/" --include="*.py" .
grep -r "tools/" --include="*.md" .

# 根据搜索结果更新路径
# 例如：docs/ 中的文档可能引用了 tools/ 下的脚本
```

**验证标准**：
- `ls tools/active/` 显示当前可用工具
- `ls tools/probes/` 显示探针脚本
- `ls tools/archive/` 显示归档脚本
- `ls tools/fixtures/` 显示测试夹具
- 根 tools/ 目录无残留文件（除了 README.md）
- `pytest` 全量测试通过（确保路径更新正确）

**回滚**：
```bash
# 将所有文件移回 tools/ 根目录
mv active/* . 2>/dev/null
mv probes/* . 2>/dev/null
mv archive/* . 2>/dev/null
mv fixtures/* . 2>/dev/null
rmdir active probes archive fixtures
```

**预计耗时**：30分钟

---

### Step 1-3: docs/ 索引

**目标**：创建文档索引，提升文档可发现性

**当前状态**：
```
docs/
├── P12-AZ-账号长消息一条发.md
├── P12-BA-运势按性别称呼.md
├── P12-BB-茶水间@没反应-未定义名字.md
├── P12-BC-容器重建会吃掉正在处理的消息.md
├── P12-BD-若瑾更像人-方案.md
├── P12-BE-双边上下文.md
├── P12-BG-会话真死自动重登.md
├── P12-BI-风控墙与会话真死分开.md
├── P12-BJ-恢复收口与告警去噪.md
├── P12-BL-续期链再检查.md
├── P12-BM-茶水间引用触发-方案.md
├── P12-BN-男生被叫姐姐-方案.md
├── P12-BP-硬编码提示走话术表.md
├── P12-BQ-回复冷却与递话兜底.md
├── P12-BR-P3旧账施工清单.md
├── PLATFORM-OPTIMIZATION.md
├── ADMIN-OPTIMIZATION.md
└── ... (共 65 个文件)
```

**目标状态**：新增 `docs/INDEX.md` 索引文件

**执行步骤**：

1. **创建 INDEX.md**：
```bash
cat > docs/INDEX.md << 'EOF'
# DDZM Bot 文档索引

> 按主题分类的文档导航

---

## 架构与入门

- [平台完整优化方案](PLATFORM-OPTIMIZATION.md) — 六阶段优化路线图
- [后台优化方案](ADMIN-OPTIMIZATION.md) — 运维后台现代化详细方案
- [部署指南](DEPLOY.md) — 部署流程与回滚程序（待创建）

---

## 消息处理链路

### @ 回复链路

- [@回复链路汇总](P12-AP-AQ-AU-AZ-BD-BE-BQ-summary.md) — 合并摘要（待创建）
- [长消息一条发](P12-AZ-账号长消息一条发.md) — 解决长消息分两段问题
- [若瑾更像人](P12-BD-若瑾更像人-方案.md) — 递话率治理 + A/B 实测
- [双边上下文](P12-BE-双边上下文.md) — 排除非双边消息
- [回复冷却与递话兜底](P12-BQ-回复冷却与递话兜底.md) — 冷却机制 + solicit_guard

### 引用回复

- [茶水间引用触发](P12-BM-茶水间引用触发-方案.md) — quote_reply 功能实现

### 其他

- [茶水间@没反应](P12-BB-茶水间@没反应-未定义名字.md) — NameError 导致全群静默
- [容器重建吃消息](P12-BC-容器重建会吃掉正在处理的消息.md) — inflight 消息恢复

---

## 续期与会话

### 续期链路

- [续期链全景](P12-BL-续期链再检查.md) — S1-S9 隐患清单
- [密码重登](P12-BG-会话真死自动重登.md) — headless_relogin 实现
- [风控墙与会话真死分开](P12-BI-风控墙与会话真死分开.md) — 区分墙和真死
- [恢复收口与告警去噪](P12-BJ-恢复收口与告警去噪.md) — 告警窗口去噪

### 凭据管理

- [S1 凭据挂目录](PLATFORM-OPTIMIZATION.md#phase-2-s1-凭据挂目录) — 解决 EBUSY 问题

---

## 人设与话术

### 人设调整

- [运势按性别称呼](P12-BA-运势按性别称呼.md) — 性别识别 + 称呼调整
- [男生被叫姐姐](P12-BN-男生被叫姐姐-方案.md) — 修复性别识别错误
- [管家妹妹自称泄漏](P12-BR-P3旧账施工清单.md#问题-c管家妹妹自称泄漏) — weisi 人设修复

### 话术优化

- [硬编码提示走话术表](P12-BP-硬编码提示走话术表.md) — 移除 bot.py 中的硬编码提示

---

## 运维与施工

### 施工记录

- [P3 旧账施工清单](P12-BR-P3旧账施工清单.md) — ⑤⑥⑦ 落点 + 验收 + S1 五步法
- [BP/BQ/BE 上线记录](P12-BP-BQ-BE-上线记录.md) — 最近一批改动留档 + 零回归清单

### 执行计划

- [执行计划](EXECUTION-PLAN.md) — 本文档

---

## 附录

### 设计与规范

- [Design Token 速查](PLATFORM-OPTIMIZATION.md#附录-c-design-token-速查) — CSS 变量参考
- [组件使用示例](PLATFORM-OPTIMIZATION.md#附录-d-组件使用示例) — 15 种 UI 组件

### 测试与验证

- [测试策略](EXECUTION-PLAN.md#测试策略) — 本地 / 预发 / 生产测试流程
- [回滚程序](EXECUTION-PLAN.md#回滚程序) — 各阶段回滚步骤
EOF
```

2. **验证索引**：
```bash
# 检查 INDEX.md 中的所有链接是否有效
cd docs
grep -oE '\[.*?\]\(.*?\.md\)' INDEX.md | sed 's/.*(\(.*\))/\1/' | while read file; do
  if [ ! -f "$file" ]; then
    echo "❌ 链接失效: $file"
  else
    echo "✅ 链接有效: $file"
  fi
done
```

3. **更新 README.md**（如果有）：
```bash
# 在项目根目录的 README.md 中添加文档索引链接
echo "## 文档" >> ../README.md
echo "- [文档索引](docs/INDEX.md) — 按主题分类的文档导航" >> ../README.md
```

**验证标准**：
- `docs/INDEX.md` 文件存在
- 所有链接指向的文件均存在（或标记为"待创建"）
- 文档按主题分类，结构清晰

**回滚**：
```bash
# 删除 INDEX.md
rm docs/INDEX.md

# 从 README.md 中移除链接
sed -i '/文档索引/d' README.md
```

**预计耗时**：30分钟

---

### Phase 1 总结

**总耗时**：1-2小时

**验收检查清单**：
- [ ] 根目录无非代码文件
- [ ] tools/ 已分类（active/probes/archive/fixtures）
- [ ] docs/INDEX.md 已创建且链接有效
- [ ] `pytest` 全量测试通过
- [ ] 无残留的路径引用错误

**进入 Phase 2 的前提**：
- 所有 Step 的验证标准均已满足
- 确认 Phase 2 的施工窗口（需要至少 30 分钟的观察期）

---

## Phase 2: S1 凭据挂目录

> **目标**：解决凭据文件 EBUSY 问题，统一凭据管理  
> **风险**：🟡 中风险（涉及 Docker 配置和文件迁移）  
> **预计耗时**：2-3天（含观察期）  
> **依赖**：Phase 1 完成

### Step 2-1: 代码准备（env var fallback）

**目标**：修改代码，支持通过环境变量指定凭据文件路径

**修改文件**：
1. `token_store.py` (第 22 行)
2. `admin/dashboard.py` (第 101 行)
3. `monitor_recover.py` (第 22 行)
4. `switch_node.py` (第 21 行)

**修改内容**：

#### 1. token_store.py

```python
# 第 22 行附近
# 原代码：
TOKEN_FILE = Path(__file__).parent / ".dzmm_token.json"

# 修改为：
TOKEN_FILE = Path(
    os.environ.get("DZMM_TOKEN_FILE")
    or (Path(__file__).parent / ".dzmm_token.json")
)
```

#### 2. admin/dashboard.py

```python
# 第 101 行附近
# 原代码：
TOKEN_FILE = ROOT / ".dzmm_token.json"

# 修改为：
TOKEN_FILE = Path(
    os.environ.get("DZMM_TOKEN_FILE")
    or (ROOT / ".dzmm_token.json")
)
```

#### 3. monitor_recover.py

```python
# 第 22 行附近
# 原代码：
TOKEN_FILE = "/opt/dzmm/.dzmm_token.json"

# 修改为：
TOKEN_FILE = os.environ.get("DZMM_TOKEN_FILE", "/opt/dzmm/.dzmm_token.json")
```

#### 4. switch_node.py

```python
# 第 21 行附近
# 原代码：
TOKEN_FILE = "/opt/dzmm/.dzmm_token.json"

# 修改为：
TOKEN_FILE = os.environ.get("DZMM_TOKEN_FILE", "/opt/dzmm/.dzmm_token.json")
```

**验证标准**：
- `grep "DZMM_TOKEN_FILE" token_store.py admin/dashboard.py monitor_recover.py switch_node.py` 显示 4 处
- `pytest tests/test_token_store.py` 通过
- 不设置 `DZMM_TOKEN_FILE` 时，代码行为与修改前一致（零回归）

**回滚**：
```bash
# 移除环境变量 fallback，恢复硬编码路径
sed -i 's/Path(os.environ.get("DZMM_TOKEN_FILE") or/Path(/' token_store.py
sed -i 's/Path(os.environ.get("DZMM_TOKEN_FILE") or/Path(/' admin/dashboard.py
sed -i 's/os.environ.get("DZMM_TOKEN_FILE", "\/opt\/dzmm\/.dzmm_token.json")/"\/opt\/dzmm\/.dzmm_token.json"/' monitor_recover.py
sed -i 's/os.environ.get("DZMM_TOKEN_FILE", "\/opt\/dzmm\/.dzmm_token.json")/"\/opt\/dzmm\/.dzmm_token.json"/' switch_node.py
```

**预计耗时**：30分钟

---

### Step 2-2: compose 变更（双写期）

**目标**：修改 docker-compose.yml，添加 secrets 目录挂载，保留老挂载

**修改文件**：`docker-compose.prod.yml`

**修改内容**：

```yaml
# 在 volumes 部分添加 secrets 目录挂载
volumes:
  # 新增：secrets 目录（原子写）
  - ./secrets:/app/secrets
  # 保留：老挂载（双写期）
  - ./.dzmm_token.json:/app/.dzmm_token.json
  - ./.dzmm_login.json:/app/.dzmm_login.json
  # ... 其他挂载

# 在 environment 部分添加环境变量
environment:
  # 新增：指定新路径
  DZMM_TOKEN_FILE: /app/secrets/token.json
  DZMM_LOGIN_FILE: /app/secrets/login.json
  # ... 其他环境变量
```

**执行步骤**：

1. **备份当前 compose 文件**：
```bash
cp docker-compose.prod.yml docker-compose.prod.yml.backup-$(date +%Y%m%d-%H%M%S)
```

2. **编辑 compose 文件**：
```bash
# 使用 sed 或手动编辑
# 添加 volumes 和 environment（见上方修改内容）
```

3. **验证 compose 语法**：
```bash
docker-compose -f docker-compose.prod.yml config > /dev/null
echo "✅ compose 语法正确"
```

**验证标准**：
- `docker-compose.prod.yml` 包含 `./secrets:/app/secrets` 挂载
- `docker-compose.prod.yml` 包含 `DZMM_TOKEN_FILE: /app/secrets/token.json` 环境变量
- `docker-compose config` 无报错

**回滚**：
```bash
# 恢复备份
cp docker-compose.prod.yml.backup-* docker-compose.prod.yml
```

**预计耗时**：15分钟

---

### Step 2-3: 宿主迁移

**目标**：在宿主机创建 secrets 目录，迁移凭据文件

**执行步骤**：

1. **创建 secrets 目录**：
```bash
cd /opt/dzmm
mkdir -p secrets
```

2. **迁移凭据文件**：
```bash
# 复制（不删除原文件，保留双写）
cp -a .dzmm_token.json secrets/token.json
cp -a .dzmm_login.json secrets/login.json

# 设置正确的权限
chown 501:games secrets/token.json
chown 501:games secrets/login.json
chmod 600 secrets/token.json
chmod 600 secrets/login.json
```

3. **验证迁移结果**：
```bash
ls -lh secrets/
# 应该看到：
# -rw------- 1 501 games 1.2K Oct  1 10:00 token.json
# -rw------- 1 501 games 800  Oct  1 10:00 login.json

# 验证文件内容一致
diff .dzmm_token.json secrets/token.json
diff .dzmm_login.json secrets/login.json
# 应该无输出（文件完全一致）
```

**验证标准**：
- `secrets/` 目录存在
- `secrets/token.json` 和 `secrets/login.json` 存在且权限为 `600`
- 文件内容与原文件一致
- 文件属主为 `501:games`

**回滚**：
```bash
# 删除 secrets 目录
rm -rf /opt/dzmm/secrets
```

**预计耗时**：10分钟

---

### Step 2-4: 双写期部署与观察

**目标**：部署新配置，观察至少一个完整续期周期（5分钟）

**执行步骤**：

1. **部署新配置**：
```bash
# 使用 deploy.ps1 部署
powershell -File deploy.ps1

# 或使用 docker-compose 直接重启
docker-compose -f docker-compose.prod.yml up -d
```

2. **观察续期日志**：
```bash
# 实时观察续期日志
tail -f /opt/dzmm/logs/renew_token.log

# 等待至少一个完整续期周期（5分钟）
# 应该看到：
# - "续期成功，写入 /app/secrets/token.json"
# - 无 "原子替换失败" 错误
```

3. **验证双写**：
```bash
# 检查两个文件是否都被更新
ls -lh /opt/dzmm/.dzmm_token.json /opt/dzmm/secrets/token.json
# 两个文件的 mtime 应该接近（双写成功）

# 检查文件内容是否一致
diff /opt/dzmm/.dzmm_token.json /opt/dzmm/secrets/token.json
# 应该无输出（内容一致）
```

4. **检查 bot 日志**：
```bash
# 检查 bot 是否正常启动
docker logs dzmm-bot | tail -20

# 检查是否有错误
docker logs dzmm-bot | grep -i error
# 应该无错误
```

**验证标准**：
- 续期日志显示 "写入 /app/secrets/token.json"
- 无 "原子替换失败" 错误
- 两个凭据文件 mtime 接近，内容一致
- bot 日志无错误
- 至少观察一个完整续期周期（5分钟）

**回滚**：
```bash
# 如果出现问题，立即回滚
# 1. 恢复 compose 文件
cp docker-compose.prod.yml.backup-* docker-compose.prod.yml

# 2. 重启容器
docker-compose -f docker-compose.prod.yml up -d

# 3. 验证恢复
docker logs dzmm-bot | tail -20
tail -f /opt/dzmm/logs/renew_token.log
```

**预计耗时**：30分钟（含观察期）

---

### Step 2-5: 清理老挂载

**目标**：确认新挂载稳定后，移除老挂载和老文件

**前提条件**：
- Step 2-4 观察期无错误
- 至少 3 个续期周期（15分钟）无 "原子替换失败"

**执行步骤**：

1. **修改 compose 文件**：
```yaml
# 移除老挂载
volumes:
  # 保留：secrets 目录（原子写）
  - ./secrets:/app/secrets
  # 移除：老挂载
  # - ./.dzmm_token.json:/app/.dzmm_token.json  # 已移除
  # - ./.dzmm_login.json:/app/.dzmm_login.json  # 已移除
  # ... 其他挂载
```

2. **重启容器**：
```bash
docker-compose -f docker-compose.prod.yml up -d
```

3. **验证新挂载**：
```bash
# 检查续期日志
tail -f /opt/dzmm/logs/renew_token.log

# 应该看到：
# - "续期成功，写入 /app/secrets/token.json"
# - 无 "原子替换失败" 错误
```

4. **删除老文件**：
```bash
# 备份老文件（可选）
mv /opt/dzmm/.dzmm_token.json /opt/dzmm/.dzmm_token.json.old
mv /opt/dzmm/.dzmm_login.json /opt/dzmm/.dzmm_login.json.old

# 确认无问题后删除
rm /opt/dzmm/.dzmm_token.json.old
rm /opt/dzmm/.dzmm_login.json.old
```

5. **最终验证**：
```bash
# 检查续期日志（至少 2 个周期，10分钟）
tail -f /opt/dzmm/logs/renew_token.log

# 检查 bot 日志
docker logs dzmm-bot | tail -20

# 运行测试
docker exec dzmm-bot pytest tests/test_token_store.py
```

**验证标准**：
- 续期日志显示 "写入 /app/secrets/token.json"
- 无 "原子替换失败" 错误
- 老文件已删除
- bot 日志无错误
- `pytest tests/test_token_store.py` 通过

**回滚**：
```bash
# 如果出现问题，立即回滚
# 1. 恢复老文件
mv /opt/dzmm/.dzmm_token.json.old /opt/dzmm/.dzmm_token.json
mv /opt/dzmm/.dzmm_login.json.old /opt/dzmm/.dzmm_login.json

# 2. 恢复 compose 文件（添加回老挂载）
# 手动编辑 docker-compose.prod.yml，添加回老挂载

# 3. 重启容器
docker-compose -f docker-compose.prod.yml up -d
```

**预计耗时**：30分钟（含观察期）

---

### Phase 2 总结

**总耗时**：2-3天（含观察期）

**验收检查清单**：
- [ ] 代码支持 `DZMM_TOKEN_FILE` 环境变量
- [ ] `docker-compose.prod.yml` 包含 secrets 目录挂载
- [ ] `secrets/token.json` 和 `secrets/login.json` 存在且权限正确
- [ ] 双写期至少 15 分钟无错误
- [ ] 老挂载已移除，老文件已删除
- [ ] 续期日志无 "原子替换失败" 错误
- [ ] `pytest tests/test_token_store.py` 通过

**进入 Phase 3 的前提**：
- 所有 Step 的验证标准均已满足
- 至少 24 小时无续期相关错误

---

## Phase 3: L2 bot.py 拆分

> **目标**：拆分 bot.py，提升代码可维护性  
> **风险**：🟡 中风险（涉及核心业务逻辑重构）  
> **预计耗时**：4-6周（每步 1 周，含测试和验证）  
> **依赖**：Phase 2 完成

### Step 3-1: 抽出 `_handle_joins`

**目标**：将入群筛查和欢迎语逻辑从 bot.py 中抽出

**当前状态**：
- `bot.py` 中 `main_loop` 函数包含入群筛查和欢迎语逻辑（约 100 行）
- 逻辑与 `sender`、`group_state`、`_save_group` 等状态耦合

**目标状态**：
- 新增 `runtime/joins.py` 模块
- `_handle_joins` 函数接收必要的状态，返回更新后的状态
- `bot.py` 中 `main_loop` 调用 `_handle_joins`

**修改文件**：
1. 新增 `runtime/joins.py`
2. 修改 `bot.py`

**执行步骤**：

1. **分析当前逻辑**：
```bash
# 找到 bot.py 中入群筛查和欢迎语的代码
grep -n "join" bot.py | head -20
# 约在 1200-1300 行

# 阅读相关代码，理解状态依赖
vim bot.py +1200
```

2. **设计函数接口**：
```python
# runtime/joins.py
def _handle_joins(
    cid: str,
    joins: list[str],  # 新加入的用户 uid 列表
    sender: Any,  # sender 对象
    dry: bool,  # 是否 dry run
    group_state: dict,  # 当前群状态
) -> tuple[dict, bool]:
    """
    处理入群筛查和欢迎语
    
    Returns:
        (updated_group_state, handled)
        - updated_group_state: 更新后的群状态
        - handled: 是否处理了入群（用于日志）
    """
    ...
```

3. **实现函数**：
```python
# runtime/joins.py
def _handle_joins(cid, joins, sender, dry, group_state):
    # 将 bot.py 中的入群逻辑移到这里
    # 注意：不要直接修改 group_state，返回新的 dict
    ...
```

4. **修改 bot.py**：
```python
# bot.py
from runtime.joins import _handle_joins

def main_loop(...):
    ...
    # 原来的入群逻辑
    # if joins:
    #     ...
    
    # 替换为：
    if joins:
        group_state, handled = _handle_joins(cid, joins, sender, dry, group_state)
        if handled:
            log(f"处理了 {len(joins)} 个入群")
    ...
```

5. **编写测试**：
```python
# tests/test_joins.py
def test_handle_joins_basic():
    # 测试基本的入群处理
    ...

def test_handle_joins_dry_run():
    # 测试 dry run 模式
    ...

def test_handle_joins_state_update():
    # 测试状态更新
    ...
```

**验证标准**：
- `runtime/joins.py` 文件存在
- `_handle_joins` 函数接口清晰，状态依赖明确
- `bot.py` 中 `main_loop` 调用 `_handle_joins`
- `pytest tests/test_joins.py` 通过
- `pytest` 全量测试通过
- 部署后入群功能正常（手动验证）

**回滚**：
```bash
# 1. 恢复 bot.py（从 git）
git checkout bot.py

# 2. 删除 runtime/joins.py
rm runtime/joins.py

# 3. 删除测试
rm tests/test_joins.py

# 4. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：1周（3天实现 + 2天测试 + 2天验证）

---

### Step 3-2: 抽出 `_dispatch_slash`

**目标**：将斜杠指令分派逻辑从 bot.py 中抽出

**当前状态**：
- `bot.py` 中 `main_loop` 函数包含斜杠指令分派逻辑（约 300 行）
- 逻辑涉及 `/运势`、`/羞辱`、`/调教`、`/发骚`、`/投票`、`/剧情` 等多个指令

**目标状态**：
- 新增 `runtime/dispatch.py` 模块
- `_dispatch_slash` 函数接收指令和状态，返回回复内容
- `bot.py` 中 `main_loop` 调用 `_dispatch_slash`

**执行步骤**：

1. **分析当前逻辑**：
```bash
# 找到 bot.py 中斜杠指令分派的代码
grep -n "slash" bot.py | head -20
# 约在 1500-1800 行

# 阅读相关代码，理解指令列表和分派逻辑
vim bot.py +1500
```

2. **设计函数接口**：
```python
# runtime/dispatch.py
def _dispatch_slash(
    cid: str,
    cmd: str,  # 斜杠指令（如 "/运势"）
    cmd_sender: str,  # 发送者 uid
    sender: Any,  # sender 对象
    dry: bool,  # 是否 dry run
    group_state: dict,  # 当前群状态
) -> tuple[str, dict, bool]:
    """
    分派斜杠指令
    
    Returns:
        (reply, updated_group_state, handled)
        - reply: 回复内容（如果未处理则为空字符串）
        - updated_group_state: 更新后的群状态
        - handled: 是否处理了指令
    """
    ...
```

3. **实现函数**：
```python
# runtime/dispatch.py
def _dispatch_slash(cid, cmd, cmd_sender, sender, dry, group_state):
    # 将 bot.py 中的斜杠指令分派逻辑移到这里
    # 注意：不要直接修改 group_state，返回新的 dict
    ...
```

4. **修改 bot.py**：
```python
# bot.py
from runtime.dispatch import _dispatch_slash

def main_loop(...):
    ...
    # 原来的斜杠指令分派逻辑
    # if cmd.startswith("/"):
    #     if cmd == "/运势":
    #         ...
    
    # 替换为：
    if cmd.startswith("/"):
        reply, group_state, handled = _dispatch_slash(
            cid, cmd, cmd_sender, sender, dry, group_state
        )
        if handled:
            log(f"处理了斜杠指令: {cmd}")
    ...
```

5. **编写测试**：
```python
# tests/test_dispatch.py
def test_dispatch_slash_fortune():
    # 测试 /运势 指令
    ...

def test_dispatch_slash_insult():
    # 测试 /羞辱 指令
    ...

def test_dispatch_slash_unknown():
    # 测试未知指令
    ...
```

**验证标准**：
- `runtime/dispatch.py` 文件存在
- `_dispatch_slash` 函数接口清晰，支持所有斜杠指令
- `bot.py` 中 `main_loop` 调用 `_dispatch_slash`
- `pytest tests/test_dispatch.py` 通过
- `pytest` 全量测试通过
- 部署后所有斜杠指令正常（手动验证）

**回滚**：
```bash
# 1. 恢复 bot.py（从 git）
git checkout bot.py

# 2. 删除 runtime/dispatch.py
rm runtime/dispatch.py

# 3. 删除测试
rm tests/test_dispatch.py

# 4. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：1周（3天实现 + 2天测试 + 2天验证）

---

### Step 3-3: 抽出 `_handle_room`

**目标**：将房间消息处理逻辑从 bot.py 中抽出

**当前状态**：
- `bot.py` 中 `main_loop` 函数包含房间消息处理逻辑（约 250 行）
- 逻辑涉及管家回应、女鬼模式、布局切换、花瓣招待品等

**目标状态**：
- 新增 `runtime/room.py` 模块
- `_handle_room` 函数接收房间消息和状态，返回回复内容
- `bot.py` 中 `main_loop` 调用 `_handle_room`

**执行步骤**：

1. **分析当前逻辑**：
```bash
# 找到 bot.py 中房间消息处理的代码
grep -n "room" bot.py | head -20
# 约在 2000-2250 行

# 阅读相关代码，理解房间逻辑和状态依赖
vim bot.py +2000
```

2. **设计函数接口**：
```python
# runtime/room.py
def _handle_room(
    cid: str,
    room_hit: tuple[str, str],  # (sender_uid, text)
    msgs: list[dict],  # 当前批次的消息
    sender: Any,  # sender 对象
    dry: bool,  # 是否 dry run
    group_state: dict,  # 当前群状态
) -> tuple[str, dict, bool]:
    """
    处理房间消息
    
    Returns:
        (reply, updated_group_state, handled)
        - reply: 回复内容（如果未处理则为空字符串）
        - updated_group_state: 更新后的群状态
        - handled: 是否处理了消息
    """
    ...
```

3. **实现函数**：
```python
# runtime/room.py
def _handle_room(cid, room_hit, msgs, sender, dry, group_state):
    # 将 bot.py 中的房间消息处理逻辑移到这里
    # 注意：不要直接修改 group_state，返回新的 dict
    ...
```

4. **修改 bot.py**：
```python
# bot.py
from runtime.room import _handle_room

def main_loop(...):
    ...
    # 原来的房间消息处理逻辑
    # if room_hit:
    #     ...
    
    # 替换为：
    if room_hit:
        reply, group_state, handled = _handle_room(
            cid, room_hit, msgs, sender, dry, group_state
        )
        if handled:
            log(f"处理了房间消息: {room_hit[1][:20]}")
    ...
```

5. **编写测试**：
```python
# tests/test_room.py
def test_handle_room_butler():
    # 测试管家回应
    ...

def test_handle_room_ghost():
    # 测试女鬼模式
    ...

def test_handle_room_layout():
    # 测试布局切换
    ...
```

**验证标准**：
- `runtime/room.py` 文件存在
- `_handle_room` 函数接口清晰，支持所有房间功能
- `bot.py` 中 `main_loop` 调用 `_handle_room`
- `pytest tests/test_room.py` 通过
- `pytest` 全量测试通过
- 部署后房间功能正常（手动验证）

**回滚**：
```bash
# 1. 恢复 bot.py（从 git）
git checkout bot.py

# 2. 删除 runtime/room.py
rm runtime/room.py

# 3. 删除测试
rm tests/test_room.py

# 4. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：1周（3天实现 + 2天测试 + 2天验证）

---

### Step 3-4: 抽出 `_handle_at_chat`

**目标**：将 @ 闲聊全流程逻辑从 bot.py 中抽出

**当前状态**：
- `bot.py` 中 `main_loop` 函数包含 @ 闲聊全流程逻辑（约 200 行）
- 逻辑涉及上下文构建、记忆注入、生成回复、装饰、冷却、兜底、发送等

**目标状态**：
- 新增 `runtime/at_chat.py` 模块
- `_handle_at_chat` 函数接收 @ 消息和状态，返回回复内容
- `bot.py` 中 `main_loop` 调用 `_handle_at_chat`

**执行步骤**：

1. **分析当前逻辑**：
```bash
# 找到 bot.py 中 @ 闲聊全流程的代码
grep -n "at_chat" bot.py | head -20
# 约在 2500-2700 行

# 阅读相关代码，理解全流程和状态依赖
vim bot.py +2500
```

2. **设计函数接口**：
```python
# runtime/at_chat.py
def _handle_at_chat(
    cid: str,
    at_msg: str,  # @ 消息内容
    at_sender: str,  # 发送者 uid
    sender: Any,  # sender 对象
    dry: bool,  # 是否 dry run
    group_state: dict,  # 当前群状态
) -> tuple[str, dict, bool]:
    """
    处理 @ 闲聊全流程
    
    Returns:
        (reply, updated_group_state, handled)
        - reply: 回复内容（如果未处理则为空字符串）
        - updated_group_state: 更新后的群状态
        - handled: 是否处理了消息
    """
    ...
```

3. **实现函数**：
```python
# runtime/at_chat.py
def _handle_at_chat(cid, at_msg, at_sender, sender, dry, group_state):
    # 将 bot.py 中的 @ 闲聊全流程逻辑移到这里
    # 包括：上下文构建、记忆注入、生成回复、装饰、冷却、兜底、发送
    # 注意：不要直接修改 group_state，返回新的 dict
    ...
```

4. **修改 bot.py**：
```python
# bot.py
from runtime.at_chat import _handle_at_chat

def main_loop(...):
    ...
    # 原来的 @ 闲聊全流程逻辑
    # if at_msg:
    #     ...
    
    # 替换为：
    if at_msg:
        reply, group_state, handled = _handle_at_chat(
            cid, at_msg, at_sender, sender, dry, group_state
        )
        if handled:
            log(f"处理了 @ 消息: {at_msg[:20]}")
    ...
```

5. **编写测试**：
```python
# tests/test_at_chat.py
def test_handle_at_chat_basic():
    # 测试基本的 @ 闲聊
    ...

def test_handle_at_chat_context():
    # 测试上下文构建
    ...

def test_handle_at_chat_memory():
    # 测试记忆注入
    ...

def test_handle_at_chat_cooldown():
    # 测试冷却机制
    ...

def test_handle_at_chat_solicit_guard():
    # 测试 solicit_guard
    ...
```

**验证标准**：
- `runtime/at_chat.py` 文件存在
- `_handle_at_chat` 函数接口清晰，包含全流程
- `bot.py` 中 `main_loop` 调用 `_handle_at_chat`
- `pytest tests/test_at_chat.py` 通过
- `pytest` 全量测试通过
- 部署后 @ 闲聊功能正常（手动验证）

**回滚**：
```bash
# 1. 恢复 bot.py（从 git）
git checkout bot.py

# 2. 删除 runtime/at_chat.py
rm runtime/at_chat.py

# 3. 删除测试
rm tests/test_at_chat.py

# 4. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：1周（3天实现 + 2天测试 + 2天验证）

---

### Step 3-5: brains.py 提示词外置

**目标**：将 brains.py 中的提示词默认值外置到 prompts/ 目录

**当前状态**：
- `brains.py` 中包含多个 `_p("KEY", "...")` 调用，默认值直接写在代码中
- 部分提示词已有 `prompts/KEY.md` 文件，但未完全覆盖

**目标状态**：
- 所有 `_p("KEY", "...")` 的默认值提取到 `prompts/KEY.md`
- `brains.py` 只保留 `_p()` loader 和 `generate_*()` 函数
- 测试钉住 byte-identical

**修改文件**：
1. 新增 `prompts/*.md`（多个文件）
2. 修改 `brains.py`

**执行步骤**：

1. **列出所有 `_p()` 调用**：
```bash
# 找到 brains.py 中所有 _p() 调用
grep -n "_p(" brains.py

# 输出类似：
# 100:CMD_PROMPT = _p("CMD_PROMPT", """...""")
# 150:INSULT_PROMPT = _p("INSULT_PROMPT", """...""")
# ...
```

2. **提取默认值到 prompts/**：
```bash
# 对于每个 _p("KEY", "...") 调用：
# 1. 提取默认值内容
# 2. 写入 prompts/KEY.md
# 3. 修改 brains.py 中的调用为 _p("KEY")

# 示例：
cat > prompts/CMD_PROMPT.md << 'EOF'
# CMD_PROMPT

（原 brains.py 中的默认值内容）
EOF

# 修改 brains.py：
# CMD_PROMPT = _p("CMD_PROMPT")  # 不再传默认值
```

3. **编写 byte-identical 测试**：
```python
# tests/test_prompts_byte_identical.py
def test_cmd_prompt_byte_identical():
    # 验证 prompts/CMD_PROMPT.md 与原默认值 byte-identical
    with open("prompts/CMD_PROMPT.md") as f:
        new = f.read()
    
    # 原默认值（从 git history 中获取）
    original = """..."""
    
    assert new == original, "prompts/CMD_PROMPT.md 与原默认值不一致"
```

**验证标准**：
- 所有 `_p("KEY", "...")` 调用已提取到 `prompts/KEY.md`
- `brains.py` 中 `_p()` 调用不再传默认值
- `pytest tests/test_prompts_byte_identical.py` 通过
- `pytest` 全量测试通过
- 部署后所有提示词功能正常（手动验证）

**回滚**：
```bash
# 1. 恢复 brains.py（从 git）
git checkout brains.py

# 2. 删除 prompts/*.md（保留原有的）
# 注意：不要删除原有的 prompts/ 文件

# 3. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：1周（3天提取 + 2天测试 + 2天验证）

---

### Step 3-6: personas.py 人设分离

**目标**：将 personas.py 按人设拆分为多个文件

**当前状态**：
- `personas.py` 包含 `PERSONAS`、`PHRASES`、`PHRASE_LABELS` 等数据结构
- 所有人设（nuo、ruojin、weisi）混在一个文件中

**目标状态**：
```
personas/
├── __init__.py      # fill/p/persona_for/phrases_for
├── registry.py      # PERSONAS/PHRASES/PHRASE_LABELS 汇总
├── nuo.py           # 小小糯 extra + phrases
├── ruojin.py        # 若瑾 extra + phrases
├── weisi.py         # 薇丝 extra + phrases
└── defaults.py      # 默认话术 (PHRASES["nuo"])
```

**修改文件**：
1. 新增 `personas/` 目录及多个文件
2. 修改 `bot.py` 等引用 `personas` 的文件

**执行步骤**：

1. **创建 personas/ 目录**：
```bash
mkdir personas
```

2. **拆分 personas.py**：
```python
# personas/__init__.py
from .registry import PERSONAS, PHRASES, PHRASE_LABELS
from .core import fill, p, persona_for, phrases_for

# personas/registry.py
from .nuo import NUO_PERSONA, NUO_PHRASES
from .ruojin import RUOJIN_PERSONA, RUOJIN_PHRASES
from .weisi import WEISI_PERSONA, WEISI_PHRASES
from .defaults import DEFAULT_PHRASES

PERSONAS = {
    "nuo": NUO_PERSONA,
    "ruojin": RUOJIN_PERSONA,
    "weisi": WEISI_PERSONA,
}

PHRASES = {
    "nuo": NUO_PHRASES,
    "ruojin": RUOJIN_PHRASES,
    "weisi": WEISI_PHRASES,
}

PHRASE_LABELS = {...}  # 从原 personas.py 中提取

# personas/nuo.py
NUO_PERSONA = {...}  # 从原 personas.py 中提取
NUO_PHRASES = {...}  # 从原 personas.py 中提取

# personas/ruojin.py
RUOJIN_PERSONA = {...}
RUOJIN_PHRASES = {...}

# personas/weisi.py
WEISI_PERSONA = {...}
WEISI_PHRASES = {...}

# personas/defaults.py
DEFAULT_PHRASES = {...}  # PHRASES["nuo"] 的默认值

# personas/core.py
def fill(text, cid): ...
def p(key, cid): ...
def persona_for(cid): ...
def phrases_for(key): ...
```

3. **删除原 personas.py**：
```bash
rm personas.py
```

4. **更新引用**：
```bash
# 搜索所有引用 personas 的文件
grep -r "import personas" --include="*.py" .
grep -r "from personas" --include="*.py" .

# 更新引用（大多数应该不需要改，因为 personas/__init__.py 导出了相同的接口）
```

5. **编写测试**：
```python
# tests/test_personas_split.py
def test_personas_import():
    # 验证 personas 模块可以正常导入
    from personas import PERSONAS, PHRASES, PHRASE_LABELS
    assert "nuo" in PERSONAS
    assert "ruojin" in PERSONAS
    assert "weisi" in PERSONAS

def test_personas_interface():
    # 验证接口与原来一致
    from personas import fill, p, persona_for, phrases_for
    assert callable(fill)
    assert callable(p)
    assert callable(persona_for)
    assert callable(phrases_for)
```

**验证标准**：
- `personas/` 目录存在，包含所有拆分文件
- 原 `personas.py` 已删除
- `pytest tests/test_personas_split.py` 通过
- `pytest` 全量测试通过
- 部署后所有人设功能正常（手动验证）

**回滚**：
```bash
# 1. 恢复 personas.py（从 git）
git checkout personas.py

# 2. 删除 personas/ 目录
rm -rf personas/

# 3. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：1周（3天拆分 + 2天测试 + 2天验证）

---

### Phase 3 总结

**总耗时**：4-6周

**验收检查清单**：
- [ ] `bot.py` 行数从 4908 降至 < 1500
- [ ] `runtime/joins.py`、`runtime/dispatch.py`、`runtime/room.py`、`runtime/at_chat.py` 存在
- [ ] `brains.py` 中 `_p()` 调用不再传默认值
- [ ] `prompts/*.md` 包含所有提示词默认值
- [ ] `personas/` 目录存在，包含所有拆分文件
- [ ] 所有新增测试通过
- [ ] `pytest` 全量测试通过
- [ ] 部署后所有功能正常（手动验证）

**进入 Phase 4 的前提**：
- 所有 Step 的验证标准均已满足
- 至少 1 周无相关错误

---

## Phase 4: 运维后台重构

> **目标**：现代化运维后台，提升用户体验  
> **风险**：🟡 中风险（涉及前端重构）  
> **预计耗时**：4-6周  
> **依赖**：Phase 3 完成

### Step 4A: 样式层升级

**目标**：将运维后台的 CSS 升级到新的设计系统

**当前状态**：
- 5 个 UI 文件（`dashboard.py`、`config_ui.py`、`features_ui.py`、`personas_ui.py`、`prompts_ui.py`）各自包含独立的 `_CSS` 变量
- CSS 已漂移，样式不一致

**目标状态**：
- 所有 UI 文件使用共享的 CSS（`base.css`、`components.css`、`dashboard.css`）
- 删除各 UI 文件中的 `_CSS` 变量

**修改文件**：
1. 修改 `dashboard.py`、`config_ui.py`、`features_ui.py`、`personas_ui.py`、`prompts_ui.py`

**执行步骤**：

1. **注入新 CSS**：
```python
# dashboard.py
# 在 HTML 头部添加：
<link rel="stylesheet" href="/static/base.css">
<link rel="stylesheet" href="/static/components.css">
<link rel="stylesheet" href="/static/dashboard.css">
```

2. **删除旧 CSS**：
```python
# dashboard.py
# 删除 _CSS 变量
# _CSS = """..."""  # 删除这行
```

3. **重复以上步骤**：
```python
# config_ui.py、features_ui.py、personas_ui.py、prompts_ui.py
# 同样注入新 CSS，删除旧 _CSS
```

4. **验证样式**：
```bash
# 部署后访问运维后台，检查样式是否正确
# 检查亮/暗主题切换是否正常
```

**验证标准**：
- 所有 UI 文件不再包含 `_CSS` 变量
- 运维后台样式正确，亮/暗主题切换正常
- `pytest` 全量测试通过

**回滚**：
```bash
# 1. 恢复所有 UI 文件（从 git）
git checkout admin/*.py

# 2. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：1天

---

### Step 4B: 布局重构

**目标**：重构运维后台布局，提升用户体验

**当前状态**：
- 顶部一行链接导航
- 10 个 section 平铺

**目标状态**：
- 侧边栏导航（移动端汉堡菜单）
- 首页分层仪表盘（告警 → stat → 表格 → 折叠）

**修改文件**：
1. 修改 `dashboard.py`

**执行步骤**：

1. **实现侧边栏导航**：
```python
# dashboard.py
# 修改 HTML 结构，添加侧边栏
<aside class="sidebar">
  <nav>
    <a href="/">概览</a>
    <a href="/config">配置</a>
    <a href="/features">功能</a>
    ...
  </nav>
</aside>
```

2. **实现首页分层仪表盘**：
```python
# dashboard.py
# 修改首页布局
<div class="dashboard">
  <section class="alerts">...</section>
  <section class="stats">...</section>
  <section class="tables">...</section>
  <section class="collapsible">...</section>
</div>
```

3. **验证布局**：
```bash
# 部署后访问运维后台，检查布局是否正确
# 检查移动端响应式是否正常
```

**验证标准**：
- 侧边栏导航正常，移动端汉堡菜单正常
- 首页分层仪表盘正常
- 移动端响应式正常
- `pytest` 全量测试通过

**回滚**：
```bash
# 1. 恢复 dashboard.py（从 git）
git checkout admin/dashboard.py

# 2. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：1-2天

---

### Step 4C: 渲染引擎重构

**目标**：重构渲染引擎，提升代码可维护性

**当前状态**：
- `dashboard.py` 2091 行，包含 HTML 渲染逻辑
- 渲染逻辑与数据采集逻辑混杂

**目标状态**：
- 新增 `admin/render.py` 模块，包含组件函数库
- `dashboard.py` 瘦身至 ~800 行
- section 注册制

**修改文件**：
1. 新增 `admin/render.py`
2. 修改 `dashboard.py`

**执行步骤**：

1. **创建组件函数库**：
```python
# admin/render.py
def badge(text: str, level: str = "info") -> str:
    ...

def card(title: str, body: str) -> str:
    ...

def table(headers: list, rows: list) -> str:
    ...

def msg(text: str, level: str = "info") -> str:
    ...
```

2. **修改 dashboard.py**：
```python
# dashboard.py
from admin.render import badge, card, table, msg

# 使用组件函数替换原来的 HTML 字符串拼接
```

3. **实现 section 注册制**：
```python
# dashboard.py
SECTIONS = [
    ("alerts", render_alerts),
    ("stats", render_stats),
    ...
]

def render_home():
    for name, render_func in SECTIONS:
        html += render_func()
```

**验证标准**：
- `admin/render.py` 存在，包含所有组件函数
- `dashboard.py` 行数从 2091 降至 ~800
- 运维后台渲染正常
- `pytest` 全量测试通过

**回滚**：
```bash
# 1. 恢复 dashboard.py（从 git）
git checkout admin/dashboard.py

# 2. 删除 admin/render.py
rm admin/render.py

# 3. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：2-3天

---

### Step 4D: API + 实时数据

**目标**：拆分轻量 API 端点，实现 30s 自动刷新

**当前状态**：
- 只有一个 `/api/summary` 端点，返回全量数据（~50KB）
- 无自动刷新

**目标状态**：
- 新增 `/api/status`、`/api/groups`、`/api/outbox`、`/api/errors`、`/api/audit` 端点
- 30s 自动刷新 stat 卡片

**修改文件**：
1. 修改 `dashboard.py`
2. 修改 `admin/static/app.js`

**执行步骤**：

1. **拆分 API 端点**：
```python
# dashboard.py
@app.route("/api/status")
def api_status():
    # 返回轻量状态数据（<1KB）
    return jsonify({...})

@app.route("/api/groups")
def api_groups():
    # 返回群列表
    return jsonify({...})

@app.route("/api/outbox")
def api_outbox():
    # 返回出站队列（支持 ?status=failed&limit=50）
    return jsonify({...})

@app.route("/api/errors")
def api_errors():
    # 返回错误日志
    return jsonify({...})

@app.route("/api/audit")
def api_audit():
    # 返回审计日志（支持 ?since=2026-09-30）
    return jsonify({...})
```

2. **实现 30s 自动刷新**：
```javascript
// admin/static/app.js
setInterval(() => {
  fetch("/api/status")
    .then(res => res.json())
    .then(data => {
      // 更新 stat 卡片
      updateStatCards(data);
    });
}, 30000);
```

**验证标准**：
- 所有新 API 端点正常
- 30s 自动刷新正常
- `pytest` 全量测试通过

**回滚**：
```bash
# 1. 恢复 dashboard.py 和 app.js（从 git）
git checkout admin/dashboard.py admin/static/app.js

# 2. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：2天

---

### Step 4E: 编辑体验优化

**目标**：优化编辑页体验，提升用户效率

**当前状态**：
- 编辑页整页刷新回顶部
- 无 diff 预览
- 无保存锚点

**目标状态**：
- diff 预览（保存前显示变更）
- 保存锚点（保存后停留在原位置）
- Toast 通知（保存成功/失败提示）

**修改文件**：
1. 修改 `config_ui.py`、`features_ui.py`、`personas_ui.py`、`prompts_ui.py`
2. 修改 `admin/static/app.js`

**执行步骤**：

1. **实现 diff 预览**：
```javascript
// admin/static/app.js
function showDiffPreview() {
  // 比较当前值与原始值，显示 diff
  ...
}
```

2. **实现保存锚点**：
```javascript
// admin/static/app.js
function saveWithAnchor() {
  // 记录当前位置，保存后回到原位置
  const anchor = document.querySelector(":focus");
  // 提交表单
  // 保存后回到 anchor
}
```

3. **实现 Toast 通知**：
```javascript
// admin/static/app.js
function showToast(message, level = "info") {
  // 显示 Toast 通知
  ...
}
```

**验证标准**：
- diff 预览正常
- 保存锚点正常
- Toast 通知正常
- `pytest` 全量测试通过

**回滚**：
```bash
# 1. 恢复所有 UI 文件和 app.js（从 git）
git checkout admin/*.py admin/static/app.js

# 2. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：2天

---

### Step 4F: 新功能（按需）

**目标**：根据需求添加新功能

**可选功能**：
- 日志查看器（/logs）
- 审计可视化（/audit）
- 群详情（/groups/{cid}）
- 在线测试台（/test）
- 导出/导入配置

**执行步骤**：
- 根据需求优先级，逐个实现
- 每个功能独立开发和测试

**预计耗时**：按需

---

### Phase 4 总结

**总耗时**：4-6周

**验收检查清单**：
- [ ] 所有 UI 文件使用共享 CSS
- [ ] 侧边栏导航正常
- [ ] 首页分层仪表盘正常
- [ ] `dashboard.py` 行数从 2091 降至 ~800
- [ ] 所有新 API 端点正常
- [ ] 30s 自动刷新正常
- [ ] diff 预览、保存锚点、Toast 通知正常
- [ ] `pytest` 全量测试通过

**进入 Phase 5 的前提**：
- 所有 Step 的验证标准均已满足
- Phase 5 为长期演进，无硬性前提

---

## Phase 5: L3 架构重构

> **目标**：架构抽象，长期演进  
> **风险**：🔴 高风险（涉及架构级重构）  
> **预计耗时**：2-3月  
> **依赖**：Phase 4 完成

### Step 5-1: 消息管道抽象

**目标**：抽象消息处理流程，提升可扩展性

**当前状态**：
- `bot.py` 中 `main_loop` 函数包含所有消息处理逻辑
- 新增指令需要修改 `main_loop`

**目标状态**：
```python
class MessagePipeline:
    filters: list[Filter]
    routers: list[Router]
    post_processors: list[PostProcessor]
    
    async def process(self, msg: InboundMessage) -> OutboundMessage | None:
        for filter in self.filters:
            if not filter.accept(msg):
                return None
        
        for router in self.routers:
            if router.match(msg):
                reply = await router.handle(msg)
                
                for pp in self.post_processors:
                    reply = pp.process(reply)
                
                return reply
        
        return None
```

**修改文件**：
1. 新增 `runtime/pipeline.py`
2. 修改 `bot.py`

**执行步骤**：

1. **设计 Filter/Router/PostProcessor 接口**：
```python
# runtime/pipeline.py
class Filter(Protocol):
    def accept(self, msg: InboundMessage) -> bool: ...

class Router(Protocol):
    def match(self, msg: InboundMessage) -> bool: ...
    async def handle(self, msg: InboundMessage) -> OutboundMessage: ...

class PostProcessor(Protocol):
    def process(self, reply: OutboundMessage) -> OutboundMessage: ...
```

2. **实现 MessagePipeline**：
```python
# runtime/pipeline.py
class MessagePipeline:
    def __init__(self):
        self.filters = []
        self.routers = []
        self.post_processors = []
    
    def add_filter(self, filter: Filter): ...
    def add_router(self, router: Router): ...
    def add_post_processor(self, pp: PostProcessor): ...
    
    async def process(self, msg: InboundMessage) -> OutboundMessage | None: ...
```

3. **修改 bot.py**：
```python
# bot.py
from runtime.pipeline import MessagePipeline

pipeline = MessagePipeline()

# 注册 filters
pipeline.add_filter(AtOnlyFilter())
pipeline.add_filter(QuoteReplyFilter())
pipeline.add_filter(SlashCmdsFilter())
pipeline.add_filter(CooldownFilter())

# 注册 routers
pipeline.add_router(FortuneRouter())
pipeline.add_router(InsultRouter())
pipeline.add_router(ChatRouter())
pipeline.add_router(RoomRouter())
pipeline.add_router(RecapRouter())

# 注册 post_processors
pipeline.add_post_processor(DecorPostProcessor())
pipeline.add_post_processor(HumanizePostProcessor())
pipeline.add_post_processor(SolicitGuardPostProcessor())
pipeline.add_post_processor(SpicePostProcessor())

def main_loop(...):
    ...
    reply = await pipeline.process(msg)
    ...
```

**验证标准**：
- `runtime/pipeline.py` 存在，接口清晰
- `bot.py` 中 `main_loop` 使用 `MessagePipeline`
- 新增指令只需添加 Router，无需修改 `main_loop`
- `pytest` 全量测试通过
- 部署后所有功能正常

**回滚**：
```bash
# 1. 恢复 bot.py（从 git）
git checkout bot.py

# 2. 删除 runtime/pipeline.py
rm runtime/pipeline.py

# 3. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：2-3周

---

### Step 5-2: 统一状态存储接口

**目标**：统一状态存储接口，提升可维护性

**当前状态**：
- 状态存储分散在 `state.json`、`data/store.db`、`data/outbox.db` 等多个位置
- 无统一的存储接口

**目标状态**：
```python
class StateStore(Protocol):
    def get_turns(self, cid: str, n: int) -> list[Turn]: ...
    def remember_turn(self, cid: str, turn: Turn): ...
    def get_group_state(self, cid: str) -> dict: ...
    def save_group_state(self, cid: str, state: dict): ...
    def get_memory(self, cid: str, query: str, n: int) -> list[MemoryEntry]: ...
    def get_profile(self, uid: str) -> dict | None: ...
    def upsert_profile(self, uid: str, data: dict): ...
    def enqueue(self, outbound: OutboundMessage): ...
    def mark_sent(self, id: str, platform_id: str): ...
    def mark_failed(self, id: str, reason: str): ...
    def failed_count(self) -> int: ...
```

**修改文件**：
1. 新增 `runtime/state_store.py`
2. 修改所有使用状态存储的文件

**执行步骤**：

1. **设计 StateStore 接口**：
```python
# runtime/state_store.py
class StateStore(Protocol):
    ...
```

2. **实现 StateStore**：
```python
# runtime/state_store.py
class SQLiteStateStore(StateStore):
    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path)
    
    def get_turns(self, cid, n): ...
    def remember_turn(self, cid, turn): ...
    ...
```

3. **修改所有使用状态存储的文件**：
```python
# 将所有直接读写 state.json、store.db、outbox.db 的代码
# 替换为使用 StateStore 接口
```

**验证标准**：
- `runtime/state_store.py` 存在，接口清晰
- 所有状态存储通过 `StateStore` 接口
- `pytest` 全量测试通过
- 部署后所有功能正常

**回滚**：
```bash
# 1. 恢复所有修改的文件（从 git）
git checkout .

# 2. 删除 runtime/state_store.py
rm runtime/state_store.py

# 3. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：3-4周

---

### Step 5-3: 发送通道抽象

**目标**：抽象发送通道，提升可扩展性

**当前状态**：
- 发送逻辑分散在 `bot.py`、`outbox.py` 等文件中
- 无统一的发送接口

**目标状态**：
```python
class SendChannel(Protocol):
    async def send(self, text: str, cid: str) -> bool: ...

class AccountChannel(SendChannel):
    # typing + socket + split
    ...

class BotApiChannel(SendChannel):
    # POST /bot/message
    ...

class RoutingSender(SendChannel):
    # 按 cid 路由到 AccountChannel 或 BotApiChannel
    ...
```

**修改文件**：
1. 新增 `runtime/send_channel.py`
2. 修改所有使用发送逻辑的文件

**执行步骤**：

1. **设计 SendChannel 接口**：
```python
# runtime/send_channel.py
class SendChannel(Protocol):
    async def send(self, text: str, cid: str) -> bool: ...
```

2. **实现各种 SendChannel**：
```python
# runtime/send_channel.py
class AccountChannel(SendChannel): ...
class BotApiChannel(SendChannel): ...
class RoutingSender(SendChannel): ...
```

3. **修改所有使用发送逻辑的文件**：
```python
# 将所有直接发送消息的代码
# 替换为使用 SendChannel 接口
```

**验证标准**：
- `runtime/send_channel.py` 存在，接口清晰
- 所有发送通过 `SendChannel` 接口
- `pytest` 全量测试通过
- 部署后所有功能正常

**回滚**：
```bash
# 1. 恢复所有修改的文件（从 git）
git checkout .

# 2. 删除 runtime/send_channel.py
rm runtime/send_channel.py

# 3. 重新部署
powershell -File deploy.ps1
```

**预计耗时**：2-3周

---

### Phase 5 总结

**总耗时**：2-3月

**验收检查清单**：
- [ ] `runtime/pipeline.py` 存在，接口清晰
- [ ] `runtime/state_store.py` 存在，接口清晰
- [ ] `runtime/send_channel.py` 存在，接口清晰
- [ ] 所有消息处理通过 `MessagePipeline`
- [ ] 所有状态存储通过 `StateStore`
- [ ] 所有发送通过 `SendChannel`
- [ ] 新增指令只需添加 Router
- [ ] `pytest` 全量测试通过
- [ ] 部署后所有功能正常

---

## 测试策略

### 测试层次

```
单元测试（pytest）
    ↓
集成测试（pytest + mock）
    ↓
端到端测试（本地 Docker 环境）
    ↓
预发环境测试（staging server）
    ↓
生产环境测试（production server，灰度发布）
```

### 测试要求

| Phase | 单元测试 | 集成测试 | 端到端测试 | 预发测试 | 生产测试 |
|---|---|---|---|---|---|
| Phase 0 | 不需要 | 不需要 | 不需要 | 不需要 | ✅ 全量 pytest |
| Phase 1 | ✅ 路径测试 | 不需要 | 不需要 | 不需要 | ✅ 全量 pytest |
| Phase 2 | ✅ token_store | ✅ 双写验证 | ✅ 续期观察 | 不需要 | ✅ 24h 观察 |
| Phase 3 | ✅ 每个模块 | ✅ 接口测试 | ✅ 功能验证 | ✅ 灰度发布 | ✅ 1 周观察 |
| Phase 4 | ✅ 组件测试 | ✅ API 测试 | ✅ 页面验证 | ✅ 灰度发布 | ✅ 1 周观察 |
| Phase 5 | ✅ 接口测试 | ✅ 管道测试 | ✅ 全流程验证 | ✅ 灰度发布 | ✅ 2 周观察 |

### 测试命令

```bash
# 单元测试
pytest

# 特定模块测试
pytest tests/test_joins.py
pytest tests/test_dispatch.py
pytest tests/test_room.py
pytest tests/test_at_chat.py
pytest tests/test_prompts_byte_identical.py
pytest tests/test_personas_split.py

# 集成测试
pytest tests/integration/

# 端到端测试（本地 Docker）
docker-compose -f docker-compose.test.yml up -d
pytest tests/e2e/
docker-compose -f docker-compose.test.yml down

# 覆盖率报告
pytest --cov=. --cov-report=html
```

---

## 部署策略

### 部署流程

```
本地开发
    ↓
本地测试（pytest）
    ↓
本地 Docker 环境测试
    ↓
提交代码（git commit）
    ↓
预发环境部署（staging server）
    ↓
预发环境测试
    ↓
生产环境灰度发布（茶水间优先）
    ↓
生产环境全量发布
    ↓
生产环境观察（1-2 周）
```

### 部署命令

```bash
# 本地测试
pytest

# 提交代码
git add .
git commit -m "Phase X: 描述"
git push

# 预发环境部署
ssh staging-server
cd /opt/dzmm
git pull
docker-compose -f docker-compose.prod.yml up -d

# 生产环境灰度发布（茶水间）
ssh production-server
cd /opt/dzmm
git pull
docker-compose -f docker-compose.prod.yml up -d dzmm-bot-tea

# 生产环境全量发布
ssh production-server
cd /opt/dzmm
git pull
docker-compose -f docker-compose.prod.yml up -d

# 观察日志
tail -f /opt/dzmm/logs/bot.log
tail -f /opt/dzmm/logs/renew_token.log
docker logs dzmm-bot
```

### 灰度发布策略

**Phase 3-4 的改动**：
1. 先在茶水间（最小群，4 人）验证
2. 观察 1 周，无错误后推广到上班群（780 人）
3. 观察 1 周，无错误后推广到认证妹子群（最大群）
4. 最后推广到玫瑰房

**Phase 5 的改动**：
1. 先在本地 Docker 环境充分测试
2. 在预发环境测试 1 周
3. 在生产环境灰度发布（茶水间）
4. 观察 2 周，无错误后全量发布

---

## 回滚程序

### 回滚原则

1. **快速回滚**：回滚时间 < 5 分钟
2. **数据保护**：回滚不影响用户数据
3. **日志记录**：记录回滚原因和时间
4. **事后分析**：分析回滚原因，防止重复

### 回滚步骤

#### Phase 0 回滚

```bash
# 无需回滚（纯清理操作）
```

#### Phase 1 回滚

```bash
# 1. 恢复文件结构
git checkout .

# 2. 重新部署
powershell -File deploy.ps1
```

#### Phase 2 回滚

```bash
# 1. 恢复 docker-compose.prod.yml
cp docker-compose.prod.yml.backup-* docker-compose.prod.yml

# 2. 重启容器
docker-compose -f docker-compose.prod.yml up -d

# 3. 验证恢复
tail -f /opt/dzmm/logs/renew_token.log
```

#### Phase 3 回滚

```bash
# 1. 恢复所有修改的文件
git checkout .

# 2. 删除新增文件
rm -rf runtime/joins.py runtime/dispatch.py runtime/room.py runtime/at_chat.py
rm -rf personas/
rm -rf prompts/*.md  # 保留原有的

# 3. 重新部署
powershell -File deploy.ps1

# 4. 验证恢复
pytest
docker logs dzmm-bot
```

#### Phase 4 回滚

```bash
# 1. 恢复所有修改的文件
git checkout admin/

# 2. 重新部署
powershell -File deploy.ps1

# 3. 验证恢复
# 访问运维后台，检查样式和布局
```

#### Phase 5 回滚

```bash
# 1. 恢复所有修改的文件
git checkout .

# 2. 删除新增文件
rm -rf runtime/pipeline.py runtime/state_store.py runtime/send_channel.py

# 3. 重新部署
powershell -File deploy.ps1

# 4. 验证恢复
pytest
docker logs dzmm-bot
```

---

## 风险评估

### 风险矩阵

| Phase | 风险等级 | 主要风险 | 缓解措施 |
|---|---|---|---|
| Phase 0 | 🟢 零风险 | 清理操作可能误删文件 | 备份后再清理 |
| Phase 1 | 🟢 零风险 | 文件移动可能导致路径错误 | grep 验证所有路径引用 |
| Phase 2 | 🟡 中风险 | 凭据迁移可能导致掉线 | 双写期 + 24h 观察 |
| Phase 3 | 🟡 中风险 | 重构可能引入回归 | 每步全量测试 + 灰度发布 |
| Phase 4 | 🟡 中风险 | 前端重构可能样式错乱 | 逐页视觉对比 + 灰度发布 |
| Phase 5 | 🔴 高风险 | 架构重构可能引入严重问题 | 设计评审 + 充分测试 + 灰度发布 |

### 风险缓解措施

#### Phase 2 风险缓解

1. **双写期**：至少 15 分钟双写，确认无错误后再删除老挂载
2. **24h 观察**：部署后观察 24 小时，确认无续期相关错误
3. **快速回滚**：准备好回滚脚本，回滚时间 < 5 分钟

#### Phase 3 风险缓解

1. **每步全量测试**：每完成一个 Step，运行全量 pytest
2. **灰度发布**：先在茶水间验证，再推广到其他群
3. **git tag**：每完成一个 Step，打 tag 方便回滚
4. **1 周观察**：每完成一个 Step，观察 1 周再进入下一个 Step

#### Phase 4 风险缓解

1. **逐页视觉对比**：每修改一个页面，对比修改前后的截图
2. **灰度发布**：先在茶水间验证，再推广到其他群
3. **无 JS 可用**：确保无 JS 时所有功能仍可用

#### Phase 5 风险缓解

1. **设计评审**：在实现前进行设计评审，确保架构合理
2. **充分测试**：本地 Docker 环境充分测试，预发环境测试 1 周
3. **灰度发布**：先在茶水间验证，观察 2 周再全量发布
4. **快速回滚**：准备好回滚脚本，回滚时间 < 5 分钟

---

## 总结

本执行计划详细描述了 DDZM Bot 平台优化的 6 个阶段，包括：

- **Phase 0**: 即时清理（<1h）
- **Phase 1**: L1 整理（1-2h）
- **Phase 2**: S1 凭据挂目录（2-3d）
- **Phase 3**: L2 bot.py 拆分（4-6w）
- **Phase 4**: 运维后台重构（4-6w）
- **Phase 5**: L3 架构重构（2-3m）

每个阶段都包含详细的执行步骤、验证标准、回滚程序和风险评估。

**总预计耗时**：4-6个月

**关键约束**：
1. 不停机原则
2. 双写期验证
3. 测试先行
4. 回滚就绪
5. 灰度发布

**成功标准**：
- 所有阶段的验证标准均已满足
- `pytest` 全量测试通过
- 部署后所有功能正常
- 无严重错误（P0/P1）

---

**下一步**：等待用户确认计划后，开始执行 Phase 0。
