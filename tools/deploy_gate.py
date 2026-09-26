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
          "runtime.prompts", "runtime.ctx", "runtime.obs", "runtime.identity",
          "runtime.dice", "runtime.text", "runtime.members", "runtime.fortune",
          "runtime.trend", "runtime.recap", "runtime.sign", "runtime.group_state",
          "runtime.heat", "runtime.memory", "runtime.poem", "runtime.novel",
          "runtime.vote", "runtime.story", "runtime.perf", "runtime.fasao",
          "runtime.diaojiao"]:
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

# ---------- 7) 模块拆分（P8 第 4 步） ----------
print()
print("模块拆分（P8 第 4 步）:")
try:
    import ast as _ast2
    import asyncio as _aio
    import runtime.ctx as _ctx
    import runtime.dice as _dice
    import runtime.fortune as _fort
    import runtime.diaojiao as _dj
    import runtime.fasao as _fasao
    import runtime.group_state as _gstate
    import runtime.heat as _heat
    import runtime.identity as _ident
    import runtime.members as _memb
    import runtime.memory as _mem
    import runtime.novel as _novel
    import runtime.obs as _obs
    import runtime.perf as _perf
    import runtime.poem as _poem
    import runtime.recap as _recap
    import runtime.sign as _sign
    import runtime.story as _story
    import runtime.text as _textm
    import runtime.trend as _trend
    import runtime.vote as _vote

    # a) 搬走的函数必须还是**同一个对象**（不是两边各定义一份）
    _moved = {
        "log": _obs, "get_name": _ident, "is_self": _ident, "self_uids": _ident,
        "_dice_state": _dice, "_dice_save": _dice, "_dice_start_cmd": _dice,
        "_dice_join_cmd": _dice, "_dice_exit_cmd": _dice,
        "_dice_current_player": _dice, "_dice_board_text": _dice,
        "_normalize_cn": _textm, "_clean_name": _textm, "_is_stop_word": _textm,
        "_norm_poem": _textm, "_extract_msgs": _memb, "_find_uid_by_name": _memb,
        "_archive_text_from_profile": _memb, "_history_lines": _memb,
        "_fortune_calc": _fort, "is_fortune_cmd": _fort, "_record_fortune": _fort,
        "week_trend": _trend, "_trend_chong": _trend,
        "recap_absent": _recap, "is_recap_cmd": _recap,
        "_sign_reply": _sign, "_sign_calc": _sign, "_is_flower_grab": _sign,
        "_get_vote_state": _gstate, "_remember": _gstate, "_add_heat": _heat,
        "_retrieve_memory": _mem, "is_remember_cmd": _mem,
        "_norm_poem": _textm, "poem": _poem, "_novel_save_path": _novel,
        "is_vote_start_cmd": _vote, "is_story_end_cmd": _story,
        "_handle_perf_start": _perf, "is_fasao_cmd": _fasao,
        "is_diaojiao_cmd": _dj,
    }
    _bad = [n for n, m in _moved.items() if getattr(bot, n, None) is not getattr(m, n)]
    check("搬走的 {} 个函数仍是同一对象".format(len(_moved)), not _bad, _bad[:4])

    # b2) 自动推导的"已搬走"总数（不依赖任何手维护清单）
    _mods = {}
    for _p in sorted((GATE_ROOT / "runtime").glob("*.py")):
        if _p.stem == "__init__":
            continue
        _mods[_p.name] = __import__("runtime." + _p.stem, fromlist=["x"])
    _auto = 0
    for _fn, _m in _mods.items():
        _names = {n.name for n in _ast2.parse(
            (GATE_ROOT / "runtime" / _fn).read_text(encoding="utf-8")).body
            if isinstance(n, (_ast2.FunctionDef, _ast2.AsyncFunctionDef))}
        for _n in _names:
            if getattr(bot, _n, None) is getattr(_m, _n, None):
                _auto += 1
    check("自动推导出 {} 个已搬函数（应 ≥60）".format(_auto), _auto >= 60)

    # b) 全自动"两边都定义"守卫：bot.py 与 runtime/ 不得有同名函数
    _bot_defs = {n.name for n in _ast2.parse(
        (GATE_ROOT / "bot.py").read_text(encoding="utf-8")).body
        if isinstance(n, (_ast2.FunctionDef, _ast2.AsyncFunctionDef))}
    _clash = {}
    for _p in sorted((GATE_ROOT / "runtime").glob("*.py")):
        _names = {n.name for n in _ast2.parse(_p.read_text(encoding="utf-8")).body
                  if isinstance(n, (_ast2.FunctionDef, _ast2.AsyncFunctionDef))}
        if _names & _bot_defs:
            _clash[_p.name] = sorted(_names & _bot_defs)
    check("无同名重复定义", not _clash, _clash)

    # c) `_ACTIVE_CID` 只能由 _set_active_cid 写（否则搬出去的模块读到旧群）
    _tree = _ast2.parse((GATE_ROOT / "bot.py").read_text(encoding="utf-8"))
    _badw = []
    for _fn in _ast2.walk(_tree):
        if not isinstance(_fn, (_ast2.FunctionDef, _ast2.AsyncFunctionDef)):
            continue
        for _s in _ast2.walk(_fn):
            if isinstance(_s, _ast2.Assign) and any(
                    isinstance(t, _ast2.Name) and t.id == "_ACTIVE_CID"
                    for t in _s.targets) and _fn.name != "_set_active_cid":
                _badw.append((_fn.name, _s.lineno))
    check("_ACTIVE_CID 只有一个写点", not _badw, _badw)

    bot._set_active_cid("gate-cid-1")
    check("ctx 与 bot 镜像同步",
          _ctx.get_active_cid() == "gate-cid-1" == bot._ACTIVE_CID)
    bot._set_active_cid("")

    # d) 拆出去的模块不得回指 bot.py（回指 = 只是把文字挪了个位置）
    _refs = []
    for _p in sorted((GATE_ROOT / "runtime").glob("*.py")):
        for _i, _ln in enumerate(_p.read_text(encoding="utf-8").splitlines(), 1):
            if _ln.strip().startswith(("import bot", "from bot import")):
                _refs.append((_p.name, _i))
    check("runtime/ 无回指 bot.py", not _refs, _refs)

    # e) 骰子局跑一遍真流程（搬完还能用）
    #    先清空：上面"非 UUID 顶层键保留"那项往库里写了 {"active": True}，
    #    不清的话 _dice_start_cmd 会正确地拒绝重复开局，测试反而失败。
    _dice._dice_save({})
    _dice_get_name = _dice.get_name
    _dice.get_name = lambda uid: "名-" + uid[:4]
    try:
        _r1 = _aio.run(_dice._dice_start_cmd("gate-cid", "u1"))
        _r2 = _aio.run(_dice._dice_join_cmd("u2"))
        _g = _dice._dice_state()
        _r3 = _dice._dice_exit_cmd("u2")
        _after = _dice._dice_state()
        check("骰子局 发起/加入/退出 全流程",
              "发起" in _r1 and "加入" in _r2 and "剩余不足 2 人" in _r3
              and _g["players"] == ["u1", "u2"] and _after["active"] is False)
    finally:
        _dice.get_name = _dice_get_name
