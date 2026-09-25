"""长期记忆 + 档案变更轨迹（P8 第 4 步从 bot.py 搬出）。

两件事放在一起是因为它们是同一条链：
/记住 写入的记忆要能被 /回忆 找回来（先关键词粗排、再交给 AI 精筛），
而"人变了"（改名/改签名/改简介）也要留痕，查人时才能拿新旧对比做反差。

`_retrieve_memory` 的两级筛选是刻意的：先用纯本地的词面匹配把候选压到几条，
再花一次 LLM 调用精筛 —— 直接全量喂 LLM 又慢又贵。
"""
import re
import time

from brains import _call_llm
from dzmm import get_user
from profiles import get_profile, load_profiles, upsert_profile
from runtime import ctx
from runtime.group_state import _get_memory, _remember
from runtime.identity import get_full_name
from runtime.members import HISTORY_MAX, _profile_history
from runtime.obs import log
import state_store

__all__ = [
    "_extract_names", "_extract_keywords", "_fast_rank", "_ai_filter",
    "_retrieve_memory", "_append_memory_ref", "_extract_remember",
    "is_remember_cmd", "is_recall_cmd",
    "_record_history", "_on_rename", "_refresh_profile_latest",
]


MEMORY_TRIGGERS = ["帮我记住", "帮我记", "记住", "记一下", "记着", "别忘了", "牢记"]


MEMORY_FILTER_PROMPT = """你是记忆筛选助手。下面给你一个「问题」和若干条「记忆」，判断哪些记忆与问题相关、能帮助回答。

输出规则：
- 只输出选中的记忆条目原文，每条一行
- 不加序号、不加解释、不改写原文
- 最多选 {limit} 条
- 一条都不相关就输出：无
"""


def _extract_names(text: str) -> list:
    """从文本提取出现的成员昵称（人名是记忆最强锚点）。"""
    found = []
    try:
        for p in load_profiles().values():
            n = p.get("name", "")
            if n and len(n) >= 2 and n in text:
                found.append(n)
    except Exception:
        pass
    return found


def _extract_keywords(text: str) -> list:
    """提取问题关键词（2-3 字片段/英文单词），用于弱匹配。"""
    clean = re.sub(r'[@～~！!？?。，,\s]+', ' ', text).strip()
    words = []
    for token in re.findall(r'[A-Za-z0-9]+|[\u4e00-\u9fa5]+', clean):
        if re.match(r'^[A-Za-z0-9]+$', token):
            if len(token) >= 2 and token not in words:
                words.append(token)
        else:
            for i in range(len(token) - 1):
                seg = token[i:i + 3]
                if len(seg) >= 2 and seg not in words:
                    words.append(seg)
        if len(words) >= 8:
            break
    return words[:8]


def _fast_rank(cid: str, question: str, top: int = 20) -> list:
    """快速粗筛：人名命中(+4) + 关键词命中(+1) 打分，取 top。"""
    mem = _get_memory(cid)
    if not mem:
        return []
    names = _extract_names(question)
    kws = _extract_keywords(question)
    scored = []
    for m in mem:
        text = m["text"]
        s = 0
        for n in names:
            if n and n in text:
                s += 4
        for kw in kws:
            if kw and kw in text:
                s += 1
        if s > 0:
            scored.append((s, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:top]]


def _ai_filter(question: str, candidates: list, limit: int = 10) -> list:
    """AI 精筛：从候选里挑与问题相关的（≤limit 条）。"""
    try:
        items = "\n".join(f"- {m['text']}" for m in candidates)
        content = f"问题：{question}\n\n记忆条目：\n{items}"
        reply = _call_llm(MEMORY_FILTER_PROMPT.format(limit=limit), content, max_tokens=500)
        picked = []
        for line in (reply or "").splitlines():
            line = line.strip().lstrip("-·•* ")
            if not line or line == "无":
                continue
            for m in candidates:
                if m["text"] == line or (len(line) >= 4 and (line in m["text"] or m["text"] in line)):
                    if m not in picked:
                        picked.append(m)
                    break
            if len(picked) >= limit:
                break
        return picked if picked else candidates[:limit]
    except Exception:
        return candidates[:limit]


def _retrieve_memory(cid: str, question: str, limit: int = 10) -> list:
    """两级检索：快速粗筛 → AI 精筛。"""
    mem = _get_memory(cid)
    if not mem:
        return []
    cand = _fast_rank(cid, question, top=20)
    if not cand:
        cand = mem[-limit:]
    if len(cand) <= limit:
        return cand
    return _ai_filter(question, cand, limit)


