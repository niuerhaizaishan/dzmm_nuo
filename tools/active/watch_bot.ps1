#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Watch the live bot and EXIT when the state changes (exiting is what delivers
    the notification to whoever launched it).

.DESCRIPTION
    Why this exists: the in-container health check (check_bot_health.py, every 5 min)
    only looks at session / process / outbox backlog. It is blind to:

      * crash loop  - bot.py exits and restarts every 10s; the health check still
                      reports "normal" because a process exists.
      * loop errors - repeated "rollback last_id" / "module 'datetime' has no
                      attribute" exceptions; the health check never reads app logs.
      * stuck       - inbound messages arrive but nothing is ever sent out.

    This script covers exactly those three gaps. By default it is READ-ONLY: it only
    runs docker ps / docker logs / docker inspect and cat. It never writes a file,
    never restarts anything and never posts to a group.

    ### 自动修复（-AutoRepair，屋主 2026-10-01 授权）

    屋主原话："如果发现异常或续期失败这种，你就自己修复。"
    开了 `-AutoRepair` 之后，发现**可修复**的问题会先自救，再决定要不要吵醒屋主。
    纪律（比"能修"更重要，逐条写在下面 `Invoke-Repair` 上方）：

      1. **绝不删锁**。修复一律走 `renew_token.py` / `headless_relogin.py` —— 它们
         自己会正确地抢锁，抢不到就 SKIP。互斥交给唯一权威实现，脚本不越权。
      2. **不重启还活着的 bot**。会话类问题只走 renew -> relogin；只有容器不在
         (container_down) 或真在崩溃循环 (crash_loop) 才 `docker compose up -d`。
      3. **限流**：`-RepairCooldownSec` 内不重复、一小时最多 `-MaxRepairsPerHour` 次。
         修复风暴比原故障更糟。
      4. **有人在过验证码时不插手**（容器里存在 `bot.paused` 哨兵）。
      5. **留痕**：每次动作追加到与脚本同目录的 `watch_bot.repairs.log`。
      6. **修不好就退出通知**（exit 1），交给屋主 —— 不自作主张反复试。

    De-noising: an issue is reported once when it appears and once when it clears,
    never on every round (same "window alert" rule as P12-BJ).

    NOTE: this file contains non-ASCII (Chinese/emoji log anchors), so it MUST be
    saved as UTF-8 **with BOM**. Windows PowerShell 5.1 reads .ps1 as ANSI unless a
    BOM is present, and the mojibake then breaks parsing outright. Verify with:
        $b=[IO.File]::ReadAllBytes($p); $b[0..2] -join ','   # want 239,187,191
    (That is also why deploy.ps1 says to keep itself ASCII-only -- different choice,
    same underlying trap.)

.PARAMETER IntervalSec
    Seconds between checks (default 120).

.PARAMETER MaxMinutes
    Give up after this many minutes and report "no anomaly" (default 180).

.PARAMETER Window
    Time window for `docker logs --since` (default 5m).

.PARAMETER AutoRepair
    Enable self-repair. OFF by default: several callers rely on this script being
    strictly read-only, so repairing is an explicit opt-in.

.PARAMETER DryRun
    With -AutoRepair: log exactly what WOULD be done, but run nothing.

.PARAMETER RepairGraceRounds
    How many consecutive rounds an issue must persist before repairing (default 3).
    A single-round blip is not worth touching production.

    ⚠️ 为什么是 3（≈6 分钟）而不是更小：容器**内置**的恢复路径在会话坏满 5 分钟时
    才升级到密码重登（`renew_token.RELOGIN_MIN_BAD_MIN`）。本脚本是**兜底**、
    不是主力 —— 门槛设在它之后，先让它自己救；只有它没救回来我才动手。
    抢在它前面只会和它撞在同一把锁上。

.PARAMETER RepairCooldownSec
    Minimum seconds between two repair attempts (default 300).

.PARAMETER MaxRepairsPerHour
    Hard cap on repair attempts in any rolling hour (default 3).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File watch_bot.ps1 -MaxMinutes 60

.EXAMPLE
    # 屋主要的形态：盯着 + 自己修
    powershell -NoProfile -ExecutionPolicy Bypass -File watch_bot.ps1 -AutoRepair
#>
param(
    [int]$IntervalSec = 120,
    [int]$MaxMinutes = 180,
    [string]$Window = '5m',
    # Longer window for the "stuck" counters: a reply that lands just outside the
    # short window would otherwise look like "talked to, never answered".
    [string]$WindowLong = '10m',
    [string]$Remote = 'root@47.237.152.187',
    [string]$RemoteDir = '/opt/dzmm',
    # --- 自动修复（默认关；调用方显式打开）---
    [switch]$AutoRepair,
    [switch]$DryRun,
    [int]$RepairGraceRounds = 3,
    [int]$RepairCooldownSec = 300,
    [int]$MaxRepairsPerHour = 3,
    # 用**合成输入**验证"修不修"的决策逻辑，然后退出。不碰生产、不发 ssh。
    [switch]$SelfTest
)

$ErrorActionPreference = 'Continue'

# Cumulative counters from the previous round (token-renewal log). The first round
# only records a baseline -- otherwise every pre-existing failure would look "new".
$script:PrevCum = @{}
$script:CumReady = $false

