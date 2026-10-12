"""从认证群本人归档生成分阶段的个人故事。

2026-10-07 改造（屋主口径：「不用准确的时间，只需要第一阶段什么之类的模糊分段」、
「可以出现引号」，随后又改成「不要用第一阶段、第二阶段这样的话，就用"一、XXX"取一个题目」）：

- **分段不带日期、也不叫"第N阶段"**：模型给每段取一个短标题，程序编上号 ——
  渲染成 `一、刚进群那阵` / `二、开始折腾功能`。旧版要求阶段边界首尾覆盖证据区间、
  且每条引用必须落在本阶段日期区间内 —— 真模型实测 **6/6 过不了**
  （`unverified quote`：边界是模型自己划的，它得同时让"引用选得对"和"边界划得准"自洽，
  做不到）。
- **允许出现引号**：旧版禁止成品里出现**任何**引号字符（`“”「」『』"`），
  模型写「剧情游戏」这种带直角引号的功能名就整篇作废
  （画像改前 6/6 死在这条）。
- **一条底线不动**：引用只能走 `[[message_id]]`，且 ID 必须真的在证据里 ——
  查不到就拒绝整篇，不给编造留口子。第一次不过会**带拒绝原因重试一次**。
"""
import asyncio
import json
import re
import threading
import time
from datetime import date

import brains
import personas
from runtime import features
from runtime.chat_archive_query import story_evidence
from runtime.ctx import PRIMARY_CID
from runtime.obs import log
from runtime.tuning import get as _tune

FAILURE = "糯糯这次没整理好你的故事，稍后再试一下吧～"
USAGE = "发「/我的故事」回顾已有聊天，也可发「/我的故事 2026-08-01~2026-09-30」指定日期。只能生成你自己的故事～"
_COMMAND = re.compile(r"^/我的故事(?=$|\s|[：:])\s*[：:]?\s*(.*)$", re.S)
_ASK = re.compile(r"^(?:(?:请)?(?:帮我|给我)(?:写|生成|整理)(?:一下|一份|一篇)?(?:我的)?故事|我的故事)(?=$|\s|[：:，。！～~])\s*[：:]?\s*(.*?)\s*[。！～~]*$", re.S)
_RANGE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*[~～至]\s*(\d{4}-\d{2}-\d{2})")
_QUOTE = re.compile(r"\[\[([A-Za-z0-9_-]+)\]\]")
# 占位符外面可能已经被模型加了引号（现在允许引号了）—— 一起吃掉，
# 统一换成一对规范引号，免得渲染出「“原话”」这种双引号。
_QUOTED = re.compile(r"[“\"「『]?\[\[([A-Za-z0-9_-]+)\]\][”\"」』]?")
# 阶段编号由**程序**出（一、二、三），模型只负责标题文字 —— 见 2026-10-07 屋主口径
_NUMERALS = ("一", "二", "三", "四")
# 模型有时还是会在正文里自己写"第一阶段："，剥掉
_LEADING_LABEL = re.compile(r"^\s*第[一二三四五六七八九十\d]+(?:阶段|部分|章)[：:，,。\s]*")
_BAD_TITLE = re.compile(r"[\[\]#*\n\r\t]|第[一二三四五六七八九十\d]+(?:阶段|部分|章)|\d{4}")

MAX_STAGES = 4
MAX_TITLE_CHARS = 16
MAX_STAGE_CHARS = 1600
MIN_RESULT = 300            # 材料够多时的成品下限（太薄的"故事"会被拒 → 重试）
MIN_RESULT_SPARSE = 60      # 材料很少时放宽
# 屋主口径 2026-10-07：**约 500 字**（先要 700～800，实测后嫌长，连压两档），
# 一条群消息发完。硬上限故意比提示词里写的上限松（提示词说超 650 作废）——
# 模型算不准插入引文后的真实字数，留一点余量，别让"多写了一点点"变成整篇失败。
# ⚠️ `personal_letter` 直接 import 这两个常量（屋主要求两者长度一样），改这里两家一起变。
MAX_RESULT = 700
MAX_ATTEMPTS = 3            # 第一次没过就带原因重试（实测偶发"某个维度忘了带引用"）
RETRY_HINT = ("\n\n【上一次的回答被程序拒绝了】\n拒绝原因：{}\n"
              "请重新输出一个**完整、合规**的 JSON 对象，不要再犯同一个错误。\n"
              "上一次的回答（只看错在哪，不要照抄）：\n{}")

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
    return cid == PRIMARY_CID and personas.persona_key_for(cid) == "nuo" and features.on(cid, "personal_story")