except Exception as e:                                      # noqa: BLE001
    check("模块拆分自检", False, e)

print()
print("配置（P11）:")
try:
    import sys as _sys
    _sys.path.insert(0, str(GATE_ROOT / "admin"))
    import dashboard as _dash
    from runtime import config_store as _cs
    _defaults = _dash._config_defaults()
    check("默认值可解析（读 bot.py）", not _defaults.get("error"), _defaults.get("error"))
    _p = _cs.config_path()
    check("配置文件存在", _p.is_file(), str(_p))
    if _p.is_file():
        # **这条最要紧**：配置不合法 → bot 直接 SystemExit 拒绝启动（线上就是挂掉）。
        # 所以要在重启前先拿真文件真校验一遍。
        _cfg = _cs.load(_defaults)
        check("配置合法（不合法 bot 会拒绝启动）", True,
              "source={}".format(_cfg.source))
        print("     schema=v{} 群(配置)={} 生效={} 静默={} 筛查={} 推送={} Bot={}".format(
            _cs.SCHEMA_VERSION, len(_cfg.group_specs), len(_cfg.groups),
            len(_cfg.mute), len(_cfg.screen), len(_cfg.push), len(_cfg.bots)))
        if _cfg.migrated_from:
            print("     ⚠️ 磁盘上还是 v{}：本程序会在启动时自动升级 + 备份".format(
                _cfg.migrated_from))
        _ov = _cfg.feature_map()
        if _ov:
            print("     功能/参数覆盖 {} 项（其中按群 {} 项）".format(
                len(_ov), sum(1 for k in _ov if "|" in k)))
        if _cfg.scopes:
            print("     按群集合：{}".format(
                ", ".join("{}={}".format(k, len(v)) for k, v in _cfg.scopes.items())))
        _missing = [b.key for b in _cfg.bots if not (b.resolve_token() or "").strip()]
        check("每个 Bot 的 token 都能解析到", not _missing, _missing)
        _off = [s.cid[:8] for s in _cfg.group_specs.values() if not s.enabled]
        if _off:
            print("     已停用的群（enabled=false，不做任何事）：{}".format(", ".join(_off)))
except Exception as e:                                      # noqa: BLE001
    check("配置自检", False, "{}: {}".format(type(e).__name__, e))