# -- Remote probe: ONE ssh round trip, strictly read-only.
# Only single-pattern greps (no '|' alternation) to avoid PowerShell/ssh quoting issues.
$CheckCmd = @(
    'echo CONTAINER=$(docker ps --filter name=dzmm-bot --format {{.Status}})',
    'echo SESSION=$(cat /opt/dzmm/data/session_state.json)',
    # Startup marker: '我的身份数=' appears EXACTLY once per bot.py start.
    # Do NOT use 'P11:' here -- measured 2026-10-01, one startup prints 15 lines
    # containing P11: (identity dump, bot keys, persona, config), so counting it
    # reports a 15x-inflated "start count" and a healthy bot looks like a crash loop.
    "echo NSTART=`$(docker logs --since $Window dzmm-bot 2>&1 | grep -c 我的身份数=)",
    "echo NDTERR=`$(docker logs --since $Window dzmm-bot 2>&1 | grep -c datetime)",
    "echo NTRACE=`$(docker logs --since $Window dzmm-bot 2>&1 | grep -c Traceback)",
    "echo NIN=`$(docker logs --since $Window dzmm-bot 2>&1 | grep -c webhook)",
    "echo NROLL=`$(docker logs --since $Window dzmm-bot 2>&1 | grep -c 回滚)",
    "echo NEBUSY=`$(docker logs --since $Window dzmm-bot 2>&1 | grep -c Errno.16)",
    "echo NCWFAIL=`$(docker logs --since $Window dzmm-bot 2>&1 | grep -c 写凭据失败)",
    # 'talked to the bot' vs 'delivered something'. Using the @ marker and the
    # delivery tick makes this precise, and the tick covers BOTH send channels
    # (bot_ok only ever appears for official-Bot groups, never the tea room).
    "echo NAT=`$(docker logs --since $WindowLong dzmm-bot 2>&1 | grep -c @消息)",
    "echo NOK=`$(docker logs --since $WindowLong dzmm-bot 2>&1 | grep -c ✅)",
    # --- token renewal: CUMULATIVE counters over the whole renew log.
    # The renewal log is append-only and NOT rotated, and its fast path is silent
    # by design (a quiet log is normal), so a time window would be misleading.
    # Instead we count the whole file and compare with the previous round: only an
    # INCREASE is news. A rotation makes counts drop, which never raises an alarm.
    # NOTE: keep patterns free of spaces and double quotes. Windows PowerShell 5.1
    # strips embedded double quotes when passing an argument to ssh, and a space
    # splits the pattern into extra arguments (`grep -c "a b"` -> grep treats `b`
    # as a filename and prints "file:43", which fails TryParse and reads as 0).
    # Non-ASCII patterns DO survive the wire intact (verified 2026-10-01).
    'echo CUMRENFAIL=$(grep -c FAIL /opt/dzmm/renew_token.log)',
    'echo CUMRELSKIP=$(grep -c relogin..SKIP /opt/dzmm/renew_token.log)',
    'echo CUMREMRG=$(grep -c EMERGENCY /opt/dzmm/renew_token.log)',
    'echo CUMREESC=$(grep -c ESCALATE /opt/dzmm/renew_token.log)',
    'echo NOW=$(date +%s)'
) -join '; '

function Invoke-Probe {
    # Returns a hashtable of key=value lines, or @{ ssh_fail = $true }.
    $raw = & ssh -o BatchMode=yes -o ConnectTimeout=15 $Remote $CheckCmd 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        return @{ ssh_fail = $true; raw = (($raw | Out-String).Trim()) }
    }
    $kv = @{}
    foreach ($line in $raw) {
        $s = "$line".Trim()
        $i = $s.IndexOf('=')
        if ($i -gt 0) { $kv[$s.Substring(0, $i)] = $s.Substring($i + 1) }
    }
    return $kv
}

