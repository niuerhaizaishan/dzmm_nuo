"""从认证群本人归档生成多维度、约四百字的聊天画像。

2026-10-07 与「我的故事」同批改造：
- **允许出现引号** —— 旧版禁止成品里出现任何引号字符（`“”「」『』"`），
  真模型 6/6 全死在这条（写「剧情游戏」这种带直角引号的功能名就整篇作废）。
- **字数窗口放宽**（150～900，目标约 450）：旧版的 280～550 是**插入真实引录之后**
  的长度，模型写 `[[id]]` 时算不出来，实测既撞过 618 字（超）也撞过不足 280 字。
- **同一条发言被引两次不再致命**：重复引用是文风问题，不是编造问题，不值得整篇作废。
- 底线不动：引用只能走 `[[message_id]]`，ID 必须真实存在，查不到就拒绝整篇。
"""
import asyncio
import json
import re
import threading
import time

import brains
import personas
from runtime import features
from runtime.chat_archive_query import portrait_evidence
from runtime.ctx import PRIMARY_CID
from runtime.obs import log
from runtime.personal_story import ask_and_render, date_range, has_quote, insert_quotes
from runtime.tuning import get as _tune

INTRO = "从已有聊天看，你在群里呈现出这些特点："
SCOPE = "这些只是你在群聊里留下的一部分侧面。"
FAILURE = "糯糯这次没整理好你的画像，稍后再试一下吧～"
USAGE = "发「/我的画像」，也可发「/我的画像 2026-08-01~2026-09-30」指定日期。只能生成你自己的画像～"
_COMMAND = re.compile(r"^/我的画像(?=$|\s|[：:])\s*[：:]?\s*(.*)$", re.S)
_ASK = re.compile(r"^(?:(?:请)?(?:帮我|给我)(?:写|生成|整理|分析)(?:一下|一份|一篇)?(?:我的)?画像|我的画像)(?=$|\s|[：:，。！～~])\s*[：:]?\s*(.*?)\s*[。！～~]*$", re.S)
_BAD_TITLE = re.compile(r"[\[\]#*\n\r\t]|第[一二三四五六七八九十]+章|\d{4}")

MIN_DIMENSIONS = 3
MAX_DIMENSIONS = 5
MAX_DIMENSION_CHARS = 900
MIN_RESULT = 150
# 同样故意比提示词里写的上限（550）松一点：模型算不准插入引文后的字数。
# 认证群是官方 Bot API **整段一条发**，这里卡长度只为"读起来别太长"。
MAX_RESULT = 800
# 重试次数住在 `personal_story.MAX_ATTEMPTS`（`ask_and_render` 是共用的），这里不重复定义
_LIMIT = threading.BoundedSemaphore(1)
_LOCK = threading.Lock()
_ACTIVE = set()
_LAST = {}


def command_payload(text):
    match = _COMMAND.fullmatch((text or "").strip())
    return match.group(1).strip() if match else None


def request_payload(text):
    command = command_payload(text)
    if command is not None:
        return command
    match = _ASK.fullmatch((text or "").strip())
    return match.group(1).strip(" ，。！～~") if match else None


def enabled(cid):
    return cid == PRIMARY_CID and personas.persona_key_for(cid) == "nuo" and features.on(cid, "personal_portrait")


def parse_portrait(reply):
    text = (reply or "").strip()
    if len(text) > 20000:
        raise ValueError("portrait oversized")
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    # 与「我的故事」同口径：多写的字段忽略，别为无害的键把整篇作废
    if not isinstance(data, dict) or not {"dimensions", "ending"} <= set(data):
        raise ValueError("portrait schema")
    if not isinstance(data["dimensions"], list) or not MIN_DIMENSIONS <= len(data["dimensions"]) <= MAX_DIMENSIONS:
        raise ValueError("portrait dimensions")
    titles = set()
    for dimension in data["dimensions"]:
        if not isinstance(dimension, dict) or not isinstance(dimension.get("title"), str) \
                or not isinstance(dimension.get("text"), str):
            raise ValueError("portrait dimension schema")
        title, body = dimension["title"], dimension["text"]
        if not isinstance(title, str) or not 2 <= len(title.strip()) <= 24:
            raise ValueError("portrait title")
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_DIMENSION_CHARS:
            raise ValueError("portrait text")
        normalized = re.sub(r"\s+", "", title)
        if normalized in titles or _BAD_TITLE.search(title):
            raise ValueError("portrait duplicate or chronological title")
        titles.add(normalized)
    ending = data["ending"]
    if not isinstance(ending, str) or not 15 <= len(ending.strip()) <= 120:
        raise ValueError("portrait ending")
    if "[[" in ending or "]]" in ending:
        raise ValueError("portrait ending quote")
    for value in [ending.strip()] + [dimension[key]
                                     for dimension in data["dimensions"]
                                     for key in ("title", "text")]:
        if any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("portrait control")
    return data


