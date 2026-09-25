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
    ap.add_argument("--include-mutating", action="store_true",
                    help="⚠️ 一并探测会改状态的端点（leave/kick/ban/delete/set-webhook）。"
                         "默认关闭：这些路径即使传无效 id 也可能被后端真的执行（最坏 = bot 退群）")
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
    # 实测（2026-09-22）：该 API **不用 HTTP 状态码表达存在性** —— 未知方法也返回
    # 200 + {"ok":false,"message":"Invalid method"}。故必须按 **响应体** 判定。
    print("\n" + "=" * 70)
    print("[Q6] 文档外端点发现（按响应体判定：'Invalid method' = 方法不存在）")
    print("=" * 70)

    # 全 0 的 chatroom_id：确定不存在，用于取"方法存在"的响应形状，且不会投递到任何群
    BOGUS = "00000000-0000-0000-0000-000000000000"

    def classify(tx: str) -> str:
        try:
            j = json.loads(tx)
        except Exception:
            return "非JSON"
        if not isinstance(j, dict):
            return "?"
        msg = str(j.get("message", ""))
        if msg.strip().lower() == "invalid method":
            return "不存在"
        if j.get("ok") is True:
            return "存在·成功"
        return "存在·" + (msg[:36] or "ok:false")

    # 对照：已知端点 + 不存在的 chatroom_id → 拿到"方法存在"的响应形状（安全，不会真的发出消息）
    print("\n[对照] 已知端点 + 不存在的 chatroom_id（安全，不会投递到任何群）")
    for label, url, hdr, payload in [
        ("send-message", f"{O}/api/bot/send-message", HDR,
         {"chatroom_id": BOGUS, "content": "probe"}),
        ("pin-message", f"{O}/api/bot/pin-message", HDR,
         {"chatroom_id": BOGUS, "message_id": "x"}),
        ("bot<token>/sendMessage", f"{O}/api/bot/bot{a.token}/sendMessage",
         {"Content-Type": "application/json"}, {"chat_id": BOGUS, "text": "probe"}),
    ]:
        st, ms, tx = call("POST", url, hdr, payload, a.proxy)
        print(f"   {label:<24} -> {st} {ms}ms [{classify(tx)}] {tx[:120]}")
    print("   ↑ 这种形状 = 『方法存在』。下面同形状即存在；'Invalid method' 即不存在。")

    # ⚠️ 安全设计：默认只探只读端点。
    #    会改状态的路径（leave/kick/ban/delete/set-webhook）即使传无效 id，
    #    也可能被实现不严的后端真的执行 —— 最坏情况是让 bot 直接退群。
    #    确需探测时显式加 --include-mutating。
    read_only_kebab = ["get-me", "get-chat", "get-members", "get-chat-members",
                       "get-chat-member", "get-chat-member-count", "get-chat-administrators",
                       "get-user", "get-updates", "get-verified", "get-profile",
                       "get-webhook-info", "webhook-info"]
    read_only_camel = ["getMe", "getChat", "getChatMember", "getChatMemberCount",
                       "getChatAdministrators", "getUpdates", "getWebhookInfo",
                       "getUserProfilePhotos"]
    mutating_kebab = ["send-message", "pin-message", "send-photo", "send-photo-url",
                      "delete-message", "edit-message", "leave-chat", "kick-member",
                      "ban-member", "remove-member", "set-webhook", "delete-webhook"]
    mutating_camel = ["sendMessage", "sendPhoto", "deleteMessage", "editMessageText",
                      "sendChatAction", "leaveChat", "banChatMember", "kickChatMember",
                      "setWebhook", "deleteWebhook"]

    kebab = read_only_kebab + (mutating_kebab if a.include_mutating else [])
    camel = read_only_camel + (mutating_camel if a.include_mutating else [])
    if a.include_mutating:
        print("\n⚠️ 已启用 --include-mutating：将探测会改状态的端点，请确认目标是测试群！")
    else:
        print("\n（只探只读端点；要探 leave/kick/ban/delete 等请显式加 --include-mutating）")

    print("\n-- A 形式：POST /api/bot/<kebab> + X-Bot-Token --")
    for m in kebab:
        st, ms, tx = call("POST", f"{O}/api/bot/{m}", HDR,
                          {"chatroom_id": BOGUS, "chat_id": BOGUS}, a.proxy)
        c = classify(tx)
        mark = "★ " if c.startswith("存在") else "  "
        print(f"   {mark}{m:<24} -> {st} {ms}ms [{c}] {tx[:90]}")

    print("\n-- B 形式：POST /api/bot/bot<token>/<Camel> --")
    for m in camel:
        st, ms, tx = call("POST", f"{O}/api/bot/bot{a.token}/{m}",
                          {"Content-Type": "application/json"},
                          {"chat_id": BOGUS}, a.proxy)
        c = classify(tx)
        mark = "★ " if c.startswith("存在") else "  "
        print(f"   {mark}{m:<26} -> {st} {ms}ms [{c}] {tx[:90]}")

    print("\n把 ★ 标记的端点逐个人工确认语义，结论记入 docs/BotAPI能力矩阵.md")


if __name__ == "__main__":
    main()
