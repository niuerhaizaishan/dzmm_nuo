"""功能与参数页（P12-D）：**以群为主视图 + 高频白名单 + 长尾折叠**（P4-4B §3）。

### 为什么单独一页，而不是塞进 `/edit`

`/edit` 是"群与 Bot"（低频：加减群、改 token）；这一页是"每个群开哪些功能、
参数调多少"（高频：试玩法、调平衡）。两个页面提交的字段完全不同，
混在一张表单里会让"整表提交"的爆炸半径变大 —— 这里**只覆盖三节**，
不碰 groups / bots，所以旧页面提交也不可能删掉群（反过来也一样）。

### 三态而不是两态（关键设计，§3 不动它）

功能开关在"全局"和"每个群"两级都有，而且两级都可以是**没配**（= 跟随默认）。
如果 UI 只给"开/关"两个按钮，用户就没法表达"这一格回到默认"，
所以每一格仍是**三个单选框**：跟随默认 ／ 开 ／ 关。纯 HTML radio，
**没有 JavaScript 也能用**（这是要求，不是巧合）。

### §3 简化后的信息结构（2026-10，屋主："不用那么细致"）

屋主的高频问题只有三个：「这个群现在开着什么」「我想关掉/打开某个能力」
「我改过哪些参数」。所以：

1. **高频白名单前置**：真正会动的 ~10 个键（`WHITELIST`）做成页首的
   直接开关区 —— 全局层一张表 + 每个群的折叠页里各一张按群表。
2. **以群为主视图**：每群一个 tab（CSS-only radio 切换，无 JS 也能用），
   摘要行直接回答"这个群现在开着什么"。
3. **长尾折叠**：其余 58 键的完整矩阵、全局参数全量、按群集合、
   新增参数覆盖，全部收进 `<details>` 高级区（原生折叠，无 JS 可用）。
4. **参数只显示已覆盖**：全局与每群的参数区默认只列配置里**真的写了覆盖**
   的键；"新增覆盖"入口按需展开。

⚠️ **表单字段名一个都没变**（`gfeat[key]` / `gfeat[cid][key]` / `gtune[key]` /
`tune[cid][key]` / `scope[..]` + `feat_form`），`parse_features` 与全部
解析/端到端测试零改动 —— 这轮是纯展示层重组。

### 与运行期的关系

这里只写 `config.json` 的三节（features / tuning / scopes），
改完约 1 秒热加载生效（P12-F），不用重启。
"""
import html

__all__ = ["render_features", "parse_features", "features_diff",
           "effective_feature", "effective_param", "safe_scope", "WHITELIST"]

# P4-4A：`_CSS` 已删除 —— 样式统一走 /static/base.css + components.css，
# 页面外壳（侧边栏/header/主题切换）由 admin.shell.page 提供。

# §3.1.2 高频白名单：屋主真正会动的键（顺序 = 预期使用频率）。
# 这些键**从高级矩阵里摘出来**（避免同名单选框在页面出现两处、互相踩），
# 全局三态渲染在页首白名单表，按群三态渲染在各群 tab 里。
WHITELIST = ("at_only", "slash_cmds", "quote_reply", "devil_roast", "devil_cold",
             "fortune", "trend", "recap", "profile_scan", "memory_report")


def _e(v):
    return html.escape("" if v is None else str(v))


def effective_feature(cfg_raw: dict, cid: str, key: str, default: bool) -> bool:
    """这一格**实际生效**的值：本群覆盖 > 全局覆盖 > 代码默认。"""
    g = {x.get("cid"): x for x in (cfg_raw.get("groups") or [])}.get(cid) or {}
    ov = (g.get("features") or {}).get(key)
    if ov is not None:
        return bool(ov)
    gl = (cfg_raw.get("features") or {}).get(key)
    return default if gl is None else bool(gl)


def effective_param(cfg_raw: dict, cid: str, key: str, default):
    """参数同理。返回 (值, 来源)。"""
    g = {x.get("cid"): x for x in (cfg_raw.get("groups") or [])}.get(cid) or {}
    ov = (g.get("tuning") or {}).get(key)
    if ov is not None:
        return ov, "本群"
    gl = (cfg_raw.get("tuning") or {}).get(key)
    if gl is not None:
        return gl, "全局"
    return default, "代码"


