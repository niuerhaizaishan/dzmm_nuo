"""认证群聊天记录只读页面。"""
import html
import math
from datetime import datetime
from urllib.parse import urlencode

from admin import shell
from runtime.chat_archive_query import LOCAL_ZONE, search
from runtime.chat_archive import ARCHIVE_GROUPS
from runtime.ctx import PRIMARY_CID


def _escape(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _date(value):
    return datetime.fromtimestamp(value, LOCAL_ZONE).strftime("%Y-%m-%d %H:%M:%S") if value is not None else "—"


def render(parameters=None, *, path=None):
    parameters = parameters or {}
    error = ""
    try:
        data = search(parameters, path=path)
    except ValueError as exception:
        error = str(exception)
        data = None
    except Exception:
        error = "聊天记录暂时读不了，请稍后重试。"
        data = None
    selected = str(parameters.get("group_id", PRIMARY_CID))
    options = "".join("<option value='{}'{}>{}</option>".format(
        _escape(cid), " selected" if cid == selected else "", _escape(label))
        for cid, label in ARCHIVE_GROUPS.items())
    fields = ["<label>归档群<select class='input' name='group_id'>" + options + "</select></label>"]
    for key, label, kind in (("start", "开始日期", "date"), ("end", "结束日期", "date"),
                             ("name", "昵称包含", "text"), ("uid", "发送者 UID", "text"),
                             ("q", "消息关键词", "text")):
        fields.append("<label>{}<input class='input' type='{}' name='{}' value='{}'></label>".format(
            label, kind, key, _escape(str(parameters.get(key, ""))[:256])))
    content = ("<h1>群消息 · 聊天记录</h1>"
               "<p class='text-dim'>按所选群独立查询；昵称按每条记录保存，UID 用于区分同名和改名。日期使用北京时间。"
               "不自动清理，不在此页面发送消息。</p>"
               "<form method='get' action='/messages' class='archive-filters'>" + "".join(fields)
               + "<button class='btn btn--primary' type='submit'>查询</button>"
               "<a href='/messages' class='btn btn--ghost'>重置</a></form>")
    if error:
        content += "<p role='alert'>" + _escape(error) + "</p>"
    elif not data["available"]:
        content += "<p>尚无本地归档。历史导入或开始采集后，可在这里查询。</p>"
    else:
        pages = max(1, math.ceil(data["matches"] / data["page_size"]))
        content += "<p>共 {} 条记录，匹配 {} 条；第 {} / {} 页。</p><p class='text-dim'>已保存范围：{} 至 {}。</p>".format(
            data["total"], data["matches"], data["filters"]["page"], pages,
            _date(data["first_at"]), _date(data["last_at"]))
        rows = []
        sources = {"historical_export": "导出文件中的昵称，未核实其生效时间", "message": "消息携带的昵称",
                   "member_cache": "采集时的成员昵称缓存", "unknown": "暂无昵称"}
        for record in data["rows"]:
            nickname = record["sender_name"] or "昵称未知"
            sender_query = {**data["filters"], "uid": record["sender_id"], "name": "", "page": 1}
            body = _escape(record["text"])
            if len(record["text"]) > 500:
                body = "<details><summary>" + _escape(record["text"][:200]) + "… 展开全文</summary>" + body + "</details>"
            rows.append("<tr><td>{}</td><td><span title='{}'>{}</span><br><a href='{}'><small>{}</small></a>"
                        "</td><td>{}</td><td class='archive-text'>{}</td></tr>".format(
                            _date(record["sent_at"]), _escape(sources.get(record["name_source"], "")),
                            _escape(nickname), _escape("/messages?" + urlencode(sender_query)),
                            _escape(record["sender_id"] or "系统/未知发送者"), _escape(record["message_type"]), body))
        content += ("<div class='archive-table'><table><thead><tr><th>时间</th><th>发送者</th>"
                    "<th>类型</th><th>内容</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
        if not rows:
            content += "<p>没有匹配的消息。</p>"
        for delta, label in ((-1, "上一页"), (1, "下一页")):
            page = data["filters"]["page"] + delta
            if 1 <= page <= pages:
                target = "/messages?" + urlencode({**data["filters"], "page": page})
                content += "<a class='btn btn--ghost' href='{}'>{}</a> ".format(_escape(target), label)
    styles = ("<style>.archive-filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:20px 0}"
              ".archive-filters label{display:flex;flex-direction:column;gap:6px}.archive-table{overflow:auto}"
              ".archive-table table{width:100%}.archive-table td{vertical-align:top}"
              ".archive-text{white-space:pre-wrap;overflow-wrap:anywhere;min-width:220px;max-width:700px}"
              ".archive-table small{overflow-wrap:anywhere}</style>")
    return shell.page(active="/messages", title="群聊天记录", breadcrumb="监控 / 群聊天记录",
                      content=content, extra_head=styles)