function Get-Issues($kv) {
    # Turn one sample into the set of problems that are definitely present.
    # Deliberately conservative: prefer a miss over a false alarm.
    $issues = @{}

    if ($kv.ContainsKey('ssh_fail')) {
        if ($kv.ssh_fail) { $issues['ssh_fail'] = "ssh probe failed: $($kv.raw)" }
        return $issues
    }

    # (1) container not running
    $c = "$($kv['CONTAINER'])".Trim()
    if (-not $c.StartsWith('Up')) {
        $issues['container_down'] = "docker ps says '$c' (expected 'Up ...')"
    }

    # (2) crash loop: real startup marker ('我的身份数=', 1 per start) seen >= 2 times.
    # NOTE: bot.py DOES self-exit whenever it sees a token.emergency signal
    # ("主动退出交给 entrypoint 重启") and entrypoint restarts it after 10s, so a
    # restart right after an emergency is DESIGNED, not a loop. A loop only exists
    # if the marker fires repeatedly -- i.e. the signal file is never cleared.
    $nstart = 0; [void][int]::TryParse("$($kv['NSTART'])", [ref]$nstart)
    if ($nstart -ge 2) {
        $issues['crash_loop'] = "bot started $nstart times in last $Window (expected <=1)"
    }

    # (3) main-loop exceptions.
    # 'Traceback' is a generic escape; the explicit markers below are more precise
    # when they exist. Threshold 2 so a single hiccup does not page anyone.
    $ntrace = 0; [void][int]::TryParse("$($kv['NTRACE'])", [ref]$ntrace)
    $ndterr = 0; [void][int]::TryParse("$($kv['NDTERR'])", [ref]$ndterr)
    $nroll  = 0; [void][int]::TryParse("$($kv['NROLL'])",  [ref]$nroll)
    if ($ntrace -ge 2) {
        $issues['loop_error_traceback'] = "$ntrace python tracebacks in last $Window"
    }
    if ($ndterr -ge 2) {
        $issues['loop_error_datetime'] = "$ndterr datetime attribute errors in last $Window (mainloop.py import bug)"
    }
    if ($nroll -ge 2) {
        $issues['loop_error_rollback'] = "$nroll message-processing failures (rollback last_id) in last $Window"
    }

    # (3b) credential writes. Both the renewer AND the bot itself write the token,
    # and both log this on stderr, so the container log is the only place that sees
    # all of them (renew_token.log misses the bot's own harvest writes).
    #
    # Fallback-to-in-place (Errno 16 / S1) is EXPECTED on every write, because
    # .dzmm_token.json is a single-file bind mount where os.replace can never work.
    # Measured 2026-10-01: ~2 per hour, and the in-place write SUCCEEDS (token file
    # mtime advances). So alerting on each one would be pure noise; only a burst
    # (many writes in one window) is news.
    $nbusy = 0; [void][int]::TryParse("$($kv['NEBUSY'])", [ref]$nbusy)
    if ($nbusy -ge 5) {
        $issues['cred_write_ebusy_storm'] = "$nbusy credential writes hit Errno 16 in last $Window (usual is 1-2/hour) -- S1 degraded path is spinning"
    }
    # The real alarm: the in-place fallback ITSELF failed, i.e. the token was NOT
    # written back. That is the state that ends in a dead session.
    $ncwfail = 0; [void][int]::TryParse("$($kv['NCWFAIL'])", [ref]$ncwfail)
    if ($ncwfail -ge 1) {
        $issues['cred_write_failed'] = "$ncwfail credential write(s) FAILED outright -- token was not written back"
    }

    # (4) stuck: something addressed the bot but nothing at all was delivered.
    # Uses the LONG window, and requires an actual @ (not merely group chatter --
    # webhook receives every message, most of which the bot must not answer).
    $nat = 0;  [void][int]::TryParse("$($kv['NAT'])", [ref]$nat)
    $nok = 0;  [void][int]::TryParse("$($kv['NOK'])", [ref]$nok)
    if ($nat -ge 2 -and $nok -eq 0) {
        $issues['stuck_no_outbound'] = "$nat messages addressed to the bot but 0 successful sends in last $WindowLong"
    }

    # (5) session state and renewal heartbeat
    $sess = "$($kv['SESSION'])".Trim()
    if ($sess -match '"probe"\s*:\s*"([^"]+)"') {
        $probe = $Matches[1]
        # ⚠️ 'locked' 是**正常**状态，不是故障：renew_token 抢不到会话锁时写
        # `_write_session_state("locked")`（含义是"循环跑到了，但这一轮没验证会话"）。
        # 典型场景就是**人工在过验证码** —— 那条路径最长持锁 10 分钟（noVNC 等人）。
        # 原先按 `probe != 'ok'` 一律判 session_bad ⇒ 每次人工过验证码都误报一条
        # "会话故障"，真正该报的 unauth/error 反而被这条噪音淹没。
        if ($probe -ne 'ok' -and $probe -ne 'locked') {
            $issues['session_bad'] = "session probe = '$probe' (expected ok|locked)"
        }
    } else {
        $issues['session_unreadable'] = "cannot parse session_state.json: $sess"
    }
    if ($sess -match '"at"\s*:\s*([0-9.]+)') {
        $at = [double]$Matches[1]
        $now = 0.0; [void][double]::TryParse("$($kv['NOW'])", [ref]$now)
        if ($now -gt 0) {
            $ageMin = [int](($now - $at) / 60)
            if ($ageMin -gt 25) {
                $issues['heartbeat_stale'] = "heartbeat is $ageMin min old (threshold 25)"
            }
        }
    }

    # (6) token renewal -- delta of cumulative counters (see the probe comments).
    # Turns "the renewal chain broke" into a specific, actionable sentence each.
    $fields = @{
        CUMRENFAIL = @('renew_failure',            'renew_token.log grew FAIL lines')
        CUMRELSKIP = @('renew_relogin_selflocked', 'auto relogin was skipped because the caller already held the session lock (P0 self-lock)')
        CUMREMRG   = @('renew_emergency',          'a token.emergency signal was written (bot restart path)')
        CUMREBUSY  = @('renew_cred_ebusy',         'credential write fell back to in-place write (Errno 16 / single-file mount, S1)')
        CUMREESC   = @('renew_escalate',           'the renewal escalation chain fired (session was unavailable)')
    }
    $cum = @{}
    foreach ($k in $fields.Keys) {
        $v = 0; [void][int]::TryParse("$($kv[$k])", [ref]$v)
        $cum[$k] = $v
        if ($script:CumReady) {
            $before = 0
            if ($script:PrevCum.ContainsKey($k)) { $before = [int]$script:PrevCum[$k] }
            $delta = $v - $before
            if ($delta -gt 0) {
                $issues[$fields[$k][0]] = "$($fields[$k][1]) [+$delta since last check]"
            }
        }
    }
    $script:PrevCum = $cum
    $script:CumReady = $true

    return $issues
}

function Show-Report($title, $cur, $prev) {
    Write-Output ""
    Write-Output "=================================================="
    Write-Output "  $title"
    Write-Output "  local time $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Output "=================================================="
    if ($cur.Count -gt 0) {
        Write-Output "  OPEN issues ($($cur.Count)):"
        foreach ($k in ($cur.Keys | Sort-Object)) { Write-Output "    [$k] $($cur[$k])" }
    }
    $cleared = @($prev.Keys | Where-Object { -not $cur.ContainsKey($_) })
    if ($cleared.Count -gt 0) {
        Write-Output "  CLEARED ($($cleared.Count)):"
        foreach ($k in ($cleared | Sort-Object)) { Write-Output "    $k" }
    }
    Write-Output "=================================================="
}

# -- 自动修复（-AutoRepair）--------------------------------------------------
#
# 屋主 2026-10-01 授权："如果发现异常或续期失败这种，你就自己修复。"
#
# ⚠️ 纪律（比"能修"更重要）：
#   1. **绝不删锁**。应急手册写明"锁文件存在 ≠ 锁被持有"（flock 刻意不删文件），
#      盲删会破坏"唯一登录者"约束，让两端并发登录把**轮换制** refresh_token
#      互相作废 —— 那比原来的故障更糟。
#      所以修复一律**走脚本本身**（renew_token.py / headless_relogin.py）：
#      它们自己会正确地抢锁、抢不到就 SKIP。互斥交给唯一权威实现，本脚本不越权。
#   2. **不重启还活着的 bot**。会话类问题只走 renew -> relogin；只有容器**真的不在**
#      （container_down）或**真在崩溃循环**（crash_loop）才 `docker compose up -d`。
#      否则一次误判就是一次无谓停机。
#   3. **限流**：`RepairCooldownSec` 内不重复，一小时最多 `MaxRepairsPerHour` 次。
#      修复风暴比原故障更糟，而且会把平台风控刷起来。
#   4. **两个"别动"哨兵**：
#        · 容器里 `/app/bot.paused`  —— 有人正在 noVNC 过验证码，动手会把人家搅黄
#        · 宿主机 `/opt/dzmm/.no_autorepair` —— **屋主主动停服维护时先建这个文件**，
#          否则脚本会把你故意停掉的容器又拉起来（compose 是 restart: unless-stopped，
#          手动 stop 是"粘"的，被自动 up -d 顶掉就违背你的意图）
#   5. **留痕**：每个动作追加到与脚本同目录的 `watch_bot.repairs.log`，报告里也列出。
#   6. **修不好就退出通知**（exit 1）交给屋主 —— 不自作主张反复试。

