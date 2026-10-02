"""P12-AR：续期链路体检（**不打印任何 token 内容**，只看时间与状态）。

为什么要单独一个探针（屋主 2026-09-29 问"token 续期那事儿怎么样了"）：

    P12-AJ 之后 `_renew_locked()` 有个**静默快路径** —— token 剩余 > `RENEW_AHEAD_SEC`
    时 `_write_session_state("ok"); record_success(); return 0`，**连一行日志都不写**。
    于是 `renew_token.log` 长时间没有新行有两种完全不同的含义：

        (a) 健康：token 还新鲜，循环每 5 分钟静默跑一次；
        (b) 停摆：循环死了 / 卡在某个 HTTP 上。

    光看日志分不出来。这个探针看**事实**：

        · cookie 里 `expires_at` 还剩几分钟（决定下一次"真续期"何时发生）
        · `session_state.json` 多久没被写过（> 5 分钟 = 循环没在跑）
        · 失败连击 / emergency 冷却 / 日志尾部 / 有没有 token.emergency

用法（容器内）：
    python /app/probe_token_state.py
"""
import base64
import json
import sys
import time
from pathlib import Path

APP = Path("/app")
COOKIE_NAME = "sb-rls-auth-token"
RENEW_AHEAD_SEC = 600          # renew_token.RENEW_AHEAD_SEC 的兜底值（下面会尝试真读）


def _fmt(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _age_min(p: Path):
    return (time.time() - p.stat().st_mtime) / 60.0 if p.exists() else None


def _tail(p: Path, n=3):
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-n:]
    except Exception:                                          # noqa: BLE001
        return []


def _decode_cookie_json(path=None):
    """取 `sb-rls-auth-token` 的 cookie 负载。

    `path` 给了就读**那个 token JSON 文件**（用来对比历史备份，比如
    `/app/.dzmm_token.json.bak-*`）；否则走 `token_store`（= 当前生效的那份）。
    """
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for c in (data.get("cookies") or []):
            if c.get("name") == COOKIE_NAME:
                val = c.get("value", "")
                break
        else:
            return None, {}
    else:
        import token_store
        cookie = token_store.get_cookie(COOKIE_NAME)
        val = (cookie or {}).get("value", "")
    payload = val[len("base64-"):] if val.startswith("base64-") else val
    payload += "=" * (-len(payload) % 4)
    try:
        return None, json.loads(base64.b64decode(payload)) if payload else {}
    except Exception:                                          # noqa: BLE001
        return None, {}


def _probe_business():
    """当场跑一次 `renew_token._api_probe()` —— 它是 NOTE / SESSION_DEAD 的分水岭。

    只做**只读业务调用**（不改 token、不写 emergency），但会刷新
    `session_state.json`（这正是循环本来每 5 分钟要做的事）。
    """
    import renew_token as R
    t0 = time.time()
    verdict = R._api_probe()
    dt = time.time() - t0
    print("业务探针      = {!r}（{:.1f}s）".format(verdict, dt))
    if verdict == "ok":
        print("              → 会话**活着**：此时只读接口就算 401，也只会记 NOTE、不重登 ✅")
    elif verdict == "unauth":
        print("              → 会话**真失效**：会记 SESSION_DEAD 并写 token.emergency 触发重登")
    else:
        print("              → 探针本身失败：按 P12-AJ 的规则**不下结论、不重登**（避免误杀）")
    return verdict


def main():
    if "--probe" in sys.argv:
        sys.argv.remove("--probe")
        _probe_business()
        print("-" * 62)
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    try:
        import renew_token as R
        ahead = int(getattr(R, "RENEW_AHEAD_SEC", RENEW_AHEAD_SEC))
    except Exception:                                          # noqa: BLE001
        ahead = RENEW_AHEAD_SEC

    print("=" * 62)
    print("续期链路体检  " + _fmt(time.time()))
    print("=" * 62)

    # 1) token 本身（只输出时间与长度，不输出内容）
    if len(sys.argv) > 1:
        path = sys.argv[1]
        print("对比文件 = {}".format(path))
        stamp = Path(path).stat().st_mtime if Path(path).exists() else 0
        if stamp:
            print("  写入时间 = {}".format(_fmt(stamp)))
        cookie, d = _decode_cookie_json(path)
    else:
        try:
            cookie, d = _decode_cookie_json()
        except Exception as e:                                 # noqa: BLE001
            cookie, d = None, {}
            print("❌ 读 cookie 失败：{}".format(str(e)[:120]))

    if not d:
        print("❌ cookie 解析不出 JSON（line 37 `read_token_cookie` 会返回 None → 判 FAIL）")
    else:
        exp = float(d.get("expires_at", 0) or 0)
        left_min = (exp - time.time()) / 60.0
        print("cookie 字段   = {}".format(sorted(d.keys())))
        print("access_token  = <str len={}>".format(len(str(d.get("access_token", "")))))
        print("refresh_token = <str len={}>".format(len(str(d.get("refresh_token", "")))))
        if exp:
            print("expires_at    = {}   → {}".format(
                int(exp), "已过期 {:.0f} 分钟".format(-left_min) if left_min < 0
                else "还剩 {:.0f} 分钟".format(left_min)))
        else:
            print("expires_at    = 缺失（会一直走真续期路径，不会静默快路径）")

    # 2) 循环心跳：session_state.json 是快路径唯一会写的东西
    ss = APP / "data" / "session_state.json"
    age = _age_min(ss)
    print("-" * 62)
    if age is None:
        print("循环心跳      = ❌ 没有 {}（循环从没成功跑过？）".format(ss))
    else:
        mark = "✅ 活着" if age <= 6 else ("⚠️ 偏旧" if age <= 15 else "❌ 疑似停摆")
        print("循环心跳      = {}（session_state.json {:.1f} 分钟前写过）".format(mark, age))
        try:
            print("              内容 = {}".format(
                json.loads(ss.read_text(encoding="utf-8"))))
        except Exception:                                      # noqa: BLE001
            pass

    # 3) 静默快路径在不在生效（这决定了"日志没新行"是不是正常）
    if d:
        left = float(d.get("expires_at", 0) or 0) - time.time()
        if left > ahead:
            print("快路径        = 生效中（还剩 {:.0f} 分钟 > 阈值 {}s）"
                  "→ 日志静默是**正常**的".format(left / 60.0, ahead))
            print("下一次真续期  = 约 {:.0f} 分钟后（剩余 ≤ {} 分钟时才会打接口）".format(
                (left - ahead) / 60.0, ahead // 60))
        else:
            print("快路径        = 未生效（还剩 {:.0f} 分钟 ≤ 阈值 {}s）"
                  "→ 现在每 5 分钟都会真打一次续期".format(left / 60.0, ahead))

    # 4) 失败/紧急状态
    print("-" * 62)
    for name in ("renew_fail_state.json", "renew_emergency_state.json"):
        p = APP / name
        txt = p.read_text(encoding="utf-8").strip() if p.exists() else "（无）"
        print("{:28} = {}".format(name, txt))
    flag = APP / "token.emergency"
    print("{:28} = {}".format("token.emergency",
                              "❌ 存在（会触发 bot 重启）" if flag.exists() else "无 ✅"))
    p = APP / "bot.paused"
    print("{:28} = {}".format("bot.paused",
                              "⚠️ 存在（已暂停自动任务）" if p.exists() else "无 ✅"))

    # 5) 日志尾部（快路径下这里会长时间不动，别据此判断死活）
    print("-" * 62)
    print("renew_token.log 尾部：")
    for line in _tail(APP / "renew_token.log", 4):
        print("   " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
