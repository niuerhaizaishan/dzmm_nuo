"""档案同步族（P3-13 从 bot.py 拆出）。

三件事：
  · daily_profile_scan —— 每天一次全群扫昵称/简介/签名/性别，改名播报 +
    档案自动更新（基线 profile_scan.json 跨重启保留；首次只建基线）；
  · run_sync_profiles —— 增量拉新消息 → 提取注册人发言 → 刷 bio/last_seen
    → 滚动总结 → 推进游标（`/同步` 与 cron `--sync` 共用）；
  · run_greet_once / run_memory_report_once —— CLI 一次性模式
    （`--greet` / `--memory-report`）：独立 Playwright profile，避免和主循环抢锁。

**不持有 bot 侧状态**：群集合（groups/mute_cids/room_cids/primary_cid/push_cids）、
群状态读写（load_state/_save_group）、功能开关唯一入口（_feat_on —— bot 的
包装器带"log+放行"回退；⚠️ 注入参数必须沿用 `_feat_on` 名，静态扫描只认
`_feat_on(` 字面量调用，test_p8s4 也禁止与 bot 壳同名 —— 所以真身都带
run_/daily_ 前缀）全部 kw-only 注入；其余（dzmm 平台读口、profiles 读写、
brains 生成、identity/memory/text 叶子、personas、Sender）直接 import。
"""
import asyncio
import json
import time
from datetime import datetime

import personas as _personas
from brains import generate_greeting, generate_profile_summary
from dzmm import (get_members, get_messages, get_messages_all,
                  get_messages_since, get_user)
from profiles import get_profile, load_profiles, save_profiles, upsert_profile
from runtime import ctx
from runtime.climax import _publish_memory_report
from runtime.identity import _name_cache, is_self
from runtime.joins import is_join_msg as _is_join_msg
from runtime.memory import _on_rename, _record_history
from runtime.obs import ROOT, log
from runtime.sender import Sender
from runtime.text import _clean_name, _squash
from runtime.tuning import get as _tune


__all__ = ["daily_profile_scan", "run_sync_profiles", "run_sync_all",
           "run_greet_once", "run_memory_report_once", "scan_load", "scan_save"]


PROFILE_SCAN_FILE = ROOT / "profile_scan.json"  # 全群档案扫描基线（跨重启保留）
PROFILE_SCAN_HOUR = 6      # 每天 06:00 后首次循环执行一次
PROFILE_SCAN_MAX_API = 150  # 单次扫描 get_user 上限（防限流）
PROFILE_NOTICE_MAX = 10     # 单次扫描最多发几条群通知（防刷屏）


