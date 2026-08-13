from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str, *, encoding: str = "utf-8-sig") -> str:
    return (PROJECT_ROOT / path).read_text(encoding=encoding)


def _available_powershell_runtimes() -> list[tuple[str, str]]:
    candidates = (
        ("PowerShell 7", shutil.which("pwsh.exe") or shutil.which("pwsh")),
        (
            "Windows PowerShell 5.1",
            shutil.which("powershell.exe") or shutil.which("powershell"),
        ),
    )
    runtimes: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for runtime_name, executable in candidates:
        if not executable:
            continue
        normalized_path = str(Path(executable).resolve()).casefold()
        if normalized_path in seen_paths:
            continue
        seen_paths.add(normalized_path)
        runtimes.append((runtime_name, executable))
    return runtimes


POWERSHELL_RUNTIMES = _available_powershell_runtimes()
POWERSHELL_PARAMS = POWERSHELL_RUNTIMES or [
    pytest.param(
        "PowerShell unavailable",
        "",
        marks=pytest.mark.skip(reason="PowerShell is not available in this environment."),
    )
]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _prepare_release_launcher_repo(tmp_path: Path, *, engine_exit_code: int) -> Path:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / "apply_update_release.ps1", scripts)
    (scripts / "apply_update.ps1").write_text(
        "[CmdletBinding()]\n"
        "param([string]$BranchName)\n"
        "$branch = (& git symbolic-ref --quiet --short HEAD).Trim()\n"
        "[IO.File]::WriteAllText($env:APPLY_UPDATE_TEST_RESULT, $branch)\n"
        f"exit {engine_exit_code}\n",
        encoding="utf-8-sig",
    )

    _git(repo, "init", "-b", "main")
    _git(repo, "add", "scripts")
    _git(
        repo,
        "-c",
        "user.name=Apply Update Test",
        "-c",
        "user.email=apply-update@example.invalid",
        "commit",
        "-m",
        "test fixture",
    )
    _git(repo, "switch", "-c", "work/0.10.106")
    return repo


def test_apply_update_entrypoint_prefers_pwsh_and_has_safe_fallback() -> None:
    batch = _read("apply_update.bat", encoding="ascii")
    entrypoint = _read("scripts/apply_update_entrypoint.ps1", encoding="utf-8")

    assert 'cd /d "%~dp0"' in batch
    assert 'set "SCRIPT_PATH=%~dp0scripts\\apply_update_entrypoint.ps1"' in batch
    assert 'scripts\\apply_update_entrypoint.ps1' in batch
    assert "where pwsh.exe" in batch
    assert 'set "POWERSHELL_EXE=pwsh.exe"' in batch
    assert "%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" in batch
    assert '-ExecutionPolicy Bypass -File "%SCRIPT_PATH%" "%~1" & exit /b' in batch
    assert "TEMP_LAUNCHER" not in batch
    assert "%~f0" not in batch
    assert "chcp 65001" not in batch
    assert "pause" not in batch.lower()
    assert "setx" not in batch.lower()

    assert 'Join-Path $PSScriptRoot "apply_update_release.ps1"' in entrypoint
    assert 'Join-Path $PSScriptRoot "apply_update_work.ps1"' in entrypoint
    assert '$normalizedBranch.StartsWith("work/", [StringComparison]::OrdinalIgnoreCase)' in entrypoint
    assert '& $workPath -BranchName $normalizedBranch' in entrypoint
    assert '& $releasePath -BranchName $normalizedBranch' in entrypoint


def test_apply_update_release_launcher_accepts_clean_work_branch_start() -> None:
    source = _read("scripts/apply_update_release.ps1")

    status_index = source.index("'status', '--porcelain=v1', '--untracked-files=all'")
    work_guard_index = source.index('$AllowedSourceBranchPrefix = "work/"')
    switch_index = source.index("'switch', $DefaultBranch")
    engine_index = source.index('Join-Path $PSScriptRoot "apply_update.ps1"')

    assert work_guard_index < status_index < switch_index
    assert engine_index < switch_index
    assert "Assert-CleanWorkingTree" in source
    assert "현재 작업 브랜치가 깨끗하므로 릴리스 검증을 위해" in source
    assert "Restore-OriginalBranch" in source
    assert "'switch', $OriginalBranch" in source
    assert "자동 stash나 덮어쓰기는 하지 않습니다." in source


def test_apply_update_release_launcher_does_not_use_destructive_git_commands() -> None:
    source = _read("scripts/apply_update_release.ps1").lower()

    assert "push --force" not in source
    assert "reset --hard" not in source
    assert "clean -fd" not in source
    assert "'checkout', '-f'" not in source
    assert "@('stash'" not in source


