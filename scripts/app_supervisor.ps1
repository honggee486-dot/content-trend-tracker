[CmdletBinding()]
param(
    [ValidateSet('Run', 'Stop', 'Status')]
    [string]$Action = 'Run',

    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [string]$PythonExe = '',

    [ValidateRange(1024, 65535)]
    [int]$Port = 8518
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Utf8Encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8Encoding
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding

$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$AppPath = Join-Path $ProjectRoot 'app.py'
$StateRoot = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Join-Path ([IO.Path]::GetTempPath()) 'content-trend-tracker'
} else {
    Join-Path $env:LOCALAPPDATA 'content-trend-tracker'
}
$RuntimeStatePath = Join-Path $StateRoot 'app_runtime.json'
$UpdateRequestPath = Join-Path $StateRoot 'app_update_request.json'
$UpdateStatusPath = Join-Path $StateRoot 'update_restart_status.json'
$UpdateLogPath = Join-Path $StateRoot 'update_restart.log'
$MutexName = 'Local\content-trend-tracker-app-supervisor'
$ExpectedRepository = 'honggee486-dot/content-trend-tracker'
$BranchPattern = '^work/\d+\.\d+\.\d+(?:[-._][A-Za-z0-9][A-Za-z0-9._-]*)?$'
$ShaPattern = '^[0-9a-fA-F]{40}$'
$script:RuntimeToken = [Guid]::NewGuid().ToString('N')
$script:SupervisorStartTicks = 0L
$script:ChildProcess = $null

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
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    if ($ProcessId -le 0) { return 0L }
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        return [long]$process.StartTime.ToUniversalTime().Ticks
    }
    catch {
        return 0L
    }
}

function Get-ProcessStartTicksWithRetry {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    foreach ($attempt in 1..10) {
        $ticks = Get-ProcessStartTicks -ProcessId $ProcessId
        if ($ticks -gt 0) { return $ticks }
        Start-Sleep -Milliseconds 50
    }
    return 0L
}

function Test-ProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][long]$ExpectedStartTicks
    )
    $actual = Get-ProcessStartTicks -ProcessId $ProcessId
    if ($actual -le 0) { return $false }
    if ($ExpectedStartTicks -le 0) { return $true }
    return $actual -eq $ExpectedStartTicks
}

