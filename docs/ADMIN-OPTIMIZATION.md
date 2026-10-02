# DDZM 运维后台优化方案（完整版）

> 2026-09-30 · 基于 12 轮施工记录 + 项目结构审计 + 后台代码逐行分析

---

## 一、现状诊断

### 1.1 系统概览

DDZM Bot 平台是一个运行在阿里云 ECS（1.87G RAM）上的多群聊天机器人系统，通过 Docker 容器部署。运维后台（`admin/`）是唯一的可视化管理界面，承载以下职责：

| 页面 | 路径 | 功能 | 写入 |
|---|---|---|---|
| 概览仪表盘 | `/` | 进程/续期/出站/错误/群状态/配置/热加载 | ❌ 只读 |
| 群与Bot配置 | `/edit` | 增删群、改token、开关筛查/推送 | ✅ config.json |
| 功能与参数 | `/features` | 68个Feature开关 + 参数调优 + 按群集合 | ✅ config.json |
| 人设与话术 | `/personas` | 3个人设的extra/name/phrases编辑+预览 | ✅ config.json |
| 文案模板 | `/prompts` | 41个提示词在线编辑+契约自测+版本回滚 | ✅ prompts/*.md |
| JSON API | `/api/summary` | 全量数据快照 | ❌ 只读 |
| 健康探针 | `/healthz` | k8s/load balancer 探活 | ❌ 只读 |

**技术栈**：Python `http.server.ThreadingHTTPServer` + 纯字符串拼接HTML + 内联CSS + 极简原生JS  
**安全模型**：账号密码登录 + CSRF token + 结构性只读（sqlite mode=ro）+ 安全组白名单  

### 1.2 文件结构

```
admin/
├── dashboard.py       2091行  HTTP服务+数据采集+HTML渲染+认证 四合一
├── config_ui.py        580行  群/Bot配置编辑页（独立CSS）
├── features_ui.py      515行  功能矩阵+参数页（独立CSS）
├── personas_ui.py      508行  人设话术编辑页（独立CSS）
├── prompts_ui.py       405行  提示词模板编辑页（独立CSS）
└── static/
    └── app.js           87行  前端增强（dirty计数/范围校验/确认弹窗）
```

### 1.3 问题清单

#### 🔴 体验缺陷

| # | 问题 | 影响 | 严重度 |
|---|---|---|---|
| E1 | CSS复制粘贴五份 | dashboard/config/features/personas/prompts各自维护一份`_CSS`，已出现漂移（features有`.off/.grp/label.r`，其他没有）。改一个颜色要改五个文件 | 高 |
| E2 | HTML用Python字符串拼接 | 2091行dashboard.py里大量`"<tr><td>{}</td></tr>".format(...)`，可读性极差，IDE无语法高亮，改布局像拆弹 | 高 |
| E3 | 无响应式导航 | 顶部一行`<a>`链接，手机端挤成一团；无侧边栏、无折叠菜单、无面包屑 | 高 |
| E4 | 首页信息密度失控 | 10个section平铺grid，每个max-height:460px带滚动条；关键指标（会话状态/token剩余/失败数）淹没在卡片海里 | 高 |
| E5 | 表格无交互 | 不能排序、不能筛选、不能搜索；outbox想看历史只能SSH | 中 |
| E6 | 编辑页无即时反馈 | 改完点保存→整页刷新→回到顶部；不知道改了什么、改对没有 | 中 |
| E7 | 无实时数据 | 所有数据都是请求时快照；续期状态变化要手动刷新 | 中 |
| E8 | 暗色主题唯一 | 只有深色，白天刺眼；无亮色切换 | 低 |
| E9 | 登录页简陋 | 纯表单+错误文字，无品牌感 | 低 |
| E10 | 审计日志无可视化 | `config_audit.log`是纯文本，要看谁改了什么得读文件 | 低 |

#### 🟡 结构缺陷

| # | 问题 | 影响 |
|---|---|---|
| S1 | dashboard.py 2091行四类职责混杂 | 数据采集(section_*)、HTTP路由(do_GET/do_POST)、HTML渲染(render_*)、认证(_sessions/_fails)全在一个文件 |
| S2 | 渲染与数据耦合 | render_html()里if/elif硬编码每个section的渲染逻辑，加新section要改两处 |
| S3 | CSS变量缺失 | 颜色/间距/字号全是字面量散落各文件，无法统一调整 |
| S4 | 无组件抽象 | badge/table/card/msg反复手写HTML，样式不一致 |
| S5 | API只有/api/summary | 返回全量JSON巨无霸(~50KB)，前端想局部刷新只能全量拉再自己过滤 |

#### 🟢 已有优势（必须保留）

| 特性 | 说明 |
|---|---|
| 结构性只读 | sqlite mode=ro + 源码扫描禁写语句 + 测试钉住 |
| 认证完整 | 登录+CSRF+失败锁定(5次起)+hmac.compare_digest+HttpOnly+SameSite |
| 配置安全 | 校验后写+自动备份+审计日志+一键回滚 |
| 渐进增强 | 纯HTML表单无JS也能完成全部操作 |
| 热加载 | 提示词/配置改完~1秒生效，不用重启 |
| 提示词契约 | 保存前静态校验+可选模型自测，改坏不可能保存成功 |

---

## 二、设计系统

### 2.1 Design Tokens

已实现于 `admin/static/base.css`：

```css
/* 色彩系统 — 暗色(默认) */
--c-bg-0: #0d1117;      /* 最深背景 */
--c-bg-1: #161b22;      /* 卡片/侧边栏 */
--c-bg-2: #1c2333;      /* 表头/输入框背景 */
--c-bg-3: #242d3d;      /* hover/active */
--c-border: #30363d;
--c-text-0: #f0f6fc;    /* 标题 */
--c-text-1: #c9d1d9;    /* 正文 */
--c-text-2: #8b949e;    /* 次要 */
--c-text-3: #6e7681;    /* 辅助 */
--c-accent: #58a6ff;    /* 主色调 */
--c-ok: #3fb950;        /* 成功 */
--c-warn: #d29922;      /* 警告 */
--c-bad: #f85149;       /* 错误 */