@pytest.mark.parametrize(("runtime_name", "powershell"), POWERSHELL_PARAMS)
@pytest.mark.parametrize(
    ("engine_exit_code", "expected_branch"),
    ((0, "main"), (7, "work/0.10.106")),
)
def test_apply_update_release_launcher_switches_from_work_and_restores_on_failure(
    tmp_path: Path,
    runtime_name: str,
    powershell: str,
    engine_exit_code: int,
    expected_branch: str,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is not available in this environment.")

    repo = _prepare_release_launcher_repo(
        tmp_path,
        engine_exit_code=engine_exit_code,
    )
    result_path = tmp_path / "engine-branch.txt"
    environment = dict(os.environ)
    environment["APPLY_UPDATE_TEST_RESULT"] = str(result_path)

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo / "scripts" / "apply_update_release.ps1"),
            "-BranchName",
            "agent/release-test",
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )

    assert result.returncode == engine_exit_code, (
        f"{runtime_name} launcher returned an unexpected exit code.\n"
        + result.stdout
        + result.stderr
    )
    assert result_path.read_text(encoding="utf-8") == "main"
    assert _git(repo, "branch", "--show-current").stdout.strip() == expected_branch


def test_apply_update_engine_preserves_required_safety_order() -> None:
    source = _read("scripts/apply_update.ps1")

    status_index = source.index("'status', '--porcelain=v1', '--untracked-files=all'")
    fetch_index = source.index("'fetch',")
    selection_index = source.index(
        'Write-Step "3/8 적용 대상 단일 커밋 브랜치 선택"'
    )
    worktree_index = source.index("'worktree', 'add', '--detach'")
    compile_index = source.index(
        "'compileall', '-q', 'app.py', 'src', 'tests', 'scripts'"
    )
    pytest_index = source.index("'-m',\n                'pytest'")
    recheck_index = source.index('Write-Step "7/8 반영 직전 상태 재확인"')
    push_index = source.index("'push',")

    assert status_index < fetch_index < selection_index < worktree_index
    assert worktree_index < compile_index < pytest_index < recheck_index < push_index
    assert "honggee486-dot/content-trend-tracker" in source
    assert '$DefaultBranch = "main"' in source
    assert "'merge-base', '--is-ancestor'" in source
    assert "'diff'," in source and "'--check'," in source
    assert "'update-ref'," in source
    assert "Restore-After-PushFailure" in source
    assert "'ls-remote', '--symref', 'origin', 'HEAD'" in source


def test_apply_update_engine_requires_exactly_one_commit() -> None:
    source = _read("scripts/apply_update.ps1")

    assert "$RequiredAheadCount = 1" in source
    assert "'rev-list', '--left-right', '--count'" in source
    assert "Assert-SingleCommitBranch" in source
    assert "$relation.Behind -ne 0" in source
    assert "$relation.Ahead -ne $RequiredAheadCount" in source
    assert "ahead 1, behind 0" in source
    assert "최종 커밋 1개짜리 브랜치를 다시 만드세요." in source
    assert source.count("Assert-SingleCommitBranch $BaseSha $TargetSha $BranchName") == 2


def test_apply_update_engine_filters_auto_candidates_to_single_commit() -> None:
    source = _read("scripts/apply_update.ps1")

    assert '$WorkBranchPrefix = "agent/"' in source
    assert "'for-each-ref'," in source
    assert "if ($relation.Behind -ne 0 -or $relation.Ahead -ne $RequiredAheadCount)" in source
    assert "적용 가능한 단일 커밋 브랜치" in source
    assert "현재 적용 가능한 ahead 1, behind 0 원격 작업 브랜치가 없습니다." in source


def test_apply_update_engine_blocks_all_data_lock_files() -> None:
    source = _read("scripts/apply_update.ps1")
    gitignore = _read(".gitignore", encoding="utf-8")

    assert "$p -match '^data/.*\\.lock$'" in source
    assert "data/trend_refresh.lock" in gitignore
    assert "data/trend_clustering.lock" in gitignore


def test_apply_update_engine_blocks_prefixed_oauth_json_files() -> None:
    source = _read("scripts/apply_update.ps1")
    gitignore = _read(".gitignore", encoding="utf-8")

    pattern_match = re.search(r"\$jsonName -match '([^']+)'", source)
    assert pattern_match is not None
    sensitive_name_pattern = re.compile(pattern_match.group(1))

    for name in (
        "blogger_oauth_client.json",
        "blogger_oauth_token.json",
        "client_secret-prod.json",
        "service_account.json",
        "cookies_backup.json",
    ):
        assert sensitive_name_pattern.search(name), name

    assert not sensitive_name_pattern.search("tokenizer_config.json")
    assert "data/blogger_oauth_client.json" in gitignore
    assert "data/blogger_oauth_token.json" in gitignore


def test_apply_update_engine_handles_native_stderr_without_false_failure() -> None:
    source = _read("scripts/apply_update.ps1")

    assert "$commandParts = @($FilePath) + @($Arguments)" in source
    assert '$script:LastCommand = ($commandParts -join " ").Trim()' in source
    assert "$previousErrorActionPreference = $ErrorActionPreference" in source
    assert '$ErrorActionPreference = "Continue"' in source
    assert "$ErrorActionPreference = $previousErrorActionPreference" in source
    assert "'worktree', 'prune'" in source
    assert source.index("'worktree', 'prune'") < source.index(
        "'worktree', 'add', '--detach'"
    )


