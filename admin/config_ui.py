"""运维后台的**配置编辑页**（P11 第 C 期 / v1）。

### 为什么单独一个模块

`dashboard.py` 的那些 section 是**只读**的，而且那份"只读"是结构性的、
有测试钉住（每一处 `sqlite3.connect` 都带 `mode=ro`、源码里不出现写库语句）。
配置页是**唯一**允许写的地方，而且只写 `config.json` 一个文件
（+ 它的备份 + 审计日志）。分开写，这条边界才看得清。

### 四道安全措施

1. **登录**：与只读面板同一个会话，没登录进不来。
2. **CSRF**：表单里带一个与会话绑定的随机 token；`SameSite=Strict` 的 cookie
   已经挡掉大部分跨站请求，token 再挡一层。
3. **校验后写**：交给 `config_store.save()` —— 校验不过**一个字都不写**。
4. **备份 + 审计 + 回滚**：每次保存前自动备份（config_store 负责），
   并在 `config_audit.log` 记一行"谁在什么时候改了什么"；页面能一键回滚。

### "未生效"提示

配置在**进程启动时**读入，所以改完要重启才生效。
页面用 `config.json` 的 mtime 和 **bot 进程的启动时刻**比较 ——
文件比进程新 ⇒ "有改动未生效"。这比让用户自己记可靠。
"""
import html
import json
import os
import time
from pathlib import Path

__all__ = ["render_edit", "parse_form", "diff_summary", "audit_log",
           "pending_change", "list_backups", "restore_backup",
           "unseen_deletions", "backup_path", "unified_diff",
           "render_backup_detail", "status_block"]

AUDIT_NAME = "config_audit.log"

# 「字段没写」等于「取这个默认值」。只有 diff_summary 用它来消除假改动，
# 不参与真正落盘 —— 落盘的值由 runtime/config_store.py 决定，那边是唯一权威。
_GROUP_DEFAULTS = {"alias": "", "enabled": True, "mute": False,
                   "screen": False, "push": False, "persona": ""}
_BOT_DEFAULTS = {"name": "", "short": "", "cid": "", "token_env": "",
                 "uid": "", "enabled": True}

_CSS = """
body{font:13px/1.6 -apple-system,Segoe UI,Roboto,'Noto Sans CJK SC',sans-serif;
margin:0;background:#12141a;color:#d8dee9}
header{padding:10px 16px;background:#1b1f2a;border-bottom:1px solid #2b3140;
display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600}
h2{font-size:13px;margin:18px 0 6px}
a{color:#7aa2f7}
.badge{padding:1px 7px;border-radius:9px;font-size:11px}
.ok{background:#1f3d2b;color:#7ee2a8}.bad{background:#4a1f27;color:#ff8b9a}
.warn{background:#463a1c;color:#ffd479}
main{padding:14px 16px;max-width:1400px}
table{border-collapse:collapse;width:100%;margin-bottom:6px}
th,td{padding:3px 5px;text-align:left;border-bottom:1px solid #232935;vertical-align:middle}
th{color:#8b95a7;font-weight:500;font-size:12px}
input[type=text]{width:100%;box-sizing:border-box;background:#0f1218;color:#d8dee9;
border:1px solid #2b3140;border-radius:5px;padding:4px 6px;font-size:12px}
input[type=text].narrow{max-width:120px}
input[type=checkbox]{transform:scale(1.1)}
button{padding:6px 14px;border:0;border-radius:6px;background:#2f6feb;color:#fff;
font-size:13px;cursor:pointer}
button.ghost{background:#2b3140}
.msg{padding:8px 11px;border-radius:6px;margin:10px 0}
.msg.bad{background:#4a1f27;color:#ff8b9a}
.msg.ok{background:#1f3d2b;color:#7ee2a8}
.msg.warn{background:#463a1c;color:#ffd479}
.msg.chg{background:#243044;color:#c8d6f0;border-left:3px solid #2f6feb}
.msg.chg ul{margin:4px 0 0 18px;padding:0}
pre{margin:0;white-space:pre-wrap;word-break:break-all;font-size:12px;
background:#0f1218;padding:6px;border-radius:5px;color:#a9b6c9}
.dim{color:#6c7686}.mono{font-family:ui-monospace,Consolas,monospace}
footer{padding:10px 16px;color:#6c7686;font-size:11px}
"""


