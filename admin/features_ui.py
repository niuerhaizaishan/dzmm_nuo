"""功能与参数页（P12-D）：**功能矩阵 + 参数 + 按群集合 + 强制复位**。

### 为什么单独一页，而不是塞进 `/edit`

`/edit` 是"群与 Bot"（低频：加减群、改 token）；这一页是"每个群开哪些功能、
参数调多少"（高频：试玩法、调平衡）。两个页面提交的字段完全不同，
混在一张表单里会让"整表提交"的爆炸半径变大 —— 这里**只覆盖三节**，
不碰 groups / bots，所以旧页面提交也不可能删掉群（反过来也一样）。

### 三态而不是两态（关键设计）

功能开关在"全局"和"每个群"两级都有，而且两级都可以是**没配**（= 跟随默认）。
如果 UI 只给"开/关"两个按钮，用户就没法表达"这一格回到默认"，
于是"改回去"只能靠再点一次 —— 最后所有人都不敢动。
所以每一格是**三个单选框**：

    跟随默认 ／ 开 ／ 关

纯 HTML radio，**没有 JavaScript 也能用**（这是要求，不是巧合）。

### 参数用"留空 = 跟随"

数值/文本参数用文本框，**留空 = 不覆盖**（跟随全局/代码默认），
填了才写进配置。每个框旁边显示代码默认值、单位与允许范围。

### 与运行期的关系

这里只写 `config.json` 的三节（features / tuning / scopes），
**运行期要重启才生效**（P12-F 会做热加载）。
"生效值从哪来"（代码默认/全局/本群）在页面上逐格标出来。
"""
import html

__all__ = ["render_features", "parse_features", "features_diff",
           "effective_feature", "effective_param", "safe_scope"]

