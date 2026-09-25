<#
.SYNOPSIS
    [开发副本] 目标容器 dzmm-botapi / 远端目录 /opt/dzmm-botapi，与生产 dzmm-bot 隔离。
.SYNOPSIS
    DZMM bot one-click deploy / ops script (Windows PowerShell).
.DESCRIPTION
    Connects to the Alibaba Cloud Docker host over passwordless SSH
    (root@47.237.152.187). Actions: deploy, status, logs, rollback, sync, diff.
    "deploy" only syncs code + config (*.py / *.sh / *.yml / Dockerfile /
    requirements.txt / *.service). Excludes .env, token JSON, logs, data files.
.EXAMPLE
    .\deploy.ps1                  # deploy: backup remote -> scp upload -> rebuild
    .\deploy.ps1 -Action status   # container status + health log tail
    .\deploy.ps1 -Action logs     # bot logs (last 50 lines)
    .\deploy.ps1 -Action sync     # full pull from cloud to local
    .\deploy.ps1 -Action rollback # rollback to latest remote code backup
    .\deploy.ps1 -Action diff     # md5 diff local vs remote code
    .\deploy.ps1 -Action health   # full health check (container/process/logs/token)
#>
[CmdletBinding()]
param(
    [ValidateSet('deploy','status','logs','rollback','sync','diff','health')]
    [string]$Action    = 'deploy',
    [string]$Remote    = 'root@47.237.152.187',
    [string]$RemoteDir = '/opt/dzmm-botapi',
    [string]$LocalDir  = $PSScriptRoot,
    [int]$LogLines     = 50
)

$ErrorActionPreference = 'Stop'

function Remote-Path([string]$rel) {
    return ($Remote + ':' + $RemoteDir + '/' + $rel)
}

function Get-CodeFiles {
    Get-ChildItem $LocalDir -File | Where-Object {
        $_.Name -match '\.(py|sh|yml|yaml|service)$' -or
        $_.Name -in @('Dockerfile','requirements.txt')
    }
}

function Invoke-Ssh([string]$cmd) {
    ssh -o BatchMode=yes -o ConnectTimeout=15 $Remote "$cmd 2>&1"
    if ($LASTEXITCODE -ne 0) { throw "SSH failed: $cmd" }
}

function Invoke-Scp([string]$src, [string]$dst, [switch]$Recurse) {
    $scpArgs = @('-o','BatchMode=yes')
    if ($Recurse) { $scpArgs += '-r' }
    scp @scpArgs $src $dst
    if ($LASTEXITCODE -ne 0) { throw "SCP failed: $src -> $dst" }
}

switch ($Action) {
    'deploy' {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $tar   = "backup-code-$stamp.tar.gz"
        Write-Host "[1/3] backup remote code -> $tar"
        Invoke-Ssh "cd $RemoteDir && tar czf $tar *.py *.sh *.yml Dockerfile requirements.txt *.service 2>/dev/null || true"
        Write-Host "[2/3] upload code files..."
        $files = Get-CodeFiles
        if ($files.Count -eq 0) { throw "no deployable code files under $LocalDir" }
        foreach ($f in $files) {
            Invoke-Scp $f.FullName (Remote-Path $f.Name)
            Write-Host "  uploaded: $($f.Name)"
        }
        Write-Host "[3/3] rebuild container on cloud..."
        Invoke-Ssh "cd $RemoteDir && docker compose build dzmm-botapi && docker compose up -d dzmm-botapi"
        Write-Host "OK deploy done (backup: $RemoteDir/$tar)"
    }
    'status' {
        Invoke-Ssh "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'; docker inspect -f 'restarts={{.RestartCount}} started={{.State.StartedAt}}' dzmm-botapi; echo '--- health log tail ---'; tail -5 $RemoteDir/check_health.log"
    }
    'logs' {
        Invoke-Ssh "docker logs --tail $LogLines dzmm-botapi 2>&1"
    }
    'rollback' {
        $latest = (Invoke-Ssh "ls -t $RemoteDir/backup-code-*.tar.gz 2>/dev/null | head -1").Trim()
        if (-not $latest) { throw "no backup-code-*.tar.gz found on cloud" }
        Write-Host "rollback to: $latest"
        Invoke-Ssh "cd $RemoteDir && tar xzf $latest && docker compose build dzmm-botapi && docker compose up -d dzmm-botapi"
        Write-Host "OK rollback done"
    }
    'sync' {
        if (-not (Test-Path $LocalDir)) { New-Item -ItemType Directory -Force $LocalDir | Out-Null }
        Invoke-Scp -Recurse (Remote-Path '.') $LocalDir
        Write-Host "OK synced from cloud to $LocalDir"
    }
    'diff' {
        foreach ($f in Get-CodeFiles) {
            $localHash = (Get-FileHash $f.FullName -Algorithm MD5).Hash.ToLower()
            $remoteMd5 = (Invoke-Ssh "md5sum $RemoteDir/$($f.Name) 2>/dev/null | cut -d' ' -f1").Trim()
            if ($localHash -eq $remoteMd5) { $mark = '  = ' } else { $mark = '  <>' }
            Write-Host "$mark $($f.Name)  local=$localHash  remote=$remoteMd5"
        }
    }
    'health' {
        Write-Host "=== [1/4] container + resources + processes ==="
        Invoke-Ssh "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'; docker inspect -f 'restarts={{.RestartCount}} started={{.State.StartedAt}}' dzmm-botapi; docker stats --no-stream dzmm-botapi --format 'CPU={{.CPUPerc}} MEM={{.MemUsage}} ({{.MemPerc}})'; docker exec dzmm-botapi ps aux | grep -E 'bot.py|renew_token|check_bot_health|heartbeat' | grep -v grep"

        Write-Host "`n=== [2/4] login state + active health check ==="
        Invoke-Ssh "docker exec dzmm-botapi python headless_relogin.py --check && echo LOGIN-OK || echo LOGIN-FAIL; docker exec dzmm-botapi python check_bot_health.py; tail -3 /opt/dzmm-botapi/check_health.log"

        Write-Host "`n=== [3/4] bot main loop log ==="
        Invoke-Ssh "docker logs --tail 30 dzmm-botapi 2>&1"

        Write-Host "`n=== [4/4] health + renew logs ==="
        Invoke-Ssh "echo '--- check_health.log ---'; tail -12 /opt/dzmm-botapi/check_health.log; echo '--- renew_token.log ---'; tail -10 /opt/dzmm-botapi/renew_token.log"
    }
}