def _append_memory_ref(text: str, cid: str, subject: str, limit: int = 10) -> str:
    """在文本后追加与 subject 相关的记忆参考。"""
    try:
        mem = _retrieve_memory(cid, subject, limit)
        if mem:
            ref = "\n".join("· " + m["text"] for m in mem)
            return text + "\n\n（以下是小小糯记住的与" + subject + "相关的事，回答时可参考，但不要主动提'这是记忆'）\n" + ref
    except Exception:
        pass
    return text


def _extract_remember(text: str):
    """从自然语言提取"记住"的内容。返回内容，None=不是记忆请求或无意义内容。"""
    for kw in MEMORY_TRIGGERS:
        idx = text.find(kw)
        if idx >= 0:
            content = text[idx + len(kw):].strip()
            # 循环清理前缀残留（语气词/标点/"忆" 等）
            while True:
                cleaned = False
                for p in ["一下", "一下下", "一件事", "这件事", "这个", "要", "我", "给我",
                          "，", ",", "！", "!", "。", "？", "?", "～", "~", "忆"]:
                    if content.startswith(p):
                        content = content[len(p):].strip()
                        cleaned = True
                        break
                if not cleaned:
                    break
            content = content.strip(" ，,！!。？?～~·")
            # 过滤无意义：太短 / 纯标点 / 纯语气词
            if not content or len(content) < 2:
                return None
            if all(c in "，,！!。？?～~ 的了吗呢啊吧呀哦" for c in content):
                return None
            return content
    return None


def is_remember_cmd(cmd: str):
    """识别记忆指令，返回要记的内容（空串=是但内容空），None=不是。"""
    for p in ["/记住", "/记一下", "/记", "/别忘了"]:
        if cmd.startswith(p):
            return cmd[len(p):].strip()
    return None


def is_recall_cmd(cmd: str) -> bool:
    """识别回忆指令。"""
    return any(cmd.startswith(p) for p in ["/回忆", "/记忆", "/记得什么", "/我让你记住"])


def _record_history(profile: dict, old: dict, at: str) -> None:
    """把「变更前」的姓名/签名/简介一起存档进轨迹（留最近 HISTORY_MAX 版）。"""
    hist = _profile_history(profile)
    hist.append({"at": at, "name": str(old.get("name") or ""),
                 "sig": str(old.get("sig") or ""), "bio": str(old.get("bio") or "")})
    del hist[:-HISTORY_MAX]
    profile.pop("name_history", None)


def _on_rename(uid: str, old: str, new: str) -> int:
    """检测到改名：把记忆里的旧昵称替换成新昵称。返回替换条数。"""
    try:
        if not old or not new or old == new:
            return 0
        state = state_store.load_state()
        n = 0
        for cid, gs in state.items():
            if not isinstance(gs, dict):
                continue
            for m in (gs.get("memory") or []):
                if isinstance(m, dict):
                    t = str(m.get("text") or "")
                    if old in t:
                        m["text"] = t.replace(old, new)
                        n += 1
        if n:
            state_store.save_state(state)
        log("🔄 改名 " + old + " → " + new + "，已更新记忆 " + str(n) + " 条")
        return n
    except Exception as e:
        log("改名记忆替换失败: " + str(e)[:60])
        return 0


def _refresh_profile_latest(uid: str, profile: dict) -> None:
    """实时刷新某人 name/bio/signature/gender 到本地档案（简介会变，羞辱/查人要用最新找反差）。失败静默。"""
    try:
        u = get_user(ctx.get_active_cid(), uid)
        if not u:
            return
        changed = False
        old = {"name": str(profile.get("name") or ""),
               "sig": str(profile.get("signature") or ""),
               "bio": str(profile.get("bio") or "")}
        # 改名检测（对比档案里的旧名）
        new_name = str(u.get("fullName") or "").strip()
        if new_name:
            profile["name"] = new_name
        if u.get("bio"):
            profile["bio"] = u["bio"]
        if u.get("quirk"):
            profile["signature"] = u["quirk"]
        if u.get("gender"):
            profile["gender"] = str(u["gender"])
        newv = {"name": str(profile.get("name") or ""),
                "sig": str(profile.get("signature") or ""),
                "bio": str(profile.get("bio") or "")}
        if newv != old:
            # 姓名/签名/简介一起进轨迹，查人时就能看到 TA 以前的样子
            _record_history(profile, old, time.strftime("%m-%d %H:%M"))
            if old["name"] and newv["name"] and old["name"] != newv["name"]:
                _on_rename(uid, old["name"], newv["name"])
            changed = True
        if changed:
            upsert_profile(uid, profile)
    except Exception:
        pass