def date_range(payload):
    if not payload:
        return "", ""
    match = _RANGE.fullmatch(payload)
    if not match:
        raise ValueError("story range")
    start, end = (date.fromisoformat(value) for value in match.groups())
    if start > end:
        raise ValueError("story range order")
    return start.isoformat(), end.isoformat()


def insert_quotes(text, messages, unknown=None):
    """把 `[[id]]` 换成核验过的原话，并补一对引号。

    ID 查不到时记进 `unknown`（调用方一律当成致命错误 —— 这条是防编造的底线）。
    占位符两边已有的引号会被一起吃掉，避免双引号。
    """
    def one(match):
        item = messages.get(match.group(1))
        if item is None:
            if unknown is not None:
                unknown.append(match.group(1))
            return match.group(0)
        return "“" + item["text"] + ("…" if item.get("clipped") else "") + "”"
    return _QUOTED.sub(one, text)


def display_name(uid, fallback=""):
    """显示名：**简化名优先** —— 别把「糯糯～（裳姐姐的骚贱小母狗❤）」整串写进正文。

    档案名 → 平台缓存 → uid 前 8 位。故事的开场白、致信的落款都用它。
    """
    try:
        from runtime.identity import get_name
        name = get_name(uid)
        if name:
            return name
    except Exception:                                           # noqa: BLE001
        pass
    try:
        from profiles import get_profile
        profile = get_profile(uid) or {}
        if profile.get("name"):
            return profile["name"]
    except Exception:                                           # noqa: BLE001
        pass
    return fallback or uid[:8]


def has_quote(text):
    """这段文字里有没有合法的引用占位符（`[[id]]`，id 只含字母数字下划线连字符）。"""
    return _QUOTE.search(text or "") is not None


