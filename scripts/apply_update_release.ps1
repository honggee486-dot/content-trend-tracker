[CmdletBinding()]
param([Parameter(Position = 0)][string]$BranchName)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Utf8Encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8Encoding
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$DefaultBranch = "main"
$AllowedSourceBranchPrefix = "work/"
$EnginePath = Join-Path $PSScriptRoot "apply_update.ps1"
$OriginalBranch = $null
$GitExe = $null
$LastCommand = ""

function Invoke-GitChecked {
    param(
        [string[]]$Arguments = @(),
        [switch]$Quiet,
        [switch]$AllowFailure
    )

    $commandParts = @($script:GitExe) + @($Arguments)
    $script:LastCommand = ($commandParts -join " ").Trim()
    $previousErrorActionPreference = $ErrorActionPreference
    $lines = @()
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $script:GitExe @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
        $lines = @($output | ForEach-Object { [string]$_ })
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if (-not $Quiet) {
        $lines | ForEach-Object { Write-Host $_ }
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "명령이 실패했습니다(종료 코드 $exitCode): $($script:LastCommand)"
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $lines }
}

function One-Line([object]$Result, [string]$Description) {
    $lines = @($Result.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -ne 1) {
        throw "$Description 값을 하나로 확인하지 못했습니다."
    }
    return $lines[0].Trim()
}

function Get-WorkingTreeStatus {
    return Invoke-GitChecked -Arguments @(
        'status', '--porcelain=v1', '--untracked-files=all'
    ) -Quiet
}

function Assert-CleanWorkingTree {
    $status = Get-WorkingTreeStatus
    if (@($status.Output).Count -gt 0) {
        $status.Output | ForEach-Object { Write-Host $_ }
        throw "로컬 미커밋 또는 미추적 변경이 있습니다. 자동 stash나 덮어쓰기는 하지 않습니다."
    }
}

function Restore-OriginalBranch {
    if (-not $OriginalBranch -or $OriginalBranch -eq $DefaultBranch) { return }

    $status = Get-WorkingTreeStatus
    if (@($status.Output).Count -gt 0) {
        Write-Host "[주의] 적용 실패 후 작업 트리가 변경되어 원래 브랜치로 자동 복귀하지 않았습니다." -ForegroundColor Yellow
        return
    }

    $current = Invoke-GitChecked -Arguments @(
        'symbolic-ref', '--quiet', '--short', 'HEAD'
    ) -Quiet -AllowFailure
    if ($current.ExitCode -eq 0) {
        $currentName = One-Line $current "현재 브랜치"
        if ($currentName -eq $OriginalBranch) { return }
    }

    $restore = Invoke-GitChecked -Arguments @(
        'switch', $OriginalBranch
    ) -Quiet -AllowFailure
    if ($restore.ExitCode -eq 0) {
        Write-Host "[안내] 릴리스 적용 실패 후 원래 작업 브랜치로 복귀했습니다: $OriginalBranch"
    }
    else {
        Write-Host "[주의] 원래 작업 브랜치로 자동 복귀하지 못했습니다: $OriginalBranch" -ForegroundColor Yellow
    }
}

$exitCode = 1
try {
    if (-not (Test-Path -LiteralPath $EnginePath -PathType Leaf)) {
        throw "최종 반영 엔진을 찾을 수 없습니다: $EnginePath"
    }

    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        throw "Git for Windows를 찾을 수 없습니다."
    }
    $script:GitExe = $git.Source

    $root = [IO.Path]::GetFullPath((
        One-Line (
            Invoke-GitChecked -Arguments @('rev-parse', '--show-toplevel') -Quiet
        ) '저장소 루트'
    ))
    if (-not [string]::Equals($root, $ProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "프로젝트 Git 저장소 루트에서 실행해야 합니다."
    }

    $OriginalBranch = One-Line (
        Invoke-GitChecked -Arguments @(
            'symbolic-ref', '--quiet', '--short', 'HEAD'
        ) -Quiet
    ) '현재 브랜치'

    Assert-CleanWorkingTree

    if ($OriginalBranch -ne $DefaultBranch) {
        if (-not $OriginalBranch.StartsWith(
            $AllowedSourceBranchPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw (
                "릴리스 적용은 $DefaultBranch 또는 깨끗한 " +
                "$AllowedSourceBranchPrefix* 브랜치에서 시작해야 합니다: $OriginalBranch"
            )
        }

        $mainCheck = Invoke-GitChecked -Arguments @(
            'rev-parse', '--verify', "refs/heads/$DefaultBranch^{commit}"
        ) -Quiet -AllowFailure
        if ($mainCheck.ExitCode -ne 0) {
            throw "로컬 $DefaultBranch 브랜치를 찾을 수 없습니다."
        }

        Write-Host "[안내] 현재 작업 브랜치가 깨끗하므로 릴리스 검증을 위해 $DefaultBranch 으로 안전하게 전환합니다."
        Invoke-GitChecked -Arguments @('switch', $DefaultBranch) -Quiet | Out-Null
        Assert-CleanWorkingTree
    }

    $powerShellExe = Join-Path $PSHOME "pwsh.exe"
    if (-not (Test-Path -LiteralPath $powerShellExe -PathType Leaf)) {
        $powerShellExe = Join-Path $PSHOME "powershell.exe"
    }
    if (-not (Test-Path -LiteralPath $powerShellExe -PathType Leaf)) {
        throw "현재 PowerShell 실행 파일을 찾을 수 없습니다."
    }

    $engineArgs = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $EnginePath
    )
    if (-not [string]::IsNullOrWhiteSpace($BranchName)) {
        $engineArgs += @('-BranchName', $BranchName)
    }

    & $powerShellExe @engineArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Restore-OriginalBranch
    }
}
catch {
    Write-Host ""
    Write-Host "[실패] 릴리스 적용 준비를 중단했습니다." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($LastCommand) {
        Write-Host "마지막 명령: $LastCommand" -ForegroundColor DarkYellow
    }
    Restore-OriginalBranch
    $exitCode = 1
}

exit $exitCode