def safe_scope(cfg_raw: dict, name: str, default) -> tuple:
    """按群集合的当前值（配置优先，否则代码默认）。"""
    v = (cfg_raw.get("scopes") or {}).get(name)
    return tuple(v) if v is not None else tuple(default)


def _radio(name, value, cur, label, title=""):
    """三态单选框：value="" 表示"跟随默认/不覆盖"。"""
    checked = " checked" if str(cur) == str(value) else ""
    return ("<label class='r' title='{t}'><input type='radio' name='{n}' "
            "value='{v}'{c}>{l}</label>".format(
                n=_e(name), v=_e(value), c=checked, l=_e(label), t=_e(title)))


def render_features(cfg_raw, registry, params, scopes, categories,
                    features_obj, tuning_obj, error="", notice="", changes=(),
                    csrf="", pending=False, reset_note="", status=None):
    """画功能与参数页。

    `registry` = `runtime/features.py::registry()` 的结果（含每个功能的参数定义）
    `features_obj` / `tuning_obj` = 两个运行期模块（用来问默认值与来源）
    """
    groups = cfg_raw.get("groups") or []
    gids = [g.get("cid") for g in groups if g.get("cid")]
    alias = {g.get("cid"): (g.get("alias") or g.get("cid", "")[:8]) for g in groups}
    enabled = {g.get("cid"): g.get("enabled", True) for g in groups}
    by_cat = {}
    for f in registry:
        by_cat.setdefault(f["category"], []).append(f)
    cat_label = dict(categories)

    # ---------------- 高频白名单 · 全局层（§3.1.2）----------------
    # 这些键直接铺在页首（其余 58 键进高级区）。全局三态整页只渲染这一次
    # —— 同 name 的 radio 渲染两处会互踩（浏览器把它们当同一组）。
    wmap = {f["key"]: f for f in registry}
    wrows = []
    for k in WHITELIST:
        f = wmap.get(k)
        if f is None:
            continue                      # 注册表改名的容错：少渲染，不炸页面
        gcur = (cfg_raw.get("features") or {}).get(k)
        gcell = ("<td class='feat'>{}</td>".format(
            _radio("gfeat[{}]".format(k), "", "" if gcur is None else "x",
                   "默认")
            + _radio("gfeat[{}]".format(k), "on", "on" if gcur is True else "",
                     "开")
            + _radio("gfeat[{}]".format(k), "off", "off" if gcur is False else "",
                     "关")))
        name = _e(f["label"])
        if f.get("note"):
            name = ("<span title='{n}'>{l}</span> <span class='mono text-dim'>{k}</span>"
                    .format(n=_e(f["note"]), l=_e(f["label"]), k=_e(k)))
        wrows.append("<tr><td class='feat'>{n}</td>{g}</tr>".format(n=name, g=gcell))
    whitelist_global = (
        "<h2>高频开关（全局）</h2>"
        "<div class='text-dim'>最常动的 {n} 个键，对所有群生效；"
        "某个群要不一样，去下面那个群的页签里单独覆盖。"
        "「跟随默认」= 不写覆盖（用代码默认）。</div>".format(n=len(wrows))
        + "<table class='table table--compact'>"
          "<tr><th>功能</th><th>全局</th></tr>{}</table>".format("".join(wrows)))

    # ---------------- 群 tabs（§3.1.1：以群为主视图）----------------
    # CSS-only 切换（radio + 兄弟选择器），没有 JavaScript 也能用；
    # 每群一个 pane：摘要 + 高频开关（本群三态）+ 已覆盖参数 + 复位。
    tab_labels, panes, pane_css = [], [], []
    for i, c in enumerate(gids):
        spec = next((x for x in groups if x.get("cid") == c), {})
        off = not enabled.get(c, True)

        # 摘要行：直接回答"这个群现在开着什么"
        n_on = sum(1 for f in registry
                   if effective_feature(cfg_raw, c, f["key"], f["default"]))
        gfeat_cnt = len(spec.get("features") or {})
        gtune_cnt = len(spec.get("tuning") or {})
        badges = ("<span class='badge badge--{cls}'>启用 {on}/{total}</span> "
                  "<span class='badge badge--neutral'>本群覆盖功能 {gf}</span> "
                  "<span class='badge badge--neutral'>参数 {gt}</span>").format(
            cls="bad" if off else ("warn" if n_on < len(registry) else "ok"),
            on=n_on, total=len(registry), gf=gfeat_cnt, gt=gtune_cnt)
        if off:
            badges += " <span class='badge badge--bad'>群已停用（完全不管这个群）</span>"

        # 高频开关 · 本群三态
        wcells = []
        for k in WHITELIST:
            f = wmap.get(k)
            if f is None:
                continue
            cur = (spec.get("features") or {}).get(k)
            tri = (_radio("gfeat[{}][{}]".format(c, k), "",
                          "" if cur is None else "x", "跟随")
                   + _radio("gfeat[{}][{}]".format(c, k), "on",
                            "on" if cur is True else "", "开")
                   + _radio("gfeat[{}][{}]".format(c, k), "off",
                            "off" if cur is False else "", "关"))
            eff = effective_feature(cfg_raw, c, k, f["default"])
            src = "本群" if cur is not None else "全局/默认"
            wcells.append(
                "<tr><td class='feat'>{l}</td><td>{tri}</td>"
                "<td class='text-dim'>{e}（{src}）</td></tr>".format(
                    l=_e(f["label"]), tri=tri,
                    e="开" if eff else "关", src=_e(src)))
        wt = ("<h3>高频开关（本群覆盖）</h3>"
              "<table class='table table--compact'>"
              "<tr><th>功能</th><th>本群（跟随 / 开 / 关）</th>"
              "<th>现在</th></tr>{}</table>".format("".join(wcells))
              if wcells else "")

        # 本群参数：已覆盖的直接列出来，其余收进「新增覆盖」
        cur_tune = spec.get("tuning") or {}
        all_params = [p for f in registry for p in f["params"]]
        plabel = {p["key"]: p for p in all_params}
        covered = "".join(
            "<tr><td class='feat'>{l}<br><span class='mono text-dim'>{k}</span></td>"
            "<td><input class='input' type=text name='tune[{c}][{k}]' value='{v}'>"
            "</td><td class='text-dim'>清空 = 取消覆盖</td></tr>".format(
                l=_e(plabel[k]["label"]), k=_e(k), c=_e(c),
                v=_e(cur_tune.get(k)))
            for k in sorted(cur_tune) if k in plabel)
        covered_t = ("<h3>本群参数覆盖（{n} 个）</h3>"
                     "<table class='table table--compact'>"
                     "<tr><th>参数</th><th>覆盖值</th><th></th></tr>{rows}</table>"
                     ).format(n=len(cur_tune), rows=covered) if covered else \
            "<div class='text-dim'>本群没有参数覆盖（全部跟随全局/默认）。</div>"
        extra = "".join(
            "<tr><td class='feat'>{l} <span class='mono text-dim'>{k}</span></td>"
            "<td><input class='input' type=text name='tune[{c}][{k}]' value='{v}' "
            "placeholder='跟随（现在 {e}）'></td></tr>".format(
                l=_e(p["label"]), k=_e(p["key"]), c=_e(c),
                v="" if cur_tune.get(p["key"]) is None else _e(cur_tune.get(p["key"])),
                e=_e(effective_param(cfg_raw, c, p["key"], p["default"])[0]))
            # ⚠️ 已覆盖的键在上面 covered 表里渲染过，这里必须跳过 ——
            # 同名文本框提交两份，解析时只取第一个，页面留着另一个只会误导。
            for p in all_params if p["key"] not in cur_tune)
        extra_t = ("<details><summary>新增本群参数覆盖（其余 {} 个参数）</summary>"
                   "<table class='table table--compact'>"
                   "<tr><th>参数</th><th>本群覆盖（留空 = 跟随）</th></tr>{}</table>"
                   "</details>").format(
                       len(all_params) - len(cur_tune), extra)

        # 该群的强制复位按钮（关玩法后清「进行中」状态）
        resets = "".join(
            "<form method=post action='/edit/reset' style='display:inline'>"
            "<input type=hidden name='csrf' value='{c}'>"
            "<input type=hidden name='cid' value='{cid}'>"
            "<input type=hidden name='key' value='{k}'>"
            "<button class='btn btn--ghost btn--sm' type=submit title='清掉 {k} 的进行中状态'>"
            "复位 {l}</button></form> ".format(
                c=_e(csrf), cid=_e(c), k=_e(f["key"]), l=_e(f["label"]))
            for f in registry if f.get("resets"))
        reset_t = ("<h3>强制复位</h3>"
                   "<div class='text-dim'>关掉玩法后若还有「进行中」状态，"
                   "会把群屏蔽住 —— 这些按钮只清状态，不删数据。</div>"
                   "<div class='mt-2'>{}</div>".format(resets) if resets else "")

        panes.append(
            "<div class='tab-panel gpane' id='gpane-{i}'>"
            "<div class='mb-2'>{badges}</div>{wt}{covered_t}{extra_t}{reset_t}"
            "</div>".format(i=i, badges=badges, wt=wt, covered_t=covered_t,
                            extra_t=extra_t, reset_t=reset_t))
        tab_labels.append(
            "<input type='radio' name='gtab' id='gtab-{i}' data-no-dirty{chk}>"
            "<label for='gtab-{i}' class='tab'>{a}{off}</label>".format(
                i=i, chk=" checked" if i == 0 else "",
                a=_e(alias.get(c, c[:8])),
                off="" if not off else "（停用）"))
        # CSS-only 切换：radio 选中 → 显示对应 pane + 高亮 label。
        # 群数是动态的（config 可加减群），这些规则渲染时生成，
        # 走 extra_head 注入（页面真特有的样式，进不了共享 CSS）。
        pane_css.append(
            "#gtab-%d:checked ~ #gpane-%d{display:block}" % (i, i))
        pane_css.append(
            ("#gtab-%d:checked ~ label[for='gtab-%d']" % (i, i))
            + "{color:var(--c-accent);border-bottom-color:var(--c-accent)}")
    group_tabs = (
        "<h2>按群调整</h2>"
        "<div class='text-dim'>点群名切换；没有 JavaScript 也能用（纯 CSS 切换）。"
        "这里的开关只管<b>这一个群</b>；全局层在上面。</div>"
        "<div class='tabs gtabs'>{labels}{panes}</div>").format(
            labels="".join(tab_labels), panes="".join(panes))

    # ---------------- 高级：全部功能矩阵（非白名单键）----------------
    head = "".join(
        "<th class='grp'>{a}{b}</th>".format(
            a=_e(alias.get(c, c[:8])),
            b="" if enabled.get(c, True) else "<br><span class='text-dim'>群已停用</span>")
        for c in gids)
    rows = []
    for cat, items in by_cat.items():
        items = [f for f in items if f["key"] not in WHITELIST]
        if not items:
            continue
        rows.append("<tr><td colspan='{}'><h3>{}</h3></td></tr>".format(
            len(gids) + 2, _e(cat_label.get(cat, cat))))
        for f in items:
            k = f["key"]
            # 全局三态
            gcur = (cfg_raw.get("features") or {}).get(k)
            gcell = ("<td class='feat'>{}</td>".format(
                _radio("gfeat[{}]".format(k), "", "" if gcur is None else "x",
                       "默认")
                + _radio("gfeat[{}]".format(k), "on", "on" if gcur is True else "",
                         "开")
                + _radio("gfeat[{}]".format(k), "off", "off" if gcur is False else "",
                         "关")))
            cells = []
            for c in gids:
                cur = ((next((x for x in groups if x.get("cid") == c), {})
                        .get("features")) or {}).get(k)
                cells.append("<td class='grp'>{}</td>".format(
                    _radio("gfeat[{}][{}]".format(c, k), "",
                           "" if cur is None else "x", "跟随")
                    + _radio("gfeat[{}][{}]".format(c, k), "on",
                             "on" if cur is True else "", "开")
                    + _radio("gfeat[{}][{}]".format(c, k), "off",
                             "off" if cur is False else "", "关")))
            note = f.get("note") or ""
            name = _e(f["label"]) + (
                " <span class='mono text-dim'>{}</span>".format(_e(k))
                if k != f["label"] else "")
            if note:
                name += "<br><span class='text-dim'>{}</span>".format(_e(note))
            if f.get("resets"):
                name += ("<br><span class='text-dim'>关掉要复位：{}</span>".format(
                    _e("、".join(f["resets"]))))
            rows.append("<tr><td class='feat'>{n}</td>{g}{cs}</tr>".format(
                n=name, g=gcell, cs="".join(cells)))

    # ---------------- 参数（§3.1.4：只显示已覆盖，新增收进折叠）----------------
    def _gtune_row(p, show_current):
        k = p["key"]
        gcur = (cfg_raw.get("tuning") or {}).get(k)
        rng = ""
        if p["lo"] is not None or p["hi"] is not None:
            rng = "（{}~{}）".format(
                "" if p["lo"] is None else p["lo"],
                "" if p["hi"] is None else p["hi"])
        if k == "schedule.rank_hours":
            rng = "（逗号分隔，如 12,18）"
        return (
            "<tr><td class='feat'>{l}<br><span class='mono text-dim'>{k}</span></td>"
            "<td><input class='input' type=text name='gtune[{k}]' value='{v}' "
            "placeholder='默认 {d}' data-lo='{lo}' data-hi='{hi}'></td>"
            "<td class='text-dim'>{u}{r}{n}</td></tr>".format(
                l=_e(p["label"]), k=_e(k),
                v="" if gcur is None else _e(gcur),
                d=_e(p["default"]), u=_e(p["unit"]), r=_e(rng),
                lo="" if p["lo"] is None else _e(p["lo"]),
                hi="" if p["hi"] is None else _e(p["hi"]),
                n=("　" + _e(p["note"])) if show_current and p.get("note") else ""))

    all_params = [p for f in registry for p in f["params"]]
    g_ov = cfg_raw.get("tuning") or {}
    # 已覆盖的参数：页面上直接可见（这就是屋主问的"我改过哪些"）。
    covered_grows = "".join(
        _gtune_row(p, True) for p in all_params if p["key"] in g_ov)
    covered_g = (
        "<h2>已覆盖参数（全局，{n} 个）</h2>"
        "<div class='text-dim'>只列配置里<b>真的写了覆盖</b>的参数 —— "
        "没列出来的都跟随代码默认（覆盖值清空保存 = 取消覆盖）。</div>"
        "<table class='table table--compact'>"
        "<tr><th>参数</th><th>覆盖值</th><th>说明</th></tr>{rows}</table>"
        ).format(n=len(g_ov), rows=covered_grows) if covered_grows else \
        "<div class='text-dim'>全局没有任何参数覆盖 —— 全部跟随代码默认。</div>"
    # 新增覆盖的入口：全量参数清单（⚠️ 已覆盖的键在上面渲染过，这里跳过 ——
    # 同名文本框提交两份，解析只取第一个，页面留着另一个只会误导）。
    prows = "".join(_gtune_row(p, False) for p in all_params if p["key"] not in g_ov)

    # （§3：旧的"按群参数覆盖"整段矩阵已移进各群 tab 的 pane —— 见上文循环；
    #   旧的整页"强制复位"区已变成 pane 内按群的复位按钮。）

    # ---------------- 按群集合 ----------------
    srows = []
    for name, default in scopes:
        cur = set(safe_scope(cfg_raw, name, default))
        cells = "".join(
            "<label class='r'><input type='checkbox' name='scope[{n}][{c}]' "
            "value='1'{ch}>{a}</label>".format(
                n=_e(name), c=_e(c), ch=" checked" if c in cur else "",
                a=_e(alias.get(c, c[:8])))
            for c in gids)
        # 隐藏标记：证明"这一节被渲染过"。没有它就没法区分
        # "用户把勾全取消了" 和 "这份表单根本没渲染这一节" ——
        # 而后者（老页面/脚本提交）如果按"空集合"处理，
        # 会把默认非空的集合（如趋势只给认证群）**静默清空**。
        srows.append(
            "<tr><td class='feat'><input type=hidden name='scope_seen[{n}]' value='1'>"
            "{n}<br><span class='text-dim'>代码默认 {d} 个群</span>"
            "</td><td>{cells}</td></tr>".format(
                n=_e(name), d=len(default), cells=cells))

    msg = ""
    if error:
        msg += "<div class='msg msg--bad'>{}</div>".format(_e(error))
    if notice:
        msg += "<div class='msg msg--ok'>{}</div>".format(_e(notice))
    if reset_note:
        msg += "<div class='msg msg--ok'>{}</div>".format(_e(reset_note))
    if changes:
        msg += ("<div class='msg msg--info'><b>本次改动 {} 项</b>（逐条列出，请核对）："
                "<ul>{}</ul></div>".format(
                    len(changes),
                    "".join("<li class='mono'>{}</li>".format(_e(x))
                            for x in changes)))
    if pending or status is not None:
        # P12-F：生效状态（已生效 / 下一轮自动生效 / 需要重启 / 文件坏了）
        # 与 `/edit`、`/personas` 共用同一段话术，三页不能各说各的。
        from admin import config_ui
        msg += config_ui.status_block(status, pending=True, csrf=csrf)

    # P4-4A：页面外壳（侧边栏/header/主题切换/CSS）由 admin.shell 统一提供，
    # 这里只产出 content 片段。
    from admin import shell
    extra_head = "<style>{}</style>".format(
        ".gtabs{flex-wrap:wrap}.gtabs .gpane{display:none;width:100%}"
        + "".join(pane_css))
    content = ("{msg}"
               "<form method=post action='/features'>"
               "<input type=hidden name='csrf' value='{csrf}'>"
               # 隐藏标记：证明这一页真的渲染过（见 parse_features 的说明）
               "<input type=hidden name='feat_form' value='1'>"
               "<div class='text-dim mb-4'>写入 config.json 的 features / tuning / "
               "scopes 三节（校验不过一个字都不写）。每一格都是三态："
               "<b>跟随默认</b>（不写覆盖）／<b>开</b>／<b>关</b>；"
               "关掉的功能命令会<b>静默</b>（不会被兜底聊天接走）。</div>"
               "{whitelist_global}"
               "{group_tabs}"
               "{covered_g}"
               "<details><summary>高级 · 新增全局参数覆盖（全部 {np} 个参数）</summary>"
               "<input class='input js-search' type='text' "
               "placeholder='搜索参数（中文名或 key，如 cooldown / 冷却）' "
               "data-table-search='#f-params' autocomplete='off'>"
               "<div class='text-dim'>留空 = 用代码默认值。括号里是允许范围，"
               "超出会被拒绝（这些值真的会闯祸：冷却 0 = 刷屏撞风控）。</div>"
               "<table class='table table--compact' id='f-params'>"
               "<tr><th>参数</th><th>覆盖值</th><th>说明</th></tr>{prows}</table>"
               "</details>"
               "<details><summary>高级 · 全部功能矩阵（{nf} 个键 × 全局 + 各群）</summary>"
               "<input class='input js-search' type='text' "
               "placeholder='搜索功能（中文名或 key，如 骰子 / dice）' "
               "data-table-search='#f-matrix' autocomplete='off'>"
               "<div class='text-dim'>完整的三态矩阵（高频那 {nw} 个键在上面，"
               "这里不重复）。左边那列是<b>全局</b>（对所有群生效，除非某个群单独覆盖）。</div>"
               "<table class='table table--compact' id='f-matrix'>"
               "<tr><th>功能</th><th>全局</th>{head}</tr>{rows}</table>"
               "</details>"
               "<details><summary>高级 · 按群集合</summary>"
               "<div class='text-dim'>这几项本来就是「只在某些群开放」的功能"
               "（勾选 = 在该群生效）。</div>"
               "<table class='table table--compact'>"
               "<tr><th>集合</th><th>生效的群</th></tr>{srows}</table>"
               "</details>"
               "<div class='mt-6 flex items-center gap-4'>"
               "<button class='btn btn--primary' type=submit>保存</button> "
               "<a href='/features'>放弃改动重新载入</a>"
               "</div></form>"
               "<footer class='footer'>功能/参数/集合改完约 1 秒自动生效"
               "（热加载，不用重启）· 校验失败不落盘 · "
               "没有 JavaScript 也能完成全部操作</footer>").format(
        msg=msg, csrf=_e(csrf), whitelist_global=whitelist_global,
        group_tabs=group_tabs, covered_g=covered_g,
        np=len(all_params), prows=prows,
        nf=len([f for f in registry if f["key"] not in WHITELIST]),
        nw=len(wrows), head=head, rows="".join(rows), srows="".join(srows))
    return shell.page(active="/features", title="功能与参数",
                      breadcrumb="运行时配置 / 功能与参数", content=content,
                      extra_head=extra_head)


