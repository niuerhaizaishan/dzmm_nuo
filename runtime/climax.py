"""高潮结算族 + 每日记忆报告（P3-9，2026-10-01 自 bot.py 原样抽出）。

`_climax_hits`（发情值满 100 判定）/ `_parse_judge`（结算单 JSON 解析）/
`_judge_pending`（待结算单拉取，AI 生成）/ `_build_climax_context`/
`_build_climax_texts`（高潮正文生成）/ `_publish_memory_report`（记忆汇报）。

依赖注入（同 3-2/3-3 模式）：_judge_pending 的 `load_state` / `_save_group`
由 main_loop 调用点传真身名字 —— `monkeypatch bot.load_state` 接缝不变。
`_active_cid()` 本模块直读 `runtime/ctx`（ContextVar 真源；bot 侧的
`_ACTIVE_CID` 镜像回退在按群并行下本就不可靠）。发送循环 `_handle_climax`
仍住 bot.py（持 re-export 名调用，`asyncio.to_thread(_build_climax_texts, …)`
的源码钉点不受影响）。本模块不许 import bot。
"""
import time
from datetime import datetime

from brains import generate_heat_climax, generate_heat_judge
from dzmm import get_messages_all
from personas import p
from profiles import get_profile
from runtime.ctx import get_active_cid as _active_cid  # P3-9：直读 ctx 真源
from runtime.group_state import _get_memory
from runtime.identity import _load_member_names, get_full_name, is_self
from runtime.members import _archive_text_from_profile, _find_uid_by_name
from runtime.obs import log
from runtime.tuning import get as _tune

def _climax_hits(group_state: dict, cid: str = None):
    """检测发情值≥阈值，返回 (hits, changed)。changed=有跨天重置或高潮（需写回磁盘）.

    `cid` 用来取**按群**的参数（阈值/扣减可调）；不传就用当前处理中的群
    （调用点在主循环里，`_active_cid()` 一定已设置）。
    """
    cid = cid or _active_cid()
    heat = group_state.get("heat_value") or {}
    climax = group_state.get("heat_climax") or {}
    today_climax = group_state.get("heat_climax_today") or {}
    today = datetime.now().strftime("%Y-%m-%d")
    changed = False
    # 跨天重置今日高潮次数
    if group_state.get("climax_date") != today:
        today_climax = {}
        group_state["climax_date"] = today
        changed = True
    hits = []
    for uid, h in list(heat.items()):
        if h >= _tune("heat.climax_threshold", cid):
            hits.append((uid, h))
            heat[uid] = h - _tune("heat.climax_reset", cid)
            climax[uid] = climax.get(uid, 0) + 1
            today_climax[uid] = today_climax.get(uid, 0) + 1
            changed = True
    if changed:
        group_state["heat_value"] = heat
        group_state["heat_climax"] = climax
        group_state["heat_climax_today"] = today_climax
    return hits, changed


def _parse_judge(result: str) -> dict:
    """解析 AI 评定结果 JSON，返回 {name: delta}."""
    import json as _json
    import re as _re
    result = (result or "").strip()
    result = _re.sub(r"```(?:json)?", "", result).strip()
    data = None
    try:
        data = _json.loads(result)
    except Exception:
        m = _re.search(r"\[.*\]", result, _re.DOTALL)
        if m:
            try:
                data = _json.loads(m.group(0))
            except Exception:
                data = None
    out = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                try:
                    delta = int(item.get("delta", 0))
                except Exception:
                    delta = 0
                if name and delta > 0:
                    out[name] = delta
    return out


def _judge_pending(cid: str, *, _save_group, load_state) -> str:
    """批量评定待评发言的发情值（每2小时调用），返回评定结果文本."""
    state = load_state()
    group_state = state.get(cid) or {}
    pending = group_state.get("heat_pending") or {}
    if not pending:
        return ""
    _load_member_names()
    lines = []
    for uid, texts in pending.items():
        name = get_full_name(uid)
        lines.append(name + ": " + " | ".join(texts))
    chat_text = "\n".join(lines)
    try:
        result = generate_heat_judge(chat_text)
    except Exception:
        result = ""
    result = (result or "").strip()
    delta = _parse_judge(result)
    heat = group_state.get("heat_value") or {}
    for name, d in delta.items():
        uid = _find_uid_by_name(name)
        if uid and not is_self(uid):
            heat[uid] = heat.get(uid, 0) + d
    group_state["heat_value"] = heat
    group_state["heat_pending"] = {}
    group_state["heat_last_judge"] = time.time()
    state[cid] = group_state
    _save_group(cid, group_state)
    return result


def _build_climax_context(cid: str, max_count: int = 60) -> str:
    """拉最近群聊内容，组装成「昵称: 内容」上下文，供高潮生成判断场景/施动者。"""
    _load_member_names()
    try:
        msgs = get_messages_all(cid, max_count=max_count)
    except Exception:
        return ""
    lines = []
    for m in msgs[-max_count:]:
        uid = m.get("sent_by", "")
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text or text.startswith("/"):
            continue
        name = get_full_name(uid) if uid else ""
        if not name or name == uid[:8]:
            continue  # 拿不到昵称的跳过，避免 uid 干扰
        lines.append(name + ": " + text[:60])
    return "\n".join(lines)


def _build_climax_texts(cid: str, hits: list, gs: dict) -> list:
    """同步部分：拉上下文 + 给每个命中的成员生成一段色色文字（P12-AA）。

    抽出来是因为它**会调模型和平台接口**：必须整体丢进 `asyncio.to_thread`，
    否则发情值一爆表就把这个群的事件循环堵住十几秒（发送本身还是留在循环里）。
    返回 `[(uid, name, msg), ...]`。
    """
    total_map = gs.get("heat_climax") or {}
    today_map = gs.get("heat_climax_today") or {}
    context = _build_climax_context(cid)  # 拉一次上下文，循环复用
    out = []
    for uid, _h in hits:
        name = get_full_name(uid)
        total = total_map.get(uid, 0)
        today = today_map.get(uid, 0)
        profile = get_profile(uid)
        user_text = _archive_text_from_profile(profile) if profile else "昵称：" + name
        if context:
            user_text += "\n\n【最近群聊上下文】\n" + context
        try:
            text = generate_heat_climax(user_text)
        except Exception:
            text = ""
        text = (text or "").strip().replace("🤖", "")
        tag = "🔥 发情值爆表！今天第" + str(today) + "次高潮，总共第" + str(total) + "次～"
        out.append((uid, name, ("@" + name + " " + tag + "\n\n" + text) if text
                    else ("@" + name + " " + tag)))
    return out


async def _publish_memory_report(sender, cid: str, dry: bool):
    """每日记忆汇报：列出具体记忆内容（原文列表）。"""
    mem = _get_memory(cid)
    if not mem:
        return
    total = len(mem)
    shown = mem[-_tune("memory.report_items", cid):]
    lines = [p("mem_list_head", cid).format(n=total)]
    for i, m in enumerate(shown, 1):
        at_short = (m.get('at') or '')
        if len(at_short) >= 11:
            at_short = at_short[5:]
        lines.append(f"{i}. {m['text']}（{m.get('from', '?')}，{at_short}）")
    if total > len(shown):
        lines.append(p("mem_list_more", cid).format(k=len(shown)))
    text = "\n".join(lines)
    if dry:
        log("[dry] 记忆汇报")
    else:
        ok = await sender.send("🤖 " + text, cid)
        log("✅" if ok else "❌")
