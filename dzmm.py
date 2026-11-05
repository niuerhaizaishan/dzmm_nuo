"""
DZMM Core — cookie from Chrome + tRPC API.
"""
import json, urllib.parse
from datetime import datetime, timezone
import requests
import token_store
import env_config

# P5.2 出站限流（令牌桶 + 冷却）。runtime 包缺失时退化为不限流。
from throttle_guard import acquire as _throttle, note_response as _note_resp

ORIGIN = env_config.get_dzmm_origin()

# 代理地址走环境变量 DZMM_PROXY（默认 127.0.0.1:7890；云端香港/新加坡直连设空字符串）
PROXIES = env_config.get_proxies()

# P12-BL：所有非浏览器请求统一用**浏览器 UA**（依据见 `api()` 里的注释与
# `tools/probe_ua_wall.py` 的实测：WAF 按 UA 挑战，`Python-urllib/x` 会被 403）。
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def get_cookies() -> dict:
    return token_store.get_cookie_dict()


def get_cookie_string() -> str:
    return token_store.get_cookie_string()


def api(path: str, inp: dict, cookies: dict | None = None,
        timeout: float = 15) -> dict:
    """调用 tRPC API。

    `timeout` 是**交给 requests 自己的**超时（P12-J）。为什么必须能传：
    主循环用 `asyncio.wait_for` 包住 `to_thread(get_messages…)` 时，等不到只是
    "放弃等待"，**线程还在跑**；这台机器 2 核，`to_thread` 默认池上限
    `min(32, cpu+4)=5`，几个卡住的请求就能把池子占满 ⇒ 之后所有 `to_thread`
    （包括 LLM 生成）一起排队（2026-09-27 实测：机器人静默 11 分钟、
    webhook 消息在队列里躺了 5 分钟没人处理）。超时交给 requests，线程能及时归还。
    """
    if cookies is None:
        cookies = get_cookies()
    q = urllib.parse.quote(json.dumps(inp))
    url = ORIGIN + "/api/trpc/" + path + "?input=" + q
    _throttle("trpc:" + path)                  # P5.2：令牌桶（失败开放，绝不阻塞）
    # P12-BL：带**浏览器 UA**。依据：平台的 WAF 按 UA 挑战 —— 实测同一端点、
    # 同一时刻，`Python-urllib/3.11` 被回 403（WAF 拦截页），而 Chrome /
    # `Mozilla/5.0 DZMM-Agent/1.0` 都是 200（`tools/probe_ua_wall.py`）。
    # requests 的默认 UA 是 `python-requests/x.y` —— 典型爬虫签名，属于"可能被挑战"那一类。
    resp = requests.get(url, cookies=cookies, timeout=timeout, proxies=PROXIES,
                        headers={"User-Agent": BROWSER_UA})
    status = getattr(resp, "status_code", 0)
    try:
        data = resp.json()
    except Exception:
        data = None
    has_err = isinstance(data, dict) and "error" in data
    # ⚠️ 只在**出错**的响应上找"限流/验证码"标记（P12-J 修）。
    # 以前对每个响应都扫全文，而 `chatroom.getMessages` 的 body 是**一整页群消息** ——
    # 于是群里任何人（包括机器人自己那条"平台风控中"的群公告）说到「验证码/风控」
    # 就会把全局冷却拉满 300 秒，冷却期里每次平台调用又 sleep 10 秒
    # ⇒ 一条普通回复要 70 秒以上，表现出来就是"机器人不回复了"。
    if status != 200 or has_err or data is None:
        _note_resp(resp, path=path)            # P5.2：命中则进入全局冷却
    if data is None:
        raise RuntimeError("tRPC " + path + ": 响应不是 JSON（HTTP {}）".format(status))
    if has_err:
        msg = data["error"].get("json", {}).get("message", str(data["error"])) if isinstance(data.get("error"), dict) else str(data.get("error"))
        raise RuntimeError("tRPC " + path + ": " + msg)
    return data.get("result", {}).get("data", {}).get("json", {})


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_messages(chat_id: str, limit: int = 20, before: str | None = None,
                 cookies: dict | None = None, timeout: float = 15) -> list[dict]:
    if cookies is None:
        cookies = get_cookies()
    if before:
        before = _utc_datetime(before).isoformat().replace("+00:00", "Z")
        # 有游标时，只带 json，不带 meta（验证过的正确格式）
        inp = {"json": {"chatroomId": chat_id, "limit": limit, "before": before}}
    else:
        inp = {"json": {"chatroomId": chat_id, "limit": limit, "before": None},
               "meta": {"values": {"before": ["undefined"]}, "v": 1}}
    data = api("chatroom.getMessages", inp, cookies, timeout)
    return list(data.get("messages") or [])


