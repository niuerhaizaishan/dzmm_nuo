"""把一段真实剧情记录变成触觉时间线，先看、再回放（月之瞳 / 糯控验用）。

用法（只打印时间线，不碰设备）：

    python tools/active/replay_raid.py --file docs/样本-月之瞳记录.txt

带时间戳的 JSON（`[{"at": 12.5, "text": "..."}]`）：

    python tools/active/replay_raid.py --file record.json --json

真机回放（房主页面开着、勾了「允许小小糯控制」，且机器上有
DZMM_SHARE_PROGRAM_TOKEN）：

    python tools/active/replay_raid.py --file record.json --json --live

纯文本输入的两种切分方式：
  * 有空行 → 按空行切；
  * 没有空行 → 按 `---` 行切；
  * 都没有 → 整份当成一条。
每条之间默认间隔 `--gap 40` 秒（只是给"回放"用的假时间轴）。
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from runtime.haptics import cues as _cues       # noqa: E402


def read_plain(text, gap):
    """把纯文本切成 [{at, text}]。"""
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if len(blocks) <= 1:
        blocks = [b.strip() for b in text.split("---") if b.strip()] or [text.strip()]
    out = []
    for index, block in enumerate(blocks):
        out.append({"at": round(index * gap, 1), "text": block})
    return out


def read_json(text):
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("messages") or data.get("items") or []
    out = []
    for index, item in enumerate(data):
        if isinstance(item, str):
            out.append({"at": round(index * 40.0, 1), "text": item})
        elif isinstance(item, dict):
            out.append({"at": float(item.get("at") if item.get("at") is not None else index * 40.0),
                        "text": str(item.get("text") or item.get("content") or "")})
    return out


def clock(seconds):
    minutes, rest = divmod(max(0.0, float(seconds)), 60.0)
    return "{:02d}:{:04.1f}".format(int(minutes), rest)


def show(rows):
    if not rows:
        print("没有解析到任何【震动】行。")
        print("提示：DM 的触觉行格式是 `【震动】角色 强度% 时长ms`，例如 `【震动】警官 55% 1200ms`。")
        return
    print("{:<4}{:<9}{:<10}{:>6}{:>9}".format("#", "时间", "角色", "强度", "时长"))
    print("-" * 40)
    for index, row in enumerate(rows, 1):
        print("{:<4}{:<9}{:<10}{:>5.0f}%{:>8}ms".format(
            index, clock(row["at"]), row["role"], row["level"] * 100, row["duration_ms"]))
    levels = [row["level"] for row in rows]
    total = sum(row["duration_ms"] for row in rows) / 1000.0
    span = rows[-1]["at"] - rows[0]["at"]
    print("-" * 40)
    print("触觉 {} 条｜时间跨度 {}｜震动总时长 {:.1f}s｜平均强度 {:.0f}%｜峰值 {:.0f}%".format(
        len(rows), clock(span), total, sum(levels) / len(levels) * 100, max(levels) * 100))
    if span > 0:
        print("平均每 {:.0f} 秒一次".format(span / max(1, len(rows) - 1)))
    print("（上限：单条 ≤ {:.0f}% / ≤ {}ms；这些数字已经过夹紧）".format(
        _cues.MAX_LEVEL * 100, _cues.MAX_MS))


async def replay(rows, speed, limit):
    from runtime.haptics import share_client as _share

    if not _share.enabled():
        print("没配 DZMM_SHARE_PROGRAM_TOKEN，没法真机回放。")
        return 1
    client = _share.client()
    start = time.monotonic()
    sent = 0
    for row in rows[:limit]:
        delay = row["at"] / max(0.1, speed) - (time.monotonic() - start)
        if delay > 0:
            await asyncio.sleep(delay)
        result = await client.command(row["level"], row["duration_ms"])
        mark = "✅" if result.get("ok") else "❌"
        print("{} {} {} {}".format(mark, clock(row["at"]), row["role"],
                                   result.get("detail") or ""))
        sent += 1 if result.get("ok") else 0
    print("回放结束：{}/{} 条成功".format(sent, min(limit, len(rows))))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="剧情触觉时间线回放")
    parser.add_argument("--file", required=True, help="记录文件（纯文本或 JSON）")
    parser.add_argument("--json", action="store_true", help="输入是 JSON 数组")
    parser.add_argument("--gap", type=float, default=40.0, help="纯文本每条之间的秒数（默认 40）")
    parser.add_argument("--live", action="store_true", help="真的下发到设备（需要房主页面开着）")
    parser.add_argument("--speed", type=float, default=1.0, help="回放倍速（默认 1×）")
    parser.add_argument("--limit", type=int, default=999, help="最多回放几条")
    args = parser.parse_args(argv)

    with open(args.file, encoding="utf-8") as handle:
        raw = handle.read()
    items = read_json(raw) if args.json else read_plain(raw, args.gap)
    rows = _cues.timeline(items)
    print("读入 {} 段叙述，解析出 {} 条触觉。\n".format(len(items), len(rows)))
    show(rows)
    if not args.live:
        return 0
    if not rows:
        return 1
    return asyncio.run(replay(rows, args.speed, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
