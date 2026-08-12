from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

from scripts.report_operation_diagnostics import (
    _build_read_only_verification,
    _capture_database_state,
    _print_human as _print_operation_human,
)
from scripts.report_source_analysis_limits import (
    _print_human as _print_source_limit_human,
)
from src.config import DEFAULT_DB_PATH, get_gemini_config
from src.services.operation_diagnostic_report_service import (
    build_operation_diagnostic_report,
)
from src.services.source_analysis_limit_diagnostic_service import (
    build_source_analysis_limit_diagnostic,
)


def _build_bundle(
    db_path: Path,
    *,
    days: int,
    refresh_runs: int,
) -> tuple[dict[str, object], dict[str, object]]:
    before_state = _capture_database_state(db_path)
    config = get_gemini_config()

    with duckdb.connect(str(db_path), read_only=True) as con:
        operation = build_operation_diagnostic_report(
            con,
            app_id=config.app_id,
            items_per_request=config.topic_angle_batch_limit,
            thinking_level=config.topic_angle_thinking_level,
            timeout_seconds=config.topic_angle_timeout_seconds,
            min_opportunity_score=config.topic_angle_min_opportunity_score,
            portal_days=days,
            refresh_run_limit=refresh_runs,
        )
        source_limits = build_source_analysis_limit_diagnostic(con)

    after_state = _capture_database_state(db_path)
    verification = _build_read_only_verification(before_state, after_state)
    operation["read_only_verification"] = verification
    source_limits["read_only"] = True
    source_limits["read_only_verification"] = verification

    bundle: dict[str, object] = {
        "read_only": True,
        "read_only_verification": verification,
        "operation": operation,
        "source_analysis_limits": source_limits,
    }
    return bundle, verification


def _print_human(bundle: dict[str, object]) -> None:
    operation = bundle["operation"]
    source_limits = bundle["source_analysis_limits"]
    assert isinstance(operation, dict)
    assert isinstance(source_limits, dict)

    _print_operation_human(operation)
    print()
    print("-" * 48)
    print()
    _print_source_limit_human(source_limits)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "실제 DuckDB를 수정하지 않고 P2 운영 진단과 NAVER·Daum 분석 입력 "
            "상한 진단을 한 번에 출력합니다."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="진단할 DuckDB 경로",
    )
    parser.add_argument(
        "--days",
        type=int,
        choices=(7, 30),
        default=7,
        help="NAVER·Daum 요청 집계 기간",
    )
    parser.add_argument(
        "--refresh-runs",
        type=int,
        default=10,
        help="출처 수집·Gemini 분리 상태를 볼 최근 실행 수",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="사람용 요약 대신 하나의 JSON 객체로 출력",
    )
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if not db_path.exists():
        print(f"DB 파일을 찾을 수 없습니다: {db_path}", file=sys.stderr)
        return 2

    try:
        bundle, verification = _build_bundle(
            db_path,
            days=args.days,
            refresh_runs=args.refresh_runs,
        )
    except Exception as exc:
        print(f"읽기 전용 P2 통합 진단에 실패했습니다: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
    else:
        _print_human(bundle)

    if not bool(verification["verified"]):
        print(
            "DB 또는 WAL이 진단 중 변경되었습니다. 자동 수집과 앱을 멈춘 뒤 다시 실행하세요.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