/* 亮色主题通过 [data-theme="light"] 覆盖上述变量 */
```

### 2.2 组件库

已实现于 `admin/static/components.css`：

| 组件 | CSS类 | 变体 |
|---|---|---|
| Badge | `.badge` | `--ok/--warn/--bad/--info/--neutral`, `--dot`, `--pulse` |
| Card | `.card` | `--elevated`, header/body/footer/compact/scroll |
| Stat Card | `.stat-card` | `--ok/--warn/--bad` |
| Table | `.table` | `--striped/--compact`, sticky header, hover highlight |
| KV Table | `.kv-table` | key-value两列布局 |
| Message | `.msg` | `--ok/--warn/--bad/--info` |
| Toast | `.toast` | `--ok/--bad/--info`, auto-dismiss 3s |
| Button | `.btn` | `--primary/--secondary/--ghost/--danger`, `--sm/--lg/--block` |
| Input | `.input` | `--error/--narrow/--wide`, focus ring |
| Tabs | `.tabs/.tab/.tab-panel` | 下划线激活态 |
| Empty State | `.empty-state` | icon + text |
| Diff View | `.diff-line` | `diff-add/diff-del/diff-ctx` |
| Skeleton | `.skeleton` | shimmer动画加载态 |
| Tooltip | `[data-tooltip]` | hover显示 |

### 2.3 布局系统

已实现于 `admin/static/base.css`：

```
┌─────────────────────────────────────────────┐
│ Header (sticky, backdrop-blur)               │
│ [☰] 面包屑              [🌙] [退出]         │
├──────────┬──────────────────────────────────┤
│ Sidebar  │                                  │
│ 📊 概览   │         Content Area             │
│ ⚙ 配置   │         max-width: 1400px        │
│ 🧩 功能   │         padding: 24px            │
│ 🎭 人设   │                                  │
│ 📝 文案   │                                  │
│ 📦 出站   │                                  │
│ 🔍 日志   │                                  │
│ 📋 审计   │                                  │
├──────────┴──────────────────────────────────┤
│ Footer                                       │
└─────────────────────────────────────────────┘
```

- 桌面端：固定侧边栏 240px + 弹性内容区
- 移动端(<768px)：侧边栏滑出 + 遮罩层 + 汉堡菜单
- 响应式断点：480px / 768px / 1200px

---

## 三、已交付的前端资产

### 3.1 文件清单

| 文件 | 大小 | 说明 |
|---|---|---|
| `admin/static/base.css` | ~8KB | Design tokens + Reset + 排版 + App Shell + 响应式 + 工具类 + 动画 |
| `admin/static/components.css` | ~10KB | 15种UI组件 + 变体 + 交互状态 |
| `admin/static/dashboard.css` | ~3KB | 仪表盘专用：status-grid / alert-banner / log-viewer / proof-item / heat-bar |
| `admin/static/app.js` | ~7KB | 8个模块：主题切换/侧边栏/dirty追踪/范围校验/自动刷新/表格排序/折叠/Tabs |

### 3.2 JS模块详解

| 模块 | 功能 | 降级策略 |
|---|---|---|
| `initTheme()` | localStorage记忆偏好 + 跟随系统 + 手动切换 | 无JS时默认暗色 |
| `initSidebar()` | 移动端滑出菜单 + ESC关闭 + 遮罩点击关闭 | 无JS时桌面端侧边栏仍可见(CSS) |
| `initDirtyTracking()` | 表单改动计数 + sticky警告条 + beforeunload提醒 + 提交确认 | 无JS时直接提交 |
| `initRangeValidation()` | input事件实时标红 + title提示范围 | 服务端仍有校验兜底 |
| `initAutoRefresh()` | 30s轮询/api/summary + 更新stat/badge + stale指示器 | 无JS时手动刷新 |
| `initTableSort()` | 点击表头排序(数字/中文) + 方向指示 | 无JS时按原始顺序 |
| `initCollapsible()` | data-collapse-toggle属性驱动展开/收起 | 无JS时全部展开 |
| `initTabs()` | data-tab属性驱动标签页切换 | 无JS时显示第一个panel |

### 3.3 接入方式（向后兼容）

现有dashboard.py的`_CSS`变量和`render_html()`**不需要立即替换**。新CSS/JS可以通过以下方式渐进接入：

```python
# 在 render_html() 的 <head> 里追加新样式表（优先级高于内联_CSS）
"<link rel='stylesheet' href='/static/base.css'>"
"<link rel='stylesheet' href='/static/components.css'>"
"<link rel='stylesheet' href='/static/dashboard.css'>"