def _e(v):
    return html.escape("" if v is None else str(v))


def status_block(status=None, pending=False, csrf="") -> str:
    """配置生效状态横幅（P12-F）：**已生效 / 下一轮自动生效 / 需要重启 / 文件坏了**。

    三个写配置的页面（`/edit`、`/features`、`/personas`）共用这一段，
    免得一处说"要重启"、另一处说"已生效" —— 那就等于没说。

    `status` 来自 `runtime.hotload.page_state()`（**在 bot 进程内**读运行期状态）；
    返回 None 表示这个进程没启用热加载（例如本地单独跑 dashboard），
    那就退回 P12-D 的旧提示：要重启。
    """
    btn = ("<form method=post action='/reload' style='display:inline'>"
           "<input type=hidden name='csrf' value='{c}'>"
           "<button type='submit' style='padding:2px 8px;font-size:11px;"
           "margin-left:8px'>立即热加载</button></form>").format(c=_e(csrf))
    if status is None:
        if not pending:
            return ""
        # ★ 这里**不能**写 `up -d`：改的只是挂载进去的 config.json，
        # compose 的"配置没变"判定只看 compose 文件/镜像，看不出挂载文件变了，
        # 于是 `up -d` 会打印 "Container ... Running" 然后**什么都不做** ——
        # 用户以为重启了，其实没生效。这是真踩过的坑，所以固定用 restart。
        return ("<div class='msg warn'>⚠️ <b>有改动尚未生效</b>：配置文件比 bot 进程新。"
                "改完要重启容器才会加载：<span class='mono'>"
                "cd /opt/dzmm &amp;&amp; docker compose restart dzmm-bot</span>"
                "<br><span class='dim'>用 <span class='mono'>up -d</span> 没用 —— "
                "改的是挂载文件，compose 看不出变化就直接跳过。"
                "改的是 <span class='mono'>.env</span> 时才需要 "
                "<span class='mono'>up -d --force-recreate</span>。"
                "重启会把你从这个后台登出（会话在进程内存里），重新登录即可。</span></div>")
    if status.get("invalid") or status.get("error"):
        return ("<div class='msg bad'>❌ <b>磁盘上的配置现在不合法</b>，运行期保持原样"
                "（没有半新半旧）：<span class='mono'>{}</span>"
                "<br><span class='dim'>在页面上改回去，或者从备份回滚，就会自动恢复。</span>"
                "</div>").format(_e(status.get("error")))
    if not status.get("enabled"):
        return ("<div class='msg warn'>热加载已关闭（<span class='mono'>{}"
                "</span>）：改完要重启容器才生效。</div>").format(
                    _e("DZMM_HOTLOAD=off"))
    if status.get("structural"):
        # 结构性改动**永远不会**被热加载应用 ⇒ 只要还有差异就一直提示，
        # 与"待生效"（会自己消失）不同。这里两种情况分开说，别让人误会。
        pend = status.get("pending")
        return ("<div class='msg warn'>⚠️ <b>磁盘上有「需要重启」的改动</b>{}："
                "<ul>{}</ul>"
                "<div class='dim'>功能开关 / 参数 / 人设文本与话术 / 提示词是热加载的，"
                "上面这些不是（它们决定 Socket.IO 订阅哪些群、主循环跑哪些群，"
                "启动时就固定了）。</div></div>").format(
                    "（还没处理，但热加载也不会应用它们）" if pend else "",
                    "".join("<li>{}</li>".format(_e(x))
                            for x in status["structural"]))
    if status.get("pending"):
        return ("<div class='msg chg'>✅ <b>改动已保存，正在热加载</b>："
                "下一次循环就会生效（约 1 秒），<b>不用重启</b>。{}"
                "</div>").format(btn)
    at = status.get("at_text") or ""
    return ("<div class='msg ok'>✅ <b>已生效</b>{}{}"
            "</div>").format(
                "（最近一次热加载 {}{}）".format(
                    _e(at), "，共 {} 次".format(status["count"])
                    if status.get("count") else "") if at else "",
                btn)



