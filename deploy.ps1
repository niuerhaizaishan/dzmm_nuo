<#
.SYNOPSIS
    [开发副本] DZMM bot 部署/运维脚本。目标容器 dzmm-botapi / 远端目录 /opt/dzmm-botapi，
    与生产 dzmm-bot 完全隔离。
.DESCRIPTION
    通过免密 SSH（root@47.237.152.187）操作。
    "deploy" **递归**同步代码（*.py / *.sh / *.yml / *.yaml / *.service /
    Dockerfile / requirements.txt），**保留目录层级**（P4 起有 runtime/ 等包目录）。

    排除：.env、token JSON、日志、data/、novels/、tests/、tools/、probe/、__pycache__。
    凭据只存在于服务器上的受限文件里，**永不从本地覆盖**。
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

# 不上传的目录（相对仓库根）；tests/tools/probe 是本地开发用的，容器里不需要
$ExcludeDirNames = @(
    '__pycache__', '.git', '.pytest_cache', '.pytest-tmp',
    'tests', 'tools', 'probe', 'data', 'novels', 'docs', '_tmp'
)

function Remote-Path([string]$rel) {
    return ($Remote + ':' + $RemoteDir + '/' + $rel)
}

function Get-RelPath([string]$full) {
    return $full.Substring($LocalDir.Length).TrimStart('\', '/')
}

function Test-Excluded([string]$rel) {
    $parts = $rel -split '[\\/]'
    for ($i = 0; $i -lt ($parts.Count - 1); $i++) {
        if ($ExcludeDirNames -contains $parts[$i]) { return $true }
    }
    return $false
}

function Get-CodeFiles {
    # -ErrorAction SilentlyContinue 是必需的：本机存在几个沙箱受限目录
    # （.pytest-tmp / pytest-cache-files-* 等），-Recurse 撞上会抛
    # UnauthorizedAccessException，而 $ErrorActionPreference='Stop' 会直接中止部署。
    Get-ChildItem $LocalDir -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
        $rel = Get-RelPath $_.FullName
        if (Test-Excluded $rel) { return $false }
        ($_.Name -match '\.(py|sh|yml|yaml|service)$') -or
        ($_.Name -in @('Dockerfile', 'requirements.txt'))
    }
}

function Invoke-Ssh([string]$cmd) {
    ssh -o BatchMode=yes -o ConnectTimeout=15 $Remote "$cmd 2>&1"
    if ($LASTEXITCODE -ne 0) { throw "SSH failed: $cmd" }
}

function Invoke-Scp([string]$src, [string]$dst, [switch]$Recurse) {
    $scpArgs = @('-o', 'BatchMode=yes')
    if ($Recurse) { $scpArgs += '-r' }
    scp @scpArgs $src $dst
    if ($LASTEXITCODE -ne 0) { throw "SCP failed: $src -> $dst" }
}

switch ($Action) {
    'deploy' {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $tar   = "backup-code-$stamp.tar.gz"

        Write-Host "[1/3] backup remote code -> $tar"
        # 整目录备份（排除运行时数据），回滚时能还原子目录
        Invoke-Ssh "cd $RemoteDir && tar czf $tar --exclude=./data --exclude=./novels --exclude=./__pycache__ --exclude='*.log' --exclude='*.tar.gz' . 2>/dev/null || true"

        Write-Host "[2/3] upload code files (recursive, 保留目录层级)..."
        $files = @(Get-CodeFiles)
        if ($files.Count -eq 0) { throw "no deployable code files under $LocalDir" }

        # 先在远端建好所需子目录（含包目录）
        $dirs = $files | ForEach-Object {
            $d = Split-Path (Get-RelPath $_.FullName) -Parent
            if ($d) { $d -replace '\\', '/' }
        } | Where-Object { $_ } | Sort-Object -Unique
        foreach ($d in $dirs) {
            Invoke-Ssh "mkdir -p '$RemoteDir/$d'"
            Write-Host "  mkdir: $d"
        }

        foreach ($f in $files) {
            $rel = (Get-RelPath $f.FullName) -replace '\\', '/'
            Invoke-Scp $f.FullName (Remote-Path $rel)
            Write-Host "  uploaded: $rel"
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
            $rel = (Get-RelPath $f.FullName) -replace '\\', '/'
            $localHash = (Get-FileHash $f.FullName -Algorithm MD5).Hash.ToLower()
            $remoteMd5 = (Invoke-Ssh "md5sum '$RemoteDir/$rel' 2>/dev/null | cut -d' ' -f1").Trim()
            if ($localHash -eq $remoteMd5) { $mark = '  = ' } else { $mark = '  <>' }
            Write-Host "$mark $rel  local=$localHash  remote=$remoteMd5"
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
