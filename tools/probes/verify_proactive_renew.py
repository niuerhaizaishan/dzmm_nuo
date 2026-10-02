"""P12-AT 验收：**窗口内主动浏览器续期**到底会不会真跑（真跑，但只动 token 本身）。

为什么不在线上等：一小时才进一次窗口。所以在独立进程里做两件事：

    1. 把 `RENEW_AHEAD_SEC` 调到 `10**9`（快路径判据 `expires_at - now > AHEAD`
       立刻为假）→ 真的走到"回显接口比对"那一步；
    2. **把回显接口的返回值打桩成"原样返回当前 token"** —— 这正是 2026-09-29
       15:39/15:44 平台的真实行为（也是 142 分钟宕机的前一步）。
       不打这个桩，平台今天恰好给了新 token 就会走 `OK` 路径，
       **新分支根本不会被经过**（第一版验收就是这么"通过"的假阳性）。

除了这一处打桩，**浏览器那半段全是真的**：真开 `pw_renew` profile 的 headless 页面、
真让前端刷 access_token、真写回 token JSON。它不会碰 bot 的 `pw_profile`。

用法（容器内）：
    python /app/verify_proactive_renew.py
"""
import hashlib
import json
import sys
import time
from pathlib import Path

APP = "/app"
for _p in (APP,):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _exp():
    import renew_token as R
    _c, d = R.read_token_cookie()
    d = d or {}
    at = d.get("access_token", "")
    return (d.get("expires_at", 0), at,
            hashlib.sha256(at.encode()).hexdigest()[:12] if at else "-")


def _fmt(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def main():
    import renew_token as R

    old_exp, old_at, old_fp = _exp()
    print("=" * 66)
    print("P12-AT 验收：窗口内主动浏览器续期")
    print("=" * 66)
    print("续期前：expires_at={} ({})  token指纹={}".format(
        int(old_exp), _fmt(old_exp), old_fp))

    logp = R.ROOT / "renew_token.log"
    n0 = len(logp.read_text(encoding="utf-8").splitlines()) if logp.exists() else 0

    # ① 打穿窗口 ② 打桩"回显接口原样返回旧 token"（平台在 15:39/15:44 的真行为）
    R.RENEW_AHEAD_SEC = 10 ** 9
    R.get_new_token = lambda: {"access_token": old_at,
                               "expires_at": old_exp,
                               "expires_in": 3600}
    t0 = time.time()
    rc = R.main()
    dt = time.time() - t0
    print("\nmain() 返回 = {}（耗时 {:.1f}s，浏览器那半段是真的）".format(rc, dt))

    new_lines = []
    if logp.exists():
        new_lines = logp.read_text(encoding="utf-8").splitlines()[n0:]
    print("新增日志：")
    for ln in new_lines:
        print("   " + ln)

    new_exp, new_at, new_fp = _exp()
    print("\n续期后：expires_at={} ({})  token指纹={}".format(
        int(new_exp), _fmt(new_exp), new_fp))

    ran = any("主动走浏览器续期" in x for x in new_lines)
    got_new = new_fp != old_fp
    print("\n" + "-" * 66)
    print("走到主动续期分支 = {}".format("✅" if ran else "❌（没走到，看上面的日志）"))
    print("拿到新 token       = {}（{} → {}）".format(
        "✅" if got_new else "❌", old_fp, new_fp))
    print("expires_at 前移    = {}".format(
        "✅ {} → {}".format(_fmt(old_exp)[11:], _fmt(new_exp)[11:])
        if new_exp > old_exp else "（没前移：这次平台也没给更晚的 exp）"))
    ok = ran and got_new
    print("结论：{}".format(
        "✅ 回显接口给不出新 token 时，会主动开浏览器把 token 刷新"
        if ok else "⚠️ 仍需人工看一眼（ran={} got_new={}）".format(ran, got_new)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
