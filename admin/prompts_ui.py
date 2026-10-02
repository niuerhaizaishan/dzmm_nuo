# -*- coding: utf-8 -*-
"""文案模板页（P12-G）：**40 个提示词可编辑，改坏不可能保存成功**。

### 页面为什么是"列表 + 单个编辑器"而不是 40 个文本框

一份提示词最长 3.4KB（公演小说），40 份堆在一页上既有 100KB+ 的表单，
也把"提交整表"的爆炸半径放大了 —— 而这一页**每一条都是独立的小表单**
（隐藏字段只有 `key`），所以"编辑 A 却顺手把 B 改回去"这种事故在结构上不可能发生。

### 与配置页完全不同的三条纪律（都是被这一页的性质逼出来的）

1. **页面只写 `prompts/<NAME>.md`，永不改代码。** 文件在且非空才覆盖，
   否则退回代码默认值 —— 这套机制 P8 就有，"恢复默认"就是**删掉文件**。
2. **保存前必须过两道校验**：静态（必须保留的片段 / 片段最少出现次数）
   + 可选**契约自测**（真调一次模型，用固定样例检查 JSON/【1】【2】【3】/换行数）。
   任一不过 ⇒ **一个字都不写**（沿用 `config_store.save()` 那条"校验后写"的纪律）。
3. **改之前先留一份历史。** 每份提示词在 `prompts/prompts_backups/` 下按
   `<KEY>-<时间戳>.md` 保留最近 N 版，页面上能看逐行差异并回滚
   —— 长文本改错了，"整份 config.json 备份"这个粒度根本不够用。

### 生效

写完文件后**写一个热加载请求**（`config/hotload_request`），机器人进程约 1 秒就把
新的 prompt 重算进内存（P12-F 的机制）。所以这一页不需要重启。
"""
import html
import time
from pathlib import Path

__all__ = ["render_prompts", "prompt_file", "backup_dir", "list_backups",
           "write_prompt", "reset_prompt", "rollback_prompt", "backup_prompt",
           "render_history", "KEEP_BACKUPS"]

KEEP_BACKUPS = 20

# P4-4A：`_CSS` 大头已删（样式走 /static/base.css + components.css，外壳走
# admin.shell.page；badge/msg 等状态色用共享的 --ok/--warn/--bad 变体）。
# 下面只剩本页特有的 3 条：
#   · pre.reply —— 模型返回很长时限高滚动（共享 pre 没有高度上限）
#   · pre.diff  —— 逐行差异不能折行（共享 pre 是 pre-wrap）
#   · .changed  —— 「已改」行的左边框标记
_CSS = """
pre.reply{max-height:340px;overflow:auto}
pre.diff{white-space:pre;word-break:normal}
.changed{border-left:3px solid var(--c-accent);padding-left:6px}
"""


def _e(v):
    return html.escape("" if v is None else str(v))


def prompt_file(key):
    from runtime import prompts as RP
    return RP.prompt_dir() / (key + ".md")


def backup_dir():
    from runtime import prompts as RP
    return RP.prompt_dir() / "prompts_backups"


