"""@ 闲聊全流程（P3 Step 3-4，2026-10-01 自 bot.py 原样抽出）。

main_loop 的 `elif at_msg:` 分支：打断保护 → 记忆请求 / 大白话 recap /
大白话运势 / 个人表现查询 / 普通问答（滚动上下文 + 记忆注入 + 收尾块）→
递话重写兜底 → 装饰与排版 → 发送 → 冷却记账 → 立绘补发。

**原样搬入**的纪律与前三步一致：逻辑逐字不变，只动「住在哪」。
随块一起搬来的 @ 闲聊族小件（bot.py 侧改为 import 区同名 re-export，调用点零改动）：
    _ctx_mode / _ctx_only_uid / _pending_line / _FRAME_LEN_RE /
    _apply_frame_length / _ref_of / _CHAT_LAST_REPLY / _chat_cooling /
    _chat_mark_replied / _SOLICIT_REDO / _strip_at / _active_play_hint
（`_recent_turns` / `_seed_turns` / `_remember_turn` 仍住 bot.py —— 它们挂在
`_TURNS` 进程内上下文上，还被批量记账与 dispatch/room 注入共用；按参数注入，
`monkeypatch bot.X` 接缝不变。）

bot 侧依赖以参数注入（本模块**不许 import bot**，见 p8s4 守卫）：
    state / group_state / msgs / _at_msg_obj / _via_quote（数据）+
    _save_group / _mark_inflight / _remember_turn / _recent_turns / _seed_turns
`_gate` / `_feat_on` 与 joins.py/room.py 同一份语义（赋值别名，静态扫描按 `_feat_on(` 数接线）。
"""
import asyncio
import random
import re
import time

from brains import AT_FRAME_PROMPT, generate_reply
from personas import p
from runtime import features as _features
from runtime import dream as _dream
from runtime import intro_photo as _intro_photo
from runtime.dispatch import _ph
from runtime.fortune import fortune_reply, is_fortune_ask
from runtime.group_state import (_get_dj_state, _get_fasao_state,
                                 _get_perf_state, _get_story_state, _remember)
from runtime.identity import get_name, is_self, self_names_longest_first
from runtime.members import _gender_of
from runtime.memory import _extract_remember, _retrieve_memory
from runtime.obs import log
from runtime.perf import OWNER_UID, _detect_perf_query, summarize_person_perf
from runtime.recap import _recap_intent, recap_absent, _is_noise as _recap_is_noise, _ignore_kw as _recap_ignore_kw
from runtime.text import (_ensure_decor, _humanize_chat, _solicit_tail,
                          _strip_solicit_tail)
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

# ---------------------------------------------------------------- @ 闲聊族小件（自 bot.py 搬入）

def _ctx_mode(cid: str) -> int:
    """P12-BE：@ 回复读哪一段上下文。0=全群（默认，零回归）/ 1=双边 / 2=双边（仅屋主）。"""
    try:
        return int(_tune("reply.ctx_mode", cid) or 0)
    except Exception:                                          # noqa: BLE001
        return 0


def _ctx_only_uid(cid: str, uid: str) -> str:
    """按 `reply.ctx_mode` 决定"只留谁"（返回空串 = 不筛，走全群）。"""
    mode = _ctx_mode(cid)
    if mode <= 0 or not uid:
        return ""
    if mode == 2 and uid != OWNER_UID:
        return ""                                              # 只有屋主享受双边
    return uid

def _pending_line(mem, per_item: int = 40, max_items: int = 5) -> str:
    """P12-AQ：把"她手上真记着的事"拼成一行，填进 `AT_FRAME_PROMPT` 的 `{pending}`。

    为什么要有这一行：屋主 2026-09-29 问"治一下汇报内容真实性"。现场是
    **没人托付她任何事**时，被问"有什么事要跟我说吗"，她会自己编：
      "你上次让我记着的那件事，要不要现在说呀？"（没有这件事）
      "待办我都整理好啦"（没有待办）
      "糯米团子新口味配方 / 供应商报价单放在你桌上了"（群里根本没这些事）
    探针（中性历史 + 不注入记忆）实测 3/3 出问题。

    只加一句"别编"没用 —— "秘书被问事就该报事"的惯性压过规则。所以把**事实依据**
    摆到她眼前：有就逐条列出（也就是记忆里真有的），没有就明确写「（空）」，
    并规定汇报只能讲这一行或聊天记录里真出现过的。

    条目会截断（`per_item`）并限量（`max_items`）：这一行是每条 @ 都要带的固定开销。
    """
    items = [(m.get("text") or "").strip() for m in (mem or [])]
    items = [t[:per_item] for t in items if t]
    return "；".join(items[:max_items]) or "（空）"

