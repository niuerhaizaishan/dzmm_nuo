#!/usr/bin/env python3
"""W1 探针 · 出站接口 + 文档外端点探测（对应实施计划 W1.3 的 Q4/Q5/Q6）。

⚠️ 会向目标群真实发送消息 —— 只对**测试群**使用。

用法：
    python probe/api_probe.py --token <API Token> --chatroom-id <测试群 cid> [--origin https://www.dzmm.ai]
                              [--proxy http://127.0.0.1:7890] [--skip-send] [--rate-n 8]

说明：本机直连 dzmm.ai / laopo.ai 会被 SNI 阻断，本地调试请给 --proxy（默认走 127.0.0.1:7890 的 FlClash）。
"""
import argparse
import json
import time

import requests

TIMEOUT = 25


def call(method: str, url: str, headers: dict, payload: dict | None, proxy: str | None):
    """发一次请求，返回 (status, elapsed_ms, text)。"""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    t0 = time.time()
    try:
        r = requests.request(method, url, headers=headers, json=payload,
                             timeout=TIMEOUT, proxies=proxies)
        return r.status_code, int((time.time() - t0) * 1000), r.text[:800]
    except Exception as e:
        return None, int((time.time() - t0) * 1000), f"EXC {type(e).__name__}: {e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--chatroom-id", required=True)
    ap.add_argument("--origin", default="https://www.dzmm.ai")
    ap.add_argument("--proxy", default="http://127.0.0.1:7890")
    ap.add_argument("--rate-n", type=int, default=8)
    ap.add_argument("--skip-send", action="store_true", help="只做端点发现，不发任何消息")
    a = ap.parse_args()

    O = a.origin.rstrip("/")
    CID = a.chatroom_id
    HDR = {"X-Bot-Token": a.token, "Content-Type": "application/json"}
    print(f"origin={O}  群={CID}  代理={a.proxy or '直连'}\n")

    if not a.skip_send:
        print("=" * 70)
        print("[Q4] 出站接口能力实测（会在群里留下测试消息）")
        print("=" * 70)

        # 1) 自定义接口：发文本
        st, ms, tx = call("POST", f"{O}/api/bot/send-message", HDR,
                          {"chatroom_id": CID, "content": "【探针1】纯文本发送测试"}, a.proxy)
        print(f"1. send-message 文本            -> {st} {ms}ms {tx[:160]}")

        # 2) 能否 @ 人（mention 是否被解析、是否真的产生提醒）
        st, ms, tx = call("POST", f"{O}/api/bot/send-message", HDR,
                          {"chatroom_id": CID, "content": "【探针2】@测试 能否 @ 到人？"}, a.proxy)
        print(f"2. send-message 含 @            -> {st} {ms}ms {tx[:160]}   ← 人工看群里是否真的 @ 到")

        # 3) Telegram 兼容：sendMessage
        st, ms, tx = call("POST", f"{O}/api/bot/bot{a.token}/sendMessage",
                          {"Content-Type": "application/json"},
                          {"chat_id": CID, "text": "【探针3】Telegram 兼容 sendMessage"}, a.proxy)
        print(f"3. bot<token>/sendMessage       -> {st} {ms}ms {tx[:160]}")

        # 4) Telegram 兼容：sendPhoto（仅 URL 图片）
        st, ms, tx = call("POST", f"{O}/api/bot/bot{a.token}/sendPhoto",
                          {"Content-Type": "application/json"},
                          {"chat_id": CID,
                           "photo": "https://www.python.org/static/community_logos/python-logo-master-v3-TM.png",
                           "caption": "【探针4】URL 图片"}, a.proxy)
        print(f"4. bot<token>/sendPhoto         -> {st} {ms}ms {tx[:160]}")

        # 5) 置顶（先用上一步返回的 message_id 更好；此处用占位 id 测接口是否存在）
        st, ms, tx = call("POST", f"{O}/api/bot/pin-message", HDR,
                          {"chatroom_id": CID, "message_id": "probe-placeholder"}, a.proxy)
        print(f"5. pin-message（占位 id）        -> {st} {ms}ms {tx[:160]}   ← 非 404 即接口存在")

        # 6) content 长度上限：文档称 10000
        for n in (9999, 10000, 10001):
            st, ms, tx = call("POST", f"{O}/api/bot/send-message", HDR,
                              {"chatroom_id": CID, "content": "【探针6】" + "A" * (n - 6)}, a.proxy)
            print(f"6. content={n:>5} 字符          -> {st} {ms}ms {tx[:120]}")

        # 7) 频率限制：连发
        print(f"\n[Q5] 频率限制：连发 {a.rate_n} 条")
        ok = fail = 0
        for i in range(a.rate_n):
            st, ms, tx = call("POST", f"{O}/api/bot/send-message", HDR,
                              {"chatroom_id": CID, "content": f"【限流测试 {i+1}/{a.rate_n}】"}, a.proxy)
            if st == 200:
                ok += 1
            else:
                fail += 1
            print(f"    #{i+1} -> {st} {ms}ms {'' if st == 200 else tx[:120]}")
        print(f"    成功 {ok} / 失败 {fail}" + ("（未见限流）" if fail == 0 else "（出现限流，需退避策略）"))

    # ------------------------------------------------------------------ Q6 端点发现
    print("\n" + "=" * 70)
    print("[Q6] 文档外端点发现（非 404 即可能存在；401/403 说明存在但需权限）")
    print("=" * 70)

    kebab = ["send-message", "pin-message", "get-me", "get-chat", "get-members",
             "get-chat-members", "get-chat-member", "get-user", "get-updates",
             "delete-message", "edit-message", "send-photo", "send-photo-url",
             "leave-chat", "kick-member", "ban-member", "remove-member",
             "get-verified", "get-profile", "set-webhook", "delete-webhook", "get-webhook-info"]
    camel = ["getMe", "getChat", "getChatMember", "getChatMemberCount", "getChatAdministrators",
             "getUpdates", "deleteMessage", "editMessageText", "sendChatAction", "leaveChat",
             "banChatMember", "kickChatMember", "getUserProfilePhotos", "setWebhook",
             "deleteWebhook", "getWebhookInfo", "sendMessage", "sendPhoto"]

    print("\n-- A 形式：POST /api/bot/<kebab> + X-Bot-Token --")
    print("   对照：send-message 已知可用（应 200）")
    for m in kebab:
        st, ms, tx = call("POST", f"{O}/api/bot/{m}", HDR,
                          {"chatroom_id": CID, "chat_id": CID}, a.proxy)
        mark = "  " if st == 404 else "★ "
        print(f"   {mark}{m:<22} -> {st} {ms}ms {tx[:100]}")

    print("\n-- B 形式：POST /api/bot/bot<token>/<Camel> --")
    for m in camel:
        st, ms, tx = call("POST", f"{O}/api/bot/bot{a.token}/{m}",
                          {"Content-Type": "application/json"},
                          {"chat_id": CID}, a.proxy)
        mark = "  " if st == 404 else "★ "
        print(f"   {mark}{m:<24} -> {st} {ms}ms {tx[:100]}")

    print("\n把 ★ 标记的端点逐个人工确认语义，结论记入 docs/BotAPI能力矩阵.md")


if __name__ == "__main__":
    main()