print()
print("人设文本（P12-E）:")
try:
    import personas as _pers

    # 1) 登记册完整：每个话术都要有中文标签（否则页面上只有一串英文 key）
    _nophrase = {}
    for _k in _pers.PERSONAS:
        _miss = sorted(set(_pers.known_phrase_keys(_k)) - set(_pers.PHRASE_LABELS))
        if _miss:
            _nophrase[_k] = _miss
    check("话术都有中文标签（{} 个人设 / {} 条）".format(
        len(_pers.PERSONAS), len(_pers.PHRASE_LABELS)), not _nophrase, _nophrase)

    # 2) 注入配置里的覆盖**不报错**，并且真的改变生效值
    from runtime import config_store as _cs
    _texts = {}
    try:
        _texts = _cs.load(_defaults).personas
    except Exception:                                       # noqa: BLE001
        _texts = {}
    _pers.set_content_overrides(_texts)
    _s = _pers.summary()
    print("     覆盖：人设={} 文本段={} 话术={}".format(
        _s["personas"], _s["fields"], _s["phrases"]))
    for _line in _s["issues"]:
        print("     ⚠️ 体检 " + _line)
    check("人设文本注入不报错", True)
    # 3) 每个群的最终人设都能取到（页面/日志都靠它）
    _keys = {_pers.persona_key_for(_cid) for _cid in _cs.load(_defaults).groups}
    check("每个生效群都有可用人设", _keys <= set(_pers.PERSONAS), _keys)
    # 4) 覆盖生效自证：配置里改过的名字必须真的换掉
    for _k, _v in (_texts or {}).items():
        _nn = _v.get("name_short")
        if _nn:
            _hit = any(_pers.persona_for(_cid)["name_short"] == _nn
                       for _cid in _cs.load(_defaults).groups
                       if _pers.persona_key_for(_cid) == _k)
            check("人设 {} 的新名字「{}」已生效".format(_k, _nn), _hit)
except Exception as e:                                      # noqa: BLE001
    check("人设文本自检", False, "{}: {}".format(type(e).__name__, e))

print()
print("热加载（P12-F）:")
try:
    from runtime import hotload as _hot
    from runtime import features as _F2
    from runtime import tuning as _T2

    check("bot.py 主循环里接了热加载检查",
          "hot_reload_check()" in inspect.getsource(bot.main_loop))
    check("bot.py 在启动后注册了热加载状态",
          "_hotload.configure(" in (GATE_ROOT / "bot.py").read_text(encoding="utf-8"))
    check("运行期来源与启动同一条路（无第二份注入实现）",
          "hotload" in inspect.getsource(bot.apply_config_overrides))

    # 真跑一次：不加改动 ⇒ 必须什么都不做；然后改一份配置喂给它 ⇒ 必须真的换掉
    _hot.configure(dict(bot._FEATURES_DEFAULT_KW), bot._live_structural)
    check("无改动时是 no-op", _hot.maybe_reload() is None)

    import copy as _copy
    import json as _json2
    _cfg_now = _cs.load(_dash._config_defaults())
    _before = _F2.overrides()
    _flip = "insult" if _F2.on("gate-cid", "insult") else "vote"
    _cfg2 = _copy.copy(_cfg_now)
    _cfg2.features = {_flip: not _F2.on("gate-cid", _flip)}
    _rep = _hot.apply_cfg(_cfg2, source="gate")
    check("apply_cfg 真的改了开关",
          _F2.on("gate-cid", _flip) == _cfg2.features[_flip], _rep.get("errors"))
    check("apply_cfg 报告了改动", bool(_rep["changes"]["features"]))
    # 复原（很重要：闸门不能把运行期状态带跑偏）
    _hot.apply_cfg(_cfg_now, source="gate-restore")
    check("复原后与配置一致", _F2.overrides() == _cfg_now.feature_map(),
          _F2.overrides())
    del _before

    # 坏配置不许半途生效
    _raw_bad = _dash._raw_from_store(_dash._config_defaults())
    _raw_bad = _copy.deepcopy(_raw_bad)
    _raw_bad["features"] = {"no_such_feature_xyz": True}
    _tmpcfg = _tmpdir("gate-badcfg-") / "config.json"
    import os as _os2
    _old_cfg_env = _os2.environ.get("DZMM_CONFIG")
    _tmpcfg.write_text(_json2.dumps(_raw_bad, ensure_ascii=False), encoding="utf-8")
    _os2.environ["DZMM_CONFIG"] = str(_tmpcfg)
    _cs._cache = None
    _snap = _F2.overrides()
    _bad = _hot.reload_now()
    check("坏配置被拒（invalid）", _bad.get("invalid") is True, _bad.get("errors"))
    check("坏配置时运行期保持原样", _F2.overrides() == _snap)
    if _old_cfg_env is None:
        _os2.environ.pop("DZMM_CONFIG", None)
    else:
        _os2.environ["DZMM_CONFIG"] = _old_cfg_env
    _cs._cache = None

    # 页面状态：三个页面共用同一段话术
    _ps = _hot.page_state()
    check("page_state 可用", isinstance(_ps, dict) and "pending" in _ps, _ps)
    check("三个界面模块都用共用的状态横幅",
          all("status_block" in (GATE_ROOT / "admin" / f).read_text(encoding="utf-8")
              for f in ("config_ui.py", "features_ui.py", "personas_ui.py")))
except Exception as e:                                      # noqa: BLE001
    check("热加载自检", False, "{}: {}".format(type(e).__name__, e))

print()
print("FAILED: {}".format(fails) if fails else "ALL CHECKS PASSED")
raise SystemExit(1 if fails else 0)
