"""人设与话术页（P12-E）：**改机器人是谁、说什么话**，不用改代码。

### 两段

- **A 段 · 人设文本**：`name_full`（全名，提示词里那个"小小糯～"）、
  `name_short`（简称）、`extra`（本群专属设定，会追加到系统提示词末尾）。
- **B 段 · 固定话术**：不走 AI 的那些群内文案（错误提示、记忆回执、等待提示……）。
  房间群原来会漏出"小秘书"口吻，就是因为这些句子写死在代码里。

### 一条规则，全页统一

**页面上每一格显示的，就是"当前生效的文本"**（代码默认 + 已有覆盖）。
保存时逐格与**代码默认**比对：

    与默认相同 → 不写覆盖（配置文件里不留痕）
    与默认不同 → 写覆盖（**空串也算不同** → "这段不要了"）

这样"改回去"和"没改过"是同一件事，不需要额外记"我原来是什么"；
也不会出现功能页那种"复选框没勾 = 啥都没提交"的歧义 ——
文本框永远会提交自己的内容（哪怕是空的）。

代价是：`extra` 清空 = **真的空**（不是回到默认），想要回默认就按那一节的
「恢复默认」按钮。这句话也印在页面上。

### 预览

点「预览（不保存）」会用**页面上的草稿**渲染一遍：合成后的提示词、每条话术的
最终文本、体检结果；勾上「顺便让模型试说一句」还会真调一次模型（**不会发到群里**）。
草稿通过 `personas.use_overrides()` 走 contextvars，只影响这一次请求 ——
面板是多线程的，改全局再改回来会让别的请求读到草稿。

### 与运行期的关系

只写 `config.json` 的 `personas` 一节，**运行期要重启才生效**（P12-F 做热加载）。
"""
import html

__all__ = ["render_personas", "parse_personas", "personas_diff",
           "effective_field", "effective_phrases", "persona_preview",
           "FIELDS", "DEMO_SYSTEM"]

# (字段名, 中文标签, 说明)
FIELDS = (
    ("name_full", "全名", "提示词里写的那个名字（小小糯～），改名靠它"),
    ("name_short", "简称", "群里自称、名字替换用的短名"),
    ("extra", "专属设定", "追加到系统提示词末尾的一段；留空 = 不加"),
)

# P4-4A：`_CSS` 大头已删（样式走 /static/base.css + components.css，外壳走
# admin.shell.page）。下面只剩本页特有的 4 条：
_CSS = """
textarea.extra{height:200px;line-height:1.5}
textarea.p{height:52px;line-height:1.5}
label.chk{font-size:12px;color:var(--c-text-2)}
.changed{border-left:3px solid var(--c-accent);padding-left:6px}
"""


def _e(v):
    return html.escape("" if v is None else str(v))


def _short(s, n=26):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _P():
    import personas
    return personas


# ---------------------------------------------------------------- 生效值

def effective_field(default: dict, ov: dict, field: str) -> str:
    """这一格**当前生效**的文本：有覆盖用覆盖，没有用代码默认。

    ⚠️ 判据是 `field in ov`（**空串也算覆盖**），不是 `ov.get(field)`。
    """
    if field in (ov or {}):
        v = ov[field]
        return "" if v is None else str(v)
    v = (default or {}).get(field)
    return "" if v is None else str(v)


def effective_phrases(default_phrases: dict, ov: dict) -> dict:
    """话术同理：覆盖里写了（哪怕空串）就用它。"""
    out = {k: ("" if v is None else str(v)) for k, v in (default_phrases or {}).items()}
    for k, v in ((ov or {}).get("phrases") or {}).items():
        out[str(k)] = "" if v is None else str(v)
    return out


def _personas_cfg(cfg_raw: dict) -> dict:
    return cfg_raw.get("personas") or {}


def _in_use(cfg_raw: dict, key: str, groups) -> list:
    """哪些群在用它（配置指定的 + 页面留空时由代码兜底的）。"""
    out = []
    for g in groups:
        cid = g.get("cid")
        if not cid:
            continue
        spec = (g.get("persona") or "").strip()
        if spec:
            if spec == key:
                out.append((cid, g.get("alias") or cid[:8]))
            continue
        try:
            if _P().persona_key_for(cid) == key:
                out.append((cid, (g.get("alias") or cid[:8]) + "（代码默认）"))
        except Exception:                                       # noqa: BLE001
            pass
    return out


# ---------------------------------------------------------------- 预览

# 示例提示词：故意包含默认的全名/简称，好让"名字替换"看得见。
DEMO_SYSTEM = ("你是{DEFAULT_NAME_FULL}（群里大家叫你{DEFAULT_NAME_SHORT}），"
               "群里的小助手。回复要短、要口语，别端着。")


