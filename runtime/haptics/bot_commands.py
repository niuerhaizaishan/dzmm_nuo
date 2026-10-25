"""/震、/预设、/停：群里的触觉指令（群主 + 白名单）。

指令形态：
  /震              默认 30% / 800ms
  /震 50           50% / 800ms
  /震 50 2000      50% / 2000ms
  /预设            列出所有预设节奏
  /预设 心跳       播「心跳」，默认 60 秒
  /预设 心跳 30    播 30 秒
  /停              立刻停（正在跑的预设也一起取消）

预设节奏＝页面上那七个（点按/脉冲/心跳/渐强/渐弱/连抖/全开），由房主页本地按时序执行，
服务端只透传 id + 总时长。强度按百分比给（1~100），时长 200~10000ms；
实际是否生效取决于房主页面是否打开了「允许小小糯控制」——没打开时回一句怎么开。
"""
import json
import os
import re

from runtime.haptics import share_client

# 白名单：{"uids": ["<uid>", ...], "names": ["御褚"]}。每次指令都重读，改完立即生效、不用重启。
DEFAULT_ALLOW_FILE = "/app/data/haptics_allow.json"


def allow_file():
    return os.environ.get("DZMM_HAPTICS_ALLOW_FILE") or DEFAULT_ALLOW_FILE


COMMAND = re.compile(r"^/(震|停|预设|节奏)(?=\s|$)(.*)$", re.S)
HELP_CMD = re.compile(r"^/(?:帮助|help|指令|菜单|命令)\s+(\S.*)$", re.S)
TOY_TOPICS = ("玩具", "糯控", "触觉", "震动", "振动", "设备", "小恶魔")
DEFAULT_LEVEL = 0.30
DEFAULT_DURATION_MS = 800
MIN_DURATION_MS = 200
MAX_DURATION_MS = 10000
PRESET_DEFAULT_S = 60
PRESET_MIN_S = 5
PRESET_MAX_S = 60
USAGE = "用法：/震 30 500｜/预设 心跳 30｜/停"

# 预设节奏：名字 → (页面里的 id, 给人的说明)。别名都指向同一个 id。
PRESETS = {
    "点按": ("tap", "细碎地敲，300ms 一下"),
    "轻叩": ("tap", "同上"),
    "脉冲": ("pulse", "规律推进，600ms 一下"),
    "心跳": ("heartbeat", "一秒两下，有停顿"),
    "渐强": ("ramp", "20%→100% 一路加码"),
    "渐弱": ("ramp_down", "100%→20% 慢慢退"),
    "连抖": ("stutter", "150ms 开 150ms 关，很碎"),
    "断续": ("stutter", "同上"),
    "全开": ("hold", "一直满档，不带间隙"),
    "长震": ("hold", "同上"),
}
PRESET_LABELS = [
    ("点按", "tap"), ("脉冲", "pulse"), ("心跳", "heartbeat"),
    ("渐强", "ramp"), ("渐弱", "ramp_down"), ("连抖", "stutter"), ("全开", "hold"),
]


