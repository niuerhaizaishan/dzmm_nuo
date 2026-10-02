# -*- coding: utf-8 -*-
"""P12-BI 探针：**"首页 418"到底是谁在 418**（几条路的对照）。

背景：宿主机上的 `monitor_recover.py` 三周来一直记 `首页=418`（6570 次），
而我在容器里用 requests 测同一个地址是 **200**。两者差在哪，必须先查清 ——
因为 monitor_recover 的"风控解除后自动重启重登"就是靠这个 200 触发的，
如果它永远看不到 200，那条自动恢复**从来没生效过**。

对照项：
  ① urllib（monitor_recover 的原样：自定义 UA、关校验、不跟代理）
  ② requests（默认 UA）
  ③ requests（浏览器 UA）
  ④ requests + env_config 里的代理
  ⑤ 业务调用（读群消息）—— 真正决定机器人能不能干活的那条

用法（容器内）：`python /app/probe_home_wall.py`
"""
import ssl
import sys

sys.path.insert(0, "/app")

ORIGIN = "https://" + "www.dzmm.ai"
CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
UA_AGENT = "Mozilla/5.0 DZMM-Agent/1.0"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def main():
    import env_config
    proxies = env_config.get_proxies() or {}
    print("origin = {} | 代理 = {}".format(ORIGIN, proxies or "（没配，直连）"))
    print()

    # ① urllib
    try:
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(ORIGIN + "/", headers={"User-Agent": UA_AGENT})
        r = urllib.request.urlopen(req, timeout=20, context=ctx)
        print("① urllib(DZMM-Agent, 直连)   -> {} {}".format(
            r.status, "✅" if r.status == 200 else "🚧 风控墙"))
    except Exception as e:                                     # noqa: BLE001
        code = getattr(e, "code", None)
        print("① urllib(DZMM-Agent, 直连)   -> {} {}".format(
            code or "异常", "🚧 风控墙（418=captcha）" if code == 418 else str(e)[:60]))

    import requests
    for label, ua, use_proxy in (
            ("② requests(默认UA, 直连)", None, False),
            ("③ requests(浏览器UA, 直连)", UA_BROWSER, False),
            ("④ requests(浏览器UA, 代理)", UA_BROWSER, True)):
        h = {"User-Agent": ua} if ua else {}
        try:
            r = requests.get(ORIGIN + "/", timeout=20, headers=h,
                             proxies=proxies if use_proxy else None, allow_redirects=True)
            print("{} -> {} {}".format(
                label, r.status_code,
                "✅" if r.status_code == 200 else
                ("🚧 风控墙" if r.status_code == 418 else "?")))
        except Exception as e:                                 # noqa: BLE001
            print("{} -> 异常 {}".format(label, str(e)[:60]))

    # ⑤ 业务调用
    try:
        import dzmm
        msgs = dzmm.get_messages(CID, 3)
        print("⑤ 业务调用(读群消息)          -> ✅ 通了（{} 条）".format(len(msgs or [])))
    except Exception as e:                                     # noqa: BLE001
        print("⑤ 业务调用(读群消息)          -> ❌ {}".format(str(e)[:90]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