def persona_preview(cfg_raw, cid, try_msg="", persona_mod=None) -> dict:
    """按**草稿**渲染一遍：合成提示词 / 每条话术 / 体检 /（可选）真调一次模型。

    `cfg_raw` 就是页面表单解析出来的那份（含 personas 节）。
    用 `use_overrides()` 走 contextvars —— 不碰全局，别的请求读不到草稿。
    """
    P = persona_mod or _P()
    out = {"key": "", "cid": cid, "system": "", "phrases": {},
           "health": [], "reply": None, "error": "", "tried": bool(try_msg)}
    base = DEMO_SYSTEM.format(DEFAULT_NAME_FULL=P.DEFAULT_NAME_FULL,
                              DEFAULT_NAME_SHORT=P.DEFAULT_NAME_SHORT)
    draft = _personas_cfg(cfg_raw)
    with P.use_overrides(draft):
        try:
            P.set_cid(cid or "")
            out["key"] = P.persona_key_for(cid)
            out["system"] = P.apply_persona(base, cid)
            out["phrases"] = P.phrases_for(out["key"])
            out["health"] = P.health(out["key"])
            if try_msg:
                from brains import _call_llm
                out["reply"] = _call_llm(
                    base, "（试说一句，不要解释，直接说）" + try_msg,
                    max_tokens=200, timeout=45)
        except Exception as e:                                  # noqa: BLE001
            out["error"] = str(e)[:300]
        finally:
            try:
                P.set_cid("")
            except Exception:                                   # noqa: BLE001
                pass
    return out


# ---------------------------------------------------------------- 渲染

