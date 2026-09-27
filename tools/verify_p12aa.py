"""P12-AA 线上自检（容器内，只读为主）：三件事的真实行为。

1. 「只写本群」在**真库**上成立：写 A 群后 B 群那一行逐字节不变；
2. `OutboxStore` 在**工作线程**里能读能写（临时库，不碰生产出站队列）；
3. `get_full_name` 在事件循环上不发 HTTP（用"耗时 + 待补集合"证明）。
"""
import asyncio
import hashlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import bot                                                     # noqa: E402
import state_store                                             # noqa: E402
from runtime import identity as ident                          # noqa: E402
from runtime.outbox import OutboxStore                         # noqa: E402

AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"

print("=== 1) 只写本群：别的群那一行不能被碰 ===")
before_b = json.dumps(state_store.load_state().get(AUTH) or {},
                      ensure_ascii=False, sort_keys=True)
row_j = json.dumps(state_store.load_state().get(RUOJIN) or {},
                   ensure_ascii=False, sort_keys=True)
bot._save_group(RUOJIN, json.loads(row_j))         # 原样写回（不改内容）
after_b = json.dumps(state_store.load_state().get(AUTH) or {},
                     ensure_ascii=False, sort_keys=True)
print("  认证群那一行 md5: {} -> {}".format(
    hashlib.md5(before_b.encode()).hexdigest()[:12],
    hashlib.md5(after_b.encode()).hexdigest()[:12]))
print("  没被改动：", before_b == after_b)
print("  若瑾群行长度（写回后）：", len(json.dumps(
    state_store.load_state().get(RUOJIN) or {}, ensure_ascii=False)))

print()
print("=== 2) 出站队列：工作线程读写在真实现上成立 ===")
tmp = Path(tempfile.mkdtemp(prefix="p12aa-")) / "o.db"
st = OutboxStore(tmp)
out = {}


def _worker():
    out["oid"] = st.enqueue(chatroom_id="c1", text="线程里入队")
    out["read"] = st.get(out["oid"])["text"]
    out["pending"] = st.pending_count()


t = threading.Thread(target=_worker)
t.start()
t.join()
st.mark_sent(out["oid"])
print("  线程里入队/读回：", out["read"], "| pending =", out["pending"])
print("  主线程看到状态：", st.get(out["oid"])["status"])
print("  有锁：", hasattr(st, "_lock"))
st.close()

print()
print("=== 3) 事件循环上取昵称不发 HTTP ===")
ident._name_cache.clear()
ident._pending_names.clear()
ident._load_member_names(RUOJIN)                   # 正常预载（工作线程里做的，这里直接调）
print("  预载后缓存条数：", len(ident._name_cache),
      "/ 待补：", ident.pending_name_count())


async def _probe():
    t0 = time.time()
    nm = ident.get_full_name("00000000-0000-0000-0000-0000000000ff")
    return nm, time.time() - t0, ident._on_event_loop()


nm, dt, on_loop = asyncio.run(_probe())
print("  事件循环上取一个不存在的 uid：{!r}  耗时 {:.0f} ms  在循环上={}".format(
    nm, dt * 1000, on_loop))
print("  待补集合里有没有它：", "00000000-0000-0000-0000-0000000000ff" in ident._pending_names)
print("  耗时是否远小于一次 HTTP（<300ms）：", dt < 0.3)

print()
print("done")
