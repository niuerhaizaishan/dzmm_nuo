#!/usr/bin/env python3
"""W1 探针 · webhook 接收与记录（对应实施计划 W1.1/W1.3）。

职责（刻意做得极简，不依赖任何业务代码）：
  1. 接收平台推送的 Webhook（Telegram Update 兼容结构）
  2. 逐条落盘到 probe/captured/<bot_key>.jsonl（含完整请求头 + 原始 body）
  3. 立即返回 200 —— 绝不阻塞在业务处理上
  4. GET /report 依据已抓取数据，自动回答能力矩阵里可自动判定的问题

用法：
    python probe/webhook_probe.py --bot-key test --port 8081 [--secret <Webhook Secret>] [--enforce-secret]

    --secret         提供后仅用于"校验是否匹配并标记"，仍会记录（方便确认 secret 是否配对）
    --enforce-secret 校验失败返回 401（真正上线时用这个）
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from aiohttp import web

CAPTURE_DIR = Path(__file__).resolve().parent / "captured"
SECRET: str | None = None
ENFORCE_SECRET = False
UPDATES: list[dict] = []          # 内存镜像，供 /report 即时分析
DEDUPE_IDS: Counter = Counter()


def _record(bot_key: str, headers: dict, body: bytes, secret_ok: bool) -> dict:
    """落盘一条推送记录并返回解析后的记录."""
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {
        "received_at": time.time(),
        "bot_key": bot_key,
        "secret_ok": secret_ok,
        "headers": headers,
        "body": body.decode("utf-8", "replace"),
    }
    with open(CAPTURE_DIR / f"{bot_key}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    try:
        parsed = json.loads(rec["body"])
    except Exception:
        parsed = None
    # update_id 重放检测（能力矩阵 Q5）
    if isinstance(parsed, dict) and "update_id" in parsed:
        DEDUPE_IDS[str(parsed["update_id"])] += 1
    if parsed is not None:
        UPDATES.append(parsed)
    return rec


async def handle_webhook(request: web.Request) -> web.Response:
    bot_key = request.match_info["bot_key"]
    body = await request.read()
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    secret_ok = (SECRET is None) or (got == SECRET)

    rec = _record(bot_key, dict(request.headers), body, secret_ok)

    if ENFORCE_SECRET and not secret_ok:
        print(f"[{bot_key}] 401 secret 不匹配（收到 {got!r}）", flush=True)
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    parsed = json.loads(rec["body"]) if rec["body"].strip().startswith("{") else {}
    kind = ",".join(k for k in parsed.keys() if k != "update_id") or "?"
    print(f"[{bot_key}] 200 update_id={parsed.get('update_id')} kind={kind} "
          f"bytes={len(body)} secret_ok={secret_ok}", flush=True)
    # 立即返回，业务处理交给主项目 worker（本探针不做任何业务）
    return web.json_response({"ok": True})


async def handle_healthz(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "captured": len(UPDATES)})


async def handle_report(_: web.Request) -> web.Response:
    return web.Response(text=build_report(), content_type="text/plain", charset="utf-8")


# ---------------------------------------------------------------- 能力矩阵自动判定

def _msg(upd: dict) -> dict:
    return upd.get("message") or upd.get("edited_message") or {}


def build_report() -> str:
    L: list[str] = []
    n = len(UPDATES)
    L.append(f"=== W1 能力矩阵自动判定（样本 {n} 条 update）===")
    if n == 0:
        L.append("尚无数据：把平台的 Webhook URL 指到 /webhook/<bot_key> 后，在群里发几条消息。")
        return "\n".join(L)

    kinds = Counter(",".join(k for k in u.keys() if k != "update_id") or "?" for u in UPDATES)
    L.append("\n[Q2] update 顶层类型分布（看是否有 message 之外的事件）")
    for k, v in kinds.most_common():
        L.append(f"     {k}: {v}")
    has_non_message = any(k != "message" for k in kinds)
    L.append(f"     含 message 之外的事件: {'是 → 可能推送入群/退群等事件' if has_non_message else '否 → 未观察到入群/退群事件'}")

    # Q2 补充：message 内的成员变动字段
    inner = Counter()
    for u in UPDATES:
        m = _msg(u)
        for f in ("new_chat_members", "left_chat_member", "new_chat_member",
                  "chat_member", "my_chat_member", "pinned_message"):
            if f in m:
                inner[f] += 1
    L.append("\n[Q2b] message 内的成员变动/系统字段")
    L.append("     " + (", ".join(f"{k}={v}" for k, v in inner.items()) if inner else "无"))

    # Q1 / Q3：是否全部消息、有无 mention 实体
    mention_yes = mention_entity = 0
    texts = 0
    for u in UPDATES:
        m = _msg(u)
        t = m.get("text")
        if not t:
            continue
        texts += 1
        ents = m.get("entities") or []
        if any((e or {}).get("type") in ("mention", "text_mention") for e in ents):
            mention_entity += 1
        if "@" in t:
            mention_yes += 1
    L.append("\n[Q1] 是否推送全部群消息")
    L.append(f"     含文本的 update: {texts}；其中带 @ 的: {mention_yes}")
    L.append("     判定：若存在明显非 @ 的普通聊天也被推送，则是『全部群消息』（需人工确认其中几条确实是无人 @ bot 的闲聊）")

    L.append("\n[Q3] mention 实体")
    L.append(f"     entities 里含 mention/text_mention 的条数: {mention_entity}")
    L.append("     判定：" + ("有 → is_at_me 可用实体精确判定" if mention_entity else "无 → 只能退化为按 bot 名字做字符串匹配"))

    # Q5：update_id 重放
    dups = {k: v for k, v in DEDUPE_IDS.items() if v > 1}
    L.append("\n[Q5] update_id 重放")
    L.append(f"     重复出现的 update_id: {len(dups)} 个" + (f" → {list(dups.items())[:5]}" if dups else "（未见重放，仍需保留幂等表）"))

    # 结构样本
    L.append("\n[样本] 首条 update 的字段路径")
    L.append(json.dumps(_shape(UPDATES[0]), ensure_ascii=False, indent=2)[:2000])

    L.append("\n[需人工判定] Q4 能否 @ 人/引用回复 · Q5 频率限制 · Q6 文档外端点 · Q7 玫瑰房类型 · Q8 Bot 管理员权限")
    L.append("     → 用 probe/api_probe.py 与平台 UI 完成，结论记入 docs/BotAPI能力矩阵.md")
    return "\n".join(L)


def _shape(o, depth=0):
    """输出字段结构骨架（值替换为类型名），便于快速看 schema。"""
    if depth > 5:
        return "..."
    if isinstance(o, dict):
        return {k: _shape(v, depth + 1) for k, v in o.items()}
    if isinstance(o, list):
        return [_shape(o[0], depth + 1)] if o else []
    if o is None:
        return None
    return type(o).__name__


def main() -> None:
    global SECRET, ENFORCE_SECRET
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot-key", default="test")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--secret", default=None)
    ap.add_argument("--enforce-secret", action="store_true")
    a = ap.parse_args()
    SECRET, ENFORCE_SECRET = a.secret, a.enforce_secret

    app = web.Application()
    app.router.add_post("/webhook/{bot_key}", handle_webhook)
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/report", handle_report)

    print(f"W1 探针启动: http://{a.host}:{a.port}/webhook/{a.bot_key}"
          f"  (secret={'已配置' if SECRET else '未配置'} enforce={ENFORCE_SECRET})", flush=True)
    print(f"抓取落盘: {CAPTURE_DIR}", flush=True)
    web.run_app(app, host=a.host, port=a.port, print=None)


if __name__ == "__main__":
    main()
