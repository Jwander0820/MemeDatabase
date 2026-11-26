<#
.SYNOPSIS
  在與部署相同的 Linux／Python 3.12 基底中，產生含 SHA-256 的 pip 依賴鎖檔。
  預設沿用既有鎖定版本；-Upgrade 才更新符合 requirements*.txt 的間接依賴。
  僅修改 requirements-locks/，不安裝套件到本機 .venv，不讀取 .env 或館藏。
#>
[CmdletBinding()]
param([switch]$Upgrade)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Dockerfile = Get-Content -LiteralPath (Join-Path $ProjectRoot "Dockerfile") -Raw
$BaseMatch = [regex]::Match($Dockerfile, '(?m)^FROM (python:\S+) AS base\r?$')
if (-not $BaseMatch.Success) { throw "Dockerfile 找不到固定的 Python base 映像" }
$BaseImage = $BaseMatch.Groups[1].Value
$UpgradeFlag = if ($Upgrade) { "--upgrade" } else { "" }
$Compile = @'
set -eu
python -m pip install --disable-pip-version-check --quiet pip==26.3 pip-tools==7.7.0
mkdir -p requirements-locks
python -m piptools compile --quiet --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --pip-args='--only-binary=:all:' $LOCK_UPGRADE --output-file=requirements-locks/public-py312-linux.txt requirements.txt
for profile in admin mcp; do
  python -m piptools compile --quiet --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --pip-args='--only-binary=:all:' $LOCK_UPGRADE --constraint=requirements-locks/public-py312-linux.txt --output-file="requirements-locks/$profile-py312-linux.txt" "requirements-$profile.txt"
done
'@

& docker run --rm --mount "type=bind,source=$ProjectRoot,target=/workspace" --workdir /workspace `
    --env "LOCK_UPGRADE=$UpgradeFlag" --env "CUSTOM_COMPILE_COMMAND=.\scripts\lock_dependencies.ps1" `
    --env PIP_ROOT_USER_ACTION=ignore $BaseImage sh -c $Compile
if ($LASTEXITCODE -ne 0) { throw "產生依賴鎖檔失敗（exit $LASTEXITCODE）" }
