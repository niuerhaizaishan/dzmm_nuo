"""重启前部署闸门：在**目标镜像内**验证 P3–P7 整批改动。

在重启之前用当前运行中的镜像验证，避免带着坏代码重启生产。

    docker run --rm --entrypoint python -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \
        -v /opt/dzmm:/app:ro -v /tmp/deploy_gate.py:/g.py:ro -w /tmp dzmm-bot:latest /g.py
"""
import inspect
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")

# ⚠️ 必须**在 import state_store 之前**把库指到可写目录：
# 闸门把 /opt/dzmm 以只读挂载（:ro），默认路径 /app/data/store.db 建不出来。
# 生产环境 /app/data 是可写的挂载卷，不受此影响。
_STATE_TMP = Path(tempfile.mkdtemp(prefix="gate-state-"))
os.environ["DZMM_STORE_DB"] = str(_STATE_TMP / "store.db")

fails = []


def check(name, ok, detail=""):
    print("  [{}] {}{}".format("OK" if ok else "!!", name,
                               (" -- " + str(detail)) if detail else ""))
    if not ok:
        fails.append(name)


print("python:", sys.version.split()[0])
print()

# ---------- 1) 导入 ----------
print("导入:")
for m in ["bot", "dzmm", "token_store", "profiles", "state_store",
          "renew_token", "headless_relogin", "auto_recover",
          "check_bot_health", "session_guard", "throttle_guard",
          "runtime.session", "runtime.throttle", "runtime.outbox",
          "runtime.routing", "runtime.transport_bot", "runtime.store"]:
    try:
        __import__(m)
        check(m, True)
    except Exception as e:                                  # noqa: BLE001
        check(m, False, e)
print()

# ---------- 2) 会话锁（P5.1） ----------
print("会话锁（唯一登录者）:")
try:
    from runtime import session
    td = Path(tempfile.mkdtemp(prefix="gate-"))
    session.LOCK_FILE = td / ".session.lock"
    session.VERSION_FILE = td / ".session_version.json"
    check("生产后端是 flock", session._fcntl is not None)
    with session.guard("a", timeout=1.0) as g1:
        check("可获取", g1 is True)
        with session.guard("b", timeout=0.3) as g2:
            check("持锁期间被拒（互斥）", g2 is False)
    with session.guard("c", timeout=1.0) as g3:
        check("释放后可获取", g3 is True)
except Exception as e:                                      # noqa: BLE001
    check("会话锁自检", False, e)
print()

# ---------- 3) 限流（P5.2） ----------
print("限流:")
try:
    from runtime import throttle
    throttle._state["enabled"] = None
    throttle.reset()
    throttle._state["bucket"] = throttle.TokenBucket(600, 1)
    check("首个立即通过", throttle.acquire("a") is False)
    check("第二个会等待", throttle.acquire("b", timeout=1.0) is True)
    check("识别限流信号", throttle.note_text("请稍后再试") == "throttle")
    check("冷却已生效", throttle.cooldown_remaining() > 0)
    throttle.reset()
    os.environ["DZMM_THROTTLE"] = "off"
    throttle._state["enabled"] = None
    check("开关可关闭", throttle.acquire("x") is False
          and throttle.stats()["counts"]["bypass"] == 1)
    os.environ.pop("DZMM_THROTTLE", None)
    throttle.reset()
except Exception as e:                                      # noqa: BLE001
    check("限流自检", False, e)
print()

# ---------- 4) 存储（P7） ----------
print("存储（P7）:")
try:
    from runtime.store import SCOPE_GROUP, SCOPE_PROFILE, Store
    td = Path(tempfile.mkdtemp(prefix="gate-store-"))
    p = td / "store.db"
    CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
    CID_B = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"
    a, b = Store(p), Store(p)
    a.save_scope(SCOPE_GROUP, {CID_A: {"v": 1}})
    b.save_scope(SCOPE_GROUP, {CID_B: {"v": 2}})
    merged = Store(p).load_scope(SCOPE_GROUP)
    check("两个写者互不覆盖", merged.get(CID_A) == {"v": 1}
          and merged.get(CID_B) == {"v": 2})
    a.close()
    b.close()
except Exception as e:                                      # noqa: BLE001
    check("存储自检", False, e)

import state_store  # noqa: E402
try:
    st = state_store.stats()
    check("统一入口后端", st.get("backend") == "sqlite", st)
    check("已从 JSON 导入", (st.get("counts") or {}).get("profile", 0) > 0,
          st.get("counts"))
    CID_G = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
    state_store.save_state({CID_G: {"gate": 1}})
    check("群状态可写可读", state_store.load_state().get(CID_G) == {"gate": 1})
    # ⚠️ 关键：非 UUID 顶层键（如 dice_game）**必须保留**。
    # 早期版本按 UUID 过滤 load_state()，会让骰子局状态永远读成空。
    state_store.save_state({CID_G: {"gate": 1}, "dice_game": {"active": True}})
    check("非 UUID 顶层键保留（dice_game）",
          state_store.load_state().get("dice_game") == {"active": True})
except Exception as e:                                      # noqa: BLE001
    check("统一入口自检", False, e)
print()

# ---------- 5) 接线自证 ----------
print("接线自证:")
import bot  # noqa: E402
import dzmm  # noqa: E402
import profiles  # noqa: E402
import token_store  # noqa: E402

check("bot.load_state 走 state_store",
      "state_store" in inspect.getsource(bot.load_state))
check("profiles.save_profiles 走 state_store",
      "state_store" in inspect.getsource(profiles.save_profiles))
check("token_store.save_cookies 触发版本递增",
      "_note_written" in inspect.getsource(token_store.save_cookies))
api_src = inspect.getsource(dzmm.api)
check("dzmm.api 接了令牌桶", "_throttle(" in api_src and "_note_resp(" in api_src)
check("dzmm.remove_member 接了令牌桶",
      "_throttle(" in inspect.getsource(dzmm.remove_member))
for mod_name in ("renew_token", "headless_relogin", "auto_recover"):
    mod = sys.modules[mod_name]
    check("{} 的 main 用会话锁".format(mod_name),
          "guard(" in inspect.getsource(mod.main))

# ---------- 6) 回归（P3/P4） ----------
print()
print("回归（P3/P4）:")
try:
    from runtime.outbox import OutboxStore
    from runtime.routing import RoutingSender
    td = Path(tempfile.mkdtemp(prefix="gate-ob-"))
    s = OutboxStore(td / "o.db")
    oid = s.enqueue(chatroom_id="gate", text="x")
    ok = s.lease(oid, "w")
    s.mark_sent(oid, "m")
    check("outbox 落库/租约/标记", ok and s.get(oid)["status"] == "sent")
    s.close()
    check("routing 可装配", RoutingSender(object(), {}).bot_cids == ())
    os.environ.pop("DZMM_BOT_ROUTING", None)
    os.environ["DZMM_OUTBOX_DB"] = str(td / "o2.db")
    w = bot._wrap_with_outbox(type("F", (), {"send": None})(), dry=False)
    check("发送链仍为 OutboxSender", type(w).__name__ == "OutboxSender", type(w).__name__)
except Exception as e:                                      # noqa: BLE001
    check("P3/P4 回归", False, e)

print()
print("FAILED: {}".format(fails) if fails else "ALL CHECKS PASSED")
raise SystemExit(1 if fails else 0)