def render_edit(cfg_raw, error="", notice="", csrf="", pending=False,
                backups=(), audit=(), unsupported=(), changes=(),
                persona_names=(), persona_effective=None, status=None):
    """把配置渲染成可编辑的表单。

    `persona_names` = 代码里可用的人设名（来自 `personas.PERSONAS`）。
    页面上必须把它列出来 —— 否则用户只能靠猜，猜错了运行期会静默回落默认，
    又变成"看起来配了、其实没生效"。校验也会拒（见 config_store._parse）。

    `persona_effective` = {cid: 人设名}：**留空时实际会用哪个人设**（来自代码）。
    填进输入框的 placeholder，这样"这一格是空的"不等于"不知道会怎样"。
    """
    persona_hint = "、".join(persona_names) if persona_names else "(读取失败)"
    eff = dict(persona_effective or {})
    groups = cfg_raw.get("groups") or []
    bots = cfg_raw.get("bots") or []
    # 多留两行空行，方便"加一个"
    group_rows = list(groups) + [{}] * 2
    bot_rows = list(bots) + [{}] * 2

    def cb(name, checked):
        return ("<input type='checkbox' name='{}' value='1'{}>"
                .format(name, " checked" if checked else ""))

    g_html = []
    for i, g in enumerate(group_rows):
        p = "groups[{}]".format(i)
        _cid = str(g.get("cid") or "")
        # placeholder 显示"留空时会用哪个人设"（代码里的默认），避免空格子含义不明
        _eff = eff.get(_cid, "")
        _ph = "留空 = " + _eff if (not g.get("persona") and _eff) else "（默认）"
        g_html.append(
            "<tr>"
            "<td><input type=text name='{p}[cid]' value='{cid}' placeholder='群 id'></td>"
            "<td><input type=text name='{p}[alias]' value='{alias}' placeholder='别名（只用于显示）'></td>"
            "<td>{en}</td><td>{mu}</td><td>{sc}</td><td>{pu}</td>"
            "<td><input type=text class=narrow name='{p}[persona]' value='{pe}' placeholder='{ph}'></td>"
            "</tr>".format(
                p=p, cid=_e(g.get("cid", "")), alias=_e(g.get("alias", "")),
                en=cb(p + "[enabled]", g.get("enabled", True)),
                mu=cb(p + "[mute]", g.get("mute", False)),
                sc=cb(p + "[screen]", g.get("screen", False)),
                pu=cb(p + "[push]", g.get("push", False)),
                pe=_e(g.get("persona", "")), ph=_e(_ph)))

    b_html = []
    for i, b in enumerate(bot_rows):
        p = "bots[{}]".format(i)
        b_html.append(
            "<tr>"
            "<td><input type=text class=narrow name='{p}[key]' value='{k}' placeholder='key'></td>"
            "<td><input type=text name='{p}[name]' value='{n}' placeholder='平台显示名'></td>"
            "<td><input type=text class=narrow name='{p}[short]' value='{s}' placeholder='简称'></td>"
            "<td><input type=text name='{p}[cid]' value='{c}' placeholder='绑定的群 id'></td>"
            "<td><input type=text name='{p}[token_env]' value='{te}' placeholder='DZMM_BOT_TOKEN_XXX'></td>"
            # P12-H：Bot 在平台上的用户 id。留空也行（启动时按成员表里的名字认），
            # 但**必须能填**：它是"这条消息是不是我发的"判定的输入，填了就确定。
            "<td><input type=text name='{p}[uid]' value='{u}' placeholder='留空=按名字认'></td>"
            "<td>{en}</td>"
            "</tr>".format(
                p=p, k=_e(b.get("key", "")), n=_e(b.get("name", "")),
                s=_e(b.get("short", "")), c=_e(b.get("cid", "")),
                te=_e(b.get("token_env", "")), u=_e(b.get("uid", "")),
                en=cb(p + "[enabled]", b.get("enabled", True))))

    msg = ""
    if error:
        msg += "<div class='msg bad'>{}</div>".format(_e(error))
    if notice:
        msg += "<div class='msg ok'>{}</div>".format(_e(notice))
    if changes:
        # 为什么要把"改了什么"摊开列出来：这个页面是整表提交的，
        # 只要表单里有哪个勾没带上（客户端 bug、脚本拼 body、
        # 人为漏看），一存就会**顺手把别的群的开关也翻掉**，
        # 而页面上只写一句"已保存"根本看不出来。曾经真发生过：
        # 验收脚本没判 `checked`，一次保存把 4 个群的静默/筛查/推送
        # 全翻成 true。所以这里无条件逐条列出，不折叠、不省略。
        msg += ("<div class='msg chg'><b>本次改动 {} 项</b>"
                "（逐条列出，请核对是不是你想要的）：<ul>{}</ul></div>"
                .format(len(changes),
                        "".join("<li class='mono'>{}</li>".format(_e(x))
                                for x in changes)))
    if pending or status is not None:
        # P12-F：状态横幅交给共用函数（三个页面同一套话术）。
        # 有 status（bot 进程内读到运行期状态）时以它为准，`pending` 只是兜底。
        msg += status_block(status, pending=True, csrf=csrf)
    if unsupported:
        msg += ("<div class='msg warn'>⚠️ <b>这些改动暂时不会生效</b>：<br>{}</div>"
                .format("<br>".join(_e(x) for x in unsupported)))

    bk = "".join(
        "<li><span class='mono'>{}</span> &nbsp; "
        "<a href='/edit/backup?name={n}'>看差异</a> &nbsp; "
        "<form method=post action='/edit/rollback' "
        "style='display:inline'><input type=hidden name='csrf' value='{c}'>"
        "<input type=hidden name='name' value='{n}'>"
        "<button class=ghost type=submit>回滚到这一版</button></form></li>".format(
            _e(n), c=_e(csrf), n=_e(n)) for n in backups) \
        or "<li class='dim'>（还没有备份）</li>"

    au = "".join("<li class='mono'>{}</li>".format(_e(x)) for x in audit[-15:]) \
        or "<li class='dim'>（还没有改动记录）</li>"

    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>配置 · 小小糯 运维后台</title><style>{css}</style></head><body>"
            "<header><h1>小小糯 · 配置</h1>"
            "<span class='dim'>写入 config.json（校验不过一个字都不会写）</span>"
            "<span style='margin-left:auto'><a href='/'>← 回只读面板</a> · "
            "<a href='/features'>🧩 功能与参数</a> · "
            "<a href='/personas'>🎭 人设与话术</a> · "
            "<a href='/prompts'>📝 文案模板</a> · "
            "<a href='/logout'>退出</a></span></header><main>"
            "{msg}"
            "<form method=post action='/edit'>"
            "<input type=hidden name='csrf' value='{csrf}'>"
            # 记下这一版页面渲染时存在哪些条目 —— 保存时用它判断"提交的是不是旧页面"
            "<input type=hidden name='__groups_seen' value='{seen_g}'>"
            "<input type=hidden name='__bots_seen' value='{seen_b}'>"
            "<h2>群（{n} 个）—— 空行会被忽略；cid 填了就新增</h2>"
            "<table><tr><th>群 id</th><th>别名</th><th>启用</th><th>静默</th>"
            "<th>入群筛查</th><th>定时推送</th><th>人设（填人设名）</th></tr>{g}</table>"
            "<div class='dim'>静默 = 只读不回（不回复/不接指令/不筛查/不推送/不同步档案）；"
            "取消勾选「启用」= 完全不管这个群。"
            "人设留空 = 用默认；可用的人设名：<span class='mono'>{plist}</span>"
            "（由代码里的人设表决定，填不存在的名字会被拒绝）。</div>"
            "<h2>Bot（{m} 个）—— 空行会被忽略</h2>"
            "<table><tr><th>key</th><th>平台显示名</th><th>简称</th><th>绑定的群</th>"
            "<th>token 环境变量名</th><th>启用</th></tr>{b}</table>"
            "<div class='dim'>token <b>不填在这里</b> —— 填它所在的<u>环境变量名</u>"
            "（如 DZMM_BOT_TOKEN），值留在服务器的 .env 里。"
            "一个群只能绑一个 Bot；两个 Bot 不能绑同一个群。"
            "<br>人设<b>按群配</b>（见上表）：Bot 只是把文本送进群的通道，"
            "所以它自己没有单独的人设。</div>"
            "<div style='margin-top:16px'><button type=submit>保存配置</button> "
            "<a href='/edit' style='margin-left:10px'>放弃改动重新载入</a></div>"
            "</form>"
            "<h2>最近改动</h2><ul>{au}</ul>"
            "<h2>备份（可回滚）</h2><ul>{bk}</ul>"
            "</main><footer>功能/参数/人设改完自动热加载；<b>群与 Bot 需要重启容器</b> · "
            "写入前自动备份 · 校验失败不落盘</footer></body></html>").format(
        css=_CSS, msg=msg, csrf=_e(csrf), g="".join(g_html), b="".join(b_html),
        au=au, bk=bk, n=len(groups), m=len(bots), plist=_e(persona_hint),
        seen_g=_e(",".join(str(g.get("cid") or "") for g in groups if g.get("cid"))),
        seen_b=_e(",".join(str(b.get("key") or "") for b in bots if b.get("key"))))