def test_apply_update_release_launcher_handles_native_stderr_without_false_failure() -> None:
    source = _read("scripts/apply_update_release.ps1")

    assert "$commandParts = @($script:GitExe) + @($Arguments)" in source
    assert '$script:LastCommand = ($commandParts -join " ").Trim()' in source
    assert "$previousErrorActionPreference = $ErrorActionPreference" in source
    assert '$ErrorActionPreference = "Continue"' in source
    assert "$ErrorActionPreference = $previousErrorActionPreference" in source


def test_apply_update_engine_streams_validation_and_preserves_utf8() -> None:
    source = _read("scripts/apply_update.ps1")

    assert "[switch]$StreamOutput" in source
    assert "if ($StreamOutput)" in source
    assert "& $FilePath @Arguments" in source
    assert source.count("-WorkingDirectory $WorktreePath") == 2
    assert '$env:PYTHONUTF8 = "1"' in source
    assert '$env:PYTHONIOENCODING = "utf-8"' in source
    assert "$env:PYTHONUTF8 = $oldPythonUtf8" in source
    assert "$env:PYTHONIOENCODING = $oldPythonIoEncoding" in source
    assert "[Console]::OutputEncoding = $Utf8Encoding" in source
    assert "$OutputEncoding = $Utf8Encoding" in source


def test_apply_update_engine_keeps_step_headers_single() -> None:
    source = _read("scripts/apply_update.ps1")

    for step in range(1, 9):
        assert source.count(f'Write-Step "{step}/8') == 1


def test_apply_update_engine_is_utf8_with_bom() -> None:
    data = (PROJECT_ROOT / "scripts" / "apply_update.ps1").read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")


def test_apply_update_release_launcher_is_utf8_with_bom() -> None:
    data = (PROJECT_ROOT / "scripts" / "apply_update_release.ps1").read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize(("runtime_name", "powershell"), POWERSHELL_PARAMS)
@pytest.mark.parametrize(
    "script_relpath",
    (
        "scripts/apply_update_entrypoint.ps1",
        "scripts/apply_update.ps1",
        "scripts/apply_update_release.ps1",
    ),
)
def test_apply_update_scripts_parse_in_each_available_powershell(
    runtime_name: str,
    powershell: str,
    script_relpath: str,
) -> None:
    script_path = str(PROJECT_ROOT / script_relpath).replace("'", "''")
    parser_command = (
        "$tokens = $null; $errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script_path}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { "
        "$errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }; "
        "exit 1 }"
    )
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            parser_command,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"{runtime_name} parser failed for {script_relpath}.\n"
        + result.stdout
        + result.stderr
    )


def test_apply_update_engine_rechecks_selected_branch_before_push() -> None:
    source = _read("scripts/apply_update.ps1")

    branch_recheck = source.index(
        'if ((Remote-Sha "refs/heads/$BranchName") -ne $TargetSha)'
    )
    relation_recheck = source.rindex(
        "Assert-SingleCommitBranch $BaseSha $TargetSha $BranchName"
    )
    push_index = source.index("'push',")

    assert branch_recheck < relation_recheck < push_index
    assert "검증 도중 원격 작업 브랜치가 변경되었습니다." in source


def test_apply_update_engine_does_not_use_destructive_git_commands() -> None:
    source = _read("scripts/apply_update.ps1").lower()

    assert "push --force" not in source
    assert "'push', '--force" not in source
    assert "reset --hard" not in source
    assert "clean -fd" not in source
    assert "'checkout', '-f'" not in source
    assert "git add" not in source
    assert "git commit" not in source
    assert "@('stash'" not in source


def test_next_work_document_keeps_current_priorities_and_completed_portal_axis_visible() -> None:
    source = _read("docs/NEXT_WORK.md", encoding="utf-8")

    assert "P1. Streamlit 제작 흐름 전체 브라우저 회귀검증" in source
    assert "P2. 실제 DuckDB 기반 Gemini·수집 운영 진단" in source
    assert "P3. 진단 결과에 따른 단일 축 개선" in source
    assert "600초 제한 시간" in source
    assert "NAVER·Daum 사용자 분석 입력 개수 상한 제거" in source
    assert "전역 군집 임계값" in source
    assert "Google Trends 강제 연결" in source
    assert "동시에 변경하지 않는다" in source


def test_safe_zip_keeps_official_apply_engine_but_removes_root_patch_scripts() -> None:
    source = _read("make_safe_upload_zip.bat", encoding="utf-8")
    exclusion_section = source.split("/XF ", 1)[1].split(">nul", 1)[0]

    assert '"apply_*.ps1"' not in exclusion_section
    assert 'del /f /q "%STAGING%\\apply_*.ps1"' in source
    assert "scripts\\apply_update.ps1" in source
