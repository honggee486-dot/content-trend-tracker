[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [ValidateRange(1024, 65535)]
    [int]$Port = 8518
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$StateRoot = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Join-Path ([IO.Path]::GetTempPath()) 'content-trend-tracker'
} else {
    Join-Path $env:LOCALAPPDATA 'content-trend-tracker'
}
$RuntimeStatePath = Join-Path $StateRoot 'app_runtime.json'
$UpdateRequestPath = Join-Path $StateRoot 'app_update_request.json'

function Read-JsonObject {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Same-Path {
    param([string]$Left, [string]$Right)
    try {
        $leftPath = [IO.Path]::GetFullPath($Left)
        $rightPath = [IO.Path]::GetFullPath($Right)
    }
    catch {
        return $false
    }
    return [string]::Equals(
        $leftPath,
        $rightPath,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Get-ProcessStartTicks {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return 0L }
    try {
        return [long](Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToUniversalTime().Ticks
    }
    catch {
        return 0L
    }
}

function Test-ProcessIdentity {
    param([int]$ProcessId, [long]$ExpectedTicks)
    $actual = Get-ProcessStartTicks -ProcessId $ProcessId
    if ($actual -le 0) { return $false }
    if ($ExpectedTicks -le 0) { return $true }
    return $actual -eq $ExpectedTicks
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    if ($ProcessId -le 0 -or $ProcessId -eq $PID) { return }
    $taskkill = Get-Command taskkill.exe -ErrorAction SilentlyContinue
    if ($taskkill) {
        & $taskkill.Source /PID $ProcessId /T /F *> $null
        return
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Test-PortAvailable {
    param([int]$LocalPort)
    $listener = $null
    try {
        $listener = New-Object Net.Sockets.TcpListener(
            [Net.IPAddress]::Loopback,
            $LocalPort
        )
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($listener) {
            try { $listener.Stop() } catch { }
        }
    }
}

function Get-PortOwnerPid {
    param([int]$LocalPort)
    try {
        $row = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction Stop |
            Select-Object -First 1
        if ($row) { return [int]$row.OwningProcess }
    }
    catch { }
    return 0
}

$state = Read-JsonObject -Path $RuntimeStatePath
if (-not $state) {
    if (Test-PortAvailable -LocalPort $Port) {
        Write-Host '[완료] 등록된 content-trend-tracker 앱이 없습니다.'
        exit 0
    }
    $ownerPid = Get-PortOwnerPid -LocalPort $Port
    throw "상태 파일 없이 포트 $Port 이 사용 중입니다. 임의 종료하지 않았습니다. 점유 PID: $ownerPid"
}
if (-not (Same-Path -Left ([string]$state.project_root) -Right $ProjectRoot)) {
    throw '등록된 앱의 프로젝트 경로가 현재 저장소와 다릅니다.'
}
if ([int]$state.port -ne $Port) {
    throw "등록된 앱 포트가 예상값과 다릅니다: $($state.port)"
}

$stopped = $false
$supervisorPid = [int]$state.supervisor_pid
$supervisorTicks = [long]$state.supervisor_start_ticks
if (Test-ProcessIdentity -ProcessId $supervisorPid -ExpectedTicks $supervisorTicks) {
    Write-Host "[INFO] 앱 관리자와 자식 프로세스 종료 · PID $supervisorPid"
    Stop-ProcessTree -ProcessId $supervisorPid
    $stopped = $true
}

Start-Sleep -Milliseconds 300
$streamlitPid = [int]$state.streamlit_pid
$streamlitTicks = [long]$state.streamlit_start_ticks
if (Test-ProcessIdentity -ProcessId $streamlitPid -ExpectedTicks $streamlitTicks) {
    Write-Host "[INFO] 남아 있는 등록 Streamlit 종료 · PID $streamlitPid"
    Stop-ProcessTree -ProcessId $streamlitPid
    $stopped = $true
}

$deadline = [DateTime]::UtcNow.AddSeconds(15)
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-PortAvailable -LocalPort $Port) { break }
    Start-Sleep -Milliseconds 200
}
if (-not (Test-PortAvailable -LocalPort $Port)) {
    $ownerPid = Get-PortOwnerPid -LocalPort $Port
    throw "등록 프로세스를 종료했지만 포트 $Port 이 해제되지 않았습니다. 점유 PID: $ownerPid"
}

Remove-Item -LiteralPath $RuntimeStatePath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $UpdateRequestPath -Force -ErrorAction SilentlyContinue
if ($stopped) {
    Write-Host '[완료] content-trend-tracker 앱을 정확히 종료했습니다.'
} else {
    Write-Host '[완료] 등록 프로세스는 이미 종료되어 상태 파일만 정리했습니다.'
}
exit 0