# ---------------------------------------------------------------- 表单 → 配置

def _s(form, key):
    v = form.get(key)
    if isinstance(v, list):
        v = v[0] if v else ""
    return (v or "").strip()


def _tri(v):
    """三态："" = 不覆盖；"on"/"off" = 覆盖。"""
    s = (v or "").strip().lower()
    if s in ("on", "1", "true", "yes"):
        return True
    if s in ("off", "0", "false", "no"):
        return False
    return None


def _same_as_default(key: str, raw, default) -> bool:
    """文本框里的 raw 是否与代码默认值等价（按参数类型规范化后再比）。"""
    try:
        from runtime.tuning import PARAMS, _coerce
        p, _f = PARAMS[key]
        return _coerce(p, raw, key) == default
    except Exception:                                          # noqa: BLE001
        return str(raw) == str(default)


def _norm_value(key: str, v):
    """把值规范化成参数该有的类型（int/float/str）；失败就按原样。

    为什么要在**写之前**规范化：文本框提交上来的是字符串，直接落盘会得到
    `"devil.cooldown": "601"`（字符串）。虽然 `_parse` 读的时候会转成数字、
    行为没错，但配置文件里"数字写成字符串"会让对账/比对/diff 都变麻烦，
    而且下一次有人手工改更容易写错。
    """
    if v is None:
        return None
    try:
        from runtime.tuning import PARAMS, _coerce
        p, _f = PARAMS.get(key, (None, None))
    except Exception:                                          # noqa: BLE001
        return v
    if p is None:
        return v
    try:
        return _coerce(p, v, key)
    except Exception:                                          # noqa: BLE001
        return v


