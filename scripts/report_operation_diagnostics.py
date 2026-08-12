from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

from src.config import DEFAULT_DB_PATH, get_gemini_config
from src.services.operation_diagnostic_report_service import (
    build_operation_diagnostic_report,
)


def _duration(value_ms: int) -> str:
    milliseconds = max(0, int(value_ms or 0))
    if milliseconds <= 0:
        return "기록 없음"
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f}초"
    minutes, remaining = divmod(int(round(seconds)), 60)
    return f"{minutes}분 {remaining}초"


def _interval(value_ms: int | None) -> str:
    if value_ms is None:
        return "기록 없음"
    milliseconds = int(value_ms)
    sign = "-" if milliseconds < 0 else ""
    seconds = abs(milliseconds) / 1000
    if seconds < 60:
        return f"{sign}{seconds:.1f}초"
    minutes, remaining = divmod(int(round(seconds)), 60)
    return f"{sign}{minutes}분 {remaining}초"


def _percent(value) -> str:
    if value is None:
        return "기록 없음"
    return f"{float(value) * 100:.1f}%"


def _throttle_label(value: str) -> str:
    return {
        "unavailable": "집계 불가",
        "no_requests": "요청 기록 없음",
        "no_throttle": "제한 신호 없음",
        "local_tpm_wait_only": "로컬 TPM 속도조절만 발생",
        "provider_rate_limit": "Gemini 일시 rate limit",
        "provider_daily_quota": "Gemini 일일 quota 소진",
        "mixed_provider_limits": "Gemini rate limit·일일 quota 혼재",
    }.get(str(value or ""), str(value or "미분류"))


def _baseline_percent(value) -> str:
    if value is None:
        return "계산 불가"
    return f"{float(value):.1f}%"


def _print_deterministic_baseline(baseline: dict) -> None:
    print("[결정론적 군집 baseline · 읽기 전용 비교]")
    if not baseline.get("available"):
        missing = ", ".join(str(value) for value in baseline.get("missing") or ())
        detail = f" · {missing}" if missing else ""
        print(
            "- 비교 불가: "
            f"{baseline.get('reason') or '군집 작업 또는 비교 자료가 없습니다.'}{detail}"
        )
        return

    comparison_complete = bool(baseline.get("comparison_complete"))
    print(f"- 비교 완전성: {'완전' if comparison_complete else '불완전'}")
    if not comparison_complete:
        reasons = ", ".join(
            str(value) for value in baseline.get("comparison_incomplete_reasons") or ()
        )
        if reasons:
            print(f"- 불완전 사유: {reasons}")

    print(
        "- 평가 후보/비교 쌍/baseline 병합 후보: "
        f"{int(baseline.get('evaluable_candidate_count') or 0):,}/"
        f"{int(baseline.get('evaluated_candidate_pair_count') or 0):,}/"
        f"{int(baseline.get('baseline_merge_pair_count') or 0):,}"
    )
    print(
        "- 현재 군집 일치/불일치/안전 차단: "
        f"{int(baseline.get('same_cluster_agreement_pair_count') or 0):,}/"
        f"{int(baseline.get('different_cluster_disagreement_pair_count') or 0):,}/"
        f"{int(baseline.get('blocked_candidate_pair_count') or 0):,}"
    )
    if comparison_complete:
        print(
            "- 현재 군집 대비 precision/recall 참고값: "
            f"{_baseline_percent(baseline.get('precision_vs_current_percent'))}/"
            f"{_baseline_percent(baseline.get('recall_vs_current_percent'))}"
        )
    else:
        print("- 현재 군집 대비 precision/recall 참고값: 제공 안 함 · 불완전 비교")

    samples = dict(baseline.get("samples") or {})
    for key, label in (
        ("agreements", "일치"),
        ("disagreements", "불일치"),
        ("safety_blocks", "안전 차단"),
    ):
        for row in list(samples.get(key) or [])[:3]:
            similarity = row.get("similarity")
            similarity_text = (
                f"{float(similarity):.1f}" if similarity is not None else "-"
            )
            print(
                f"  {label}: {row.get('left_title') or '-'} ↔ "
                f"{row.get('right_title') or '-'} · "
                f"유사도 {similarity_text} · {row.get('rule') or '-'}"
            )

    note = str(baseline.get("interpretation_note") or "").strip()
    if note:
        print(f"- {note}")