# 允许自动修复的问题；其余只报告。
#   · 重启治不好代码 bug（loop_error_*），也不该拿重启去试
#   · stuck_no_outbound 语义含糊（可能只是这段时间没人 @ 她），不自动动
#
# ⚠️ `crash_loop` 放在**会话类**里不是笔误：崩溃循环几乎总是会话死的**症状** ——
#    bot.py 见到 `token.emergency`（会话死的时候写的）就主动退出、交给 entrypoint
#    重启。2026-10-02 00:46 就是这么发生的：我当时把它归到"容器问题"，结果
#    `docker compose up -d` 打了个空转（容器本来就在跑，输出是 "Container dzmm-bot
#    Running"，**什么也没做**），根因会话从头到尾没被碰过。
$script:RepairableSession = @(
    'session_bad', 'heartbeat_stale', 'cred_write_failed', 'crash_loop')
# 只有"容器**真的不在**"才重启容器 —— 那才是重启能解决的事。
$script:RepairableContainer = @('container_down')

# ⚠️ 续期**事件**（累计计数器的**增量**）：它们是"一次性事件" —— 只在实际发生的那一轮
# 出现，下一轮 delta=0 就消失。所以**必须 grace 0**，否则永远等不到门槛、一次修复都跑不了。
#
# 实测 2026-10-02 12:47：`renew_emergency` / `renew_failure` 出现一轮就消失，监控打印
# "只存在过 1 轮 -> 判为启动残留，不作为恢复通知"，**自动修复一次都没尝试**。
# 而"续期失败"恰恰是屋主点名要我处理的那一类 —— 这个缺陷等于把交办的事做成了摆设。
$script:RepairableEvent = @(
    'renew_failure', 'renew_relogin_selflocked', 'renew_emergency', 'renew_escalate')

$script:IssueFirstSeen = @{}
$script:IssueRounds = @{}      # 每个问题当前连续出现了几轮
$script:ClearedAfter = @{}     # 问题消失时，它一共开了几轮（用来识破"只闪一轮"的残留）
$script:LastRepairAt = $null
$script:RepairTimes = @()
$script:AuditLog = Join-Path $PSScriptRoot 'watch_bot.repairs.log'

function Write-Audit($msg) {
    # ⚠️ DryRun 的条目要能一眼认出来，免得日后翻审计日志把"没跑的动作"当成真跑过。
    $tag = if ($DryRun) { '[DRYRUN] ' } else { '' }
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "  " + $tag + $msg
    try { Add-Content -Path $script:AuditLog -Value $line -Encoding UTF8 } catch { }
    Write-Output "    [audit] $line"
}

function Invoke-SshCmd {
    param([string]$Cmd, [int]$TimeoutSec = 300)
    $raw = & ssh -o BatchMode=yes -o ConnectTimeout=15 $Remote $Cmd 2>&1
    return @{ rc = $LASTEXITCODE; out = (($raw | Out-String).Trim()) }
}

function Get-LightIssues {
    # 修复后只看"容器 + 会话探针"两样 —— 整体重探会搅乱累积计数的基线。
    $r = Invoke-SshCmd 'echo CONTAINER=$(docker ps --filter name=dzmm-bot --format {{.Status}}); echo SESSION=$(cat /opt/dzmm/data/session_state.json)' 60
    $kv = @{}
    foreach ($ln in ($r.out -split "`n")) {
        $s = "$ln".Trim(); $i = $s.IndexOf('=')
        if ($i -gt 0) { $kv[$s.Substring(0, $i)] = $s.Substring($i + 1) }
    }
    $iss = @{}
    $c = "$($kv['CONTAINER'])".Trim()
    if (-not $c.StartsWith('Up')) { $iss['container_down'] = "docker ps says '$c'" }
    $sess = "$($kv['SESSION'])".Trim()
    if ($sess -match '"probe"\s*:\s*"([^"]+)"') {
        $p = $Matches[1]
        # 'locked' 也算正常（人工过验证码时 renew 会写它，见 Get-Issues 里的说明）
        if ($p -ne 'ok' -and $p -ne 'locked') { $iss['session_bad'] = "session probe = '$p'" }
    }
    return $iss
}

function Test-IssueRepairable($k) {
    return ($script:RepairableSession -contains $k) -or
           ($script:RepairableContainer -contains $k) -or
           ($script:RepairableEvent -contains $k)
}

function Test-FlashOnly($newKeys, $goneKeys, $clearedAfter) {
    # 只报"清除"、且被清除的每个问题都只存在过 1 轮 -> 判为启动/窗口残留。
    # 抽成纯函数只为了能被 -SelfTest 直接钉住（这段最容易差一）。
    if ($newKeys.Count -gt 0) { return $false }
    if ($goneKeys.Count -eq 0) { return $false }
    foreach ($k in $goneKeys) {
        if ([int]$clearedAfter[$k] -ge 2) { return $false }
    }
    return $true
}

function Test-WaitForGrace($newKeys, $repNote, $autoRepair) {
    # 新问题里有可修复项、且当前只是"在等门槛" -> 该留下来等，不该退出通知。
    #
    # ⚠️ 2026-10-02 08:55 实测出来的**时序冲突**：新问题出现的那一轮，Invoke-Repair
    # 说"未满 N 轮"，紧接着的 `$changed` 判定就把监控退掉了 ⇒ 门槛永远等不到 ⇒
    # **会话类的自动修复从来没真正跑过**。而"退出 -> relay 重启"还会把
    # `IssueFirstSeen` 一起清空，门槛每一轮都从 1 重来，永远熟不了。
    if (-not $autoRepair) { return $false }
    if ("$repNote" -notmatch '未满') { return $false }
    foreach ($k in $newKeys) { if (Test-IssueRepairable $k) { return $true } }
    return $false
}