def render_personas(cfg_raw, groups, csrf="", error="", notice="", changes=(),
                    pending=False, reset_note="", preview=None, preview_cid="",
                    preview_msg="", persona_mod=None, status=None):
    """画人设与话术页。`cfg_raw` 里 `personas` 节 = 要**显示**的那一份（可能是草稿）。"""
    P = persona_mod or _P()
    ovs = _personas_cfg(cfg_raw)

    tab_labels, panes, pane_css = [], [], []
    for i, key in enumerate(P.PERSONAS):
        default = P.PERSONAS[key]
        ov = ovs.get(key) or {}
        dph = dict(P.PHRASES["nuo"])
        dph.update(P.PHRASES.get(key) or {})
        eph = effective_phrases(dph, ov)
        changed = []
        for f, label, _note in FIELDS:
            if effective_field(default, ov, f) != (default.get(f) or ""):
                changed.append(label)
        for pk, v in eph.items():
            if v != (dph.get(pk) or ""):
                changed.append(P.PHRASE_LABELS.get(pk, pk))

        badge = ("<span class='badge badge--info'>已改 {} 处</span>".format(len(changed))
                 if changed else "<span class='badge badge--ok'>未改</span>")
        in_use = _in_use(cfg_raw, key, groups)
        use_txt = ("<span class='text-dim'>用于：" + "、".join(a for _c, a in in_use)
                   + "</span>"
                   if in_use else "<span class='text-dim'>目前没有群在用它</span>")

        rows = []
        for f, label, note in FIELDS:
            cur = effective_field(default, ov, f)
            is_ch = cur != (default.get(f) or "")
            if f == "extra":
                cell = ("<textarea class='extra' name='ptext[{k}][extra]' "
                        "spellcheck=false>{v}</textarea>").format(k=_e(key), v=_e(cur))
            else:
                cell = ("<input type=text name='ptext[{k}][{f}]' value='{v}' "
                        "maxlength=40>").format(k=_e(key), f=_e(f), v=_e(cur))
            rows.append(
                "<tr class='{cls}'><td>{l}<br><span class='text-dim'>{n}</span></td>"
                "<td>{cell}</td></tr>".format(
                    cls="changed" if is_ch else "", l=_e(label), n=_e(note),
                    cell=cell))

        prows = []
        for pk in P.known_phrase_keys(key):
            cur = eph.get(pk, "")
            dflt = dph.get(pk) or ""
            is_ch = cur != dflt
            prows.append(
                "<tr class='{cls}'><td>{l}<br><span class='mono text-dim'>{k}</span></td>"
                "<td><textarea class='p' name='pphrase[{key}][{k}]' spellcheck=false>"
                "{v}</textarea></td>"
                "<td class='text-dim' title='{dt}'>{d}</td></tr>".format(
                    cls="changed" if is_ch else "",
                    l=_e(P.PHRASE_LABELS.get(pk, pk)), k=_e(pk), key=_e(key),
                    v=_e(cur), dt=_e(dflt), d=_e(_short(dflt, 30))))

        # 体检一律按**页面上的草稿**算（模块全局里那份可能是 bot 启动时的旧配置）
        health = _health_with_draft(P, key, ovs)
        hmsg = ("<div class='msg msg--bad'><b>体检：{} 个问题</b><ul>{}</ul></div>".format(
            len(health), "".join("<li>{}</li>".format(_e(x)) for x in health))
            if health else "<div class='msg msg--ok'>体检通过</div>")

        # 恢复按钮：主表单不能嵌套，用 HTML5 的 button form= 属性引用页面
        # 底部的隐藏小表单（纯 HTML、无 JS；见 content 模板尾部的 rforms）。
        reset_btn = ("<button class='btn btn--ghost btn--sm' type='submit' "
                     "form='pf-reset-{k}'>恢复「{l}」的全部默认</button>".format(
                         k=_e(key), l=_e(_label_of(P, key))))

        panes.append(
            "<div class='tab-panel ppane' id='ppane-{i}'>"
            "<h2>人设：{label} <span class='mono text-dim'>{key}</span>{badge}</h2>"
            "<div class='mb-2'>{use} {reset_btn}</div>{hmsg}"
            "<input type=hidden name='pseen[{key}]' value='1'>"
            "<h3>A · 人设文本</h3>"
            "<table class='table table--compact'>{rows}</table>"
            "<h3>B · 固定话术（这些句子不走 AI，直接发出去）</h3>"
            "<table class='table table--compact'><tr><th style='width:190px'>话术</th>"
            "<th>当前生效文本（可改）</th><th style='width:200px'>代码默认</th></tr>"
            "{prows}</table></div>".format(
                i=i, label=_e(_label_of(P, key)), key=_e(key), badge=badge,
                use=use_txt, reset_btn=reset_btn, hmsg=hmsg,
                rows="".join(rows), prows="".join(prows)))
        # tab 标签带改动徽章（一眼看出哪个人设动过）
        tab_labels.append(
            "<input type='radio' name='ptab' id='ptab-{i}' data-no-dirty{chk}>"
            "<label for='ptab-{i}' class='tab'>{l}{b}</label>".format(
                i=i, chk=" checked" if i == 0 else "",
                l=_e(_label_of(P, key)), b=badge))
        # CSS-only 切换（同功能页的 gtabs：radio + 兄弟选择器，无 JS 可用）
        pane_css.append("#ptab-%d:checked ~ #ppane-%d{display:block}" % (i, i))
        pane_css.append(
            ("#ptab-%d:checked ~ label[for='ptab-%d']" % (i, i))
            + "{color:var(--c-accent);border-bottom-color:var(--c-accent)}")

    # 恢复默认的小表单：本体藏在页面底部，pane 里的按钮经 form= 引用提交。
    # （表单不能嵌套，所以这些 form 必须在主 form 外。）
    rforms = []
    for key in P.PERSONAS:
        rforms.append(
            "<form id='pf-reset-{k}' method=post action='/personas/reset' "
            "class='sr-only'>"
            "<input type=hidden name='csrf' value='{c}'>"
            "<input type=hidden name='persona' value='{k}'>"
            "</form>".format(c=_e(csrf), k=_e(key)))
    rforms.append(
        "<form method=post action='/personas/reset' style='display:inline'>"
        "<input type=hidden name='csrf' value='{c}'>"
        "<input type=hidden name='persona' value='*'>"
        "<button class='btn btn--ghost btn--sm' type=submit>恢复全部人设的默认"
        "</button></form>".format(c=_e(csrf)))
    persona_tabs = "<div class='tabs ptabs'>{labels}{panes}</div>".format(
        labels="".join(tab_labels), panes="".join(panes))

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
                    "".join("<li>{}</li>".format(_e(x)) for x in changes)))
    if pending or status is not None:
        # P12-F：生效状态（与 /edit、/features 共用同一段话术）
        from admin import config_ui
        msg += config_ui.status_block(status, pending=True, csrf=csrf)

    pv = ""
    if preview is not None:
        pv = _render_preview(preview, cfg_raw, groups, csrf, preview_cid, preview_msg)

    # 预览用的目标群下拉（跟着主表单一起提交）
    opts = []
    for g in groups:
        cid = g.get("cid")
        if not cid:
            continue
        opts.append("<option value='{v}'{sel}>{a} · {c}</option>".format(
            v=_e(cid), c=_e(cid[:8]),
            a=_e(g.get("alias") or cid[:8]),
            sel=" selected" if cid == preview_cid else ""))

    # P4-4A：外壳走 admin.shell；页面特有样式经 extra_head 注入。
    from admin import shell
    extra_head = "<style>{}</style>".format(
        _CSS + ".ptabs{flex-wrap:wrap}.ptabs .ppane{display:none;width:100%}"
        + "".join(pane_css))
    content = ("{msg}{pv}"
            "<form method=post action='/personas'>"
            "<input type=hidden name='csrf' value='{csrf}'>"
            "<input type=hidden name='persona_form' value='1'>"
            "<div class='text-dim'>每格里显示的就是<b>当前生效的文本</b>。"

            "保存时逐格和代码默认比：一样 = 不写覆盖，不一样 = 写（<b>清空也算改动</b>）。"
            "想回默认，按各人设页签里的「恢复默认」。</div>{persona_tabs}"
            "<h2>预览与试说</h2>"
            "<div class='text-dim'>点「预览（不保存）」会用<b>你刚改的草稿</b>渲染一遍"
            "（合成后的提示词、每条话术的最终样子、体检结果）——"
            "不会写文件、也不会发到群里。</div>"
            "<div class='mt-2'>"
            "看哪个群：<select name='preview_cid'>{opts}</select> "
            "<label class='chk'><input type=checkbox name='preview_try' value='1'"
            "{trychk}> 顺便让模型试说一句（调一次模型，可能要十几秒）</label> "
            "<input class='input' type=text name='preview_msg' value='{pmsg}' maxlength=100 "
            "placeholder='对群里说的一句话（可留空）'></div>"
            "<div class='mt-6 flex items-center gap-4'>"
            "<button class='btn btn--primary' type=submit name='action' value='save'>保存</button> "
            "<button class='btn btn--ghost' type=submit name='action' value='preview'>"
            "预览（不保存）</button> "
            "<a href='/personas'>放弃改动重新载入</a>"
            "</div></form>"
            "<h2>恢复默认</h2>"
            "<div class='text-dim'>清掉这个人设的全部文本/话术覆盖，回到代码里写的那份。"
            "（只动人设，不影响群、Bot、功能开关）</div>"
            "<div class='mt-2'>{rforms}</div>"
            "<footer class='footer'>人设与话术改完约 1 秒自动生效（热加载，不用重启）· "
            "写入前自动备份 · 校验失败不落盘 · 没有 JavaScript 也能完成全部操作</footer>"
            ).format(
        msg=msg, pv=pv, csrf=_e(csrf), persona_tabs=persona_tabs,
        opts="".join(opts),
        trychk=" checked" if preview is not None and preview.get("tried") else "",
        pmsg=_e(preview_msg), rforms="".join(rforms))
    return shell.page(
        active="/personas", title="人设与话术",
        breadcrumb="运行时配置 / 人设与话术", content=content,
        extra_head=extra_head)


