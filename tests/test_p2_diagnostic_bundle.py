from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.report_operation_diagnostics import _capture_database_state
from src.database import init_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_bundle(db_path: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PROJECT_ROOT / "scripts" / "report_p2_diagnostics.py"),
            "--db",
            str(db_path),
            *extra_args,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_p2_bundle_json_combines_operation_and_source_limit_diagnostics(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "p2-bundle.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    completed = _run_bundle(
        db_path,
        "--days",
        "30",
        "--refresh-runs",
        "3",
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["read_only"] is True
    assert report["read_only_verification"]["verified"] is True
    assert report["operation"]["portal_requests"]["days"] == 30
    assert report["operation"]["collection_separation"]["run_limit"] == 3
    assert "quality_sample" in report["operation"]["trend_clustering"]
    assert report["operation"]["trend_clustering"]["quality_sample"]["available"] is False
    assert report["source_analysis_limits"]["read_only"] is True
    assert report["source_analysis_limits"]["available"] is True
    assert report["source_analysis_limits"]["analysis_mode"] == "portal_full_window"
    assert report["source_analysis_limits"]["limits"] == {"naver": 0, "daum": 0}
    assert report["source_analysis_limits"]["read_only_verification"]["verified"] is True
    assert _capture_database_state(db_path) == before


def test_p2_bundle_human_output_contains_both_diagnostic_sections(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "p2-bundle-human.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    completed = _run_bundle(db_path)

    assert completed.returncode == 0, completed.stderr
    assert "콘텐츠 트렌드 트래커 P2 읽기 전용 운영 진단" in completed.stdout
    assert "NAVER·Daum 최근 분석 범위 전체 적용 읽기 전용 진단" in completed.stdout
    assert completed.stdout.count("DB 무변경 검증: 통과") == 2
    assert _capture_database_state(db_path) == before


def test_p2_diagnostic_batch_preserves_windows_contract() -> None:
    batch_path = PROJECT_ROOT / "run_p2_diagnostics.bat"
    batch_bytes = batch_path.read_bytes()
    batch_text = batch_bytes.decode("utf-8")

    assert b"\r\n" in batch_bytes
    assert 'set "PYTHON_EXE=%~dp0.venv\\Scripts\\python.exe"' in batch_text
    assert 'set "PYTHON_EXE=%~dp0venv\\Scripts\\python.exe"' in batch_text
    assert (
        '"%PYTHON_EXE%" "%~dp0scripts\\report_p2_diagnostics.py" %*'
        in batch_text
    )
    assert "exit /b %EXIT_CODE%" in batch_text
    assert "pause" not in batch_text.lower()
    assert "chcp" not in batch_text.lower()
