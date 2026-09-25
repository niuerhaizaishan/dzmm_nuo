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
           "unseen_deletions"]

AUDIT_NAME = "config_audit.log"

# 「字段没写」等于「取这个默认值」。只有 diff_summary 用它来消除假改动，
# 不参与真正落盘 —— 落盘的值由 runtime/config_store.py 决定，那边是唯一权威。
_GROUP_DEFAULTS = {"alias": "", "enabled": True, "mute": False,
                   "screen": False, "push": False, "persona": ""}
_BOT_DEFAULTS = {"name": "", "short": "", "cid": "", "token_env": "",
                 "persona": "", "enabled": True}

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


def render_edit(cfg_raw, error="", notice="", csrf="", pending=False,
                backups=(), audit=(), unsupported=(), changes=()):
    """把配置渲染成可编辑的表单。"""
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
        g_html.append(
            "<tr>"
            "<td><input type=text name='{p}[cid]' value='{cid}' placeholder='群 id'></td>"
            "<td><input type=text name='{p}[alias]' value='{alias}' placeholder='别名（只用于显示）'></td>"
            "<td>{en}</td><td>{mu}</td><td>{sc}</td><td>{pu}</td>"
            "<td><input type=text class=narrow name='{p}[persona]' value='{pe}' placeholder='（默认）'></td>"
            "</tr>".format(
                p=p, cid=_e(g.get("cid", "")), alias=_e(g.get("alias", "")),
                en=cb(p + "[enabled]", g.get("enabled", True)),
                mu=cb(p + "[mute]", g.get("mute", False)),
                sc=cb(p + "[screen]", g.get("screen", False)),
                pu=cb(p + "[push]", g.get("push", False)),
                pe=_e(g.get("persona", ""))))

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
            "<td><input type=text class=narrow name='{p}[persona]' value='{pe}' placeholder='（默认）'></td>"
            "<td>{en}</td>"
            "</tr>".format(
                p=p, k=_e(b.get("key", "")), n=_e(b.get("name", "")),
                s=_e(b.get("short", "")), c=_e(b.get("cid", "")),
                te=_e(b.get("token_env", "")), pe=_e(b.get("persona", "")),
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
    if pending:
        # ★ 这里**不能**写 `up -d`：改的只是挂载进去的 config.json，
        # compose 的"配置没变"判定只看 compose 文件/镜像，看不出挂载文件变了，
        # 于是 `up -d` 会打印 "Container ... Running" 然后**什么都不做** ——
        # 用户以为重启了，其实没生效。这是真踩过的坑，所以固定用 restart。
        msg += ("<div class='msg warn'>⚠️ <b>有改动尚未生效</b>：配置文件比 bot 进程新。"
                "改完要重启容器才会加载：<span class='mono'>"
                "cd /opt/dzmm &amp;&amp; docker compose restart dzmm-bot</span>"
                "<br><span class='dim'>用 <span class='mono'>up -d</span> 没用 —— "
                "改的是挂载文件，compose 看不出变化就直接跳过。"
                "改的是 <span class='mono'>.env</span> 时才需要 "
                "<span class='mono'>up -d --force-recreate</span>。"
                "重启会把你从这个后台登出（会话在进程内存里），重新登录即可。</span></div>")
    if unsupported:
        msg += ("<div class='msg warn'>⚠️ <b>这些改动暂时不会生效</b>：<br>{}</div>"
                .format("<br>".join(_e(x) for x in unsupported)))

    bk = "".join(
        "<li><span class='mono'>{}</span> &nbsp; <form method=post action='/edit/rollback' "
        "style='display:inline'><input type=hidden name='csrf' value='{c}'>"
        "<input type=hidden name='name' value='{n}'>"
        "<button class=ghost type=submit>回滚到这一版</button></form></li>".format(
            _e(n), c=_e(csrf), n=_e(n)) for n in backups) or "<li class='dim'>（还没有备份）</li>"

    au = "".join("<li class='mono'>{}</li>".format(_e(x)) for x in audit[-15:]) \
        or "<li class='dim'>（还没有改动记录）</li>"

    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>配置 · 小小糯 运维后台</title><style>{css}</style></head><body>"
            "<header><h1>小小糯 · 配置</h1>"
            "<span class='dim'>写入 config.json（校验不过一个字都不会写）</span>"
            "<span style='margin-left:auto'><a href='/'>← 回只读面板</a> · "
            "<a href='/logout'>退出</a></span></header><main>"
            "{msg}"
            "<form method=post action='/edit'>"
            "<input type=hidden name='csrf' value='{csrf}'>"
            # 记下这一版页面渲染时存在哪些条目 —— 保存时用它判断"提交的是不是旧页面"
            "<input type=hidden name='__groups_seen' value='{seen_g}'>"
            "<input type=hidden name='__bots_seen' value='{seen_b}'>"
            "<h2>群（{n} 个）—— 空行会被忽略；cid 填了就新增</h2>"
            "<table><tr><th>群 id</th><th>别名</th><th>启用</th><th>静默</th>"
            "<th>入群筛查</th><th>定时推送</th><th>人设</th></tr>{g}</table>"
            "<div class='dim'>静默 = 只读不回（不回复/不接指令/不筛查/不推送/不同步档案）；"
            "取消勾选「启用」= 完全不管这个群。</div>"
            "<h2>Bot（{m} 个）—— 空行会被忽略</h2>"
            "<table><tr><th>key</th><th>平台显示名</th><th>简称</th><th>绑定的群</th>"
            "<th>token 环境变量名</th><th>人设</th><th>启用</th></tr>{b}</table>"
            "<div class='dim'>token <b>不填在这里</b> —— 填它所在的<u>环境变量名</u>"
            "（如 DZMM_BOT_TOKEN），值留在服务器的 .env 里。"
            "一个群只能绑一个 Bot；两个 Bot 不能绑同一个群。</div>"
            "<div style='margin-top:16px'><button type=submit>保存配置</button> "
            "<a href='/edit' style='margin-left:10px'>放弃改动重新载入</a></div>"
            "</form>"
            "<h2>最近改动</h2><ul>{au}</ul>"
            "<h2>备份（可回滚）</h2><ul>{bk}</ul>"
            "</main><footer>配置改动<b>需要重启容器</b>才生效 · "
            "写入前自动备份 · 校验失败不落盘</footer></body></html>").format(
        css=_CSS, msg=msg, csrf=_e(csrf), g="".join(g_html), b="".join(b_html),
        au=au, bk=bk, n=len(groups), m=len(bots),
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


def parse_form(form) -> dict:
    """把扁平的表单字段还原成配置 dict。**空行忽略**。"""
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
            "persona": _s(form, p + "[persona]"),
            "enabled": _flag(form, p + "[enabled]"),
        }
        token = _s(form, p + "[token]")
        if token:
            bot["token"] = token
        bots.append(bot)

    return {"version": 1, "groups": groups, "bots": bots}


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
            for f in ("name", "short", "cid", "token_env", "persona", "enabled"):
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


def list_backups(backup_dir: Path, n: int = 10) -> list:
    try:
        return [p.name for p in sorted(Path(backup_dir).glob("config-*.json"))[-n:]][::-1]
    except Exception:                                           # noqa: BLE001
        return []


def restore_backup(backup_dir: Path, name: str):
    """读一版备份的内容（调用方负责校验后再 save）。"""
    p = Path(backup_dir) / os.path.basename(name or "")
    if not p.is_file():
        raise FileNotFoundError("找不到备份 {}".format(name))
    return json.loads(p.read_text(encoding="utf-8"))