# 【写多长】那一行（收尾块里唯一讲篇幅的条目）——P12-BD 按群覆盖它，见 `_apply_frame_length`
_FRAME_LEN_RE = re.compile(r"^【写多长】.*$", re.M)


def _apply_frame_length(frame: str, length: str) -> str:
    """P12-BD：按群**替换**收尾块里的【写多长】那一行（`chat.frame_length`）。

    屋主 2026-09-30："她说话太像机器人了。"量出来的主因是**排版**（93% 带空行分段、
    中位 116 字 vs 真人 7 字），但提示词里那句"2~6 句、100~300 字起步"是**通用口径**：
    它在"认真答问题"时是对的，在"闲聊"时就变成了"每句都要写成小作文"。
    所以给一个**按群的长度口径**：写了就整行换掉（省得两个口径互相打架），
    没写就一字不动（默认群零回归）。

    ⚠️ 只换这一行：`{who}`/`{q}`/`{pending}`/【怎么回】都还在，P12-AP/AQ/AU 的
    话题锚点与"别编待办"不受影响。
    """
    if not frame:
        return frame
    length = str(length or "").strip()
    if not length:
        return frame
    return _FRAME_LEN_RE.sub("【写多长】" + length, frame, count=1)

def _ref_of(m: dict) -> dict:
    """别人"右键点击回复"某条消息时，平台塞进来的**被引用消息**（P12-AE）。

    2026-09-27 线上实测的原始形状（`chatroom.getMessages`，40 条里 3 条有）：
        content.reference = {"id": "<uuid>", "sentBy": "<uid>",
                             "content": {"text": "被引用的原文", "type": "text"}}
    注意原文在 `reference.content.text`（**不是** `reference.text`），两层都认。

    ⚠️ 只有**入站**有这个东西：官方 Bot API 的出站没有"回复某条"的字段
    （`tools/probe_reply_capability.py` 探过 15 个候选，全无校验）。
    """
    c = m.get("content") if isinstance(m, dict) else None
    if not isinstance(c, dict):
        return {}
    r = c.get("reference") or c.get("quote") or c.get("reply")
    if not isinstance(r, dict):
        return {}
    inner = r.get("content") if isinstance(r.get("content"), dict) else {}
    return {
        "id": str(r.get("id") or r.get("message_id") or ""),
        "sent_by": str(r.get("sentBy") or r.get("sent_by") or ""),
        "text": str(inner.get("text") or r.get("text") or ""),
    }

# ---------------------------------------------------------------- P12-BQ：@ 闲聊的"人味"闸门
# D1（屋主 2026-09-30 批准）：**同一个人连着 @ 她不必每条都答**。
# 量出来的事实：真人闲聊大多数时候是"说完就停"，也不会秒回每一条；而她是条条必答。
# 只作用于 @ 闲聊那条路（`chat`），指令/运势/房间触发一律不受影响。
_CHAT_LAST_REPLY: dict = {}


def _chat_cooling(cid: str, uid: str) -> int:
    """这个人还在冷却里吗？返回剩余秒数（0 = 可以答）。

    刻意**不静默丢消息**：命中时调用方会打一行日志（"冷却中，这条不接"），
    而且只是"这一轮不接话"，不是把消息从队列里抹掉。
    """
    secs = float(_tune("chat.reply_cooldown", cid) or 0)
    if secs <= 0 or not uid:
        return 0
    left = secs - (time.time() - float(_CHAT_LAST_REPLY.get(cid + "|" + uid, 0) or 0))
    return int(left) if left > 0 else 0


