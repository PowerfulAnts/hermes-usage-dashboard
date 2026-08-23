# install.ps1 -- install the usage-dashboard plugin into a Hermes install.
#
# Copies:
#   dashboard/plugin_api.py            -> <hermes>/plugins/usage-dashboard/dashboard/plugin_api.py
#   dashboard/manifest.json            -> <hermes>/plugins/usage-dashboard/dashboard/manifest.json
#   backend/  (recursively)            -> <hermes>/plugins/usage-dashboard/dashboard/backend/
#   desktop-plugins/usage-dashboard/plugin.js
#                                      -> <hermes>/desktop-plugins/usage-dashboard/plugin.js
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 [-HermesDir <path>] [-DryRun]
#
# Exit codes: 0 = ok, 1 = Hermes dir not found, 2 = repo source files missing,
#             3 = copy failed, 4 = post-copy verification failed.

param(
    # Override the Hermes installation directory instead of auto-detecting.
    [string]$HermesDir = "",
    # Print what would be copied without touching anything.
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }
function Die($code, $msg)  { Write-Host "ERROR: $msg" -ForegroundColor Red; exit $code }

# ---------------------------------------------------------------- locate Hermes
if (-not $HermesDir) {
    $candidate = Join-Path $env:LOCALAPPDATA "hermes"
    if (Test-Path $candidate) {
        $HermesDir = $candidate
    } else {
        Die 1 ("Hermes directory not found at '$candidate'. " +
               "Install Hermes first, or pass the location explicitly: " +
               "-HermesDir '<path-to-hermes>'")
    }
}
if (-not (Test-Path $HermesDir)) {
    Die 1 "Hermes directory '$HermesDir' does not exist."
}
Write-Step "Hermes dir: $HermesDir"

# ------------------------------------------------------------- source manifest
# repo-relative source -> destination relative to $HermesDir
$pluginRoot   = Join-Path $HermesDir "plugins\usage-dashboard\dashboard"
$backendDest  = Join-Path $pluginRoot "backend"
$desktopDest  = Join-Path $HermesDir "desktop-plugins\usage-dashboard"

$sources = @(
    @{ Src = Join-Path $RepoRoot "dashboard\plugin_api.py"; DstDir = $pluginRoot },
    @{ Src = Join-Path $RepoRoot "dashboard\manifest.json"; DstDir = $pluginRoot },
    @{ Src = Join-Path $RepoRoot "desktop-plugins\usage-dashboard\plugin.js"; DstDir = $desktopDest }
)
$backendSrc = Join-Path $RepoRoot "backend"

foreach ($s in $sources) {
    if (-not (Test-Path $s.Src)) {
        Die 2 "Source file missing in repo: $($s.Src)"
    }
}
if (-not (Test-Path (Join-Path $backendSrc "sources.py"))) {
    Die 2 "Source folder incomplete: $backendSrc\sources.py not found."
}

# ---------------------------------------------------------------------- copy
function Copy-One($src, $dstDir) {
    $dst = Join-Path $dstDir (Split-Path $src -Leaf)
    if ($DryRun) {
        Write-Host "  [dry-run] $src -> $dst"
        return
    }
    if (-not (Test-Path $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
}

Write-Step "Copying plugin files"
foreach ($s in $sources) { Copy-One $s.Src $s.DstDir }

# Backend tree: copy recursively, then strip any __pycache__ dirs that came along.
if ($DryRun) {
    Write-Host "  [dry-run] $backendSrc -> $backendDest (recursive)"
} else {
    if (Test-Path $backendDest) {
        # Start clean so removed adapters do not linger from a previous install.
        Remove-Item -LiteralPath $backendDest -Recurse -Force
    }
    New-Item -ItemType Directory -Path $backendDest -Force | Out-Null
    Copy-Item -Path (Join-Path $backendSrc "*") -Destination $backendDest -Recurse -Force
    Get-ChildItem -Path $backendDest -Recurse -Directory -Filter "__pycache__" |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
}
Write-Ok "backend copied"

if ($DryRun) {
    Write-Step "Dry run complete -- nothing was written."
    exit 0
}

# ------------------------------------------------------------ post-copy verify
Write-Step "Verifying installed files"
$mustExist = @(
    (Join-Path $pluginRoot "manifest.json"),
    (Join-Path $pluginRoot "plugin_api.py"),
    (Join-Path $backendDest "sources.py"),
    (Join-Path $desktopDest "plugin.js")
)
$failed = $false
foreach ($f in $mustExist) {
    if (Test-Path $f) { Write-Ok (Split-Path $f -Leaf) }
    else              { Write-Warn2 "MISSING $f"; $failed = $true }
}
$adapterCount = @(Get-ChildItem -Path (Join-Path $backendDest "adapters") -Filter "*.py" -ErrorAction SilentlyContinue |
                  Where-Object { $_.Name -notlike "__*" }).Count
if ($adapterCount -gt 0) { Write-Ok "adapters: $adapterCount adapter module(s)" }
else                     { Write-Warn2 "MISSING adapters (count = 0)"; $failed = $true }

# Best-effort byte-compile of the installed backend (skipped if no python).
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    Write-Step "Byte-compiling installed backend (python -m compileall)"
    & python -m compileall -q $backendDest 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok "compiled cleanly" }
    else { Write-Warn2 "compileall reported issues (best-effort check, continuing)" }
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -m compileall -q $backendDest 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok "compiled cleanly" }
    else { Write-Warn2 "compileall reported issues (best-effort check, continuing)" }
} else {
    Write-Host "    --  python not found, skipping compile check"
}

if ($failed) { Die 4 "Verification failed -- see MISSING lines above." }

Write-Host ""
Write-Host "Installed usage-dashboard into $HermesDir" -ForegroundColor Green
Write-Host "Restart Hermes once -- backend mounts at process start" -ForegroundColor Yellow
exit 0
