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
    [ValidateSet('deploy','upload','compose','status','logs','rollback','sync','diff','health','exec')]
    [string]$Action    = 'deploy',
    # -Action exec: run one shell command on the server (with the ssh retry loop).
    # This exists because the handshake to this host fails intermittently, so
    # ad-hoc `ssh ...` from a shell prompt is a coin flip; everything through
    # here gets the retries. Read-only / idempotent commands only, please.
    [string]$Cmd       = '',
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
# config.json: the per-group / per-bot config is SERVER-SIDE STATE (the ops UI
# will write it). Uploading the repo copy would silently revert whatever was
# changed in the UI. It is already excluded by the extension filter (.json is
# not uploaded), but listing it here makes the intent explicit and survives
# someone later adding "json" to that filter.
$ExcludeFileNames = @('docker-compose.yml', 'config.json')

function Get-CodeFiles {
    # -ErrorAction SilentlyContinue is required: a few sandbox-restricted dirs
    # (.pytest-tmp, pytest-cache-files-*) make -Recurse throw
    # UnauthorizedAccessException, and $ErrorActionPreference='Stop' would abort
    # the whole deploy.
    Get-ChildItem $script:BaseDir -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
        $rel = Get-RelPath $_.FullName
        if (Test-Excluded $rel) { return $false }
        if ($ExcludeFileNames -contains $_.Name) { return $false }
        # prompts/*.md is RUNTIME code (Prompt externalisation, P8 step 3).
        # It must ship; other .md files stay local (they are notes, and the
        # repo root has plenty of them).
        $isPrompt = $rel -like 'prompts\*'
        # js/css: the admin UI is no longer JS-free (P12-D added
        # admin/static/app.js). Forgetting them here means the page loads but
        # the script 404s -- and, worse, it fails silently (the page still works
        # without JS, so nobody notices the feature is missing).
        # png/jpg: P12-L/N public images (deploy/img/: the butler portrait plus
        # the four room-layout shots). They live only in the directory Caddy
        # serves, so without them a fresh deploy = "images 404, no picture in
        # the group" and the log only shows a platform rejection.
        ($_.Name -match '\.(py|sh|yml|yaml|service|js|css|png|jpg)$') -or
        ($isPrompt -and ($_.Name -match '\.md$')) -or
        # .dockerignore MUST ship: the image is built on the server from this
        # tree, and without the ignore file `COPY . .` bakes .env /
        # .dzmm_token.json / profiles.json into the image (2026-09-27 audit, P0).
        ($_.Name -in @('Dockerfile', 'requirements.txt', '.dockerignore'))
    }
}

# SSH cipher is PINNED on purpose (2026-09-26):
#   The path to this host corrupts some SSH cipher/MAC combinations.
#   Symptoms seen: the handshake hangs right after KEXINIT ("Connection to
#   ... port 22: Connection timed out" although the TCP port is open and the
#   web UI answers 200), and with aes256-ctr it fails with
#   "Corrupted MAC on input". aes128-gcm@openssh.com (an AEAD cipher) works
#   reliably. Without this pin, roughly every other deploy attempt died in
#   the handshake -- which looked like "the server is down" but was not.
#
# 2026-09-26 (P12-E deploy) -- IMPORTANT correction to the note above:
#   the loss is **intermittent and not tied to one combo**. On that day
#   aes128-gcm failed 4 times in a row while
#   `aes128-ctr + hmac-sha2-256-etm@openssh.com` succeeded -- and then that
#   combo also failed 2 of 3 times. So no cipher is "the reliable one";
#   the only reliable strategy is **retry**. $SshAttempts / $SshRetryDelaySec
#   below implement that. The pin stays (it is a good default and the AEAD
#   suite avoids the "Corrupted MAC" class of failures).
$SshCipherOpts = @('-o', 'Ciphers=aes128-gcm@openssh.com')
# Attempt count / delay are tunable via env vars (measured 2026-09-26: at the
# worst the handshake succeeded only ~1 in 5 tries; 5 attempts is not enough,
# especially for a long-running `-Action exec`). Hence the knob.
#   $env:DZMM_SSH_ATTEMPTS = 20 ; .\deploy.ps1 -Action upload
$SshAttempts = 10
$SshRetryDelaySec = 6
if ($env:DZMM_SSH_ATTEMPTS) {
    $n = 0
    if ([int]::TryParse($env:DZMM_SSH_ATTEMPTS, [ref]$n) -and $n -gt 0) { $SshAttempts = $n }
}
if ($env:DZMM_SSH_RETRY_DELAY) {
    $n = 0
    if ([int]::TryParse($env:DZMM_SSH_RETRY_DELAY, [ref]$n) -and $n -ge 0) { $SshRetryDelaySec = $n }
}