def parse_features(form, base: dict, registry, scopes, params) -> dict:
    """把这一页的表单解析成新的三节（+ 按群覆盖）。

    **只写"与默认不同"的覆盖**：这样配置文件里永远只有"真的改过的东西"，
    而不是把几十个默认值抄一遍（那样以后改代码默认值就失效了）。

    返回 `{"features": {...}, "tuning": {...}, "scopes": {...},
           "group_features": {cid: {...}}, "group_tuning": {cid: {...}}}`
    """
    # 这一页渲染时会写一个隐藏标记 `feat_form=1`。
    # 没有它 ⇒ 这份表单**不是这一页发的**（老页面、脚本、半截提交），
    # 那就**一个字都不动**（原样继承），免得把整片覆盖清空。
    # 复选框尤其危险：没勾 = 什么都不提交，与"没渲染"长得一模一样。
    if _s(form, "feat_form") != "1":
        return {"features": dict(base.get("features") or {}),
                "tuning": dict(base.get("tuning") or {}),
                "scopes": {k: list(v) for k, v in (base.get("scopes") or {}).items()},
                "group_features": {g["cid"]: dict(g.get("features") or {})
                                   for g in (base.get("groups") or [])
                                   if g.get("cid") and g.get("features")},
                "group_tuning": {g["cid"]: dict(g.get("tuning") or {})
                                 for g in (base.get("groups") or [])
                                 if g.get("cid") and g.get("tuning")},
                "untouched": True}

    feats, tunes = {}, {}
    for f in registry:
        k = f["key"]
        v = _tri(_s(form, "gfeat[{}]".format(k)))
        if v is not None and v != bool(f["default"]):
            feats[k] = v
    for p in params:
        k = p["key"]
        raw = _s(form, "gtune[{}]".format(k))
        if raw == "":
            continue
        # 与默认值比要**先规范化**：文本框里是字符串（"0.03"），
        # 默认值是数字（0.03）—— 直接比字符串会把"没改"当成改了，
        # 于是配置文件里堆一堆与默认相同的覆盖（以后改代码默认值就失效了）。
        if _same_as_default(k, raw, p["default"]):
            continue
        tunes[k] = _norm_value(k, raw)   # 规范化成参数该有的类型（别把数字写成字符串）

    gfeat, gtune = {}, {}
    for g in base.get("groups") or []:
        cid = g.get("cid")
        if not cid:
            continue
        for f in registry:
            k = f["key"]
            v = _tri(_s(form, "gfeat[{}][{}]".format(cid, k)))
            if v is not None:
                # 与"全局（或默认）"相同就不必写按群覆盖
                eff = effective_feature(base, cid, k, f["default"])
                glob = (base.get("features") or {}).get(k)
                base_val = f["default"] if glob is None else bool(glob)
                if v != base_val:
                    gfeat.setdefault(cid, {})[k] = v
        for p in params:
            k = p["key"]
            raw = _s(form, "tune[{}][{}]".format(cid, k))
            if raw == "":
                continue
            eff, _src = effective_param(base, cid, k, p["default"])
            if not _same_as_default(k, raw, eff):
                gtune.setdefault(cid, {})[k] = _norm_value(k, raw)

    out_scopes = {}
    for name, default in scopes:
        if _s(form, "scope_seen[{}]".format(name)) != "1":
            continue                      # 这一节没被渲染 ⇒ 不动它
        picked = [g.get("cid") for g in (base.get("groups") or [])
                  if g.get("cid") and _s(form, "scope[{}][{}]".format(name, g["cid"]))]
        if set(picked) != set(default):
            out_scopes[name] = picked
        elif (base.get("scopes") or {}).get(name):
            pass                          # 与默认一致 ⇒ 不写（等于取消覆盖）

    return {"features": feats, "tuning": tunes, "scopes": out_scopes,
            "group_features": gfeat, "group_tuning": gtune}