function Test-PortAvailable {
    param([Parameter(Mandatory = $true)][int]$LocalPort)
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

function Test-PortListening {
    param([Parameter(Mandatory = $true)][int]$LocalPort)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $LocalPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(250)) { return $false }
        $client.EndConnect($async)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-PortOwnerPid {
    param([Parameter(Mandatory = $true)][int]$LocalPort)
    try {
        $row = Get-NetTCPConnection `
            -State Listen `
            -LocalPort $LocalPort `
            -ErrorAction Stop |
            Select-Object -First 1
        if ($row) { return [int]$row.OwningProcess }
    }
    catch { }
    return 0
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    if ($ProcessId -le 0 -or $ProcessId -eq $PID) { return }
    $taskkill = Get-Command taskkill.exe -ErrorAction SilentlyContinue
    if ($taskkill) {
        & $taskkill.Source /PID $ProcessId /T /F *> $null
        return
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Wait-PortReleased {
    param([Parameter(Mandatory = $true)][int]$LocalPort)
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-PortAvailable -LocalPort $LocalPort) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return (Test-PortAvailable -LocalPort $LocalPort)
}

function Write-RuntimeState {
    param(
        [int]$StreamlitPid = 0,
        [long]$StreamlitStartTicks = 0L,
        [string]$State = 'running'
    )
    Write-JsonAtomic -Path $RuntimeStatePath -Payload ([ordered]@{
        schema_version = 1
        project_root = $ProjectRoot
        app_path = $AppPath
        port = $Port
        state = $State
        runtime_token = $script:RuntimeToken
        supervisor_pid = $PID
        supervisor_start_ticks = $script:SupervisorStartTicks
        streamlit_pid = $StreamlitPid
        streamlit_start_ticks = $StreamlitStartTicks
        updated_at = [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:sszzz')
    })
}

function Remove-RuntimeStateIfOwned {
    $state = Read-JsonObject -Path $RuntimeStatePath
    if ($state -and [string]$state.runtime_token -eq $script:RuntimeToken) {
        Remove-Item -LiteralPath $RuntimeStatePath -Force -ErrorAction SilentlyContinue
    }
}

function Write-UpdateStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$BranchName = '',
        [string]$ExpectedSha = '',
        [int]$ResultCode = -1
    )
    Write-JsonAtomic -Path $UpdateStatusPath -Payload ([ordered]@{
        status = $Status
        stage = $Stage
        branch_name = $BranchName
        expected_sha = $ExpectedSha.ToLowerInvariant()
        message = $Message
        result_code = $ResultCode
        supervisor_pid = $PID
        port = $Port
        updated_at = [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:sszzz')
    })
}

function Resolve-PythonExecutable {
    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        if ([IO.Path]::IsPathRooted($PythonExe)) {
            $candidate = [IO.Path]::GetFullPath($PythonExe)
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
            throw "Python 실행기를 찾을 수 없습니다: $candidate"
        }
        $command = Get-Command $PythonExe -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
        $candidate = Join-Path $ProjectRoot $PythonExe
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [IO.Path]::GetFullPath($candidate)
        }
        throw "Python 실행기를 찾을 수 없습니다: $PythonExe"
    }

    foreach ($candidate in @(
        (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
        (Join-Path $ProjectRoot 'venv\Scripts\python.exe')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command python -ErrorAction SilentlyContinue }
    if ($command) { return $command.Source }
    throw 'Python 실행기를 찾을 수 없습니다.'
}

function Stop-ManagedApplication {
    $state = Read-JsonObject -Path $RuntimeStatePath
    $stopped = $false
    if ($state -and (Same-Path -Left ([string]$state.project_root) -Right $ProjectRoot)) {
        $supervisorPid = [int]$state.supervisor_pid
        $supervisorTicks = [long]$state.supervisor_start_ticks
        if (Test-ProcessIdentity `
            -ProcessId $supervisorPid `
            -ExpectedStartTicks $supervisorTicks) {
            Write-Host "[INFO] 관리형 앱 종료 · PID $supervisorPid · 포트 $([int]$state.port)"
            Stop-ProcessTree -ProcessId $supervisorPid
            $stopped = $true
        }
    }

    if (-not (Wait-PortReleased -LocalPort $Port)) {
        $ownerPid = Get-PortOwnerPid -LocalPort $Port
        throw "포트 $Port 이 해제되지 않았습니다. 점유 PID: $ownerPid"
    }
    Remove-Item -LiteralPath $RuntimeStatePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $UpdateRequestPath -Force -ErrorAction SilentlyContinue
    if ($stopped) {
        Write-Host '[완료] content-trend-tracker 앱을 종료했습니다.'
    } else {
        Write-Host '[완료] 실행 중인 관리형 content-trend-tracker 앱이 없습니다.'
    }
}

function Show-ManagedStatus {
    $state = Read-JsonObject -Path $RuntimeStatePath
    if (-not $state) {
        Write-Host '[상태] 관리형 앱 상태 파일이 없습니다.'
        return
    }
    $supervisorAlive = Test-ProcessIdentity `
        -ProcessId ([int]$state.supervisor_pid) `
        -ExpectedStartTicks ([long]$state.supervisor_start_ticks)
    $streamlitAlive = Test-ProcessIdentity `
        -ProcessId ([int]$state.streamlit_pid) `
        -ExpectedStartTicks ([long]$state.streamlit_start_ticks)
    Write-Host "[상태] supervisor=$supervisorAlive PID=$($state.supervisor_pid)"
    Write-Host "[상태] streamlit=$streamlitAlive PID=$($state.streamlit_pid)"
    Write-Host "[상태] http://127.0.0.1:$($state.port)"
}

function Start-StreamlitChild {
    param([Parameter(Mandatory = $true)][string]$ResolvedPython)
    if (-not (Test-PortAvailable -LocalPort $Port)) {
        $ownerPid = Get-PortOwnerPid -LocalPort $Port
        throw "전용 포트 $Port 이 이미 사용 중입니다. 점유 PID: $ownerPid"
    }

    $env:CONTENT_TREND_TRACKER_SUPERVISOR_PID = [string]$PID
    $env:CONTENT_TREND_TRACKER_SUPERVISOR_START_TICKS = [string]$script:SupervisorStartTicks
    $env:CONTENT_TREND_TRACKER_APP_PORT = [string]$Port
    $env:CONTENT_TREND_TRACKER_RUNTIME_STATE = $RuntimeStatePath
    $env:CONTENT_TREND_TRACKER_UPDATE_REQUEST = $UpdateRequestPath

    $argumentLine = (
        '-m streamlit run "' + $AppPath + '"' +
        ' --server.address 127.0.0.1' +
        ' --server.port ' + [string]$Port +
        ' --server.headless true' +
        ' --browser.gatherUsageStats false'
    )
    Write-Host "[시작] content-trend-tracker · http://127.0.0.1:$Port"
    Write-Host '[안내] 이 터미널에서 Ctrl+C를 누르면 앱 전체가 종료됩니다.'
    $process = Start-Process `
        -FilePath $ResolvedPython `
        -ArgumentList $argumentLine `
        -WorkingDirectory $ProjectRoot `
        -NoNewWindow `
        -PassThru
    $startTicks = Get-ProcessStartTicksWithRetry -ProcessId $process.Id
    if ($startTicks -le 0) {
        Stop-ProcessTree -ProcessId $process.Id
        throw 'Streamlit 프로세스 시작 정보를 확인하지 못했습니다.'
    }
    Write-RuntimeState `
        -StreamlitPid $process.Id `
        -StreamlitStartTicks $startTicks `
        -State 'starting'

    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while ([DateTime]::UtcNow -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) {
            throw "Streamlit이 시작 확인 전에 종료됐습니다. 종료 코드: $($process.ExitCode)"
        }
        if (Test-PortListening -LocalPort $Port) {
            Write-RuntimeState `
                -StreamlitPid $process.Id `
                -StreamlitStartTicks $startTicks `
                -State 'running'
            return $process
        }
        Start-Sleep -Milliseconds 200
    }
    Stop-ProcessTree -ProcessId $process.Id
    throw "Streamlit이 제한 시간 안에 포트 $Port 에서 응답하지 않았습니다."
}

