[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$BranchName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Utf8Encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8Encoding
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ExpectedRepository = "honggee486-dot/content-trend-tracker"
$DefaultBranch = "main"
$WorkBranchPrefix = "work/"
$TempRoot = $null
$Mutex = $null
$MutexAcquired = $false
$LastCommand = ""

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host ("=" * 68) -ForegroundColor DarkCyan
    Write-Host $Message -ForegroundColor Cyan
    Write-Host ("=" * 68) -ForegroundColor DarkCyan
}

function Write-Info([string]$Message) { Write-Host "[안내] $Message" }
function Write-Ok([string]$Message) { Write-Host "[완료] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[주의] $Message" -ForegroundColor Yellow }

function Invoke-CommandChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $ProjectRoot,
        [switch]$Quiet,
        [switch]$AllowFailure,
        [switch]$StreamOutput
    )

    $commandParts = @($FilePath) + @($Arguments)
    $script:LastCommand = ($commandParts -join " ").Trim()
    $previous = Get-Location
    $previousErrorActionPreference = $ErrorActionPreference
    $lines = @()
    try {
        Set-Location -LiteralPath $WorkingDirectory
        $ErrorActionPreference = "Continue"
        if ($StreamOutput) {
            & $FilePath @Arguments
            $exitCode = $LASTEXITCODE
        }
        else {
            $output = @(& $FilePath @Arguments 2>&1)
            $exitCode = $LASTEXITCODE
            $lines = @($output | ForEach-Object { [string]$_ })
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Set-Location -LiteralPath $previous.Path
    }

    if (-not $StreamOutput -and -not $Quiet) {
        $lines | ForEach-Object { Write-Host $_ }
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "명령이 실패했습니다(종료 코드 $exitCode): $($script:LastCommand)"
    }
    if ($StreamOutput) { return }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $lines }
}

