"""代发起者，写给某个群友的一封信。

与「我的故事」的关系：**同一套取证、同一套引用核验、同一套失败重试与限流**
（`ask_and_render` / `insert_quotes` / 同一组字数上下限）。区别有三处：

1. **读谁的归档**：故事只读发起者本人；这封信读**收信人**的归档 —— 用 TA 说过的原话当依据。
2. **口吻**：「我」= 发起者，「你」= 收信人。这是本功能最容易串的地方（故事里"你"是本人）。
3. **形状**：不分章节。称呼与落款由程序出，模型只写正文段落。

屋主口径（2026-10-07）：

- 「代发起者写」→ 信是发起者的口吻，落款写明是谁托的。
- 「任意」→ 不限制谁能被写；但**归档里 TA 的发言太少就直接说写不了** —— 没料就是编。
- 「不设冷却」→ 目标级冷却默认 **0（关）**，参数留着随时开。
- 「不需要分章节，按照信的格式」→ `{paragraphs, ending}`，称呼/落款程序生成。
- 不想被写的人：环境变量 `DZMM_LETTER_OPTOUT`（逗号分隔 uid）。

**防编造这条底线不动**：引用只能走 `[[message_id]]` 且 ID 必须真实存在于证据里。
另外「我」这边**不许编造共同经历**（"我们一起…"这类具体情节），只许表达心意与感受 ——
否则代笔会把发起者没做过的事写成他们之间的事实。
"""
import asyncio
import json
import os
import random
import re
import threading
import time

import brains
import personas
from runtime import features
from runtime.chat_archive_query import find_senders, story_evidence
from runtime.ctx import PRIMARY_CID
from runtime.obs import log
from runtime.personal_story import (MAX_RESULT, MIN_RESULT, MIN_RESULT_SPARSE,
                                    ask_and_render, date_range, display_name,
                                    insert_quotes)
from runtime.tuning import get as _tune

FAILURE = "糯糯这次没把信写好，稍后再试一下吧～"
USAGE = "格式：/致 某某（也可以 /致某某，或 @小小糯 给某某写封信）。只能写给认证群里的群友～"
NOT_FOUND = "「{who}」？糯糯没在认证群里找到这个人呀～写全一点再试试？"
AMBIGUOUS = "「{who}」对得上好几个人：{names}。写全一点再试一次？"
TOO_LITTLE = "糯糯还不太了解{who}，归档里 TA 说的话太少，写不出有依据的信～"
OPTOUT = "{who}说过不想被写，换个人吧～"
TARGET_BUSY = "刚给{who}写过信啦，过一会儿再来吧～"
SIGN_NOTE = "—— 代笔：{me} · 受「{sender}」所托"

# 2026-10-07 屋主实测：「为什么所有的开头都一样啊，都是我其实很早就注意到你了。」
# 根因是**示例泄漏** —— 提示词里的示例形状第一句就是那句话，模型照着抄。
# 两头一起治：示例改成只示范 JSON 结构（句子明令不许照抄），
# 再加一个**运行时随机**的切入角度，逼它每次换一种进法。
ANGLES = (
    "从 TA 常在什么时间出现说起（比如总是深夜冒泡）",
    "从 TA 说过的一句让你记住的话说起",
    "从 TA 在群里做过的一件具体的事说起",
    "从你第一次注意到 TA 的那个场景说起（要有依据）",
    "从 TA 最近的一条动态说起",
    "从 TA 的口头禅、自称或爱用的说法说起",
    "从 TA 那种一眼就认得出的说话方式说起",
    "从 TA 反复惦记、提过好几次的某件事说起",
)

# `/致某某`（不带空格）必须支持，所以不能像 `/我的故事` 那样要求分隔符 ——
# `/致谢` 这类会被当成"写给谢"的请求，落到「没找到这个人」的提示上，无害。
_COMMAND = re.compile(r"^/致\s*[：:]?\s*(.*)$", re.S)
# @ 形式的目标至少 2 个字：否则「致敬」会被当成"写给敬的一封信"
_ASK_GIVE = re.compile(
    r"^(?:请)?(?:帮我|给我|替我)?(?:写|来)?(?:一封)?给\s*(.{2,}?)\s*(?:写|的)?\s*(?:一封|封)?信[。！～~]?$", re.S)
_ASK_ZHI = re.compile(
    r"^(?:请)?(?:帮我|给我|替我)?(?:写|来)?(?:一封)?致\s*(.{2,}?)\s*(?:的)?(?:一封)?信?[。！～~]?$", re.S)
