"""办公室双人场景生成；专用模板，不改变原有场景提示词。"""
import json
import re
import threading
import time

import brains

_MODEL_LIMIT = threading.BoundedSemaphore(4)


class SceneGenerationError(ValueError):
    """仅保留安全的失败原因、字数及结束状态，不携带正文。"""
    def __init__(self, reason, *, length=0, finish="", attempts=0):
        super().__init__(reason)
        self.reason = reason
        self.length = length
        self.finish = finish if finish in ("stop", "length", "content_filter") else "other"
        self.attempts = attempts


def visible_length(text):
    return len(re.sub(r"\s", "", text))


def generate(profiles, history, direction, timeout=45):
    # 超时或取消 await 不会杀掉工作线程；额度由真正结束的线程释放。
    if not _MODEL_LIMIT.acquire(blocking=False):
        raise SceneGenerationError("office_model_busy")
    try:
        return _generate(profiles, history, direction, timeout)
    finally:
        _MODEL_LIMIT.release()


def generate_failure(profiles, direction, timeout=25):
    """概率失败时的**简短失败情节**（这一段没有成事，费用照扣、不分账）。

    方案 §9：程序先定结果，模型只负责把"没得手"这一个瞬间写出来；不需要走长稿三轮修订，
    但同样要写完、不能是半句话。
    """
    if not _MODEL_LIMIT.acquire(blocking=False):
        raise SceneGenerationError("office_model_busy")
    try:
        payload = {"participants": profiles, "direction": direction, "outcome": "fail"}
        prompt = brains.OFFICE_SCENE_PROMPT + (
            "\n【本次结果：失败】这一场没成事：发起者没能得手（被躲开、被反制、被打断、被拒绝）。"
            "只写这一个瞬间的结尾，120～260 个可见字符，一个自然段；不写性行为、不写事后复盘、"
            "不解释原因，结尾就落在「没成」上。只输出正文，不输出标题或说明。")
        deadline = time.monotonic() + timeout
        text, finish = brains._call_llm_raw(
            prompt, json.dumps(payload, ensure_ascii=False),
            max_tokens=600, timeout=max(1, int(deadline - time.monotonic())))
        text = " ".join((text or "").split())
        length = visible_length(text)
        ok_finish = finish not in ("content_filter",)
        if length < 40 or length > 400 or not ok_finish or text.startswith("```"):
            raise SceneGenerationError("office_scene_invalid_length_or_finish",
                                       length=length, finish=finish, attempts=1)
        return text
    finally:
        _MODEL_LIMIT.release()



def _generate(profiles, history, direction, timeout):
    payload = {"participants": profiles, "history": history[-4:], "direction": direction}
    prompt = brains.OFFICE_SCENE_PROMPT + (
        "\n本段必须是一个连续自然段，不换行、不留空行。"
        "以400～600个非空白字符为目标；完整自然的200～399字正文也可以，低于400字不必凑字。"
        "收到 previous_draft 和 length_correction 时，按测得字数修订整稿，仍只输出完整正文。")
    deadline = time.monotonic() + timeout
    length, finish, attempts = 0, "", 0
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        text, finish = brains._call_llm_raw(prompt, json.dumps(payload, ensure_ascii=False),
                                          max_tokens=1600, timeout=max(1, int(remaining)))
        attempts += 1
        text = " ".join((text or "").split())
        length = visible_length(text)
        if (finish not in ("length", "content_filter") and 200 <= length <= 600
                and not text.startswith("```") and text[-1:] in "。！？.!?～…）”」"):
            return text
        payload["previous_draft"] = text
        adjustment = ("请补充具体环境、动作和对话，至少增加{}字。".format(500 - length)
                      if length < 200 else
                      "请精简重复描写，至少减少{}字。".format(length - 550) if length > 600 else
                      "请修正输出格式并写完完整句子。")
        payload["length_correction"] = (
            "程序实测上一稿为{}个非空白字符，结束状态{}。{}"
            "请修订到500～550字的完整单段正文，不要解释、不截断，也不要只输出补写片段。"
        ).format(length, finish, adjustment)
    raise SceneGenerationError("office_scene_invalid_length_or_finish", length=length,
                               finish=finish, attempts=attempts)
