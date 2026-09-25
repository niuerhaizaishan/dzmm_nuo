"""重启前部署闸门：在**目标镜像内**验证 P3–P7 整批改动。

在重启之前用当前运行中的镜像验证，避免带着坏代码重启生产。

     docker run --rm --entrypoint python -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \
        -v /opt/dzmm:/app:ro -v /tmp/deploy_gate.py:/g.py:ro -w /tmp dzmm-bot:latest /g.py

这是一个一次性脚本，不是生产代码 —— 唯一的职责是"重启前把话说清楚"。

`GATE_ROOT` 可覆盖代码根目录（默认 `/app`），用来在本地先跑一遍同样的检查：

    GATE_ROOT=D:/Deepseek/dzmm_botapi TEMP=D:/Deepseek/dzmm_botapi/tests/_tmp python tools/deploy_gate.py

（本地要顺手把 `TEMP` 指到可写目录 —— 本机沙箱下的系统临时目录写不进去，
会让存储/闸门那几项假失败。`生产后端是 flock` 在 Windows 上必然失败，属正常。）
"""
import inspect
import os
import sys
import tempfile
import uuid
from pathlib import Path

GATE_ROOT = Path(os.environ.get("GATE_ROOT") or "/app")
sys.path.insert(0, str(GATE_ROOT))


def _tmpdir(prefix):
    """建一个临时目录。

    刻意**不用** `tempfile.mkdtemp`：它建的是 0700 目录，在受限沙箱（本机开发
    环境）里后续往里写文件会被拒（`PermissionError`），于是在本地跑闸门时
    一半检查会假失败。普通 `mkdir` 两端都正常，唯一性由 uuid 保证。
    """
    d = Path(tempfile.gettempdir()) / (prefix + uuid.uuid4().hex)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ⚠️ 必须**在 import state_store 之前**把库指到可写目录：
# 闸门把 /opt/dzmm 以只读挂载（:ro），默认路径 /app/data/store.db 建不出来。
# 生产环境 /app/data 是可写的挂载卷，不受此影响。
_STATE_TMP = _tmpdir("gate-state-")
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
          "runtime.routing", "runtime.transport_bot", "runtime.store",
          "runtime.prompts"]:
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
    td = _tmpdir("gate-session-")
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
    td = _tmpdir("gate-store-")
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

# P8：状态存取器 + 发情值常量
check("bot._add_heat 走 state_store",
      "state_store" in inspect.getsource(bot._add_heat))
check("bot._set_vote_state 走 state_store",
      "state_store" in inspect.getsource(bot._set_vote_state))
_heat_consts = [n for n in dir(bot) if n.startswith("HEAT_")]
check("发情值常量已集中（{} 个）".format(len(_heat_consts)), len(_heat_consts) == 11)
try:
    import re as _re
    _bot_src = (GATE_ROOT / "bot.py").read_text(encoding="utf-8")
    _left = _re.findall(r"_add_heat\([^)]*,\s*\d+\)", _bot_src)
    check("无裸数字 _add_heat 调用", not _left, _left[:3])
except Exception as e:                                      # noqa: BLE001
    check("裸数字检查", False, e)

# P8 第 3 步：prompt 外置
print()
print("prompt 外置（P8 第 3 步）:")
try:
    import ast as _ast
    import brains as _brains
    from runtime import prompts as _rp

    _tree = _ast.parse((GATE_ROOT / "brains.py").read_text(encoding="utf-8"))
    _decl = {}
    for _n in _tree.body:
        if (isinstance(_n, _ast.Assign) and len(_n.targets) == 1
                and isinstance(_n.targets[0], _ast.Name)
                and _n.targets[0].id.endswith("_PROMPT")):
            _name = _n.targets[0].id
            if not isinstance(_n.value, _ast.Call):
                check("{} 被 _p() 包裹".format(_name), False, "仍是裸字面量")
                continue
            _decl[_name] = _ast.literal_eval(_n.value.args[1])
    check("39 个 prompt 全部走 _p()", len(_decl) == 39, len(_decl))

    _bad = [n for n in _decl if not (getattr(_brains, n) or "").strip()]
    check("无空 prompt", not _bad, _bad[:3])

    # 机制自检走临时目录，不依赖 prompts/ 是否挂上。
    # 单独 try：临时目录不可写（只读挂载 / 沙箱）不该连累下面的比对检查。
    try:
        _td = _tmpdir("gate-prompt-")
        _old = os.environ.get("DZMM_PROMPT_DIR")
        os.environ["DZMM_PROMPT_DIR"] = str(_td)
        _saved = dict(_rp._sources)
        _rp._sources.clear()
        check("文件缺失时退回默认值", _rp.load("X", "d") == "d")
        (_td / "X.md").write_text("f", encoding="utf-8")
        check("文件存在时覆盖", _rp.load("X", "d") == "f")
        (_td / "X.md").write_text("", encoding="utf-8")
        check("空文件退回默认值", _rp.load("X", "d") == "d")
        _rp._sources.clear()
        _rp._sources.update(_saved)
        if _old is None:
            os.environ.pop("DZMM_PROMPT_DIR", None)
        else:
            os.environ["DZMM_PROMPT_DIR"] = _old
    except Exception as e:                                  # noqa: BLE001
        check("覆盖/退回机制自检", False, e)

    # 挂了 prompts/ 就必须与代码默认值逐字节一致（否则等于偷偷改人设）
    _st = _rp.stats()
    if _st["files_on_disk"]:
        _files = {p.stem: p.read_text(encoding="utf-8")
                  for p in _rp.prompt_dir().glob("*.md")}
        _miss = sorted(set(_decl) - set(_files))
        _diff = [n for n in _decl if n in _files and _files[n] != _decl[n]]
        _orph = sorted(set(_files) - set(_decl))
        check("prompts/ 文件齐全（{} 个）".format(_st["files_on_disk"]),
              not _miss, _miss[:3])
        check("文件与代码默认值一致", not _diff, _diff[:3])
        check("无孤儿文件", not _orph, _orph[:3])
    else:
        check("prompts/ 未挂载，全用代码默认值", True, "功能正常，只是不能用文件调 prompt")
except Exception as e:                                      # noqa: BLE001
    check("prompt 外置自检", False, e)

# ---------- 6) 回归（P3/P4） ----------
print()
print("回归（P3/P4）:")
try:
    from runtime.outbox import OutboxStore
    from runtime.routing import RoutingSender
    td = _tmpdir("gate-ob-")
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