def features_diff(old: dict, parsed: dict, registry, scopes,
                  effective_feature_fn=None) -> list:
    """人话改动清单（只列真的变了的）。"""
    out = []
    label = {f["key"]: f["label"] for f in registry}
    for k, v in sorted(parsed["features"].items()):
        cur = (old.get("features") or {}).get(k)
        if cur != v:
            out.append("全局功能 {} : {} -> {}".format(
                label.get(k, k), _zh(cur), _zh(v)))
    for k in sorted(set(old.get("features") or {}) - set(parsed["features"])):
        out.append("全局功能 {} : 取消覆盖（回到默认）".format(label.get(k, k)))
    for k, v in sorted(parsed["tuning"].items()):
        cur = _norm_value(k, (old.get("tuning") or {}).get(k))
        now = _norm_value(k, v)
        if cur != now:
            out.append("全局参数 {} : {} -> {}".format(k, cur, now))
    for k in sorted(set(old.get("tuning") or {}) - set(parsed["tuning"])):
        out.append("全局参数 {} : 取消覆盖（回到默认）".format(k))
    for cid, ov in sorted(parsed["group_features"].items()):
        for k, v in sorted(ov.items()):
            was = ((next((g for g in (old.get("groups") or [])
                          if g.get("cid") == cid), {}).get("features")) or {}).get(k)
            if was != v:
                out.append("群 {} 功能 {} : {} -> {}".format(
                    cid[:8], label.get(k, k), _zh(was), _zh(v)))
    for cid, ov in sorted(parsed["group_tuning"].items()):
        for k, v in sorted(ov.items()):
            was = ((next((g for g in (old.get("groups") or [])
                          if g.get("cid") == cid), {}).get("tuning")) or {}).get(k)
            if _norm_value(k, was) != _norm_value(k, v):
                out.append("群 {} 参数 {} : {} -> {}".format(cid[:8], k, was, v))
    for name, _default in scopes:
        was = set((old.get("scopes") or {}).get(name) or [])
        now = set(parsed["scopes"].get(name) or [])
        if name in parsed["scopes"] and was != now:
            out.append("集合 {} : {} 个群 -> {} 个群".format(name, len(was), len(now)))
        elif name not in parsed["scopes"] and (old.get("scopes") or {}).get(name):
            out.append("集合 {} : 取消覆盖（回到代码默认）".format(name))
    return out


def _zh(v):
    if v is None:
        return "跟随"
    return "开" if v else "关"
