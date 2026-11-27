<#
.SYNOPSIS
  把本機已匯出的公開發布版本打包，推送到 VPS 並在 VPS 上安裝（可選擇啟用）。

.DESCRIPTION
  流程：查詢遠端縮圖雜湊 -> 本機 bundle 產生增量 tar.gz -> scp 到遠端 /tmp -> ssh 以公開映像執行 install-bundle
        -> 刪除遠端與本機暫存檔。
  前提：
    - VPS 已有公開映像（預設 memedatabase-public-api）與發布目錄，且目錄權限允許 UID 10001 寫入。
    - ssh 認證完全交給使用者既有的 ssh config／agent；本腳本不處理任何密碼或 token。
  未加 -Activate 時只安裝並驗證，不切換 current.json；current.json 只由 activate 原子切換。
  資料庫與 manifest 每次完整打包，縮圖預設僅傳遠端缺少的檔案；-Full 改用完整發布包。
  遠端公開映像須支援 media-inventory；舊映像請先更新，或使用 -Full。
  -Prune：安裝（與啟用）完成後，在 VPS 執行 prune（保留最新三版與目前啟用版本），不需停服務。
  -KeepRecent：搭配 -Prune 指定保留的近期版本數，0 只保留啟用版。
  -DryRun：只印出將執行的命令，本機 bundle 步驟也不執行，不產生任何檔案。

.EXAMPLE
  .\scripts\push_release.ps1 -Version 20261125-120000 -SshHost myvps -RemoteReleaseRoot /srv/memedatabase/releases -Activate
  .\scripts\push_release.ps1 -Version 20261125-120000 -SshHost myvps -RemoteReleaseRoot /srv/memedatabase/releases -Activate -Prune
  .\scripts\push_release.ps1 -Version test -SshHost example -RemoteReleaseRoot /srv/releases -DryRun
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$SshHost,
    [Parameter(Mandatory = $true)][string]$RemoteReleaseRoot,
    [string]$LocalReleaseRoot = "data/releases",
    [string]$Image = "memedatabase-public-api",
    [switch]$Activate,
    [switch]$Full,
    [switch]$Prune,
    [ValidateRange(0, 20)][int]$KeepRecent = 3,
    [string]$RemoteComposeDir = "/opt/memedatabase",
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
if ($RemoteComposeDir -notmatch '^/[a-zA-Z0-9._/-]*$') {
    throw "RemoteComposeDir 必須是只含安全字元的絕對路徑：$RemoteComposeDir"
}
if ($Image -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._/:@-]*$') {
    throw "無效的 Image：$Image"
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LocalBundle = Join-Path ([System.IO.Path]::GetTempPath()) ("memedatabase-" + $Version + ".tar.gz")
$LocalInventory = Join-Path ([System.IO.Path]::GetTempPath()) ("memedatabase-inventory-" + [guid]::NewGuid().ToString('N') + ".json")
$RemoteBundle = "/tmp/memedatabase-$Version.tar.gz"
$ActivateFlag = ""
if ($Activate) { $ActivateFlag = " --activate" }
$RemoteCommand = "docker run --rm --user 10001:10001 -v ${RemoteReleaseRoot}:/releases -v ${RemoteBundle}:/bundle.tar.gz:ro $Image python -m src.anime_memes.runtime install-bundle --root /releases /bundle.tar.gz$ActivateFlag"
$InventoryCommand = "docker run --rm --user 10001:10001 -v ${RemoteReleaseRoot}:/releases:ro $Image python -m src.anime_memes.runtime media-inventory --root /releases"

# 公開 API 只讀啟用版，清理不需停服務。
$PruneCommand = "docker run --rm --user 10001:10001 -v ${RemoteReleaseRoot}:/releases $Image python -m src.anime_memes.runtime prune --root /releases --keep-recent $KeepRecent"
$Total = 5
if (-not $Full) { $Total++ }
if ($Prune) { $Total++ }

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
    $Step = 1
    $BundleArgs = @('-m', 'src.anime_memes.runtime', 'bundle', '--root', $LocalReleaseRoot, '--output', $LocalBundle, $Version)
    if (-not $Full) {
        Invoke-Step "$Step/$Total 查詢遠端縮圖" {
            $InventoryJson = & ssh $SshHost $InventoryCommand
            if ($LASTEXITCODE -ne 0) { throw '無法取得遠端縮圖清單；請更新 VPS 公開映像或使用 -Full。' }
            [System.IO.File]::WriteAllText($LocalInventory, ($InventoryJson -join "`n"), [System.Text.UTF8Encoding]::new($false))
        } "ssh $SshHost `"$InventoryCommand`""
        $Step++
        $BundleArgs += @('--inventory', $LocalInventory)
    }
    Invoke-Step "$Step/$Total 本機打包" { & $Python @BundleArgs } "$Python $($BundleArgs -join ' ')"
    $Step++
    Invoke-Step "$Step/$Total 上傳" { & scp $LocalBundle "${SshHost}:${RemoteBundle}" } "scp $LocalBundle ${SshHost}:${RemoteBundle}"
    $Step++
    Invoke-Step "$Step/$Total 遠端安裝" { & ssh $SshHost $RemoteCommand } "ssh $SshHost `"$RemoteCommand`""
    $Step++
    Invoke-Step "$Step/$Total 刪除遠端暫存檔" { & ssh $SshHost "rm -f $RemoteBundle" } "ssh $SshHost `"rm -f $RemoteBundle`""
    $Step++
    Invoke-Step "$Step/$Total 刪除本機暫存檔" { Remove-Item -LiteralPath $LocalBundle -Force; $global:LASTEXITCODE = 0 } "Remove-Item $LocalBundle"
    if ($Prune) {
        $Step++
        Invoke-Step "$Step/$Total 遠端清理舊版本" { & ssh $SshHost $PruneCommand } "ssh $SshHost `"$PruneCommand`""
    }
}
finally {
    Pop-Location
    # 流程失敗時可能尚未清除遠端暫存檔，這裡補刪（失敗不影響原始結果）。
    if (-not $DryRun) {
        try { & ssh $SshHost "rm -f $RemoteBundle" } catch { }
    }
    if (-not $DryRun -and (Test-Path -LiteralPath $LocalBundle)) {
        Remove-Item -LiteralPath $LocalBundle -Force -ErrorAction SilentlyContinue
    }
    if (-not $DryRun -and (Test-Path -LiteralPath $LocalInventory)) {
        Remove-Item -LiteralPath $LocalInventory -Force -ErrorAction SilentlyContinue
    }
}