# ---------------------------------------------------------------- 表单 → 配置

def _s(form, key):
    v = form.get(key)
    if isinstance(v, list):
        v = v[0] if v else ""
    return (v or "").strip()


def _seen(form, name) -> set:
    """页面渲染时"当时存在哪些条目"（隐藏字段里记着）。"""
    return {x for x in _s(form, name).split(",") if x}


def unseen_deletions(old: dict, new: dict, form) -> list:
    """会被**顺手删掉**、但这个页面从来没见过那些条目的情况。

    表单是整表提交，所以"配置里有、提交里没有"既可能是用户清空了 cid（想删），
    也可能是**提交的是一个旧页面**（另一个标签页；或者刚禁用某群、页面还没刷新
    就又点了保存）。后者会把配置里那几行整行删掉，连别名和人设一起没。

    判据：拿页面渲染时写下的隐藏字段 `__groups_seen` / `__bots_seen` 比对 ——
    配置里有、提交里没有、**而且渲染那一版页面时也没有** ⇒ 一定是旧页面，拒绝保存。
    返回人话列表；空列表表示可以保存。
    """
    out = []
    for what, listkey, idkey, seenkey in (
            ("群", "groups", "cid", "__groups_seen"),
            ("Bot", "bots", "key", "__bots_seen")):
        seen = _seen(form, seenkey)
        old_ids = {str(x.get(idkey)) for x in (old.get(listkey) or []) if x.get(idkey)}
        new_ids = {str(x.get(idkey)) for x in (new.get(listkey) or []) if x.get(idkey)}
        for gone in sorted(old_ids - new_ids):
            if gone not in seen:
                out.append("{} {}".format(what, gone[:8] if idkey == "cid" else gone))
    return out