function Invoke-Ssh([string]$cmd) {
    # ServerAlive* : a long build/extract over one connection must not be dropped
    # as an idle socket.
    # Retry loop: every command routed through here is idempotent (upload /
    # build / up -d / status / logs), so a retry is safe. A failed attempt
    # usually dies in the handshake and never reaches the server at all.
    #
    # EAP dance: this script runs with $ErrorActionPreference='Stop', and under
    # PowerShell 5.1 a native command writing to stderr (which ssh does on every
    # failed attempt) becomes a TERMINATING error -- so the retry loop would
    # never get to its second iteration. Hence 'Continue' around the call.
    for ($i = 1; $i -le $SshAttempts; $i++) {
        $eap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            # `2>&1 | Out-String` (not a bare `2>&1`): with EAP='Continue' the
            # merged stderr records would still be PRINTED as red NativeCommandError
            # noise on every failed attempt -- the pipeline consumes them instead,
            # so only the retry lines above show up.
            $out = (& ssh @SshCipherOpts -o BatchMode=yes -o ConnectTimeout=15 `
                -o ServerAliveInterval=15 -o ServerAliveCountMax=4 $Remote "$cmd 2>&1" `
                2>&1 | Out-String)
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $eap
        }
        if ($code -eq 0) {
            if ($i -gt 1) { Write-Host "  [ssh] attempt $i OK (previous $($i-1) handshake failures)" }
            return $out
        }
        if ($i -lt $SshAttempts) {
            Write-Host "  [ssh] attempt $i failed, retrying in ${SshRetryDelaySec}s..."
            Start-Sleep -Seconds $SshRetryDelaySec
        }
    }
    throw "SSH failed after $SshAttempts attempts: $cmd"
}

function Invoke-Scp([string]$src, [string]$dst, [switch]$Recurse) {
    $scpArgs = @('-o', 'BatchMode=yes', '-o', 'ServerAliveInterval=15',
                 '-o', 'ServerAliveCountMax=4') + $SshCipherOpts
    if ($Recurse) { $scpArgs += '-r' }
    for ($i = 1; $i -le $SshAttempts; $i++) {
        $eap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            scp @scpArgs $src $dst 2>&1 | Out-String
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $eap
        }
        if ($code -eq 0) { return }
        if ($i -lt $SshAttempts) {
            Write-Host "  [scp] attempt $i failed, retrying in ${SshRetryDelaySec}s..."
            Start-Sleep -Seconds $SshRetryDelaySec
        }
    }
    throw "SCP failed after $SshAttempts attempts: $src -> $dst"
}

function Get-RelPaths([array]$files) {
    $rels = @()
    foreach ($f in $files) {
        $rel = (Get-RelPath $f.FullName) -replace '\\', '/'
        # Hard guard: a colon or leading separator here means the archive (or a
        # per-file scp) would create a bogus directory on the server.
        # See the Resolve-BaseDir comment for the incident this prevents.
        if ($rel -match ':' -or $rel -match '^[/\\]') {
            throw "refusing to upload suspicious remote path: '$rel'"
        }
        $rels += $rel
    }
    return $rels
}

function Push-WithTar([array]$rels) {
    # ONE archive over ONE connection instead of ~145 separate scp calls.
    # Rationale (both observed for real):
    #   * a single dropped scp aborted the whole upload half-way, leaving the
    #     remote tree partially updated;
    #   * 145 sequential connections took >120s, so the upload kept getting
    #     killed by command timeouts.
    $stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
    $tarName = "dzmm-upload-$stamp.tar.gz"
    $tarLocal = Join-Path $env:TEMP $tarName
    $listLocal = Join-Path $env:TEMP "dzmm-upload-$stamp.txt"
    # LF, no BOM: bsdtar treats a BOM/CR as part of the filename.
    [System.IO.File]::WriteAllText($listLocal, ($rels -join "`n"),
        (New-Object System.Text.UTF8Encoding($false)))
    try {
        tar -czf $tarLocal -C $script:BaseDir --files-from $listLocal
        if ($LASTEXITCODE -ne 0) { throw "local tar failed" }
        Invoke-Ssh "mkdir -p '$RemoteDir'"
        Invoke-Scp $tarLocal (Remote-Path $tarName)
        # Extract into place, then remove the archive so it never shows up in a
        # later `Get-CodeFiles` (it is not a .py/.yml, but keep the tree clean).
        Invoke-Ssh "cd '$RemoteDir' && tar xzf '$tarName' && rm -f '$tarName'"
    } finally {
        Remove-Item -Force $tarLocal, $listLocal -ErrorAction SilentlyContinue
    }
    Write-Host "  packed $($rels.Count) files into one archive and extracted remotely"
}