def _label_of(P, key):
    """给人看的名字：**必须带 key**。

    两个代码人设的 `name_full` 可能一样（玫瑰房的管家也叫"小小糯～"，
    屋主才是 Vv姐姐），只显示名字就分不出是哪个人设 —— 页面上会看到
    两节一模一样的标题。
    """
    nm = (P.PERSONAS.get(key) or {}).get("name_full") or ""
    return "{}（{}）".format(nm, key) if nm and nm != key else str(key)


def _health_with_draft(P, key, ovs):
    """体检要按**草稿**算（页面上还没保存的那份）。"""
    try:
        with P.use_overrides(ovs):
            return P.health(key)
    except Exception:                                           # noqa: BLE001
        return P.health(key)


def _render_preview(pv, cfg_raw, groups, csrf, preview_cid, preview_msg):
    alias = {g.get("cid"): (g.get("alias") or (g.get("cid") or "")[:8])
             for g in groups}
    key = pv.get("key") or "?"
    head = "预览 · 人设 {l}（{k}）· 群 {a}".format(
        l=_e(_label_of(_P(), key)), k=_e(key),
        a=_e(alias.get(pv.get("cid"), (pv.get("cid") or "")[:8])))
    body = []
    if pv.get("error"):
        body.append("<div class='msg msg--bad'>试说失败：{}</div>".format(_e(pv["error"])))
    body.append("<h3>1 · 合成后的系统提示词（人设那部分）</h3>"
                "<pre>{}</pre>".format(_e(pv.get("system"))))
    body.append("<h3>2 · 这条话术最终会发成什么（示例 3 条）</h3><pre>{}</pre>".format(
        _e("\n".join("{} → {}".format(k, _short(v, 60)) for k, v in
                     list((pv.get("phrases") or {}).items())[:3]))))
    health = pv.get("health") or []
    body.append("<h3>3 · 体检</h3>" + (
        "<div class='msg msg--bad'><b>{} 个问题</b><ul>{}</ul></div>".format(
            len(health), "".join("<li>{}</li>".format(_e(x)) for x in health))
        if health else "<div class='msg msg--ok'>体检通过</div>"))
    if pv.get("tried"):
        body.append("<h3>4 · 模型试说（没有发到群里）</h3><pre>{}</pre>".format(
            _e(pv.get("reply") or "（模型没返回内容）")))
    return ("<div class='msg msg--info'>{head}<div class='text-dim'>"
            "这是<b>草稿</b>的效果，还没有保存。</div></div>{body}").format(
        head=head, body="".join(body))