def allow_uids():
    """读白名单；文件缺失或坏掉就当空表（宁可不开权限）。"""
    try:
        with open(allow_file(), encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return set()
    rows = data.get("uids") if isinstance(data, dict) else data
    return {str(item).strip().lower() for item in (rows or []) if str(item).strip()}


def is_allowed(uid):
    return str(uid or "").strip().lower() in allow_uids()


def command(text):
    """返回动作名（震/预设/停/帮助）或 None。"""
    raw = (text or "").strip()
    match = COMMAND.match(raw)
    if match:
        kind = match.group(1)
        return "预设" if kind == "节奏" else kind
    # /帮助 玩具（也叫糯控/触觉/震动…）走这里；别的 /帮助 主题还给通用帮助
    help_match = HELP_CMD.match(raw)
    if help_match and is_toy_topic(help_match.group(1)):
        return "帮助"
    return None


def is_toy_topic(text):
    value = (text or "").strip()
    return any(key in value for key in TOY_TOPICS)


def help_text():
    """`/帮助 玩具` 的内容：所有玩具相关指令 + 网页版怎么开。"""
    lines = [
        "🧸 玩具指令（主人白名单可用）：",
        "/预设 — 看全部预设节奏",
        "/预设 心跳 — 播 60 秒；/预设 心跳 30 = 30 秒（5~60）",
        "   七个：点按 · 脉冲 · 心跳 · 渐强 · 渐弱 · 连抖 · 全开",
        "/震 — 单发：/震 30 800（强度% 时长ms，都可省）",
        "/停 — 立刻全停（正在跑的预设、背景层一起停）",
        "",
        "🕹 网页版（手动/预设/实时强度）：https://www.nuonuo.pw/haptics/",
        "   房主：连接设备 → 生成分享码；要给小小糯用就勾「允许小小糯控制」",
        "   想让它跟着团本剧情自己震：房主页开着 + 勾上面那个就够",
    ]
    if not share_client.enabled():
        lines.append("⚠️ 机器人通道没开（服务器缺 DZMM_SHARE_PROGRAM_TOKEN），现在只能用网页版。")
    return "\n".join(lines)


def preset_list_text():
    lines = ["🎵 预设节奏（默认 60 秒，可跟一个秒数改短）："]
    for name, pid in PRESET_LABELS:
        lines.append("  /预设 {} — {}".format(name, PRESETS[name][1]))
    lines.append("用法：/预设 心跳 30｜/停 随时全停")
    return "\n".join(lines)


def parse_preset(name):
    """按名字找预设 id（容错：去空格；允许唯一前缀）。"""
    key = (name or "").strip()
    if not key:
        return None
    if key in PRESETS:
        return key
    hits = [item for item in PRESETS if item.startswith(key)]
    return hits[0] if len(hits) == 1 else None


def parse(text):
    """解析成动作 dict；参数不合法返回 (None, 提示)。"""
    match = COMMAND.match((text or "").strip())
    if not match:
        if command(text) == "帮助":
            return {"action": "help"}, ""
        return None, USAGE
    kind = command(text)
    if kind == "停":
        return {"action": "stop"}, ""
    parts = match.group(2).split()
    if kind == "预设":
        if not parts:
            return {"action": "presets"}, ""
        name = parse_preset(parts[0])
        if name is None:
            return None, ("没有这个预设。\n" + preset_list_text())
        seconds = PRESET_DEFAULT_S
        if len(parts) > 1:
            try:
                seconds = int(parts[1].rstrip("sS秒"))
            except ValueError:
                return None, USAGE
            if not PRESET_MIN_S <= seconds <= PRESET_MAX_S:
                return None, "预设时长要在 %d~%d 秒之间" % (PRESET_MIN_S, PRESET_MAX_S)
        if len(parts) > 2:
            return None, USAGE
        return {"action": "preset", "name": name,
                "pattern": PRESETS[name][0], "total_ms": seconds * 1000}, ""
    level, duration = DEFAULT_LEVEL, DEFAULT_DURATION_MS
    if parts:
        try:
            level = float(parts[0].rstrip("%")) / 100.0
        except ValueError:
            return None, USAGE
        if not 0.01 <= level <= 1.0:
            return None, "强度要在 1~100 之间（按百分比写，例如 /震 30）"
    if len(parts) > 1:
        try:
            duration = int(parts[1])
        except ValueError:
            return None, USAGE
        if not MIN_DURATION_MS <= duration <= MAX_DURATION_MS:
            return None, "时长要在 200~10000 毫秒之间"
    if len(parts) > 2:
        return None, USAGE
    return {"action": "vibrate", "level": round(level, 3), "duration_ms": duration}, ""


def _hint(detail):
    if "没有" in detail or "房间" in detail:
        return "没连上你的控制页：打开 https://www.nuonuo.pw/haptics/ 连接设备，并勾上「允许小小糯控制」。"
    return "没能下发：" + detail


async def handle(text, *, uid="", owner=False, share=None):
    """执行一条指令并返回要发回群里的短回执。owner=群主，uid 用于查白名单。"""
    action, problem = parse(text)
    if action is None:
        return problem
    if action["action"] == "help":
        return help_text()
    if action["action"] == "presets":
        return preset_list_text()
    if not owner and not is_allowed(uid):
        return "这个指令只有主人和白名单里的人能用。"
    if not share_client.enabled():
        return "机器人通道没开（服务器缺 DZMM_SHARE_PROGRAM_TOKEN），先用网页版控制。"
    share = share or share_client.client()
    if action["action"] == "stop":
        result = await share.stop("群指令")
        return "已停。" if result["ok"] else ("停不下来：" + result["detail"])
    if action["action"] == "preset":
        result = await share.command(0.0, action["total_ms"], pattern=action["pattern"],
                                     total_ms=action["total_ms"])
        if result["ok"]:
            return "🎵 %s · %d 秒（随时 /停）" % (action["name"],
                                                action["total_ms"] // 1000)
        return _hint(result.get("detail") or "未知原因")
    result = await share.command(action["level"], action["duration_ms"])
    if result["ok"]:
        return ("⚡ %d%% / %dms" % (round(action["level"] * 100), action["duration_ms"]))
    return _hint(result.get("detail") or "未知原因")
