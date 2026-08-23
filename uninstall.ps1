# uninstall.ps1 - remove the usage-dashboard plugin from a Hermes install.
#
# Safety: each folder is only deleted if it contains OUR manifest.json
# (name == "usage-dashboard"), so we never nuke someone else's plugin.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1 [-HermesDir <path>] [-DryRun]
# Exit codes: 0 ok | 1 Hermes dir not found | 5 safety check failed

param(
    [string]$HermesDir = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Die($code, $msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit $code }

if (-not $HermesDir) {
    $candidate = Join-Path $env:LOCALAPPDATA "hermes"
    if (Test-Path $candidate) { $HermesDir = $candidate }
    else { Die 1 "Hermes directory not found at '$candidate'. Pass -HermesDir '<path>'." }
}
if (-not (Test-Path $HermesDir)) { Die 1 "Hermes directory '$HermesDir' does not exist." }

$targets = @(
    (Join-Path $HermesDir "plugins\usage-dashboard"),
    (Join-Path $HermesDir "desktop-plugins\usage-dashboard")
)

function Is-Ours($dir) {
    # True only if $dir holds our manifest.json declaring name "usage-dashboard".
    $manifest = Join-Path $dir "manifest.json"
    if (-not (Test-Path $manifest)) { return $false }
    try {
        $json = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
        return ($json.name -eq "usage-dashboard")
    } catch { return $false }
}

$removedAny = $false
foreach ($t in $targets) {
    if (-not (Test-Path $t)) {
        Write-Host "--  not installed: $t" -ForegroundColor DarkGray
        continue
    }
    if (-not (Is-Ours $t)) {
        Die 5 ("Safety check failed: '$t' exists but has no usage-dashboard manifest.json - refusing to delete.")
    }
    if ($DryRun) {
        Write-Host "[dry-run] would remove $t"
    } else {
        Remove-Item -LiteralPath $t -Recurse -Force
        Write-Host "removed $t" -ForegroundColor Green
    }
    $removedAny = $true
}

if (-not $removedAny -and -not $DryRun) {
    Write-Host "Nothing to uninstall - usage-dashboard is not installed." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Restart Hermes once - backend mounts are refreshed at process start" -ForegroundColor Yellow
exit 0