# 在 </body> 前追加新脚本
"<script src='/static/app.js'></script>"
```

旧的内联`_CSS`会作为fallback存在，新CSS通过更高特异性或后加载覆盖。这样可以**逐步迁移**而不是一次性重写所有渲染函数。

---

## 四、分阶段执行计划

### Phase A: 样式层升级（零功能变更，1天）✅ 已完成基础资产

| 步骤 | 内容 | 风险 |
|---|---|---|
| A-1 | base.css + components.css + dashboard.css 已创建 | ✅ 零 |
| A-2 | app.js v2 已重写 | ✅ 零 |
| A-3 | dashboard.py `<head>` 注入新CSS链接 | 极低（追加不替换） |
| A-4 | dashboard.py `</body>` 注入新JS | 极低 |
| A-5 | 逐个UI文件的`_CSS`替换为`<link>`引用 | 低（视觉回归测试） |
| A-6 | 删除冗余内联CSS | 低 |

### Phase B: 布局重构（小改，1-2天）

| 步骤 | 内容 |
|---|---|
| B-1 | render_html() 输出App Shell结构(sidebar/header/main/footer) |
| B-2 | 导航从header链接改为sidebar nav-items |
| B-3 | 首页10个section重排：告警置顶 → 3个大stat卡 → 群状态表 → 折叠组 |
| B-4 | 登录页重新设计(居中卡片+品牌logo+错误动画) |
| B-5 | 移动端适配验证 |

### Phase C: 渲染引擎升级（中等改动，2-3天）

| 步骤 | 内容 |
|---|---|
| C-1 | 抽取组件函数：card()/table()/badge()/msg()/kv_table()/log_viewer() |
| C-2 | section注册制：每个section自带render函数，消除if/elif链 |
| C-3 | dashboard.py瘦身：数据采集/HTTP路由/渲染/认证拆成四个内部模块 |
| C-4 | 目标：dashboard.py从2091行降至~800行 |

### Phase D: API + 实时性（中等改动，2天）

| 步骤 | 内容 |
|---|---|
| D-1 | 新增轻量API：`/api/status`(<1KB) / `/api/groups` / `/api/outbox?status=&limit=` / `/api/errors` / `/api/audit` |
| D-2 | 首页30s自动刷新stat卡片（app.js已实现框架） |
| D-3 | outbox页支持URL参数筛选+分页 |
| D-4 | 日志页SSE tail-f效果（可选） |

### Phase E: 编辑体验（中等改动，2天）

| 步骤 | 内容 |
|---|---|
| E-1 | 保存前diff预览（config_ui已有diff_summary，扩展到所有编辑页） |
| E-2 | 概率参数滑块+百分比预览条 |
| E-3 | 文本参数字符计数 |
| E-4 | 保存后锚点停留+高亮"已保存"淡出 |
| E-5 | Toast通知替代alert |

### Phase F: 新功能（按需排期）

| 功能 | 说明 | 优先级 |
|---|---|---|
| 日志查看器 | `/logs`页，tail-f效果+SSE，关键词过滤/高亮 | 高 |
| 审计可视化 | `/audit`页，时间线+diff查看 | 中 |
| 群详情页 | 点击群状态表行→展开完整状态/记忆/发情值 | 中 |
| 在线测试台 | 选人设+输入→预览生成结果（不发群） | 低 |
| 导出/导入配置 | JSON格式跨环境迁移 | 低 |

---

## 五、与后端施工的衔接

### 5.1 与P3旧账的关系

| P3项 | 与后台优化的关系 |
|---|---|
| ⑤d main_loop拆分 | 后台Phase C的section注册制与bot.py拆分可并行推进，共享组件抽象思路 |
| ⑥ S1凭据挂目录 | dashboard.py的TOKEN_FILE也要跟着改（已在§2读口清单里列出） |
| ⑦ outbox清理 | Phase D的`/api/outbox`端点可直接复用resolve_outbox.py的逻辑 |

### 5.2 与热加载的关系

- CSS/JS文件通过`/static/`serve，浏览器缓存可能延迟生效 → deploy后建议加版本号query string（`app.js?v=2`）
- 模板变更不需要重启（如果走文件读取），但当前render_*()是Python函数内联HTML → Phase C完成后才能热更

### 5.3 安全约束不变

| 约束 | 保持方式 |
|---|---|
| 结构性只读 | 新API端点同样sqlite mode=ro + 源码扫描 |
| CSRF | 新表单继续带csrf token |
| 无JS可用 | 所有交互都有HTML fallback |
| CSP | script-src 'self' 不变，新JS在static/下 |
| 认证 | 登录/session/锁定机制不动 |

---

## 六、成功指标

| 指标 | 当前 | Phase A+B后 | Phase C+D后 | Phase E+F后 |
|---|---|---|---|---|
| 打开首页到看到关键信息 | 翻找10个卡片 | 顶部3个大数字+告警横幅 | 同左+30s自动刷新 | 同左 |
| 改一个参数的操作步骤 | 找到→改→保存→整页刷新→滚回去确认 | 同左+亮色主题 | 同左+diff预览 | 改→预览→保存→原地Toast确认 |
| 查一条历史失败消息 | SSH+grep | SSH+grep | 页面筛选+排序 | 同左+SSE实时 |
| 看谁改了什么配置 | 读log文件 | 读log文件 | 读log文件 | 审计时间线页面 |
| 手机端可用性 | 勉强能用 | 侧边栏+自适应 | 同左 | 同左 |
| dashboard.py行数 | 2091 | 2091 | ~800 | ~800 |
| CSS维护点 | 5份 | 3份(base+components+page) | 同左 | 同左 |
| 亮色主题 | ❌ | ✅ | ✅ | ✅ |
| 新section开发成本 | 改2处(data+render) | 改2处 | 改1处(注册) | 改1处 |
| 首次加载大小 | ~60KB HTML+内联CSS | ~60KB+21KB CSS+7KB JS | 同左(缓存后更快) | 同左 |

---

## 七、风险与缓解

| 阶段 | 风险 | 缓解 |
|---|---|---|
| Phase A | 新CSS覆盖不完全导致样式错乱 | 追加不替换；旧_CSS作fallback；逐页视觉对比 |
| Phase B | App Shell结构变化导致旧书签/爬虫失效 | 保留旧URL重定向；`/index.html`→`/` |
| Phase C | 渲染函数拆分引入bug | 每拆一个section跑全量pytest+deploy验证；git tag里程碑 |
| Phase D | 新API暴露敏感数据 | 所有新端点走_require_session()；只读；测试钉住 |
| Phase E | diff预览计算错误误导用户 | diff_summary已有测试；预览标注"仅供参考" |
| 全局 | 浏览器缓存旧CSS/JS | deploy时给static文件加版本号hash |

---

## 八、附录

### A. 现有页面截图对照（改造前后）

*待补充：Phase A+B完成后截图对比*

### B. Design Token速查表

见 `admin/static/base.css` 的 `:root` 块。

### C. 组件使用示例

见 `admin/static/components.css` 头部注释及各组件的CSS类名。

### D. 相关文档

- [P12-BR-P3旧账施工清单](P12-BR-P3旧账施工清单.md) — 后端剩余项
- [P12-BP-BQ-BE上线记录](P12-BP-BQ-BE-上线记录.md) — 最近一批改动留档
- [P12-BL-续期链再检查](P12-BL-续期链再检查.md) — S1凭据挂目录方案