_RANGE_TAIL = re.compile(r"\s*(\d{4}-\d{2}-\d{2})\s*[~～至]\s*(\d{4}-\d{2}-\d{2})\s*$")

MIN_PARAGRAPHS = 2
MAX_PARAGRAPHS = 4
MAX_PARAGRAPH_CHARS = 1200

_LIMIT = threading.BoundedSemaphore(1)
_LOCK = threading.Lock()
_ACTIVE = set()          # (cid, 发起者 uid, 收信人 uid)
_LAST = {}               # (cid, 发起者 uid, 收信人 uid) -> 冷却到期时刻
_TARGET_LAST = {}        # (cid, 收信人 uid) -> 冷却到期时刻（默认关，见 target_cooldown）


def command_payload(text):
    """`/致 某某`、`/致某某` → 「某某 [日期范围]」；不是这个指令返回 None。"""
    match = _COMMAND.fullmatch((text or "").strip())
    return match.group(1).strip() if match else None


def _split_range(text):
    """把结尾的日期范围切出来 → `(主体, " 2026-08-01~2026-09-30")` 或 `(原文, "")`。"""
    raw = text or ""
    match = _RANGE_TAIL.search(raw)
    if not match:
        return raw.strip(), ""
    return raw[:match.start()].strip(), " " + match.group(0).strip()


def request_payload(text):
    """@ 入口：给某某写封信 / 致某某 / 写给某某的信（都允许尾巴带日期范围）。"""
    command = command_payload(text)
    if command is not None:
        return command
    body, tail = _split_range((text or "").strip())
    for pattern in (_ASK_GIVE, _ASK_ZHI):
        match = pattern.fullmatch(body)
        if match:
            return (match.group(1).strip(" ，。！～~") + tail).strip()
    return None


def enabled(cid):
    return (cid == PRIMARY_CID and personas.persona_key_for(cid) == "nuo"
            and features.on(cid, "personal_letter"))


def split_request(payload):
    """「某某 2026-08-01~2026-09-30」→ (某某, start, end)；日期非法就抛 ValueError。"""
    body, tail = _split_range(payload)
    if not tail:
        return body, "", ""
    match = _RANGE_TAIL.search(payload)
    start, end = match.groups()
    date_range("{}~{}".format(start, end))
    return body, start, end


def optout_uids():
    """不想被写的人：`DZMM_LETTER_OPTOUT=uid1,uid2`（也可以逗号用中文）。"""
    raw = os.environ.get("DZMM_LETTER_OPTOUT", "")
    return {item.strip() for item in raw.replace("，", ",").split(",") if item.strip()}


def salutation(cid, uid, fallback=""):
    """称呼按群里现有口径：女生→「X姐姐」、男生→「X哥」、未知→只叫名字。"""
    from runtime.identity import get_name
    from runtime.members import _gender_of
    try:
        name = get_name(uid) or fallback or uid[:8]
    except Exception:                                           # noqa: BLE001
        name = fallback or uid[:8]
    try:
        gender = _gender_of(uid, cid)
    except Exception:                                           # noqa: BLE001
        gender = "未知"
    if gender == "女生":
        return name + "姐姐"
    if gender == "男生":
        return name + "哥"
    return name


def _has_quote(text):
    """这段文字里有没有合法的引用占位符（`[[id]]`，id 只含字母数字下划线连字符）。"""
    from runtime.personal_story import _QUOTE
    return _QUOTE.search(text or "") is not None


def parse_letter(reply):
    text = (reply or "").strip()
    if len(text) > 20000:
        raise ValueError("letter oversized")
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    # 与故事同口径：多写的字段忽略，别为无害的键把整封作废
    if not isinstance(data, dict) or not {"paragraphs", "ending"} <= set(data):
        raise ValueError("letter schema")
    if not isinstance(data["paragraphs"], list) or not MIN_PARAGRAPHS <= len(data["paragraphs"]) <= MAX_PARAGRAPHS:
        raise ValueError("letter paragraphs")
    for value in data["paragraphs"]:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("letter paragraph")
        if len(value) > MAX_PARAGRAPH_CHARS:
            raise ValueError("letter paragraph oversized")
    ending = data["ending"]
    if not isinstance(ending, str) or not 15 <= len(ending.strip()) <= 160:
        raise ValueError("letter ending")
    if "[[" in ending or "]]" in ending:
        raise ValueError("letter ending quote")
    for value in [item.strip() for item in data["paragraphs"]] + [ending.strip()]:
        if any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("letter control")
    return data


