from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.report_operation_diagnostics import (
    _build_read_only_verification,
    _capture_database_state,
    _print_deterministic_baseline,
    main,
)
from src.database import init_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_read_only_verification_detects_database_change(tmp_path: Path) -> None:
    db_path = tmp_path / "changed.duckdb"
    db_path.write_bytes(b"before")
    before = _capture_database_state(db_path)

    db_path.write_bytes(b"after-data")
    after = _capture_database_state(db_path)
    verification = _build_read_only_verification(before, after)

    assert verification["verified"] is False
    assert verification["changes"]
    assert verification["changes"][0]["file"] == "database"


def test_read_only_verification_detects_wal_creation(tmp_path: Path) -> None:
    db_path = tmp_path / "wal-created.duckdb"
    db_path.write_bytes(b"database")
    before = _capture_database_state(db_path)

    Path(f"{db_path}.wal").write_bytes(b"wal")
    after = _capture_database_state(db_path)
    verification = _build_read_only_verification(before, after)

    assert verification["verified"] is False
    assert any(change["file"] == "wal" for change in verification["changes"])


def test_json_cli_verifies_database_and_wal_are_unchanged(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "operation-cli.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    exit_code = main(["--db", str(db_path), "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    report = json.loads(captured.out)
    assert report["read_only"] is True
    assert report["read_only_verification"]["verified"] is True
    assert report["read_only_verification"]["changes"] == []
    selection = report["topic_angle"]["candidate_selection"]
    assert selection["available"] is True
    assert selection["selected_is_estimate"] is True
    assert selection["selection_limit"] >= 1
    assert "quality_sample" in report["trend_clustering"]
    assert _capture_database_state(db_path) == before


def test_human_cli_prints_read_only_topic_angle_selection_funnel(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "operation-cli-human.duckdb"
    init_database(db_path)
    before = _capture_database_state(db_path)

    exit_code = main(["--db", str(db_path)])
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    assert "[주제 방향 대상 선정 · 읽기 전용 현재 조건 추정]" in captured.out
    assert "실제 생성은 수행하지 않으며" in captured.out
    assert "[군집 품질 표본 · 읽기 전용 재구성]" in captured.out
    assert "[결정론적 군집 baseline · 읽기 전용 비교]" in captured.out
    assert _capture_database_state(db_path) == before


def test_human_baseline_prints_complete_metrics_and_title_samples(capsys) -> None:
    _print_deterministic_baseline(
        {
            "available": True,
            "comparison_complete": True,
            "evaluable_candidate_count": 12,
            "evaluated_candidate_pair_count": 7,
            "baseline_merge_pair_count": 3,
            "same_cluster_agreement_pair_count": 2,
            "different_cluster_disagreement_pair_count": 1,
            "blocked_candidate_pair_count": 1,
            "precision_vs_current_percent": 66.7,
            "recall_vs_current_percent": 50.0,
            "samples": {
                "agreements": [
                    {
                        "left_title": "같은 제품 출시 일정",
                        "right_title": "같은 제품 출시 일정 안내",
                        "similarity": 96.4,
                        "rule": "high_title_similarity",
                    }
                ],
                "disagreements": [
                    {
                        "left_title": "현재 분리 제목 A",
                        "right_title": "현재 분리 제목 B",
                        "similarity": 94.0,
                        "rule": "high_title_similarity",
                    }
                ],
                "safety_blocks": [
                    {
                        "left_title": "S26 출시",
                        "right_title": "S27 출시",
                        "similarity": 98.0,
                        "rule": "blocked:product_conflict",
                    }
                ],
            },
            "interpretation_note": "현재 저장 군집은 비교 기준일 뿐 정답률이 아닙니다.",
        }
    )

    output = capsys.readouterr().out
    assert "비교 완전성: 완전" in output
    assert "66.7%/50.0%" in output
    assert "같은 제품 출시 일정 ↔ 같은 제품 출시 일정 안내" in output
    assert "현재 분리 제목 A ↔ 현재 분리 제목 B" in output
    assert "S26 출시 ↔ S27 출시" in output
    assert "정답률이 아닙니다" in output


def test_human_baseline_withholds_metrics_when_comparison_is_incomplete(capsys) -> None:
    _print_deterministic_baseline(
        {
            "available": True,
            "comparison_complete": False,
            "comparison_incomplete_reasons": [
                "oversized_blocks_skipped",
                "pair_limit_reached",
            ],
            "evaluable_candidate_count": 500,
            "evaluated_candidate_pair_count": 20_000,
            "baseline_merge_pair_count": 120,
            "same_cluster_agreement_pair_count": 100,
            "different_cluster_disagreement_pair_count": 20,
            "blocked_candidate_pair_count": 8,
            "precision_vs_current_percent": 83.3,
            "recall_vs_current_percent": 77.7,
            "samples": {},
        }
    )

    output = capsys.readouterr().out
    assert "비교 완전성: 불완전" in output
    assert "oversized_blocks_skipped, pair_limit_reached" in output
    assert "precision/recall 참고값: 제공 안 함 · 불완전 비교" in output
    assert "83.3%" not in output
    assert "77.7%" not in output


def test_human_baseline_reports_unavailable_reason_without_guessing(capsys) -> None:
    _print_deterministic_baseline(
        {
            "available": False,
            "reason": "quality_sample_unreliable",
            "missing": [],
        }
    )

    output = capsys.readouterr().out
    assert "[결정론적 군집 baseline · 읽기 전용 비교]" in output
    assert "비교 불가: quality_sample_unreliable" in output
    assert "precision/recall" not in output


def test_operation_diagnostic_batch_preserves_windows_contract() -> None:
    batch_path = PROJECT_ROOT / "run_operation_diagnostics.bat"
    batch_bytes = batch_path.read_bytes()
    batch_text = batch_bytes.decode("utf-8")

    assert b"\r\n" in batch_bytes
    assert 'set "PYTHON_EXE=%~dp0.venv\\Scripts\\python.exe"' in batch_text
    assert 'set "PYTHON_EXE=%~dp0venv\\Scripts\\python.exe"' in batch_text
    assert (
        '"%PYTHON_EXE%" "%~dp0scripts\\report_operation_diagnostics.py" %*'
        in batch_text
    )
    assert "exit /b %EXIT_CODE%" in batch_text
    assert "pause" not in batch_text.lower()
    assert "chcp" not in batch_text.lower()
