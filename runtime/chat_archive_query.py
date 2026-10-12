"""认证群归档只读查询，不创建数据库或触发平台接口。"""
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from runtime.chat_archive import archive_path
from runtime.ctx import PRIMARY_CID
from runtime.text import _normalize_cn

LOCAL_ZONE = timezone(timedelta(hours=8))
PAGE_SIZE = 50

# 2026-10-07 屋主：「我的故事 / 我的画像，少了很多成人向的、色色的、露骨的聊天内容。」
#
# 这一层是**取样**，不是写作。每个时间桶只取 12 条，排序是
#   ① 命中关键词优先 → ② 越长越前
# 而关键词表里原来一个涩涩/调教词都没有 ⇒ 露骨发言只能靠"够长"挤进来。
#
# 真库实测（认证群 11,363 条合格发言，其中 2,017 条涉涩涩）——按人统计样本里
# 涉涩涩条数占比：
#     故事取样  6% / 12% / 17% / 17%   →  加词后 54% / 38% / 26% / 52%
#     画像取样  5% / 12% / 17% / 14%   →  加词后 57% / 37% / 28% / 51%
#
# ⚠️ 两层都要放行才看得见内容：这里决定"有没有被取样到"，
#    `prompts/PERSONAL_*_PROMPT.md` 决定"写不写"。只改一层 = 白改。
_ADULT_KEYWORDS = ("骚", "发骚", "母狗", "母狗化", "调教", "主人", "命令", "发情",
                   "淫", "穴", "跪", "项圈", "露出", "惩罚", "高潮", "呻吟",
                   "舔", "湿", "性癖", "XP", "小M", "小S", "自慰", "内裤",
                   "绳缚", "鞭", "束缚", "脱", "摸", "胸", "腿", "奴")


def filters(parameters):
    result = {key: str(parameters.get(key, "")).strip() for key in ("q", "name", "uid", "start", "end")}
    if len(result["q"]) > 256 or len(result["name"]) > 128 or len(result["uid"]) > 128:
        raise ValueError("查询条件过长")
    try:
        result["page"] = int(parameters.get("page", "1"))
        if not 1 <= result["page"] <= 100000:
            raise ValueError
        for key in ("start", "end"):
            if result[key]:
                date.fromisoformat(result[key])
        if result["start"] and result["end"] and result["start"] > result["end"]:
            raise ValueError
    except ValueError as error:
        raise ValueError("日期或页码不正确") from error
    return result


def search(parameters=None, *, path=None):
    selected = filters(parameters or {})
    database = Path(path or archive_path())
    result = {"available": database.is_file(), "filters": selected, "total": 0, "matches": 0,
              "page_size": PAGE_SIZE, "rows": [], "first_at": None, "last_at": None}
    if not result["available"]:
        return result
    clauses = ["group_id = ?"]
    values = [PRIMARY_CID]
    for key, column in (("q", "text"), ("name", "sender_name")):
        if selected[key]:
            clauses.append("instr(" + column + ", ?) > 0")
            values.append(selected[key])
    if selected["uid"]:
        clauses.append("sender_id = ?")
        values.append(selected["uid"])
    for key, operator in (("start", ">="), ("end", "<")):
        if selected[key]:
            day = date.fromisoformat(selected[key])
            if key == "end":
                day += timedelta(days=1)
            clauses.append("sent_at " + operator + " ?")
            values.append(datetime.combine(day, time.min, LOCAL_ZONE).timestamp())
    where = " AND ".join(clauses)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            connection.execute("BEGIN")
            result["total"], result["first_at"], result["last_at"] = connection.execute(
                "SELECT count(*), min(sent_at), max(sent_at) FROM messages WHERE group_id = ?", (PRIMARY_CID,)).fetchone()
            result["matches"] = connection.execute("SELECT count(*) FROM messages WHERE " + where, values).fetchone()[0]
            result["rows"] = [dict(row) for row in connection.execute(
                "SELECT message_id,sender_id,sender_name,name_source,sent_at,message_type,text,source FROM messages WHERE "
                + where + " ORDER BY sent_at DESC, message_id DESC LIMIT ? OFFSET ?",
                values + [PAGE_SIZE, (selected["page"] - 1) * PAGE_SIZE])]
    return result


