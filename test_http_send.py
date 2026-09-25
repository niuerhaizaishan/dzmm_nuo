import sys, time
sys.path.insert(0, "/app")
import importlib.util
import token_store

spec = importlib.util.spec_from_file_location("dzmm_socketio", "/app/socketio.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

d = mod.DZMMSocket("https://www.dzmm.ai")
cookie = token_store.get_cookie_string()
cid = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"
uid = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"

test_text = "🔧 HTTP直发测试 " + str(int(time.time()))
ok = d.send_message(cid, uid, test_text, cookie)
print("HTTP 发送结果:", "✅" if ok else "❌")

time.sleep(3)
from dzmm import get_messages
msgs = get_messages(cid, 10)
found = any(test_text in (m.get("content") or {}).get("text", "") for m in msgs)
print("API 读到测试消息:", "✅ 真实投递" if found else "❌ 未投递（假成功）")
