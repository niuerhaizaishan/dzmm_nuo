# -*- coding: utf-8 -*-
"""P12-BF 探针：**会话到底死没死、风控在哪一层**（只读）。

背景（屋主 2026-09-30 14:0x："妹子群怎么不自动踢人了？"）：
踢人要先**读群 + 查成员资料**，而这两个都走账号会话；日志里每 10 分钟一次
`tRPC chatroom.getMessages: Invalid authentication credentials` ⇒ 会话失效。

这个探针把"死在哪一层"分清楚：
  ① 首页（IP 级风控：418 = 撞验证码墙 / 200 = 通）
  ② `/api/auth/token`（会话回显：有没有 access_token）
  ③ 业务调用（读群消息 —— 真正决定机器人能不能干活的那一层）
  ④ 本地 token JSON 的 expires_at（还剩多久）
  ⑤ renew 的退避状态（browser backoff / session_state）

用法（容器内）：`python /app/probe_session_layers.py [cid]`
"""
import json
import sys
import time

sys.path.insert(0, "/app")

CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"      # 认证妹子群


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else CID
    import requests
    import env_config
    import token_store
    import dzmm

    origin = env_config.get_dzmm_origin().rstrip("/")
    cookies = token_store.get_cookie_dict()
    print("origin =", origin, "| cookie 数 =", len(cookies))
    proxies = env_config.get_proxies() or {}

    # ① 首页
    try:
        r = requests.get(origin + "/", timeout=20, proxies=proxies,
                         headers={"User-Agent": "Mozilla/5.0 DZMM-Agent/1.0"})
        print("① 首页            -> {} {}".format(
            r.status_code, "✅ 通" if r.status_code == 200 else
            ("🚧 撞验证码墙（418）" if r.status_code == 418 else "?")))
    except Exception as e:                                     # noqa: BLE001
        print("① 首页            -> 异常", str(e)[:70])

    # ② 会话回显
    try:
        r = requests.get(origin + "/api/auth/token", timeout=20, proxies=proxies,
                         cookies=cookies)
        tok = ""
        try:
            tok = (r.json() or {}).get("access_token", "")
        except Exception:                                      # noqa: BLE001
            pass
        print("② /api/auth/token -> {} | access_token {} | body {}".format(
            r.status_code, ("有（" + tok[:12] + "…）") if tok else "**没有**",
            r.text[:80].replace("\n", " ")))
    except Exception as e:                                     # noqa: BLE001
        print("② /api/auth/token -> 异常", str(e)[:70])

    # ③ 业务调用
    try:
        msgs = dzmm.get_messages(cid, 3)
        print("③ 读群消息        -> ✅ 通了，拿到 {} 条".format(len(msgs or [])))
    except Exception as e:                                     # noqa: BLE001
        print("③ 读群消息        -> ❌ {}".format(str(e)[:110]))

    # ④ 本地 token
    try:
        import token_store
        d = json.loads(token_store.TOKEN_FILE.read_text(encoding="utf-8"))
        import base64
        c = {x.get("name"): x for x in (d.get("cookies") or [])}
        v = (c.get("sb-rls-auth-token") or {}).get("value") or ""
        payload = v[len("base64-"):] if v.startswith("base64-") else v
        payload += "=" * (-len(payload) % 4)
        info = json.loads(base64.b64decode(payload))
        exp = float(info.get("expires_at") or 0)
        print("④ token expires_at -> {}（{}）".format(
            time.strftime("%H:%M:%S", time.localtime(exp)),
            ("已过期 {:.0f} 分钟".format((time.time() - exp) / 60)) if exp < time.time()
            else "还剩 {:.0f} 分钟".format((exp - time.time()) / 60)))
    except Exception as e:                                     # noqa: BLE001
        print("④ token 解析       -> 失败", str(e)[:70])

    # ⑤ 退避状态
    for name, path in (("session_state", env_config.data_dir() / "session_state.json"),
                       ("browser_backoff", env_config.data_dir() / "renew_browser_backoff.json"),
                       ("renew_backoff", env_config.renew_backoff_file())):
        try:
            print("⑤ {:<15} -> {}".format(name, path.read_text(encoding="utf-8")[:200]))
        except Exception as e:                                 # noqa: BLE001
            print("⑤ {:<15} -> （读不到：{}）".format(name, str(e)[:40]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
