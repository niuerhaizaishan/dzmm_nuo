"""
DZMM Core — cookie from Chrome + tRPC API.
"""
import json, urllib.parse
import requests
import token_store
import env_config

ORIGIN = env_config.get_dzmm_origin()

# 代理地址走环境变量 DZMM_PROXY（默认 127.0.0.1:7890；云端香港/新加坡直连设空字符串）
PROXIES = env_config.get_proxies()


def get_cookies() -> dict:
    return token_store.get_cookie_dict()


def get_cookie_string() -> str:
    return token_store.get_cookie_string()


def api(path: str, inp: dict, cookies: dict | None = None) -> dict:
    """调用 tRPC API."""
    if cookies is None:
        cookies = get_cookies()
    q = urllib.parse.quote(json.dumps(inp))
    url = ORIGIN + "/api/trpc/" + path + "?input=" + q
    resp = requests.get(url, cookies=cookies, timeout=15, proxies=PROXIES)
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        msg = data["error"].get("json", {}).get("message", str(data["error"])) if isinstance(data.get("error"), dict) else str(data.get("error"))
        raise RuntimeError("tRPC " + path + ": " + msg)
    return data.get("result", {}).get("data", {}).get("json", {})


def get_messages(chat_id: str, limit: int = 20, before: str | None = None,
                 cookies: dict | None = None) -> list[dict]:
    if cookies is None:
        cookies = get_cookies()
    if before:
        before = before.replace(" ", "T").replace("+00", "Z")
        # 有游标时，只带 json，不带 meta（验证过的正确格式）
        inp = {"json": {"chatroomId": chat_id, "limit": limit, "before": before}}
    else:
        inp = {"json": {"chatroomId": chat_id, "limit": limit, "before": None},
               "meta": {"values": {"before": ["undefined"]}, "v": 1}}
    data = api("chatroom.getMessages", inp, cookies)
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
                       cookies: dict | None = None, max_count: int = 1000) -> list[dict]:
    """增量拉取：拉取 sent_at > since_time 的消息（从新往旧翻，翻过 since_time 停）."""
    if cookies is None:
        cookies = get_cookies()
    all_msgs, cursor = [], None
    for _ in range(100):
        msgs = get_messages(chat_id, 50, cursor, cookies)
        if not msgs:
            break
        all_msgs = msgs + all_msgs
        # get_messages 返回从旧到新，msgs[0] 是最旧一条
        if msgs[0]["sent_at"] <= since_time:
            break  # 已翻到 since_time 或更早，停止
        cursor = msgs[0]["sent_at"]
        if len(all_msgs) >= max_count:
            break
    # 过滤出严格晚于 since_time 的消息
    return [m for m in all_msgs if m["sent_at"] > since_time]


def get_user(chat_id: str, user_id: str,
             cookies: dict | None = None) -> dict | None:
    try:
        if cookies is None:
            cookies = get_cookies()
        return api("user.getChatroomUser",
                   {"json": {"chatroomId": chat_id, "userId": user_id}}, cookies)
    except Exception:
        return None


def get_members(chat_id: str, cookies: dict | None = None) -> dict:
    """一次查所有成员，返回 {uid: fullName}."""
    if cookies is None:
        cookies = get_cookies()
    data = api("chatroom.getMembers", {"json": {"chatroomId": chat_id}}, cookies)
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
    resp = requests.post(url, cookies=cookies, timeout=15, proxies=PROXIES,
                         json={"json": {"chatroomId": chatroom_id, "memberId": member_id}},
                         headers={"Content-Type": "application/json",
                                  "Origin": ORIGIN, "Referer": ORIGIN + "/"})
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        msg = data["error"].get("json", {}).get("message", str(data["error"]))
        raise RuntimeError("removeMember: " + msg)
    return True


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