function Test-UpdateRequest {
    param(
        [Parameter(Mandatory = $true)]$Request,
        [Parameter(Mandatory = $true)][int]$StreamlitPid,
        [Parameter(Mandatory = $true)][long]$StreamlitStartTicks
    )
    if (-not $Request) { return $false }
    if (-not (Same-Path -Left ([string]$Request.project_root) -Right $ProjectRoot)) {
        return $false
    }
    if ([string]$Request.branch_name -notmatch $BranchPattern) { return $false }
    if ([string]$Request.expected_sha -notmatch $ShaPattern) { return $false }
    if ([int]$Request.supervisor_pid -ne $PID) { return $false }
    if ([long]$Request.supervisor_start_ticks -ne $script:SupervisorStartTicks) {
        return $false
    }
    if ([int]$Request.streamlit_pid -ne $StreamlitPid) { return $false }
    if ([long]$Request.streamlit_start_ticks -ne $StreamlitStartTicks) {
        return $false
    }
    return [int]$Request.port -eq $Port
}

function Invoke-GitText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = @(& $script:GitExe @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Git 명령 실패: git $($Arguments -join ' ')`n$($output -join [Environment]::NewLine)"
    }
    return (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Repository-Slug {
    param([string]$Url)
    $match = [regex]::Match(
        $Url.Trim(),
        '(?i)github\.com[/:](?<slug>[^/\s]+/[^/\s]+?)(?:\.git)?$'
    )
    if (-not $match.Success) { return '' }
    return $match.Groups['slug'].Value
}

function Invoke-RequestedUpdate {
    param([Parameter(Mandatory = $true)]$Request)
    $branchName = [string]$Request.branch_name
    $expectedSha = ([string]$Request.expected_sha).ToLowerInvariant()
    try {
        Write-UpdateStatus `
            -Status 'checking' `
            -Stage 'preflight' `
            -Message '앱 종료를 확인하고 업데이트 안전 조건을 검사합니다.' `
            -BranchName $branchName `
            -ExpectedSha $expectedSha

        foreach ($lockName in @('trend_refresh.lock', 'trend_clustering.lock')) {
            if (Test-Path -LiteralPath (Join-Path (Join-Path $ProjectRoot 'data') $lockName)) {
                throw "실행 중 작업 잠금이 있어 업데이트를 중단합니다: $lockName"
            }
        }

        $git = Get-Command git -ErrorAction SilentlyContinue
        if (-not $git) { throw 'Git for Windows를 찾을 수 없습니다.' }
        $script:GitExe = $git.Source
        $remoteUrl = Invoke-GitText @('remote', 'get-url', 'origin')
        if (-not [string]::Equals(
            (Repository-Slug $remoteUrl),
            $ExpectedRepository,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "origin이 예상 저장소 $ExpectedRepository 와 일치하지 않습니다."
        }
        $remoteLine = Invoke-GitText @('ls-remote', 'origin', "refs/heads/$branchName")
        $remoteRows = @($remoteLine -split "`r?`n" | Where-Object { $_.Trim() })
        if ($remoteRows.Count -ne 1) {
            throw "원격 작업 브랜치를 확인하지 못했습니다: $branchName"
        }
        $remoteSha = (($remoteRows[0] -split '\s+')[0]).ToLowerInvariant()
        if ($remoteSha -ne $expectedSha) {
            throw "원격 브랜치가 변경되었습니다. 예상 $expectedSha, 현재 $remoteSha"
        }

        $applyBat = Join-Path $ProjectRoot 'apply_update.bat'
        if (-not (Test-Path -LiteralPath $applyBat -PathType Leaf)) {
            throw "적용 도구를 찾을 수 없습니다: $applyBat"
        }
        Write-UpdateStatus `
            -Status 'applying' `
            -Stage 'apply_update' `
            -Message "선택한 $branchName 브랜치를 검증하고 적용합니다." `
            -BranchName $branchName `
            -ExpectedSha $expectedSha

        $output = @(& $applyBat $branchName 2>&1)
        $applyExitCode = $LASTEXITCODE
        [IO.File]::WriteAllLines(
            $UpdateLogPath,
            @($output | ForEach-Object { [string]$_ }),
            $Utf8Encoding
        )
        if ($applyExitCode -ne 0) {
            throw "apply_update.bat이 종료 코드 $applyExitCode 로 실패했습니다."
        }

        $currentBranch = Invoke-GitText @('symbolic-ref', '--quiet', '--short', 'HEAD')
        $currentSha = (Invoke-GitText @('rev-parse', 'HEAD^{commit}')).ToLowerInvariant()
        if ($currentBranch -ne $branchName -or $currentSha -ne $expectedSha) {
            throw '적용 완료 후 브랜치 또는 커밋이 예상값과 일치하지 않습니다.'
        }
        Write-UpdateStatus `
            -Status 'restarting' `
            -Stage 'restart_app' `
            -Message '검증을 통과했습니다. 같은 터미널 관리자가 앱을 다시 시작합니다.' `
            -BranchName $branchName `
            -ExpectedSha $expectedSha
        return [pscustomobject]@{
            success = $true
            branch_name = $branchName
            expected_sha = $expectedSha
            message = "$branchName · $($expectedSha.Substring(0, 8)) 적용 완료"
        }
    }
    catch {
        return [pscustomobject]@{
            success = $false
            branch_name = $branchName
            expected_sha = $expectedSha
            message = $_.Exception.Message
        }
    }
}

