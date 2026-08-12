from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.report_operation_diagnostics import _capture_database_state
from src.database import init_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_trend_source_visibility_json_cli_is_read_only(tmp_path: Path) -> None:
    db_path = tmp_path / "visibility-cli.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PROJECT_ROOT / "scripts" / "report_trend_source_visibility.py"),
            "--db",
            str(db_path),
            "--json",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["read_only"] is True
    assert report["available"] is True
    assert report["minimum_score"] == 30.0
    assert set(report["groups"]) == {
        "youtube",
        "naver",
        "daum",
        "google_trends",
        "wikipedia",
    }
    assert report["read_only_verification"]["verified"] is True
    assert _capture_database_state(db_path) == before


def test_trend_source_visibility_human_cli_shows_exposure_chain(tmp_path: Path) -> None:
    db_path = tmp_path / "visibility-human.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PROJECT_ROOT / "scripts" / "report_trend_source_visibility.py"),
            "--db",
            str(db_path),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert "트렌드 출처별 글감 노출 읽기 전용 진단" in completed.stdout
    assert "[YouTube]" in completed.stdout
    assert "[NAVER]" in completed.stdout
    assert "[Daum]" in completed.stdout
    assert "[Google Trends]" in completed.stdout
    assert "[위키백과]" in completed.stdout
    assert "기본 목록 노출/추천·검토 점수 미달/최고 트렌드 점수" in completed.stdout
    assert "해석 순서:" in completed.stdout
    assert _capture_database_state(db_path) == before


def test_trend_source_visibility_batch_preserves_windows_contract() -> None:
    batch_path = PROJECT_ROOT / "run_trend_source_visibility_diagnostics.bat"
    batch_bytes = batch_path.read_bytes()
    batch_text = batch_bytes.decode("utf-8")

    assert b"\r\n" in batch_bytes
    assert 'set "PYTHON_EXE=%~dp0.venv\\Scripts\\python.exe"' in batch_text
    assert 'set "PYTHON_EXE=%~dp0venv\\Scripts\\python.exe"' in batch_text
    assert (
        '"%PYTHON_EXE%" "%~dp0scripts\\report_trend_source_visibility.py" %*'
        in batch_text
    )
    assert "exit /b %EXIT_CODE%" in batch_text
    assert "pause" not in batch_text.lower()
    assert "chcp" not in batch_text.lower()
