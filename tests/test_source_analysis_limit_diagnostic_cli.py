from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.report_operation_diagnostics import _capture_database_state
from scripts.report_source_analysis_limits import main
from src.database import init_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_source_analysis_limit_json_cli_is_read_only(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "source-limit-cli.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    exit_code = main(["--db", str(db_path), "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    report = json.loads(captured.out)
    assert report["read_only"] is True
    assert report["available"] is True
    assert report["analysis_mode"] == "portal_full_window"
    assert report["limits"] == {"naver": 0, "daum": 0}
    assert report["read_only_verification"]["verified"] is True
    assert set(report["groups"]) == {"naver", "daum"}
    assert _capture_database_state(db_path) == before


def test_source_analysis_limit_human_cli_shows_full_window_and_backlog_fields(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "source-limit-human.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    exit_code = main(["--db", str(db_path)])
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert "NAVER·Daum 최근 분석 범위 전체 적용 읽기 전용 진단" in captured.out
    assert "[NAVER]" in captured.out
    assert "[Daum]" in captured.out
    assert "분석 모드/최근 원문/이번 선택/범위 밖" in captured.out
    assert "최근 미군집/선택 미군집/범위 밖 미군집" in captured.out
    assert "현재 선택 중 2단계 분석 대기" in captured.out
    assert "범위 밖 미군집 합계가 0이어야 합니다" in captured.out
    assert _capture_database_state(db_path) == before


def test_source_analysis_limit_batch_preserves_windows_contract() -> None:
    batch_path = PROJECT_ROOT / "run_source_analysis_limit_diagnostics.bat"
    batch_bytes = batch_path.read_bytes()
    batch_text = batch_bytes.decode("utf-8")

    assert b"\r\n" in batch_bytes
    assert 'set "PYTHON_EXE=%~dp0.venv\\Scripts\\python.exe"' in batch_text
    assert 'set "PYTHON_EXE=%~dp0venv\\Scripts\\python.exe"' in batch_text
    assert (
        '"%PYTHON_EXE%" "%~dp0scripts\\report_source_analysis_limits.py" %*'
        in batch_text
    )
    assert "exit /b %EXIT_CODE%" in batch_text
    assert "pause" not in batch_text.lower()
    assert "chcp" not in batch_text.lower()
