$ErrorActionPreference = 'Stop'
# Repo-root-relative parse check; works from any checkout location.
$repoRoot = Split-Path -Parent $PSScriptRoot
foreach ($f in @('install.ps1','uninstall.ps1','tools\verify_install.ps1')) {
    $errs = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $repoRoot $f), [ref]$null, [ref]$errs)
    if ($errs -and $errs.Count) { Write-Output "$f PARSE-FAIL"; $errs | ForEach-Object { Write-Output $_.Message }; exit 1 }
    else { Write-Output "$f AST-OK" }
}