def scan_load() -> dict:
    try:
        d = json.loads(PROFILE_SCAN_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def scan_save(d: dict) -> None:
    try:
        PROFILE_SCAN_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def daily_profile_scan(*, groups, mute_cids, room_cids, primary_cid,
                       _feat_on) -> list:
    """每天一次：全群扫昵称 + 已建档成员的简介/签名/性别。
    返回 [(cid, 通知文本)]；首次运行（无基线）只建基线，不发通知。"""
    snap = scan_load()
    prev_all = snap.get("members") or {}
    first_run = not prev_all
    cur = {}
    notices = []
    seen = set()
    api_used = 0
    for cid in groups:
        if cid in mute_cids:  # 静默监控群：不扫档案、不播报
            continue
        if not _feat_on(cid, "profile_scan"):   # P12-A 闸门：这个群今天不扫
            continue
        try:
            members = get_members(cid)
        except Exception:
            continue
        for uid, name in members.items():
            if not name or uid in seen:
                continue
            seen.add(uid)
            prev = prev_all.get(uid) or {}
            info = {"name": name}
            prof = get_profile(uid)
            if prof:
                old = {"name": str(prof.get("name") or ""),
                       "sig": str(prof.get("signature") or ""),
                       "bio": str(prof.get("bio") or "")}
                if api_used < _tune("profile.scan_max_api", cid):
                    api_used += 1
                    u = get_user(cid, uid)
                    if u:
                        info["bio"] = str(u.get("bio") or "")
                        info["sig"] = str(u.get("quirk") or "")
                        info["gender"] = str(u.get("gender") or "")
                # 姓名/签名/简介一起比：有变化 → 记轨迹 + 同步档案（首日静默，不播报历史变化）
                newv = {"name": name,
                        "sig": info.get("sig", old["sig"]),
                        "bio": info.get("bio", old["bio"])}
                if newv != old:
                    _record_history(prof, old, time.strftime("%m-%d %H:%M"))
                    prof["name"] = newv["name"]
                    prof["signature"] = newv["sig"]
                    prof["bio"] = newv["bio"]
                    if info.get("gender"):
                        prof["gender"] = info["gender"]
                    upsert_profile(uid, prof)
            cur[uid] = info
            _name_cache[uid] = name
            if first_run or not prev:
                continue
            old_name = str(prev.get("name") or "")
            rename = bool(old_name) and old_name != name
            bits = []
            if info.get("bio") is not None and str(prev.get("bio") or "") != info["bio"]:
                bits.append("简介换啦：" + (_squash(info["bio"], 36) or "（清空）"))
            if info.get("sig") is not None and str(prev.get("sig") or "") != info["sig"]:
                bits.append("签名换啦：" + (_squash(info["sig"], 24) or "（清空）"))
            g_old = str(prev.get("gender") or "")
            if info.get("gender") and g_old and g_old != info["gender"]:
                bits.append("性别改成「" + info["gender"] + "」咯")
            if rename:
                _on_rename(uid, old_name, name)
                if cid in room_cids:
                    continue  # 房间群照常扫档案，但不播报（客人不该看到后台改名播报）
                txt = "🤖 咦～" + old_name + " 改名成「" + name + "」啦"
                if bits:
                    txt += "，" + "，".join(bits)
                txt += ("，" + _personas.me(cid) + "记住咯～以后就叫你 "
                        + (_clean_name(name) or name) + " 姐姐啦～♡")
                notices.append((cid, txt))
            elif bits:
                if cid in room_cids:
                    continue  # 同上：房间群不播报简介/签名变化
                notices.append((cid, "🤖 咦～" + name + " 姐姐" + "，".join(bits)
                                + "，" + _personas.me(cid) + "偷偷更新档案咯～♡"))
    snap["day"] = time.strftime("%Y-%m-%d")
    snap["members"] = cur
    scan_save(snap)
    log("📇 档案扫描完成：成员 " + str(len(cur)) + "，查简介 " + str(api_used)
        + "，变化 " + str(len(notices))
        + ("（首次运行，只建基线）" if first_run else ""))
    return notices[:max(1, int(_tune("profile.notice_max", primary_cid)))]


def _to_local(sent_at: str) -> str:
    """sent_at(UTC) → 'YYYY-MM-DD HH:MM' 北京时间."""
    from datetime import timedelta, timezone
    try:
        dt = datetime.fromisoformat(sent_at.replace("+00", "+00:00"))
        dt = dt.astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return sent_at[:16]


def _build_profile_text(profile: dict) -> str:
    """从档案构建给 AI 的文本（生成滚动总结用）."""
    lines = ["昵称：" + profile.get("name", "")]
    if profile.get("gender"):
        lines.append("性别：" + profile["gender"])
    if profile.get("signature"):
        lines.append("签名：" + profile["signature"])
    if profile.get("bio"):
        lines.append("简介：" + profile["bio"])
    msgs = profile.get("messages", [])
    if msgs:
        lines.append("发言（" + str(len(msgs)) + "条）：")
        for m in msgs[-30:]:
            lines.append("  - " + m.get("text", "")[:80])
    return "\n".join(lines)


def run_sync_profiles(*, _feat_on, _save_group, load_state) -> str:
    """增量同步：拉新消息 → 提取注册人发言 → 更新档案 → 推进游标."""
    cid = ctx.get_active_cid()
    state = load_state()
    group_state = state.get(cid) or {}
    since = group_state.get("last_sync_at")

    # 1. 拉增量（或首次全量）
    if since:
        msgs = get_messages_since(cid, since)
    else:
        msgs = get_messages_all(cid, max_count=1000)

    if not msgs:
        return "没有新消息，无需同步～"

    profiles = load_profiles()
    if not profiles:
        return "还没有人注册，无需同步～"

    # 2. 提取注册人发言，追加原文（去重：按 at+text 避免重复追加）+ 更新 last_seen
    updated = 0
    active_uids = []  # 本次有新发言的 uid（用于刷新 bio/last_seen）
    for uid, profile in profiles.items():
        their = [m for m in msgs if m.get("sent_by") == uid and not _is_join_msg(m)]
        if not their:
            continue
        active_uids.append(uid)
        msgs_list = profile.get("messages", [])
        seen = {(x.get("at"), x.get("text")) for x in msgs_list}
        for m in their:
            text = (m.get("content") or {}).get("text", "").strip()
            if not text:
                continue
            at = _to_local(m["sent_at"])
            key = (at, text)
            if key in seen:
                continue
            msgs_list.append({"text": text, "at": at})
            seen.add(key)
        msgs_list.sort(key=lambda x: x.get("at", ""))
        profile["messages"] = msgs_list[-200:]
        profile["last_seen"] = _to_local(their[-1]["sent_at"])  # 最新活跃时间
        updated += 1

    # 2.5 刷新 bio/signature/gender（有发言的人，降频防 captcha）
    for uid in active_uids:
        try:
            u = get_user(cid, uid)
            if u:
                profile = profiles.get(uid)
                if not profile:
                    continue
                if u.get("bio"):
                    profile["bio"] = u["bio"]
                if u.get("quirk"):
                    profile["signature"] = u["quirk"]
                if u.get("gender"):
                    profile["gender"] = str(u["gender"])
        except Exception:
            pass
        time.sleep(0.4)  # 降频

    # 3. 更新滚动总结
    # P12-A：`archive_build` 管的是"档案内容（画像/滚动总结）的自动构建"。
    # 关掉它：**发言记录照常落库**（数据不丢），只是不再自动生成画像/总结
    # —— 省掉每个档案一次 LLM 调用，也不会因为画像写得不好而影响体验。
    for uid, profile in profiles.items():
        if profile.get("messages") and _feat_on(cid, "archive_build"):
            try:
                profile["summary"] = generate_profile_summary(_build_profile_text(profile))
            except Exception:
                pass

    # 4. 保存档案（全局整合，跨群发言都归到同一人的档案）
    save_profiles(profiles)

    # 5. 推进游标
    group_state["last_sync_at"] = msgs[-1]["sent_at"]
    state[cid] = group_state
    _save_group(cid, group_state)

    return "同步完成～更新了 " + str(updated) + " 人的发言档案～"


def run_sync_all(*, groups, mute_cids, sync_profiles) -> str:
    """对所有群执行增量同步（供 cron --sync 调用）."""
    results = []
    for cid in groups:
        if cid in mute_cids:  # 静默监控群：不同步档案
            continue
        ctx.set_active_cid(cid)
        try:
            results.append("[" + cid[:8] + "] " + sync_profiles())
        except Exception as e:
            results.append("[" + cid[:8] + "] 同步失败: " + str(e)[:60])
    return "\n".join(results)


async def run_greet_once(period: str, *, groups, push_cids) -> None:
    """发送一次问候到所有群（早/中/晚），用独立 profile 避免和主循环抢锁."""
    sender = Sender(profile_dir="pw_profile_greet", groups=groups)
    try:
        await sender.start()
    except Exception as e:
        log("greet sender init failed: " + str(e)[:80])
        return
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        greeting = generate_greeting(period, today)
    except Exception:
        greeting = ""
    greeting = (greeting or "").strip()
    if not greeting:
        greeting = "姐姐们好呀～"
    for cid in push_cids:  # 只在配置了定时推送的群发问候（裳姐姐群、团本群不发）
        ok = await sender.send("🤖 " + greeting, cid)
        if ok:
            # 读回验证：看最近 5 条里有没有 bot 自己刚发的这条问候。
            # 不能只看最后一条——12:00 午安和「骚度排行」定时撞车，排行会插在中间，
            # 若只看 msgs[-1] 会误判「漏发」→ 补发 → 群里收到两遍午安（2026-08-24 实测）。
            await asyncio.sleep(2)
            try:
                msgs = await asyncio.to_thread(get_messages, cid, 5)
                sent = any(
                    is_self(m.get("sent_by"))
                    and greeting[:8] in (m.get("content") or {}).get("text", "")
                    for m in msgs
                )
                if not sent:
                    ok = await sender.send("🤖 " + greeting, cid)
            except Exception:
                pass
        log("greet[" + period + "] " + cid[:8] + ": " + ("✅" if ok else "❌"))
    await sender.close()


async def run_memory_report_once(*, groups, push_cids) -> None:
    """定时记忆汇报（独立 sender，准点执行，机制同问候）."""
    sender = Sender(profile_dir="pw_profile_greet", groups=groups)
    try:
        await sender.start()
    except Exception as e:
        log("memory-report sender init failed: " + str(e)[:80])
        return
    try:
        for cid in push_cids:
            await _publish_memory_report(sender, cid, dry=False)
    finally:
        await sender.close()