def render_portrait(data, evidence):
    messages = {item["message_id"]: item for item in evidence["messages"]}
    unknown = []
    sections = [INTRO]
    for number, dimension in enumerate(data["dimensions"], 1):
        # 一个维度就是一条"关于你"的判断，必须挂着真实原话 —— 不许空口断言
        if not has_quote(dimension["text"]):
            raise ValueError("portrait missing evidence")
        body = insert_quotes(dimension["text"], messages, unknown)
        if "[[" in body or "]]" in body:
            raise ValueError("portrait malformed quote")
        sections.append("{}. {}\n{}".format(number, dimension["title"].strip(), body.strip()))
    if unknown:
        raise ValueError("portrait unknown quote")
    sections.append(SCOPE + data["ending"].strip())
    result = "\n\n".join(sections)
    if not MIN_RESULT <= len(result) <= MAX_RESULT:
        raise ValueError("portrait length")
    return result


def _generate(cid, uid, start, end, timeout):
    try:
        evidence = portrait_evidence(uid, start=start, end=end)
        messages = evidence["messages"]
        if not evidence["available"]:
            return "糯糯还没有找到认证群的聊天归档，暂时没法整理你的画像～"
        if len(messages) < 6 or len({item["text"] for item in messages}) < 4 or sum(len(item["text"]) for item in messages) < 100:
            return "这段时间的文字还不够支持多个维度的画像，换个更长的日期范围，或再积累些聊天吧～"
        payload = json.dumps({"start": evidence["start"], "end": evidence["end"],
                              "target_chars": 500, "messages": messages},
                             ensure_ascii=False)
        return ask_and_render(cid, brains.PERSONAL_PORTRAIT_PROMPT, payload, evidence,
                              parse_portrait, render_portrait, timeout, 1800, "我的画像")
    except Exception as error:                                  # noqa: BLE001
        log("我的画像生成失败 [{}] {}".format(cid[:8], type(error).__name__))
        return FAILURE
    finally:
        with _LOCK:
            _ACTIVE.discard((cid, uid))
        _LIMIT.release()


async def portrait_reply(cid, uid, text, *, via_at=False):
    payload = request_payload(text) if via_at else command_payload(text)
    if payload is None or not enabled(cid) or not uid:
        return None
    try:
        start, end = date_range(payload)
    except ValueError:
        log("我的画像日期参数无效 [{}]".format(cid[:8]))
        return USAGE
    now = time.monotonic()
    with _LOCK:
        for key in [key for key, expires in _LAST.items() if expires <= now]:
            del _LAST[key]
        if (cid, uid) in _ACTIVE or _LAST.get((cid, uid), 0) > now:
            return "糯糯正在整理，或刚写过你的画像，等一小会儿再来吧～"
        if len(_LAST) >= 4096 or not _LIMIT.acquire(blocking=False):
            return "糯糯正在整理另一份画像，稍后再来吧～"
        _ACTIVE.add((cid, uid))
        _LAST[(cid, uid)] = now + int(_tune("personal_portrait.cooldown", cid))
    timeout = int(_tune("personal_portrait.timeout", cid))
    task = asyncio.create_task(asyncio.to_thread(_generate, cid, uid, start, end, timeout))
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        log("我的画像等待超时 [{}]".format(cid[:8]))
        return "这次整理画像有些慢，稍后再来找糯糯吧～"
    if result == FAILURE:
        with _LOCK:
            _LAST.pop((cid, uid), None)
    return result