def _flag(form, key):
    v = form.get(key)
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v).strip() in ("1", "on", "true", "yes")


def parse_form(form, base=None) -> dict:
    """把扁平的表单字段还原成配置 dict。**空行忽略**。

    `base` = 当前配置（`_raw_from_store()` 的结果）。**P12-C 起必须传**：
    schema v2 多了 `features`/`tuning`/`scopes` 三节，而这个表单页
    （P12-D 之前）只画了群和 Bot —— 不把这三节带过去，一次"改别名"的保存
    就会把它们**整节抹掉**（用户配好的开关与参数凭空消失）。
    这类静默丢失比报错难查得多，所以这里显式继承。
    """
    groups = []
    for i in range(200):
        p = "groups[{}]".format(i)
        if not any(k.startswith(p + "[") for k in form):
            if i > 0 and not any(k.startswith("groups[{}]".format(j) + "[")
                                 for j in range(i, i + 50) for k in form):
                break
            continue
        cid = _s(form, p + "[cid]")
        if not cid:
            continue                      # 空行忽略
        groups.append({
            "cid": cid,
            "alias": _s(form, p + "[alias]"),
            "enabled": _flag(form, p + "[enabled]"),
            "mute": _flag(form, p + "[mute]"),
            "screen": _flag(form, p + "[screen]"),
            "push": _flag(form, p + "[push]"),
            "persona": _s(form, p + "[persona]"),
        })

    bots = []
    for i in range(200):
        p = "bots[{}]".format(i)
        if not any(k.startswith(p + "[") for k in form):
            if i > 0 and not any(k.startswith("bots[{}]".format(j) + "[")
                                 for j in range(i, i + 50) for k in form):
                break
            continue
        key = _s(form, p + "[key]")
        if not key:
            continue                      # 空行忽略
        bot = {
            "key": key,
            "name": _s(form, p + "[name]"),
            "short": _s(form, p + "[short]"),
            "cid": _s(form, p + "[cid]"),
            "token_env": _s(form, p + "[token_env]"),
            "enabled": _flag(form, p + "[enabled]"),
        }
        uid = _s(form, p + "[uid]")
        if uid:
            bot["uid"] = uid          # P12-H：留空就不写（启动时按名字认）
        token = _s(form, p + "[token]")
        if token:
            bot["token"] = token
        bots.append(bot)

    from runtime.config_store import SCHEMA_VERSION
    out = {"version": SCHEMA_VERSION, "groups": groups, "bots": bots}
    # 这一版表单页还画不了 v2 的三节 ⇒ 从 base **原样继承**（见函数注释）。
    # P12-D 加上矩阵/参数表单后，这里会改成"从表单读、读不到才继承"。
    for sec in ("features", "tuning", "scopes"):
        if base and base.get(sec):
            out[sec] = base[sec]
    return out


