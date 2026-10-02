# -*- coding: utf-8 -*-
"""P12-BH 关键验证：**浏览器 profile 里的 cookie 和 token JSON 里的是不是同一份？**

怀疑（"这几天经常续期失效"）：
平台的 `refresh_token` 是**轮换制**，而 SPA（常驻页面）自己也会刷 token ——
刷完它把**新的** refresh_token 留在浏览器 profile 的 cookie 里，而我们抄到 JSON 的
那份是**旧的**。于是：

    我们拿旧 refresh_token 去续期 → 平台回"未授权" → 我们判"会话真死" → emergency/重登
    （而浏览器 profile 里其实躺着一份**能用的**新 token）

如果这个猜测成立，日志里那 460 次 `refresh_token 失效（未授权）` 大部分根本不用重登 ——
**把浏览器那份抄回来**就行（这也解释了：为什么"会话死了"的时候，浏览器还起得来页面）。

做法：直接读 `pw_profile/Default/Cookies`（sqlite，先 copy 一份避免锁），
把里面的 `sb-rls-auth-token` 解出来，和 token JSON 比对 refresh_token / expires_at。

用法（容器内）：`python /app/probe_cookie_freshness.py`
"""
import base64
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")


def _decode(value: str) -> dict:
    v = value[len("base64-"):] if value.startswith("base64-") else value
    v += "=" * (-len(v) % 4)
    try:
        return json.loads(base64.b64decode(v))
    except Exception as e:                                     # noqa: BLE001
        return {"_err": str(e)[:60]}


def _cookie_from_profile(profile: str) -> dict:
    src = Path("/app") / profile / "Default" / "Cookies"
    if not src.exists():
        return {"_err": "没有 " + str(src)}
    tmp = Path("/tmp") / (profile + "_Cookies")
    try:
        shutil.copy2(src, tmp)
    except Exception as e:                                     # noqa: BLE001
        return {"_err": "copy 失败 " + str(e)[:50]}
    try:
        con = sqlite3.connect(str(tmp))
        rows = con.execute(
            "SELECT name, value FROM cookies WHERE name LIKE '%auth%'").fetchall()
        con.close()
    except Exception as e:                                     # noqa: BLE001
        return {"_err": "sqlite 失败 " + str(e)[:60]}
    out = {}
    for name, value in rows:
        out[name] = _decode(value)
    return out


def _fmt(d: dict) -> str:
    if not d:
        return "（无）"
    if "_err" in d:
        return "错误：" + d["_err"]
    exp = d.get("expires_at") or 0
    left = (exp - time.time()) / 60 if exp else 0
    return ("refresh={!r} access={} expires_at={}（{}）".format(
        str(d.get("refresh_token"))[:12],
        str(d.get("access_token"))[:10] + "…" if d.get("access_token") else "无",
        time.strftime("%H:%M:%S", time.localtime(exp)) if exp else "-",
        ("剩 {:.0f} 分".format(left)) if left > 0 else "已过期"))


def main():
    import env_config
    import token_store
    print("=" * 74)
    print("① token JSON（机器人自己抄的那份）")
    try:
        v = (token_store.get_cookie("sb-rls-auth-token") or {}).get("value", "")
        js = _decode(v)
        print("   " + _fmt(js))
    except Exception as e:                                     # noqa: BLE001
        print("   读不到：", str(e)[:80])
    print()
    print("② 浏览器 profile 里的那份")
    for prof in ("pw_profile", "pw_profile_greet", "pw_login", "pw_captcha"):
        ck = _cookie_from_profile(prof)
        if "_err" in ck:
            print("   {:<18} {}".format(prof, ck["_err"][:90]))
            continue
        for name, d in ck.items():
            print("   {:<18} {} -> {}".format(prof, name[:28], _fmt(d)))
        if not ck:
            print("   {:<18} （没找到 auth cookie）".format(prof))
    print()
    print("=" * 74)
    print("③ 结论提示")
    print("   两份的 refresh_token 不同 ⇒ 我们手里那份已经**被轮换掉**了（就是未授权的来源）")
    print("   ⇒ 续期前应**先从浏览器 profile 抄回 cookie**，而不是直接判死重登。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
