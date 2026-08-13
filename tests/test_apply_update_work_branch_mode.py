from __future__ import annotations

from pathlib import Path
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
        normalized = str(Path(executable).resolve()).casefold()
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
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


def test_launcher_routes_explicit_work_branch_to_work_engine() -> None:
    batch = _read("apply_update.bat", encoding="ascii")
    entrypoint = _read("scripts/apply_update_entrypoint.ps1", encoding="utf-8")

    assert 'set "SCRIPT_PATH=%~dp0scripts\\apply_update_entrypoint.ps1"' in batch
    assert "TEMP_LAUNCHER" not in batch
    assert "%~f0" not in batch
    assert 'scripts\\apply_update_entrypoint.ps1' in batch
    assert '-File "%SCRIPT_PATH%" "%~1" & exit /b' in batch

    assert 'Join-Path $PSScriptRoot "apply_update_release.ps1"' in entrypoint
    assert 'Join-Path $PSScriptRoot "apply_update_work.ps1"' in entrypoint
    assert '$normalizedBranch.StartsWith("work/", [StringComparison]::OrdinalIgnoreCase)' in entrypoint
    assert '& $workPath -BranchName $normalizedBranch' in entrypoint
    assert '& $releasePath -BranchName $normalizedBranch' in entrypoint


def test_work_engine_preserves_safe_order_and_main_immutability() -> None:
    source = _read("scripts/apply_update_work.ps1")

    status_index = source.index("'status', '--porcelain=v1', '--untracked-files=all'")
    fetch_index = source.index("'fetch', '--no-tags', '--prune', 'origin'")
    switch_index = source.index("'switch', '--track', '-c', $BranchName")
    resolve_index = source.index("'--resolve-targets'")
    compile_index = source.index(
        "'-m', 'compileall', '-q', 'app.py', 'src', 'tests', 'scripts'"
    )
    pytest_index = source.index("'-m', 'pytest', '-n', '6', '--dist', 'loadfile'")
    final_index = source.index(
        'Write-Step "5/5 기본 브랜치 무변경과 최종 상태 확인"'
    )

    assert status_index < fetch_index < switch_index < resolve_index < compile_index
    assert compile_index < pytest_index < final_index
    assert '$WorkBranchPrefix = "work/"' in source
    assert "'show-ref', '--verify', '--quiet'" in source
    assert "'merge-base', '--is-ancestor'" in source
    assert "'merge', '--ff-only'" in source
    assert "'switch', '--track', '-c'" in source
    assert "scripts/check_text_hygiene.py" in source
    assert "$FinalMainSha -ne $InitialMainSha" in source
    assert 'Remote-Sha "refs/heads/$DefaultBranch"' in source
    assert "로컬·원격 main을 변경하거나 push하지 않고" in source


def test_work_engine_rejects_unsafe_branch_relationships_and_files() -> None:
    source = _read("scripts/apply_update_work.ps1")

    assert "$relation.Behind -ne 0 -or $relation.Ahead -lt 1" in source
    assert "Protected-Path" in source
    assert "보호 대상 파일이 작업 브랜치에 포함되어 있습니다." in source
    assert "로컬 작업 브랜치가 원격과 diverged" in source
    assert "자동 stash나 덮어쓰기는 하지 않습니다." in source
    assert "'diff', '--check'" in source


def test_work_engine_does_not_run_destructive_or_publish_git_commands() -> None:
    source = _read("scripts/apply_update_work.ps1").lower()

    assert "@('push'" not in source
    assert "'push'," not in source
    assert "reset --hard" not in source
    assert "clean -fd" not in source
    assert "@('stash'" not in source
    assert "'checkout', '-f'" not in source
    assert "git add" not in source
    assert "git commit" not in source


def test_work_engine_incremental_delta_and_safety_contracts() -> None:
    source = _read("scripts/apply_update_work.ps1")

    assert "$beforeHead =" in source
    assert "$targetHead =" in source
    assert "Is-Ancestor $beforeHead $targetHead" in source
    assert "적용 전 로컬 커밋($beforeHead)이 대상 커밋($targetHead)의 조상이 아닙니다." in source
    assert "'--diff-filter=ACDMRT'" in source
    assert '"$beforeHead..$targetHead"' in source
    assert "$isNoOp = ($beforeHead -eq $targetHead)" in source
    assert "$mode = 'no_op'" in source
    assert "$deltaFailed = $true" in source
    assert "변경 파일 계산 실패로 인해 전체 pytest로 fallback합니다." in source


def test_work_engine_is_utf8_with_bom() -> None:
    data = (PROJECT_ROOT / "scripts" / "apply_update_work.ps1").read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize(("runtime_name", "powershell"), POWERSHELL_PARAMS)
def test_work_engine_parses_in_each_available_powershell(
    runtime_name: str,
    powershell: str,
) -> None:
    script_path = str(
        PROJECT_ROOT / "scripts" / "apply_update_work.ps1"
    ).replace("'", "''")
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
        f"{runtime_name} parser failed.\n" + result.stdout + result.stderr
    )
