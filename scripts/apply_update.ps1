[CmdletBinding()]
param([Parameter(Position = 0)][string]$BranchName)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Utf8Encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8Encoding
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ExpectedRepository = "honggee486-dot/content-trend-tracker"
$DefaultBranch = "main"
$WorkBranchPrefix = "agent/"
$RequiredAheadCount = 1
$MaxBranchChoices = 20

$LastCommand = ""
$TempRoot = $null
$WorktreePath = $null
$WorktreeAdded = $false
$Detached = $false
$LocalAdvanced = $false
$RemotePushed = $false
$InitialLocalSha = $null
$BaseSha = $null
$TargetSha = $null
$Mutex = $null
$MutexAcquired = $false

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
        [string]$WorkingDirectory = $ProjectRoot,
        [switch]$Quiet,
        [switch]$AllowFailure
    )
    return Invoke-CommandChecked `
        -FilePath $script:GitExe `
        -Arguments $Arguments `
        -WorkingDirectory $WorkingDirectory `
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

function Is-Ancestor([string]$Ancestor, [string]$Descendant) {
    $result = Invoke-Git -Arguments @('merge-base', '--is-ancestor', $Ancestor, $Descendant) -Quiet -AllowFailure
    if ($result.ExitCode -eq 0) { return $true }
    if ($result.ExitCode -eq 1) { return $false }
    throw "커밋 관계를 확인하지 못했습니다: $Ancestor -> $Descendant"
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

function Repository-Slug([string]$Url) {
    $match = [regex]::Match($Url.Trim(), '(?i)github\.com[/:](?<slug>[^/\s]+/[^/\s]+)$')
    if (-not $match.Success) { return $null }
    $slug = $match.Groups['slug'].Value
    if ($slug.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
        $slug = $slug.Substring(0, $slug.Length - 4)
    }
    return $slug
}

function Normalize-BranchName([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    $name = $Value.Trim()
    if ($name.StartsWith('origin/', [StringComparison]::OrdinalIgnoreCase)) {
        $name = $name.Substring(7).Trim()
    }
    if ($name.StartsWith('refs/heads/', [StringComparison]::OrdinalIgnoreCase)) {
        $name = $name.Substring(11).Trim()
    }
    return $name
}

function Resolve-FetchedBranchSha([string]$Name) {
    $result = Invoke-Git -Arguments @(
        'rev-parse', '--verify', "refs/remotes/origin/$Name^{commit}"
    ) -Quiet -AllowFailure
    if ($result.ExitCode -ne 0) { return $null }
    return One-Line $result "원격 작업 브랜치 커밋"
}

function Get-AheadBehind([string]$Base, [string]$Target) {
    $counts = One-Line (
        Invoke-Git -Arguments @('rev-list', '--left-right', '--count', "$Base...$Target") -Quiet
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

function Assert-SingleCommitBranch([string]$Base, [string]$Target, [string]$Name) {
    $relation = Get-AheadBehind $Base $Target
    if ($relation.Behind -ne 0 -or $relation.Ahead -ne $RequiredAheadCount) {
        throw (
            "[중단] 최종 반영 브랜치는 origin/$DefaultBranch 보다 정확히 " +
            "$RequiredAheadCount 커밋 앞서고 뒤처진 커밋이 없어야 합니다. " +
            "브랜치: $Name, ahead: $($relation.Ahead), behind: $($relation.Behind). " +
            "최신 origin/$DefaultBranch 에서 최종 커밋 1개짜리 브랜치를 다시 만드세요."
        )
    }
    return $relation
}

function Get-EligibleBranchCandidates([string]$Base) {
    $format = '%(refname:strip=3)|%(objectname)|%(committerdate:iso-strict)|%(subject)'
    $refs = Invoke-Git -Arguments @(
        'for-each-ref', "--format=$format", "refs/remotes/origin/$WorkBranchPrefix"
    ) -Quiet
    $candidates = @()

    foreach ($line in $refs.Output) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $parts = $line -split '\|', 4
        if ($parts.Count -lt 4) { continue }

        $name = $parts[0].Trim()
        $sha = $parts[1].Trim()
        if (-not $name.StartsWith($WorkBranchPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            continue
        }

        try {
            $relation = Get-AheadBehind $Base $sha
        }
        catch {
            continue
        }
        if ($relation.Behind -ne 0 -or $relation.Ahead -ne $RequiredAheadCount) {
            continue
        }

        try {
            $commitDate = [DateTimeOffset]::Parse(
                $parts[2].Trim(),
                [Globalization.CultureInfo]::InvariantCulture
            )
            $displayDate = $commitDate.ToLocalTime().ToString('yyyy-MM-dd HH:mm')
        }
        catch {
            $commitDate = [DateTimeOffset]::MinValue
            $displayDate = $parts[2].Trim()
        }

        $candidates += [pscustomobject]@{
            Name = $name
            Sha = $sha
            Subject = $parts[3].Trim()
            CommitDate = $commitDate
            DisplayDate = $displayDate
            AheadBy = $relation.Ahead
            BehindBy = $relation.Behind
        }
    }

    return @(
        $candidates | Sort-Object -Property `
            @{ Expression = { $_.CommitDate }; Descending = $true }, `
            @{ Expression = { $_.Name }; Descending = $false }
    )
}

function Select-BranchCandidate([object[]]$Candidates) {
    if ($Candidates.Count -eq 0) { return $null }

    if ($Candidates.Count -eq 1) {
        $candidate = $Candidates[0]
        Write-Ok "적용 가능한 단일 커밋 브랜치를 자동으로 선택했습니다."
        Write-Host "브랜치: $($candidate.Name)"
        Write-Host "최근 작업: $($candidate.Subject)"
        Write-Host "커밋 시각: $($candidate.DisplayDate)"
        Write-Host "ahead/behind: $($candidate.AheadBy)/$($candidate.BehindBy)"
        return $candidate
    }

    $visible = @($Candidates | Select-Object -First $MaxBranchChoices)
    Write-Info "적용 가능한 단일 커밋 브랜치가 여러 개 있습니다."
    Write-Host ""
    for ($index = 0; $index -lt $visible.Count; $index++) {
        $candidate = $visible[$index]
        Write-Host ("[{0}] {1}" -f ($index + 1), $candidate.Name)
        Write-Host "    최근 작업: $($candidate.Subject)"
        Write-Host "    커밋 시각: $($candidate.DisplayDate)"
        Write-Host "    ahead/behind: $($candidate.AheadBy)/$($candidate.BehindBy)"
        Write-Host ""
    }

    if ($Candidates.Count -gt $visible.Count) {
        Write-Warn "최신 $($visible.Count)개만 표시했습니다."
    }

    while ($true) {
        $answer = Read-Host "적용할 번호를 입력하세요 (0: 취소)"
        if ($answer.Trim() -eq '0') { return $null }

        $selectedNumber = 0
        if (
            [int]::TryParse($answer.Trim(), [ref]$selectedNumber) -and
            $selectedNumber -ge 1 -and
            $selectedNumber -le $visible.Count
        ) {
            return $visible[$selectedNumber - 1]
        }
        Write-Warn "1부터 $($visible.Count) 사이의 번호를 입력하거나 0으로 취소하세요."
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
    if ($p -match '^data/.*\.lock$' -or $p -match '^data/.*\.duckdb(?:\.wal)?$') {
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
    $jsonName = [IO.Path]::GetFileName($p)
    if (
        $jsonName.EndsWith('.json') -and
        $jsonName -match '(^|[._-])(?:credentials?|token|oauth|client_secret|service_account|cookies?|storage_state|auth_state)([._-]|$)'
    ) {
        return $true
    }
    return @(
        'scratch_inspect_db.py',
        'inspect_db.py',
        'run_trend_diagnostic.bat',
        'scripts/diagnose_trend_collection.py'
    ) -contains $p
}

function Restore-After-PushFailure {
    if ($RemotePushed) { return }

    if ($LocalAdvanced) {
        $rollback = Invoke-Git -Arguments @(
            'update-ref', "refs/heads/$DefaultBranch", $InitialLocalSha, $TargetSha
        ) -Quiet -AllowFailure
        if ($rollback.ExitCode -eq 0) {
            $script:LocalAdvanced = $false
            Write-Info "로컬 기본 브랜치를 원래 커밋으로 복구했습니다."
        }
        else {
            Write-Warn "로컬 기본 브랜치 자동 복구에 실패했습니다. Git 상태를 확인하세요."
        }
    }

    if ($Detached) {
        $switch = Invoke-Git -Arguments @('switch', $DefaultBranch) -Quiet -AllowFailure
        if ($switch.ExitCode -eq 0) {
            $script:Detached = $false
        }
        else {
            Write-Warn "기본 브랜치로 돌아가지 못했습니다. 현재 브랜치를 확인하세요."
        }
    }
}

function Cleanup-Workspace {
    if ($WorktreeAdded -and $WorktreePath) {
        $remove = Invoke-Git -Arguments @(
            'worktree', 'remove', $WorktreePath
        ) -Quiet -AllowFailure
        if ($remove.ExitCode -ne 0 -and (Test-Path -LiteralPath $WorktreePath)) {
            Remove-Item -LiteralPath $WorktreePath -Recurse -Force -ErrorAction SilentlyContinue
        }
        Invoke-Git -Arguments @('worktree', 'prune') -Quiet -AllowFailure | Out-Null
    }

    if ($TempRoot -and (Test-Path -LiteralPath $TempRoot)) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
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

    Write-Step "1/8 로컬 저장소와 안전 상태 확인"
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

    $currentBranch = One-Line (
        Invoke-Git -Arguments @('symbolic-ref', '--quiet', '--short', 'HEAD') -Quiet
    ) '현재 브랜치'
    if ($currentBranch -ne $DefaultBranch) {
        throw "현재 브랜치가 $DefaultBranch 이 아닙니다: $currentBranch"
    }

    $status = Invoke-Git -Arguments @(
        'status', '--porcelain=v1', '--untracked-files=all'
    ) -Quiet
    if (@($status.Output).Count -gt 0) {
        $status.Output | ForEach-Object { Write-Host $_ }
        throw "로컬 미커밋 또는 미추적 변경이 있습니다. 자동 stash나 덮어쓰기는 하지 않습니다."
    }

    $InitialLocalSha = One-Line (
        Invoke-Git -Arguments @('rev-parse', "refs/heads/$DefaultBranch^{commit}") -Quiet
    ) '로컬 기본 브랜치 커밋'
    Write-Ok "저장소, origin, 현재 브랜치와 작업 트리가 안전합니다."

    Write-Step "2/8 원격 기본 브랜치와 작업 브랜치 정보 갱신"
    $remoteHead = Invoke-Git -Arguments @(
        'ls-remote', '--symref', 'origin', 'HEAD'
    ) -Quiet
    $remoteDefault = $null
    foreach ($line in $remoteHead.Output) {
        if ($line -match '^ref:\s+refs/heads/(?<name>\S+)\s+HEAD$') {
            $remoteDefault = $Matches['name']
            break
        }
    }
    if ($remoteDefault -ne $DefaultBranch) {
        throw "원격 기본 브랜치를 $DefaultBranch 로 확인하지 못했습니다."
    }

    $BranchName = Normalize-BranchName $BranchName
    if ($BranchName) {
        if ($BranchName -eq $DefaultBranch) {
            throw "기본 브랜치는 적용 대상으로 사용할 수 없습니다."
        }
        $formatCheck = Invoke-Git -Arguments @(
            'check-ref-format', '--branch', $BranchName
        ) -Quiet -AllowFailure
        if ($formatCheck.ExitCode -ne 0) {
            throw "유효하지 않은 브랜치명입니다: $BranchName"
        }
    }

    Invoke-Git -Arguments @(
        'fetch',
        '--no-tags',
        '--prune',
        'origin',
        '+refs/heads/*:refs/remotes/origin/*'
    ) -Quiet | Out-Null

    $BaseSha = One-Line (
        Invoke-Git -Arguments @(
            'rev-parse',
            "refs/remotes/origin/$DefaultBranch^{commit}"
        ) -Quiet
    ) '원격 기본 브랜치 커밋'

    if (-not (Is-Ancestor $InitialLocalSha $BaseSha)) {
        throw "로컬 $DefaultBranch 에 원격과 다른 커밋이 있어 자동 적용할 수 없습니다."
    }
    Write-Ok "원격 기본 브랜치와 작업 브랜치 정보를 갱신했습니다."

    Write-Step "3/8 적용 대상 단일 커밋 브랜치 선택"
    if (-not $BranchName) {
        $candidates = @(Get-EligibleBranchCandidates $BaseSha)
        if ($candidates.Count -eq 0) {
            Write-Info "현재 적용 가능한 ahead 1, behind 0 원격 작업 브랜치가 없습니다."
            $exitCode = 0
            return
        }

        $selected = Select-BranchCandidate $candidates
        if (-not $selected) {
            Write-Info "사용자가 적용 작업을 취소했습니다. 아무것도 변경하지 않았습니다."
            $exitCode = 0
            return
        }
        $BranchName = $selected.Name
        $TargetSha = $selected.Sha
    }
    else {
        $TargetSha = Resolve-FetchedBranchSha $BranchName
        if (-not $TargetSha) {
            throw "원격 작업 브랜치를 찾을 수 없습니다: $BranchName"
        }
        Write-Info "명령행에서 지정된 작업 브랜치를 사용합니다."
        Write-Host "브랜치: $BranchName"
    }

    Assert-SingleCommitBranch $BaseSha $TargetSha $BranchName | Out-Null
    Write-Ok "적용 대상: origin/$BranchName (ahead 1, behind 0)"

    Write-Step "4/8 원격 커밋과 변경 범위 확인"
    $changed = @(
        (
            Invoke-Git -Arguments @(
                '-c',
                'core.quotepath=false',
                'diff',
                '--name-only',
                '--diff-filter=ACDMRT',
                $BaseSha,
                $TargetSha
            ) -Quiet
        ).Output | Where-Object { $_ }
    )
    if ($changed.Count -eq 0) {
        throw "적용할 파일 변경을 찾지 못했습니다."
    }

    $blocked = @($changed | Where-Object { Protected-Path $_ })
    if ($blocked.Count -gt 0) {
        $blocked | ForEach-Object { Write-Host " - $_" }
        throw "보호 대상 파일이 변경 범위에 포함되어 있습니다."
    }

    Invoke-Git -Arguments @(
        '-c',
        'core.whitespace=cr-at-eol',
        'diff',
        '--check',
        $BaseSha,
        $TargetSha
    ) | Out-Null

    $changed | ForEach-Object { Write-Host " - $_" }
    Write-Ok "단일 커밋 관계, 보호 대상과 공백 오류 검사를 통과했습니다."

    Write-Step "5/8 격리된 임시 작업 공간 생성"
    $TempRoot = Join-Path (
        [IO.Path]::GetTempPath()
    ) ("content-trend-tracker-apply-" + [Guid]::NewGuid().ToString('N'))
    $WorktreePath = Join-Path $TempRoot 'worktree'
    $PytestTemp = Join-Path $TempRoot 'pytest'
    $PythonCache = Join-Path $TempRoot 'pycache'

    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    Invoke-Git -Arguments @('worktree', 'prune') -Quiet -AllowFailure | Out-Null
    Invoke-Git -Arguments @(
        'worktree', 'add', '--detach', $WorktreePath, $TargetSha
    ) | Out-Null
    $WorktreeAdded = $true

    Write-Step "6/8 프로젝트 필수 검증 실행"
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "프로젝트 Python을 찾을 수 없습니다: .venv\Scripts\python.exe"
    }

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
            -WorkingDirectory $WorktreePath `
            -StreamOutput
        Write-Ok "Python 구문 검사 통과"

        Invoke-CommandChecked `
            -FilePath $python `
            -Arguments @(
                '-m',
                'pytest',
                '-q',
                '-p',
                'no:cacheprovider',
                "--basetemp=$PytestTemp"
            ) `
            -WorkingDirectory $WorktreePath `
            -StreamOutput
        Write-Ok "전체 pytest 통과"
    }
    finally {
        $env:PYTHONPYCACHEPREFIX = $oldCache
        $env:PYTHONUTF8 = $oldPythonUtf8
        $env:PYTHONIOENCODING = $oldPythonIoEncoding
    }

    Write-Step "7/8 반영 직전 상태 재확인"
    $currentBranch = One-Line (
        Invoke-Git -Arguments @('symbolic-ref', '--quiet', '--short', 'HEAD') -Quiet
    ) '현재 브랜치'
    if ($currentBranch -ne $DefaultBranch) {
        throw "검증 중 현재 브랜치가 변경되었습니다."
    }

    $currentSha = One-Line (
        Invoke-Git -Arguments @('rev-parse', 'HEAD^{commit}') -Quiet
    ) '현재 커밋'
    if ($currentSha -ne $InitialLocalSha) {
        throw "검증 중 로컬 기본 브랜치가 변경되었습니다."
    }

    $status = Invoke-Git -Arguments @(
        'status', '--porcelain=v1', '--untracked-files=all'
    ) -Quiet
    if (@($status.Output).Count -gt 0) {
        throw "검증 중 로컬 파일 변경이 발생했습니다."
    }

    if ((Remote-Sha "refs/heads/$DefaultBranch") -ne $BaseSha) {
        throw "검증 도중 원격 기본 브랜치가 변경되었습니다."
    }
    if ((Remote-Sha "refs/heads/$BranchName") -ne $TargetSha) {
        throw "검증 도중 원격 작업 브랜치가 변경되었습니다."
    }
    Assert-SingleCommitBranch $BaseSha $TargetSha $BranchName | Out-Null
    Write-Ok "로컬·원격 기준과 ahead 1, behind 0 조건이 동일합니다."

    Write-Step "8/8 검증된 커밋을 기본 브랜치에 반영"
    Invoke-Git -Arguments @('switch', '--detach', $InitialLocalSha) | Out-Null
    $Detached = $true

    Invoke-Git -Arguments @(
        'update-ref',
        "refs/heads/$DefaultBranch",
        $TargetSha,
        $InitialLocalSha
    ) | Out-Null
    $LocalAdvanced = $true

    try {
        Invoke-Git -Arguments @(
            'push',
            '--porcelain',
            'origin',
            "$TargetSha`:refs/heads/$DefaultBranch"
        ) | Out-Null
        $RemotePushed = $true
    }
    catch {
        Restore-After-PushFailure
        throw
    }

    if ((Remote-Sha "refs/heads/$DefaultBranch") -ne $TargetSha) {
        throw "push 이후 원격 기본 브랜치 커밋 확인에 실패했습니다."
    }

    Invoke-Git -Arguments @('switch', $DefaultBranch) | Out-Null
    $Detached = $false

    $finalSha = One-Line (
        Invoke-Git -Arguments @('rev-parse', 'HEAD^{commit}') -Quiet
    ) '최종 커밋'
    if ($finalSha -ne $TargetSha) {
        throw "로컬 기본 브랜치가 검증 커밋과 일치하지 않습니다."
    }

    $status = Invoke-Git -Arguments @(
        'status', '--porcelain=v1', '--untracked-files=all'
    ) -Quiet
    if (@($status.Output).Count -gt 0) {
        throw "반영 후 작업 트리가 깨끗하지 않습니다."
    }

    Write-Ok "검증한 단일 커밋을 로컬·원격 $DefaultBranch 에 반영했습니다."
    Write-Host "작업 브랜치: origin/$BranchName"
    Write-Host "반영 커밋: $TargetSha"
    $exitCode = 0
}
catch {
    if (-not $RemotePushed) {
        Restore-After-PushFailure
    }

    Write-Host ""
    Write-Host "[실패] 적용 작업을 중단했습니다." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($LastCommand) {
        Write-Host "마지막 명령: $LastCommand" -ForegroundColor DarkYellow
    }

    if ($RemotePushed) {
        Write-Warn "원격 push 이후 로컬 마무리에서 실패했습니다. 원격 커밋을 확인하세요."
    }
    else {
        Write-Info "원격 기본 브랜치는 변경하지 않았습니다."
    }
    $exitCode = 1
}
finally {
    Cleanup-Workspace
    if ($MutexAcquired -and $Mutex) {
        try { $Mutex.ReleaseMutex() } catch {}
    }
    if ($Mutex) {
        $Mutex.Dispose()
    }
}

exit $exitCode
