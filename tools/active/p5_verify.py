"""P5 重启前闸门：在目标镜像内验证「唯一登录者 + 令牌桶」整批改动。

在**重启之前**用当前运行中的镜像验证，避免带着坏代码重启生产。

    docker run --rm --entrypoint python -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \
        -v /opt/dzmm:/app:ro -v /tmp/p5_verify.py:/v.py:ro -w /tmp dzmm-bot:latest /v.py
"""
import inspect
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")

fails = []


def check(name, ok, detail=""):
    print("  [{}] {}{}".format("OK" if ok else "!!", name,
                               (" -- " + str(detail)) if detail else ""))
    if not ok:
        fails.append(name)


print("python:", sys.version.split()[0])
print()

# ---------- 1) 所有被改动的模块都能导入 ----------
print("导入（P5 改动的全部文件）:")
mods = ["bot", "dzmm", "token_store", "renew_token", "headless_relogin",
        "auto_recover", "session_guard", "throttle_guard",
        "runtime.session", "runtime.throttle"]
for m in mods:
    try:
        __import__(m)
        check(m, True)
    except Exception as e:                                  # noqa: BLE001
        check(m, False, e)
print()

# ---------- 2) 会话锁：真实互斥（Linux flock） ----------
print("会话锁（唯一登录者）:")
try:
    from runtime import session
    td = Path(tempfile.mkdtemp(prefix="p5chk-"))
    session.LOCK_FILE = td / ".session.lock"
    session.VERSION_FILE = td / ".session_version.json"
    check("生产后端是 flock", session._fcntl is not None)
    with session.guard("a", timeout=1.0) as g1:
        check("可获取", g1 is True)
        with session.guard("b", timeout=0.3) as g2:
            check("持锁期间被拒（互斥）", g2 is False)
    with session.guard("c", timeout=1.0) as g3:
        check("释放后可获取", g3 is True)
    session.note_credentials_written("gate")
    check("版本号递增", session.version() == 1, session.version())
except Exception as e:                                      # noqa: BLE001
    check("会话锁自检", False, e)
print()

# ---------- 3) 限流 ----------
print("限流（令牌桶 + 冷却）:")
try:
    from runtime import throttle
    throttle._state["enabled"] = None
    throttle.reset()
    throttle._state["bucket"] = throttle.TokenBucket(600, 1)
    check("首个请求立即通过", throttle.acquire("a") is False)
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

# ---------- 4) 接线（源码级：确认真的接上了，不是"我以为接上了"） ----------
print("接线自证:")
import dzmm  # noqa: E402
api_src = inspect.getsource(dzmm.api)
rm_src = inspect.getsource(dzmm.remove_member)
check("dzmm.api 接了令牌桶", "_throttle(" in api_src and "_note_resp(" in api_src)
check("dzmm.remove_member 接了令牌桶", "_throttle(" in rm_src and "_note_resp(" in rm_src)

import token_store  # noqa: E402
check("token_store.save_cookies 触发版本递增",
      "_note_written" in inspect.getsource(token_store.save_cookies))

import auto_recover, headless_relogin, renew_token  # noqa: E402
for mod in (renew_token, headless_relogin, auto_recover):
    check("{} 的 main 用会话锁".format(mod.__name__),
          "guard(" in inspect.getsource(mod.main))
check("三个登录脚本已拆出受锁函数",
      callable(renew_token._renew_locked)
      and callable(headless_relogin._do_login)
      and callable(auto_recover._recover_locked))

# ---------- 5) 出站队列仍然正常（P3/P4 未回归） ----------
print()
print("回归（P3/P4）:")
import bot  # noqa: E402  （上面的 __import__ 不会绑定名字，这里显式导入）
try:
    from runtime.outbox import OutboxSender, OutboxStore
    from runtime.routing import RoutingSender
    st = OutboxStore("/tmp/p5_gate_outbox.db")
    oid = st.enqueue(chatroom_id="gate", text="x")
    check("outbox 落库/租约/标记", st.lease(oid, "w") and (st.mark_sent(oid, "m") or True)
          and st.get(oid)["status"] == "sent")
    st.close()
    check("routing 可装配", RoutingSender(object(), {}).bot_cids == ())
    os.environ.pop("DZMM_BOT_ROUTING", None)
    os.environ["DZMM_OUTBOX_DB"] = "/tmp/p5_gate_outbox2.db"
    w = bot._wrap_with_outbox(type("F", (), {"send": None})(), dry=False)
    check("bot 发送链仍为 OutboxSender", type(w).__name__ == "OutboxSender", type(w).__name__)
except Exception as e:                                      # noqa: BLE001
    check("P3/P4 回归", False, e)

print()
print("FAILED: {}".format(fails) if fails else "ALL CHECKS PASSED")
raise SystemExit(1 if fails else 0)