def get_messages_all(chat_id: str, cookies: dict | None = None,
                     max_count: int = 200) -> list[dict]:
    """拉取最近消息（最多 max_count 条，画像/总结无需全量历史）."""
    if cookies is None:
        cookies = get_cookies()
    all_msgs, cursor = [], None
    for _ in range(100):
        msgs = get_messages(chat_id, 50, cursor, cookies)
        if not msgs:
            break
        all_msgs = msgs + all_msgs
        if len(all_msgs) >= max_count:
            break
        cursor = msgs[0]["sent_at"]
    return all_msgs


def get_messages_since(chat_id: str, since_time: str,
                       cookies: dict | None = None, max_count: int = 1000,
                       until_time: str | None = None) -> list[dict]:
    """增量拉取窗口内消息；有终点时从终点向前翻页。"""
    if cookies is None:
        cookies = get_cookies()
    since = _utc_datetime(since_time)
    until = _utc_datetime(until_time) if until_time else None
    all_msgs, cursor = [], until_time
    for _ in range(100):
        msgs = get_messages(chat_id, 50, cursor, cookies)
        if not msgs:
            break
        all_msgs = msgs + all_msgs
        # get_messages 返回从旧到新，msgs[0] 是最旧一条
        if _utc_datetime(msgs[0]["sent_at"]) <= since:
            break  # 已翻到 since_time 或更早，停止
        cursor = msgs[0]["sent_at"]
        if len(all_msgs) >= max_count:
            break
    # 过滤出窗口内的消息；终点由 API 游标限制后仍在这里兜底。
    return [m for m in all_msgs
            if _utc_datetime(m["sent_at"]) >= since
            and (until is None or _utc_datetime(m["sent_at"]) < until)]


def get_user(chat_id: str, user_id: str,
             cookies: dict | None = None) -> dict | None:
    try:
        if cookies is None:
            cookies = get_cookies()
        return api("user.getChatroomUser",
                   {"json": {"chatroomId": chat_id, "userId": user_id}}, cookies)
    except Exception:
        return None


# P12-AO（2026-09-28）：`chatroom.getMembers` **默认只返回 101 个成员**，而且是静默截断
# （返回体里没有任何"还有下一页"的提示）。茶水间 782 人 → 只有前 101 人拿得到群昵称，
# 其余 681 人的昵称缓存**永远补不上**；这些人 @ 她时，`get_full_name` 只能退化成
# 「档案名 → uid 前 8 位」，于是被叫成 `bcec3c30` 这种一串 uid。
# 实测（线上 782 人群）：不传 limit → 101 人；`limit=1000` → 782 人。
# 这就是"小小糯有时候会叫错名字"的主因：不在前 101 的人才会错。
MEMBER_FETCH_LIMIT = 1000


def get_members(chat_id: str, cookies: dict | None = None,
                limit: int = MEMBER_FETCH_LIMIT) -> dict:
    """一次查所有成员，返回 {uid: fullName}.

    ⚠️ 必须显式带 `limit`（见 `MEMBER_FETCH_LIMIT`）：平台默认只给 101 个成员。
    """
    if cookies is None:
        cookies = get_cookies()
    data = api("chatroom.getMembers",
               {"json": {"chatroomId": chat_id, "limit": limit}}, cookies)
    members = data.get("members") or []
    return {m["id"]: m.get("fullName", "") for m in members if m.get("id")}


def get_chatroom_name(chat_id: str, cookies: dict | None = None) -> str:
    """查询群名."""
    try:
        if cookies is None:
            cookies = get_cookies()
        chats = api("chat.listAll", {"json": None,
                                     "meta": {"values": ["undefined"], "v": 1}}, cookies)
        if isinstance(chats, list):
            for c in chats:
                if c.get("id") == chat_id:
                    return (c.get("data") or {}).get("title", "") or ""
    except Exception:
        pass
    return ""


def get_private_cid(user_id: str, cookies: dict | None = None) -> str | None:
    """查 user_id 对应的私聊 chat_id（chat.listAll 里 chatType=one_on_one 且 otherUserId=user_id）。"""
    try:
        if cookies is None:
            cookies = get_cookies()
        chats = api("chat.listAll", {"json": None,
                                     "meta": {"values": ["undefined"], "v": 1}}, cookies)
        if isinstance(chats, list):
            for c in chats:
                d = (c.get("data") or {}) if isinstance(c, dict) else {}
                if d.get("chatType") == "one_on_one" and d.get("otherUserId") == user_id:
                    return c.get("id")
    except Exception:
        pass
    return None


