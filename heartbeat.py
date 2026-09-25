#!/usr/bin/env python3
"""每 2 小时心跳：报告 bot 完整运行状态到飞书（复用 check_bot_health 的巡检项目）。

定位：飞书目前只有「异常告警」，用户希望定期收到完整运行状态确认。
本脚本由 entrypoint 每 7200 秒触发一次（首次启动立即发一条）。

内容：复用 check_bot_health.py 的 6 项巡检函数（进程/CPU内存/token/续期/网络/Socket.IO），
生成一条汇总心跳消息。异常项会标 ❌/⚠️ 并提示关注巡检告警。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    sys.path.insert(0, str(ROOT))
    import check_bot_health as ch

    # 1. 进程
    count, pids = ch.check_process()
    proc_ok = count == 1
    pid = pids[0] if pids else None

    # 2. CPU / 内存（需进程存活）
    cpu = rss = None
    if pid:
        cpu, rss = ch.check_cpu_mem(pid)

    # 3. token
    tk = ch.check_token()
    access_remain = tk.get("access_remain_sec")
    refresh_days = tk.get("refresh_remain_days")

    # 4. 续期脚本（近 15 分钟是否有 FAIL）
    renew_fail = ch.check_renew()

    # 5. 网络 / 验证码
    net = ch.check_network()

    # 6. Socket.IO（近 5 分钟是否有断线）
    sio_fail = ch.check_sio()

    # 组装消息
    now = time.strftime("%H:%M")
    all_ok = proc_ok and renew_fail is False and net == "ok" and not sio_fail \
        and (access_remain is None or access_remain > 0)
    head = "🟢" if all_ok else "🔴"
    lines = [f"{head}【小小糯bot 心跳】{now}"]

    # 进程
    if proc_ok:
        lines.append("• 进程: ✅ 单实例")
    elif count == 0:
        lines.append("• 进程: ❌ 未运行")
    else:
        lines.append(f"• 进程: ⚠️ {count} 个实例")

    # CPU / 内存
    if cpu is not None and rss is not None:
        cpu_flag = "⚠️" if cpu > ch.CPU_HIGH_THRESHOLD else "✅"
        mem_flag = "⚠️" if rss > ch.MEM_THRESHOLD_KB else "✅"
        lines.append(f"• CPU{cpu_flag} {cpu:.0f}% | 内存{mem_flag} {rss/1024:.0f}MB")

    # token
    if access_remain is not None:
        if access_remain > 0:
            lines.append(f"• token: ✅ 剩余 {access_remain/60:.0f} 分钟")
        else:
            lines.append(f"• token: ❌ 已过期 {(-access_remain)/60:.0f} 分钟")
    if refresh_days is not None and refresh_days < ch.REFRESH_WARN_DAYS:
        lines.append(f"• refresh_token: ⚠️ 剩 {refresh_days:.0f} 天")

    # 续期
    lines.append("• 续期: " + ("❌ 近15分钟有失败" if renew_fail else "✅ 正常"))

    # 网络
    net_map = {"ok": "✅ 正常", "captcha": "⚠️ 撞验证码", "down": "❌ 不通"}
    lines.append("• 网络: " + net_map.get(net, "?"))

    # Socket.IO
    lines.append("• Socket.IO: " + ("❌ 近5分钟断线" if sio_fail else "✅ 已连接"))

    if not all_ok:
        lines.append("⚠️ 有异常项，巡检告警应已推送，请关注")

    msg = "\n".join(lines)

    # 发送（走飞书主通道）
    try:
        from wx_alert import send_weixin
        ok = send_weixin(msg)
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 心跳发送: {'✅' if ok else '❌'}")
        return 0 if ok else 1
    except Exception as e:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 心跳异常: {str(e)[:80]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
