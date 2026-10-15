"""主循环与启动调度（P3-19 从 bot.py 整体搬迁，循环体逐字未动）。

main_loop：每轮 = 读消息 → 指令/@/房间三分支 → 记账 → 全局块（热加载、
验证码恢复、档案扫描、定时问候、骰子超时——只在 is_primary 实例）。
P12-I 按群并行：groups 只放一个群、每个实例跑同一份代码；跨群共享的
sender/state/subscriber 由 run_all 建好传进来，**只建一份**。

**bot 模块桥**：体里所有 bot 侧名字（壳、常量、注册表）经 `B.` 访问 ——
B 是 kw-only 注入的 bot 模块对象，**运行期属性查找**，所以 monkeypatch
`bot.X` 的全部既有接缝与搬迁前语义一致（conftest.state、test_* 的 patch）。
局部状态（_last_scan_day/_was_sio_connected/http_backoff_until 等）仍是
普通局部变量，跨轮存活于各自的协程帧里。
"""
import asyncio
import os
import subprocess
import time
from datetime import datetime  # ⚠️ 必须是类：bot 命名空间 `from datetime import datetime`，
                                # 体内 datetime.now() 走直连（不在 B. 桥上）——
                                # 顶部 import datetime（模块）曾让线上运势每发一条
                                # 就 "module 'datetime' has no attribute 'now'"
                                # 并回滚 last_id（2026-10-01 部署事故二）。

from runtime.obs import log
from runtime import dream as _dream
from runtime import session_obs as _session_obs
from runtime import chat_archive as _chat_archive
from runtime import rose_moderation as _rose_moderation


__all__ = ["run_main_loop", "run_all"]


# ---- 会话失效（unauthorized）的统一判定 + "自动恢复"拉起 -------------------
#
# ⚠️ P0-2（2026-10-01 21:29 掉线 15 分钟实锤）：会话真死时**第一个**症状是
#   "读不到消息"，而读消息失败原先是个裸 except —— 只打一行日志就 `msgs = []`，
#   既不判 unauthorized、也不累加 `_unauth_streak`、更不拉起 `auto_recover.py`。
#   而 auto_recover 只在**生成/发送**异常路径可达，那条路径需要"先成功读到一条
#   消息"才会执行 ⇒ 会话一死就永远不可达，"两条恢复链"实际只剩 renew_token
#   那条（而它当时正被 P0 自锁卡死）。
#   证据（bot.log，含掉线窗口）：`平台读消息失败`=16 / `UNAUTHORIZED`=0 /
#   `触发自动登录恢复`=0；对照正常时期 bot.log.1：865 / 86 / 81。

# 读被拒后的重试间隔。**刻意不是生成路径那个 1800 秒**：
#   · 读很便宜，2 分钟一次谈不上"刷风控"（正常轮询是 0.8 秒一次）；
#   · 更要紧的是 `captcha_cooldown_until` 只会被时间清掉、**没有"恢复即清"的
#     机制**，套 1800 秒会让 bot 在 auto_recover 修好之后继续瞎半小时。
READ_UNAUTH_COOLDOWN_SEC = 120.0

# 定时从浏览器抄回 token 的间隔（秒）。见主循环 `is_primary` 块里的长注释
# （P12-BK 的缺口：抄回原先只在发送路径被调，群一安静就永远不抄）。
TOKEN_HARVEST_INTERVAL = 600.0

# auto_recover 子进程的**进程级**去重窗口。按群并行（P12-I）下主循环有 4 个实例，
# 各自持有自己的 `_recovering`；而会话一死是 4 个群**同时**读失败 ⇒ 只用实例级
# 标志会让 4 个实例各拉起一个 auto_recover，而 `auto_recover._recover_locked()`
# 结尾**无条件 `restart_bot()`** ⇒ 变成连续重启 bot 4 次。
AUTO_RECOVER_MIN_GAP = 300.0
_auto_recover_last_spawn = 0.0
_auto_recover_trace = ""


def _is_unauth_error(err) -> bool:
    """平台"拒绝了我们"的判定 —— 读消息失败与生成失败**必须同一套口径**。

    与 `renew_token.UNAUTH_MARKS` 同源（多认一个中文"未授权"形态）。
    """
    s = str(err)
    low = s.lower()
    return ("unauthorized" in low or "invalid authentication" in low
            or "未授权" in s)


