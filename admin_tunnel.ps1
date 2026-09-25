<#
.SYNOPSIS
    Open an SSH tunnel to the DZMM ops dashboard and launch a browser.

.DESCRIPTION
    The dashboard has NO public address on purpose: it is published on the
    server's loopback only, so the ONLY way in is an SSH tunnel.

    A very common misunderstanding, so it is stated plainly here:
    http://127.0.0.1:8090 means "port 8090 on MY OWN computer". That port does
    not exist until this tunnel is running. Close the tunnel and the URL dies.

    This script starts ssh DETACHED (Start-Process), so it keeps running after
    you close this PowerShell window. Use -Stop to close it.

    IMPORTANT: THIS FILE MUST STAY ASCII-ONLY.
    Windows PowerShell 5.1 reads a BOM-less script using the system ANSI code
    page (GBK here). Chinese bytes then swallow the following quote and the
    file fails to parse. Same trap the repo documents for .bat and deploy.ps1.

.EXAMPLE
    .\admin_tunnel.ps1              # open tunnel + browser
    .\admin_tunnel.ps1 -Port 9090   # local port 9090 instead of 8090
    .\admin_tunnel.ps1 -Stop        # close the tunnel
#>
[CmdletBinding()]
param(
    [int]$Port = 8090,
    [string]$Remote = 'root@47.237.152.187',
    [int]$RemotePort = 8090,
    [switch]$Stop,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$needle = "$Port`:127.0.0.1:$RemotePort"

function Get-TunnelProcs {
    Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($needle) }
}

function Test-LocalPort([int]$p) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $p)
        $c.Close()
        return $true
    } catch {
        return $false
    }
}

if ($Stop) {
    $procs = @(Get-TunnelProcs)
    if ($procs.Count -eq 0) {
        Write-Host "no tunnel for $needle is running"
        exit 0
    }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force
        Write-Host "stopped ssh pid $($p.ProcessId)"
    }
    exit 0
}

if (Test-LocalPort $Port) {
    Write-Host "local port $Port is ALREADY answering."
    $existing = @(Get-TunnelProcs)
    if ($existing.Count -gt 0) {
        Write-Host "tunnel already up (ssh pid $($existing[0].ProcessId)) - just open the URL."
    } else {
        Write-Host "note: something else is listening on $Port (not our tunnel)."
        Write-Host "pick another local port:  .\admin_tunnel.ps1 -Port 9090"
    }
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
    exit 0
}

Write-Host "starting tunnel: 127.0.0.1:$Port -> $Remote`:$RemotePort"
# Start-Process = detached: it survives this window being closed.
# (ssh -f is unreliable on Windows OpenSSH - it can hang forever.)
Start-Process -FilePath 'ssh' `
    -ArgumentList @('-N', '-o', 'ExitOnForwardFailure=yes',
                    '-o', 'ServerAliveInterval=30',
                    '-L', "$Port`:127.0.0.1:$RemotePort", $Remote) `
    -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if (Test-LocalPort $Port) { break }
}

if (-not (Test-LocalPort $Port)) {
    Write-Host ""
    Write-Host "FAILED: port $Port still not answering after 20s."
    Write-Host "  * can you ssh at all?      ssh $Remote 'echo ok'"
    Write-Host "  * is the dashboard up?     ssh $Remote 'curl -s -m 5 http://127.0.0.1:8090/healthz'"
    Write-Host "  * local port in use?       .\admin_tunnel.ps1 -Port 9090"
    Write-Host "  * detach not allowed here? run it in the foreground instead:"
    Write-Host "      ssh -N -L $Port`:127.0.0.1:$RemotePort $Remote"
    exit 1
}

Write-Host ""
Write-Host "OK  tunnel is up.  http://127.0.0.1:$Port"
Write-Host "    (started detached via Start-Process, so the usual terminal does not"
Write-Host "     need to stay open; check any time by running this script again -"
Write-Host "     it will say 'ALREADY answering'.)"
Write-Host "    stop it later with:  .\admin_tunnel.ps1 -Stop"
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
