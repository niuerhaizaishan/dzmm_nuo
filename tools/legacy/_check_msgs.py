import sys
sys.path.insert(0, "/app")
from dzmm import get_messages
try:
    m = get_messages("bd7fe6a4-0065-4551-ad3d-94116dba8f9c", 3)
    print("get_messages OK, count=", len(m))
except Exception as e:
    print("get_messages FAIL:", str(e)[:80])