_CSS = """
body{font:13px/1.6 -apple-system,Segoe UI,Roboto,'Noto Sans CJK SC',sans-serif;
margin:0;background:#12141a;color:#d8dee9}
header{padding:10px 16px;background:#1b1f2a;border-bottom:1px solid #2b3140;
display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600}
h2{font-size:13px;margin:18px 0 6px}
h3{font-size:12px;margin:12px 0 4px;color:#8b95a7;font-weight:500}
a{color:#7aa2f7}
.badge{padding:1px 7px;border-radius:9px;font-size:11px}
.ok{background:#1f3d2b;color:#7ee2a8}.bad{background:#4a1f27;color:#ff8b9a}
.warn{background:#463a1c;color:#ffd479}.chg{background:#243044;color:#c8d6f0}
main{padding:14px 16px;max-width:1600px}
table{border-collapse:collapse;width:100%;margin-bottom:6px}
th,td{padding:3px 5px;text-align:left;border-bottom:1px solid #232935;
vertical-align:middle}
th{color:#8b95a7;font-weight:500;font-size:12px}
td.feat{white-space:nowrap}
input[type=text]{background:#0f1218;color:#d8dee9;border:1px solid #2b3140;
border-radius:5px;padding:3px 6px;font-size:12px;width:110px}
input[type=text].wide{width:170px}
button{padding:6px 14px;border:0;border-radius:6px;background:#2f6feb;color:#fff;
font-size:13px;cursor:pointer}
button.ghost{background:#2b3140}
button.tiny{padding:2px 8px;font-size:11px}
.msg{padding:8px 11px;border-radius:6px;margin:10px 0}
.msg.bad{background:#4a1f27;color:#ff8b9a}
.msg.ok{background:#1f3d2b;color:#7ee2a8}
.msg.warn{background:#463a1c;color:#ffd479}
.msg.chg{background:#243044;color:#c8d6f0;border-left:3px solid #2f6feb}
.msg.chg ul,.msg.warn ul{margin:4px 0 0 18px;padding:0}
.dim{color:#6c7686}.mono{font-family:ui-monospace,Consolas,monospace}
.off{opacity:.45}
td.grp{text-align:center}
label.r{font-size:11px;color:#8b95a7;margin-right:4px;white-space:nowrap}
footer{padding:10px 16px;color:#6c7686;font-size:11px}
"""


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

    # ---------------- 功能矩阵 ----------------
    head = "".join(
        "<th class='grp'>{a}{b}</th>".format(
            a=_e(alias.get(c, c[:8])),
            b="" if enabled.get(c, True) else "<br><span class='dim'>群已停用</span>")
        for c in gids)
    rows = []
    for cat, items in by_cat.items():
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
            eff = effective_feature(cfg_raw, gids[0], k, f["default"]) if gids else f["default"]
            note = f.get("note") or ""
            name = _e(f["label"]) + (
                " <span class='dim mono'>{}</span>".format(_e(k))
                if k != f["label"] else "")
            if note:
                name += "<br><span class='dim'>{}</span>".format(_e(note))
            if f.get("resets"):
                name += ("<br><span class='dim'>关掉要复位：{}</span>".format(
                    _e("、".join(f["resets"]))))
            rows.append("<tr><td class='feat'>{n}</td>{g}{cs}</tr>".format(
                n=name, g=gcell, cs="".join(cells)))

    # ---------------- 参数 ----------------
    prows = []
    for f in registry:
        if not f["params"]:
            continue
        prows.append("<tr><td colspan='3'><h3>{}</h3></td></tr>".format(
            _e(f["label"])))
        for p in f["params"]:
            k = p["key"]
            gcur = (cfg_raw.get("tuning") or {}).get(k)
            rng = ""
            if p["lo"] is not None or p["hi"] is not None:
                rng = "（{}~{}）".format(
                    "" if p["lo"] is None else p["lo"],
                    "" if p["hi"] is None else p["hi"])
            if k == "schedule.rank_hours":
                rng = "（逗号分隔，如 12,18）"
            prows.append(
                "<tr><td class='feat'>{l}<br><span class='dim mono'>{k}</span></td>"
                "<td><input type=text name='gtune[{k}]' value='{v}' "
                "placeholder='默认 {d}' data-lo='{lo}' data-hi='{hi}'></td>"
                "<td class='dim'>{u}{r}{n}</td></tr>".format(
                    l=_e(p["label"]), k=_e(k),
                    v="" if gcur is None else _e(gcur),
                    d=_e(p["default"]), u=_e(p["unit"]), r=_e(rng),
                    lo="" if p["lo"] is None else _e(p["lo"]),
                    hi="" if p["hi"] is None else _e(p["hi"]),
                    n=("　" + _e(p["note"])) if p.get("note") else ""))

    # ---------------- 按群参数覆盖 ----------------
    grows = []
    for c in gids:
        spec = next((x for x in groups if x.get("cid") == c), {})
        cur = spec.get("tuning") or {}
        cells = []
        for f in registry:
            for p in f["params"]:
                k = p["key"]
                eff, src = effective_param(cfg_raw, c, k, p["default"])
                cells.append(
                    "<tr><td class='feat'>{l} <span class='dim mono'>{k}</span>"
                    "</td><td><input type=text name='tune[{c}][{k}]' value='{v}' "
                    "placeholder='跟随（现在 {e}）'></td>"
                    "<td class='dim'>{src}</td></tr>".format(
                        l=_e(f["label"]), k=_e(k), c=_e(c),
                        v="" if cur.get(k) is None else _e(cur.get(k)),
                        e=_e(eff), src=_e(src)))
        grows.append(
            "<h3>{a} <span class='dim mono'>{c}</span>{off}</h3>"
            "<table><tr><th>参数</th><th>本群覆盖（留空 = 跟随）</th>"
            "<th>当前来源</th></tr>{rows}</table>".format(
                a=_e(alias.get(c, c[:8])), c=_e(c),
                off="" if enabled.get(c, True) else " <span class='dim'>（群已停用）</span>",
                rows="".join(cells)))

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
            "{n}<br><span class='dim'>代码默认 {d} 个群</span>"
            "</td><td>{cells}</td></tr>".format(
                n=_e(name), d=len(default), cells=cells))

    # ---------------- 强制复位 ----------------
    rrows = []
    for c in gids:
        for f in registry:
            if not f.get("resets"):
                continue
            rrows.append(
                "<form method=post action='/edit/reset' style='display:inline'>"
                "<input type=hidden name='csrf' value='{c}'>"
                "<input type=hidden name='cid' value='{cid}'>"
                "<input type=hidden name='key' value='{k}'>"
                "<button class='ghost tiny' type=submit title='清掉 {k} 的进行中状态'>"
                "复位 {l} @ {a}</button></form> ".format(
                    c=_e(csrf), cid=_e(c), k=_e(f["key"]), l=_e(f["label"]),
                    a=_e(alias.get(c, c[:8]))))
    reset_block = ("<h2>强制复位（清掉「进行中」的状态）</h2>"
                   "<div class='dim'>关掉一个玩法时，它可能还有「进行中」的状态；"
                   "状态不清会把整个群屏蔽住（公演尤其如此）。"
                   "这些按钮只清状态，<b>不删数据</b>（档案/记忆/发情值不动）。</div>"
                   "<div style='margin-top:6px'>{rows}</div>".format(rows=rrows)
                   ) if rrows else ""

    msg = ""
    if error:
        msg += "<div class='msg bad'>{}</div>".format(_e(error))
    if notice:
        msg += "<div class='msg ok'>{}</div>".format(_e(notice))
    if reset_note:
        msg += "<div class='msg ok'>{}</div>".format(_e(reset_note))
    if changes:
        msg += ("<div class='msg chg'><b>本次改动 {} 项</b>（逐条列出，请核对）："
                "<ul>{}</ul></div>".format(
                    len(changes),
                    "".join("<li class='mono'>{}</li>".format(_e(x))
                            for x in changes)))
    if pending or status is not None:
        # P12-F：生效状态（已生效 / 下一轮自动生效 / 需要重启 / 文件坏了）
        # 与 `/edit`、`/personas` 共用同一段话术，三页不能各说各的。
        from admin import config_ui
        msg += config_ui.status_block(status, pending=True, csrf=csrf)

    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>功能与参数 · 小小糯 运维后台</title><style>{css}</style>"
            "<script src='/static/app.js' defer></script></head><body>"
            "<header><h1>小小糯 · 功能与参数</h1>"
            "<span class='dim'>写入 config.json 的 features / tuning / scopes 三节"
            "（校验不过一个字都不写）</span>"
            "<span style='margin-left:auto'><a href='/'>← 只读面板</a> · "
            "<a href='/edit'>群与 Bot</a> · "
            "<a href='/personas'>🎭 人设与话术</a> · "
            "<a href='/prompts'>📝 文案模板</a> · "
            "<a href='/logout'>退出</a></span>"
            "</header><main>{msg}"
            "<form method=post action='/features'>"
            "<input type=hidden name='csrf' value='{csrf}'>"
            # 隐藏标记：证明这一页真的渲染过（见 parse_features 的说明）
            "<input type=hidden name='feat_form' value='1'>"
            "<h2>功能矩阵</h2>"
            "<div class='dim'>每一格三态：<b>跟随默认</b>（不写覆盖）／<b>开</b>／"
            "<b>关</b>。左边那列是<b>全局</b>（对所有群生效，除非某个群单独覆盖）。"
            "关掉一个功能后，它的命令会<b>静默</b>（不会被兜底聊天接走）。</div>"
            "<table><tr><th>功能</th><th>全局</th>{head}</tr>{rows}</table>"
            "<h2>参数（全局）</h2>"
            "<div class='dim'>留空 = 用代码默认值。括号里是允许范围，"
            "超出会被拒绝（这些值真的会闯祸：冷却 0 = 刷屏撞风控）。</div>"
            "<table><tr><th>参数</th><th>覆盖值</th><th>说明</th></tr>{prows}</table>"
            "<h2>参数（按群覆盖）</h2>{grows}"
            "<h2>按群集合</h2>"
            "<div class='dim'>这几项本来就是「只在某些群开放」的功能"
            "（勾选 = 在该群生效）。</div>"
            "<table><tr><th>集合</th><th>生效的群</th></tr>{srows}</table>"
            "<div style='margin-top:16px'><button type=submit>保存</button> "
            "<a href='/features' style='margin-left:10px'>放弃改动重新载入</a>"
            "</div></form>{reset_block}"
            "</main><footer>功能/参数/集合改完约 1 秒自动生效（热加载，不用重启）· "
            "校验失败不落盘 · 没有 JavaScript 也能完成全部操作</footer>"
            "</body></html>").format(
        css=_CSS, msg=msg, csrf=_e(csrf), head=head, rows="".join(rows),
        prows="".join(prows), grows="".join(grows), srows="".join(srows),
        reset_block=reset_block)


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