def list_backups(key):
    """该提示词的历史版本（新→旧）。"""
    d = backup_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob(key + "-*.md"), reverse=True):
        try:
            out.append({"name": p.name, "path": str(p),
                        "size": p.stat().st_size,
                        "at": p.name[len(key) + 1:-3]})
        except OSError:
            continue
    return out


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def backup_prompt(key, text=None):
    """把**当前文件**（或给定的 text）留一份历史。返回备份文件名（没有就 None）。

    ⚠️ 文件名要**保证唯一**：时间戳只到秒，同一秒内连写两次（脚本、连着点两下）
    会覆盖成一份 —— 而"每份保存都留历史"是这一页的核心承诺。
    """
    try:
        src = prompt_file(key)
        content = text if text is not None else (
            src.read_text(encoding="utf-8") if src.is_file() else None)
        if content is None:
            return None
        d = backup_dir()
        d.mkdir(parents=True, exist_ok=True)
        base = "{}-{}".format(key, _stamp())
        name = base + ".md"
        i = 1
        while (d / name).exists():
            i += 1
            name = "{}-{}.md".format(base, i)
        # ⚠️ 按**字节**写：`Path.write_text` 在 Windows 上会把 \n 翻成 \r\n，
        # 备份/覆盖文件就与代码默认值逐字节不同了（"文件与默认值一致"那条不变量
        # 会在 Windows 上莫名其妙地破掉）。Linux 上无所谓，但不能只在 Linux 上对。
        (d / name).write_bytes(content.encode("utf-8"))
        for old in sorted(d.glob(key + "-*.md"))[:-KEEP_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass
        return name
    except Exception:                                           # noqa: BLE001
        return None


def write_prompt(key, text):
    """原子写 `prompts/<KEY>.md`（先备份旧内容）。按**字节**写，别让换行被改写。"""
    from runtime import prompts as RP
    p = prompt_file(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    backup_prompt(key)
    tmp = p.with_suffix(".md.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    import os
    os.replace(str(tmp), str(p))
    RP.clear_cache()
    return p


def reset_prompt(key):
    """恢复代码默认值：把默认文本**写回** `prompts/<KEY>.md`。

    ⚠️ 为什么不是"删掉覆盖文件"（虽然 P8 的机制是"没文件就用默认值"）：
    本仓库的事实是 **`prompts/` 里本来就齐着 40 个文件**（它们是默认值的载体），
    "文件在且非空才覆盖"只是第二道保险。删掉一个文件会让"文件与登记册一一对应"
    这条不变量破掉（闸门会报"文件齐全/无孤儿"失败），而且下次部署上传时它又回来了。
    写回默认值的效果**完全一样**（内容逐字节等于代码默认值 ⇒ 行为相同），
    但"覆盖状态"是显式的：页面上显示"有覆盖文件/与默认一致"。
    """
    from runtime import prompts as RP
    default = RP.default_of(key)
    if not default:
        raise ValueError("{} 没有代码默认值，不能恢复".format(key))
    p = prompt_file(key)
    old = p.read_text(encoding="utf-8") if p.is_file() else None
    if old == default:
        return False                      # 本来就是默认值，什么都不用做
    if old is not None:
        backup_prompt(key)                # 先留一份改前的
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(default.encode("utf-8"))
    RP.clear_cache()
    return True


def rollback_prompt(key, version):
    """回滚到某个历史版本（会先把当前内容也留一份）。"""
    d = backup_dir()
    src = d / version
    # 只允许回滚这个 key 自己的版本（防路径穿越 / 串档）
    if src.parent != d or not src.name.startswith(key + "-") or not src.is_file():
        raise ValueError("历史版本不存在：{}".format(version))
    text = src.read_text(encoding="utf-8")
    bad = []
    try:
        from runtime import prompt_tiers as PT
        bad = PT.validate(key, text)
    except Exception:                                           # noqa: BLE001
        pass
    if bad:
        raise ValueError("这个历史版本过不了现在的校验：{}".format("；".join(bad)))
    write_prompt(key, text)
    return text


# ---------------------------------------------------------------- 渲染

# C1（自由文案，绿）/ C2（形状敏感，黄）/ C3（结构化输出，改坏即报错，红）
# —— 颜色语义沿用旧 _CSS 的 .c1/.c2/.c3，色值走共享 badge 变体。
_TIER_BADGE = {"C1": "badge--ok", "C2": "badge--warn", "C3": "badge--bad"}


def _tier_badge(tier):
    return "<span class='badge {}'>{}</span>".format(
        _TIER_BADGE.get(tier, "badge--neutral"), _e(tier))


def render_prompts(rows, selected=None, error="", notice="", changes=(),
                   preview=None, preview_user="", status_line="", csrf="",
                   history=()):
    """画文案模板页。

    `rows`：`runtime.prompt_tiers.all_specs()` + 每条的实际内容（由调用方装配）
    `selected`：要展开编辑的那一条（dict，含 key/text/...）
    """
    from runtime import prompt_tiers as PT
    groups = {}
    for r in rows:
        groups.setdefault(r["tier"], []).append(r)

    def _row(r):
        return ("<tr><td>{badge}<a href='/prompts?name={k}'>{t}</a>"
                "<br><span class='mono text-dim'>{k}</span></td>"
                "<td class='text-dim'>{note}</td>"
                "<td>{src}<br><span class='text-dim'>{n} 字 / {lines} 行</span>"
                "{chg}</td></tr>").format(
                    badge=_tier_badge(r["tier"]), k=_e(r["key"]), t=_e(r["title"]),
                    note=_e(r["note"] or ""),
                    src=("<span class='badge badge--warn'>有覆盖文件</span>"
                         if r["source"] == "file"
                         else "<span class='badge badge--ok'>代码默认</span>"),
                    n=r["chars"], lines=r["lines"],
                    chg=("<br><span class='badge badge--bad'>已改（与默认 {} 字不同）</span>"
                         if r.get("edited") else ""))

    ed = ""
    if selected:
        s = selected["spec"]
        mk = "、".join(s.must_keep) if s.must_keep else "（无）"
        if s.min_counts:
            mk += "；" + "、".join("{} ≥{} 次".format(a, b)
                                   for a, b in s.min_counts)
        contract = {
            "json_list": "必须是 JSON 数组（程序会 json.loads）",
            "json_objects": "必须是 JSON 数组，元素含 name / delta",
            "three_options": "必须给【1】【2】【3】三个选项",
            "lines": "逐行输出原文（程序按行比对）",
        }.get(s.contract, "无（自由文本）")
        hint = ("" if s.sample_user else
                "<div class='text-dim'>这一条没有自带样例：填一句你自己的输入就能试。</div>")
        ed = (
            "<h2>编辑：{t} <span class='mono text-dim'>{k}</span>{badge}</h2>"
            "<div class='text-dim'>{note}</div>"
            "<h3>这一条不能丢的东西</h3>"
            "<div class='mono'>{mk}</div>"
            "<h3>输出契约</h3><div>{contract}</div>"
            "{hint}"
            "<form method=post action='/prompts'>"
            "<input type=hidden name='csrf' value='{csrf}'>"
            "<input type=hidden name='key' value='{k}'>"
            "<h3>内容（现状：{src}；代码默认 {dlen} 字）</h3>"
            "<textarea name='text' rows='26' spellcheck=false>{text}</textarea>"
            "<h3>自测 / 预览（用固定样例或你自己的输入真调一次模型，不会发到群里）</h3>"
            "<div><input type=text name='user' value='{user}' maxlength=300 "
            "placeholder='{ph}'></div>"
            "<div style='margin-top:10px'>"
            "<button class='btn btn--primary' type=submit name='action' value='save'>保存</button> "
            "<button class='btn btn--ghost' type=submit name='action' value='preview'>"
            "预览（不保存）</button> "
            "<button class='btn btn--ghost' type=submit name='action' value='savetest'>"
            "保存并自测（调模型，几秒）</button> "
            "<a href='/prompts' style='margin-left:10px'>取消</a></div>"
            "</form>"
            "<form method=post action='/prompts/reset' style='display:inline'>"
            "<input type=hidden name='csrf' value='{csrf}'>"
            "<input type=hidden name='key' value='{k}'>"
            "<button class='btn btn--ghost btn--sm' type=submit>恢复代码默认（把默认文本写回去）"
            "</button></form>".format(
                t=_e(s.title), k=_e(s.key), badge=_tier_badge(s.tier),
                note=_e(s.note or ""), mk=_e(mk), contract=_e(contract),
                hint=hint, csrf=_e(csrf), src=("有覆盖文件 · 文件内容生效"
                                               if selected["source"] == "file"
                                               else "代码默认值生效"),
                dlen=selected["default_len"], text=_e(selected["text"]),
                user=_e(preview_user), ph=_e(
                    (s.sample_user or "对这群人说的一句话")[:80].replace("\n", " "))))

    pv = ""
    if preview:
        s = selected["spec"] if selected else None
        head = "自测 / 预览 · {}".format(_e(s.title) if s else "")
        if preview.get("skipped"):
            body = "<div class='msg msg--warn'>{}（这一条没有自测样例）</div>".format(
                _e(preview.get("note") or ""))
        else:
            st = preview.get("shape") or {}
            body = (
                "<div class='msg {cls}'><b>{verdict}</b>"
                "　<span class='text-dim'>返回 {n} 字 / 换行 {nl}</span>{issues}</div>"
                "<h3>模型返回</h3><pre class='reply'>{reply}</pre>").format(
                    cls="msg--ok" if preview.get("ok") else "msg--bad",
                    verdict="契约通过 ✓" if preview.get("ok") else "**没通过** ✗",
                    n=st.get("chars"), nl=st.get("newlines"),
                    issues=("" if not preview.get("issues") else
                            "<ul>{}</ul>".format("".join(
                                "<li>{}</li>".format(_e(x))
                                for x in preview["issues"]))),
                    reply=_e(preview.get("reply") or "（没有返回内容）"))
        pv = "<div class='msg msg--info'>{head}<div class='text-dim'>{note}</div></div>{body}".format(
            head=head,
            note="这是**草稿**的效果，还没有保存。" if not preview.get("saved")
            else "已保存并自测。",
            body=body)

    hist = ""
    if selected and history:
        rows_h = []
        for h in history:
            rows_h.append(
                "<tr><td class='mono'>{at}</td><td>{size} 字节</td>"
                "<td><a href='/prompts/history?name={k}&amp;version={v}'>看差异</a>"
                " &nbsp; "
                "<form method=post action='/prompts/rollback' style='display:inline'>"
                "<input type=hidden name='csrf' value='{c}'>"
                "<input type=hidden name='key' value='{k}'>"
                "<input type=hidden name='version' value='{v}'>"
                "<button class='btn btn--ghost btn--sm' type=submit>回滚到这一版</button>"
                "</form></td></tr>".format(
                    at=_e(h["at"]), size=h["size"], k=_e(selected["spec"].key),
                    v=_e(h["name"]), c=_e(csrf)))
        hist = ("<h3>历史版本（每份保留最近 {} 版）</h3>"
                "<table class='table table--compact'>"
                "<tr><th>时间</th><th>大小</th><th>操作</th></tr>{}</table>"
                ).format(KEEP_BACKUPS, "".join(rows_h))

    msg = ""
    if error:
        msg += "<div class='msg msg--bad'>{}</div>".format(_e(error))
    if notice:
        msg += "<div class='msg msg--ok'>{}</div>".format(_e(notice))
    if changes:
        msg += ("<div class='msg msg--info'><b>本次改动</b><ul>{}</ul></div>").format(
            "".join("<li>{}</li>".format(_e(x)) for x in changes))
    if status_line:
        msg += "<div class='msg msg--ok'>{}</div>".format(_e(status_line))

    # P4-4B（屋主）：按 tier 收进页签，不平铺。selected 的编辑器/预览/历史
    # 放进它所属 tier 的 pane，并让那个页签默认激活（服务端渲染 checked，
    # 纯 CSS 切换、无 JS 可用——同功能页/人设页的机制）。
    sel_tier = selected["spec"].tier if selected else None
    tier_labels, tpanes, tpane_css = [], [], []
    for i, tier in enumerate(PT.TIERS):
        items = groups.get(tier) or []
        if not items:
            continue
        listing = (
            "<h2>{label} <span class='text-dim'>（{n} 个）</span></h2>"
            "<div class='text-dim'>{note}</div>"
            "<table class='table table--compact'>"
            "<tr><th style='width:280px'>提示词</th>"
            "<th>改坏的后果 / 用途</th><th style='width:190px'>状态</th></tr>"
            "{rows}</table>".format(
                label=_e(PT.TIER_LABEL[tier]), n=len(items),
                note=_e(PT.TIER_NOTE[tier]),
                rows="".join(_row(r) for r in items)))
        # 编辑器/预览/历史只属于 selected 所在的 tier；其余页签只有清单
        inner = (pv + ed + hist + listing) if (selected and sel_tier == tier) \
            else listing
        tpanes.append(
            "<div class='tab-panel tierpane' id='tierpane-{i}'>{inner}</div>".format(
                i=i, inner=inner))
        tier_labels.append(
            "<input type='radio' name='tiertab' id='tiertab-{i}' data-no-dirty{chk}>"
            "<label for='tiertab-{i}' class='tab'>{label}（{n}）</label>".format(
                i=i,
                chk=" checked" if (sel_tier == tier or (not sel_tier and i == 0))
                else "",
                label=_e(PT.TIER_LABEL[tier]), n=len(items)))
        tpane_css.append(
            "#tiertab-%d:checked ~ #tierpane-%d{display:block}" % (i, i))
        tpane_css.append(
            ("#tiertab-%d:checked ~ label[for='tiertab-%d']" % (i, i))
            + "{color:var(--c-accent);border-bottom-color:var(--c-accent)}")
    tier_tabs = "<div class='tabs tier-tabs'>{labels}{panes}</div>".format(
        labels="".join(tier_labels), panes="".join(tpanes))

    # P4-4A：页面外壳（侧边栏/header/主题切换/CSS）由 admin.shell 统一提供，
    # 这里只产出 content 片段。
    from admin import shell
    extra_head = "<style>{}</style>".format(
        _CSS + ".tier-tabs{flex-wrap:wrap}.tier-tabs .tierpane"
        "{display:none;width:100%}" + "".join(tpane_css))
    content = ("{msg}{tier_tabs}"
               "<footer class='footer'>40 个提示词 · 只写 prompts/*.md（永不改代码）· "
               "保存后约 1 秒自动生效（热加载，不用重启）· 改动前自动留历史版本 · "
               "校验/自测不过不落盘 · 没有 JavaScript 也能完成全部操作</footer>"
               ).format(msg=msg, tier_tabs=tier_tabs)
    return shell.page(active="/prompts", title="提示词",
                      breadcrumb="运行时配置 / 提示词",
                      content=content,
                      extra_head=extra_head)


def render_history(key, title, version, old_text, cur_text, csrf=""):
    """一个历史版本与当前内容的逐行差异（+ 回滚按钮）。"""
    from admin import config_ui
    from admin import shell
    diff = config_ui.unified_diff(old_text, cur_text,
                                  "历史版本 " + version, "当前 prompts/{}.md".format(key))
    content = (
        "<p><a class='btn btn--ghost btn--sm' "
        "href='/prompts?name={k}'>← 回到编辑</a></p>"
        "<div class='text-dim'>上：历史版本 {v}　下：当前 prompts/{k}.md</div>"
        "<pre class='diff'>{diff}</pre>"
        "<form method=post action='/prompts/rollback'>"
        "<input type=hidden name='csrf' value='{c}'>"
        "<input type=hidden name='key' value='{k}'>"
        "<input type=hidden name='version' value='{v}'>"
        "<button class='btn btn--primary' type=submit>回滚到这个版本</button></form>"
        ).format(k=_e(key), v=_e(version), diff=_e(diff), c=_e(csrf))
    return shell.page(active="/prompts", title="{} · 差异".format(title),
                      breadcrumb="运行时配置 / 提示词 / 差异",
                      content=content,
                      extra_head="<style>{}</style>".format(_CSS))