def render_letter(data, evidence, salutation_text, sign_note):
    messages = {item["message_id"]: item for item in evidence["messages"]}
    unknown = []
    parts = []
    for raw in list(data["paragraphs"]) + [data["ending"]]:
        body = insert_quotes(raw.strip(), messages, unknown).strip()
        if "[[" in body or "]]" in body:
            raise ValueError("letter malformed quote")
        parts.append(body)
    if unknown:
        raise ValueError("letter unknown quote")
    if not any(_has_quote(item) for item in data["paragraphs"]):
        # 整封信至少要挂一条真实原话 —— 一封完全不引用的信就没有依据可查了
        raise ValueError("letter missing evidence")
    result = "{who}：\n\n{body}\n\n{sign}".format(
        who=salutation_text, body="\n\n".join(parts), sign=sign_note)
    minimum = MIN_RESULT if len(messages) >= 8 else MIN_RESULT_SPARSE
    if not minimum <= len(result) <= MAX_RESULT:
        raise ValueError("letter length")
    return result


def _generate(cid, requester_uid, target_uid, start, end, timeout, who, sign_note):
    try:
        evidence = story_evidence(target_uid, start=start, end=end)
        if not evidence["available"]:
            return "糯糯还没有找到认证群的聊天归档，暂时写不了信～"
        if len(evidence["messages"]) < 3 or sum(len(item["text"]) for item in evidence["messages"]) < 40:
            return TOO_LITTLE.format(who=who)
        payload = json.dumps({
            "to": who, "from": display_name(requester_uid),
            "angle": random.choice(ANGLES),
            "start": evidence["start"], "end": evidence["end"],
            "target_chars": 500, "messages": evidence["messages"]}, ensure_ascii=False)
        return ask_and_render(
            cid, brains.PERSONAL_LETTER_PROMPT, payload, evidence,
            parse_letter,
            lambda data, ev: render_letter(data, ev, who, sign_note),
            timeout, 2200, "致信")
    except Exception as error:                                  # noqa: BLE001
        log("致信生成失败 [{}] {}".format(cid[:8], type(error).__name__))
        return FAILURE
    finally:
        with _LOCK:
            _ACTIVE.discard((cid, requester_uid, target_uid))
        _LIMIT.release()


async def letter_reply(cid, uid, text, *, via_at=False):
    payload = request_payload(text) if via_at else command_payload(text)
    if payload is None or not enabled(cid) or not uid:
        return None
    try:
        target_text, start, end = split_request(payload)
    except ValueError:
        log("致信日期参数无效 [{}]".format(cid[:8]))
        return USAGE
    if not target_text:
        return USAGE
    hits = find_senders(target_text)
    if not hits:
        return NOT_FOUND.format(who=target_text)
    if len(hits) > 1:
        return AMBIGUOUS.format(who=target_text,
                                names="、".join(item["name"] for item in hits[:5]))
    target_uid, target_name = hits[0]["uid"], hits[0]["name"]
    if target_uid in optout_uids():
        return OPTOUT.format(who=target_name)
    who = salutation(cid, target_uid, target_name)
    sign_note = SIGN_NOTE.format(me=personas.me_full(cid), sender=display_name(uid, uid[:8]))

    now = time.monotonic()
    key = (cid, uid, target_uid)
    with _LOCK:
        for stale in [item for item, expires in _LAST.items() if expires <= now]:
            del _LAST[stale]
        if key in _ACTIVE or _LAST.get(key, 0) > now:
            return "糯糯正在写这封信，或刚写过，等一小会儿再来吧～"
        if len(_LAST) >= 4096 or not _LIMIT.acquire(blocking=False):
            return "糯糯正在写另一封信，稍后再来吧～"
        _ACTIVE.add(key)
        _LAST[key] = now + int(_tune("personal_letter.cooldown", cid))
        target_gap = int(_tune("personal_letter.target_cooldown", cid))
        if target_gap > 0 and _TARGET_LAST.get((cid, target_uid), 0) > now:
            # 默认 0 = 关。开了以后「同一个人被反复写」会被挡住
            _ACTIVE.discard(key)
            _LAST.pop(key, None)
            _LIMIT.release()
            return TARGET_BUSY.format(who=who)
        if target_gap > 0:
            _TARGET_LAST[(cid, target_uid)] = now + target_gap
    timeout = int(_tune("personal_letter.timeout", cid))
    task = asyncio.create_task(asyncio.to_thread(
        _generate, cid, uid, target_uid, start, end, timeout, who, sign_note))
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        log("致信等待超时 [{}]".format(cid[:8]))
        return "这封信写得有些慢，稍后再来找糯糯吧～"
    if result == FAILURE:
        with _LOCK:
            _LAST.pop(key, None)                # 失败不吃冷却，用户可以直接再发一次
    return result