def story_evidence(uid, *, start="", end="", path=None, focus="story"):
    """本人文字按时间分层限量取样；不创建库、不读取其他人的消息。"""
    selected = filters({"uid": uid, "start": start, "end": end})
    if not selected["uid"]:
        raise ValueError("missing story uid")
    if focus not in ("story", "portrait"):
        raise ValueError("unknown evidence focus")
    keywords = ("上班", "下班", "工资", "工作", "上课", "学校", "宿舍", "放假", "旅游",
                "旅行", "海边", "看海", "看一次海", "写了", "画了", "生日", "开心", "难过", "烦躁")
    if focus == "portrait":
        keywords = ("相声", "音乐", "诗", "写卡", "角色", "团本", "研究", "尝试", "建议", "意见",
                    "欢迎", "好久", "身体好", "开心", "谢谢", "睡觉", "出门", "无聊", "工作完成")
    keywords = keywords + _ADULT_KEYWORDS
    priority = " OR ".join("instr(text, ?)" for keyword in keywords)
    database = Path(path or archive_path())
    result = {"available": database.is_file(), "start": "", "end": "", "messages": [], "total": 0}
    if not result["available"]:
        return result
    clauses = ["group_id = ?", "sender_id = ?", "message_type = 'text'",
               "length(trim(text)) >= 4", "length(text) <= 2000",
               "substr(trim(text), 1, 1) NOT IN ('/', '#')", "trim(text) NOT LIKE 'http%'",
               "trim(text) NOT LIKE '{%'", "text NOT LIKE '%🤖%'", "text NOT LIKE '%[[%'",
               "text NOT LIKE '%]]%'", "text NOT LIKE '%://%'", "text NOT LIKE '%@%'",
               "message_id NOT GLOB '*[^A-Za-z0-9_-]*'", "length(message_id) BETWEEN 1 AND 64"]
    values = [PRIMARY_CID, selected["uid"]]
    for key, operator in (("start", ">="), ("end", "<")):
        if selected[key]:
            day = date.fromisoformat(selected[key])
            if key == "end":
                day += timedelta(days=1)
            clauses.append("sent_at " + operator + " ?")
            values.append(datetime.combine(day, time.min, LOCAL_ZONE).timestamp())
    where = " AND ".join(clauses)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        with connection:
            connection.execute("BEGIN")
            total, first_at, last_at = connection.execute(
                "SELECT count(*), min(sent_at), max(sent_at) FROM messages WHERE " + where, values).fetchone()
            result["total"] = total
            if not total:
                return result
            result["start"] = datetime.fromtimestamp(first_at, LOCAL_ZONE).date().isoformat()
            result["end"] = datetime.fromtimestamp(last_at, LOCAL_ZONE).date().isoformat()
            bucket_seconds = max((last_at - first_at + 1) / 12, 1)
            rows = connection.execute(
                "WITH ranked AS (SELECT message_id, sent_at, text, ROW_NUMBER() OVER ("
                "PARTITION BY CAST((sent_at - ?) / ? AS INTEGER) ORDER BY "
                "CASE WHEN " + priority + " THEN 1 ELSE 0 END DESC, "
                "min(length(text), 160) DESC, sent_at, message_id) AS position "
                "FROM messages WHERE " + where + ") "
                "SELECT message_id, sent_at, text FROM ranked WHERE position <= 12 "
                "UNION SELECT message_id, sent_at, text FROM (SELECT message_id, sent_at, text "
                "FROM messages WHERE " + where + " ORDER BY sent_at, message_id LIMIT 1) "
                "UNION SELECT message_id, sent_at, text FROM (SELECT message_id, sent_at, text "
                "FROM messages WHERE " + where + " ORDER BY sent_at DESC, message_id DESC LIMIT 1) "
                "ORDER BY sent_at, message_id LIMIT 146",
                [first_at, bucket_seconds] + list(keywords) + values + values + values).fetchall()
    # `chars` 是给模型看的**字数成本**：程序把 [[id]] 换成这条摘录的原文，
    # 所以引文占多少字，只有这条摘录自己知道。不给它，模型就只能瞎猜 ——
    # 2026-10-07 实测：加了成人内容之后模型的引文变长，成品直接撑爆上限，
    # `render_story` 抛 story length ⇒ 用户只看到「糯糯这次没整理好」。
    # 摘录本身中位数才 16 字（127/146 条 ≤30 字），料是够的，缺的只是这个数字。
    remaining = 24000
    for message_id, stamp, text in rows:
        excerpt = text.strip()[:160]
        if len(excerpt) > remaining:
            continue
        result["messages"].append({"message_id": message_id,
                                   "day": datetime.fromtimestamp(stamp, LOCAL_ZONE).date().isoformat(),
                                   "text": excerpt, "clipped": len(text.strip()) > 160,
                                   "chars": len(excerpt)})
        remaining -= len(excerpt)
    return result


