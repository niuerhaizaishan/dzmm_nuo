#!/usr/bin/env python3
"""诊断 DZMMSocket 发送失败的具体环节（handshake/connect/poll/send）。"""
import sys, time, json, re, uuid
sys.path.insert(0, "/app")
import requests, token_store, env_config

ORIGIN = env_config.get_dzmm_origin()
cookie = token_store.get_cookie_string()
CID = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"
UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"

s = requests.Session()
s.proxies.update(env_config.get_proxies() or {})
s.headers.update({
    "Cookie": cookie, "Origin": ORIGIN, "Referer": ORIGIN + "/chat",
    "User-Agent": "Mozilla/5.0 DZMM-Agent/1.0", "Accept": "*/*",
})

ts = str(int(time.time() * 1000))
base_q = "&timestamp=" + ts + "&syncMode=http"

# 1. handshake
hs = ORIGIN + "/ws/matching/?EIO=4&transport=polling&t=" + ts + base_q
try:
    r = s.get(hs, timeout=8)
    print("1.handshake GET  status=%d" % r.status_code)
    print("  body[:160]=", r.text[:160].replace("\n", " "))
    m = re.search(r"0(\{.*?\})", r.text)
    sid = json.loads(m.group(1))["sid"] if m else None
    print("  sid=", sid)
except Exception as e:
    print("1.handshake 异常:", str(e)[:80])
    sys.exit()

base = ORIGIN + "/ws/matching/?EIO=4&transport=polling&sid=" + sid + base_q

# 2. connect
try:
    r2 = s.post(base, data=b"40", headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=8)
    print("2.connect POST   status=%d  body[:160]=" % r2.status_code, r2.text[:160].replace("\n", " "))
except Exception as e:
    print("2.connect 异常:", str(e)[:80])

# 2b. GET poll 读服务端确认
try:
    r2b = s.get(base, timeout=8)
    print("2b.GET poll     status=%d  body[:200]=" % r2b.status_code, r2b.text[:200].replace("\n", " "))
except Exception as e:
    print("2b.GET poll 异常:", str(e)[:80])

# 3. join + send
mid = str(uuid.uuid4())
msg = {
    "message_id": mid, "chatroom_id": CID, "sent_by": UID,
    "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    "content": {"type": "text", "text": "🔧SIO诊断 " + str(int(time.time() * 1000))},
}
batch = "\x1e".join([
    "42" + json.dumps(["message:join-room", {"chatroomId": CID}], ensure_ascii=False, separators=(",", ":")),
    "42" + json.dumps(["message:send", {"chatroomId": CID, "message": msg}], ensure_ascii=False, separators=(",", ":")),
])
try:
    r3 = s.post(base, data=batch.encode("utf-8"),
                headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=8)
    print("3.join+send POST status=%d  body[:300]=" % r3.status_code, r3.text[:300].replace("\n", " "))
except Exception as e:
    print("3.join+send 异常:", str(e)[:80])

# 4. 读回验证
try:
    r4 = s.get(base, timeout=8)
    print("4.读回 GET       status=%d  body[:300]=" % r4.status_code, r4.text[:300].replace("\n", " "))
except Exception as e:
    print("4.读回 异常:", str(e)[:80])

time.sleep(2)
from dzmm import get_messages
try:
    msgs = get_messages(CID, 10)
    last = [(m.get("sent_by", "")[:8], (m.get("content") or {}).get("text", "")[:30]) for m in msgs[-3:]]
    print("5.get_messages 最近3条:", last)
except Exception as e:
    print("5.get_messages 异常:", str(e)[:80])