def parse_story(reply):
    text = (reply or "").strip()
    if len(text) > 20000:
        raise ValueError("story oversized")
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    # 多写的字段一律**忽略**而不是拒绝：模型爱自己加 `start`/`title`/`text_placeholder`
    # 这类键，它们无害，为这个把整篇作废不值当。必须的字段在不在才是重点。
    if not isinstance(data, dict) or not {"opening", "stages", "ending"} <= set(data):
        raise ValueError("story schema")
    opening = data["opening"]
    if not isinstance(opening, str) or not 20 <= len(opening.strip()) <= 160:
        raise ValueError("story opening")
    if "[[" in opening or "]]" in opening:
        raise ValueError("story opening quote")
    data["opening"] = opening.strip()
    if not isinstance(data["stages"], list) or not 1 <= len(data["stages"]) <= MAX_STAGES:
        raise ValueError("story stages")
    titles = set()
    for stage in data["stages"]:
        if not isinstance(stage, dict) or not isinstance(stage.get("text"), str) \
                or not isinstance(stage.get("title"), str):
            raise ValueError("story stage schema")
        title = stage["title"].strip().strip("：:、.。,， 　")
        if not 2 <= len(title) <= MAX_TITLE_CHARS or _BAD_TITLE.search(title):
            raise ValueError("story stage title")
        if title in titles:
            raise ValueError("story stage title duplicate")
        titles.add(title)
        stage["title"] = title
        value = stage["text"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("story stage text")
        if len(value) > MAX_STAGE_CHARS:
            raise ValueError("story stage oversized")
        stripped = _LEADING_LABEL.sub("", value).strip()
        stage["text"] = stripped or value.strip()
    ending = data["ending"]
    if not isinstance(ending, str) or not 15 <= len(ending.strip()) <= 160:
        raise ValueError("story ending")
    if "[[" in ending or "]]" in ending:
        raise ValueError("story ending quote")
    for value in [data["opening"]] + [stage["text"] for stage in data["stages"]] + [ending.strip()]:
        if any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("story control")
    return data


def render_story(data, evidence):
    messages = {item["message_id"]: item for item in evidence["messages"]}
    unknown = []
    sections = []
    for number, stage in enumerate(data["stages"]):
        body = insert_quotes(stage["text"], messages, unknown).strip()
        if "[[" in body or "]]" in body:
            # 形如 [[bad id]] 的畸形占位符（ID 里有空格等）—— 不许原样漏进成品
            raise ValueError("story malformed quote")
        sections.append("{}、{}\n{}".format(_NUMERALS[number], stage["title"], body))
    if unknown:
        raise ValueError("story unknown quote")
    result = "\n\n".join([data["opening"]] + sections + [data["ending"].strip()])
    minimum = MIN_RESULT if len(messages) >= 8 else MIN_RESULT_SPARSE
    if not minimum <= len(result) <= MAX_RESULT:
        raise ValueError("story length")
    return result


def ask_and_render(cid, prompt, payload, evidence, parse, render, timeout, max_tokens, tag):
    """调模型 + 校验；没过就**带拒绝原因重试一次**，仍不过则抛。

    为什么要重试：真模型会偶发写出不存在的引用 ID 或坏 JSON。旧版一次不过就直接
    回「糯糯这次没整理好」，实测在真实内容上几乎必然发生。重试最多多花一次调用，
    总预算仍卡在 `timeout` 之内（第二次只拿剩下的时间）。
    """
    deadline = time.monotonic() + max(timeout, 8)
    user, last = payload, ValueError(tag + " failed")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        budget = timeout if attempt == 1 else max(8, int(deadline - time.monotonic()))
        reply, finish = brains._call_llm_raw(prompt, user, max_tokens=max_tokens, timeout=budget)
        try:
            if finish == "length":
                raise ValueError(tag + " truncated")
            return render(parse(reply), evidence)
        except Exception as error:                              # noqa: BLE001
            last = error
            log("{}第 {} 次未通过 [{}] {}".format(tag, attempt, cid[:8], type(error).__name__))
            user = payload + RETRY_HINT.format(
                "{}: {}".format(type(error).__name__, error), (reply or "")[:1500])
    raise last


def _generate(cid, uid, start, end, timeout):
    try:
        evidence = story_evidence(uid, start=start, end=end)
        if not evidence["available"]:
            return "糯糯还没有找到认证群的聊天归档，暂时没法写你的故事～"
        if len(evidence["messages"]) < 3 or sum(len(item["text"]) for item in evidence["messages"]) < 40:
            return "这段时间留下的文字还不够写成故事，换个更长的日期范围，或再积累些聊天吧～"
        payload = json.dumps({"name": display_name(uid),
                              "start": evidence["start"], "end": evidence["end"],
                              "target_chars": 500, "messages": evidence["messages"]},
                             ensure_ascii=False)
        return ask_and_render(cid, brains.PERSONAL_STORY_PROMPT, payload, evidence,
                              parse_story, render_story, timeout, 2200, "我的故事")
    except Exception as error:                                  # noqa: BLE001
        log("我的故事生成失败 [{}] {}".format(cid[:8], type(error).__name__))
        return FAILURE
    finally:
        with _LOCK:
            _ACTIVE.discard((cid, uid))
        _LIMIT.release()


async def story_reply(cid, uid, text, *, via_at=False):
    payload = request_payload(text) if via_at else command_payload(text)
    if payload is None or not enabled(cid) or not uid:
        return None
    try:
        start, end = date_range(payload)
    except ValueError:
        log("我的故事日期参数无效 [{}]".format(cid[:8]))
        return USAGE
    now = time.monotonic()
    with _LOCK:
        for key in [key for key, expires in _LAST.items() if expires <= now]:
            del _LAST[key]
        if (cid, uid) in _ACTIVE or _LAST.get((cid, uid), 0) > now:
            return "糯糯正在整理，或刚写过你的故事，等一小会儿再来吧～"
        if len(_LAST) >= 4096 or not _LIMIT.acquire(blocking=False):
            return "糯糯正在写另一份故事，稍后再来吧～"
        _ACTIVE.add((cid, uid))
        _LAST[(cid, uid)] = now + int(_tune("personal_story.cooldown", cid))
    timeout = int(_tune("personal_story.timeout", cid))
    task = asyncio.create_task(asyncio.to_thread(_generate, cid, uid, start, end, timeout))
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        # 超时那条**保留**冷却：底层线程还在跑，名额也还占着，放行会叠加任务
        log("我的故事等待超时 [{}]".format(cid[:8]))
        return "这次整理得有些慢，稍后再来找糯糯吧～"
    if result == FAILURE:
        # 生成失败不该吃掉冷却 —— 否则用户要白等 5 分钟才能重试
        with _LOCK:
            _LAST.pop((cid, uid), None)
    return result
