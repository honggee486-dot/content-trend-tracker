[CmdletBinding()]
param([Parameter(Position = 0)][string]$BranchName)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$normalizedBranch = [string]$BranchName
if (-not [string]::IsNullOrWhiteSpace($normalizedBranch)) {
    $normalizedBranch = $normalizedBranch.Trim().Trim([char]34)
    if ($normalizedBranch.StartsWith("origin/", [StringComparison]::OrdinalIgnoreCase)) {
        $normalizedBranch = $normalizedBranch.Substring(7).Trim()
    }
    if ($normalizedBranch.StartsWith("refs/heads/", [StringComparison]::OrdinalIgnoreCase)) {
        $normalizedBranch = $normalizedBranch.Substring(11).Trim()
    }
}

$releasePath = Join-Path $PSScriptRoot "apply_update_release.ps1"
$workPath = Join-Path $PSScriptRoot "apply_update_work.ps1"

if (-not [string]::IsNullOrWhiteSpace($normalizedBranch) -and
    $normalizedBranch.StartsWith("work/", [StringComparison]::OrdinalIgnoreCase)) {
    & $workPath -BranchName $normalizedBranch
    return
}

if ([string]::IsNullOrWhiteSpace($normalizedBranch)) {
    & $releasePath
    return
}

& $releasePath -BranchName $normalizedBranch