# ---------------------------------------------------------------- 表单 → 配置

def _s(form, key):
    v = form.get(key)
    if isinstance(v, list):
        v = v[0] if v else ""
    return v if v is not None else ""


def parse_personas(form, base, persona_mod=None) -> dict:
    """把这一页的表单解析成新的 `personas` 节。

    返回 `{"personas": {...}, "untouched": bool, "draft": {...}}`
    —— `draft` 是"页面显示的那一份"（预览时要原样画回去，别把用户刚打的字吃了）。

    规则见模块开头：逐格与**代码默认**比对，不同才写覆盖（空串也算不同）。
    """
    P = persona_mod or _P()
    if _s(form, "persona_form") != "1":
        # 不是这一页发的表单（旧页面/脚本/半截提交）⇒ 一个字都不动
        old = _personas_cfg(base)
        return {"personas": _copy(old), "untouched": True, "draft": _copy(old)}
    old = _personas_cfg(base)
    out = {}
    for key in P.PERSONAS:
        if _s(form, "pseen[{}]".format(key)) != "1":
            if old.get(key):
                out[key] = _copy(old[key])       # 这一节没被渲染 ⇒ 原样继承
            continue
        item = {}
        default = P.PERSONAS[key]
        for f, _label, _note in FIELDS:
            new = _s(form, "ptext[{}][{}]".format(key, f))
            if f == "extra":
                # 文本框的换行可能被 URL 编码成 \r\n —— 统一成 \n
                new = new.replace("\r\n", "\n").replace("\r", "\n")
            if new == (default.get(f) or ""):
                continue                          # 与代码默认一样 ⇒ 不写覆盖
            item[f] = new
        ph = {}
        for pk in P.known_phrase_keys(key):
            new = _s(form, "pphrase[{}][{}]".format(key, pk))
            new = new.replace("\r\n", "\n").replace("\r", "\n")
            if new == _default_phrase(P, key, pk):
                continue                          # 与代码默认一样 ⇒ 不写覆盖
            ph[pk] = new
        if ph:
            item["phrases"] = ph
        if item:
            out[key] = item
    return {"personas": out, "untouched": False, "draft": _copy(out)}


def _copy(v):
    import copy
    return copy.deepcopy(v)


def _default_phrase(P, key, pk):
    """话术的**代码默认**：该人设自己的，没有就用默认人设的。"""
    d = P.PHRASES.get(key) or {}
    if d.get(pk) is None:
        d = P.PHRASES["nuo"]
    return d.get(pk) or ""


def personas_diff(old: dict, parsed: dict, persona_mod=None) -> list:
    """人话改动清单（比较的是**最终文本**，不是"覆盖写没写"）。"""
    P = persona_mod or _P()
    old_ov = _personas_cfg(old)
    new_ov = parsed.get("personas") or {}
    out = []
    for key in P.PERSONAS:
        default = P.PERSONAS[key]
        a, b = old_ov.get(key) or {}, new_ov.get(key) or {}
        for f, label, _note in FIELDS:
            x = effective_field(default, a, f)
            y = effective_field(default, b, f)
            if x != y:
                out.append("人设「{}」· {}：{} → {}".format(
                    _label_of(P, key), label, _short(x, 30) or "（空）",
                    _short(y, 30) or "（空）"))
        dph = dict(P.PHRASES["nuo"])
        dph.update(P.PHRASES.get(key) or {})
        ea, eb = effective_phrases(dph, a), effective_phrases(dph, b)
        for pk in sorted(set(ea) | set(eb)):
            if (ea.get(pk) or "") != (eb.get(pk) or ""):
                out.append("人设「{}」· 话术「{}」：{} → {}".format(
                    _label_of(P, key), P.PHRASE_LABELS.get(pk, pk),
                    _short(ea.get(pk), 24) or "（空）",
                    _short(eb.get(pk), 24) or "（空）"))
    return out