function Get-IssueGrace($k) {
    # 容器类留 **1 轮**（≈2 分钟）缓冲，而不是 0。
    # 原因（2026-10-02 亲历）：`docker compose up -d` 重建容器有 ~15 秒的
    # `docker ps` 看不到 Up 的窗口 —— grace=0 时**我自己的部署会被自己的监控
    # 当成故障去"修"**。1 轮正好滤掉这种瞬态；真容器挂了多等 2 分钟可接受
    # （compose 是 `restart: unless-stopped`，多数情况 Docker 自己会先拉起来）。
    if ($script:RepairableContainer -contains $k) { return 1 }
    # 续期事件是"一次性"的：只出现一轮，门槛必须是 0（见集合上方的注释）
    if ($script:RepairableEvent -contains $k) { return 0 }
    return $RepairGraceRounds
}

function Invoke-Repair($cur, $round) {
    $res = @{ tried = $false; ok = $false; actions = @(); note = ''; after = $cur }
    if (-not $AutoRepair -or $cur.Count -eq 0) { return $res }

    # 「别动」哨兵
    $guard = Invoke-SshCmd 'test -f /opt/dzmm/.no_autorepair; echo GUARD=$?; test -f /app/bot.paused; echo PAUSED=$?' 30
    if ($guard.out -match 'GUARD=0') {
        $res.note = '存在 /opt/dzmm/.no_autorepair（屋主指定别动）-> 不自动修复'
        return $res
    }
    if ($guard.out -match 'PAUSED=0') {
        $res.note = '容器里有 bot.paused（有人正在过验证码）-> 不插手'
        return $res
    }

    # 记录每个问题首次出现的轮次（持续性门槛用）
    foreach ($k in $cur.Keys) {
        if (-not $script:IssueFirstSeen.ContainsKey($k)) { $script:IssueFirstSeen[$k] = $round }
    }

    $targets = @($cur.Keys | Where-Object { Test-IssueRepairable $_ })
    if ($targets.Count -eq 0) {
        $res.note = '这些问题不在自动修复范围内（只报告，不重启去试）'
        return $res
    }

    # 持续性门槛：会话类要连续 >= RepairGraceRounds 轮；容器类不设门槛
    $ripe = @($targets | Where-Object {
        ($round - [int]$script:IssueFirstSeen[$_]) -ge (Get-IssueGrace $_) })
    if ($ripe.Count -eq 0) {
        $res.note = "刚出现，未满 $RepairGraceRounds 轮 -> 先观察不动手"
        return $res
    }

    # 限流
    $now = Get-Date
    if ($script:LastRepairAt -ne $null -and ($now - $script:LastRepairAt).TotalSeconds -lt $RepairCooldownSec) {
        $ago = [int]($now - $script:LastRepairAt).TotalSeconds
        $res.note = "冷却中（距上次修复 $ago s < $RepairCooldownSec s）"
        return $res
    }
    $script:RepairTimes = @($script:RepairTimes | Where-Object { ($now - $_).TotalMinutes -lt 60 })
    if ($script:RepairTimes.Count -ge $MaxRepairsPerHour) {
        $res.note = "一小时内已修 $($script:RepairTimes.Count) 次，达上限 $MaxRepairsPerHour -> 停手交给你"
        return $res
    }

    $res.tried = $true
    $script:LastRepairAt = $now
    $script:RepairTimes += $now
    $script:IssueFirstSeen = @{}      # 修完重置，免得下一轮又立刻"熟透"

    $containerIssue = @($ripe | Where-Object { $script:RepairableContainer -contains $_ })
    # ⚠️ 续期**事件**也要走会话阶梯（renew -> relogin）—— 它们不在 `RepairableSession`
    # 那个集合里（那个集合管的是"持续存在"的会话症状），但修法是同一条。
    # 漏了这一句的后果：`tried=true` 却**动作列表为空** —— "修了但什么都没做"。
    $sessionIssue = @($ripe | Where-Object {
        ($script:RepairableSession -contains $_) -or ($script:RepairableEvent -contains $_) })

    # ---- 1) 先修**根因**：会话/续期 ----
    # ⚠️ 顺序不能反。2026-10-02 00:48 的教训：崩溃循环 + 会话死时，我原先用
    # `if (容器问题) {...} else {会话阶梯}` 二选一，于是只跑了 `compose up -d`。
    # 而容器本来就在跑 ⇒ 输出 "Container dzmm-bot Running" ⇒ **纯空转**，
    # 会话（真正的根因）从头到尾没被碰过。崩溃循环是会话死引起的，先修会话。
    if ($sessionIssue.Count -gt 0) {
        # 先走续期脚本：它内部就会升级到密码重登，且自带冷却与锁
        $act = "会话问题 [$($sessionIssue -join ',')] -> renew_token.py"
        $res.actions += $act; Write-Audit $act
        if (-not $DryRun) {
            $r = Invoke-SshCmd 'docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python renew_token.py' 420
            $tail = (($r.out -split "`n") | Select-Object -Last 2) -join ' / '
            $res.actions += "rc=$($r.rc)  $tail"; Write-Audit "  rc=$($r.rc)  $tail"
        }
        Start-Sleep -Seconds 5
        if ((Get-LightIssues).Count -gt 0) {
            # 续期可能被自己的 30 分钟冷却挡住 -> 直接手动重登绕过冷却。
            # ⚠️ 它自己会抢锁、抢不到就 SKIP，所以这一步不会破坏"唯一登录者"。
            $act = '仍未恢复 -> headless_relogin.py（绕过续期冷却；它自己抢锁，抢不到就 SKIP）'
            $res.actions += $act; Write-Audit $act
            if (-not $DryRun) {
                $r = Invoke-SshCmd 'timeout 240 docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python headless_relogin.py' 300
                $tail = (($r.out -split "`n") | Select-Object -Last 2) -join ' / '
                $res.actions += "rc=$($r.rc)  $tail"; Write-Audit "  rc=$($r.rc)  $tail"
            }
        }
    }

    # ---- 2) 再看容器是不是**真的不在**（那才是重启能解决的事）----
    if ($containerIssue.Count -gt 0) {
        $act = "容器问题 [$($containerIssue -join ',')] -> docker compose up -d dzmm-bot"
        $res.actions += $act; Write-Audit $act
        if (-not $DryRun) {
            $r = Invoke-SshCmd "cd $RemoteDir && docker compose up -d dzmm-bot" 300
            $tail = (($r.out -split "`n") | Select-Object -Last 2) -join ' / '
            $res.actions += "rc=$($r.rc)  $tail"; Write-Audit "  rc=$($r.rc)  $tail"
            # 让 entrypoint 的 headless_relogin + bot 启动跑完再判定
            Start-Sleep -Seconds 30
        } else {
            $res.actions += '(DryRun：没有真的执行)'
        }
    }

    if ($DryRun) {
        $res.note = '(DryRun) 以上是"将会执行"的动作，实际一个都没跑'
        return $res
    }

    # ⚠️ 复探前**强制刷新一次状态**。`data/session_state.json` 是 renew 循环**每 5 分钟**
    # 才写一次的文件；刚修完就立刻读它，读到的是"修复前"的陈旧值 ——
    # 2026-10-02 00:48 的误判"修复失败"就是这么来的（真实会话 00:47:12 就好了，
    # 但文件里还停在 00:46:24 的 unauth）。跑一次 renew_token.py 让它落地再判。
    $null = Invoke-SshCmd 'docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python renew_token.py' 420
    Start-Sleep -Seconds 5
    $res.after = Get-LightIssues
    $res.ok = ($res.after.Count -eq 0)
    if ($res.ok) { $res.note = '修复后复探（已强制刷新状态）：容器 Up、会话 ok' }
    else { $res.note = '修复后复探仍有问题：' + (($res.after.Keys) -join ',') }
    Write-Audit "  => $($res.note)"
    return $res
}

