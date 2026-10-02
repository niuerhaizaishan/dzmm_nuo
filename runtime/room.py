"""房间群消息处理（P3 Step 3-3，2026-10-01 自 bot.py 原样抽出）。

玫瑰房的管家回应，一条链按优先级：
    女鬼退场（屋主回来）→ 女鬼期间（摘花警告 / 其余静默）→
    客人指令婉拒 → 男生摘花放鬼 → 屋主命令女鬼上场 →
    固定自我介绍 → 星座运势 → 兜底 AI 管家对话（含调戏计数）。

**原样搬入**的纪律与前三步一致：逻辑逐字不变，只动「住在哪」。
随块一起搬来的房间族小件：is_open_room_cmd / _room_male / _strike_get /
_strike_set（bot.py 侧改为 import 区同名 re-export，`bot.is_open_room_cmd`
等调用点零改动）。

bot 侧依赖以参数注入（本模块**不许 import bot**，见 p8s4 守卫）：
    state / _save_group / _mark_inflight / _remember_turn
`_gate` / `_feat_on` 与 joins.py 同一份语义（赋值别名，静态扫描按 `_feat_on(` 数接线）。
"""
import asyncio
import time

import personas as _personas
from brains import generate_reply
from personas import (ROOMS, ROOM_GHOST_BACK, ROOM_GHOST_COOLDOWN,
                      ROOM_GHOST_MAX_H, ROOM_GHOST_TEXT, ROOM_GHOST_WARN,
                      ROOM_GHOST_WARN_COOLDOWN, ROOM_GUEST_REFUSE,
                      ROOM_LEWD_WORDS, NUONUO_UID, VV_UID, p)
from dzmm import get_user
from profiles import get_profile, upsert_profile

from runtime import features as _features
from runtime import ghost_photo as _ghost_photo
from runtime import intro_photo as _intro_photo
from runtime import room_layout as _room_layout
from runtime.dispatch import INTRO_TEXT
from runtime.identity import get_name, is_self, mentions_self
from runtime.obs import log
from runtime.sign import _is_flower_grab, _room_owner, _sign_reply
from runtime.tuning import get as _tune

# ---------------------------------------------------------------- 基础件

def _gate(cid: str, key: str) -> bool:
    """功能开关的安全封装 —— 与 bot._feat_on 同一份语义（fail-open + 记日志）。

    本模块的调用点统一写 `_feat_on(群, "键")`（下面有赋值别名），
    让 test_feature_registry 的静态扫描把这里的接线也数进去。
    """
    try:
        return _features.on(cid or "", key)
    except Exception as e:                                     # noqa: BLE001
        log("features: 判定失败 key=" + str(key) + " " + str(e)[:60])
        return True


_feat_on = _gate  # 字面别名：调用点写 `_feat_on(...)`（原因见模块 docstring）

# ---------------------------------------------------------------- 房间族小件（自 bot.py 搬入）

# ⚠️ 不加名字也要放行（`/查` 后面空着也照样走命令分支）：那样会落到"看不懂的指令"
# 话术，比"这间屋子只有 Vv姐姐 能吩咐我哦～"更贴切。
# 2026-10-02 屋主：`/注册` 也开放 —— 客人在玫瑰房可以给自己建档
# （代他人注册本来就有 reg_self_only 闸门拦着，这里只放行"注册自己"）。
ROOM_OPEN_CMDS = ("/查", "/whois", "/资料", "/注册")


def is_open_room_cmd(cmd: str) -> bool:
    """这条指令在房间群里是否对所有人开放（见 `ROOM_OPEN_CMDS`）."""
    c = str(cmd or "").strip()
    return any(c.startswith(pre) for pre in ROOM_OPEN_CMDS)


def _room_male(cid: str, uid: str) -> bool:
    """这人是不是男生（档案优先；档案没写就实时查一次并写回档案）。阻塞调用，放线程里跑."""
    def g(v):
        v = str(v or "").strip().lower()
        if v in ("male", "man", "男", "m"):
            return "male"
        if v in ("female", "lesbian", "女", "f", "百合"):
            return "female"
        return ""
    p = get_profile(uid) or {}
    r = g(p.get("gender"))
    if r:
        return r == "male"
    try:
        u = get_user(cid, uid) or {}
    except Exception:
        u = {}
    r = g(u.get("gender"))
    if r and p:
        try:  # 顺手写回档案，避免每次都查
            p["gender"] = r
            upsert_profile(uid, p)
        except Exception:
            pass
    return r == "male"


def _strike_get(group_state: dict, uid: str, reset_h: int) -> int:
    """该客人当前累计调戏次数（超过 reset_h 小时未犯 → 视为 0）."""
    d = (group_state.get("rose_strikes") or {}).get(uid) or {}
    n = int(d.get("n") or 0)
    if n and time.time() - float(d.get("at") or 0) > reset_h * 3600:
        return 0
    return n


def _strike_set(group_state: dict, uid: str, n: int) -> None:
    d = group_state.get("rose_strikes") or {}
    if n <= 0:
        d.pop(uid, None)
    else:
        d[uid] = {"n": n, "at": time.time()}
    group_state["rose_strikes"] = d


