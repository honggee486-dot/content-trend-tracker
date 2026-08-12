[CmdletBinding()]
param(
    [string[]]$Scenario = @('all')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$Harness = Join-Path $ProjectRoot 'scripts\agent_test_harness.py'

if (-not (Test-Path -LiteralPath $Harness -PathType Leaf)) {
    throw "Agent 테스트 하네스를 찾을 수 없습니다: $Harness"
}

$RepositoryRoot = (& git -C $ProjectRoot rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    throw '프로젝트 Git 저장소 루트를 확인하지 못했습니다.'
}
if (
    -not [string]::Equals(
        [IO.Path]::GetFullPath($RepositoryRoot.Trim()),
        $ProjectRoot,
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    throw '현재 경로가 content-trend-tracker 저장소 루트와 일치하지 않습니다.'
}

[string]$Python = ''
foreach ($Candidate in @(
    (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
    (Join-Path $ProjectRoot 'venv\Scripts\python.exe')
)) {
    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        $Python = $Candidate
        break
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($null -eq $PythonCommand) {
        throw '프로젝트 가상환경 또는 실행 가능한 Python을 찾을 수 없습니다.'
    }
    $Python = $PythonCommand.Source
}

Write-Host "[Agent Harness] Python: $Python"
Write-Host '[Agent Harness] 실제 DB·외부 API·Windows 스케줄러를 변경하지 않는 테스트를 시작합니다.'
# 지원 시나리오와 별칭의 검증은 Python 하네스를 단일 기준으로 사용합니다.
& $Python $Harness @Scenario
$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0) {
    Write-Host "[Agent Harness] 실패했습니다. 종료 코드: $ExitCode" -ForegroundColor Red
}
else {
    Write-Host '[Agent Harness] 선택한 시나리오를 모두 통과했습니다.' -ForegroundColor Green
}
exit $ExitCode