# -- 自检（-SelfTest）：用合成输入验证"修不修"的决策，不碰生产、不发 ssh ----
if ($SelfTest) {
    # 假的 ssh：两个哨兵一律报"不存在"
    function Invoke-SshCmd {
        param([string]$Cmd, [int]$TimeoutSec = 0)
        return @{ rc = 0; out = "GUARD=1`nPAUSED=1" }
    }
    $DryRun = $true
    $AutoRepair = $true      # ⚠️ 必须一起打开：Invoke-Repair 的第一道门槛就是它
    # ⚠️ 自检**绝不能**写真实的审计日志（第一次跑就把 8 行测试噪声写进了
    # watch_bot.repairs.log）。审计日志必须只含"真的动过生产"的记录。
    $script:AuditLog = Join-Path ([IO.Path]::GetTempPath()) 'watch_bot_selftest.log'
    $script:selfPass = 0; $script:selfFail = 0

    function T($name, $cond) {
        if ($cond) { $script:selfPass++; Write-Output "  [PASS] $name" }
        else { $script:selfFail++; Write-Output "  [FAIL] $name" }
    }
    function Reset-RepairState {
        $script:IssueFirstSeen = @{}
        $script:LastRepairAt = $null
        $script:RepairTimes = @()
    }

    Write-Output "[self-test] 决策逻辑（不碰生产）"
    Write-Output "  grace=$RepairGraceRounds cooldown=${RepairCooldownSec}s cap=$MaxRepairsPerHour/h"

    # 1) 没问题 -> 不动
    Reset-RepairState
    $r = Invoke-Repair @{} 1
    T '无问题 -> 不修' (-not $r.tried)

    # 2) 不在范围内的（代码 bug，重启治不好）-> 只报告
    Reset-RepairState
    $r = Invoke-Repair @{ loop_error_traceback = 'x' } 5
    T '不可修复类(loop_error_traceback) -> 不修' ((-not $r.tried) -and ($r.note -match '不在自动修复范围'))

    # 3) 会话问题第 1 轮 -> 未满 grace，先观察
    Reset-RepairState
    $r = Invoke-Repair @{ session_bad = 'x' } 1
    T '会话问题首次出现 -> 不修（观察）' (-not $r.tried)

    # 4) 会话问题持续到满 grace 轮 -> 熟 -> 修，且先走 renew_token.py
    #    （不 Reset：case 3 已经以 round=1 记下了"首次出现"）
    $ripeRound = 1 + $RepairGraceRounds
    $r = Invoke-Repair @{ session_bad = 'x' } $ripeRound
    T "会话问题持续 $RepairGraceRounds 轮 -> 修" ($r.tried)
    T '  修复动作 = renew_token.py' (($r.actions -join ' ') -match 'renew_token')

    # 4b) ⚠️ 2026-10-02 00:48 的真实教训：crash_loop 是**会话症状**
    #     （bot.py 见 token.emergency 就自杀），必须走会话阶梯，**绝不能**只重启容器
    #     —— 容器本来就在跑，`compose up -d` 是空转。
    Reset-RepairState
    $r = Invoke-Repair @{ crash_loop = 'x'; session_bad = 'x' } 1
    T 'crash_loop+session_bad 首轮 -> 仍未满 grace，不动' (-not $r.tried)
    $r = Invoke-Repair @{ crash_loop = 'x'; session_bad = 'x' } $ripeRound
    T 'crash_loop+session_bad 满 grace -> 修' ($r.tried)
    T '  动作里必须有 renew_token.py（修根因）' (($r.actions -join ' ') -match 'renew_token')
    T '  动作里不许出现 compose（那是空转）' (($r.actions -join ' ') -notmatch 'docker compose up -d')

    # 5) 容器问题留 **1 轮**缓冲 —— 滤掉"部署重建容器"那 ~15 秒的瞬态
    #    （2026-10-02 亲历：grace=0 时我自己的部署会被自己的监控当成故障去修）
    Reset-RepairState
    $r = Invoke-Repair @{ container_down = 'x' } 1
    T '容器不在 首轮 -> 留 1 轮缓冲，不动' (-not $r.tried)
    $r = Invoke-Repair @{ container_down = 'x' } 2
    T '容器不在 第 2 轮 -> 修' ($r.tried)
    T '  修复动作 = docker compose up -d' (($r.actions -join ' ') -match 'docker compose up -d')
    T '  容器问题**不**走 renew_token' (($r.actions -join ' ') -notmatch 'renew_token')

    # 6) 冷却：直接摆好"刚修过"的状态去撞冷却闸门
    #    （不能靠连续调用来撞：修完会 Reset IssueFirstSeen，下一轮又"未满门槛"，
    #      根本走不到冷却那一行 —— 这个顺序本身就是 Invoke-Repair 的设计）
    Reset-RepairState
    $script:IssueFirstSeen['session_bad'] = 1
    $script:LastRepairAt = (Get-Date).AddSeconds(-10)      # 10 秒前刚修过
    $r = Invoke-Repair @{ session_bad = 'x' } $ripeRound
    T '冷却期内不重复修' ((-not $r.tried) -and ($r.note -match '冷却中'))

    # 7) 一小时上限
    Reset-RepairState
    $script:IssueFirstSeen['session_bad'] = 1
    $script:LastRepairAt = (Get-Date).AddSeconds(-99999)
    $script:RepairTimes = @(1..$MaxRepairsPerHour | ForEach-Object { Get-Date })
    $r = Invoke-Repair @{ session_bad = 'x' } $ripeRound
    T '一小时达上限 -> 停手交人' ((-not $r.tried) -and ($r.note -match '上限'))

    # 8) 哨兵：bot.paused / .no_autorepair 存在时必须一律不动
    #    （哨兵检查在门槛检查**之前**，所以第 1 轮就该被拦住）
    foreach ($case in @(@('GUARD=1`nPAUSED=0', 'bot.paused'), @('GUARD=0`nPAUSED=1', '.no_autorepair'))) {
        function Invoke-SshCmd {
            param([string]$Cmd, [int]$TimeoutSec = 0)
            return @{ rc = 0; out = $case[0] }
        }
        Reset-RepairState
        $r = Invoke-Repair @{ container_down = 'x' } 1
        T "哨兵 $($case[1]) 存在 -> 不修" (-not $r.tried)
    }

    # 9) "只闪一轮"的窗口残留不许当成一次恢复通知
    #    （2026-10-02 00:53 实测：crash_loop 只剩启动前的重启，窗口滑过去就消失）
    T '清除且只开过 1 轮 -> 判为残留，不通知' (Test-FlashOnly @() @('crash_loop') @{ crash_loop = 1 })
    T '清除且开过 2 轮 -> 是真恢复，要通知' (-not (Test-FlashOnly @() @('crash_loop') @{ crash_loop = 2 }))
    T '有新问题 -> 绝不是残留' (-not (Test-FlashOnly @('session_bad') @('crash_loop') @{ crash_loop = 1 }))
    T '什么都没变 -> 不是残留（也就不用通知）' (-not (Test-FlashOnly @() @() @{}))
    T '两个一起清、其中开过 3 轮 -> 要通知' (-not (Test-FlashOnly @() @('a', 'b') @{ a = 1; b = 3 }))
    T '两个一起清、都只 1 轮 -> 残留' (Test-FlashOnly @() @('a', 'b') @{ a = 1; b = 1 })

    # 10) 门槛与退出通知的时序冲突（2026-10-02 08:55 实测）
    #     新问题可修复但还没熟 -> 必须留下来等，不能退
    T '新可修复问题 + 未满门槛 -> 留下来等' (Test-WaitForGrace @('session_bad') '刚出现，未满 3 轮 -> 先观察不动手' $true)
    T '新可修复问题 + 已到门槛 -> 不等（当轮就修）' (-not (Test-WaitForGrace @('session_bad') '冷却中（距上次修复 12 s < 300 s）' $true))
    T '新问题不可修复 -> 不等，直接通知' (-not (Test-WaitForGrace @('loop_error_traceback') '刚出现，未满 3 轮 -> 先观察不动手' $true))
    T 'AutoRepair 关 -> 永远不等（保持只读语义）' (-not (Test-WaitForGrace @('session_bad') '刚出现，未满 3 轮 -> 先观察不动手' $false))

    # 11) ⚠️ 2026-10-02 12:47 实测：续期事件只存在一轮，门槛必须是 0
    #
    # ⚠️ 必须先把 ssh 桩复位！case 8 的 foreach 结束后，`Invoke-SshCmd` 会**留在最后
    # 一轮的状态**（`GUARD=0` = 哨兵存在）⇒ 后面每个 `Invoke-Repair` 都被哨兵挡掉、
    # 全部假失败。**自检的用例之间不隔离** —— 这是这个脚手架第三次踩同一类坑
    # （前两次：`$AutoRepair` 没打开、DryRun 写进真实审计日志）。
    function Invoke-SshCmd {
        param([string]$Cmd, [int]$TimeoutSec = 0)
        return @{ rc = 0; out = "GUARD=1`nPAUSED=1" }
    }
    Reset-RepairState
    $r = Invoke-Repair @{ renew_emergency = 'x' } 1
    T "续期事件首轮 -> 立刻修（grace 0）[$($r.note)]" ($r.tried)
    T '  动作里必须有 renew_token.py' (($r.actions -join ' ') -match 'renew_token')
    T '  续期事件**不**走 compose' (($r.actions -join ' ') -notmatch 'docker compose up -d')
    Reset-RepairState
    $r = Invoke-Repair @{ renew_failure = 'x'; session_bad = 'x' } 1
    T "续期事件+会话同时来 -> 首轮就修 [$($r.note)]" ($r.tried)
    T '  且仍走会话阶梯（renew_token 打头）' (($r.actions -join ' ') -match 'renew_token')

    Write-Output ""
    Write-Output "[self-test] PASS=$script:selfPass FAIL=$script:selfFail"
    if ($script:selfFail -gt 0) { exit 1 }
    exit 0
}

