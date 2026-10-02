"""DZMM Socket.IO — 快速发送（batch 模式，不 poll）.

策略：走 7890 代理 + 自动重试。dzmm.ai Socket.IO 间歇性返回
「Session ID unknown」（connect/send 时好时坏），重试可大幅提高成功率。
失败最终返回 False，由 bot 层 Playwright 兜底。
"""
import json, re, time, uuid
import requests
import env_config


class DZMMSocket:
    def __init__(self, origin: str):
        self.origin = origin.rstrip("/")

    def send_message(self, chatroom_id: str, user_id: str, text: str,
                     cookie_str: str, retries: int = 3) -> bool:
        """发送消息，自动重试（每次重新握手），提高间歇性失败的成功率."""
        return self.send_message_ex(chatroom_id, user_id, text, cookie_str,
                                    retries)[0]

    def send_message_ex(self, chatroom_id: str, user_id: str, text: str,
                        cookie_str: str, retries: int = 3,
                        message_id: str = "") -> tuple:
        """同 `send_message`，但把**服务端回执**带出来：`(是否被接受, 说明, message_id)`。

        P12-AZ（2026-09-30）：为什么需要回执 —— 这条路"时好时坏"，失败几乎都发生在
        `connect` 阶段（`400 {"code":1,"message":"Session ID unknown"}`）。
        实测：**connect 失败时 `send` 有时照样返回 `200 ok`，而群里什么都没有** ——
        所以回执只能用来判断"要不要重新握手再来一次"，**能不能算成功只有读回说了算**。
        """
        mid = message_id or str(uuid.uuid4())
        notes = []
        for attempt in range(retries):
            if attempt > 0:
                time.sleep(0.5 * attempt)
            note = self._try_send(chatroom_id, user_id, text, cookie_str, mid)
            if not note:
                return True, "ok", mid
            notes.append(note)
        return False, "; ".join(notes) or "没试", mid

    def _try_send(self, chatroom_id: str, user_id: str, text: str,
                  cookie_str: str, message_id: str = "") -> str:
        """发一次。**成功返回空串**，失败返回说明（`connect=400` / `send=400` / `err=…`）。"""
        ts = str(int(time.time() * 1000))
        s = requests.Session()
        # 走代理：dzmm.ai Socket.IO 直连会丢 session（connect 返回 Session ID unknown），代理直达源站才稳
        s.proxies.update(env_config.get_proxies() or {})
        s.headers.update({
            "Cookie": cookie_str, "Origin": self.origin,
            "Referer": self.origin + "/chat",
            "User-Agent": "Mozilla/5.0 DZMM-Agent/1.0",
            "Accept": "*/*",
        })
        try:
            # 1. Handshake
            hs = (self.origin + "/ws/matching/?EIO=4&transport=polling"
                  + "&t=" + ts + "&timestamp=" + ts + "&syncMode=http")
            r = s.get(hs, timeout=8)
            r.raise_for_status()
            sid = json.loads(re.search(r"0(\{.*?\})", r.text).group(1))["sid"]
            base = (self.origin + "/ws/matching/?EIO=4&transport=polling"
                    + "&sid=" + sid + "&timestamp=" + ts + "&syncMode=http")

            # 2. Connect
            r2 = s.post(base, data=b"40",
                        headers={"Content-Type": "text/plain;charset=UTF-8"},
                        timeout=8)
            if r2.status_code >= 400:
                # 最常见的失败：`400 {"code":1,"message":"Session ID unknown"}` ——
                # 握手拿到的 sid 没被注册（代理/边缘节点不对味），只能整个重来。
                return "connect=" + str(r2.status_code)
            # 2b. GET poll 读取服务端连接确认（关键：否则 sid 未注册，下一步 POST 报 Session ID unknown）
            try:
                s.get(base, timeout=8)
            except Exception:
                pass

            # 3. Join + send（batch 一起发，省一次往返）
            mid = message_id or str(uuid.uuid4())
            msg = {
                "message_id": mid, "chatroom_id": chatroom_id,
                "sent_by": user_id,
                "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                "content": {"type": "text", "text": text},
            }
            batch = "\x1e".join([
                "42" + json.dumps(["message:join-room", {"chatroomId": chatroom_id}],
                                  ensure_ascii=False, separators=(",", ":")),
                "42" + json.dumps(["message:send", {"chatroomId": chatroom_id,
                                                    "message": msg}],
                                  ensure_ascii=False, separators=(",", ":")),
            ])
            r3 = s.post(base, data=batch.encode("utf-8"),
                        headers={"Content-Type": "text/plain;charset=UTF-8"},
                        timeout=8)
            if r3.status_code >= 400:
                return "send=" + str(r3.status_code)
            # ⚠️ `200 ok` **不代表投递**（connect 没成时它也回 200）⇒ 这里只报"接受"，
            # 真正算不算数由调用方读回决定。
            return ""
        except Exception as e:
            print("[socket] " + str(e)[:60])
            return "err=" + str(e)[:40]
        finally:
            s.close()
