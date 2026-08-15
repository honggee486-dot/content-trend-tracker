from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.report_operation_diagnostics import _capture_database_state
from scripts.report_trend_source_visibility import main
from src.database import connect_database, init_database, set_setting


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_trend_source_visibility_json_cli_is_read_only(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "visibility-cli.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    exit_code = main(["--db", str(db_path), "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    report = json.loads(captured.out)
    assert report["read_only"] is True
    assert report["available"] is True
    assert report["lookback_hours"] == 72
    assert report["minimum_score"] == 30.0
    assert report["display_limit"] == 100
    assert report["sort_by"] == "opportunity"
    assert report["eligible_clusters"] == 0
    assert report["default_visible_clusters"] == 0
    assert set(report["groups"]) == {
        "youtube",
        "naver",
        "daum",
        "google_trends",
        "wikipedia",
    }
    assert report["read_only_verification"]["verified"] is True
    assert _capture_database_state(db_path) == before


def test_trend_source_visibility_cli_uses_configured_lookback_by_default(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "visibility-configured-lookback.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        set_setting(con, "trend_lookback_hours", "168")
    before = _capture_database_state(db_path)

    exit_code = main(["--db", str(db_path), "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    report = json.loads(captured.out)
    assert report["lookback_hours"] == 168
    assert report["read_only_verification"]["verified"] is True
    assert _capture_database_state(db_path) == before


def test_trend_source_visibility_human_cli_shows_exposure_chain(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "visibility-human.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    exit_code = main(["--db", str(db_path)])
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert "트렌드 출처별 글감 노출 읽기 전용 진단" in captured.out
    assert "글감 추천순 · 최대 100개" in captured.out
    assert "현재 전체 군집/필터 통과/실제 기본 목록 표시" in captured.out
    assert "[YouTube]" in captured.out
    assert "[NAVER]" in captured.out
    assert "[Daum]" in captured.out
    assert "[Google Trends]" in captured.out
    assert "[위키백과]" in captured.out
    assert "실제 목록 표시/표시 한도 밖/추천·검토 점수 미달" in captured.out
    assert "최고 글감기회/트렌드 점수" in captured.out
    assert "해석 순서:" in captured.out
    assert _capture_database_state(db_path) == before


def test_trend_source_visibility_cli_accepts_actual_list_scope_options(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "visibility-options.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        set_setting(con, "trend_lookback_hours", "168")
    before = _capture_database_state(db_path)

    exit_code = main(
        [
            "--db",
            str(db_path),
            "--lookback-hours",
            "24",
            "--display-limit",
            "25",
            "--sort-by",
            "recent",
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    report = json.loads(captured.out)
    assert report["lookback_hours"] == 24
    assert report["display_limit"] == 25
    assert report["sort_by"] == "recent"
    assert report["read_only_verification"]["verified"] is True
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
