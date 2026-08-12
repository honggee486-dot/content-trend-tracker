from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_agent_harness_batch_launcher_preserves_arguments_and_exit_code() -> None:
    text = (PROJECT_ROOT / "run_agent_harness.bat").read_text(encoding="utf-8")

    assert 'cd /d "%~dp0"' in text
    assert "scripts\\check_harness.ps1" in text
    assert "%*" in text
    assert "exit /b %ERRORLEVEL%" in text
    assert "pause" not in text.casefold()


def test_agent_harness_powershell_checks_repo_and_prefers_project_python() -> None:
    text = (PROJECT_ROOT / "scripts" / "check_harness.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert ".venv\\Scripts\\python.exe" in text
    assert "venv\\Scripts\\python.exe" in text
    assert "Get-Command python.exe" in text
    assert "agent_test_harness.py" in text
    assert "git -C $ProjectRoot rev-parse --show-toplevel" in text
    assert "실제 DB·외부 API·Windows 스케줄러를 변경하지 않는" in text
    assert "& $Python $Harness @Scenario" in text
    assert "exit $ExitCode" in text
    assert "Invoke-Expression" not in text


def test_agent_harness_powershell_does_not_duplicate_python_scenario_catalog() -> None:
    text = (PROJECT_ROOT / "scripts" / "check_harness.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "ValidateSet" not in text
    assert "[string[]]$Scenario" in text
    assert "지원 시나리오와 별칭의 검증은 Python 하네스를 단일 기준" in text


def test_harness_docs_route_focused_checks_and_runtime_installers() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    harness_doc = (PROJECT_ROOT / "docs" / "AGENT_TEST_HARNESS.md").read_text(
        encoding="utf-8"
    )
    lessons = (PROJECT_ROOT / "docs" / "HARNESS_LESSONS.md").read_text(
        encoding="utf-8"
    )

    assert "## 작업 종류별 확인 경로" in agents
    assert "## 변경 영역별 검증 라우팅" in agents
    assert "## 하네스 확장 경계" in agents
    assert "src/__init__.py" in agents
    assert ".\\run_agent_harness.bat diagnostics" in agents
    assert ".\\run_agent_harness.bat workflow" in agents
    assert ".\\run_agent_harness.bat harness" in agents
    assert "작은 수정마다 처음부터 전체 하네스" in agents
    assert "### `diagnostics`" in harness_doc
    assert "### `workflow`" in harness_doc
    assert "### `harness`" in harness_doc
    assert "scripts/agent_test_harness.py`가 단일 기준" in harness_doc
    assert "실제 브라우저 렌더링을 실행했다는 뜻이 아니다" in harness_doc
    assert "post_collection_cleanup_runtime.py" in lessons
    assert "원본 함수 본문만으로 판단하지 않는다" in lessons
    assert "시나리오 목록은 한 곳에서만 관리한다" in lessons
