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
from src.services.trend_source_visibility_diagnostic_service import (
    build_trend_source_visibility_diagnostic,
)


_STATUS_LABELS = {
    "recommended": "추천",
    "review": "검토",
    "hold": "보류",
}
_SORT_LABELS = {
    "opportunity": "글감 추천순",
    "trend": "급상승순",
    "quality": "자료 완성도순",
    "recent": "최근 확인순",
}


def _print_human(report: dict) -> None:
    print("트렌드 출처별 글감 노출 읽기 전용 진단")
    print("=" * 48)
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

    sort_label = _SORT_LABELS.get(
        str(report.get("sort_by") or ""),
        str(report.get("sort_by") or "-"),
    )
    print(
        f"조회 범위: 최근 {int(report['lookback_hours']):,}시간 · 추천+검토 · "
        f"최소 트렌드 점수 {float(report['minimum_score']):g} · {sort_label} · "
        f"최대 {int(report['display_limit']):,}개"
    )
    print(
        "현재 전체 군집/필터 통과/실제 기본 목록 표시: "
        f"{int(report['total_clusters']):,}/"
        f"{int(report['eligible_clusters']):,}/"
        f"{int(report['default_visible_clusters']):,}"
    )
    print(f"참고: {report['scope_note']}")
    print(f"참고: {report['overlap_note']}")
    print()

    for group_name in ("youtube", "naver", "daum", "google_trends", "wikipedia"):
        item = report["groups"][group_name]
        print(f"[{item['label']}]")
        print(
            "- 최근 원문/현재 군집 연결/미군집: "
            f"{int(item['recent_items']):,}/"
            f"{int(item['recent_clustered_items']):,}/"
            f"{int(item['recent_unclustered_items']):,}"
        )
        print(
            "- 현재 군집 추천/검토/보류: "
            f"{int(item['recommended_count']):,}/"
            f"{int(item['review_count']):,}/"
            f"{int(item['hold_count']):,}"
        )
        print(
            "- 실제 목록 표시/표시 한도 밖/추천·검토 점수 미달: "
            f"{int(item['default_visible_count']):,}/"
            f"{int(item['ranked_out_count']):,}/"
            f"{int(item['eligible_below_score_count']):,}"
        )
        print(
            "- 최고 글감기회/트렌드 점수: "
            f"{float(item['highest_opportunity_score']):.1f}/"
            f"{float(item['highest_trend_score']):.1f}"
        )
        print(f"- 진단: {item['diagnosis_label']}")
        for example in item.get("examples") or []:
            status = _STATUS_LABELS.get(
                str(example.get("recommendation_status") or ""),
                str(example.get("recommendation_status") or "-"),
            )
            visibility = (
                "기본 목록 표시"
                if bool(example.get("in_default_list"))
                else "기본 목록 밖"
            )
            print(
                f"  표본: [{status}·{visibility}] {example.get('title') or '-'} · "
                f"트렌드 {float(example.get('trend_score') or 0):.1f} · "
                f"기회 {float(example.get('opportunity_score') or 0):.1f}"
            )
        print()

    print(
        "해석 순서: 최근 원문이 있는지 → 최근 원문이 현재 군집에 연결됐는지 → "
        "추천·검토와 최소 점수를 통과했는지 → 실제 기본 정렬 상위 표시 한도 안에 "
        "들어왔는지 확인합니다."
    )


from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "실제 DuckDB를 수정하지 않고 YouTube·NAVER·Daum·Google Trends·"
            "위키백과 신호가 현재 글감 목록까지 도달하는지 출력합니다."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="진단할 DuckDB 경로",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=None,
        help=(
            "최근 원문 확인 시간 범위. 생략하면 DB의 trend_lookback_hours "
            "앱 설정을 사용합니다."
        ),
    )
    parser.add_argument(
        "--minimum-score",
        type=float,
        default=30.0,
        help="추천·검토 후보 최소 트렌드 점수",
    )
    parser.add_argument(
        "--display-limit",
        type=int,
        default=100,
        help="실제 후보 목록에 표시할 최대 행 수",
    )
    parser.add_argument(
        "--sort-by",
        choices=("opportunity", "trend", "quality", "recent"),
        default="opportunity",
        help="실제 후보 목록 정렬 기준",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="사람용 요약 대신 JSON을 출력",
    )
    args = parser.parse_args(argv)

    db_path = args.db.expanduser().resolve()
    if not db_path.exists():
        print(f"DB 파일을 찾을 수 없습니다: {db_path}", file=sys.stderr)
        return 2

    before_state = _capture_database_state(db_path)
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            report = build_trend_source_visibility_diagnostic(
                con,
                lookback_hours=args.lookback_hours,
                minimum_score=float(args.minimum_score),
                display_limit=int(args.display_limit),
                sort_by=str(args.sort_by),
            )
    except Exception as exc:
        print(f"읽기 전용 출처별 글감 노출 진단에 실패했습니다: {exc}", file=sys.stderr)
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
