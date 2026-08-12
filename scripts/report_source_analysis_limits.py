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
)
from src.config import DEFAULT_DB_PATH
from src.services.source_analysis_limit_diagnostic_service import (
    build_source_analysis_limit_diagnostic,
)


def _print_human(report: dict) -> None:
    print("NAVER·Daum 최근 분석 범위 전체 적용 읽기 전용 진단")
    print("=" * 58)
    print("DB 연결: read_only=True")
    verification = report["read_only_verification"]
    print(
        "DB 무변경 검증: "
        f"{'통과' if verification['verified'] else '확인 필요'} · "
        f"{verification['message']}"
    )

    if not report["available"]:
        missing = ", ".join(report.get("missing_tables") or [])
        error = report.get("error_type") or ""
        detail = missing or error or "집계 조건을 확인할 수 없습니다."
        print(f"진단 불가: {detail}")
        if report.get("error_message"):
            print(f"오류: {report['error_message']}")
        return

    print(f"조회 범위: 최근 {int(report['lookback_hours']):,}시간 전체")
    print()
    for group_name in ("naver", "daum"):
        item = report["groups"][group_name]
        print(f"[{item['label']}]")
        print(
            "- 분석 모드/최근 원문/이번 선택/범위 밖: "
            f"전체/{int(item['recent_items']):,}/"
            f"{int(item['selected_items']):,}/"
            f"{int(item['outside_limit_items']):,}"
        )
        legacy_limit = int(item.get("stored_legacy_limit") or 0)
        if legacy_limit:
            print(
                "- 이전 저장 상한: "
                f"{legacy_limit:,}개 · 호환 정보로만 보존하며 현재 분석에는 적용하지 않음"
            )
        print(
            "- 최근 미군집/선택 미군집/범위 밖 미군집: "
            f"{int(item['recent_unclustered_items']):,}/"
            f"{int(item['selected_unclustered_items']):,}/"
            f"{int(item['outside_limit_unclustered_items']):,}"
            f" ({float(item['outside_limit_unclustered_percent']):.1f}%)"
        )
        print(
            "- 현재 선택 중 2단계 분석 대기: "
            f"{int(item['selected_pending_items']):,}개"
        )
        print()

    print(
        "분석 시간 범위 밖이 아닌데 선택에서 빠진 미군집 합계: "
        f"{int(report['outside_limit_unclustered_items']):,}개"
    )
    print(
        "현재 선택 중 2단계 분석 대기 합계: "
        f"{int(report['selected_pending_items']):,}개"
    )
    print(
        "정상 계약에서는 최근 분석 시간 범위 안의 NAVER·Daum 원문이 모두 선택되므로 "
        "범위 밖 미군집 합계가 0이어야 합니다."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "실제 DuckDB를 수정하지 않고 NAVER·Daum 최근 분석 시간 범위 전체가 "
            "순위·군집 입력에 포함되는지 출력합니다."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="진단할 DuckDB 경로",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="사람용 요약 대신 JSON을 출력",
    )
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if not db_path.exists():
        print(f"DB 파일을 찾을 수 없습니다: {db_path}", file=sys.stderr)
        return 2

    before_state = _capture_database_state(db_path)
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            report = build_source_analysis_limit_diagnostic(con)
    except Exception as exc:
        print(f"읽기 전용 포털 전체 분석 진단에 실패했습니다: {exc}", file=sys.stderr)
        return 1

    after_state = _capture_database_state(db_path)
    verification = _build_read_only_verification(before_state, after_state)
    report["read_only"] = True
    report["read_only_verification"] = verification

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    if not verification["verified"]:
        print(
            "DB 또는 WAL이 진단 중 변경되었습니다. 자동 수집과 앱을 멈춘 뒤 다시 실행하세요.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