def diff_summary(old: dict, new: dict) -> list:
    """人话版的改动摘要（只列变化，不列全文）。

    ⚠️ 两边都先**补默认值**再比。旧文件里没写的字段（比如早期 hand-write 的
    `config.json` 里没有 `bots[].enabled`）和表单提交上来的、显式写着
    `enabled=true` 的，其实是同一件事；不补默认值就会报出一条"改了"的假消息，
    然后白白多出一份备份 + 一行审计。真实症状：打开页面什么都不动直接点保存，
    页面说"1 项改动：Bot ... enabled: None -> True"。
    """
    out = []

    def norm(x, defaults):
        d = dict(x or {})
        for k, v in defaults.items():
            d.setdefault(k, v)
        return d

    def index(items, key, defaults):
        return {str(x.get(key) or ""): norm(x, defaults)
                for x in items if x.get(key)}

    og = index(old.get("groups") or [], "cid", _GROUP_DEFAULTS)
    ng = index(new.get("groups") or [], "cid", _GROUP_DEFAULTS)
    for cid in sorted(set(og) | set(ng)):
        a, b = og.get(cid), ng.get(cid)
        tag = (a or {}).get("alias") or (b or {}).get("alias") or ""
        if a is None:
            out.append("新增群 {} {}".format(cid[:8], tag))
        elif b is None:
            out.append("删除群 {} {}".format(cid[:8], tag))
        else:
            for f in ("alias", "enabled", "mute", "screen", "push", "persona"):
                if a.get(f) != b.get(f):
                    out.append("群 {} {} : {!r} -> {!r}".format(
                        cid[:8], f, a.get(f), b.get(f)))

    ob = index(old.get("bots") or [], "key", _BOT_DEFAULTS)
    nb = index(new.get("bots") or [], "key", _BOT_DEFAULTS)
    for k in sorted(set(ob) | set(nb)):
        a, b = ob.get(k), nb.get(k)
        if a is None:
            out.append("新增 Bot {}（绑 {}）".format(k, (b or {}).get("cid", "")[:8]))
        elif b is None:
            out.append("删除 Bot {}".format(k))
        else:
            for f in ("name", "short", "cid", "token_env", "enabled", "uid"):
                if a.get(f) != b.get(f):
                    out.append("Bot {} {} : {!r} -> {!r}".format(
                        k, f, a.get(f), b.get(f)))
            if ("token" in b) != ("token" in a):
                out.append("Bot {} token : {}".format(
                    k, "改为内联" if "token" in b else "改为走环境变量"))
    return out


def audit_log(path: Path, ip: str, summary: list) -> None:
    """追加一行审计。失败**不影响**保存本身（审计不是关键路径）。"""
    try:
        line = "{} ip={} {}".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), ip or "-",
            "；".join(summary) if summary else "（无变化）")
        with Path(path).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:                                           # noqa: BLE001
        pass


def read_audit(path: Path, n: int = 50) -> list:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        return lines[-n:]
    except Exception:                                           # noqa: BLE001
        return []


def pending_change(config_path: Path, bot_started_epoch) -> bool:
    """配置文件比 bot 进程新 ⇒ 改动尚未生效。

    比"让用户自己记"可靠。拿不到进程启动时刻就返回 False（不误报）。
    """
    try:
        if not bot_started_epoch:
            return False
        return Path(config_path).stat().st_mtime > float(bot_started_epoch) + 1
    except Exception:                                           # noqa: BLE001
        return False


