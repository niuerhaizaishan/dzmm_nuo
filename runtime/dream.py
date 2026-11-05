"""小小糯单轮解梦：两群硬范围、短回复、限频和有限后台模型任务。"""
import asyncio
import json
import re
import threading
import time

import brains
import personas
from runtime import features
from runtime.ctx import PRIMARY_CID
from runtime.dream_sources import SOURCES, candidates
from runtime.obs import log
from runtime.tuning import get as _tune
from runtime.text import _normalize_command_prefix

TEA_CID = "4fc4a2fd-465a-4436-9884-dbb5e60c6af2"
ALLOWED_CIDS = frozenset((PRIMARY_CID, TEA_CID))
FOOTER = "仅作自我觉察和文化趣读，不预示现实。"
FAILURE = "糯糯这次没整理好，稍后再试一下吧～"
_COMMAND = re.compile(r"^[/#]解梦(?=$|\s|[：:])\s*[：:]?\s*(.*)$", re.S)
_ASK = re.compile(r"(?:^解梦|(?:请)?(?:帮我|给我|帮忙|请)解(?:一下)?梦)(?:一下)?(?=$|\s|[：:，。！？])|(?:请)?(?:帮我|给我|帮忙|请)分析(?:一下)?(?:这个|这)?梦|(?:这个梦|这梦)(?:到底|究竟)?(?:是什么意思|意味着什么|怎么解)")
_NEGATIVE = re.compile(r"(?:不(?:要|用|想)|别)\s*(?:帮我|给我)?(?:解梦|分析)")
_LIMIT = threading.BoundedSemaphore(2)
_LOCK = threading.Lock()
_LAST = {}
_ACTIVE = set()
_UNSAFE = re.compile(r"(?:一定|必定|注定|预示|预言).{0,12}(?:死亡|生病|发财|怀孕|出轨|灾祸)|你(?:会|将).{0,8}(?:死|患|得病)|你(?:有|患有)(?:抑郁症|精神病)|周公|古籍|《|》")


def command_payload(text: str):
    match = _COMMAND.fullmatch(_normalize_command_prefix(text))
    return match.group(1).strip() if match else None


def request_payload(text: str):
    command = command_payload(text)
    if command is not None:
        return command
    text = (text or "").strip()
    if text.startswith(("/", "#")):
        return None
    if _NEGATIVE.search(text) or not _ASK.search(text):
        return None
    return _ASK.sub("", text).strip(" ，。！？:：～~")


def in_scope(cid: str) -> bool:
    return cid in ALLOWED_CIDS and personas.persona_key_for(cid) == "nuo"


def enabled(cid: str) -> bool:
    return in_scope(cid) and features.on(cid, "dream")


def is_hash_request(cid: str, text: str) -> bool:
    text = _normalize_command_prefix(text)
    return (cid == TEA_CID and text.startswith("#")
            and command_payload(text) is not None and enabled(cid)
            and bool(_tune("dream.hash_cmd", cid)))


def parse_card(reply: str) -> dict:
    text = (reply or "").strip()
    if len(text) > 4096:
        raise ValueError("dream oversized")
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"reading", "advice", "source_id"}:
        raise ValueError("dream schema")
    for key in ("reading", "advice", "source_id"):
        if not isinstance(data[key], str) or len(data[key]) > 700:
            raise ValueError("dream field")
        if any(ord(char) < 32 and char not in "\n\t" for char in data[key]):
            raise ValueError("dream control")
    if not data["reading"].strip() or not data["advice"].strip():
        raise ValueError("dream empty")
    if data["source_id"] and data["source_id"] not in {item["id"] for item in SOURCES}:
        raise ValueError("dream source")
    return data


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    ending = max(head.rfind(mark) for mark in "。！？；")
    return head[:ending + 1] if ending >= limit // 2 else head.rstrip("，、： ") + "…"


def _render(data: dict, sources: tuple, budget: int) -> str:
    reading, advice = data["reading"], data["advice"]
    if _UNSAFE.search(reading + advice) or any(
            item["quote"] in reading + advice for item in SOURCES):
        raise ValueError("dream unsafe output")
    source = next((item for item in sources if item["id"] == data["source_id"]), None)
    if data["source_id"] and source is None:
        raise ValueError("dream unmatched source")
    tail = "\n" + FOOTER
    if source:
        tail = ("\n古籍趣读：「" + source["quote"] + "」（《周公解夢》·"
                + source["chapter"] + "）" + tail)
    advice = _shorten(advice, 35)
    available = budget - len(tail) - len(advice) - len("糯糯看梦～\n\n试试：")
    if available < 60:
        tail = "\n" + FOOTER
        available = budget - len(tail) - len(advice) - len("糯糯看梦～\n\n试试：")
    return "糯糯看梦～\n" + _shorten(reading, available) + "\n试试：" + advice + tail


def _generate(dream: str, cid: str, uid: str, timeout: int) -> str:
    try:
        sources = candidates(dream)
        budget = min(int(_tune("dream.max_chars", cid)), 180 if cid == TEA_CID else 280)
        payload = {"dream": dream, "max_chars": budget,
                   "candidates": [{"id": item["id"], "chapter": item["chapter"],
                                   "quote": item["quote"]} for item in sources]}
        reply, finish = brains._call_llm_raw(
            brains.DREAM_PROMPT, json.dumps(payload, ensure_ascii=False),
            max_tokens=650, timeout=timeout)
        if finish == "length":
            raise ValueError("dream truncated")
        return _render(parse_card(reply), sources, budget)
    except Exception as error:
        log("解梦生成失败 [{}] {}".format(cid[:8], type(error).__name__))
        return FAILURE
    finally:
        with _LOCK:
            _ACTIVE.discard((cid, uid))
        _LIMIT.release()


async def dream_reply(cid: str, uid: str, text: str, *, via_at: bool = False):
    dream = request_payload(text) if via_at else command_payload(text)
    if dream is None or not enabled(cid):
        return None
    if via_at:
        if command_payload(text) is None and not _tune("dream.natural", cid):
            return None
    elif not ((text.startswith("/") and cid == PRIMARY_CID) or is_hash_request(cid, text)):
        return None
    if not dream:
        command = "#解梦" if cid == TEA_CID else "/解梦"
        return "糯糯在～发「" + command + " 梦境」，说说发生了什么、最后怎样、你的感受。"
    input_limit = int(_tune("dream.input_limit", cid))
    if len(dream) > input_limit:
        return "梦境有点长啦～压缩到 {} 字以内，留下经过、结尾和感受就好。".format(input_limit)
    now = time.monotonic()
    cooldown = int(_tune("dream.cooldown", cid))
    with _LOCK:
        expired = [key for key, expires in _LAST.items() if expires <= now]
        for key in expired:
            del _LAST[key]
        if (cid, uid) in _ACTIVE or _LAST.get((cid, uid), 0) > now:
            return "糯糯刚看过啦～等一小会儿再来吧。"
        if len(_LAST) >= 4096 or not _LIMIT.acquire(blocking=False):
            return "糯糯正看着别的梦，稍后再来呀～"
        _LAST[(cid, uid)] = now + cooldown
        _ACTIVE.add((cid, uid))
    timeout = int(_tune("dream.timeout", cid))
    task = asyncio.create_task(asyncio.to_thread(_generate, dream, cid, uid, timeout))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        log("解梦等待超时 [{}]".format(cid[:8]))
        return "糯糯这次看得有点慢，稍后再试吧～"
