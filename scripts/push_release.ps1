<#
.SYNOPSIS
  把本機已匯出的公開發布版本打包，推送到 VPS 並在 VPS 上安裝（可選擇啟用）。

.DESCRIPTION
  流程：本機 bundle 產生 tar.gz -> scp 到遠端 /tmp -> ssh 以公開映像執行 install-bundle
        -> 刪除遠端與本機暫存檔。
  前提：
    - VPS 已有公開映像（預設 animememes-public-api）與發布目錄，且目錄權限允許 UID 10001 寫入。
    - ssh 認證完全交給使用者既有的 ssh config／agent；本腳本不處理任何密碼或 token。
  未加 -Activate 時只安裝並驗證，不切換 current.json；current.json 只由 activate 原子切換。
  -DryRun：只印出將執行的命令，本機 bundle 步驟也不執行，不產生任何檔案。

.EXAMPLE
  .\scripts\push_release.ps1 -Version 20261125-120000 -SshHost myvps -RemoteReleaseRoot /srv/animememes/releases -Activate
  .\scripts\push_release.ps1 -Version test -SshHost example -RemoteReleaseRoot /srv/releases -DryRun
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$SshHost,
    [Parameter(Mandatory = $true)][string]$RemoteReleaseRoot,
    [string]$LocalReleaseRoot = "data/releases",
    [string]$Image = "animememes-public-api",
    [switch]$Activate,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# 與 releases.VERSION_RE 相同規則，避免注入到遠端命令。
if ($Version -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$') {
    throw "無效的 Version：$Version"
}
if ($SshHost -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._@-]*$') {
    throw "無效的 SshHost：$SshHost"
}
if ($RemoteReleaseRoot -notmatch '^/[a-zA-Z0-9._/-]*$') {
    throw "RemoteReleaseRoot 必須是只含安全字元的絕對路徑：$RemoteReleaseRoot"
}
if ($Image -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._/:@-]*$') {
    throw "無效的 Image：$Image"
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LocalBundle = Join-Path ([System.IO.Path]::GetTempPath()) ("animememes-" + $Version + ".tar.gz")
$RemoteBundle = "/tmp/animememes-$Version.tar.gz"
$ActivateFlag = ""
if ($Activate) { $ActivateFlag = " --activate" }
$RemoteCommand = "docker run --rm --user 10001:10001 -v ${RemoteReleaseRoot}:/releases -v ${RemoteBundle}:/bundle.tar.gz:ro $Image python -m src.anime_memes.runtime install-bundle --root /releases /bundle.tar.gz$ActivateFlag"

function Invoke-Step([string]$Name, [scriptblock]$Action, [string]$Display) {
    Write-Host "[$Name] $Display"
    if ($DryRun) { return }
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "步驟失敗：$Name（結束碼 $LASTEXITCODE）"
    }
}

Push-Location $ProjectRoot
try {
    Invoke-Step "1/5 本機打包" { & $Python -m src.anime_memes.runtime bundle --root $LocalReleaseRoot --output $LocalBundle $Version } `
        "$Python -m src.anime_memes.runtime bundle --root $LocalReleaseRoot --output $LocalBundle $Version"
    Invoke-Step "2/5 上傳" { & scp $LocalBundle "${SshHost}:${RemoteBundle}" } "scp $LocalBundle ${SshHost}:${RemoteBundle}"
    Invoke-Step "3/5 遠端安裝" { & ssh $SshHost $RemoteCommand } "ssh $SshHost `"$RemoteCommand`""
    Invoke-Step "4/5 刪除遠端暫存檔" { & ssh $SshHost "rm -f $RemoteBundle" } "ssh $SshHost `"rm -f $RemoteBundle`""
    Invoke-Step "5/5 刪除本機暫存檔" { Remove-Item -LiteralPath $LocalBundle -Force; $global:LASTEXITCODE = 0 } "Remove-Item $LocalBundle"
}
finally {
    Pop-Location
    # 安裝失敗時第 4 步不會執行，這裡補刪遠端暫存檔（失敗不影響結果）。
    if (-not $DryRun) {
        try { & ssh $SshHost "rm -f $RemoteBundle" } catch { }
    }
    if (-not $DryRun -and (Test-Path -LiteralPath $LocalBundle)) {
        Remove-Item -LiteralPath $LocalBundle -Force -ErrorAction SilentlyContinue
    }
}