def _chat_mark_replied(cid: str, uid: str) -> None:
    """记一次"刚跟这个人说过话"（只在他那条真的发出去之后调）。"""
    if uid:
        _CHAT_LAST_REPLY[cid + "|" + uid] = time.time()

# e（屋主 2026-09-30 批准）：收尾在"递话"时重写一次用的指令。
# 只写"别做什么"（一句话），别把【怎么回】那一整套再抄一遍 —— 那会把话题锚点冲淡。
_SOLICIT_REDO = ("重写你刚才那句：**不要以提问或派活收尾**（别写「要不要…」「说清楚」「我等着」），"
                 "不要贴指令菜单，不要倒计时。说完就停。")

def _strip_at(text: str, cid: str = None) -> str:
    """去掉 @我 的前缀，返回纯提问（`cid` 决定本群我可能被叫的名字）.

    ⚠️ 必须**长名优先**（`self_names_longest_first`）：名字之间可能互为前缀，
    短名先剥会把长名的残余留进 prompt（`@小小糯-bot` → `-bot`）。
    见 runtime/identity.py 里那个函数的说明。
    """
    for name in self_names_longest_first(cid):
        if name:
            text = text.replace("@" + name, "", 1)
    return text.strip()

def _active_play_hint(cid: str) -> str:
    """返回当前独占型玩法进行中的「打断保护」提示，无则返回 None.

    P12-BP（2026-09-30）：这几句原先**硬编码在 bot.py 里**（公演 / 剧情那两条），
    绕过话术表 ⇒ 换人设时漏旧口吻（若瑾那套会冒出"哦～…啦～"）。现在四条都走话术表：
    代码默认值**逐字等于原来的硬编码串**（认证群零回归），想换口吻就在人设话术里覆盖。
    """
    if _get_perf_state(cid).get("active"):
        return _ph(cid, "play_busy_perf",
                   "🎭 公演进行中哦～暂无法响应，想参与就直接发言，等公演结束后再试～")
    if _get_fasao_state(cid).get("active"):
        return _ph(cid, "play_busy_fasao", "🎲 发骚进行中哦～暂无法响应其他指令，想继续发 /继续，想结束发 /结束～")
    if _get_dj_state(cid).get("active"):
        return _ph(cid, "play_busy_diaojiao", "🔗 调教进行中哦～暂无法响应其他指令，想参与发 /命令 xxx，想结束发 /调教结束～")
    if _get_story_state(cid).get("active"):
        return _ph(cid, "play_busy_story",
                   "🎬 剧情进行中哦～暂无法响应其他指令，想投票发 /选 X，想结束发 /剧情结束～")
    return None



# ---------------------------------------------------------------- @ 闲聊主链