def remove_member(chatroom_id: str, member_id: str, cookies: dict | None = None) -> bool:
    """移出群成员（需群主/管理员权限）。成功返回 True，失败抛 RuntimeError。"""
    if cookies is None:
        cookies = get_cookies()
    url = ORIGIN + "/api/trpc/groupChat.removeMember"
    _throttle("trpc:groupChat.removeMember")   # P5.2：与 api() 共用同一个桶
    resp = requests.post(url, cookies=cookies, timeout=15, proxies=PROXIES,
                         json={"json": {"chatroomId": chatroom_id, "memberId": member_id}},
                         headers={"Content-Type": "application/json",
                                  "Origin": ORIGIN, "Referer": ORIGIN + "/"})
    try:
        data = resp.json()
    except Exception:
        data = None
    # 同 api()：只在出错响应上找标记（成功响应里可能是别的内容）
    if getattr(resp, "status_code", 0) != 200 or not isinstance(data, dict) \
            or data.get("error"):
        _note_resp(resp, path="groupChat.removeMember")
    if not isinstance(data, dict):
        raise RuntimeError("removeMember: 响应不是 JSON（HTTP {}）".format(
            getattr(resp, "status_code", 0)))
    if data.get("error"):
        msg = data["error"].get("json", {}).get("message", str(data["error"]))
        raise RuntimeError("removeMember: " + msg)
    return True


# 禁言时长档位（平台 UI 5 档，2026-11-02 前端 bundle 实锤）。
# `duration=None` = 解除禁言（前端 onSuccess 以 silencedUntil 空否区分成功/解除）。
SILENCE_DURATIONS = ("10m", "1h", "24h", "7d", "forever")


def set_member_silence(chatroom_id: str, member_id: str,
                       duration: str | None = None,
                       cookies: dict | None = None) -> dict:
    """禁言/解禁群成员（需群管理员权限）。

    POST groupChat.setMemberSilence {"json": {chatroomId, memberId, duration}}；
    `duration` ∈ 10m/1h/24h/7d/forever，**None = 解除禁言**。
    成功返回服务端数据（含 silencedUntil）；失败抛 RuntimeError（服务端权限
    校验是权威——"不能禁言群主、管理员或自己"等 403 消息原样透出，调用方
    把它转成群里回复即可，不必自己实现角色判断）。
    """
    if duration is not None and duration not in SILENCE_DURATIONS:
        raise RuntimeError("setMemberSilence: 非法时长 " + str(duration))
    if cookies is None:
        cookies = get_cookies()
    url = ORIGIN + "/api/trpc/groupChat.setMemberSilence"
    _throttle("trpc:groupChat.setMemberSilence")   # P5.2：与 api() 共用同一个桶
    resp = requests.post(url, cookies=cookies, timeout=15, proxies=PROXIES,
                         json={"json": {"chatroomId": chatroom_id,
                                        "memberId": member_id,
                                        "duration": duration}},
                         headers={"Content-Type": "application/json",
                                  "User-Agent": BROWSER_UA,
                                  "Origin": ORIGIN, "Referer": ORIGIN + "/"})
    try:
        data = resp.json()
    except Exception:
        data = None
    # 同 api()：只在出错响应上找标记（成功响应里可能是别的内容）
    if getattr(resp, "status_code", 0) != 200 or not isinstance(data, dict) \
            or data.get("error"):
        _note_resp(resp, path="groupChat.setMemberSilence")
    if not isinstance(data, dict):
        raise RuntimeError("setMemberSilence: 响应不是 JSON（HTTP {}）".format(
            getattr(resp, "status_code", 0)))
    if data.get("error"):
        msg = data["error"].get("json", {}).get("message", str(data["error"]))
        raise RuntimeError("setMemberSilence: " + msg)
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError("setMemberSilence: HTTP {}".format(resp.status_code))
    result = data.get("result")
    payload = result.get("data") if isinstance(result, dict) else data.get("data")
    if isinstance(payload, dict) and "json" in payload:
        payload = payload["json"]
    if not isinstance(payload, dict) or "silencedUntil" not in payload:
        raise RuntimeError("setMemberSilence: 无法确认禁言结果")
    if (duration is None) != (not payload["silencedUntil"]):
        raise RuntimeError("setMemberSilence: 返回状态与请求不一致")
    return payload


def get_verified(user_id: str, cookies: dict | None = None):
    """查询妹子认证状态，返回 {vipLevel, verified} 或 None（失败）。"""
    if cookies is None:
        cookies = get_cookies()
    try:
        return api("verification.identity", {"json": {"userId": user_id}}, cookies)
    except Exception:
        return None


def get_verified_bulk(user_ids: list, cookies: dict | None = None) -> dict:
    """批量查询妹子认证状态，返回 {uid: {vipLevel, verified}}（失败返回空 dict）。"""
    if cookies is None:
        cookies = get_cookies()
    try:
        return api("verification.identityBulk", {"json": {"userIds": list(user_ids)}}, cookies)
    except Exception:
        return {}
