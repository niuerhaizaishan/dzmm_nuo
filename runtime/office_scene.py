"""办公室双人场景生成；专用模板，不改变原有场景提示词。"""
import json
import re
import threading
import time

import brains

_MODEL_LIMIT = threading.BoundedSemaphore(4)


def visible_length(text):
    return len(re.sub(r"\s", "", text))


def generate(profiles, history, direction, timeout=45):
    # 超时或取消 await 不会杀掉工作线程；额度由真正结束的线程释放。
    if not _MODEL_LIMIT.acquire(blocking=False):
        raise ValueError("office_model_busy")
    try:
        return _generate(profiles, history, direction, timeout)
    finally:
        _MODEL_LIMIT.release()


def _generate(profiles, history, direction, timeout):
    payload = {"participants": profiles, "history": history[-4:], "direction": direction}
    prompt = brains.OFFICE_SCENE_PROMPT
    deadline = time.monotonic() + timeout
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        text, finish = brains._call_llm_raw(prompt, json.dumps(payload, ensure_ascii=False),
                                          max_tokens=1600, timeout=max(1, int(remaining)))
        text = (text or "").strip()
        if (finish != "length" and 400 <= visible_length(text) <= 600
                and not text.startswith("```") and text[-1:] in "。！？.!?～…）”」"):
            return text
        payload["length_correction"] = "上一稿未满足400～600字或完整结尾，请重新写完整一段，不截断。"
    raise ValueError("office_scene_invalid_length_or_finish")