# -- main loop -------------------------------------------------------------
$deadline  = (Get-Date).AddMinutes($MaxMinutes)
$prev      = @{}
$first     = $true
$round     = 0
$sshFails  = 0

Write-Output "[watch] interval=${IntervalSec}s max=${MaxMinutes}min window=$Window"
if ($AutoRepair) {
    $mode = if ($DryRun) { 'AUTO-REPAIR (DryRun：只记录不执行)' } else { 'AUTO-REPAIR (会自己修)' }
    Write-Output "[watch] target $Remote | $mode"
    Write-Output "[watch] 限流：同问题连续 $RepairGraceRounds 轮才动手 / 间隔 >= ${RepairCooldownSec}s / 每小时 <= $MaxRepairsPerHour 次"
    Write-Output "[watch] 哨兵：/app/bot.paused 或 /opt/dzmm/.no_autorepair 存在时一律不动"
} else {
    Write-Output "[watch] target $Remote | read-only (no writes, no restart, no group posts)"
}

while ((Get-Date) -lt $deadline) {
    $round++
    $kv  = Invoke-Probe
    $cur = Get-Issues $kv

    # A single ssh hiccup is not an incident; require two in a row.
    if ($cur.ContainsKey('ssh_fail')) {
        $sshFails++
        if ($sshFails -lt 2) { $cur = @{} }
    } else {
        $sshFails = 0
    }

    # 记"每个问题连续出现了几轮"，并留住在消失时它已经开了几轮。
    # 用来区分**真的恢复**和**只闪了一轮的窗口残留**：
    # 典型是监控刚启动时 `crash_loop` 的 5 分钟窗口里还留着启动前的重启，
    # 窗口一滑过去它就"自动消失"了（2026-10-02 00:53 的误报）。
    foreach ($k in $cur.Keys) {
        if ($script:IssueRounds.ContainsKey($k)) { $script:IssueRounds[$k] = [int]$script:IssueRounds[$k] + 1 }
        else { $script:IssueRounds[$k] = 1 }
    }
    foreach ($k in @($script:IssueRounds.Keys)) {
        if (-not $cur.ContainsKey($k)) {
            $script:ClearedAfter[$k] = [int]$script:IssueRounds[$k]
            $script:IssueRounds.Remove($k)
        }
    }

    $newKeys = @($cur.Keys  | Where-Object { -not $prev.ContainsKey($_) })
    $goneKeys = @($prev.Keys | Where-Object { -not $cur.ContainsKey($_) })
    $changed = ($newKeys.Count -gt 0) -or ($goneKeys.Count -gt 0)

    # ---- 自动修复：在"退出通知"之前先自救 ----
    # 修好了 -> 继续盯着（不吵屋主）；修不好 -> 退出通知（exit 1）。
    $rep = Invoke-Repair $cur $round
    if ($rep.tried) {
        Write-Output ""
        Write-Output "  [auto-repair] $($rep.note)"
        foreach ($a in $rep.actions) { Write-Output "    - $a" }
        if ($DryRun) {
            Write-Output "[watch] (DryRun) 未执行任何动作 -> 继续观察。"
            $first = $false; $prev = $cur
            Start-Sleep -Seconds $IntervalSec
            continue
        }
        $cur = $rep.after
        $newKeys = @($cur.Keys  | Where-Object { -not $prev.ContainsKey($_) })
        $goneKeys = @($prev.Keys | Where-Object { -not $cur.ContainsKey($_) })
        $changed = ($newKeys.Count -gt 0) -or ($goneKeys.Count -gt 0)
        if (-not $rep.ok) {
            Show-Report '自动修复失败 -- 需要你介入' $cur $prev
            Write-Output "[watch] 自救没成 -> 退出通知。"
            exit 1
        }
        if (-not $changed) {
            Write-Output "[watch] 自动修复成功 -> 继续盯着（不打扰你）。"
            $first = $false; $prev = $cur
            Start-Sleep -Seconds $IntervalSec
            continue
        }
    } elseif ($rep.note -and $cur.Count -gt 0) {
        Write-Output "[watch] 自动修复未执行：$($rep.note)"
    }

    # ⚠️ 门槛与"退出通知"的时序冲突：只要新问题里有可修复项、且当前只是在等门槛，
    # 就必须留下来等它熟 —— 否则门槛永远等不到，自动修复等于没接线。
    if (Test-WaitForGrace $newKeys $rep.note $AutoRepair) {
        Write-Output "[watch] 新问题可修复、正在等门槛（$($rep.note)）-> 留下继续盯，先不退出。"
        $first = $false
        $prev  = $cur
        Start-Sleep -Seconds $IntervalSec
        continue
    }

    if ($first) {
        # Round 1 is ALWAYS a baseline, even when problems are already open: a
        # pre-existing issue is not a "change". Exiting here would mean the watch
        # (a) can never observe the recovery it was started to catch, and
        # (b) hot-loops whenever a relay relaunches it mid-incident (exit ->
        #     relaunch -> exit ...), which is exactly what happened 2026-10-01 21:36.
        if ($cur.Count -gt 0) {
            Show-Report 'BASELINE: issues already open, watching for change' $cur @{}
            Write-Output "[watch] baseline recorded; staying up to catch change/recovery."
        } else {
            Write-Output "[watch] round 1: baseline clean."
        }
    } elseif ($changed) {
        # ⚠️ "只报清除"且被清除的问题**只存在过 1 轮** -> 不当成一次恢复通知。
        # 为什么这能精确命中"启动残留"：任何**新**问题出现时 newKeys>0 会立刻退出通知，
        # 所以能走到"只有 goneKeys"这一步的，必然都是**基线里就带着的**问题。
        # 典型场景（2026-10-02 00:53 实测）：监控刚重启时 `crash_loop` 的 5 分钟窗口
        # 里还留着启动前的两次重启，第 2 轮窗口一滑过去它就"自动消失"，
        # 于是白报一条 RECOVERED 并把监控退掉。
        $flashOnly = Test-FlashOnly $newKeys $goneKeys $script:ClearedAfter
        if ($flashOnly) {
            Write-Output "[watch] $($goneKeys -join ',') 只存在过 1 轮 -> 判为启动残留，不作为恢复通知，继续盯着。"
            $first = $false
            $prev  = $cur
            Start-Sleep -Seconds $IntervalSec
            continue
        }
        $title = if ($newKeys.Count -gt 0) { 'STATE CHANGE: PROBLEM' } else { 'STATE CHANGE: RECOVERED' }
        Show-Report $title $cur $prev
        Write-Output "[watch] state changed on round $round -> exit so the caller is notified."
        exit 0
    }

    $first = $false
    $prev  = $cur

    if ($round % 5 -eq 0) {
        Write-Output "[watch] round $round ok ($(Get-Date -Format 'HH:mm:ss'))"
    }
    Start-Sleep -Seconds $IntervalSec
}

Write-Output "[watch] watched $MaxMinutes min, no anomaly."
exit 0