def _capture_file_state(path: Path) -> dict[str, int | bool]:
    if not path.exists():
        return {"exists": False, "size": 0, "mtime_ns": 0}
    stat = path.stat()
    return {
        "exists": True,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _capture_database_state(db_path: Path) -> dict[str, dict[str, int | bool]]:
    wal_path = Path(f"{db_path}.wal")
    return {
        "database": _capture_file_state(db_path),
        "wal": _capture_file_state(wal_path),
    }


def _build_read_only_verification(
    before: dict[str, dict[str, int | bool]],
    after: dict[str, dict[str, int | bool]],
) -> dict[str, object]:
    changes: list[dict[str, object]] = []
    for file_name in ("database", "wal"):
        before_state = before[file_name]
        after_state = after[file_name]
        if before_state != after_state:
            changes.append(
                {
                    "file": file_name,
                    "before": before_state,
                    "after": after_state,
                }
            )

    verified = not changes
    return {
        "verified": verified,
        "checked": [
            "database.exists",
            "database.size",
            "database.mtime_ns",
            "wal.exists",
            "wal.size",
            "wal.mtime_ns",
        ],
        "changes": changes,
        "message": (
            "DB 크기·수정 시각과 WAL 상태가 유지되었습니다."
            if verified
            else "진단 중 DB 또는 WAL 상태가 달라져 무변경을 확인할 수 없습니다."
        ),
    }


def _print_human(report: dict) -> None:
    runtime = report["runtime"]
    topic = report["topic_angle"]
    candidate_selection = topic["candidate_selection"]
    failure_diagnostics = topic["failure_diagnostics"]
    portal = report["portal_requests"]
    collection = report["collection_separation"]
    clustering = report["trend_clustering"]
    throttle = report["trend_clustering_throttle"]
    quality = dict(clustering.get("quality_sample") or {})
    baseline = dict(clustering.get("deterministic_baseline") or {})
    action = report["next_action"]
    verification = report["read_only_verification"]

    print("콘텐츠 트렌드 트래커 P2 읽기 전용 운영 진단")
    print("=" * 48)
    print(f"진단 시각: {report['generated_at']}")
    print("DB 연결: read_only=True")
    print(
        "DB 무변경 검증: "
        f"{'통과' if verification['verified'] else '확인 필요'} · "
        f"{verification['message']}"
    )
    print(
        "현재 조건: "
        f"{runtime['items_per_request']}개 · {runtime['thinking_level']} · "
        f"{runtime['timeout_seconds']}초"
    )
    print()
    print("[Gemini 주제 방향]")
    print(f"- 상태: {topic['status']} · {topic['summary']}")
    print(
        "- 현재 조건 성공 표본: "
        f"{topic['matching_successful_requests']}회 · "
        f"{topic['requested_items']}개 · "
        f"{'충족' if topic['sample_sufficient'] else '미충족'}"
    )
    print(
        "- 응답 검증 실패: "
        f"현재 조건 {topic['current_validation_failure_count']}회 · "
        f"다른 조건 {topic['other_runtime_validation_failure_count']}회"
    )
    print(
        "- 생성 토큰 평균/최대: "
        f"{topic['average_generation_tokens']:,}/"
        f"{topic['maximum_generation_tokens']:,}"
    )
    print(f"- 평균 소요: {_duration(topic['average_duration_ms'])}")
    print(
        "- 저장 계약/원문 연결: "
        f"{_percent(topic['contract_completion_rate'])}/"
        f"{_percent(topic['evidence_link_rate'])}"
    )
    print(
        "- 분석 대기: "
        f"{topic['pending_cluster_count']:,}개 · "
        f"약 {topic['estimated_runs_to_clear']:,}회"
    )

    print()
    print("[주제 방향 대상 선정 · 읽기 전용 현재 조건 추정]")
    if not candidate_selection["available"]:
        missing = ", ".join(candidate_selection.get("missing_tables") or [])
        error = candidate_selection.get("error_type") or ""
        detail = missing or error or "집계 조건을 확인할 수 없습니다."
        print(f"- 대상 선정 흐름을 집계할 수 없습니다: {detail}")
    else:
        print(
            "- 전체/추천·검토/점수 통과: "
            f"{candidate_selection['total_clusters']:,}/"
            f"{candidate_selection['eligible_status_clusters']:,}/"
            f"{candidate_selection['score_eligible_clusters']:,}"
        )
        print(
            "- 기존 완료/생성 필요: "
            f"{candidate_selection['already_complete_clusters']:,}/"
            f"{candidate_selection['generation_needed_clusters']:,}"
        )
        print(
            "- 이번 확인/민감 제목/근거 없음/생성 대상 추정: "
            f"{candidate_selection['inspected_clusters']:,}/"
            f"{candidate_selection['skipped_sensitive_clusters']:,}/"
            f"{candidate_selection['skipped_no_evidence_clusters']:,}/"
            f"{candidate_selection['selected_clusters']:,}"
        )
        print(
            "- 요청 범위 밖 미검사/현재 확인 상한: "
            f"{candidate_selection['deferred_uninspected_clusters']:,}/"
            f"{candidate_selection['selection_limit']:,}"
        )
        print("- 실제 생성은 수행하지 않으며 현재 DB와 설정 기준의 읽기 전용 추정입니다.")

    print()
    print(
        "[Gemini 주제 방향 최종 실패 · 최근 최대 "
        f"{failure_diagnostics['sample_limit']}건]"
    )
    if not failure_diagnostics["available"]:
        missing = ", ".join(failure_diagnostics.get("missing_columns") or [])
        detail = f" · {missing}" if missing else ""
        print(
            "- 실패 호출을 집계할 수 없습니다: "
            f"{failure_diagnostics.get('reason') or 'unknown'}{detail}"
        )
    elif not failure_diagnostics["samples"]:
        print("- 최종 실패로 남은 Gemini 주제 방향 요청이 없습니다.")
    else:
        print(
            "- 최종 실패 요청 현재 조건/다른 조건/전체: "
            f"{failure_diagnostics['current_runtime_failure_count']}/"
            f"{failure_diagnostics['other_runtime_failure_count']}/"
            f"{failure_diagnostics['terminal_failure_count']}"
        )
        categories = failure_diagnostics.get("failure_categories") or []
        if categories:
            category_text = " · ".join(
                (
                    f"{item['label']} {item['count']}회"
                    f"(현재 {item['current_runtime_count']}회)"
                )
                for item in categories
            )
            print(f"- 최종 실패 원인: {category_text}")
        print(
            "- 재시도 후 최종 실패 전체/현재 조건: "
            f"{failure_diagnostics.get('retried_terminal_failure_count', 0)}/"
            f"{failure_diagnostics.get('current_runtime_retried_failure_count', 0)}"
        )
        for sample in failure_diagnostics["samples"]:
            scope = (
                "현재 조건"
                if sample["matches_current_runtime"]
                else "다른 조건"
            )
            http_status = (
                sample["http_status"]
                if sample["http_status"] is not None
                else "-"
            )
            error_label = sample["error_type"] or sample["status"] or "미분류"
            category_label = sample.get("failure_category_label") or "미분류"
            retry_label = " · 재시도 포함" if sample.get("had_retry") else ""
            print(
                f"- {sample['created_at'] or '시각 미기록'} · {scope} · "
                f"{sample['status'] or '상태 미기록'} · "
                f"HTTP {http_status} · {category_label}{retry_label} · {error_label}"
            )
            print(
                "  요청/설정/사고/제한: "
                f"{sample['requested_item_count']}/"
                f"{sample['configured_items_per_request']}/"
                f"{sample['thinking_level'] or '-'}/"
                f"{sample['request_timeout_seconds']}초 · "
                f"시도 {sample['attempt_number']}"
            )
            print(
                "  입력/출력/사고/전체 토큰: "
                f"{sample['input_tokens']:,}/"
                f"{sample['output_tokens']:,}/"
                f"{sample['thought_tokens']:,}/"
                f"{sample['total_tokens']:,} · "
                f"소요 {_duration(sample['duration_ms'])}"
            )
            if sample["finish_reason"]:
                print(f"  종료 사유: {sample['finish_reason']}")
            message = sample["error_message"] or sample["finish_message"]
            if message:
                print(f"  메시지: {message}")

    print()
    print(f"[NAVER·Daum 실제 요청 · 최근 {portal['days']}일]")
    if not portal["available"]:
        print("- 요청 원장 테이블이 없어 집계할 수 없습니다.")
    else:
        print(
            "- 전체 논리 요청/실제 시도/재시도/최종 실패: "
            f"{portal['request_count']}/{portal['attempt_count']}/"
            f"{portal['retry_count']}/{portal['failed_request_count']}"
        )
        for source_name in ("naver", "daum"):
            item = portal["sources"][source_name]
            label = "NAVER" if source_name == "naver" else "Daum"
            print(
                f"- {label}: 요청 {item['request_count']} · "
                f"시도 {item['attempt_count']} · 재시도 {item['retry_count']} · "
                f"오류 {item['failed_request_count']} · 0건 {item['zero_result_count']} · "
                f"신규 {item['newly_saved_count']}"
            )

    print()
    print(f"[출처 수집·Gemini 분리 · 최근 {collection['run_limit']}회]")
    if not collection["available"]:
        print("- 수집 이력 테이블이 없어 집계할 수 없습니다.")
    else:
        print(f"- 상태: {collection['status']}")
        print(
            "- 출처 수집 성공/부분·실패: "
            f"{collection['source_success_count']}/"
            f"{collection['source_problem_count']}"
        )
        print(
            "- Gemini 성공/부분·실패/생략: "
            f"{collection['gemini_success_count']}/"
            f"{collection['gemini_problem_count']}/"
            f"{collection['gemini_skipped_count']}"
        )
        print(
            "- 출처 수집 성공을 유지한 Gemini 문제 실행: "
            f"{collection['isolated_gemini_problem_count']}회"
        )

    print()
    print("[2단계 군집 시험]")
    if not clustering["available"]:
        missing = ", ".join(clustering.get("missing_columns") or [])
        detail = f" · {missing}" if missing else ""
        print(
            "- 군집 작업 이력을 집계할 수 없습니다: "
            f"{clustering.get('reason') or 'unknown'}{detail}"
        )
    elif not clustering["sample_available"]:
        print("- 아직 완료된 군집 배치 기록이 없습니다.")
    else:
        if clustering.get("contract_mode") == "token_partitioned_snapshot":
            mode = (
                "P2 진단 1회"
                if clustering.get("launcher") == "p2_diagnostic_trial"
                else "토큰 분할 스냅샷"
            )
        else:
            mode = (
                "5배치 시험"
                if clustering["trial_mode"]
                else f"확대 설정 {clustering['configured_max_batches']}회"
            )
        estimated = clustering["estimated_total_tokens_per_1000_units"]
        estimated_text = (
            f"{int(estimated):,}" if estimated is not None else "계산 불가"
        )
        absolute_limit = int(clustering.get("absolute_batch_size_limit") or 0)
        print(
            f"- 상태: {clustering['status']} · {mode} · "
            f"작업 {clustering['job_status']}"
        )
        print(
            "- 완료 배치/설정 상한/최대 배치 번호: "
            f"{clustering['completed_batches']}/"
            f"{clustering['configured_max_batches']}/"
            f"{clustering['maximum_batch_number']}"
        )
        print(
            "- 1차 단위 최대/스캔 설정 상한/절대 상한: "
            f"{clustering['maximum_first_stage_units']}/"
            f"{clustering['configured_batch_size']}/"
            f"{absolute_limit or '-'}"
        )
        print(
            "- 순차 실행/시각 완전성/겹침/역전: "
            f"{'통과' if clustering['sequential_execution_ok'] else '점검'}/"
            f"{'완전' if clustering['batch_timing_complete'] else '불완전'}/"
            f"{clustering['overlapping_batch_count']}/"
            f"{clustering['invalid_batch_interval_count']}"
        )
        print(
            "- 배치 간 최소/최대 간격: "
            f"{_interval(clustering['minimum_inter_batch_gap_ms'])}/"
            f"{_interval(clustering['maximum_inter_batch_gap_ms'])}"
        )
        print(
            "- 처리 1차 군집/원문/남은 원문: "
            f"{clustering['processed_units']}/"
            f"{clustering['processed_source_items']}/"
            f"{clustering['remaining_items']}"
        )
        print(
            "- 기존 연결/새 군집/불확실/충돌/검토: "
            f"{clustering['existing_links']}/"
            f"{clustering['new_clusters']}/"
            f"{clustering['uncertain_units']}/"
            f"{clustering['conflict_units']}/"
            f"{clustering['needs_review_items']}"
        )
        print(
            "- 입력/출력/사고/전체 토큰: "
            f"{clustering['input_tokens']:,}/"
            f"{clustering['output_tokens']:,}/"
            f"{clustering['thought_tokens']:,}/"
            f"{clustering['total_tokens']:,}"
        )
        print(f"- 1,000개당 예상 전체 토큰: {estimated_text}")
        print(
            "- 배치 평균/최대 소요: "
            f"{_duration(clustering['average_duration_ms'])}/"
            f"{_duration(clustering['maximum_duration_ms'])}"
        )

    print()
    print(f"[2단계 제한 원인 · 최근 요청 최대 {throttle['sample_limit']}건]")
    if not throttle["available"]:
        missing = ", ".join(throttle.get("missing_columns") or [])
        detail = f" · {missing}" if missing else ""
        print(
            "- 요청 메트릭을 집계할 수 없습니다: "
            f"{throttle.get('reason') or 'unknown'}{detail}"
        )
    elif not throttle["request_count"]:
        print("- 저장된 2단계 Gemini 요청 메트릭이 없습니다.")
    else:
        print(
            f"- 분류: {_throttle_label(throttle['classification'])} · "
            f"최근 요청 {throttle['request_count']}건"
        )
        print(
            "- 로컬 TPM 대기: "
            f"{throttle['local_tpm_wait_count']}회 · "
            f"합계 {throttle['local_tpm_wait_seconds_total']:.3f}초 · "
            f"최대 {throttle['local_tpm_wait_seconds_max']:.3f}초"
        )
        print(
            "- Gemini 공급자 제한/일일 quota/기타 오류: "
            f"{throttle['provider_rate_limit_count']}/"
            f"{throttle['provider_daily_quota_count']}/"
            f"{throttle['provider_other_error_count']}"
        )
        print("- 로컬 TPM 대기는 호출 전 자체 속도조절이며 Gemini 거절 응답이 아닙니다.")
        print("- 요청 메트릭에는 job_id가 없어 최신 군집 작업의 직접 원인으로 단정하지 않습니다.")

    print()
    print("[군집 품질 표본 · 읽기 전용 재구성]")
    if not quality.get("available"):
        print(
            "- 표본 재구성 불가: "
            f"{quality.get('reason') or '군집 작업 또는 처리 이력이 없습니다.'}"
        )
    else:
        print(
            "- 작업 스냅샷 일치/재구성 신뢰: "
            f"{'일치' if quality.get('snapshot_matches_job') else '불일치'}/"
            f"{'신뢰 가능' if quality.get('reconstruction_reliable') else '점검 필요'}"
        )
        print(
            "- 처리 후보/단독 후보/다중 군집/기존 연결/불확실 후보: "
            f"{quality.get('processed_candidate_count', 0)}/"
            f"{quality.get('singleton_candidate_count', 0)}/"
            f"{quality.get('multi_candidate_cluster_count', 0)}/"
            f"{quality.get('existing_link_candidate_count', 0)}/"
            f"{quality.get('uncertain_candidate_count', 0)}"
        )
        print(
            "- 단독 후보 비율/재시도 원문/수동 검토 원문: "
            f"{float(quality.get('singleton_candidate_percent') or 0):.1f}%/"
            f"{quality.get('retry_source_item_count', 0)}/"
            f"{quality.get('needs_review_source_item_count', 0)}"
        )
        consistency = dict(quality.get("consistency") or {})
        print(
            "- 작업 집계와 재구성 수치: "
            f"{'일치' if consistency.get('all_match') else '불일치'}"
        )
        samples = dict(quality.get("samples") or {})
        sample_labels = (
            ("multi_candidate_clusters", "다중 신규"),
            ("existing_link_clusters", "기존 연결"),
            ("singleton_candidates", "단독 신규"),
        )
        for key, label in sample_labels:
            for row in list(samples.get(key) or [])[:3]:
                titles = " / ".join(
                    str(item.get("title") or "")
                    for item in row.get("items") or ()
                    if str(item.get("title") or "")
                )
                print(
                    f"  {label}: {row.get('canonical_title') or '-'}"
                    f" · {titles or '원문 없음'}"
                )
        for row in list(samples.get("unresolved_candidates") or [])[:3]:
            statuses = ",".join(str(value) for value in row.get("statuses") or ())
            titles = " / ".join(
                str(item.get("title") or "")
                for item in row.get("items") or ()
                if str(item.get("title") or "")
            )
            print(f"  미확정({statuses or '-'}): {titles or '원문 없음'}")
        print("- 단독 후보 비율만으로 과분리로 판정하지 않고 실제 제목·사건 표본을 함께 봅니다.")

    print()
    _print_deterministic_baseline(baseline)

    print()
    print(f"[다음 판단] {action['label']}")
    print(f"- {action['reason']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="실제 DuckDB를 수정하지 않고 P2 운영 지표를 출력합니다."
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
        help="사람용 요약 대신 JSON을 출력",
    )
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if not db_path.exists():
        print(f"DB 파일을 찾을 수 없습니다: {db_path}", file=sys.stderr)
        return 2

    before_state = _capture_database_state(db_path)
    config = get_gemini_config()
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            report = build_operation_diagnostic_report(
                con,
                app_id=config.app_id,
                items_per_request=config.topic_angle_batch_limit,
                thinking_level=config.topic_angle_thinking_level,
                timeout_seconds=config.topic_angle_timeout_seconds,
                min_opportunity_score=config.topic_angle_min_opportunity_score,
                portal_days=args.days,
                refresh_run_limit=args.refresh_runs,
            )
    except Exception as exc:
        print(f"읽기 전용 운영 진단에 실패했습니다: {exc}", file=sys.stderr)
        return 1

    after_state = _capture_database_state(db_path)
    verification = _build_read_only_verification(before_state, after_state)
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