def portrait_evidence(uid, *, start="", end="", path=None):
    return story_evidence(uid, start=start, end=end, path=path, focus="portrait")


def find_senders(query, *, path=None, limit=8):
    """在归档里按昵称找发送者 —— **只读、零 API、不猜**。

    `/致某某` 要用：写信必须有收信人本人的发言可取证，所以"在归档里能不能找到这个人"
    和"找得到几个人"是同一次查询。返回 `[{"uid","name","names","messages","last"}, ...]`，
    按发言条数从多到少；**两个以上候选一律交回调用方去问用户，绝不取第一个**。

    匹配优先级对齐 `runtime.members._find_uid_by_name`：精确（归一化后）→ 前缀 → 子串
    （**单字不参与子串匹配**，「裳」不许命中「裳姐姐」）。档案里的当前昵称也算数，
    所以改过名的人不会因为归档里是旧名字而找不到。
    """
    text = (query or "").strip()
    if not text:
        return []
    database = Path(path or archive_path())
    names: dict = {}
    counts: dict = {}
    lasts: dict = {}
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            for uid, name, total, last in connection.execute(
                    "SELECT sender_id, sender_name, count(*), max(sent_at) FROM messages "
                    "WHERE group_id = ? GROUP BY sender_id, sender_name", (PRIMARY_CID,)):
                names.setdefault(uid, set()).add(name or "")
                counts[uid] = counts.get(uid, 0) + total
                lasts[uid] = max(lasts.get(uid, 0), last or 0)
    try:
        from profiles import load_profiles
        for uid, profile in load_profiles().items():
            current = (profile or {}).get("name") or ""
            if current:
                names.setdefault(uid, set()).add(current)
    except Exception:                                           # noqa: BLE001
        pass
    if not names:
        return []
    wanted = _normalize_cn(text)
    stages = (lambda name: _normalize_cn(name) == wanted,
              lambda name: _normalize_cn(name).startswith(wanted),
              lambda name: len(wanted) >= 2 and wanted in _normalize_cn(name))
    for matches in stages:
        hits = []
        for uid, alias in names.items():
            alias = {a for a in alias if a}
            if not alias:
                continue
            if any(matches(a) for a in alias):
                hits.append({"uid": uid,
                             "name": sorted(alias, key=len)[0],
                             "names": sorted(alias),
                             "messages": counts.get(uid, 0),
                             "last": lasts.get(uid, 0)})
        if hits:
            hits.sort(key=lambda item: (-item["messages"], item["name"]))
            return hits[:limit]
    return []