function Invoke-Git {
    param(
        [string[]]$Arguments = @(),
        [switch]$Quiet,
        [switch]$AllowFailure
    )
    return Invoke-CommandChecked `
        -FilePath $script:GitExe `
        -Arguments $Arguments `
        -WorkingDirectory $ProjectRoot `
        -Quiet:$Quiet `
        -AllowFailure:$AllowFailure
}

function One-Line([object]$Result, [string]$Description) {
    $lines = @($Result.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -ne 1) {
        throw "$Description 값을 하나로 확인하지 못했습니다."
    }
    return $lines[0].Trim()
}

function Normalize-BranchName([string]$Value) {
    $name = [string]$Value
    if ([string]::IsNullOrWhiteSpace($name)) { return "" }
    $name = $name.Trim()
    if ($name.StartsWith('origin/', [StringComparison]::OrdinalIgnoreCase)) {
        $name = $name.Substring(7).Trim()
    }
    if ($name.StartsWith('refs/heads/', [StringComparison]::OrdinalIgnoreCase)) {
        $name = $name.Substring(11).Trim()
    }
    return $name
}

function Repository-Slug([string]$Url) {
    $match = [regex]::Match($Url.Trim(), '(?i)github\.com[/:](?<slug>[^/\s]+/[^/\s]+)$')
    if (-not $match.Success) { return $null }
    $slug = $match.Groups['slug'].Value
    if ($slug.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
        $slug = $slug.Substring(0, $slug.Length - 4)
    }
    return $slug
}

function Remote-Sha([string]$RefName) {
    $result = Invoke-Git -Arguments @('ls-remote', 'origin', $RefName) -Quiet
    $lines = @($result.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -eq 0) { return $null }
    if ($lines.Count -ne 1) {
        throw "원격 참조를 하나로 확인하지 못했습니다: $RefName"
    }
    return ($lines[0] -split '\s+')[0]
}

function Is-Ancestor([string]$Ancestor, [string]$Descendant) {
    $result = Invoke-Git -Arguments @(
        'merge-base', '--is-ancestor', $Ancestor, $Descendant
    ) -Quiet -AllowFailure
    if ($result.ExitCode -eq 0) { return $true }
    if ($result.ExitCode -eq 1) { return $false }
    throw "커밋 관계를 확인하지 못했습니다: $Ancestor -> $Descendant"
}

function Get-AheadBehind([string]$Base, [string]$Target) {
    $counts = One-Line (
        Invoke-Git -Arguments @(
            'rev-list', '--left-right', '--count', "$Base...$Target"
        ) -Quiet
    ) "ahead/behind"
    $parts = @($counts -split '\s+')
    if ($parts.Count -ne 2) {
        throw "ahead/behind 값을 해석하지 못했습니다: $counts"
    }
    return [pscustomobject]@{
        Behind = [int]$parts[0]
        Ahead = [int]$parts[1]
    }
}

function Protected-Path([string]$Path) {
    $p = $Path.Replace('\', '/').Trim().ToLowerInvariant()
    if ($p -eq '.env' -or ($p.StartsWith('.env.') -and $p -ne '.env.example')) {
        return $true
    }
    if ($p -eq '.streamlit/secrets.toml' -or $p -match '(^|/)secrets[^/]*\.toml$') {
        return $true
    }
    if ($p -eq 'data/trend_refresh.lock' -or $p -match '^data/.*\.duckdb(?:\.wal)?$') {
        return $true
    }
    if ($p -match '\.(?:duckdb(?:\.wal)?|db|sqlite|sqlite3|parquet|feather|arrow|log)$') {
        return $true
    }
    if (
        $p -match '(^|/)(?:backups?|logs?|reports|exports|\.git|\.venv|venv|env|' +
        'node_modules|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|htmlcov|' +
        'coverage|test-results|playwright-report|build|dist|out|\.idea|\.vscode|\.vs)(/|$)'
    ) {
        return $true
    }
    if ($p -match '\.(?:zip|7z|rar|tar|gz|bz2|xz|tmp|temp|bak|old|orig|rej|swp|swo|key|pem|p12|pfx|cer|crt)$') {
        return $true
    }
    if ($p -match '(^|/)(?:credentials|token|oauth|client_secret|service_account|cookies|storage_state|auth_state)[^/]*\.json$') {
        return $true
    }
    return @(
        'scratch_inspect_db.py',
        'inspect_db.py',
        'run_trend_diagnostic.bat',
        'scripts/diagnose_trend_collection.py'
    ) -contains $p
}

$exitCode = 1
try {
    $Mutex = New-Object Threading.Mutex(
        $false,
        'Local\content-trend-tracker-apply-update'
    )
    $MutexAcquired = $Mutex.WaitOne(0)
    if (-not $MutexAcquired) {
        throw "다른 적용 작업이 이미 실행 중입니다."
    }

    Write-Step "1/5 저장소·브랜치·로컬 변경 확인"
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        throw "Git for Windows를 찾을 수 없습니다."
    }
    $script:GitExe = $git.Source

    $root = [IO.Path]::GetFullPath((
        One-Line (Invoke-Git -Arguments @('rev-parse', '--show-toplevel') -Quiet) '저장소 루트'
    ))
    if (-not [string]::Equals($root, $ProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "프로젝트 Git 저장소 루트에서 실행해야 합니다."
    }

    $remoteUrl = One-Line (
        Invoke-Git -Arguments @('remote', 'get-url', 'origin') -Quiet
    ) 'origin 주소'
    if (
        -not [string]::Equals(
            (Repository-Slug $remoteUrl),
            $ExpectedRepository,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "origin이 예상 저장소 $ExpectedRepository 와 일치하지 않습니다."
    }

    $BranchName = Normalize-BranchName $BranchName
    if (-not $BranchName.StartsWith($WorkBranchPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "작업 브랜치 모드는 work/* 브랜치만 허용합니다: $BranchName"
    }
    $formatCheck = Invoke-Git -Arguments @(
        'check-ref-format', '--branch', $BranchName
    ) -Quiet -AllowFailure
    if ($formatCheck.ExitCode -ne 0) {
        throw "유효하지 않은 브랜치명입니다: $BranchName"
    }

    $status = Invoke-Git -Arguments @(
        'status', '--porcelain=v1', '--untracked-files=all'
    ) -Quiet
    if (@($status.Output).Count -gt 0) {
        $status.Output | ForEach-Object { Write-Host $_ }
        throw "로컬 미커밋 또는 미추적 변경이 있습니다. 자동 stash나 덮어쓰기는 하지 않습니다."
    }

    $InitialMainSha = One-Line (
        Invoke-Git -Arguments @(
            'rev-parse', "refs/heads/$DefaultBranch^{commit}"
        ) -Quiet
    ) '로컬 기본 브랜치 커밋'
    Write-Ok "저장소와 로컬 작업 트리가 안전합니다."

    Write-Step "2/5 원격 기준과 작업 브랜치 관계 확인"
    $remoteHead = Invoke-Git -Arguments @(
        'ls-remote', '--symref', 'origin', 'HEAD'
    ) -Quiet
    if (-not (@($remoteHead.Output) -match "^ref:\s+refs/heads/$DefaultBranch\s+HEAD$")) {
        throw "원격 기본 브랜치를 $DefaultBranch 로 확인하지 못했습니다."
    }

    $BaseSha = Remote-Sha "refs/heads/$DefaultBranch"
    $TargetSha = Remote-Sha "refs/heads/$BranchName"
    if (-not $BaseSha) {
        throw "원격 기본 브랜치를 찾을 수 없습니다: $DefaultBranch"
    }
    if (-not $TargetSha) {
        throw "원격 작업 브랜치를 찾을 수 없습니다: $BranchName"
    }

    Invoke-Git -Arguments @(
        'fetch', '--no-tags', '--prune', 'origin',
        "+refs/heads/$DefaultBranch`:refs/remotes/origin/$DefaultBranch",
        "+refs/heads/$BranchName`:refs/remotes/origin/$BranchName"
    ) -Quiet | Out-Null

    $relation = Get-AheadBehind $BaseSha $TargetSha
    if ($relation.Behind -ne 0 -or $relation.Ahead -lt 1) {
        throw (
            "작업 브랜치는 최신 origin/$DefaultBranch 기준으로 뒤처지지 않고 " +
            "최소 1개 커밋 앞서야 합니다. ahead: $($relation.Ahead), " +
            "behind: $($relation.Behind)"
        )
    }

    $changed = @(
        (
            Invoke-Git -Arguments @(
                '-c', 'core.quotepath=false', 'diff', '--name-only',
                '--diff-filter=ACDMRT', $BaseSha, $TargetSha
            ) -Quiet
        ).Output | Where-Object { $_ }
    )
    $blocked = @($changed | Where-Object { Protected-Path $_ })
    if ($blocked.Count -gt 0) {
        $blocked | ForEach-Object { Write-Host " - $_" }
        throw "보호 대상 파일이 작업 브랜치에 포함되어 있습니다."
    }
    Invoke-Git -Arguments @(
        '-c', 'core.whitespace=cr-at-eol', 'diff', '--check', $BaseSha, $TargetSha
    ) | Out-Null
    Write-Ok "origin/$BranchName · ahead $($relation.Ahead), behind 0"

    Write-Step "3/5 로컬 작업 브랜치 생성·전환·fast-forward"
    $localExists = Invoke-Git -Arguments @(
        'show-ref', '--verify', '--quiet', "refs/heads/$BranchName"
    ) -Quiet -AllowFailure
    if ($localExists.ExitCode -eq 0) {
        $LocalWorkSha = One-Line (
            Invoke-Git -Arguments @(
                'rev-parse', "refs/heads/$BranchName^{commit}"
            ) -Quiet
        ) '로컬 작업 브랜치 커밋'
        if (-not (Is-Ancestor $LocalWorkSha $TargetSha)) {
            throw "로컬 작업 브랜치가 원격과 diverged 되었거나 더 앞서 있어 자동 갱신할 수 없습니다."
        }
        Invoke-Git -Arguments @('switch', $BranchName) | Out-Null
        Invoke-Git -Arguments @(
            'merge', '--ff-only', "refs/remotes/origin/$BranchName"
        ) | Out-Null
    }
    elseif ($localExists.ExitCode -eq 1) {
        Invoke-Git -Arguments @(
            'switch', '--track', '-c', $BranchName,
            "refs/remotes/origin/$BranchName"
        ) | Out-Null
    }
    else {
        throw "로컬 작업 브랜치 존재 여부를 확인하지 못했습니다."
    }

    Invoke-Git -Arguments @(
        'branch', "--set-upstream-to=origin/$BranchName", $BranchName
    ) -Quiet | Out-Null
    $CurrentSha = One-Line (
        Invoke-Git -Arguments @('rev-parse', 'HEAD^{commit}') -Quiet
    ) '현재 작업 브랜치 커밋'
    if ($CurrentSha -ne $TargetSha) {
        throw "로컬 작업 브랜치가 원격 최신 커밋과 일치하지 않습니다."
    }
    Write-Ok "로컬 $BranchName 을 원격 최신 커밋으로 준비했습니다."

    Write-Step "4/5 프로젝트 검증 실행"
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "프로젝트 Python을 찾을 수 없습니다: .venv\Scripts\python.exe"
    }

    $TempRoot = Join-Path (
        [IO.Path]::GetTempPath()
    ) ("content-trend-tracker-work-apply-" + [Guid]::NewGuid().ToString('N'))
    $PytestTemp = Join-Path $TempRoot 'pytest'
    $PythonCache = Join-Path $TempRoot 'pycache'
    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null

    $oldCache = $env:PYTHONPYCACHEPREFIX
    $oldPythonUtf8 = $env:PYTHONUTF8
    $oldPythonIoEncoding = $env:PYTHONIOENCODING
    try {
        $env:PYTHONPYCACHEPREFIX = $PythonCache
        $env:PYTHONUTF8 = "1"
        $env:PYTHONIOENCODING = "utf-8"

        Invoke-CommandChecked `
            -FilePath $python `
            -Arguments @('-m', 'compileall', '-q', 'app.py', 'src', 'tests', 'scripts') `
            -WorkingDirectory $ProjectRoot `
            -StreamOutput
        Write-Ok "Python 구문 검사 통과"

        Invoke-CommandChecked `
            -FilePath $python `
            -Arguments @('scripts/check_text_hygiene.py') `
            -WorkingDirectory $ProjectRoot `
            -StreamOutput
        Write-Ok "텍스트 위생 검사 통과"

        Invoke-CommandChecked `
            -FilePath $python `
            -Arguments @(
                '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                "--basetemp=$PytestTemp"
            ) `
            -WorkingDirectory $ProjectRoot `
            -StreamOutput
        Write-Ok "전체 pytest 통과"
    }
    finally {
        $env:PYTHONPYCACHEPREFIX = $oldCache
        $env:PYTHONUTF8 = $oldPythonUtf8
        $env:PYTHONIOENCODING = $oldPythonIoEncoding
    }

    Write-Step "5/5 기본 브랜치 무변경과 최종 상태 확인"
    $CurrentBranch = One-Line (
        Invoke-Git -Arguments @('symbolic-ref', '--quiet', '--short', 'HEAD') -Quiet
    ) '현재 브랜치'
    $CurrentSha = One-Line (
        Invoke-Git -Arguments @('rev-parse', 'HEAD^{commit}') -Quiet
    ) '현재 커밋'
    $FinalMainSha = One-Line (
        Invoke-Git -Arguments @(
            'rev-parse', "refs/heads/$DefaultBranch^{commit}"
        ) -Quiet
    ) '최종 로컬 기본 브랜치 커밋'
    $FinalStatus = Invoke-Git -Arguments @(
        'status', '--porcelain=v1', '--untracked-files=all'
    ) -Quiet

    if ($CurrentBranch -ne $BranchName -or $CurrentSha -ne $TargetSha) {
        throw "검증 중 현재 작업 브랜치 또는 커밋이 변경되었습니다."
    }
    if ($FinalMainSha -ne $InitialMainSha) {
        throw "작업 브랜치 모드에서 로컬 $DefaultBranch 가 변경되었습니다."
    }
    if ((Remote-Sha "refs/heads/$DefaultBranch") -ne $BaseSha) {
        throw "검증 도중 원격 $DefaultBranch 가 변경되었습니다. 다시 실행하세요."
    }
    if ((Remote-Sha "refs/heads/$BranchName") -ne $TargetSha) {
        throw "검증 도중 원격 작업 브랜치가 변경되었습니다. 다시 실행하세요."
    }
    if (@($FinalStatus.Output).Count -gt 0) {
        $FinalStatus.Output | ForEach-Object { Write-Host $_ }
        throw "검증 중 로컬 파일 변경이 발생했습니다."
    }

    Write-Host "현재 브랜치: $CurrentBranch"
    Write-Host "현재 커밋: $CurrentSha"
    Write-Host "기준 main: $BaseSha"
    Write-Host "ahead/behind: $($relation.Ahead)/$($relation.Behind)"
    Write-Host "변경 파일: $($changed.Count)개"
    $changed | ForEach-Object { Write-Host " - $_" }
    Write-Ok "로컬·원격 main을 변경하거나 push하지 않고 작업 브랜치 준비와 검증을 완료했습니다."
    $exitCode = 0
}
catch {
    Write-Host ""
    Write-Host "[오류] $($_.Exception.Message)" -ForegroundColor Red
    if ($LastCommand) {
        Write-Host "마지막 명령: $LastCommand" -ForegroundColor DarkGray
    }
    Write-Warn "로컬·원격 main에는 자동 merge 또는 push를 수행하지 않았습니다."
    $exitCode = 1
}
finally {
    if ($TempRoot -and (Test-Path -LiteralPath $TempRoot)) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($MutexAcquired -and $Mutex) {
        $Mutex.ReleaseMutex()
    }
    if ($Mutex) {
        $Mutex.Dispose()
    }
}

exit $exitCode