def list_backups(backup_dir: Path, n: int = 0) -> list:
    """列出备份（新→旧）。

    `n=0`（默认）= **全部**。原来默认只列 10 份、而磁盘上留 20 份 ——
    改人设这种长文本会频繁写备份，"看得见的只有一半"等于把回滚点藏起来了一半。
    """
    try:
        items = sorted(Path(backup_dir).glob("config-*.json"))
        if n and n > 0:
            items = items[-n:]
        return [p.name for p in items][::-1]
    except Exception:                                           # noqa: BLE001
        return []


def backup_path(backup_dir: Path, name: str) -> Path:
    """把备份名安全地解析成路径（**只取 basename**，防目录穿越）。"""
    base = os.path.basename(str(name or ""))
    if not base or not base.startswith("config-") or not base.endswith(".json"):
        raise FileNotFoundError("备份名不合法：{!r}".format(name))
    p = Path(backup_dir) / base
    if not p.is_file():
        raise FileNotFoundError("找不到备份 {}".format(base))
    return p


def restore_backup(backup_dir: Path, name: str):
    """读一版备份的内容（调用方负责校验后再 save）。"""
    return json.loads(backup_path(backup_dir, name).read_text(encoding="utf-8"))


def unified_diff(old_text: str, new_text: str,
                 old_label: str = "备份", new_label: str = "当前") -> list:
    """逐行差异（unified diff）。

    为什么要有：备份列表只能说"回滚到这一版"，看不到**差在哪**。
    改人设/文案会天天写备份，没有差异就只能靠猜 —— 长文本尤其不能靠猜。
    """
    import difflib
    return list(difflib.unified_diff(
        str(old_text or "").splitlines(), str(new_text or "").splitlines(),
        fromfile=old_label, tofile=new_label, lineterm="", n=2))


def render_backup_detail(name: str, old_raw: dict, cur_raw: dict,
                         old_text: str, cur_text: str, csrf: str) -> str:
    """单份备份的详情页：人话改动摘要 + 逐行差异 + 「回滚到这一版」。"""
    summary = diff_summary(old_raw, cur_raw)
    lines = unified_diff(old_text, cur_text, "备份 " + name, "当前配置")

    def cls(ln):
        if ln.startswith("+++") or ln.startswith("---"):
            return "dim"
        if ln.startswith("@@"):
            return "hunk"
        if ln.startswith("+"):
            return "add"
        if ln.startswith("-"):
            return "del"
        return ""

    body = "".join(
        "<span class='{c}'>{t}</span>".format(c=cls(ln), t=_e(ln)) or "&nbsp;"
        for ln in (lines or ["（这一版与当前配置**逐字节相同**）"]))
    summ = "".join("<li class='mono'>{}</li>".format(_e(x)) for x in summary) \
        or "<li class='dim'>（人话摘要：无差异）</li>"
    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            "<title>备份 {n} · 小小糯</title><style>{css}"
            ".diff{{background:#0f1218;padding:8px;border-radius:6px;"
            "white-space:pre-wrap;word-break:break-all;font-size:12px;"
            "font-family:ui-monospace,Consolas,monospace}}"
            ".add{{color:#7ee2a8}}.del{{color:#ff8b9a}}.hunk{{color:#7aa2f7}}"
            ".dim{{color:#6c7686}}</style></head><body>"
            "<header><h1>备份 {n}</h1><span style='margin-left:auto'>"
            "<a href='/edit'>← 回配置页</a></span></header><main>"
            "<h2>这个备份与当前配置的差异</h2><ul>{summ}</ul>"
            "<div class='diff'>{body}</div>"
            "<form method=post action='/edit/rollback' style='margin-top:14px'>"
            "<input type=hidden name='csrf' value='{csrf}'>"
            "<input type=hidden name='name' value='{n}'>"
            "<button type=submit>回滚到这一版（当前配置会被覆盖，且先自动备份）</button>"
            "</form>"
            "<p class='dim'>回滚也要<b>重启容器</b>才生效。"
            "回滚本身同样会先备份当前配置，所以回滚也能再回滚。</p>"
            "</main></body></html>").format(
        n=_e(name), css=_CSS, summ=summ, body=body, csrf=_e(csrf))
