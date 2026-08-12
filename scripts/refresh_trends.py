from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.daum_search_adapter import DaumSearchAdapter
from src.adapters.google_trends_rss_adapter import GoogleTrendsRssAdapter
from src.adapters.naver_search_adapter import NaverSearchAdapter
from src.adapters.wikimedia_pageviews_adapter import WikimediaPageviewsAdapter
from src.adapters.youtube_parquet_adapter import YouTubeParquetAdapter
from src.config import (
    DEFAULT_DB_PATH,
    get_gemini_config,
    get_kakao_rest_api_key,
    get_naver_api_credentials,
)
from src.database import connect_database, get_setting, init_database
from src.services.collection_history_service import (
    finish_collection_run,
    record_skipped_overlap,
    start_collection_run,
)
from src.services.data_maintenance_service import run_automatic_cleanup_if_due
from src.services.gemini_model_service import (
    MODEL_PURPOSE_AUTO,
    build_gemini_config_for_purpose,
)
from src.services.post_collection_cleanup_runtime import (
    install_post_collection_cleanup_contract,
)
from src.services.topic_angle_ai_service import (
    execute_prepared_topic_angles,
    finalize_prepared_topic_angles,
    prepare_missing_topic_angles,
)
from src.services.topic_angle_response_integrity_service import (
    annotate_missing_topic_angle_ids,
    apply_integrity_to_batch_result,
)
from src.services.trend_discovery_service import refresh_trend_sources_short_connections
from src.services.trend_refresh_lock_service import run_with_trend_refresh_lock


def _install_direct_cleanup_contract() -> None:
    """직접 실행에서도 수집 후 정리 계약을 적용하되 import 자체는 오염시키지 않습니다."""
    install_post_collection_cleanup_contract()

    from src.services import data_maintenance_service as maintenance
    from src.services import trend_discovery_service as discovery

    global run_automatic_cleanup_if_due, refresh_trend_sources_short_connections
    run_automatic_cleanup_if_due = maintenance.run_automatic_cleanup_if_due
    refresh_trend_sources_short_connections = (
        discovery.refresh_trend_sources_short_connections
    )


def _enabled(value: str, default: bool = True) -> bool:
    clean = str(value or "").strip().casefold()
    if not clean:
        return default
    return clean in {"1", "true", "yes", "on", "enabled"}


def _source_result_is_usable(source_result: object) -> bool:
    if not isinstance(source_result, dict):
        return False
    status = str(source_result.get("status") or "success").strip().casefold()
    return status in {"success", "partial", "partial_success", "skipped"}


def _background_exit_code(
    result: dict[str, object],
    source_specs: list[tuple[str, str]],
) -> int:
    errors = result.get("errors") or {}
    usable_source_exists = any(
        _source_result_is_usable(result.get(key))
        for key, _label in source_specs
    )
    return 1 if errors and not usable_source_exists else 0


def _topic_angle_summary(topic_angle_result: object) -> str:
    if isinstance(topic_angle_result, dict):
        getter = topic_angle_result.get
    else:
        getter = lambda key, default=None: getattr(topic_angle_result, key, default)

    status = str(getter("status", "") or "")
    requested_clusters = int(getter("requested_clusters", 0) or 0)
    generated_clusters = int(getter("generated_clusters", 0) or 0)
    generated_angles = int(getter("generated_angles", 0) or 0)

    if status == "missing_api_key":
        return "Gemini API 키 없음"
    if status == "nothing_to_generate":
        return "Gemini 새 분석 없음"
    if status == "deferred_for_clustering_backlog":
        remaining_items = int(getter("remaining_items", 0) or 0)
        return f"군집 대기 {remaining_items}개·Gemini 방향 보류"
    if generated_clusters or generated_angles:
        return (
            f"Gemini 대상 {requested_clusters}개·"
            f"글감 {generated_clusters}개·방향 {generated_angles}개 저장"
        )
    return f"Gemini 분석 상태 {status or 'unknown'}"


