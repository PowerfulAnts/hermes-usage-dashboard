# verify_install.ps1 - check an installed usage-dashboard matches expectations.
#
# Checks under <hermes>\plugins\usage-dashboard\dashboard\:
#   manifest.json | plugin_api.py | sources.py   (and NO legacy backend\ tree)
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

$dash    = Join-Path $HermesDir "plugins\usage-dashboard\dashboard"
$desktop = Join-Path $HermesDir "desktop-plugins\usage-dashboard"

# item description -> full expected path ("!" prefix = must NOT exist)
$checks = @(
    @{ Name = "manifest.json";  Path = Join-Path $dash "manifest.json" },
    @{ Name = "plugin_api.py";  Path = Join-Path $dash "plugin_api.py" },
    @{ Name = "sources.py";     Path = Join-Path $dash "sources.py" },
    @{ Name = "!legacy backend tree"; Path = Join-Path $dash "backend" },
    @{ Name = "plugin.js";      Path = Join-Path $desktop "plugin.js" }
)

$missing = 0
foreach ($c in $checks) {
    if ($c.Name.StartsWith("!")) {
        if (Test-Path $c.Path) {
            Write-Host "STALE   $($c.Name.Substring(1)) still present at $($c.Path) (from a v2.x install; run install.ps1 to remove)" -ForegroundColor Red
            $missing++
        } else {
            Write-Host "OK      no legacy backend tree" -ForegroundColor Green
        }
        continue
    }
    if (Test-Path $c.Path) { Write-Host "OK      $($c.Name)" -ForegroundColor Green }
    else                   { Write-Host "MISSING $($c.Name)  (expected at $($c.Path))" -ForegroundColor Red; $missing++ }
}

Write-Host ""
if ($missing -gt 0) { Die 2 "$missing check(s) failed." }

Write-Host "All checks passed for $HermesDir" -ForegroundColor Green
exit 0
