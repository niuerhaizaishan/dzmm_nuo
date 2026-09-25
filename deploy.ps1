<#
.SYNOPSIS
    [production] DZMM bot deploy / ops script. Target container dzmm-bot,
    remote dir /opt/dzmm -- IN-PLACE production deployment.
.DESCRIPTION
    Uses passwordless SSH (root@47.237.152.187).

    IMPORTANT: THIS FILE MUST STAY ASCII-ONLY.
    Windows PowerShell 5.1 reads a BOM-less script using the system ANSI code
    page (GBK here). Chinese bytes then swallow the following quote/brace and
    the file fails to parse ("Unexpected token '{'"). Same failure mode the
    repo already documents for .bat files -- it applies to .ps1 as well.
    Keep comments and messages in English; Chinese goes in Python output.

    "upload" only copies code (recursive, keeps directory layout).
    "deploy" also rebuilds and restarts the container.

    Excluded: .env, token JSON, logs, data/, novels/, tests/, tools/, probe/,
    __pycache__. Credentials live only in restricted files on the server and
    are never overwritten from here.
.EXAMPLE
    .\deploy.ps1 -Action upload   # copy code only (no build, no start)
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
    [ValidateSet('deploy','upload','compose','status','logs','rollback','sync','diff','health')]
    [string]$Action    = 'deploy',
    [string]$Remote    = 'root@47.237.152.187',
    [string]$RemoteDir = '/opt/dzmm',
    [string]$LocalDir  = $PSScriptRoot,
    [int]$LogLines     = 50
)

$ErrorActionPreference = 'Stop'

# Resolve the local repo root ONCE, and never let it be blank.
# A blank base silently turns Substring(0) into "the full path", which then
# becomes a remote path with a drive colon in it -- scp reads that colon as a
# separator and creates a literal "D:" directory on the server. That happened
# once; the guard in Push-CodeFiles now makes it a hard failure instead.
function Resolve-BaseDir {
    $candidates = @($LocalDir, $PSScriptRoot, (Get-Location).Path)
    foreach ($c in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($c)) {
            return ([System.IO.Path]::GetFullPath($c)).TrimEnd('\', '/')
        }
    }
    throw "cannot resolve local base directory"
}
$script:BaseDir = Resolve-BaseDir

# Directories never uploaded (relative to repo root).
# tests/tools/probe are local-dev only; the container does not need them.
$ExcludeDirNames = @(
    '__pycache__', '.git', '.pytest_cache', '.pytest-tmp',
    'tests', 'tools', 'probe', 'data', 'novels', 'docs', '_tmp'
)

function Remote-Path([string]$rel) {
    return ($Remote + ':' + $RemoteDir + '/' + $rel)
}

function Get-RelPath([string]$full) {
    # Normalize separators so the prefix compare is reliable, then FAIL LOUDLY
    # if the path is not under the base dir. Never fall back to returning the
    # absolute path -- that is what created the "D:" directory.
    $norm = $full -replace '/', '\'
    $prefix = $script:BaseDir + '\'
    if ($norm.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        return $norm.Substring($prefix.Length)
    }
    if ($norm.Equals($script:BaseDir, [StringComparison]::OrdinalIgnoreCase)) {
        return ''
    }
    throw "path is outside base dir: '$full' (base '$script:BaseDir')"
}

function Test-Excluded([string]$rel) {
    $parts = $rel -split '[\\/]'
    for ($i = 0; $i -lt ($parts.Count - 1); $i++) {
        if ($ExcludeDirNames -contains $parts[$i]) { return $true }
    }
    return $false
}

# NEVER upload these by name.
# docker-compose.yml: the dev copy (repo root) compose is the ISOLATED one
# (container_name: dzmm-bot). Uploading it to /opt/dzmm would overwrite the
# production compose and then `up -d` would create a SECOND container there --
# a 1.8G host with ~700M free cannot run two Playwright bots, so this would very
# likely OOM the production bot. The production compose is applied deliberately
# from docker-compose.prod.yml (see the 'compose' action).
$ExcludeFileNames = @('docker-compose.yml')

function Get-CodeFiles {
    # -ErrorAction SilentlyContinue is required: a few sandbox-restricted dirs
    # (.pytest-tmp, pytest-cache-files-*) make -Recurse throw
    # UnauthorizedAccessException, and $ErrorActionPreference='Stop' would abort
    # the whole deploy.
    Get-ChildItem $script:BaseDir -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
        $rel = Get-RelPath $_.FullName
        if (Test-Excluded $rel) { return $false }
        if ($ExcludeFileNames -contains $_.Name) { return $false }
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

function Push-CodeFiles {
    Write-Host "  base dir: $script:BaseDir"
    $files = @(Get-CodeFiles)
    if ($files.Count -eq 0) { throw "no deployable code files under $script:BaseDir" }

    Invoke-Ssh "mkdir -p '$RemoteDir'"

    # Create remote sub-directories first (package dirs included).
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
        # Hard guard: a colon or leading separator here means scp would create a
        # bogus directory on the server (see Resolve-BaseDir comment).
        if ($rel -match ':' -or $rel -match '^[/\\]') {
            throw "refusing to upload suspicious remote path: '$rel'"
        }
        Invoke-Scp $f.FullName (Remote-Path $rel)
        Write-Host "  uploaded: $rel"
    }
    return $files.Count
}