async def handle_at_chat(sender, cid, at_msg, at_sender, dry, group_state, state, msgs,
                         _at_msg_obj, _via_quote, *,
                         _save_group, _mark_inflight, _remember_turn,
                         _recent_turns, _seed_turns):
    """@ 闲聊主链（main_loop 的 `elif at_msg:` 分支，P3-3-4 起住在这里）。

    依赖注入见模块 docstring；group_state 就地更新；old_last_id 由调用方
    先记、块内 raise RuntimeError 传 main_loop 外层 except 回滚（P12-BC）。
    """
    # P12-AW：运势卡这类"卡片回复"不补装饰（原来在 main_loop 每批开头置 False，
    # 搬成函数后语义等价：本分支每批至多走一次）。
    _no_decor = False
    _dream_hit = False

    # 独占玩法进行中时，@ 提示进行中（打断保护）
    active_hint = _active_play_hint(cid)
    if active_hint:
        reply = "🤖 " + active_hint
        log("🚫 [" + cid[:8] + "] @消息被打断保护")
        if not dry:
            ok = await sender.send(reply, cid)
            log("✅" if ok else "❌")
    else:
        # 被 @ 了（非公演中）
        reply = ""
        log("👋 [" + cid[:8] + "] @消息: " + at_msg[:40])
        group_state["last_id"] = msgs[-1]["message_id"]
        state[cid] = group_state
        _save_group(cid, group_state)
        _mark_inflight(cid, (_at_msg_obj or msgs[-1]).get("message_id"))   # P12-BC
        q = _strip_at(at_msg, cid)  # 去 @前缀，得到纯提问
        # ⚠️ 2026-10-02：**双保险**。触发层（mainloop）已经挡掉"别家 bot 的游戏噪音"，
        # 这里再挡一次 —— 记忆库一旦被垃圾污染就是**不可逆**的（要人工清），
        # 而漏挡的代价只是"这一条没被当成记忆指令"。
        # 宁可漏记，不可错记。现场见 mainloop 里那段注释（茶水间 09:21:56）。
        try:
            _noisy = _recap_is_noise(q or "", _recap_ignore_kw(cid), cid)
        except Exception:                                      # noqa: BLE001
            _noisy = False
        _dream_hit = _dream.request_payload(q) is not None
        mem_content = _extract_remember(q) \
            if (_feat_on(cid, "memory_remember") and not _noisy and not _dream_hit) else None
        if _dream_hit:
            reply = await _dream.dream_reply(cid, at_sender, q, via_at=True) \
                if not _noisy and _feat_on(cid, "dream") else None
            _no_decor = True
        elif mem_content is not None:
            # 自然语言记忆请求：@小小糯 记住 XXX
            if not mem_content:
                reply = p("mem_ask", cid)
            else:
                _remember(cid, mem_content, get_name(at_sender))
                reply = p("mem_saved", cid)
        elif _recap_intent(q) and _feat_on(cid, "recap"):
            # 大白话入口：@小小糯 我不在的时候群里发生了什么
            reply = await recap_absent(
                cid, at_sender, q,
                (_at_msg_obj or {}).get("message_id"))
            if not reply:
                reply = ""
        elif is_fortune_ask(q) and _feat_on(cid, "fortune"):
            # P12-AW：大白话运势入口 —— @小小糯 算运势 / 今日运势怎么样。
            # 屋主 2026-09-30："@小小糯 算运势（包括之类的话）也可以触发"。
            # 走 `fortune_reply`（带当天的卡缓存：茶水间冷却内原样重发）。
            # 修之前这类话会落到下面的 chat 分支，由模型**即兴编**
            # （线上实测："今日运势要抽一张的哦，我抽到的是「月亮」"）。
            reply = await asyncio.to_thread(
                fortune_reply, at_sender, cid)
            _no_decor = True     # 运势卡不补装饰（见下面 _ensure_decor）
        else:
            perf_q = _detect_perf_query(at_msg) \
                if _feat_on(cid, "person_summary") else None
            if perf_q:
                reply = await asyncio.to_thread(summarize_person_perf, perf_q[0], perf_q[1])
            else:
                # 普通问答：带上下文（含**自己说过的话**）+ 注入记忆
                # P12-AC：上下文条数与回复长度都按群可调；整块可以关。
                if not _feat_on(cid, "chat"):
                    reply = None
                else:
                    _ctxn = int(_tune("reply.context_msgs", cid))
                    # P12-AD：上下文来自本群滚动记录（含我们自己的
                    # 历史回复），而不是"这一批新消息"；并排除当前
                    # 这一条提问 —— 它下面会以问句身份进 ctx，
                    # 否则同一句话喂两遍（实测她会答"说两遍…"）。
                    _ask_mid = (_at_msg_obj or {}).get("message_id")
                    await asyncio.to_thread(_seed_turns, cid)
                    # P12-BE：`reply.ctx_mode` 决定"读谁的记录"。
                    # 默认 0 = 全群（今天的行为）；1/2 = **双边**（我 + 他）。
                    # 屋主 2026-09-30："我回复她，她会读取之前很多人的记录、
                    # 一一批判回复 —— 能只读我和她的吗？"
                    _only = _ctx_only_uid(cid, at_sender)
                    ctx = _recent_turns(cid, _ctxn, exclude_mid=_ask_mid,
                                        only_uid=_only)
                    if _only:
                        log("👥 [{}] 双边上下文（只带我和 {}）→ {} 条"
                            .format(cid[:8], get_name(at_sender), len(ctx)))
                    # P12-AE：右键回复我的那条 —— 把**被引用的原文**
                    # 也摆进来（万一它已经滚出上下文窗口）。
                    # 已经在里面就不重复塞：这轮修的就是"同一句话
                    # 被喂两遍"的毛病，别在新功能上又犯一次。
                    if _via_quote:
                        _ref = _ref_of(_at_msg_obj or {})
                        _rtxt = (_ref.get("text") or "").strip()
                        if _rtxt and not any(c["text"] == _rtxt
                                             for c in ctx):
                            _who = get_name(_ref.get("sent_by", ""))
                            ctx.append({"sender": "我（被引用的那条）"
                                        if is_self(_ref.get("sent_by", ""))
                                        else _who,
                                        "text": _rtxt})
                        log("🔗 [" + cid[:8] + "] 这是引用回复（引用了 "
                            + ("我自己" if _ref.get("sent_by")
                               and is_self(_ref["sent_by"])
                               else "别人的话") + "）")
                    # P12-BN A1（2026-09-30）：这里原来用 `_gender_label`
                    # —— 它**只看本地档案**；档案里没性别（茶水间大部分人
                    # 都没档案）就返回"未知"，而下面那行会把"未知"的标签
                    # **整个省掉** ⇒ 模型拿到的只是"顾影"两个字、**零性别
                    # 信息** ⇒ 按惯常语气叫了"姐姐"（当天实测：8:52 那条）。
                    # 运势卡（P12-BA）走的是 `_gender_of`（档案优先 + 实时
                    # 查平台）所以那边一直是对的 —— 两条路必须同源。
                    # ⚠️ `_gender_of` 会发平台请求 ⇒ 必须在线程里调（@ 分支
                    # 其它平台调用都是这个写法）；查到的性别会写回档案，
                    # 之后不再查。
                    glabel = await asyncio.to_thread(_gender_of, at_sender, cid)
                    _who = get_name(at_sender) + ("（" + glabel + "）"
                                                  if glabel != "未知" else "")
                    # P12-AP：当前这句不再混在历史里当普通一行 ——
                    # 它是一块**独立的收尾块**（`AT_FRAME_PROMPT`），
                    # 明写"现在跟你说话的人是谁、他这一句是什么、
                    # 只回这一句"。原因见 `brains.AT_FRAME_PROMPT` 上方注释：
                    # 混在历史里时，一句"摸摸头"会被上下文和记忆读成
                    # "糯糯姐冒头了"，她就会对着不相干的人念待办。
                    # P12-AA：记忆检索内部会调模型（两级精筛），
                    # 必须放工作线程 —— 紧邻的 generate_reply 本来就包了，
                    # 这一处漏了，等于每条带记忆注入的 @ 消息都堵一下。
                    # ⚠️ P12-AQ：**必须排在收尾块之前** —— 收尾块要带上
                    # "她手上真记着的事"（`_pending_line`），
                    # 那是治"空手时编待办"的**事实依据**。
                    mem = await asyncio.to_thread(
                        _retrieve_memory, cid, q,
                        _tune("memory.inject", cid))
                    # P12-BE d：双边模式下**记忆也按人过滤** ——
                    # 只留"提到当前这个人"的条目。否则上下文是双边的、
                    # 记忆里却端出别人的事，她照样会去点评别人
                    # （屋主说的"一一批判"另一半来源就在这里）。
                    if _only and mem:
                        _nm = get_name(at_sender)
                        _keep = [m for m in mem if _nm and _nm in (m.get("text") or "")]
                        if len(_keep) != len(mem):
                            log("  🧠 记忆按人过滤：{} → {} 条"
                                .format(len(mem), len(_keep)))
                        mem = _keep
                    # P12-BD：按群的长度口径（`chat.frame_length`）。
                    # 写在收尾块里而不是话术表里 —— 它只对"@ 闲聊"
                    # 生效，换掉整行是为了不让两个口径互相打架；
                    # 没配的群这一句是空操作。
                    _frame = _apply_frame_length(
                        AT_FRAME_PROMPT.replace(
                            "{who}", _who).replace(
                            "{q}", q).replace(
                            "{pending}", _pending_line(mem)),
                        str(_tune("chat.frame_length", cid) or "").strip())
                    ctx.append({"sender": "",
                                "text": _frame})
                    if mem:
                        mem_lines = "\n".join("- " + m["text"] for m in mem)
                        ctx.insert(0, {"sender": "记忆", "text": "你之前记住的相关的事：\n" + mem_lines})
                    reply = await asyncio.to_thread(
                        generate_reply, ctx,
                        max_tokens=int(_tune("reply.max_tokens", cid)),
                        context_msgs=_ctxn)
        if reply is None and _dream_hit:
            return
        reply = (reply or "").strip()
        if reply:
            # P12-BQ e（屋主 2026-09-30 批准）：收尾在"递话"时兜一下。
            # 量出来的事实：她 67% 的回复以提问或派活收尾（真人 6%）。
            # 顺序是**先重写、再不成就删最后那句纯招揽** —— 直接删容易把
            # 话说残（P12-AL 的教训）。卡片（`_no_decor`）与玩法正文不碰。
            if _tune("chat.solicit_guard", cid) and not _no_decor:
                _hit = _solicit_tail(reply)
                if _hit:
                    log("🪞 [{}] 收尾像递话（{}）→ 重写一次".format(cid[:8], _hit))
                    try:
                        _again = await asyncio.to_thread(
                            generate_reply, list(ctx) + [
                                {"sender": "系统", "text": _SOLICIT_REDO}],
                            max_tokens=int(_tune("reply.max_tokens", cid)),
                            context_msgs=int(_tune("reply.context_msgs", cid)))
                    except Exception as _e:            # noqa: BLE001
                        _again = ""
                        log("  ⚠️ 重写失败（{}），改用删句兜底".format(str(_e)[:60]))
                    _again = (_again or "").strip()
                    if _again and not _solicit_tail(_again):
                        reply = _again
                    else:
                        _cut = _strip_solicit_tail(reply)
                        log("  ⚠️ 重写还是递话 → 删掉最后那句"
                            if _cut != reply else "  ⚠️ 重写还是递话（无处可删）")
                        reply = _cut
            # P12-AH：@ 对话补足装饰（❤️ 与"！！"）——
            # 屋主要求"@ 若瑾说话时多加一些"；提示词实测只能到
            # "❤️ 6/6 条、！3/6 条"，所以这里确定性兜一下。
            # `p("chat_decor", cid)` 为空 = 关闭（其他群/人设不动）。
            # P12-AW：运势卡**跳过这一步** —— `bang` 那半段默认开，
            # 正文里没有"！"时会把**最后一个"。"换成"！！"**，
            # 把卡上的点评句改掉（指令入口那条路本来就不走后处理）。
            # P12-BD：整块按概率 + 排版收口（都只走这条路）。
            #   ① `_humanize_chat`：空行折成单换行、最多 5 行 ——
            #      93% 带空行分段是"一眼机器人"的最大单一特征；
            #      放在装饰**之前**：先收版式，再补末尾的 ❤️。
            #   ② 概率：默认 1.0 = 今天的行为；上班群给 0.3/0.5，
            #      让"每条都带 ❤️ 和 ！！"变成偶尔。
            if not _no_decor:
                if _tune("chat.humanize", cid):
                    reply = _humanize_chat(reply)
                _heart = p("chat_decor", cid)
                _bang = True
                if _heart and random.random() >= float(_tune("chat.decor_prob", cid)):
                    _heart = ""
                if random.random() >= float(_tune("chat.bang_prob", cid)):
                    _bang = False
                reply = _ensure_decor(reply, _heart, bang=_bang)
            reply = "🤖 " + reply
            log("💬 " + reply[:200])
            _remember_turn(cid, "我", reply[2:].strip())  # P12-AD
            if dry:
                log("[dry] skip")
            else:
                ok = await sender.send(reply, cid)
                log("✅" if ok else "❌")
                if ok:
                    # P12-BQ D1：只有真发出去了才起冷却（别把失败也算进去）
                    _chat_mark_replied(cid, at_sender)
                # P12-L：@ 提问里问"你是谁"，同样补一张立绘
                if ok and q and not _dream_hit:
                    await _intro_photo.maybe_send(
                        sender, cid, q, reason="@提问")
        else:
            # 生成失败（空回复），抛异常让 except 统一回滚 last_id
            raise RuntimeError("生成失败(空回复): @消息")
