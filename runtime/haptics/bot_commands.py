"""/震 与 /停：群里的触觉指令（只给主人用）。

指令形态：
  /震              默认 30% / 800ms
  /震 50           50% / 800ms
  /震 50 2000      50% / 2000ms
  /停              立刻停
强度按百分比给（1~100），时长 200~10000ms；实际是否生效取决于房主页面是否打开了
「允许小小糯控制」——没打开时回一句怎么开。
"""
import re

from runtime.haptics import share_client

COMMAND = re.compile(r"^/(震|停)(?=\s|$)(.*)$", re.S)
DEFAULT_LEVEL = 0.30
DEFAULT_DURATION_MS = 800
MIN_DURATION_MS = 200
MAX_DURATION_MS = 10000
USAGE = "用法：/震 30 500（强度%，时长ms，可省）｜/停"


def command(text):
    """返回动作名（震/停）或 None。"""
    match = COMMAND.match((text or "").strip())
    return match.group(1) if match else None


def parse(text):
    """解析成 (level 0..1, duration_ms)；参数不合法返回 (None, 提示)。"""
    match = COMMAND.match((text or "").strip())
    if not match:
        return None, USAGE
    if match.group(1) == "停":
        return {"action": "stop"}, ""
    parts = match.group(2).split()
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


async def handle(text, *, owner=False, share=None):
    """执行一条指令并返回要发回群里的短回执。"""
    action, problem = parse(text)
    if action is None:
        return problem
    if not owner:
        return "这个指令只有主人能用。"
    if not share_client.enabled():
        return "机器人通道没开（服务器缺 DZMM_SHARE_PROGRAM_TOKEN），先用网页版控制。"
    share = share or share_client.client()
    if action["action"] == "stop":
        result = await share.stop("群指令")
        return "已停。" if result["ok"] else ("停不下来：" + result["detail"])
    result = await share.command(action["level"], action["duration_ms"])
    if result["ok"]:
        return ("⚡ %d%% / %dms" % (round(action["level"] * 100), action["duration_ms"]))
    detail = result.get("detail") or "未知原因"
    if "没有" in detail or "房间" in detail:
        return "没连上你的控制页：打开 https://www.nuonuo.pw/haptics/ 连接设备，并勾上「允许小小糯控制」。"
    return "没能下发：" + detail