function Push-OneByOne([array]$files) {
    # Fallback for machines without tar.
    Invoke-Ssh "mkdir -p '$RemoteDir'"
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
        if ($rel -match ':' -or $rel -match '^[/\\]') {
            throw "refusing to upload suspicious remote path: '$rel'"
        }
        Invoke-Scp $f.FullName (Remote-Path $rel)
        Write-Host "  uploaded: $rel"
    }
}

function Push-CodeFiles {
    Write-Host "  base dir: $script:BaseDir"
    $files = @(Get-CodeFiles)
    if ($files.Count -eq 0) { throw "no deployable code files under $script:BaseDir" }
    $rels = Get-RelPaths $files
    # 2026-09-27 incident: when a deploy ran CONCURRENTLY with the full pytest
    # suite, Get-ChildItem aborted mid-walk and -ErrorAction SilentlyContinue
    # swallowed it, so only 42 files were enumerated. Production ended up
    # half-updated (old bot.py, new modules missing) while the log just said
    # "packed 42 files" and looked successful. Hard gate below: abort instead of
    # restarting on half a tree. NOTE: keep this file ASCII-only -- non-ASCII
    # literals in a .ps1 break the parser on this host.
    $names = @($rels | ForEach-Object { $_ -replace '\\', '/' })
    $need = @('bot.py', 'brains.py', 'runtime/obs.py', 'runtime/prompts.py',
              '.dockerignore')
    $miss = @($need | Where-Object { $names -notcontains $_ })
    $prompts = @($names | Where-Object { $_ -like 'prompts/*.md' }).Count
    if ($miss.Count -or $prompts -lt 30 -or $names.Count -lt 100) {
        $why = "missing=[{0}] prompts={1} total={2}" -f ($miss -join ','), $prompts, $names.Count
        throw "incomplete file list (enumeration interrupted?): $why -- aborted, nothing uploaded"
    }
    if (Get-Command tar -ErrorAction SilentlyContinue) {
        Push-WithTar $rels
    } else {
        Write-Host "  (tar not found, falling back to per-file scp)"
        Push-OneByOne $files
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
        # Credentials / PII are excluded as well: these tarballs pile up on the
        # server and used to contain .env, .dzmm_token.json, state.json and
        # profiles.json (2026-09-27 audit, P0). They are host state, not code --
        # a rollback must never bring them back.
        Invoke-Ssh "cd $RemoteDir && tar czf $tar --exclude=./data --exclude=./novels --exclude=./__pycache__ --exclude='*.log' --exclude='*.tar.gz' --exclude=./.env --exclude='./.env.*' --exclude=./.dzmm_token.json --exclude='./.dzmm_token.json.*' --exclude=./.dzmm_login.json --exclude='./.dzmm_login*.json' --exclude=./state.json --exclude=./profiles.json --exclude=./profile_scan.json --exclude='./pw_profile*' --exclude=./token.emergency --exclude=./bot.paused . 2>/dev/null || true"
        # Keep only the newest 10 code backups (they were never pruned: 49 of
        # them had accumulated by 2026-09-27).
        Invoke-Ssh "cd $RemoteDir && ls -1t backup-code-*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f; ls -1 backup-code-*.tar.gz 2>/dev/null | wc -l"

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
    'exec' {
        if (-not $Cmd) { throw "-Action exec needs -Cmd '<shell command>'" }
        Write-Host "[exec] $Cmd"
        Invoke-Ssh $Cmd | Write-Host
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
            # Null-safe: a file that is not on the server yet makes md5sum print
            # nothing, and calling .Trim() on that $null aborted the whole diff.
            $out = Invoke-Ssh "md5sum '$RemoteDir/$rel' 2>/dev/null | cut -d' ' -f1"
            $remoteMd5 = ''
            if ($out) { $remoteMd5 = ("$out").Trim() }
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