# ---------------------------------------------------------------- 管家回应主链

async def handle_room(sender, cid, room_hit, _room_obj, msgs, dry,
                      group_state, state,
                      _save_group, _mark_inflight, _remember_turn):
    """玫瑰房管家回应（main_loop 的 `elif room_hit:` 分支，P3-3-3 起住在这里）。

    依赖注入：state / _save_group / _mark_inflight / _remember_turn 是 bot 侧的
    （见模块 docstring）；group_state 就地更新；old_last_id 由调用方记录并回滚。
    """
    # ============ 房间群：管家回应（被点名 / 主人指令 / 客人点单）============
    r_uid, r_text = room_hit
    cfg = ROOMS.get(cid) or {}
    log("🌹 [" + cid[:8] + "] 房间消息: " + r_text[:40])
    _layout_after = ""      # P12-N：这句回复之后要补哪张布局图
    _ghost_after = ""       # P12-O：这句回复之后要不要补女鬼图（原因）
    group_state["last_id"] = msgs[-1]["message_id"]
    state[cid] = group_state
    _save_group(cid, group_state)
    _mark_inflight(cid, (_room_obj or msgs[-1]).get("message_id"))   # P12-BC：房间消息也会被杀掉
    # ---- 女鬼状态：屋子里没人了（门和窗被带走），直到屋主回来 ----
    ghost = group_state.get("rose_ghost") or {}
    ghost_on = bool(ghost.get("active"))
    if ghost_on and \
            time.time() - float(ghost.get("ts") or 0) > ROOM_GHOST_MAX_H * 3600:
        ghost_on = False  # 安全兜底：屋主太久没回来，自动收拾好（防房间永久哑掉）
        group_state["rose_ghost"] = {"active": False, "ts": time.time()}
        state[cid] = group_state
        _save_group(cid, group_state)
        log("👻 [" + cid[:8] + "] 女鬼状态超时，自动恢复")
    if ghost_on and _room_owner(cid, r_uid) and _feat_on(cid, "room_ghost"):
        # 屋主回来了 → 屋子复原
        group_state["rose_ghost"] = {"active": False, "ts": time.time()}
        state[cid] = group_state
        _save_group(cid, group_state)
        log("🌹 [" + cid[:8] + "] " + get_name(r_uid)[:12] + " 回来，女鬼退场")
        reply = _personas.fill(ROOM_GHOST_BACK, cid, who=get_name(r_uid))
        _layout_after = _room_layout.DEFAULT_LAYOUT   # 屋子复原 = 玫瑰房
    elif ghost_on and _feat_on(cid, "room_ghost"):
        # 女鬼还在：再摘花 → 警告+恐吓；其他话一律不接（小小糯在门外）
        if _is_flower_grab(r_text) and \
                time.time() - float(group_state.get("rose_ghost_warn_ts") or 0) \
                >= ROOM_GHOST_WARN_COOLDOWN:
            group_state["rose_ghost_warn_ts"] = time.time()
            state[cid] = group_state
            _save_group(cid, group_state)
            log("👻 [" + cid[:8] + "] 女鬼期间又摘花，警告+恐吓")
            reply = _personas.fill(ROOM_GHOST_WARN, cid)
        else:
            reply = None  # 静默：管家不在，门也没有
    elif r_text.startswith("/") and not _room_owner(cid, r_uid) \
            and not is_open_room_cmd(r_text) \
            and _feat_on(cid, "room_refuse"):
        reply = ROOM_GUEST_REFUSE  # 客人用指令 → 固定话术婉拒，不惊动 AI
    elif (_is_flower_grab(r_text)
          and _feat_on(cid, "room_flower")
          and _feat_on(cid, "room_ghost")
          and await asyncio.to_thread(_room_male, cid, r_uid)
          and time.time() - float(group_state.get("rose_ghost_ts") or 0)
          >= ROOM_GHOST_COOLDOWN):
        # 男生摘花 → 放出红衣女鬼（固定剧本，不走 AI）
        group_state["rose_ghost_ts"] = time.time()
        group_state["rose_ghost"] = {"active": True, "ts": time.time()}
        state[cid] = group_state
        _save_group(cid, group_state)
        log("👻 [" + cid[:8] + "] " + get_name(r_uid)[:12] + " 摘花，放出红衣女鬼")
        reply = _personas.fill(ROOM_GHOST_TEXT, cid)
        _ghost_after = "摘花放鬼"     # P12-O：放出女鬼 → 补那张图
    elif (_feat_on(cid, "room_ghost")
          and _ghost_photo.wants_photo(r_text, cid, r_uid)):
        # 屋主命令进入女鬼场景（2026-09-27 屋主："要进状态啊"）。
        # 与"男生摘花"共用同一套状态与固定剧本，链路因此闭环：
        # 命令 → 女鬼上场（图）→ 屋里没人接话 → 屋主回来 →
        # 屋子复原（`ROOM_GHOST_BACK`）+ 自动补玫瑰房那张图。
        group_state["rose_ghost_ts"] = time.time()
        group_state["rose_ghost"] = {"active": True, "ts": time.time()}
        state[cid] = group_state
        _save_group(cid, group_state)
        log("👻 [" + cid[:8] + "] " + get_name(r_uid)[:12] + " 命令女鬼上场")
        reply = _personas.fill(ROOM_GHOST_TEXT, cid)
        _ghost_after = "命令"
    elif (_intro_photo.photo_url(cid)
          and _feat_on(cid, "intro")
          and _intro_photo.wants_photo(r_text, cid)):
        # P12-Q：问"你是谁"用**固定的自我介绍**（不再让 AI 现编），
        # 发完文案后由发送点的钩子补上那张立绘。
        # 只在本群配了立绘时生效（玫瑰房），认证群照旧走 AI。
        reply = p("intro_text", cid) or _personas.fill(INTRO_TEXT, cid)
    elif (_feat_on(cid, "horoscope")
          and (_sr := await _sign_reply(
              cid, r_uid, r_text,
              "Vv姐姐" if r_uid == VV_UID else
              ("糯糯姐" if r_uid == NUONUO_UID else "客人")))):
        # 星座运势（待客服务）：确定性算分 + 管家口吻点评
        log("🔮 [" + cid[:8] + "] 星座: " + r_text[:30])
        reply = _sr
    else:
        n = _strike_get(group_state, r_uid, int(cfg.get("strike_reset_h") or 12))
        ctx = []
        for m in msgs[-5:]:
            muid = m.get("sent_by", "")
            if is_self(muid):
                continue
            mtxt = (m.get("content") or {}).get("text", "").strip()
            if mtxt:
                ctx.append({"sender": get_name(muid), "text": mtxt})
        ctx.insert(0, {"sender": "系统", "text":
                       "（正在跟你说话的这位是「" + get_name(r_uid)
                       + "」，"
                       + ("是这间屋子的主人，要听 TA 的吩咐"
                          if _room_owner(cid, r_uid) else "是一位来串门的客人")
                       + "）"})
        ctx.insert(0, {"sender": "系统", "text":
                       "（如果这句话是在调戏你，就是 TA 的第 " + str(n + 1)
                       + " 次，按第 " + str(n + 1)
                       + " 档演出，并在回复最末尾加上 [[STRIKE]]）"})
        # 房间回复的 token 上限跟群里的人设走：玫瑰房要"4～8 句"，
        # 认证群那份（默认 400）保持原样。见 tuning room.reply_max_tokens。
        reply = await asyncio.to_thread(
            generate_reply, ctx,
            max_tokens=int(_tune("room.reply_max_tokens", cid)))
        # 调戏计数：AI 的 [[STRIKE]] 标记为主，关键词兜底（必须点名了管家）
        # P12-A：`room_lewd` 只管"三级递进"这套计数/送出机制；
        # 关掉它，管家照常回话、只是不再数次数（不是把房间关了）
        _hit = _feat_on(cid, "room_lewd") and "[[STRIKE]]" in (reply or "")
        if not _hit:
            _named = mentions_self(r_text, cid)
            _hit = _named and any(w in r_text for w in ROOM_LEWD_WORDS)
            if _hit:
                log("🌹 [" + cid[:8] + "] 调戏（关键词兜底命中）")
        if _hit:
            nn = n + 1
            if nn >= int(cfg.get("strike_limit") or 3):
                nn = 0  # 已送出门口，重新计数
                log("🌹 [" + cid[:8] + "] " + get_name(r_uid)[:12]
                    + " 第三次调戏，送出去（纯台词）")
            _strike_set(group_state, r_uid, nn)
            state[cid] = group_state
            _save_group(cid, group_state)
    if reply is None:
        pass  # 静默（女鬼期间管家不在，不接话）
    else:
        reply = reply.replace("[[STRIKE]]", "").strip()
        if reply:
            reply = "🤖 " + reply
            log("💬 " + reply[:200])
            _remember_turn(cid, "我", reply[2:].strip())  # P12-AD
            if dry:
                log("[dry] skip")
            else:
                ok = await sender.send(reply, cid)
                log("✅" if ok else "❌")
                # P12-L：问到"你是谁"这类，补一张立绘（同一条去重）
                if ok and r_text:
                    await _intro_photo.maybe_send(
                        sender, cid, r_text, reason="自然语言")
                # P12-N：改布局 → 补对应的房间图；屋子复原 → 玫瑰房那张
                if ok:
                    if _layout_after:
                        await _room_layout.send_layout(
                            sender, cid, _layout_after, reason="屋子复原")
                    elif r_text:
                        await _room_layout.maybe_send(
                            sender, cid, r_text, r_uid, reason="改布局")
                # P12-O：放女鬼 / 命令进入女鬼场景 → 补那张图
                if ok:
                    if _ghost_after:
                        await _ghost_photo.send(
                            sender, cid, reason=_ghost_after)
                    elif r_text:
                        await _ghost_photo.maybe_send(
                            sender, cid, r_text, r_uid, reason="命令")
        else:
            raise RuntimeError("生成失败(空回复): 房间消息")