if ($Action -eq 'Stop') {
    Stop-ManagedApplication
    exit 0
}
if ($Action -eq 'Status') {
    Show-ManagedStatus
    exit 0
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.git'))) {
    throw "프로젝트 Git 저장소를 찾을 수 없습니다: $ProjectRoot"
}
if (-not (Test-Path -LiteralPath $AppPath -PathType Leaf)) {
    throw "Streamlit 진입 파일을 찾을 수 없습니다: $AppPath"
}

$resolvedPython = Resolve-PythonExecutable
$mutex = New-Object Threading.Mutex($false, $MutexName)
$mutexAcquired = $false
$pendingUpdate = $null
$openedBrowser = $false
$exitCode = 0
try {
    $mutexAcquired = $mutex.WaitOne(0)
    if (-not $mutexAcquired) {
        $state = Read-JsonObject -Path $RuntimeStatePath
        $existingPort = if ($state) { [int]$state.port } else { $Port }
        Write-Host "[안내] 앱이 이미 실행 중입니다: http://127.0.0.1:$existingPort"
        try { Start-Process "http://127.0.0.1:$existingPort" | Out-Null } catch { }
        exit 0
    }

    $script:SupervisorStartTicks = Get-ProcessStartTicksWithRetry -ProcessId $PID
    if ($script:SupervisorStartTicks -le 0) {
        throw '앱 관리자 프로세스 시작 정보를 확인하지 못했습니다.'
    }
    Write-RuntimeState -State 'supervisor_ready'

    while ($true) {
        $script:ChildProcess = Start-StreamlitChild -ResolvedPython $resolvedPython
        $childStartTicks = Get-ProcessStartTicksWithRetry -ProcessId $script:ChildProcess.Id

        if (-not $openedBrowser) {
            try { Start-Process "http://127.0.0.1:$Port" | Out-Null } catch { }
            $openedBrowser = $true
        }

        if ($pendingUpdate) {
            if ([bool]$pendingUpdate.success) {
                Write-UpdateStatus `
                    -Status 'success' `
                    -Stage 'completed' `
                    -Message "$($pendingUpdate.message) · 앱 재시작 완료" `
                    -BranchName ([string]$pendingUpdate.branch_name) `
                    -ExpectedSha ([string]$pendingUpdate.expected_sha) `
                    -ResultCode 0
            } else {
                Write-UpdateStatus `
                    -Status 'failed_restarted' `
                    -Stage 'recovered' `
                    -Message "$($pendingUpdate.message) · 현재 작업 상태로 앱을 다시 실행했습니다." `
                    -BranchName ([string]$pendingUpdate.branch_name) `
                    -ExpectedSha ([string]$pendingUpdate.expected_sha) `
                    -ResultCode 1
            }
            $pendingUpdate = $null
        }

        while ($true) {
            Start-Sleep -Milliseconds 300
            $script:ChildProcess.Refresh()
            if ($script:ChildProcess.HasExited) { break }
        }

        Write-RuntimeState -State 'streamlit_stopped'
        $request = Read-JsonObject -Path $UpdateRequestPath
        if (-not (Test-UpdateRequest `
            -Request $request `
            -StreamlitPid $script:ChildProcess.Id `
            -StreamlitStartTicks $childStartTicks)) {
            if ($request) {
                Remove-Item -LiteralPath $UpdateRequestPath -Force -ErrorAction SilentlyContinue
            }
            break
        }
        $pendingUpdate = Invoke-RequestedUpdate -Request $request
        Remove-Item -LiteralPath $UpdateRequestPath -Force -ErrorAction SilentlyContinue
    }
}
catch [System.Management.Automation.PipelineStoppedException] {
    Write-Host '[종료] 터미널 종료 요청을 받아 앱을 정리합니다.'
    $exitCode = 0
}
catch {
    $message = $_.Exception.Message
    Write-Host "[ERROR] $message"
    $exitCode = 1
    if ($pendingUpdate) {
        try {
            Write-UpdateStatus `
                -Status 'failed_restart_required' `
                -Stage 'restart_failed' `
                -Message "$($pendingUpdate.message) · 앱 재시작 실패: $message" `
                -BranchName ([string]$pendingUpdate.branch_name) `
                -ExpectedSha ([string]$pendingUpdate.expected_sha) `
                -ResultCode 1
        }
        catch { }
    }
}
finally {
    if ($script:ChildProcess) {
        try {
            $script:ChildProcess.Refresh()
            if (-not $script:ChildProcess.HasExited) {
                Stop-ProcessTree -ProcessId $script:ChildProcess.Id
            }
        }
        catch { }
    }
    Remove-RuntimeStateIfOwned
    if ($mutexAcquired) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    $mutex.Dispose()
}

exit $exitCode
