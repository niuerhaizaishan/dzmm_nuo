"""剧情触觉的「解析层」：把 DM 输出的触觉行抽成时间线（路线 a）。

DM 在每回合末尾（状态栏之前）最多输出一行：

    【震动】<角色> <强度>% <时长>ms
    例：【震动】警官 55% 1200ms

这里只做**纯文本解析 + 夹紧**，不碰网络、不碰设备 —— 所以能离线跑、
能用真实记录做回放（`tools/active/replay_raid.py`），也方便写单测。

为什么强度/时长都要再过一遍夹紧：提示词只是"请求"，模型会写 100% 3000ms
甚至 150% 90000ms。设备端的硬上限必须在这里，与提示词无关。
"""
import os
import re

# 单条触觉的硬上限。提示词里的档位表最高是「失守 100% 3000ms」，
# 默认上限放到 70% 是"不吓人"的起点；想放开就设 DZMM_HAPTICS_MAX_LEVEL=1.0。
MAX_LEVEL = float(os.environ.get("DZMM_HAPTICS_MAX_LEVEL", "0.7"))
MAX_MS = int(os.environ.get("DZMM_HAPTICS_MAX_MS", "3000"))
MIN_MS = 200
PER_TURN = int(os.environ.get("DZMM_HAPTICS_PER_TURN", "2"))

# 角色名允许中文/字母/数字，最多 12 字；强度 1~3 位；时长 1~5 位，单位可省。
CUE_MARK = "【震动】"
CUE_RE = re.compile(
    r"【震动】\s*([^\s%％,，:：]{1,12})\s*[:：,，]?\s*(\d{1,3}(?:\.\d{1,2})?)\s*([%％]?)\s*[,，]?\s*(\d{1,5})\s*(?:ms|MS|Ms|毫秒)?"
)


class Cue:
    """一条触觉请求。level 是 0~1 的比例，不是百分数。"""

    __slots__ = ("role", "level", "duration_ms", "raw")

    def __init__(self, role, level, duration_ms, raw=""):
        self.role = role
        self.level = level
        self.duration_ms = duration_ms
        self.raw = raw

    def __repr__(self):
        return "Cue({} {}% {}ms)".format(self.role, round(self.level * 100), self.duration_ms)

    def __eq__(self, other):
        return (isinstance(other, Cue) and self.role == other.role
                and abs(self.level - other.level) < 1e-9
                and self.duration_ms == other.duration_ms)

    def as_dict(self):
        return {"role": self.role, "level": self.level, "duration_ms": self.duration_ms}


def clamp(level, duration_ms, percent=None):
    """把模型给的数字夹进安全区。返回 (level 0~1, duration_ms)。

    percent=True 表示原文带 %（55/0.5 都按百分数读）；None 表示按数值大小猜。
    """
    try:
        level = float(level)
    except (TypeError, ValueError):
        level = 0.3
    if percent is None:
        percent = level > 1.0
    if percent:
        level = level / 100.0
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        duration_ms = 800
    level = max(0.0, min(level, MAX_LEVEL))
    duration_ms = max(MIN_MS, min(duration_ms, MAX_MS))
    return level, duration_ms


def parse_cues(text):
    """从一段文本里抽出所有触觉行（一行里写两条也算两条）。"""
    text = text or ""
    if CUE_MARK not in text:
        return []
    cues = []
    for match in CUE_RE.finditer(text):
        role = match.group(1)
        level, duration_ms = clamp(match.group(2), match.group(4),
                                   True if match.group(3) else None)
        cues.append(Cue(role, level, duration_ms, match.group(0)))
    return cues


def parse_first(text):
    cues = parse_cues(text)
    return cues[0] if cues else None


# (b) 模型判断只肯回"55 1200"两个数字，所以再给一套极简解析。
PLAIN_RE = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*[%％]?\s*[,，\s]\s*(\d{1,5})")


def parse_plain(text, role="模型"):
    """解析"强度 时长"两个数字（可带 %）。拿不到就返回 None。"""
    match = PLAIN_RE.search(text or "")
    if not match:
        return None
    level, duration_ms = clamp(match.group(1), match.group(2), True)
    return Cue(role, level, duration_ms, match.group(0))


def per_turn(cues, limit=None):
    """每回合最多几条（默认 2）：多写的丢掉，避免一段话刷满设备。"""
    limit = PER_TURN if limit is None else limit
    return list(cues)[:max(0, limit)]


def timeline(items):
    """[{"at": 秒, "text": ...}] → 带绝对时间的时间线，供离线打印/回放。

    items 只需要 `at`（秒）与 `text`；返回 [{"at":…, "role":…, "level":…, "duration_ms":…}]。
    """
    out = []
    for item in items or []:
        at = item.get("at", 0) or 0
        for cue in per_turn(parse_cues(item.get("text", ""))):
            row = cue.as_dict()
            row["at"] = round(float(at), 1)
            out.append(row)
    return out


def describe(cue):
    return "{} · {:.0f}% · {}ms".format(cue.role, cue.level * 100, cue.duration_ms)
