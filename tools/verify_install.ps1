# verify_install.ps1 - check an installed usage-dashboard matches expectations.
#
# Checks under <hermes>\plugins\usage-dashboard\dashboard\:
#   manifest.json | plugin_api.py | backend\sources.py | backend\adapters\*.py count > 0
# and <hermes>\desktop-plugins\usage-dashboard\: plugin.js exists.
#
# Usage: powershell -NoProfile -File tools\verify_install.ps1 [-HermesDir <path>]
# Exit codes: 0 all OK | 1 Hermes dir missing | 2 any item MISSING

param(
    # Hermes install directory; defaults to %LOCALAPPDATA%\hermes.
    [string]$HermesDir = ""
)

$ErrorActionPreference = "Stop"

function Die($code, $msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit $code }

if (-not $HermesDir) {
    $candidate = Join-Path $env:LOCALAPPDATA "hermes"
    if (Test-Path $candidate) { $HermesDir = $candidate }
    else { Die 1 "Hermes directory not found at '$candidate'. Pass -HermesDir '<path>'." }
}
if (-not (Test-Path $HermesDir)) { Die 1 "Hermes directory '$HermesDir' does not exist." }

$dash   = Join-Path $HermesDir "plugins\usage-dashboard\dashboard"
$desktop = Join-Path $HermesDir "desktop-plugins\usage-dashboard"

# item description -> full expected path (or special check)
$checks = @(
    @{ Name = "manifest.json";              Path = Join-Path $dash "manifest.json" },
    @{ Name = "plugin_api.py";              Path = Join-Path $dash "plugin_api.py" },
    @{ Name = "backend\sources.py";         Path = Join-Path $dash "backend\sources.py" },
    @{ Name = "backend\adapters (count>0)"; Path = "" },  # handled below
    @{ Name = "plugin.js";                  Path = Join-Path $desktop "plugin.js" }
)

$missing = 0
foreach ($c in $checks) {
    if ($c.Name -eq "backend\adapters (count>0)") {
        $adapters = Get-ChildItem -Path (Join-Path $dash "backend\adapters") `
                                  -Filter "*.py" -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -notlike "__*" }
        $n = @($adapters).Count
        if ($n -gt 0) { Write-Host "OK      backend\adapters ($n module(s))" -ForegroundColor Green }
        else          { Write-Host "MISSING backend\adapters (no .py modules found)" -ForegroundColor Red; $missing++ }
        continue
    }
    if (Test-Path $c.Path) { Write-Host "OK      $($c.Name)" -ForegroundColor Green }
    else                   { Write-Host "MISSING $($c.Name)  (expected at $($c.Path))" -ForegroundColor Red; $missing++ }
}

Write-Host ""
if ($missing -gt 0) {
    Write-Host "$missing item(s) MISSING - install is incomplete." -ForegroundColor Red
    exit 2
}
Write-Host "All checks passed - installation looks complete." -ForegroundColor Green
exit 0
