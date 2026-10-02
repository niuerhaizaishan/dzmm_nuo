# -*- coding: utf-8 -*-
"""P12-BR ⑦ / S3 留证：**会话画像盘点** —— 到底有几个"端"握着同一个登录态。

为什么要有（S3，"未授权"窗口 4~8 小时的成因未定论）：
`pw_profile` / `pw_profile_greet` / `pw_login` / `pw_captcha` 四个 Playwright 持久化
profile 各自存着一份 cookie，而平台侧的 refresh_token 是**轮换**的 ——
多个端拿着同一账号的会话互相刷新，很可能就是"同一 cookie 过一会儿又能用"的成因。
上面还没有证据，所以先**只看不猜**：把每个 profile 里的 cookie（域、名字、过期时间）、
文件时间、以及当前生效的 token（`token_store`）与心跳（`data/session_state.json`）
一起列出来，等下一次坏窗口时对照。

用法（容器内，只读）：
    docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python /tmp/probe_session_profiles.py
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

APP = Path("/app")
PROFILES = ("pw_profile", "pw_profile_greet", "pw_login", "pw_captcha",
            "pw_renew", "pw_captcha2")
AUTH_COOKIE = "sb-rls-auth-token"


def _fmt(ts):
    try:
        ts = float(ts)
    except Exception:                                          # noqa: BLE001
        return str(ts)
    if ts > 10 ** 11:                                          # 毫秒
        ts /= 1000.0
    if ts <= 0:
        return "会话级"
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) + \
        "（{}）".format("已过期" if ts < time.time() else "有效")


def _cookies(db: Path):
    """读 Playwright profile 的 Cookies sqlite（只读；值不解密，只看元数据）。"""
    tmp = Path("/tmp/_cookies_ro.sqlite")
    try:
        tmp.write_bytes(db.read_bytes())                       # 复制一份，别锁住线上
    except Exception as e:                                     # noqa: BLE001
        print("   （复制失败：{}）".format(str(e)[:60]))
        return []
    out = []
    try:
        con = sqlite3.connect("file:{}?mode=ro".format(tmp), uri=True)
        try:
            rows = con.execute(
                "select host_key, name, expires_utc, length(encrypted_value) "
                "from cookies order by host_key, name").fetchall()
        finally:
            con.close()
        for host, name, exp, blen in rows:
            # Chromium 的 expires_utc 是 1601 起的微秒
            ts = (float(exp) / 1e6 - 11644473600) if exp else 0
            out.append((host, name, ts, blen))
    except Exception as e:                                     # noqa: BLE001
        print("   （读 sqlite 失败：{}）".format(str(e)[:60]))
    finally:
        tmp.unlink(missing_ok=True)
    return out


def main():
    print("== ① 当前生效的凭据（token_store） ==")
    try:
        import token_store
        ck = token_store.load_cookies()
        print("   文件：{}  共 {} 个 cookie".format(token_store.TOKEN_FILE, len(ck)))
        for c in ck:
            if c.get("name") == AUTH_COOKIE:
                print("   {} @ {}  {}".format(c.get("name"), c.get("domain"),
                                              _fmt(c.get("expires"))))
    except Exception as e:                                     # noqa: BLE001
        print("   读不到：{}".format(str(e)[:80]))

    print("\n== ② 心跳（业务探针结论） ==")
    for p in (Path("/app/data/session_state.json"),):
        try:
            print("   {} → {}".format(p.name, p.read_text(encoding="utf-8").strip()[:160]))
        except Exception as e:                                 # noqa: BLE001
            print("   {} 读不到：{}".format(p.name, str(e)[:60]))

    print("\n== ③ 四个 Playwright profile（谁还握着会话） ==")
    for name in PROFILES:
        d = APP / name
        if not d.exists():
            print("  {}：不存在".format(name))
            continue
        db = None
        for cand in ("Default/Cookies", "Cookies"):
            if (d / cand).exists():
                db = d / cand
                break
        mtime = time.strftime("%m-%d %H:%M", time.localtime(d.stat().st_mtime))
        print("  {}（目录 mtime {}）".format(name, mtime))
        if not db:
            print("     没有 Cookies 库")
            continue
        rows = _cookies(db)
        auth = [r for r in rows if r[1] == AUTH_COOKIE]
        print("     cookie {} 个；其中登录态 {} 个".format(len(rows), len(auth)))
        for host, cname, ts, blen in auth:
            print("     AUTH {} @ {}  {}  ({} 字节)".format(cname, host, _fmt(ts), blen))
        for host, cname, ts, blen in rows[:6]:
            if cname != AUTH_COOKIE:
                print("       · {} @ {}".format(cname, host))
    print("\n判读：同一账号在**多个 profile**里都有未过期的登录态 ⇒ 多端轮换的嫌疑成立；"
          "\n      只有一个 profile 有效 ⇒ 排除这一条，坏窗口另有原因（等原始响应留证）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
