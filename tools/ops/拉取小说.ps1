<#
  拉取云端公演小说到本地
  ------------------------------------------------------------------
  用法（在本目录下）：
      powershell -ExecutionPolicy Bypass -File .\拉取小说.ps1
      powershell -ExecutionPolicy Bypass -File .\拉取小说.ps1 -Open     # 拉完打开目录

  云端存放位置：/opt/dzmm/novels/<群名>/<小说标题>_<时间戳>.md
  本地存放位置：.\novels\<群名>\<同名文件>
#>
param(
    [switch]$Open,        # 拉完用资源管理器打开目录
    [switch]$Quiet        # 只拉取，不打印清单
)

$ErrorActionPreference = "Stop"
$Srv    = "root@47.237.152.187"
$Remote = "/opt/dzmm/novels"
$Local  = Join-Path $PSScriptRoot "novels"

if (-not (Test-Path $Local)) { New-Item -ItemType Directory -Path $Local | Out-Null }

Write-Host "从 $Srv 拉取小说 ..." -ForegroundColor Cyan
# 远程 glob 由服务器端展开，避免中文/括号路径在本地拼接出错
scp -r -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=20 `
    "${Srv}:$Remote/*" "$Local\"
if ($LASTEXITCODE -ne 0) {
    Write-Host "拉取失败（scp exit $LASTEXITCODE）——检查网络/SSH 是否可用" -ForegroundColor Red
    exit 1
}

$files = Get-ChildItem $Local -Recurse -File |
    Where-Object { $_.Name -notlike "._*" -and $_.Name -ne ".DS_Store" } |
    Sort-Object LastWriteTime -Descending

if (-not $Quiet) {
    Write-Host ""
    Write-Host ("本地共 " + $files.Count + " 篇：") -ForegroundColor Green
    $files | Select-Object `
        @{n = "文件";   e = { $_.Name } },
        @{n = "群";     e = { $_.Directory.Name } },
        @{n = "大小KB"; e = { [math]::Round($_.Length / 1KB, 1) } },
        @{n = "行数";   e = { (Get-Content $_.FullName -Encoding UTF8).Count } },
        LastWriteTime | Format-Table -AutoSize

    if ($files.Count -gt 0) {
        Write-Host "最新一篇：" -ForegroundColor Yellow
        Write-Host ("  " + $files[0].FullName) -ForegroundColor Yellow
    }
}

if ($Open) { explorer $Local }