def _spawn_auto_recover(B, reason: str, *, trace="", cid="", source="") -> bool:
    """拉起 `auto_recover.py`（进程级去重）。返回 True = 这次真的起了进程。"""
    global _auto_recover_last_spawn, _auto_recover_trace
    trace = trace or _session_obs.new_trace()
    now = time.time()
    if now - _auto_recover_last_spawn < AUTO_RECOVER_MIN_GAP:
        _session_obs.event("recovery_spawn_skipped", trace=trace, cid=cid, source=source,
                           result="debounced", parent_trace=_auto_recover_trace)
        log("🔄 " + reason + "（本进程刚拉过 auto_recover，跳过重复拉起）")
        return False
    try:
        process = subprocess.Popen(
            [B.HERMES_PY, "auto_recover.py"],
            cwd=str(B.ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_session_obs.recovery_env(trace),
        )
    except Exception as _e:
        _session_obs.event("recovery_spawn_failed", trace=trace, cid=cid,
                           source=source, error_type=type(_e).__name__)
        log("🔄 自动恢复触发失败: " + str(_e)[:60])
        return False
    _auto_recover_last_spawn = now
    _auto_recover_trace = trace
    _session_obs.event("recovery_spawned", trace=trace, cid=cid, source=source,
                       child_pid=getattr(process, "pid", None))
    log("🔄 " + reason)
    return True


async def run_main_loop(interval: float, dry: bool, once: bool, *,
                    groups: list = None, sender, state: dict,
                    subscriber, is_primary: bool = True, B):
    """主循环。

    P12-I（按群并行）：为了不改动这 900 行循环体，改成"**每个群一个实例**" ——
    `groups` 只放一个群，每个实例跑同一份代码、各管自己的群。
    跨群共享的东西一律由调用方（`_run_all`）建好传进来，**只建一份**：
      · `sender`      —— 共用一个常驻页面（1.8G 主机跑不了两个 Playwright 机器人）
      · `state`       —— 共用同一份 dict（各自 load 会在 `save_state` 时互相覆盖）
      · `subscriber`  —— 共用一个 Socket.IO 订阅器（按群分队列，drain 各自取）
    `is_primary` 的实例额外负责**全局**工作（热加载、验证码恢复、每日档案扫描、
    定时问候、骰子超时、私聊）—— 这些是进程级/跨群的，不能被跑 N 遍。
    """
    groups = list(groups or B.GROUPS)
    log("start  dry=" + str(dry) + " interval=" + str(interval)
        + " groups=" + str(len(groups)) + ("（从实例）" if not is_primary else ""))
    _session_obs.event("group_loop_started", cid=groups[0] if len(groups) == 1 else "")

    captcha_cooldown_until = 0.0  # captcha 冷却截止时间戳
    http_backoff_until = 0.0  # 撞验证码后 HTTP 兜底退避截止时间戳（收发不受影响，继续）
    captcha_streak = 0  # 连续 captcha 次数（指数退避）
    _alerted_unauthorized = False  # unauthorized 告警防重（cookie 恢复后重置）
    _unauth_streak = 0  # 连续 unauthorized 次数（第 1 次即触发自动恢复）
    _recovering = False  # 是否正在自动恢复中
    group_meta = {cid: {"interval": B.FAST_INTERVAL, "last_poll": 0.0} for cid in groups}

    async def _on_unauthorized(where: str) -> None:
        """统一处理"平台拒绝了我们"：计数 → 告警（防重）→ 拉起自动恢复。

        读消息路径与生成路径**共用这一份**，免得两处口径再次漂移。
        """
        nonlocal _unauth_streak, _alerted_unauthorized, _recovering
        _unauth_streak += 1
        trace = _session_obs.new_trace()
        source = "read" if where.startswith("读消息被拒") else "generation"
        from runtime.ctx import get_active_cid
        observation_cid = get_active_cid()
        _session_obs.event("unauthorized", trace=trace, cid=observation_cid, source=source)
        log("⚠️ UNAUTHORIZED（" + where + "）（连续第 " + str(_unauth_streak) + " 次）")
        # 事件驱动告警：cookie 失效发微信（防重，cookie 恢复后重置）
        if not _alerted_unauthorized and B._feat_on("", "alert_cookie"):
            _alerted_unauthorized = True
            try:
                ok = await asyncio.to_thread(
                    B.send_weixin,
                    "🚨【小小糯bot】DZMM cookie 失效（unauthorized），已自动触发恢复，无需手动操作")
                log("📱 unauthorized 告警: " + ("✅ 已发微信" if ok else "❌ 发送失败（代理可能不稳）"))
            except Exception as _e:
                log("📱 unauthorized 告警异常: " + str(_e)[:60])
        # 第 1 次 unauthorized 就触发自动登录恢复（headless 兜底锁屏也能成功，不必等 2 次）
        if not _recovering and B._feat_on("", "auto_recover"):
            _recovering = True
            _spawn_auto_recover(
                B, "连续 " + str(_unauth_streak) + " 次 unauthorized，触发自动登录恢复",
                trace=trace, cid=observation_cid, source=source)

    HTTP_FALLBACK_INTERVAL = 600  # HTTP 兜底对账间隔（秒）；断线补拉不受此限
    # P12-J：socket 抖动时的"补拉"最少间隔 —— 抖动一次就补拉一次会把平台读打成风暴
    HTTP_RESYNC_MIN_GAP = 60.0
    last_http_fallback = {cid: 0.0 for cid in groups}
    last_read_attempt = {cid: 0.0 for cid in groups}
    last_read_skip_log = {cid: 0.0 for cid in groups}
    # P12-AA：每个群上次"批量刷新成员昵称缓存"的时间（在工作线程里做，防事件循环发 HTTP）
    _names_loaded_at = {cid: 0.0 for cid in groups}
    # P12-AB：上次打印出站队列状态的时间（每 30 分钟一行，失败了要看得见）
    _outbox_status_at = [0.0]
    # 2026-10-02：上次"定时从浏览器抄回 token"的时间。见下面循环里那段注释。
    _harvest_at = [0.0]
    _was_sio_connected = False  # 上一次 Socket.IO 连接状态（恢复检测用边沿：断→连）
    _last_scan_day = str(B._profile_sync.scan_load().get("day") or "")  # 上次档案扫描日期（每天 06:00 一次）

    while True:
        # 本轮开始先清空人设：分组循环之外的所有 AI 调用都必须用默认人设。
        # 必须清！否则 contextvars 会残留上一轮最后一个群的人设（GROUPS 最后一个是 Rose 房间群），
        # 广播文案（问候/记忆汇报）就会被写成房间管家口吻。
        # —— 2026-09-17 事故：研讨群的早安变成了"（推开玫瑰房的窗…）Vv姐姐、糯糯姐，早上好"
        B.set_persona_cid("")
        # P12-F：热加载检查。便宜的那一段（一次 stat）每轮都做，
        # 只有真改了文件才会做完整解析 + 应用（见 runtime/hotload.py）。
        # P12-I：**只有主实例**做 —— 它是全局的（改人设/参数/开关），
        # 两个实例同时应用会互相踩。
        if is_primary:
            await B.hot_reload_check()
            # P12-AB：每 30 分钟把出站队列状态写一行（失败消息不能只有当时那一个 ❌）
            if time.time() - _outbox_status_at[0] >= 1800:
                _outbox_status_at[0] = time.time()
                B._log_outbox_status(sender)
            # ⚠️ 2026-10-02（P12-BK 的缺口补完）：**定时**从浏览器抄回 token，
            # 不再只在"发消息时"抄。
            #
            # 为什么必须定时：平台的 refresh_token 是**轮换制**，常驻页面的 SPA 自己也会刷，
            # 刷完把新的留在浏览器里、我们 JSON 那份就成了旧的。`_harvest_token()` 原先
            # **只在发送路径**被调（`sender.py` 的 `_sync_cookies`）——
            # 群一安静/深夜没人说话，就不发送 ⇒ 不抄回 ⇒ JSON 越来越旧
            # ⇒ 续期 401 ⇒ 浏览器也刷不动（它手里的 refresh_token 同样过期）⇒ 判"会话真死"。
            #
            # 实测代价（2026-10-01）：01:32~08:00 之间 **7 次 EMERGENCY，几乎每小时一次**，
            # 其中一次共坏 **529 分钟**（8.8 小时）；浏览器续期 FAIL 24 次 / 成功 1 次；
            # 抄回**只发生过 1 次**。
            #
            # 10 分钟是权衡：token 寿命约 1 小时，10 分钟足够把"旧"控制在可续期范围内；
            # 而 `_harvest_token()` 内部还有 60 秒节流、且抢锁是 `timeout=0`（拿不到就跳过），
            # 所以这一句不会阻塞主循环、也不会和续期/重登抢。
            if time.time() - _harvest_at[0] >= TOKEN_HARVEST_INTERVAL:
                _harvest_at[0] = time.time()
                try:
                    await sender._harvest_token()
                except Exception as _he:                           # noqa: BLE001
                    log("  token 定时抄回异常（不影响收发）: " + str(_he)[:60])
        # 检测 renew_token 连续失败信号，主动退出交给 entrypoint 重启
        if (B.ROOT / "token.emergency").exists():
            try:
                (B.ROOT / "token.emergency").unlink()
            except Exception:
                pass
            log("⚠️ 检测到 token.emergency 信号，主动退出交给 entrypoint 重启")
            return
        # 撞验证码恢复检测：Socket.IO 从「断→连」的边沿 = 风控解除，触发恢复
        # （用边沿而非单次检查，避免 Socket.IO 短暂波动导致时序竞态漏掉恢复）
        is_conn = subscriber.is_connected
        if is_primary and B._ctx.captcha_active() and is_conn and not _was_sio_connected:
            B._ctx.set_captcha_active(False)
            B._captcha_notified.clear()
            for _cid in B.GROUPS:
                if _cid in B.MUTE_CIDS:
                    continue
                if B._feat_on(_cid, "screen_rescreen"):
                    await B._send(sender, _cid, "🤖 " + (
                        B.p("captcha_recovered", _cid)
                        or B._personas.fill(B.CAPTCHA_RECOVERED_NOTICE, _cid)), dry)
                    await B._rescreen_pending(sender, _cid, dry)
            log("✅ 风控解除（Socket.IO 已重连），已群内提醒 + 补筛查")
        _was_sio_connected = is_conn
        # 每天 06:00 后首次循环：全群扫描昵称/简介/签名变化，有变化就在群里说一声
        _today = time.strftime("%Y-%m-%d")
        # P12-I：下面四块是**跨群/进程级**的（全群档案扫描、全群定时问候、
        # 全局骰子局超时、私聊队列），只有主实例做 —— 否则会被跑 N 遍。
        if is_primary and time.localtime().tm_hour >= B._tune("schedule.scan_hour") \
                and _last_scan_day != _today:
            _last_scan_day = _today  # 先占位，避免扫描期间循环重入
            try:
                _notices = await asyncio.to_thread(B._daily_profile_scan)
                for _ncid, _ntxt in _notices:
                    # 「档案自动构建」与「改名播报」是两个开关：扫描仍在做，
                    # 只是可以选择"不发群消息"（数据照样更新）
                    if B._feat_on(_ncid, "notice_rename"):
                        await B._send(sender, _ncid, _ntxt, dry)
            except Exception as _e:
                log("档案扫描异常: " + str(_e)[:60])
        # 定时问候 / 记忆汇报：用主 sender（页面常驻、秒发）发出，不再靠独立进程的冷浏览器
        # （2026-09-16 事故：22:00 的晚安因独立进程冷启浏览器 + 平台抖动，整页加载超时全失败，
        #   认证群整晚没收到问候）
        if is_primary:
            try:
                await B._scheduled_pushes(sender, dry)
            except Exception as _e:
                log("定时推送异常: " + str(_e)[:80])
        # 吹牛骰子超时跳过（3 分钟无动作 → 移到顺序最后，游戏继续）
        _dg = B._dice_state()
        _dice_cid = str(_dg.get("cid") or "")
        if is_primary and (_dg.get("active") and _dg.get("phase") == "calling"
                           and B._feat_on(_dice_cid, "dice_timeout")
                           and time.time() - _dg.get("last_action_ts", 0) > B._tune("dice.timeout", _dice_cid)):
            _dcur = B._dice_current_player(_dg)
            if _dcur:
                _dmsg = B._dice_skip_cmd(_dcur)
                _dcid = _dg.get("cid") or groups[0]  # 广播只发发起群
                if _dmsg:
                    await B._send(sender, _dcid, "🤖 " + _dmsg, dry)
                    log("⏱️ 骰子局超时跳过：" + B.get_name(_dcur))
        # 处理私聊消息（吹牛骰子 /看骰）
        try:
            _pmsgs = subscriber.drain_private()
            for _pcid, _plist in _pmsgs.items():
                for _m in _plist:
                    if B.is_self(_m.get("sent_by")):
                        continue
                    _ptext = ((_m.get("content") or {}).get("text") or "").strip()
                    if _ptext == "/看骰":
                        _puid = _m.get("sent_by", "")
                        _pg = B._dice_state()
                        if _pg.get("active") and _puid in _pg.get("dice", {}):
                            await B._dice_send_private(sender, _puid, "你的骰子：" + B.dice_str(_pg["dice"][_puid]))
                        else:
                            await B._dice_send_private(sender, _puid, "你现在没有进行中的骰子局哦～发 /吹牛骰子 发起～")
        except Exception as _pe:
            log("私聊消息处理异常: " + str(_pe)[:80])
        captcha_hit = False
        for cid in groups:
            B._set_active_cid(cid)  # 同时更新 bot.py 镜像与 runtime/ctx（搬出去的模块读它）
            B.set_persona_cid(cid)  # 分群人设：本轮所有 AI 调用都按这个群的人设走
            meta = group_meta[cid]
            # 该群没到轮询时间，跳过（每群独立退避）
            if time.time() - meta["last_poll"] < meta["interval"]:
                continue
            meta["last_poll"] = time.time()
            had_new = False  # 本轮该群有没有新的用户消息
            old_last_id = None  # 本轮指令/@ 消息处理前的 last_id（生成失败回滚用）
            _t_round = None  # P12-I 延迟自证：本轮开始计时（拿到消息才算）
            _lag = -1.0      # 最新群友消息从平台发出到现在的秒数
            try:
                # 读消息：Socket.IO 推送优先，HTTP 低频兜底（对账补漏）
                sio_msgs = subscriber.drain(cid)
                need_resync = subscriber.need_resync(cid)
                now_ts = time.time()
                from_sio = False
                if sio_msgs:
                    msgs = sio_msgs
                    from_sio = True
                elif (need_resync or (now_ts - last_http_fallback.get(cid, 0) >= HTTP_FALLBACK_INTERVAL)) and now_ts >= http_backoff_until:
                    # P12-J：socket 抖动时它会不停置 resync 标记，而每次补拉都是一次平台
                    # 调用 —— 实测形成"每 30 秒一次读、每次 8~10 秒"的风暴（既拖慢自己，
                    # 也在给平台加压）。两道闸：
                    #   ① 冷却期内**不打接口**（我们自己的限流器在冷却时会 sleep 到超时，
                    #      打了也只是白等 + 继续加压）；
                    #   ② 同一个群的补拉最少隔 HTTP_RESYNC_MIN_GAP 秒。
                    _cd = 0.0
                    try:
                        from throttle_guard import cooldown_remaining as _cdrem
                        _cd = float(_cdrem())
                    except Exception:                          # noqa: BLE001
                        _cd = 0.0
                    _since_read = now_ts - last_read_attempt.get(cid, 0.0)
                    if _cd > 0:
                        if now_ts - last_read_skip_log.get(cid, 0.0) > 120:
                            last_read_skip_log[cid] = now_ts
                            log("⏳ [{}] 限流冷却中（剩 {:.0f}s），本轮不读消息".format(
                                cid[:8], _cd))
                        msgs = []
                    elif need_resync and _since_read < HTTP_RESYNC_MIN_GAP:
                        msgs = []          # 刚读过就抖动：别一路补拉，下轮再说
                    else:
                        last_read_attempt[cid] = now_ts
                        last_http_fallback[cid] = now_ts
                        if need_resync:
                            subscriber.clear_resync(cid)
                        # ⚠️ 超时**交给 requests 自己**（timeout= 传进去），
                        # 不要只靠外面 wait_for 兜：那只是"我们不等了"，线程还在跑，
                        # 池子（2 核机器上只有 5 个 worker）一满，之后所有 to_thread
                        # 一起排队 —— 2026-09-27 就是这么静默了 11 分钟的。
                        try:
                            msgs = await asyncio.wait_for(
                                asyncio.to_thread(B.get_messages, cid, 20, None, None,
                                                  B.PLATFORM_READ_TIMEOUT),
                                timeout=B.PLATFORM_READ_TIMEOUT + 10)
                        except asyncio.TimeoutError:
                            log("⏱️ [{}] 读消息超过 {:.0f}s，本轮不等了（下轮再试）".format(
                                cid[:8], B.PLATFORM_READ_TIMEOUT))
                            msgs = []
                        except Exception as _re:
                            log("⚠️ [{}] 平台读消息失败：{}".format(cid[:8], str(_re)[:70]))
                            msgs = []
                            if _is_unauth_error(_re):
                                # 会话真死的第一症状就是"读不到消息"（见文件上方 P0-2）。
                                # 设**短**冷却有两个作用：① 别再用 0.8 秒的间隔去撞一个
                                # 已经在拒绝我们的平台；② 本轮末尾的"恢复正常"分支会重置
                                # `_recovering`，不设冷却就会当轮被清零 ⇒ 下轮重复拉起。
                                captcha_hit = True
                                captcha_cooldown_until = time.time() + READ_UNAUTH_COOLDOWN_SEC
                                await _on_unauthorized("读消息被拒 " + cid[:8])
                    # HTTP 兜底成功 = 风控解除：清撞验证码状态 + 所有群提醒 + 补筛查延迟的新成员
                    if msgs and B._ctx.captcha_active():
                        B._ctx.set_captcha_active(False)
                        B._captcha_notified.clear()
                        for _cid in B.GROUPS:
                            if _cid in B.MUTE_CIDS:
                                continue
                            await B._send(sender, _cid, "🤖 " + (
                                B.p("captcha_recovered", _cid)
                                or B._personas.fill(B.CAPTCHA_RECOVERED_NOTICE, _cid)), dry)
                            await B._rescreen_pending(sender, _cid, dry)
                        log("✅ 风控解除，已群内提醒 + 补筛查")
                else:
                    msgs = []
                if msgs:
                    await _chat_archive.capture(cid, msgs, source="live_push" if from_sio else "live_http")
                    # P12-I 延迟自证：起点 + "消息在路上等了多久"
                    _t_round = time.time()
                    _lag = B.message_lag(msgs)
                    # P12-AD：把这批**所有**消息记进本群滚动上下文（含我们自己发的，
                    # 批处理后面的 `is_self → continue` 只是不处理指令，不代表不用记）。
                    for _m in msgs:
                        _mtxt = ((_m.get("content") or {}).get("text") or "").strip()
                        if not _mtxt:
                            continue
                        _muid = _m.get("sent_by", "")
                        B._remember_turn(
                            cid, "我" if B.is_self(_muid) else B.get_name(_muid),
                            _mtxt, _m.get("message_id"), _muid)
                    group_state = state.get(cid) or {}
                    last_id = group_state.get("last_id")
                    # 定位 pending：Socket.IO 推送直接去重，HTTP 兜底用 last_id 定位
                    # ⚠️ `_requeued` 的消息必须放行：它是"上一轮没轮到、被放回队列"的，
                    # 而它往往正好是上一批的批尾（== last_id），不放行就又丢了。
                    if from_sio:
                        pending = [m for m in msgs
                                   if m.get("message_id") != last_id
                                   or m.get("_requeued")]
                    else:
                        if last_id:
                            idx = -1
                            for i, m in enumerate(msgs):
                                if m.get("message_id") == last_id:
                                    idx = i
                                    break
                            if idx >= 0:
                                pending = msgs[idx + 1:]
                            else:
                                # last_id 已滚出窗口，只处理最新一条
                                pending = [msgs[-1]]
                        else:
                            # 首次启动，只处理最新一条，避免扫历史
                            pending = [msgs[-1]]
                    if not pending:
                        continue

                    # P12-AA：每 10 分钟在**工作线程**里批量刷新本群成员昵称缓存。
                    # 这样 `get_full_name` 在事件循环上永远不需要发 HTTP
                    # （缓存未命中时它只退化取档案名/uid 前缀并把 uid 记进待补集合）。
                    if now_ts - _names_loaded_at.get(cid, 0.0) >= 600:
                        _names_loaded_at[cid] = now_ts
                        try:
                            await asyncio.to_thread(B._load_member_names, cid)
                        except Exception:                          # noqa: BLE001
                            pass

                    # 静默监控群：只推进游标、不做任何处理（读得到但一声不吭）
                    if cid in B.MUTE_CIDS:
                        group_state["last_id"] = pending[-1]["message_id"]
                        state[cid] = group_state
                        B._save_group(cid, group_state)
                        continue

                    # P12-H：新 Bot 的 uid 可能还不知道（配置里没写 / 平台没给）。
                    # 用"成员名 == 配置里的 Bot 名"把它认出来，之后 is_self() 就准了。
                    # ⚠️ 必须在下面**所有** is_self(...) 之前跑：否则新 Bot 在群里
                    # 发的第一条消息会被当成群友（可能自问自答）。
                    # 只查昵称缓存，不发请求，所以放进每轮循环是安全的。
                    for _m in pending:
                        if B.learn_bot_uid(_m.get("sent_by")):
                            log("P11: 认出一个 Bot 身份（按名字）：{} -> {}".format(
                                (_m.get("sent_by") or "")[:8],
                                ((_m.get("content") or {}).get("text") or "")[:20]))

                    # 本轮是否有真正的用户新消息（非 agent）
                    had_new = any(not B.is_self(m.get("sent_by")) for m in pending)

                    # 发言统计（活跃度 + 待评定发情缓冲 + 最近发言时间，每群独立持久化）
                    counts = group_state.get("msg_count") or {}
                    pending_buf = group_state.get("heat_pending") or {}
                    last_speak = group_state.get("last_speak") or {}
                    prev_speak = group_state.get("prev_speak") or {}  # 「上一条发言」时间（回顾功能的起点）
                    _now = time.time()
                    for m in pending:
                        uid = m.get("sent_by", "")
                        text = (m.get("content") or {}).get("text", "").strip()
                        if uid and not B.is_self(uid) and text and "加入了群聊" not in text and not text.startswith("/"):
                            if last_speak.get(uid):  # 先把「现在这条之前」的发言时间存下来
                                prev_speak[uid] = last_speak[uid]
                            counts[uid] = counts.get(uid, 0) + 1
                            last_speak[uid] = _now
                            if cid not in B.ROOM_CIDS:  # 房间群不攒发情值
                                buf = pending_buf.get(uid, [])
                                buf.append(text)
                                if len(buf) > B._tune("heat.pending_max", cid):
                                    buf = buf[-10:]
                                pending_buf[uid] = buf
                    group_state["msg_count"] = counts
                    group_state["heat_pending"] = pending_buf
                    group_state["last_speak"] = last_speak
                    group_state["prev_speak"] = prev_speak
                    if had_new:
                        # 记录最后一条用户消息时间，立即落盘避免末尾 load_state 覆盖
                        group_state["last_msg_time"] = time.time()
                        state[cid] = group_state
                        B._save_group(cid, group_state)

                    # 检测新成员加入：认证群做筛查（踢游客/踢男/提醒未填性别），
                    # 其他群只发中性欢迎语，不踢人、不发认证群文案
                    # P3-3-1：分派/筛查/欢迎语全在 runtime/joins.py，这里只留故事态闸门
                    if not B._get_story_state(cid).get("active"):
                        joins = B._detect_joins(pending)
                        if joins:
                            await B._joins.handle_join_event(
                                sender, cid, joins, dry, group_state,
                                screen_cids=B.SCREEN_CIDS, room_cids=B.ROOM_CIDS,
                                screen_fn=B._handle_joins, welcome_of=B._welcome_of)
                            # ⚠️ 2026-10-02：入群事件**也要写成员快照**。快照原先只有
                            # `reconcile_members` 会写，于是正常入群的人在下一次容器重启后
                            # 仍算"没见过"，被对账再处理一遍（实测 08:43:14 已欢迎的人
                            # 08:46:14 又被捡起来）。与对账同口径，只增不减。
                            B._roster.remember_members(cid, joins)

                    # 检查 pending 里有没有 / 指令或 @ 消息
                    # ⚠️ 这里**不能**见到第一条命令就 break：`drain()` 已经把整个队列
                    # 取空了，同一批里剩下的命令/@ 消息如果不放回队列就会被静默丢掉
                    # （2026-09-27 现场：紫苑的 /运势 和一条 /写诗 同批到达，从此消失）。
                    cmd = ""
                    cmd_sender = ""
                    at_msg = ""
                    at_sender = ""
                    _via_quote = False   # P12-AE：这条是"右键回复我"而不是 @ 我
                    room_hit = None  # (uid, 原文) 需要管家回应的房间消息
                    _cmd_msg = None
                    _at_msg_obj = None
                    _room_obj = None
                    _leftover = []   # 本轮没轮到、但必须留到下一轮的
                    for m in pending:
                        if B.is_self(m.get("sent_by")):
                            continue  # 跳过 bot 自己的消息，避免 self-echo
                        t = (m.get("content") or {}).get("text", "").strip()
                        if _rose_moderation.is_command(cid, t):
                            if not cmd:
                                cmd = t
                                cmd_sender = m.get("sent_by", "")
                                _cmd_msg = m
                            else:
                                _leftover.append(m)
                            continue
                        if _dream.is_hash_request(cid, t):
                            if B._is_other_bot_noise(cid, t):
                                continue
                            if not cmd:
                                cmd = t
                                cmd_sender = m.get("sent_by", "")
                                _cmd_msg = m
                            else:
                                _leftover.append(m)
                            continue
                        # P12-AW：**`#` 指令入口**（只在配了 `fortune.hash_cmd` 的群生效）。
                        # 必须放在下面 `at_only` 闸门**之前** —— 那段会把没 @ 她的消息
                        # 直接 continue 掉，裸 `#运势` 根本走不到指令分支。
                        # 白名单只放"运势族"，其他 `#xxx` 原样走原逻辑（别吞别家 bot 的语法）。
                        if t.startswith("#") and B._feat_on(cid, "fortune") \
                                and B._tune("fortune.hash_cmd", cid) \
                                and B.is_fortune_cmd(t):
                            if not cmd:
                                cmd = t
                                cmd_sender = m.get("sent_by", "")
                                _cmd_msg = m
                            else:
                                _leftover.append(m)
                            continue
                        # P12-CF：`#写诗` 入口（茶水间，poem.hash_cmd 按群开）。
                        # 与 fortune 的 `#` 入口同款：放在 at_only 闸门**之前**放行，
                        # 只认诗族前缀，其他 `#xxx` 原样走原逻辑（别吞别家 bot 的语法）。
                        if t.startswith("#") and B._feat_on(cid, "poem") \
                                and B._tune("poem.hash_cmd", cid) \
                                and B.is_poem_cmd(t):
                            if not cmd:
                                cmd = t
                                cmd_sender = m.get("sent_by", "")
                                _cmd_msg = m
                            else:
                                _leftover.append(m)
                            continue
                        # P12-AL：`at_only` 的群里，**只有 @ 到她的消息**才继续往下走
                        # （指令、房间触发、定时播报一并免掉）。顶层茶水间（779 人、
                        # 里面有别家 bot 的游戏）要的就是这个保证。
                        if B._feat_on(cid, "at_only"):
                            _by_at = B.is_at_me(t, cid)
                            # P12-BM B：at_only 群里"引用她"**天然算触发**（见 `_quote_reply_on`）
                            _by_quote = (not _by_at) and B._quote_reply_on(cid) \
                                and B._quotes_self(m)
                            # P12-CA（2026-10-13）：at_only 群开了查档/注册时，裸 `/查`
                            # `/注册`（不带 @）也要放行到下方斜杠识别 —— 否则指令在进
                            # dispatch 前就被本闸门静默吞掉（Mr.橘/瑞雯两群实测"/查没反应"）。
                            # 只白名单这两族：其他 /xxx（别家 bot 的游戏语法）照旧吞，
                            # 避免落到 dispatch 兜底的 AI 接话在群里刷屏。
                            # 前缀必须与 dispatch.is_profile_cmd/is_register_cmd 保持同步。
                            _by_roster_cmd = t.startswith(
                                ("/查", "/whois", "/资料", "/注册", "/register"))
                            if not (_by_at or _by_quote or _by_roster_cmd):
                                continue
                        if t.startswith("/"):
                            # P12-AK：这个群不接 `/` 指令（顶层茶水间那种群里跑着别家 bot 的
                            # 游戏，`/开始摸鱼躲藏`、`/记忆考核`、`/答案 xxx` 全是它的语法）。
                            # 不拦的话会走到兜底回一句"这个我不会" —— 779 人的群里就是刷屏。
                            # 关掉闸门 = 这些消息**完全不理**（只留 @ 问答那条路）。
                            if not B._feat_on(cid, "slash_cmds"):
                                continue
                            if not cmd:
                                cmd = t
                                cmd_sender = m.get("sent_by", "")
                                _cmd_msg = m
                            else:
                                _leftover.append(m)      # 同一批里的第二/三条命令
                            continue
                        # P12-AE：@ 我，或者（开关打开时）右键回复了我的某条消息
                        # P12-BM B：at_only 群里"引用她"天然算触发 ⇒ 这里也走同一个判据，
                        # 否则闸门放行了、这里却不认，等于静默不回。
                        _by_at = B.is_at_me(t, cid)
                        _by_quote = (not _by_at) and B._quote_reply_on(cid) \
                            and B._quotes_self(m)
                        if _by_at or _by_quote:
                            if not at_msg:
                                # ⚠️ 2026-10-02：**别家 bot 的游戏消息**也会 @ 到我们。
                                # 顶层茶水间实测 09:21:56：
                                #   👋 [4fc4a2fd] @消息: 【记忆考核·第 1 级】请记住：mTrOE
                                #   💬 🤖 记好啦～小小糯会记住的～♡
                                # 那句"请记住"命中了 `memory.MEMORY_TRIGGERS` 里的裸"记住"，
                                # 于是被当成 `/记住` 指令，mTrOE 进了记忆库。
                                #
                                # 这类噪音**早就有过滤表**（`recap.ignore_kw`，茶水间配了
                                # "记忆考核"），但它原先只保护 **recap 的输入** ——
                                # 触发层没有这道过滤。这里复用同一张表。
                                #
                                # ⚠️ 刻意走**与冷却同一条路**（只是不接话），**不用 `continue`**：
                                # 这条消息后面还有记账/上下文等处理，跳过整个循环体会是另一类
                                # 隐性行为变化。噪音"不被接话"就够了。
                                _noisy = B._is_other_bot_noise(cid, t)
                                # P12-BQ D1：同一个人连着 @ 她，冷却期内**这一条不接**
                                # （真人不会条条秒回；量过：她是 100% 有求必应）。
                                # ⚠️ 不静默丢：这里显式留一行日志；而且只影响"接不接话"，
                                # 消息照旧被消费、`last_id` 照旧往前走，不会反复重放。
                                _cool = B._chat_cooling(cid, m.get("sent_by", ""))
                                if _noisy:
                                    log("⏳ [{}] 别家 bot 的游戏噪音（命中 recap.ignore_kw）→ 这条不接"
                                        .format(cid[:8]))
                                elif _cool and B._feat_on(cid, "chat"):
                                    log("⏳ [{}] @ 冷却中（还剩 {}s，同一个人）→ 这条不接"
                                        .format(cid[:8], _cool))
                                else:
                                    at_msg = t
                                    at_sender = m.get("sent_by", "")
                                    _at_msg_obj = m
                                    _via_quote = bool(_by_quote)
                            else:
                                _leftover.append(m)
                        if B._is_room_trigger(cid, m.get("sent_by", ""), t):
                            if room_hit is None:
                                room_hit = (m.get("sent_by", ""), t)
                                _room_obj = m
                            else:
                                _leftover.append(m)
                    # `cmd` 优先于 @ / 房间分支：没轮到的那条放回去，**被处理的那条不放**
                    # （见 `_pick_leftover` 的注释：放回被处理的那条 = 每轮重来一次）
                    _leftover = B._pick_leftover(
                        cid, cmd, _cmd_msg, at_msg, _at_msg_obj, _room_obj, _leftover)
                    if _leftover:
                        try:
                            subscriber.requeue(cid, _leftover)
                            log("♻️ [{}] 同批还有 {} 条没轮到，已放回队列".format(
                                cid[:8], len(_leftover)))
                        except Exception as _e:                # noqa: BLE001
                            log("⚠️ [{}] 放回队列失败：{}".format(cid[:8], str(_e)[:60]))

                    # 房间群：客人发 / 指令 → 变成一次"婉拒"的房间回应（只有主人能下命令）。
                    # 例外：`/查` 一族对所有人开放（2026-09-27 屋主要求），照常走命令分支。
                    if not _rose_moderation.is_command(cid, cmd) and B._room_cmd_refused(cid, cmd, cmd_sender):
                        room_hit = (cmd_sender, cmd)
                        cmd = ""
                        at_msg = ""

                    if cmd:
                        # P3-3-2：斜杠指令分派链整体搬去 runtime/dispatch.py（逐字搬迁，
                        # handle_slash）。依赖里属于 bot 的部分按参数注入 —— 调用点读的
                        # 是当前全局，既有 monkeypatch bot.X 接缝不受影响。
                        # old_last_id 在这里先记下（原链首那行随链搬走时删除）：
                        # 生成失败由链内 raise RuntimeError 传到外层 except 回滚（P12-BC）。
                        old_last_id = group_state.get("last_id", "")  # 记录旧值，生成失败时回滚
                        await B._dispatch.handle_slash(
                            sender, cid, cmd, cmd_sender, dry, group_state, msgs,
                            _cmd_msg, state,
                            _save_group=B._save_group, _mark_inflight=B._mark_inflight,
                            _remember_turn=B._remember_turn,
                            rank_summary=B.rank_summary, sync_profiles=B.sync_profiles)
                    elif at_msg:
                        # P3-3-4：@ 闲聊全流程整体搬去 runtime/at_chat.py（逐字搬迁，
                        # handle_at_chat）。依赖里属于 bot 的部分按参数注入 —— 同
                        # handle_slash / handle_room 的做法。
                        # old_last_id 在这里先记下（原块内那行随块搬走时删除）：
                        # 生成失败由块内 raise RuntimeError 传到外层 except 回滚（P12-BC）。
                        old_last_id = group_state.get("last_id", "")  # 记录旧值，生成失败时回滚
                        await B._at_chat.handle_at_chat(
                            sender, cid, at_msg, at_sender, dry, group_state, state, msgs,
                            _at_msg_obj, _via_quote,
                            _save_group=B._save_group, _mark_inflight=B._mark_inflight,
                            _remember_turn=B._remember_turn,
                            _recent_turns=B._recent_turns, _seed_turns=B._seed_turns)

                    elif room_hit:
                        # P3-3-3：房间消息处理块整体搬去 runtime/room.py（逐字搬迁，handle_room）。
                        # 依赖里属于 bot 的部分按参数注入 —— 同 handle_slash 的做法。
                        # old_last_id 在这里先记下（原块首那行随块搬走时删除）：
                        # 生成失败由块内 raise RuntimeError 传到外层 except 回滚（P12-BC）。
                        old_last_id = group_state.get("last_id", "")  # 记录旧值，生成失败时回滚
                        await B._room_mod.handle_room(
                            sender, cid, room_hit, _room_obj, msgs, dry, group_state, state,
                            _save_group=B._save_group, _mark_inflight=B._mark_inflight,
                            _remember_turn=B._remember_turn)

                    # 恶魔吐槽（无人@无人指令，普通聊天自主毒舌）
                    if not cmd and not at_msg and not room_hit:
                        roast = await asyncio.to_thread(B._maybe_devil_roast, cid, msgs, group_state)
                        roast = (roast or "").strip()
                        if roast:
                            log("😈 [" + cid[:8] + "] 吐槽")
                            if dry:
                                log("[dry] " + roast[:50])
                            else:
                                ok = await sender.send("🤖 " + roast, cid)
                                log("✅" if ok else "❌")
                            # 立即落盘（devil 字段 + 本轮统计，避免末尾 load_state 覆盖）
                            state[cid] = group_state
                            B._save_group(cid, group_state)

                    # 调教状态流转（目标回应 / 冷场接管 / 超时结束）
                    # ⚠️ 必须 to_thread：里面会调模型（冷场自编命令 / 超时收尾小结），
                    # 直接在这儿跑会把**本群**这一轮堵住十几秒（同款事故 2026-09-14 堵过 37 分钟）。
                    if B._get_dj_state(cid).get("active"):
                        dj_reply = await asyncio.to_thread(B._check_diaojiao, cid, pending)
                        dj_reply = (dj_reply or "").strip()
                        if dj_reply:
                            log("🔗 [" + cid[:8] + "] 调教")
                            if dry:
                                log("[dry] " + dj_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + dj_reply, cid)
                                log("✅" if ok else "❌")

                    # 公演进行中的互动（丰富/羞辱/点评）
                    # ⚠️ to_thread：这一支会调模型；同步跑会堵住本群这一轮（同下）
                    if B._get_perf_state(cid).get("active") and B._feat_on(cid, "perf_enrich"):
                        interact_reply = await asyncio.to_thread(B._check_perf_interact, cid, msgs)
                        interact_reply = (interact_reply or "").strip()
                        if interact_reply:
                            log("🎭 [" + cid[:8] + "] 互动")
                            if dry:
                                log("[dry] " + interact_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + interact_reply, cid)
                                log("✅" if ok else "❌")

                    # 剧情游戏状态流转（提醒投票 / 推进剧情 / 结局）
                    # ⚠️ to_thread：推进剧情要调模型（`generate_story_step`）
                    if B._get_story_state(cid).get("active"):
                        story_reply = await asyncio.to_thread(B._check_story, cid)
                        story_reply = (story_reply or "").strip()
                        if story_reply:
                            log("🎬 [" + cid[:8] + "] 剧情")
                            if dry:
                                log("[dry] " + story_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + story_reply, cid)
                                log("✅" if ok else "❌")

                    # 重新加载 state（scene/羞辱的 _add_heat 内部已改磁盘），避免内存旧 group_state 覆盖加分
                    state = B.load_state()
                    group_state = state.get(cid) or {}

                    # 推进 last_id 到最新（"只处理之后"的逻辑保证不会重复）
                    group_state["last_id"] = msgs[-1]["message_id"]
                    state[cid] = group_state
                    B._save_group(cid, group_state)
                    B._clear_inflight(cid)          # P12-BC：这一轮跑完了，标记清掉

                    # 批量评定发情值：30分钟周期；有发情值≥80的成员刚发言则立即评定（高潮卡在发言时刻）
                    hot_speaker = any(
                        (group_state.get("heat_value") or {}).get(m.get("sent_by", ""), 0) >= B._tune("heat.judge_trigger", cid)
                        for m in pending
                        if m.get("sent_by") and not B.is_self(m.get("sent_by"))
                        and (m.get("content") or {}).get("text", "").strip()
                        and "加入了群聊" not in (m.get("content") or {}).get("text", "")
                    )
                    if (group_state.get("heat_pending") or {}) and cid not in B.ROOM_CIDS and \
                            B._feat_on(cid, "heat") and \
                            (time.time() - group_state.get("heat_last_judge", 0)
                             >= B._tune("heat.judge_interval", cid) or hot_speaker):
                        try:
                            await asyncio.to_thread(B._judge_pending, cid, _save_group=B._save_group, load_state=B.load_state)
                        except Exception as e:
                            log("judge err: " + str(e)[:60])
                        # 重新加载（_judge_pending 内部已写回 state.json）
                        state = B.load_state()
                        group_state = state.get(cid) or {}

                    # 高潮检测：发情值≥100 → 高潮事件 + 发布排名（剧情进行中跳过，避免刷屏）
                    if B._get_story_state(cid).get("active") or cid in B.ROOM_CIDS \
                            or not B._feat_on(cid, "heat_climax"):
                        hits, climax_changed = [], False
                    else:
                        hits, climax_changed = B._climax_hits(group_state)
                    if climax_changed:
                        state[cid] = group_state
                        B._save_group(cid, group_state)
                    if hits:
                        await B._handle_climax(sender, cid, dry, hits)
                        await B._publish_rank(sender, cid, dry)
                    else:
                        # 定时发布：每天 12:00 和 18:00
                        now = datetime.now()
                        today = now.strftime("%Y-%m-%d")
                        has_data = bool((group_state.get("heat_value") or {}) or (group_state.get("msg_count") or {}))
                        if (now.hour in B._rank_hour_set() and cid in B.PUSH_CIDS
                                and group_state.get("last_daily_rank_date") != today
                                and B._feat_on(cid, "rank_push") and has_data):
                            group_state["last_daily_rank_date"] = today
                            state[cid] = group_state
                            B._save_group(cid, group_state)
                            await B._publish_rank(sender, cid, dry)

                # 冷场吐槽（群里冷清，时间驱动，无新消息也执行）——房间群不毒舌
                cold_reply = "" if cid in B.ROOM_CIDS or not B._feat_on(cid, "devil_cold") \
                    else await asyncio.to_thread(B._maybe_devil_cold_roast, cid, _save_group=B._save_group, load_state=B.load_state)
                cold_reply = (cold_reply or "").strip()
                if cold_reply:
                    log("😈 [冷场] [" + cid[:8] + "] 吐槽")
                    if dry:
                        log("[dry] " + cold_reply[:50])
                    else:
                        ok = await sender.send("🤖 " + cold_reply, cid)
                        log("✅" if ok else "❌")

                # 发骚超时自动结束（防卡死：发骚 15 分钟无互动自动解锁）
                # ⚠️ to_thread：`end_fasao` 内部会调模型写收尾（15:54:49 实测跑过一次）
                if B._get_fasao_state(cid).get("active"):
                    fasao_timeout = await asyncio.to_thread(B._check_fasao_timeout, cid)
                    fasao_timeout = (fasao_timeout or "").strip()
                    if fasao_timeout:
                        log("⏱️ [" + cid[:8] + "] 发骚超时自动结束")
                        if dry:
                            log("[dry] " + fasao_timeout[:50])
                        else:
                            ok = await sender.send("🤖 " + fasao_timeout, cid)
                            log("✅" if ok else "❌")

                # 公演超时自动解锁（P12-A 补）。原先**没有超时**：
                # 人散了没人发 /公演结束 ⇒ 这个群被永久屏蔽，只有停止词那条分支可达。
                # 这里只解锁、不发消息、不生成小说（待审的 review_status 保留）。
                if B._get_perf_state(cid).get("active"):
                    B._check_perf_timeout(cid)

                # 投票定时检查（截止到→自动结束；时间驱动，无新消息也执行）
                if B._get_vote_state(cid).get("active"):
                    vote_reply = await asyncio.to_thread(B._check_vote, cid)
                    vote_reply = (vote_reply or "").strip()
                    if vote_reply:
                        log("📊 [" + cid[:8] + "] 投票")
                        if dry:
                            log("[dry] " + vote_reply[:50])
                        else:
                            ok = await sender.send("🤖 " + vote_reply, cid)
                            log("✅" if ok else "❌")
                # P12-I 延迟自证：这一轮"从平台发出到我们处理"隔了多久、我们花了多久。
                # 只在慢的时候打，平时不刷屏；口径与 tools/latency_report.py 一致。
                if _t_round is not None:
                    _cost = time.time() - _t_round
                    if _lag >= 1.5 or _cost >= 3.0:
                        # 来源可证：webhook（平台直推）还是 sio（侧车账号的 Socket.IO）
                        _src = ""
                        for _m in reversed(msgs or ()):
                            if not B.is_self(_m.get("sent_by")):
                                _src = str(_m.get("_source") or "sio")
                                break
                        log("⏱️ [{}] 投递 {:.1f}s + 本轮处理 {:.1f}s（来源 {}）".format(
                            cid[:8], _lag, _cost, _src or "?"))
            except Exception as e:
                msg = str(e)
                if "captcha" in msg.lower():
                    # 撞验证码：只降级 HTTP 功能，不退出（收发走 /ws + Playwright 不受影响）
                    http_backoff_until = time.time() + 3600  # HTTP 兜底退避 1 小时
                    B._ctx.set_captcha_active(True)
                    if not B._captcha_notified.get(cid):
                        B._captcha_notified[cid] = True
                        if B._feat_on(cid, "screen_captcha_notice"):
                            # 撞验证码是**跨群**提示：认证群用代码默认那句（小小糯口吻），
                            # 玫瑰房有自己那份（见 personas.PHRASES.rose_butler），
                            # 取不到就回落默认 —— 认证群的文案一个字没变。
                            await B._send(sender, cid, "🤖 " + (
                                B.p("captcha_notice", cid)
                                or B._personas.fill(B.CAPTCHA_NOTICE, cid)), dry)
                        # 拉 noVNC 人工验证窗口（撞验证码时提供人工过滑块入口，只拉一次防刷屏）
                        try:
                            subprocess.Popen(
                                [B.HERMES_PY, "captcha_novnc.py"],
                                cwd=str(B.ROOT), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
                            log("已拉起 captcha_novnc.py（noVNC 人工验证）")
                        except Exception as _e:
                            log("拉起 captcha_novnc 失败: " + str(_e)[:60])
                    log("⚠️ 撞验证码，降级 HTTP 功能（收发继续），HTTP 兜底退避 1 小时")
                    # 不写 bot.paused、不 return，继续循环处理 Socket.IO 实时推送
                if _is_unauth_error(msg):
                    captcha_hit = True
                    captcha_cooldown_until = time.time() + 1800  # cookie 失效，冷却 30 分钟（避免频繁失败触发 IP 风控）
                    await _on_unauthorized("cookie 可能失效")
                    break
                # 生成失败（网络超时等），回滚 last_id，下轮重试
                if old_last_id is not None:
                    try:
                        B._rollback_last_id(cid, old_last_id)
                        log("⚠️ [" + cid[:8] + "] 生成异常，回滚 last_id，下轮重试")
                    except Exception:
                        pass
                log("loop[" + cid[:8] + "]: " + msg[:100])

            # Socket.IO 订阅是实时推送，drain 为纯内存操作，固定最快间隔（不退避）；
            # HTTP 兜底已由 HTTP_FALLBACK_INTERVAL 独立低频控制，无需在此退避
            meta["interval"] = B.FAST_INTERVAL
        if once:
            break
        # captcha 降频：触发后长冷却，冷却结束且恢复后才重置计数
        if captcha_hit or time.time() < captcha_cooldown_until:
            wait = max(captcha_cooldown_until - time.time(), 1.0)
        else:
            captcha_streak = 0  # 恢复正常轮询，重置连续 captcha 计数
            _alerted_unauthorized = False  # 恢复正常，重置告警防重标志
            _unauth_streak = 0  # 恢复正常，重置连续 unauthorized 计数
            _recovering = False  # 重置自动恢复标志
            wait = 0.2  # 小睡，每群按自己的 interval 判断轮询时间
        await asyncio.sleep(wait)


async def run_all(interval: float, dry: bool, once: bool, *, B) -> None:
    """建**唯一一份**共享资源（sender / state / 订阅器），再决定起几个主循环。

    串行（默认）= 一个实例管全部群，与 P12-I 之前逐字相同；
    并行（`DZMM_PARALLEL_GROUPS=on`）= 每个群一个实例，各管自己的群，
    一个群的长任务不再堵住另一个群（主实例额外负责全局工作）。
    """
    sender = B._wrap_with_outbox(B.Sender(groups=B.GROUPS), dry)
    # P12-J 教训：`asyncio.to_thread` 的默认池是 `min(32, cpu+4)` —— 这台机器 2 核
    # 只有 **5 个 worker**。只要有几个平台读卡住（requests 超时前），池子就满，
    # 之后连 LLM 生成都要排队（2026-09-27 实测静默 11 分钟）。
    # 这里放大到 16：线程本身很轻，真正的保护靠"把超时交给 requests"。
    try:
        import concurrent.futures as _cf
        asyncio.get_running_loop().set_default_executor(
            _cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="dzmm"))
        log("P12-J: 线程池 max_workers=16（默认只有 cpu+4=5，卡住就会连累生成）")
    except Exception as _e:                                    # noqa: BLE001
        log("P12-J: 设置线程池失败（不影响运行）：" + str(_e)[:60])
    if not dry:
        B._log_runtime_status()
        try:
            await sender.start()
        except Exception as e:
            log("sender init failed: " + str(e)[:80])
        # P12-H：给"配置里没写 uid"的 Bot 从群成员表里认一次身份。
        # 放在 sender.start() **之后**：这一步要用登录态调 getMembers，
        # 启动最初那一刻 cookie 还没准备好，太早查只会白失败一次。
        try:
            _resolved = await asyncio.to_thread(B._resolve_bot_uids)
            for _k, _uid in (_resolved or {}).items():
                log("P11: Bot {} 的身份 = {}".format(
                    _k, ("已认到 " + _uid[:8]) if _uid else
                    "**没认到**（Bot 可能还没被拉进群；进群后发出第一条消息时会自动认）"))
        except Exception as e:                                 # noqa: BLE001
            log("P11: Bot 身份识别失败（不影响运行）：" + str(e)[:80])

    state = B.load_state()
    # P12-BC：启动时收拾"上次被杀掉时正在处理"的那条消息（容器重建 = SIGKILL，
    # `except` 来不及回滚 last_id，那条消息会看起来"处理过了"而永久跳过）。
    try:
        B._recover_inflight(state, B.GROUPS)
    except Exception as e:                                     # noqa: BLE001
        log("P12-BC: inflight 恢复失败（不影响启动）：" + str(e)[:70])
    # P12-BJ：成员快照对账 —— 补"断线/风控墙期间入群、我们连事件都没看见"的新成员
    # （首次只建快照，不筛查）。只在开了筛查的群里做。
    for _cid in B.GROUPS:
        if not B._feat_on(_cid, "screen"):
            continue
        try:
            await B._reconcile_members(sender, _cid, dry)
        except Exception as e:                                 # noqa: BLE001
            log("P12-BJ: [" + _cid[:8] + "] 成员对账失败（不影响启动）：" + str(e)[:60])
    # P12-J：出站队列的**安全重试**后台任务 —— 只重试"平台明确拒绝（确定未投递）"
    # 的那一类；模糊失败绝不重发（防重复打扰群友）。
    try:
        _rw = getattr(sender, "start_retry_worker", None)
        if callable(_rw):
            _rw()
            log("P12-J: 出站重试任务已启动（只重试明确被拒的，间隔 {}s，最多 {} 次）".format(
                getattr(sender, "retry_interval", "?"),
                getattr(sender, "retry_max", "?")))
    except Exception as _e:                                    # noqa: BLE001
        log("P12-J: 出站重试任务启动失败（不影响发送）：" + str(_e)[:70])
    # Socket.IO 订阅器（读消息主通道）：后台长连接监听 message:new，实时推送。
    # 按群分队列，所以多个主循环实例各自 drain 自己的群是安全的。
    from sio_subscribe import SioSubscriber
    subscriber = SioSubscriber(B.GROUPS)
    sub_task = asyncio.create_task(subscriber.start())
    # P12-AZ：把这条长连接交给发送侧 —— 账号的长消息优先用它发（一条，不用握手）。
    B._sender_mod._LIVE_SIO = subscriber   # P3-7：真身住 runtime/sender.py

    # P12-J：官方 Bot Webhook 接收端（可选，`DZMM_WEBHOOK_PORT` 打开）。
    # 它把平台**直推**的群消息塞进同一个队列 ⇒ 主循环一行都不用改，
    # 而"读消息"不再只依赖侧车账号的 Socket.IO（那条路会断线重连、受风控影响）。
    wh_runner = None
    _wh_port = int(os.environ.get("DZMM_WEBHOOK_PORT") or 0)
    if _wh_port:
        try:
            # ⚠️ 必须按路径加载：上面刚 import 过 sio_subscribe，它会把项目根目录
            # 从 sys.path 里摘掉（见 _load_root_module 的说明）。
            _wh = B._load_root_module("webhook_server")
            # ⚠️ 必须传 `_CFG.bots`（BotSpec，带 token_env）——
            # `bots_for_identity()` 是给 identity 的 dict，没有 token_env，
            # 传它会导致一个 Secret 都收不到（P12-J 踩过）。
            _secrets = _wh.collect_secrets(B._CFG.bots)
            if not _secrets:
                log("P12-J: 开了 Webhook 端口但没有配置 Secret"
                    "（.env 需要 DZMM_BOT_SECRET / DZMM_BOT_SECRET_ROSE），跳过")
            else:
                _app = _wh.build_app(secrets=_secrets,
                                     push=lambda cid, m: subscriber.push(cid, m),
                                     log=log)
                wh_runner = await _wh.start(_app, "0.0.0.0", _wh_port)
                log("P12-J: Webhook 接收端已监听 :{}（白名单 Secret {} 个，"
                    "路径 /bot/webhook，按 message_id 去重）".format(
                        _wh_port, len(_secrets)))
        except Exception as _e:                                # noqa: BLE001
            log("P12-J: Webhook 启动失败（不影响 Socket.IO 收消息）："
                + str(_e)[:80])

    try:
        if B._parallel_groups_on() and len(B.GROUPS) > 1:
            log("P12-I: 按群并行=开，每群一个主循环实例（共 {} 个）".format(len(B.GROUPS)))
            tasks = [
                asyncio.create_task(B.main_loop(
                    interval, dry, once, groups=[cid], sender=sender, state=state,
                    subscriber=subscriber, is_primary=(i == 0)))
                for i, cid in enumerate(B.GROUPS)
            ]
            if once:
                await asyncio.gather(*tasks)
            else:
                # 任何一个实例退出（如 token.emergency / 未捕获异常）就收摊，
                # 不能留下"半个机器人在跑"。
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in pending:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):   # noqa: BLE001
                        pass
        else:
            log("P12-I: 按群并行=关（串行处理全部群）")
            await B.main_loop(interval, dry, once, groups=B.GROUPS, sender=sender,
                            state=state, subscriber=subscriber, is_primary=True)
    finally:
        sub_task.cancel()
        if wh_runner is not None:
            try:
                await wh_runner.cleanup()
            except Exception:                                  # noqa: BLE001
                pass
        try:
            await sender.close()
        except Exception:                                      # noqa: BLE001
            pass
