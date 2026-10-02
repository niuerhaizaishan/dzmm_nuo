"""P12-AR 验收：**真续期路径（SKIP/OK）到底写不写心跳**（只读实验，不动 token）。

为什么需要这个实验：心跳（`data/session_state.json`）原先只有"快路径"和 401 分支写，
而 `SKIP`/`OK` 两条真续期路径不写 → 每次续期窗口（token 剩余 ≤10 分钟那 10 分钟）
心跳必然变旧，实测出现过 15.9 分钟的"健康但像停摆"。
单测只能证明"`main()` 在 rc==0 时会写"，证明不了**真实的 SKIP 分支**会走到 rc==0。
而线上没法等（一小时才有一次窗口，而且窗口里快路径会掩盖它）。

做法：在**本进程内**把 `RENEW_AHEAD_SEC` 改成负数 —— 快路径的判据
（`expires_at - now > AHEAD`）立刻为假，于是真的走到第 3 步（调只读接口比对）。
token 没变化 ⇒ 走 `SKIP` 分支 ⇒ 返回 0 ⇒ `main()` 应当写心跳。
再顺手验一次 `backoff` 心跳，最后跑一遍真续期把状态复位成 `ok`。

**不改任何生产文件、不写 token**：只往 renew_token.log 追一两行 SKIP，
以及把 `session_state.json` 刷新几次（那正是循环本来要做的事）。

用法（容器内）：
    python /app/verify_renew_heartbeat.py
"""
import json
import sys
import time
from pathlib import Path

APP = Path("/app")
STATE = APP / "data" / "session_state.json"


def _age():
    if not STATE.exists():
        return None, {}
    d = json.loads(STATE.read_text(encoding="utf-8"))
    return (time.time() - float(d.get("at") or 0)) / 60.0, d


def _show(tag):
    age, d = _age()
    print("  [{}] 心跳 = {:.2f} 分钟前  {}".format(
        tag, age if age is not None else -1, d))
    return age, d


def main():
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    import renew_token as R

    print("=" * 66)
    print("P12-AR 验收：真续期路径写不写心跳")
    print("=" * 66)

    # 1) 真实 SKIP 分支
    print("\n① 逼真实的「该续期了」分支（只读接口比对 → 预计 SKIP）：")
    # ⚠️ 符号别写反：快路径的判据是 `expires_at - now > RENEW_AHEAD_SEC`，
    # 把阈值调到**很大**才会让它为假、从而走到真续期分支。
    # （第一版写成负数，等于"永远新鲜"，于是什么都没验到 —— 探针要能发现自己没生效。）
    R.RENEW_AHEAD_SEC = 10 ** 9
    logp = R.ROOT / "renew_token.log"
    n_before = len(logp.read_text(encoding="utf-8").splitlines()) if logp.exists() else 0
    rc = R.main()
    new_lines = []
    if logp.exists():
        new_lines = logp.read_text(encoding="utf-8").splitlines()[n_before:]
    print("  main() 返回 = {}".format(rc))
    print("  新增日志     = {}".format(new_lines or "（无！说明没走到真续期分支）"))
    age, d = _show("SKIP 之后")
    ok1 = (rc == 0 and age is not None and age < 1 and d.get("probe") == "ok"
           and any("SKIP" in x or "OK" in x for x in new_lines))
    print("  → {}：真续期路径**写了**心跳".format("✅" if ok1 else "❌ 没写！"))

    # 2) 撞验证码退避时的心跳（不能算"会话失效"）
    print("\n② 撞验证码退避分支（心跳要写、但 probe=backoff、不能算死）：")
    R._browser_backoff_ok = lambda: False       # 阈值仍是 10**9，才走得到这一步
    rc2 = R._renew_locked()
    age2, d2 = _show("退避之后")
    ok2 = (rc2 == 1 and age2 is not None and age2 < 1
           and d2.get("probe") == "backoff" and d2.get("alive") is False)
    print("  → {}：退避也留心跳，且 probe=backoff（不是 unauth）".format(
        "✅" if ok2 else "❌"))

    # 3) 复位：跑一遍真续期，让心跳回到 ok
    print("\n③ 复位（跑真的 renew_token.py，快路径会写 ok）：")
    import importlib
    importlib.reload(R)
    rc3 = R.main()
    age3, d3 = _show("复位之后")
    ok3 = (rc3 == 0 and d3.get("probe") == "ok")
    print("  → {}：状态已复位".format("✅" if ok3 else "❌"))

    print("\n" + "=" * 66)
    allok = ok1 and ok2 and ok3
    print("结论：{}".format(
        "✅ 真续期路径与退避路径都会刷新心跳，且 backoff 不会被当成会话失效"
        if allok else "❌ 有不符合的项（见上）"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
