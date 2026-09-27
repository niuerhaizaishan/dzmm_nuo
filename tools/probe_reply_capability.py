"""P12-AE 探针：**引用回复**这条链路，平台到底支持到什么程度。

屋主 2026-09-27 提的两件事：
  1. 用户"右键点击回复"若瑾那条消息时，若瑾要**自动接话**（不用再 @）；
  2. 若瑾回复别人时，也要**挂在对方那条消息上**（右键回复那种形式）。

这两件都取决于平台给不给接口，所以先探针、再写代码。探针全程**零副作用**：

  A) 出站：官方 Bot API 收不收"回复某条消息"的字段？
     判定手法 —— 目标群写**全 0 UUID**（无论如何都是 403 not a member，
     不可能投递到任何真实群），候选字段填**非法值**（"not-a-uuid"）。
       · 若该字段在 schema 里 → Zod 校验先失败，返回 400 + fieldErrors（和
         `pin-message` 已知的形状一致）；
       · 若不在 schema 里 → 直接被忽略，我们只会看到 403 not a member。
     先用 `chatroom_id="not-a-uuid"` 做**对照**，证明这套判定真的看得见 fieldErrors。

  B) 入站：群消息的原始 JSON 里，被"引用回复"的那条有没有留下痕迹？
     直接 dump `chatroom.getMessages` 的**原始对象**：顶层键、content 的键、
     以及任何名字里带 reply/quote/reference/parent 的字段。

用法（容器里跑）：
    python /app/tools/probe_reply_capability.py
    python /app/tools/probe_reply_capability.py --cid <上班群cid> --limit 40
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), "/app", "/app/admin"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

ZERO = "00000000-0000-0000-0000-000000000000"
RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"

# 候选字段名：官方 A 形态是 snake_case，B 形态是 Telegram 风格 camelCase
CANDIDATES = [
    "reply_to_message_id", "reply_message_id", "quote_message_id",
    "quoted_message_id", "parent_message_id", "reference_message_id",
    "reply_to", "reply_to_id", "in_reply_to", "message_id", "reply",
    "replyToMessageId", "quoteMessageId", "parentMessageId", "replyTo",
]

REPLYISH = ("reply", "quote", "refer", "parent", "mention", "forward")


def _sep(t):
    print()
    print("=" * 68)
    print(t)
    print("=" * 68)


def probes_outbound():
    """A) 官方 Bot API 收不收回复字段。"""
    import requests
    import bot                                     # 读生产同一份 config

    origin = (os.environ.get("DZMM_BOT_ORIGIN") or bot.ORIGIN).rstrip("/")
    proxy = os.environ.get("DZMM_PROXY") or None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    bots = [b for b in bot._CFG.bots if b.enabled and b.resolve_token()]
    if not bots:
        print("!! config 里没有可用 Bot（enabled + token 能解析）")
        return {}
    b = next((x for x in bots if x.cid == RUOJIN), bots[0])
    token = b.resolve_token()
    print("用 Bot {}({}) 探测 | origin={} | 目标群写全 0 UUID（不可能投递）".format(
        b.key, b.name or "-", origin))

    def post(path, payload):
        url = origin + path
        try:
            r = requests.post(url, json=payload,
                              headers={"X-Bot-Token": token,
                                       "Content-Type": "application/json"},
                              timeout=20, proxies=proxies)
            try:
                body = r.json()
            except ValueError:
                body = {"_raw": r.text[:160]}
            return r.status_code, body
        except Exception as e:                                 # noqa: BLE001
            return -1, {"_err": "{}: {}".format(type(e).__name__, str(e)[:80])}

    print()
    print("--- 对照组（证明这套判定看得见 fieldErrors）---")
    st, body = post("/api/bot/send-message", {"chatroom_id": "not-a-uuid",
                                              "content": "probe"})
    print("  chatroom_id 非法  -> HTTP {} {}".format(st, json.dumps(body, ensure_ascii=False)[:200]))
    st0, body0 = post("/api/bot/send-message", {"chatroom_id": ZERO,
                                                "content": "probe"})
    print("  基线（合法 body）  -> HTTP {} {}".format(st0, json.dumps(body0, ensure_ascii=False)[:200]))

    print()
    print("--- 候选字段（值一律非法，命中即报 fieldErrors）---")
    hits = []
    for f in CANDIDATES:
        st, body = post("/api/bot/send-message",
                        {"chatroom_id": ZERO, "content": "probe", f: "not-a-uuid"})
        blob = json.dumps(body, ensure_ascii=False)
        errs = {}
        if isinstance(body, dict):
            det = body.get("details") or {}
            if isinstance(det, dict):
                errs = det.get("fieldErrors") or {}
        marked = bool(errs) or (st >= 400 and st != 403)
        if marked:
            hits.append(f)
        print("  {:<22s} HTTP {:<4} {}{}".format(
            f, st, blob[:130], "   <== 可能支持" if marked else ""))

    print()
    print("--- B 形态（Telegram 兼容）也试一下 ---")
    for f in ("reply_to_message_id", "reply_parameters"):
        st, body = post("/api/bot/bot{}/sendMessage".format(token),
                        {"chat_id": ZERO, "text": "probe", f: "not-a-uuid"})
        print("  {:<22s} HTTP {:<4} {}".format(
            f, st, json.dumps(body, ensure_ascii=False)[:130]))

    print()
    print("A) 结论：", ("命中候选 " + ", ".join(hits)) if hits
          else "没有候选字段被校验 —— 官方 A 形态**大概率不支持**指定回复目标")
    return {"validated": hits,
            "control_chatroom_id": body,
            "baseline": body0}


def probes_inbound(cid, limit):
    """B) 群消息原始 JSON 里有没有"被引用的那条"。"""
    import dzmm

    msgs = dzmm.get_messages(cid, limit=limit) or []
    print("拉到消息 {} 条".format(len(msgs)))
    if not msgs:
        return {}

    keys, ckeys, replyish = {}, {}, {}
    for m in msgs:
        for k in m:
            keys[k] = keys.get(k, 0) + 1
        c = m.get("content")
        if isinstance(c, dict):
            for k in c:
                ckeys[k] = ckeys.get(k, 0) + 1
        def walk(obj, path):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if any(s in k.lower() for s in REPLYISH):
                        replyish.setdefault(path + k, []).append(v)
                    walk(v, path + k + ".")
            elif isinstance(obj, list):
                for v in obj[:3]:
                    walk(v, path)
        walk(m, "")

    print()
    print("--- 顶层键（出现次数）---")
    for k, v in sorted(keys.items(), key=lambda kv: -kv[1]):
        print("  {:<24s} {}".format(k, v))
    print()
    print("--- content 内的键 ---")
    for k, v in sorted(ckeys.items(), key=lambda kv: -kv[1]):
        print("  {:<24s} {}".format(k, v))
    print()
    print("--- 名字里带 reply/quote/refer/parent/mention/forward 的字段 ---")
    if not replyish:
        print("  （一条都没有）")
    for k, vs in list(replyish.items())[:12]:
        print("  {:<36s} 样例 {}".format(k, json.dumps(vs[0], ensure_ascii=False)[:160]))

    print()
    print("--- metadata 的键（sio 那条路的消息结构里有 metadata）---")
    mkeys = {}
    for m in msgs:
        md = m.get("metadata")
        if isinstance(md, dict):
            for k in md:
                mkeys[k] = mkeys.get(k, 0) + 1
    if not mkeys:
        print("  （没有 metadata，或不是 dict）")
    for k, v in sorted(mkeys.items(), key=lambda kv: -kv[1]):
        print("  {:<24s} {}".format(k, v))

    print()
    print("--- 最后 3 条消息的**完整原始 JSON** ---")
    for m in msgs[-3:]:
        print(json.dumps(m, ensure_ascii=False, indent=2)[:4000])
        print("  " + "-" * 40)
    return {"top": sorted(keys), "content": sorted(ckeys),
            "replyish": sorted(replyish), "metadata": sorted(mkeys)}


def main():
    ap = argparse.ArgumentParser(description="引用回复能力探针（零副作用）")
    ap.add_argument("--cid", default=RUOJIN, help="入站探针用哪个群")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--only", choices=["out", "in"], default=None)
    a = ap.parse_args()

    if a.only != "in":
        _sep("A) 出站：官方 Bot API 能不能指定「回复某条消息」")
        probes_outbound()
    if a.only != "out":
        _sep("B) 入站：被引用的那条在原始 JSON 里留不留痕")
        probes_inbound(a.cid, a.limit)
    print()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