switch ($Action) {
    'upload' {
        # Copy code only: no image build, no service start.
        # Needed because the host is small (1.87G RAM, production already using
        # ~763M) -- building/starting a second Playwright container here would
        # fight production for memory. Verify with a one-off container instead.
        Write-Host "[upload] copying code to $RemoteDir (no build, no start)"
        $n = Push-CodeFiles
        Write-Host "OK uploaded $n files. NO service was started."
    }
    'compose' {
        # Apply docker-compose.prod.yml to the remote docker-compose.yml.
        # Deliberate, validated, backed up -- never a blind overwrite.
        $src = Join-Path $script:BaseDir 'docker-compose.prod.yml'
        if (-not (Test-Path $src)) { throw "missing $src" }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        Write-Host "[compose] backup remote compose -> docker-compose.yml.bak-$stamp"
        Invoke-Ssh "cd $RemoteDir && cp -a docker-compose.yml docker-compose.yml.bak-$stamp"
        Invoke-Scp $src (Remote-Path 'docker-compose.yml')
        Write-Host "[compose] validate..."
        Invoke-Ssh "cd $RemoteDir && docker compose config -q && echo COMPOSE-OK"
        Write-Host "[compose] recreate container..."
        # Build too: a compose change usually rides along with code changes, and
        # `up -d` alone recreates from the OLD image -- that silently skipped a
        # whole deploy once (the container came up without the new code).
        Invoke-Ssh "cd $RemoteDir && docker compose build dzmm-bot && docker compose up -d dzmm-bot"
        Write-Host "OK compose applied (backup: docker-compose.yml.bak-$stamp)"
    }
    'deploy' {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $tar   = "backup-code-$stamp.tar.gz"

        Write-Host "[1/3] backup remote code -> $tar"
        # Whole-tree backup (minus runtime data) so subdirs can be restored.
        Invoke-Ssh "cd $RemoteDir && tar czf $tar --exclude=./data --exclude=./novels --exclude=./__pycache__ --exclude='*.log' --exclude='*.tar.gz' . 2>/dev/null || true"

        Write-Host "[2/3] uploading code files (recursive, keeps layout)..."
        $n = Push-CodeFiles
        Write-Host "  total: $n files"

        Write-Host "[3/3] rebuild container on cloud..."
        Invoke-Ssh "cd $RemoteDir && docker compose build dzmm-bot && docker compose up -d dzmm-bot"
        Write-Host "OK deploy done (backup: $RemoteDir/$tar)"
    }
    'status' {
        Invoke-Ssh "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'; docker inspect -f 'restarts={{.RestartCount}} started={{.State.StartedAt}}' dzmm-bot; echo '--- health log tail ---'; tail -5 $RemoteDir/check_health.log"
    }
    'logs' {
        Invoke-Ssh "docker logs --tail $LogLines dzmm-bot 2>&1"
    }
    'rollback' {
        $latest = (Invoke-Ssh "ls -t $RemoteDir/backup-code-*.tar.gz 2>/dev/null | head -1").Trim()
        if (-not $latest) { throw "no backup-code-*.tar.gz found on cloud" }
        Write-Host "rollback to: $latest"
        Invoke-Ssh "cd $RemoteDir && tar xzf $latest && docker compose build dzmm-bot && docker compose up -d dzmm-bot"
        Write-Host "OK rollback done"
    }
    'sync' {
        if (-not (Test-Path $script:BaseDir)) { New-Item -ItemType Directory -Force $script:BaseDir | Out-Null }
        Invoke-Scp -Recurse (Remote-Path '.') $script:BaseDir
        Write-Host "OK synced from cloud to $script:BaseDir"
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
        Invoke-Ssh "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'; docker inspect -f 'restarts={{.RestartCount}} started={{.State.StartedAt}}' dzmm-bot; docker stats --no-stream dzmm-bot --format 'CPU={{.CPUPerc}} MEM={{.MemUsage}} ({{.MemPerc}})'; docker exec dzmm-bot ps aux | grep -E 'bot.py|renew_token|check_bot_health|heartbeat' | grep -v grep"

        Write-Host "`n=== [2/4] login state + active health check ==="
        Invoke-Ssh "docker exec dzmm-bot python headless_relogin.py --check && echo LOGIN-OK || echo LOGIN-FAIL; docker exec dzmm-bot python check_bot_health.py; tail -3 /opt/dzmm/check_health.log"

        Write-Host "`n=== [3/4] bot main loop log ==="
        Invoke-Ssh "docker logs --tail 30 dzmm-bot 2>&1"

        Write-Host "`n=== [4/4] health + renew logs ==="
        Invoke-Ssh "echo '--- check_health.log ---'; tail -12 /opt/dzmm/check_health.log; echo '--- renew_token.log ---'; tail -10 /opt/dzmm/renew_token.log"
    }
}

