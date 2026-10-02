# -*- coding: utf-8 -*-
"""P12-BK 关键实验：**平台是不是按 User-Agent 挑战我们**？

线索链：
  · 宿主 `monitor_recover.py` 用 urllib **默认 UA**（`Python-urllib/3.x`）看首页 →
    三周一直是 **418**（6570 次）；
  · 我在容器里用 `requests` + UA `Mozilla/5.0 DZMM-Agent/1.0` 看同一个首页 → **每次 200**；
  · 而 `dzmm.api()` / `renew_token.get_new_token()` 走 `requests` **不带 UA**
    ⇒ 默认 UA 是 `python-requests/2.x`；
  · 12 个镜像域名从容器里看**全部 200 + token-ok**，但宿主看本节点还是 418。

如果真的是"按 UA 挑战"，那这几天"续期失效 4~8 小时后自己好"就有解释了：
**风控按 UA 命中率挑选，命中时我们的 API 调用被回成 401/未授权（甚至 418）**，
换个 UA 就通 —— 修法也就一行（给所有平台请求带上浏览器 UA）。

本探针把同一个端点用三种 UA 各打几轮，打印状态码与 body 片段。
只读，不发任何消息。

用法（容器内）：`python /app/probe_ua_wall.py`
"""
import sys
import time

sys.path.insert(0, "/app")

ORIGIN = "https://www.dzmm.ai"
UAS = (
    ("requests 默认", None),
    ("DZMM-Agent", "Mozilla/5.0 DZMM-Agent/1.0"),
    ("Chrome 126", ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")),
    ("Python-urllib", "Python-urllib/3.11"),
)


def main():
    import requests
    import env_config
    import token_store
    cookies = token_store.get_cookie_dict()
    proxies = env_config.get_proxies() or {}
    print("origin = {} | 代理 = {}".format(ORIGIN, proxies or "（直连）"))
    for rnd in (1, 2):
        print()
        print("========== 第 {} 轮 ==========".format(rnd))
        for label, ua in UAS:
            h = {"User-Agent": ua} if ua else {}
            # ① 首页
            try:
                r = requests.get(ORIGIN + "/", timeout=15, headers=h, proxies=proxies)
                home = "{} {}".format(r.status_code,
                                      "✅" if r.status_code == 200 else
                                      ("🚧418" if r.status_code == 418 else ""))
            except Exception as e:                             # noqa: BLE001
                home = "异常 " + str(e)[:40]
            # ② 只读 token 接口（带我们的 cookie）
            try:
                r2 = requests.get(ORIGIN + "/api/auth/token", timeout=15, headers=h,
                                  cookies=cookies, proxies=proxies)
                body = r2.text[:60].replace("\n", " ")
                tok = "有 token" if "access_token" in r2.text else "无 token"
                api = "{} {} | {}".format(r2.status_code, tok, body)
            except Exception as e:                             # noqa: BLE001
                api = "异常 " + str(e)[:40]
            print("  {:<14} 首页 {:<10} /api/auth/token -> {}".format(label, home, api))
            time.sleep(1)
    print()
    print("判读：如果只有'requests 默认 / Python-urllib'被 418 或回未授权，")
    print("      而 Chrome/DZMM-Agent 正常 ⇒ **风控是按 UA 挑战的**，")
    print("      修法 = 给所有平台请求（dzmm.api / get_new_token / SIO 握手）带上浏览器 UA。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
