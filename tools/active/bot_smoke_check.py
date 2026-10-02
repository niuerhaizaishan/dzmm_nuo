# -*- coding: utf-8 -*-
"""Bot 上线冒烟：**用生产发送链**往某个群发一条，再读回确认署名。

给新加的 Bot 做验收用（P12-H 加薇丝时就是这么验的）。它证明的不是"token 能发"，
而是**我们的路由把那个群接到了那个 Bot**：

    读 config → 注册身份 → _wrap_with_bot_routing → RoutingSender.send
                                            ↓ allow_fallback=False
         Bot 通道失败**不回退账号** ⇒ 返回值就是 Bot 的真实结果，不会"看起来成功、
         其实走的是账号"；再打印 counts（bot_ok / account）与 bot_cids 佐证。

发完用账号读回最近消息，确认有一条 `sent_by == 该 Bot 的 uid` 的 text 消息。

用法：
    python tools/bot_smoke_check.py --cid 4a56be71-... --text "上线冒烟测试"
    python tools/bot_smoke_check.py --cid <cid>            # 用默认文案
    python tools/bot_smoke_check.py --cid <cid> --keep     # 不提示"这是一次性测试"

⚠️ 会**真的往群里发言**（这就是它的用途）。跑之前想清楚是哪个群。
⚠️ 建议加 `-e DZMM_OUTBOX=off`：一次性进程不该碰线上出站队列。
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_TEXT = "（上线冒烟测试）这条消息由 Bot 通道发出，用来确认路由与身份都对上了。"


class NullAccount:
    """占位账号：Bot 通道成功就不该走到它。真走到了会留下痕迹。"""

    def __init__(self):
        self.calls = []

    async def send(self, text, cid, allow_fallback=True):
        self.calls.append((text[:20], cid))
        return False

    async def start(self):
        return None

    def __getattr__(self, name):
        return lambda *a, **k: None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bot 上线冒烟（走生产发送链）")
    ap.add_argument("--cid", required=True, help="目标 chatroom_id")
    ap.add_argument("--text", default="", help="要发的话（默认用内置测试文案）")
    ap.add_argument("--no-verify", action="store_true", help="发完不读回核对")
    a = ap.parse_args(argv)

    import bot  # 读同一份 config（DZMM_CONFIG / 挂载里的 config.json）

    text = a.text or DEFAULT_TEXT
    acc = NullAccount()
    sender = bot._wrap_with_bot_routing(acc)
    cids = getattr(sender, "bot_cids", ())
    print("发送链 =", type(sender).__name__, "| 覆盖群 =", [c[:8] for c in cids])
    if a.cid not in cids:
        print("!! 这个群不在 Bot 路由里 —— 要么 config 里没给它绑 Bot，"
              "要么它的 token 没解析到（看上面的 bot路由 日志）")
        return 2
    bot_uid = ""
    for b in bot._CFG.bots:
        if b.cid == a.cid:
            bot_uid = b.uid
            print("该群的 Bot = {} ({}) uid={}".format(b.key, b.name, b.uid or "未配置"))
    print("身份集合 =", sorted(u[:8] for u in bot.self_uids()))

    t0 = time.time()
    ok = asyncio.run(sender.send(text, a.cid, allow_fallback=False))
    print("发送 ok = {} | 耗时 {:.1f}s | 路由计数 = {}".format(
        ok, time.time() - t0, getattr(sender, "counts", {})))
    print("占位账号被调用 =", acc.calls or "没有（说明确实走的 Bot 通道）")
    if not ok:
        print("!! 没发出去；看上面的 bot_rejected/bot_ambiguous 日志与计数")
        return 1

    if a.no_verify:
        return 0
    time.sleep(3)
    import dzmm
    d = dzmm.api("chatroom.getMessages",
                 {"json": {"chatroomId": a.cid, "limit": 12, "before": None},
                  "meta": {"values": {"before": ["undefined"]}, "v": 1}})
    hit = [m for m in (d.get("messages") or [])
           if bot_uid and m.get("sent_by") == bot_uid]
    print("读回：最近 {} 条里，Bot 署名的有 {} 条".format(
        len(d.get("messages") or []), len(hit)))
    for m in hit[-3:]:
        c = m.get("content") or {}
        print("   [{}] type={} {}".format(m.get("sent_by", "")[:8], c.get("type"),
                                          (c.get("text") or "")[:60]))
    if not hit:
        print("!! 读回没看到 Bot 署名的消息（可能只是延迟；也可能发到了别处）")
        return 1
    print("==> 冒烟通过：Bot 通道可用，消息确实以该 Bot 的身份进了群。")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