def _topic_angle_payload(topic_angle_result: object) -> dict[str, object]:
    integer_fields = (
        "requested_clusters",
        "generated_clusters",
        "generated_angles",
        "skipped_sensitive_clusters",
        "attempts",
        "requested_batches",
        "completed_batches",
        "failed_batches",
        "items_per_request",
        "max_parallel_requests",
    )
    payload: dict[str, object] = {
        "status": str(getattr(topic_angle_result, "status", "unknown") or "unknown"),
        "error_type": str(getattr(topic_angle_result, "error_type", "") or ""),
        "error_message": str(getattr(topic_angle_result, "error_message", "") or ""),
        "duration_seconds": float(getattr(topic_angle_result, "duration_seconds", 0.0) or 0.0),
        "min_opportunity_score": float(
            getattr(topic_angle_result, "min_opportunity_score", 0.0) or 0.0
        ),
    }
    for field in integer_fields:
        payload[field] = int(getattr(topic_angle_result, field, 0) or 0)
    return payload


def _run_background_topic_angles(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> tuple[dict[str, object], str]:
    """예약 Gemini 요청 전후에만 DuckDB를 짧게 열어 설정된 한 묶음을 보완합니다."""
    try:
        with connect_database(db_path) as con:
            config = build_gemini_config_for_purpose(
                con,
                MODEL_PURPOSE_AUTO,
                base_config=get_gemini_config(),
            )
            items_per_request = max(1, int(config.topic_angle_batch_limit))
            preparation = prepare_missing_topic_angles(
                con,
                config=config,
                limit=items_per_request,
            )
        execution = annotate_missing_topic_angle_ids(
            execute_prepared_topic_angles(
                preparation,
                config=config,
            )
        )
        with connect_database(db_path) as con:
            topic_angle_result = finalize_prepared_topic_angles(
                con,
                config=config,
                execution=execution,
            )
        topic_angle_result = apply_integrity_to_batch_result(
            topic_angle_result,
            execution,
        )
        payload = _topic_angle_payload(topic_angle_result)
        warning = str(getattr(topic_angle_result, "error_message", "") or "")
        return payload, warning
    except Exception as exc:
        return {
            "status": "unexpected_error",
            "requested_clusters": 0,
            "generated_clusters": 0,
            "generated_angles": 0,
            "error_message": str(exc),
        }, f"Gemini 글감 자동 분석 실패: {exc}"


def _run_refresh_body(collection_run_id: str | None = None) -> tuple[int, dict[str, object]]:
    """설정·저장 구간만 DB를 열고 외부 수집과 Gemini 대기 중에는 닫습니다."""
    with connect_database(DEFAULT_DB_PATH) as con:
        settings = {
            "parquet_path": get_setting(con, "youtube_parquet_path"),
            "google_enabled": _enabled(
                get_setting(con, "google_trends_enabled", "true")
            ),
            "wikipedia_enabled": _enabled(
                get_setting(con, "wikipedia_pageviews_enabled", "true")
            ),
            "configured": [
                line.strip()
                for line in get_setting(con, "trend_seed_queries").splitlines()
                if line.strip()
            ],
            "google_limit": int(
                get_setting(con, "google_trends_limit", "50") or 50
            ),
            "wikipedia_limit": int(
                get_setting(con, "wikipedia_pageviews_limit", "50") or 50
            ),
            "results_per_query": int(
                get_setting(con, "trend_results_per_query", "10") or 10
            ),
            "portal_query_limit": int(
                get_setting(con, "trend_portal_query_limit", "50") or 50
            ),
            "portal_pages_per_query": int(
                get_setting(con, "trend_portal_pages_per_query", "2") or 2
            ),
            "naver_max_workers": int(
                get_setting(con, "naver_search_workers", "6") or 6
            ),
            "daum_max_workers": int(
                get_setting(con, "daum_search_workers", "4") or 4
            ),
            "lookback_hours": int(
                get_setting(con, "trend_lookback_hours", "72") or 72
            ),
            "naver_daily_limit": int(
                get_setting(con, "naver_search_daily_safety_limit", "25000") or 25000
            ),
            "naver_monthly_limit": int(
                get_setting(con, "naver_search_monthly_safety_limit", "775000")
                or 775000
            ),
            "kakao_daily_limit": int(
                get_setting(con, "kakao_daum_daily_safety_limit", "50000") or 50000
            ),
            "kakao_monthly_limit": int(
                get_setting(con, "kakao_daum_monthly_safety_limit", "3000000")
                or 3000000
            ),
            "analysis_source_limits": {
                "youtube": int(
                    get_setting(con, "trend_analysis_youtube_limit", "2000") or 2000
                ),
                "naver": int(
                    get_setting(con, "trend_analysis_naver_limit", "4000") or 4000
                ),
                "daum": int(
                    get_setting(con, "trend_analysis_daum_limit", "4000") or 4000
                ),
                "google_trends": int(
                    get_setting(con, "trend_analysis_google_limit", "500") or 500
                ),
                "wikipedia": int(
                    get_setting(con, "trend_analysis_wikipedia_limit", "500") or 500
                ),
            },
        }
        cleanup_result = run_automatic_cleanup_if_due(
            con,
            enabled=_enabled(get_setting(con, "data_cleanup_enabled", "true")),
            source_retention_days=int(
                get_setting(con, "source_retention_days", "30") or 30
            ),
            sync_run_retention_days=int(
                get_setting(con, "sync_run_retention_days", "90") or 90
            ),
            api_usage_retention_months=int(
                get_setting(con, "api_usage_retention_months", "13") or 13
            ),
        )

    parquet_path = str(settings["parquet_path"])
    youtube_adapter = (
        YouTubeParquetAdapter(parquet_path)
        if Path(parquet_path).is_file()
        else None
    )
    client_id, client_secret = get_naver_api_credentials()
    naver_adapter = (
        NaverSearchAdapter(client_id, client_secret)
        if client_id and client_secret
        else None
    )
    kakao_rest_api_key = get_kakao_rest_api_key()
    daum_adapter = (
        DaumSearchAdapter(kakao_rest_api_key)
        if kakao_rest_api_key
        else None
    )
    google_adapter = (
        GoogleTrendsRssAdapter("KR")
        if bool(settings["google_enabled"])
        else None
    )
    wikipedia_adapter = (
        WikimediaPageviewsAdapter("ko.wikipedia.org")
        if bool(settings["wikipedia_enabled"])
        else None
    )

    result = refresh_trend_sources_short_connections(
        DEFAULT_DB_PATH,
        youtube_adapter=youtube_adapter,
        naver_adapter=naver_adapter,
        daum_adapter=daum_adapter,
        google_trends_adapter=google_adapter,
        wikipedia_adapter=wikipedia_adapter,
        configured_seed_queries=list(settings["configured"]),
        youtube_limit=300,
        google_trends_limit=int(settings["google_limit"]),
        wikipedia_limit=int(settings["wikipedia_limit"]),
        naver_display_per_query=int(settings["results_per_query"]),
        daum_size_per_query=int(settings["results_per_query"]),
        portal_query_limit=int(settings["portal_query_limit"]),
        portal_pages_per_query=int(settings["portal_pages_per_query"]),
        naver_max_workers=int(settings["naver_max_workers"]),
        daum_max_workers=int(settings["daum_max_workers"]),
        lookback_hours=int(settings["lookback_hours"]),
        naver_daily_safety_limit=int(settings["naver_daily_limit"]),
        naver_monthly_safety_limit=int(settings["naver_monthly_limit"]),
        kakao_daum_daily_safety_limit=int(settings["kakao_daily_limit"]),
        kakao_daum_monthly_safety_limit=int(settings["kakao_monthly_limit"]),
        analysis_source_limits=dict(settings["analysis_source_limits"]),
        collection_run_id=collection_run_id,
    )

    ranking_clustering = result.get("ranking", {}).get("ai_clustering") or {}
    ranking_backlog = int(ranking_clustering.get("remaining_items", 0) or 0)
    defer_topic_angles = bool(
        ranking_backlog > 0
        or ranking_clustering.get("defer_topic_angles")
        or str(ranking_clustering.get("status") or "") == "skipped_overlap"
    )
    if defer_topic_angles:
        topic_angle_payload = {
            "status": "deferred_for_clustering_backlog",
            "remaining_items": ranking_backlog,
            "requested_clusters": 0,
            "generated_clusters": 0,
            "generated_angles": 0,
            "error_message": "",
        }
        topic_angle_warning = ""
    else:
        topic_angle_payload, topic_angle_warning = _run_background_topic_angles(
            DEFAULT_DB_PATH
        )
    result["topic_angles"] = topic_angle_payload

    messages: list[str] = []
    source_specs = [
        ("youtube", "YouTube"),
        ("google_trends", "Google Trends"),
        ("wikipedia", "위키백과"),
        ("naver", "NAVER"),
        ("daum", "Daum"),
    ]
    for key, label in source_specs:
        source_result = result.get(key)
        if source_result:
            status = str(source_result.get("status") or "success")
            if status == "skipped":
                messages.append(f"{label} 변경 없음")
                continue
            skipped = int(source_result.get("items_skipped", 0) or 0)
            skipped_text = f"(형식 제외 {skipped})" if skipped else ""
            request_count = int(source_result.get("request_count", 0) or 0)
            retry_count = int(source_result.get("retry_count", 0) or 0)
            request_text = (
                f" 요청 {request_count}회·재시도 {retry_count}회"
                if request_count or retry_count
                else ""
            )
            status_text = " 부분 성공" if status == "partial" else ""
            messages.append(
                f"{label} {source_result['items_read']}개"
                f"{skipped_text}{status_text}{request_text}"
            )
    if youtube_adapter is None:
        messages.append("YouTube 교환 파일 없음")
    if naver_adapter is None:
        messages.append("NAVER API 키 없음")
    if daum_adapter is None:
        messages.append("카카오 REST API 키 없음")
    clustering_detail = result["ranking"].get("ai_clustering") or {}
    messages.append(
        f"통합 주제 {result['ranking']['clusters']}개·"
        f"이번 군집 {int(clustering_detail.get('processed_items', 0) or 0)}개·"
        f"남은 {int(clustering_detail.get('remaining_items', 0) or 0)}개"
    )
    messages.append(_topic_angle_summary(topic_angle_payload))
    if cleanup_result is not None:
        messages.append(
            "자동 정리 "
            f"원본 {cleanup_result.source_items_deleted}개 · "
            f"출처 실행 {cleanup_result.sync_runs_deleted}개 · "
            f"전체 이력 {cleanup_result.collection_runs_deleted}개 · "
            f"호출 기록 {cleanup_result.api_usage_rows_deleted}개 삭제"
        )

    errors = result.get("errors") or {}
    warnings = result.get("warnings") or {}
    for source, message in errors.items():
        print(f"경고: {source} 수집 실패 - {message}")
    for source, message in warnings.items():
        print(f"주의: {source} 부분 수집 - {message}")
    if topic_angle_warning:
        print(f"주의: {topic_angle_warning}")
    print(" · ".join(messages))

    exit_code = _background_exit_code(result, source_specs)
    return exit_code, result


def _run_refresh() -> int:
    init_database(DEFAULT_DB_PATH)
    with connect_database(DEFAULT_DB_PATH) as con:
        run_id = start_collection_run(con, "background_refresh")

    try:
        exit_code, result = _run_refresh_body(run_id)
    except Exception as exc:
        # 원래 수집 예외를 보존하면서 종료 상태 기록은 항상 시도합니다.
        try:
            with connect_database(DEFAULT_DB_PATH) as con:
                finish_collection_run(con, run_id, error=exc)
        except Exception as history_exc:
            print(f"경고: 실패 실행 이력을 저장하지 못했습니다 - {history_exc}")
        raise

    with connect_database(DEFAULT_DB_PATH) as con:
        finish_collection_run(con, run_id, result=result)
    return exit_code


def _record_background_overlap(attempt) -> None:
    init_database(DEFAULT_DB_PATH)
    with connect_database(DEFAULT_DB_PATH) as con:
        record_skipped_overlap(
            con,
            "background_refresh",
            summary=attempt.message,
        )


def main() -> int:
    _install_direct_cleanup_contract()
    return run_with_trend_refresh_lock(
        PROJECT_ROOT,
        launcher="batch_or_scheduler",
        runner=_run_refresh,
        overlap_callback=_record_background_overlap,
    )


if __name__ == "__main__":
    raise SystemExit(main())
