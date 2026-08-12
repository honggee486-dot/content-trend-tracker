[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$BranchName,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedSha,

    [Parameter(Mandatory = $true)]
    [int]$ParentPid,

    [Parameter(Mandatory = $true)]
    [long]$ParentStartTicks,

    [Parameter(Mandatory = $true)]
    [int]$SupervisorPid,

    [Parameter(Mandatory = $true)]
    [long]$SupervisorStartTicks,

    [Parameter(Mandatory = $true)]
    [int]$AppPort,

    [Parameter(Mandatory = $true)]
    [string]$RuntimeStatePath,

    [Parameter(Mandatory = $true)]
    [string]$RequestPath,

    [Parameter(Mandatory = $true)]
    [string]$StatusPath,

    [Parameter(Mandatory = $true)]
    [string]$LogPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Utf8Encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8Encoding
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding

$BranchPattern = '^work/\d+\.\d+\.\d+(?:[-._][A-Za-z0-9][A-Za-z0-9._-]*)?$'
$ShaPattern = '^[0-9a-fA-F]{40}$'
$Mutex = $null
$MutexAcquired = $false
$RequestWritten = $false

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload
    )
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $temporary = "$Path.tmp.$PID.$([Guid]::NewGuid().ToString('N'))"
    try {
        [IO.File]::WriteAllText(
            $temporary,
            ($Payload | ConvertTo-Json -Depth 8),
            $Utf8Encoding
        )
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Read-JsonObject {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Write-Status {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Message,
        [int]$ResultCode = -1
    )
    Write-JsonAtomic -Path $StatusPath -Payload ([ordered]@{
        status = $Status
        stage = $Stage
        branch_name = $BranchName
        expected_sha = $ExpectedSha.ToLowerInvariant()
        message = $Message
        result_code = $ResultCode
        supervisor_pid = $SupervisorPid
        streamlit_pid = $ParentPid
        port = $AppPort
        updated_at = [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:sszzz')
    })
}

function Get-ProcessStartTicks {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        return [long]$process.StartTime.ToUniversalTime().Ticks
    }
    catch {
        return 0L
    }
}

function Assert-ProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][long]$ExpectedTicks,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $actual = Get-ProcessStartTicks -ProcessId $ProcessId
    if ($actual -le 0) {
        throw "$Label 프로세스가 실행 중이 아닙니다. PID=$ProcessId"
    }
    if ($ExpectedTicks -gt 0 -and $actual -ne $ExpectedTicks) {
        throw "$Label PID가 다른 프로세스에 재사용되었습니다. PID=$ProcessId"
    }
}

try {
    $ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
    $RuntimeStatePath = [IO.Path]::GetFullPath($RuntimeStatePath)
    $RequestPath = [IO.Path]::GetFullPath($RequestPath)
    $StatusPath = [IO.Path]::GetFullPath($StatusPath)
    $LogPath = [IO.Path]::GetFullPath($LogPath)
    $BranchName = $BranchName.Trim()
    $ExpectedSha = $ExpectedSha.Trim().ToLowerInvariant()

    if ($BranchName -notmatch $BranchPattern) {
        throw "허용되지 않는 작업 브랜치명입니다: $BranchName"
    }
    if ($ExpectedSha -notmatch $ShaPattern) {
        throw '예상 커밋 SHA가 올바르지 않습니다.'
    }
    if ($AppPort -lt 1024 -or $AppPort -gt 65535) {
        throw "앱 포트가 올바르지 않습니다: $AppPort"
    }

    $Mutex = New-Object Threading.Mutex(
        $false,
        'Local\content-trend-tracker-web-update-request'
    )
    $MutexAcquired = $Mutex.WaitOne(0)
    if (-not $MutexAcquired) {
        throw '다른 웹 업데이트 요청이 이미 처리 중입니다.'
    }

    $runtime = Read-JsonObject -Path $RuntimeStatePath
    if (-not $runtime) {
        throw '관리형 앱 상태를 찾지 못했습니다. run_app.bat으로 앱을 다시 실행하세요.'
    }
    if ([IO.Path]::GetFullPath([string]$runtime.project_root) -ne $ProjectRoot) {
        throw '관리형 앱의 프로젝트 경로가 현재 저장소와 일치하지 않습니다.'
    }
    if ([int]$runtime.port -ne $AppPort) {
        throw '관리형 앱의 포트가 현재 요청과 일치하지 않습니다.'
    }
    if (
        [int]$runtime.supervisor_pid -ne $SupervisorPid -or
        [long]$runtime.supervisor_start_ticks -ne $SupervisorStartTicks -or
        [int]$runtime.streamlit_pid -ne $ParentPid -or
        [long]$runtime.streamlit_start_ticks -ne $ParentStartTicks
    ) {
        throw '앱 상태가 업데이트 버튼을 누른 시점과 달라졌습니다.'
    }

    Assert-ProcessIdentity `
        -ProcessId $SupervisorPid `
        -ExpectedTicks $SupervisorStartTicks `
        -Label '앱 관리자'
    Assert-ProcessIdentity `
        -ProcessId $ParentPid `
        -ExpectedTicks $ParentStartTicks `
        -Label 'Streamlit'

    $request = [ordered]@{
        schema_version = 1
        project_root = $ProjectRoot
        branch_name = $BranchName
        expected_sha = $ExpectedSha
        supervisor_pid = $SupervisorPid
        supervisor_start_ticks = $SupervisorStartTicks
        streamlit_pid = $ParentPid
        streamlit_start_ticks = $ParentStartTicks
        port = $AppPort
        requested_at = [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:sszzz')
    }
    Write-JsonAtomic -Path $RequestPath -Payload $request
    $RequestWritten = $true
    [IO.File]::WriteAllText(
        $LogPath,
        "웹 업데이트 요청을 앱 관리자에게 전달했습니다.`r`n" +
        "브랜치: $BranchName`r`n커밋: $ExpectedSha`r`n" +
        "Streamlit PID: $ParentPid`r`nSupervisor PID: $SupervisorPid`r`n",
        $Utf8Encoding
    )
    Write-Status `
        -Status 'waiting_for_app' `
        -Stage 'stop_app' `
        -Message '현재 응답을 마친 뒤 앱 관리자가 Streamlit을 종료하고 업데이트를 적용합니다.'

    Start-Sleep -Seconds 2
    Assert-ProcessIdentity `
        -ProcessId $ParentPid `
        -ExpectedTicks $ParentStartTicks `
        -Label 'Streamlit'
    Stop-Process -Id $ParentPid -Force -ErrorAction Stop
    exit 0
}
catch {
    $message = $_.Exception.Message
    if ($RequestWritten) {
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
    }
    try {
        Write-Status `
            -Status 'failed' `
            -Stage 'request_failed' `
            -Message $message `
            -ResultCode 1
    }
    catch { }
    try {
        [IO.File]::WriteAllText($LogPath, $message, $Utf8Encoding)
    }
    catch { }
    exit 1
}
finally {
    if ($MutexAcquired -and $Mutex) {
        try { $Mutex.ReleaseMutex() } catch { }
    }
    if ($Mutex) {
        $Mutex.Dispose()
    }
}
