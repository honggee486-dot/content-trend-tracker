from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from html import escape
from time import perf_counter
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.clustering_job_status_ui import (
    build_clustering_metric_values,
    build_recent_clustering_attempt_notice,
    render_clustering_job_error,
)
from src.collection_history_ui import render_collection_history
from src.dashboard_background_refresh_ui import (
    build_trend_dashboard_action_guard,
    format_lock_owner_detail,
)
from src.app_version import (
    build_browser_page_title,
    format_app_version_label,
    read_app_version,
)
from src.adapters.daum_search_adapter import DaumSearchAdapter, DaumSearchError
from src.adapters.google_trends_rss_adapter import GoogleTrendsRssAdapter
from src.adapters.naver_search_adapter import NaverSearchAdapter, NaverSearchError
from src.adapters.wikimedia_pageviews_adapter import WikimediaPageviewsAdapter
from src.adapters.youtube_duckdb_adapter import YouTubeDuckDBAdapter
from src.adapters.youtube_parquet_adapter import YouTubeParquetAdapter, YouTubeParquetError
from src.config import (
    BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST,
    DEFAULT_DB_PATH,
    PROJECT_ROOT,
    get_gemini_config,
    get_kakao_rest_api_key,
    get_naver_api_credentials,
    PRIORITY_LABELS,
    TOPIC_STATUS_LABELS,
    TOPIC_STATUS_OPTIONS,
)
from src.database import (
    connect_database,
    get_setting,
    init_database,
    is_database_lock_error,
    set_setting,
)
from src.services.ai_result_parser import (
    build_ai_result_validation_fingerprint,
    parse_ai_result,
    validate_ai_result_against_references,
)
from src.services.api_quota_service import (
    GOOGLE_TRENDS_API,
    GOOGLE_TRENDS_PROVIDER,
    KAKAO_ALL_API_OFFICIAL_MONTHLY_LIMIT,
    KAKAO_DAUM_API,
    KAKAO_DAUM_OFFICIAL_DAILY_LIMIT,
    KAKAO_DAUM_PROVIDER,
    NAVER_SEARCH_OFFICIAL_DAILY_LIMIT,
    NAVER_SEARCH_OFFICIAL_MONTHLY_LIMIT,
    NAVER_SEARCH_OFFICIAL_RPS_LIMIT,
    WIKIMEDIA_API,
    WIKIMEDIA_PROVIDER,
    get_kakao_daum_usage,
    get_local_api_usage,
    get_naver_search_usage,
)
from src.publish_preparation_ui import render_publish_preparation
from src.services.background_refresh_status_service import (
    get_latest_background_refresh_snapshot,
)
from src.services.blog_profile_service import (
    OUTPUT_FORMAT_LABELS,
    PLATFORM_DEFINITIONS,
    archive_blog_profile,
    get_platform_definition,
    list_blog_profiles,
    restore_blog_profile,
    save_blog_profile,
)
from src.blog_channel_strategy_ui import (
    render_blog_channel_strategy_settings,
    render_publish_channel_assignment,
)
from src.curated_blog_profile_ui import render_curated_blog_profile_settings
from src.database_backup_ui import (
    render_database_backup_panel,
    render_quality_diagnostic_panels,
)
from src.services.curated_blog_profile_service import (
    synchronize_curated_blog_profiles,
)
from src.services.collection_history_service import (
    finish_collection_run,
    record_skipped_overlap,
    run_type_for_dashboard_action,
    start_collection_run,
)
from src.services.data_maintenance_service import (
    cleanup_old_data,
    get_database_stats,
    run_automatic_cleanup_if_due,
)
from src.services.content_pack_service import (
    DEFAULT_FACT_CHECK_ITEMS,
    DEFAULT_FORBIDDEN,
    DEFAULT_OUTLINE,
    DEFAULT_TITLE_RULES,
    assess_content_pack_readiness,
    build_trend_evidence_summary,
    get_content_pack,
    get_topic_content_defaults,
    list_content_packs,
    save_content_pack,
    save_quick_content_pack,
)
from src.services.draft_service import (
    FACT_CHECK_STATUS_LABELS,
    FACT_CHECK_STATUS_OPTIONS,
    build_full_copy_text,
    get_draft,
    get_fact_check_summary,
    get_fact_checks,
    list_drafts,
    save_generation_and_draft,
    update_draft,
    update_fact_check,
)
from src.services.gemini_model_service import (
    MODEL_CATALOG_REFRESHED_AT_SETTING,
    MODEL_PURPOSE_AUTO,
    MODEL_PURPOSE_DATA_REVIEW,
    GeminiModelCatalogError,
    build_gemini_config_for_purpose,
    fetch_gemini_model_catalog,
    get_available_gemini_models,
    get_selected_gemini_model,
    load_gemini_model_catalog,
    model_display_label,
    model_rate_limit_reference,
    normalize_model_id,
    save_gemini_model_catalog,
    set_selected_gemini_model,
)
from src.services.publish_service import mark_published
from src.services.scheduler_service import (
    MAX_SCHEDULE_INTERVAL_MINUTES,
    MIN_SCHEDULE_INTERVAL_MINUTES,
    calculate_quota_interval_recommendation,
    delete_refresh_scheduler,
    get_refresh_scheduler_status,
    register_or_update_refresh_scheduler,
)
from src.services.scheduler_quota_analysis_service import (
    PORTAL_LABELS as QUOTA_PORTAL_LABELS,
    analyze_actual_quota_usage,
)
from src.services.trend_refresh_lock_service import (
    acquire_trend_refresh_lock,
    inspect_trend_refresh_lock,
)
from src.services.trend_clustering_job_service import (
    create_clustering_job,
    get_active_clustering_job,
    get_latest_clustering_attempt,
    get_representative_clustering_job,
    launch_clustering_job,
)
from src.services.trend_clustering_lock_service import inspect_trend_clustering_lock
from src.services.reference_service import (
    REFERENCE_TYPE_LABELS,
    REFERENCE_TYPE_OPTIONS,
    add_topic_reference,
    archive_topic_reference,
    list_topic_references,
    update_topic_reference,
)
from src.services.trend_feedback_service import (
    FEEDBACK_LABELS,
    REJECTED_FEEDBACK_TYPES,
    build_cluster_diagnostics,
    clear_trend_feedback,
    get_trend_feedback,
    get_trend_feedback_summary,
    list_trend_feedback_map,
    save_trend_feedback,
)
from src.services.trend_discovery_service import (
    AI_CLUSTERING_BATCH_SIZE_SETTING,
    AI_CLUSTERING_ENABLED_SETTING,
    AI_CLUSTERING_MAX_BATCHES_SETTING,
    AI_CLUSTERING_MAX_ITEMS_SETTING,
    DEFAULT_AI_CLUSTERING_BATCH_SIZE,
    DEFAULT_AI_CLUSTERING_MAX_BATCHES,
    DEFAULT_AI_CLUSTERING_MAX_ITEMS,
    DEFAULT_ANALYSIS_SOURCE_LIMITS,
    SOURCE_LABELS,
    get_trend_cluster,
    get_trend_cluster_items,
    get_trend_inventory_summary,
    get_trend_ranking_refresh_status,
    list_ranked_trends,
    promote_trend_cluster,
    calculate_prepared_trend_rankings,
    finalize_prepared_trend_rankings,
    prepare_trend_ranking_rebuild,
    refresh_trend_sources_short_connections,
)
from src.services.topic_angle_demand_contract import format_direction_for_request
from src.topic_angle_quality_diagnostic_ui import (
    render_topic_angle_quality_diagnostic_panel,
)
from src.services.topic_angle_ai_service import (
    execute_prepared_topic_angles,
    finalize_prepared_topic_angles,
    get_cluster_ai_profile,
    list_cluster_ai_angles,
    prepare_missing_topic_angles,
)
from src.services.topic_service import (
    add_manual_topic,
    archive_topic,
    get_topic,
    get_topic_sources,
    get_last_successful_import,
    import_preloaded_source_signals,
    list_topics,
    update_topic,
)
from src.services.workflow_navigation_service import (
    prepare_workflow_navigation_state,
)
from src.ui import (
    page_header_title,
    render_chatgpt_request_button,
    render_copy_button,
    trend_dashboard_action_label,
    trend_dashboard_navigation_locked,
)

APP_VERSION = read_app_version(PROJECT_ROOT / "VERSION")


st.set_page_config(
    page_title=build_browser_page_title("콘텐츠 트렌드 트래커", APP_VERSION),
    page_icon=":material/travel_explore:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

try:
    init_database(DEFAULT_DB_PATH)
except Exception as exc:
    if not is_database_lock_error(exc):
        raise
    from src.dashboard_background_refresh_ui import (
        render_lightweight_refresh_dashboard_if_active,
    )
    if render_lightweight_refresh_dashboard_if_active(st):
        st.stop()
    st.warning(
        "자동 수집 결과를 DuckDB에 저장하는 짧은 구간과 겹쳤습니다. "
        "잠시 후 아래 버튼으로 다시 시도하세요."
    )
    if st.button("데이터베이스 연결 다시 시도", type="primary"):
        st.rerun()
    st.stop()


API_BUTTON_ICON = ":material/api:"

TREND_REFRESH_ACTION_KEY = "trend_dashboard_pending_action"
TREND_REFRESH_MODEL_KEY = "trend_dashboard_pending_model"
TREND_REFRESH_PROGRESS_KEY = "trend_dashboard_progress"


def queue_trend_dashboard_action(action: str, *, model_name: str) -> None:
    selected_model = normalize_model_id(model_name)
    if not selected_model:
        raise ValueError("실행할 Gemini 모델을 선택하세요.")
    st.session_state[TREND_REFRESH_ACTION_KEY] = action
    st.session_state[TREND_REFRESH_MODEL_KEY] = selected_model
    st.rerun()


def _format_elapsed_seconds(value: float | int | None) -> str:
    try:
        seconds = float(value or 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    return f"{seconds:.1f}초"


def _format_file_size(size_bytes: int) -> str:
    size = max(0, int(size_bytes or 0))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} GB"


def _tooltip_text(value: object) -> str:
    return escape(str(value or "").strip(), quote=True).replace("\n", " &#10; ")


def _render_source_status_card(column, label: str, value: str) -> None:
    column.markdown(
        f"""
        <div class="trend-source-status-card">
            <div class="trend-source-status-label">{escape(label)}</div>
            <div class="trend-source-status-value">{escape(value)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_auto_analysis_model_selector(con) -> str:
    base_config = get_gemini_config()
    models = get_available_gemini_models(con, base_config=base_config)
    model_map = {model.model_id: model for model in models}
    model_ids = list(model_map)
    current_model = get_selected_gemini_model(
        con,
        MODEL_PURPOSE_AUTO,
        base_config=base_config,
    )
    if current_model not in model_map:
        model_ids.insert(0, current_model)

    selected_model = st.selectbox(
        "Gemini 자동 분석 모델",
        model_ids,
        index=model_ids.index(current_model),
        format_func=lambda model_id: model_display_label(model_map[model_id])
        if model_id in model_map
        else model_id,
        help=(
            "최신 데이터 수집·분석, 주제 방향 자동 생성, Windows 예약 수집이 "
            "공통으로 사용할 모델입니다. 변경값은 DuckDB 설정에 즉시 저장됩니다."
        ),
        key=f"trend_auto_analysis_model_{current_model}",
    )
    if selected_model != current_model:
        set_selected_gemini_model(con, MODEL_PURPOSE_AUTO, selected_model)

    rate_limit = model_rate_limit_reference(selected_model)
    rate_text = (
        f" · 참고 RPM {rate_limit['rpm']:,} / TPM {rate_limit['tpm']:,} / RPD {rate_limit['rpd']:,}"
        if rate_limit
        else " · 실제 한도는 Google AI Studio에서 확인"
    )
    st.caption(
        f"자동·예약 분석 모델: {selected_model}{rate_text} · "
        f"실행당 새 분석 대상 상위 {BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST}개 · "
        "3시간 주기라면 하루 약 8회 실행됩니다. 모델 변경은 새 분석 대상부터 적용되며, "
        "이미 완성된 제목·요약·작성 설정·방향은 API 사용량을 아끼기 위해 자동 재생성하지 않습니다."
    )
    return selected_model


def _render_explainable_metric(
    column,
    *,
    label: str,
    value: str,
    help_text: str,
    delta: str | None = None,
    align: str = "right",
) -> None:
    delta_html = (
        f'<div class="explainable-metric-delta">{escape(delta)}</div>'
        if delta
        else ""
    )
    tooltip = _tooltip_text(help_text)
    align_class = f" tooltip-align-{align}" if align else ""
    column.markdown(
        f"""
        <div class="explainable-metric-card">
            <div class="explainable-metric-label">
                <span>{escape(label)}</span>
                <span class="explainable-metric-help{align_class}" tabindex="0"
                      aria-label="{tooltip}"
                      data-tooltip="{tooltip}">?</span>
            </div>
            <div class="explainable-metric-value">{escape(value)}</div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _analysis_source_limits(con) -> dict[str, int]:
    return {
        "youtube": int(
            get_setting(con, "trend_analysis_youtube_limit", str(DEFAULT_ANALYSIS_SOURCE_LIMITS["youtube"]))
            or DEFAULT_ANALYSIS_SOURCE_LIMITS["youtube"]
        ),
        "naver": int(
            get_setting(con, "trend_analysis_naver_limit", str(DEFAULT_ANALYSIS_SOURCE_LIMITS["naver"]))
            or DEFAULT_ANALYSIS_SOURCE_LIMITS["naver"]
        ),
        "daum": int(
            get_setting(con, "trend_analysis_daum_limit", str(DEFAULT_ANALYSIS_SOURCE_LIMITS["daum"]))
            or DEFAULT_ANALYSIS_SOURCE_LIMITS["daum"]
        ),
        "google_trends": int(
            get_setting(con, "trend_analysis_google_limit", str(DEFAULT_ANALYSIS_SOURCE_LIMITS["google_trends"]))
            or DEFAULT_ANALYSIS_SOURCE_LIMITS["google_trends"]
        ),
        "wikipedia": int(
            get_setting(con, "trend_analysis_wikipedia_limit", str(DEFAULT_ANALYSIS_SOURCE_LIMITS["wikipedia"]))
            or DEFAULT_ANALYSIS_SOURCE_LIMITS["wikipedia"]
        ),
    }


def _trend_dashboard_runtime_settings(con) -> dict[str, object]:
    client_id, client_secret = get_naver_api_credentials()
    return {
        "parquet_path": get_setting(con, "youtube_parquet_path"),
        "client_id": client_id,
        "client_secret": client_secret,
        "kakao_rest_api_key": get_kakao_rest_api_key(),
        "google_enabled": _setting_enabled(
            get_setting(con, "google_trends_enabled", "true")
        ),
        "wikipedia_enabled": _setting_enabled(
            get_setting(con, "wikipedia_pageviews_enabled", "true")
        ),
        "seeds": [
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
    }


def run_trend_dashboard_action(
    *,
    action: str,
    auto_analysis_model: str,
    parquet_path: str,
    client_id: str,
    client_secret: str,
    kakao_rest_api_key: str,
    google_enabled: bool,
    wikipedia_enabled: bool,
    seeds: list[str],
    google_limit: int,
    wikipedia_limit: int,
    results_per_query: int,
    portal_query_limit: int,
    portal_pages_per_query: int,
    naver_max_workers: int,
    daum_max_workers: int,
    lookback_hours: int,
    naver_daily_limit: int,
    naver_monthly_limit: int,
    kakao_daily_limit: int,
    kakao_monthly_limit: int,
) -> None:
    """수집·정리 작업에서 실제 DB 읽기·쓰기 구간에만 연결합니다."""
    effective_auto_model = normalize_model_id(auto_analysis_model)
    if not effective_auto_model:
        raise ValueError("실행할 Gemini 모델을 선택하세요.")

    progress_placeholder = st.empty()
    notice_placeholder = st.empty()
    lock_attempt = acquire_trend_refresh_lock(
        PROJECT_ROOT,
        launcher=f"streamlit_{action}",
    )
    if not lock_attempt.acquired or lock_attempt.lock is None:
        active_owner = lock_attempt.active_owner
        safe_owner_detail = format_lock_owner_detail(active_owner)
        owner_detail = f" · {safe_owner_detail}" if safe_owner_detail else ""
        overlap_message = {
            "refresh": "최신 데이터 수집이 실행 중이어서 새 수집을 시작하지 않았습니다.",
            "rebuild": "최신 데이터 수집이 실행 중이어서 저장 자료 정리를 시작하지 않았습니다.",
            "angles": "최신 데이터 수집이 실행 중이어서 주제 방향 생성을 시작하지 않았습니다.",
        }.get(action, lock_attempt.message)
        st.session_state["trend_refresh_flash"] = {
            "summary": "최근 실행: 중복 작업 생략",
            "source_details": [],
            "maintenance_detail": None,
            "ranking_detail": None,
            "warnings": [f"{overlap_message}{owner_detail}"],
        }
        try:
            with db_connection() as con:
                record_skipped_overlap(
                    con,
                    run_type_for_dashboard_action(action),
                    summary=f"{overlap_message}{owner_detail}",
                )
        except Exception:
            # 이력 저장 실패 때문에 기존 중복 생략 동작을 오류로 바꾸지 않습니다.
            pass
        st.session_state.pop(TREND_REFRESH_ACTION_KEY, None)
        st.session_state.pop(TREND_REFRESH_MODEL_KEY, None)
        st.session_state.pop(TREND_REFRESH_PROGRESS_KEY, None)
        st.rerun()
        return

    refresh_lock = lock_attempt.lock
    run_id: str | None = None

    def show_progress(value: float, message: str) -> None:
        percent = max(0, min(100, int(round(value * 100))))
        st.session_state[TREND_REFRESH_PROGRESS_KEY] = {
            "value": percent,
            "message": message,
        }
        progress_text = message if "Gemini" in message else f"진행률 {percent}% · {message}"
        progress_placeholder.progress(percent, text=progress_text)

    def topic_angle_payload(result) -> dict[str, object]:
        return {
            **result.__dict__,
            "model_name": effective_auto_model,
        }

    def run_topic_angles(
        *,
        progress_start: float,
        progress_span: float,
    ):
        with db_connection() as con:
            config = build_gemini_config_for_purpose(
                con,
                MODEL_PURPOSE_AUTO,
            )
            config = replace(config, model=effective_auto_model)
            show_progress(
                progress_start,
                f"Gemini {config.model} · 분석 대상 준비 중",
            )
            preparation = prepare_missing_topic_angles(
                con,
                config=config,
                progress_callback=lambda value, message: show_progress(
                    progress_start + (progress_span * min(value, 0.10)),
                    message,
                ),
            )
        execution = execute_prepared_topic_angles(
            preparation,
            config=config,
            progress_callback=lambda value, message: show_progress(
                progress_start + (progress_span * value),
                message,
            ),
        )
        with db_connection() as con:
            return finalize_prepared_topic_angles(
                con,
                config=config,
                execution=execution,
                progress_callback=lambda value, message: show_progress(
                    progress_start + (progress_span * value),
                    message,
                ),
            )

    try:
        with db_connection() as con:
            run_id = start_collection_run(
                con,
                run_type_for_dashboard_action(action),
            )

        cleanup_result = None
        source_limits: dict[str, int] = {}
        if action in {"refresh", "rebuild"}:
            show_progress(0.02, "오래된 데이터 정리 여부 확인 중")
            with db_connection() as con:
                source_limits = _analysis_source_limits(con)
                cleanup_result = run_automatic_cleanup_if_due(
                    con,
                    enabled=_setting_enabled(
                        get_setting(con, "data_cleanup_enabled", "true")
                    ),
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

        if action == "refresh":
            show_progress(0.05, "수집기 준비 중")
            youtube_adapter = (
                YouTubeParquetAdapter(parquet_path)
                if Path(parquet_path).is_file()
                else None
            )
            naver_adapter = (
                NaverSearchAdapter(client_id, client_secret)
                if client_id and client_secret
                else None
            )
            daum_adapter = (
                DaumSearchAdapter(kakao_rest_api_key)
                if kakao_rest_api_key
                else None
            )
            google_adapter = GoogleTrendsRssAdapter("KR") if google_enabled else None
            wikipedia_adapter = (
                WikimediaPageviewsAdapter("ko.wikipedia.org")
                if wikipedia_enabled
                else None
            )
            refresh_result = refresh_trend_sources_short_connections(
                DEFAULT_DB_PATH,
                youtube_adapter=youtube_adapter,
                naver_adapter=naver_adapter,
                daum_adapter=daum_adapter,
                google_trends_adapter=google_adapter,
                wikipedia_adapter=wikipedia_adapter,
                configured_seed_queries=seeds,
                youtube_limit=300,
                google_trends_limit=google_limit,
                wikipedia_limit=wikipedia_limit,
                naver_display_per_query=results_per_query,
                daum_size_per_query=results_per_query,
                portal_query_limit=portal_query_limit,
                portal_pages_per_query=portal_pages_per_query,
                naver_max_workers=naver_max_workers,
                daum_max_workers=daum_max_workers,
                lookback_hours=lookback_hours,
                naver_daily_safety_limit=naver_daily_limit,
                naver_monthly_safety_limit=naver_monthly_limit,
                kakao_daum_daily_safety_limit=kakao_daily_limit,
                kakao_daum_monthly_safety_limit=kakao_monthly_limit,
                analysis_source_limits=source_limits,
                collection_run_id=run_id,
                progress_callback=lambda value, message: show_progress(
                    0.05 + (0.70 * value),
                    message,
                ),
            )

            topic_angle_result = None
            topic_angle_warning = ""
            ranking_clustering = (
                refresh_result.get("ranking", {}).get("ai_clustering") or {}
            )
            ranking_backlog = int(ranking_clustering.get("remaining_items", 0) or 0)
            defer_topic_angles = bool(
                ranking_backlog > 0
                or ranking_clustering.get("defer_topic_angles")
                or str(ranking_clustering.get("status") or "") == "skipped_overlap"
            )
            if defer_topic_angles:
                refresh_result["topic_angles"] = {
                    "status": "deferred_for_clustering_backlog",
                    "remaining_items": ranking_backlog,
                    "model_name": effective_auto_model,
                }
            else:
                try:
                    topic_angle_result = run_topic_angles(
                        progress_start=0.78,
                        progress_span=0.20,
                    )
                    refresh_result["topic_angles"] = topic_angle_payload(topic_angle_result)
                    if topic_angle_result.error_message:
                        topic_angle_warning = topic_angle_result.error_message
                except Exception as exc:
                    topic_angle_warning = f"Gemini 주제 방향 생성 실패: {exc}"
                    refresh_result["topic_angles"] = {
                        "status": "unexpected_error",
                        "error_message": str(exc),
                        "model_name": effective_auto_model,
                    }

            warnings: list[str] = []
            if topic_angle_warning:
                warnings.append(topic_angle_warning)
            source_details: list[dict[str, object]] = []
            source_specs = [
                ("youtube", "YouTube"),
                ("google_trends", "Google Trends"),
                ("wikipedia", "위키백과"),
                ("naver", "NAVER 뉴스·블로그"),
                ("daum", "Daum 웹문서·카페"),
            ]
            timings = refresh_result.get("timings") or {}
            source_errors = refresh_result.get("errors") or {}
            source_warnings = refresh_result.get("warnings") or {}
            total_collected = 0
            for key, label in source_specs:
                source_result = refresh_result.get(key)
                if source_result:
                    items_read = int(source_result.get("items_read", 0) or 0)
                    total_collected += items_read
                    raw_status = str(source_result.get("status") or "success")
                    display_status = {
                        "success": "성공",
                        "partial": "부분 성공",
                        "failed": "실패",
                        "skipped": "변경 없음",
                    }.get(raw_status, "성공")
                    source_details.append(
                        {
                            "label": label,
                            "status": display_status,
                            "items_read": items_read,
                            "items_added": int(
                                source_result.get("items_added", 0) or 0
                            ),
                            "items_updated": int(
                                source_result.get("items_updated", 0) or 0
                            ),
                            "items_skipped": int(
                                source_result.get("items_skipped", 0) or 0
                            ),
                            "unchanged": bool(
                                source_result.get("unchanged", False)
                            ),
                            "elapsed_seconds": float(timings.get(key) or 0.0),
                            "planned_request_count": int(
                                source_result.get("planned_request_count", 0) or 0
                            ),
                            "request_count": int(
                                source_result.get("request_count", 0) or 0
                            ),
                            "successful_requests": int(
                                source_result.get("successful_requests", 0) or 0
                            ),
                            "failed_requests": int(
                                source_result.get("failed_requests", 0) or 0
                            ),
                            "skipped_requests": int(
                                source_result.get("skipped_requests", 0) or 0
                            ),
                            "retry_count": int(
                                source_result.get("retry_count", 0) or 0
                            ),
                            "network_seconds": float(
                                source_result.get("network_seconds", 0.0) or 0.0
                            ),
                            "database_seconds": float(
                                source_result.get("database_seconds", 0.0) or 0.0
                            ),
                            "error": str(
                                source_errors.get(key)
                                or source_warnings.get(key)
                                or ""
                            ),
                        }
                    )
                elif key in source_errors:
                    source_details.append(
                        {
                            "label": label,
                            "status": "실패",
                            "items_read": 0,
                            "items_added": 0,
                            "items_updated": 0,
                            "items_skipped": 0,
                            "elapsed_seconds": float(timings.get(key) or 0.0),
                            "error": str(source_errors[key]),
                        }
                    )

            if youtube_adapter is None:
                warnings.append(
                    "YouTube 교환 파일이 없어 기존 저장 데이터만 사용했습니다."
                )
            if naver_adapter is None:
                warnings.append(
                    "NAVER 키가 없어 다른 출처와 기존 NAVER 데이터로 분석했습니다."
                )
            if daum_adapter is None:
                warnings.append(
                    "카카오 REST API 키가 없어 기존 Daum 데이터만 반영했습니다."
                )

            error_labels = {
                "youtube": "YouTube",
                "google_trends": "Google Trends",
                "wikipedia": "위키백과",
                "naver": "NAVER",
                "daum": "Daum",
            }
            for source_key, error_message in source_errors.items():
                warnings.append(
                    f"{error_labels.get(source_key, source_key)} 수집 실패: "
                    f"{error_message}"
                )
            for source_key, warning_message in source_warnings.items():
                warnings.append(
                    f"{error_labels.get(source_key, source_key)} 부분 수집: "
                    f"{warning_message}"
                )

            maintenance_detail = None
            if cleanup_result is not None:
                maintenance_detail = {
                    "source_items_deleted": cleanup_result.source_items_deleted,
                    "sync_runs_deleted": cleanup_result.sync_runs_deleted,
                    "collection_runs_deleted": cleanup_result.collection_runs_deleted,
                    "api_usage_rows_deleted": cleanup_result.api_usage_rows_deleted,
                    "total_rows_deleted": cleanup_result.total_rows_deleted,
                }

            ranking_result = refresh_result["ranking"]
            total_seconds = float(
                refresh_result.get("total_elapsed_seconds") or 0.0
            )
            ranking_seconds = float(timings.get("ranking") or 0.0)
            generated_angle_count = int(
                getattr(topic_angle_result, "generated_angles", 0) or 0
            )
            if ranking_backlog > 0:
                angle_summary = f" · 군집 대기 {ranking_backlog:,}개로 주제 방향 생성 보류"
            else:
                angle_summary = (
                    f" · Gemini {effective_auto_model} 방향 "
                    f"{generated_angle_count:,}개 저장"
                    if generated_angle_count
                    else f" · Gemini {effective_auto_model} 새 분석 없음"
                )
            st.session_state["trend_refresh_flash"] = {
                "summary": (
                    f"최근 수집: 총 {total_collected:,}개 · "
                    f"{_format_elapsed_seconds(total_seconds)}{angle_summary}"
                ),
                "source_details": source_details,
                "maintenance_detail": maintenance_detail,
                "ranking_detail": {
                    "items": int(ranking_result.get("items", 0) or 0),
                    "clusters": int(ranking_result.get("clusters", 0) or 0),
                    "elapsed_seconds": ranking_seconds,
                },
                "topic_angle_detail": (
                    topic_angle_payload(topic_angle_result) if topic_angle_result else None
                ),
                "warnings": warnings,
            }
            show_progress(1.0, "최신 데이터 수집·분석 완료")

        elif action == "rebuild":
            show_progress(0.12, "순위 계산용 저장 자료 읽는 중")
            rebuild_started = perf_counter()
            with db_connection() as con:
                ranking_preparation = prepare_trend_ranking_rebuild(
                    con,
                    lookback_hours=lookback_hours,
                    source_limits=source_limits,
                )
            show_progress(0.30, "DB 연결 없이 군집·순위 계산 중")
            ranking_calculation = calculate_prepared_trend_rankings(
                ranking_preparation,
                progress_callback=lambda value, message: show_progress(
                    0.30 + (0.38 * value),
                    message,
                ),
            )
            show_progress(0.70, "군집·순위 결과 저장 중")
            with db_connection() as con:
                result = finalize_prepared_trend_rankings(
                    con,
                    ranking_calculation,
                )
            rebuild_seconds = perf_counter() - rebuild_started

            topic_angle_result = None
            topic_angle_warning = ""
            clustering_detail = result.get("ai_clustering") or {}
            ranking_backlog = int(clustering_detail.get("remaining_items", 0) or 0)
            defer_topic_angles = bool(
                ranking_backlog > 0
                or clustering_detail.get("defer_topic_angles")
                or str(clustering_detail.get("status") or "") == "skipped_overlap"
            )
            if defer_topic_angles:
                result["topic_angles"] = {
                    "status": "deferred_for_clustering_backlog",
                    "remaining_items": ranking_backlog,
                    "model_name": effective_auto_model,
                }
            else:
                try:
                    show_progress(0.76, "방향이 없는 글감 확인 중")
                    topic_angle_result = run_topic_angles(
                        progress_start=0.76,
                        progress_span=0.22,
                    )
                    result["topic_angles"] = topic_angle_payload(topic_angle_result)
                    if topic_angle_result.error_message:
                        topic_angle_warning = topic_angle_result.error_message
                except Exception as exc:
                    topic_angle_warning = f"Gemini 주제 방향 생성 실패: {exc}"
                    result["topic_angles"] = {
                        "status": "unexpected_error",
                        "error_message": str(exc),
                        "model_name": effective_auto_model,
                    }

            generated_angle_count = int(
                getattr(topic_angle_result, "generated_angles", 0) or 0
            )
            processed_items = int(clustering_detail.get("processed_items", 0) or 0)
            batch_summary = (
                f" · 이번 군집 {processed_items:,}개 · 남은 미처리 {ranking_backlog:,}개"
            )
            if ranking_backlog > 0:
                angle_summary = " · 모든 군집 처리 후 주제 방향 자동 생성"
                completion_message = "최근 미처리 자료 증분 군집 완료"
            else:
                angle_summary = (
                    f" · Gemini 방향 {generated_angle_count:,}개 저장"
                    if generated_angle_count
                    else " · 새로 저장할 Gemini 방향 없음"
                )
                completion_message = "저장 자료 정리·주제 방향 보완 완료"
            show_progress(1.0, completion_message)
            st.session_state["trend_refresh_flash"] = {
                "summary": (
                    f"최근 자료 정리: 신호 {int(result['items']):,}개 · "
                    f"통합 주제 {int(result['clusters']):,}개 · "
                    f"{_format_elapsed_seconds(rebuild_seconds)}"
                    f"{batch_summary}{angle_summary}"
                ),
                "source_details": [],
                "maintenance_detail": (
                    {
                        "source_items_deleted": cleanup_result.source_items_deleted,
                        "sync_runs_deleted": cleanup_result.sync_runs_deleted,
                        "collection_runs_deleted": cleanup_result.collection_runs_deleted,
                        "api_usage_rows_deleted": cleanup_result.api_usage_rows_deleted,
                        "total_rows_deleted": cleanup_result.total_rows_deleted,
                    }
                    if cleanup_result is not None
                    else None
                ),
                "ranking_detail": {
                    "items": int(result["items"]),
                    "clusters": int(result["clusters"]),
                    "elapsed_seconds": rebuild_seconds,
                    "reused": bool(result.get("reused")),
                    "processed_items": int(clustering_detail.get("processed_items", 0) or 0),
                    "remaining_items": ranking_backlog,
                },
                "topic_angle_detail": (
                    topic_angle_payload(topic_angle_result) if topic_angle_result else None
                ),
                "warnings": [topic_angle_warning] if topic_angle_warning else [],
            }

        elif action == "angles":
            show_progress(0.06, "방향이 없는 글감 확인 중")
            topic_angle_result = run_topic_angles(
                progress_start=0.06,
                progress_span=0.92,
            )
            result = topic_angle_payload(topic_angle_result)
            generated_clusters = int(topic_angle_result.generated_clusters or 0)
            generated_angles = int(topic_angle_result.generated_angles or 0)
            requested_clusters = int(topic_angle_result.requested_clusters or 0)
            show_progress(1.0, "주제 방향·요약 자동 생성 완료")
            topic_angle_warning = topic_angle_result.error_message
            if topic_angle_result.status == "missing_api_key":
                topic_angle_warning = (
                    "GEMINI_API_KEY가 없어 주제 방향 자동 생성을 실행하지 않았습니다."
                )
            st.session_state["trend_refresh_flash"] = {
                "summary": (
                    f"최근 Gemini 글감 분석({effective_auto_model}): 대상 {requested_clusters:,}개 · "
                    f"저장 글감 {generated_clusters:,}개 · 방향 {generated_angles:,}개"
                ),
                "source_details": [],
                "maintenance_detail": None,
                "ranking_detail": None,
                "topic_angle_detail": result,
                "warnings": [topic_angle_warning] if topic_angle_warning else [],
            }
        else:
            raise ValueError(f"지원하지 않는 트렌드 작업입니다: {action}")

        with db_connection() as con:
            finish_collection_run(
                con,
                run_id,
                result=refresh_result if action == "refresh" else result,
            )

        st.session_state.pop(TREND_REFRESH_ACTION_KEY, None)
        st.session_state.pop(TREND_REFRESH_MODEL_KEY, None)
        st.session_state.pop(TREND_REFRESH_PROGRESS_KEY, None)
        st.rerun()
    except Exception as exc:
        if run_id is not None:
            try:
                with db_connection() as con:
                    finish_collection_run(con, run_id, error=exc)
            except Exception:
                pass
        st.session_state.pop(TREND_REFRESH_ACTION_KEY, None)
        st.session_state.pop(TREND_REFRESH_MODEL_KEY, None)
        st.session_state.pop(TREND_REFRESH_PROGRESS_KEY, None)
        progress_placeholder.empty()
        action_label = trend_dashboard_action_label(action) or "트렌드 작업"
        notice_placeholder.error(f"{action_label}을 완료하지 못했습니다: {exc}")
    finally:
        refresh_lock.release()


def db_connection():
    return connect_database(DEFAULT_DB_PATH)


def topic_options(con, *, interested_only: bool = False) -> dict[str, str]:
    frame = list_topics(con, interested_only=interested_only)
    options: dict[str, str] = {}
    if frame.empty:
        return options
    for _, row in frame.iterrows():
        status = TOPIC_STATUS_LABELS.get(str(row["status"]), str(row["status"]))
        options[str(row["topic_id"])] = f"{row['주제']} · {status} · 신호 {int(row['신호수'] or 0)}개"
    return options


def _metric_text(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _setting_enabled(value: str, default: bool = True) -> bool:
    clean = str(value or "").strip().casefold()
    if not clean:
        return default
    return clean in {"1", "true", "yes", "on", "enabled"}


def _source_option_label(source: dict) -> str:
    title = source.get("item_title") or source.get("raw_title") or "제목 없음"
    label = source.get("signal_type_label") or "기타 신호"
    metric_parts = []
    if source.get("topic_score") is not None:
        metric_parts.append(f"주제점수 {_metric_text(source['topic_score'])}")
    if source.get("view_count") is not None:
        metric_parts.append(f"조회수 {_metric_text(source['view_count'])}")
    if source.get("views_per_hour") is not None:
        metric_parts.append(f"시간당 {_metric_text(source['views_per_hour'])}")
    suffix = f" · {' · '.join(metric_parts)}" if metric_parts else ""
    return f"[{label}] {title}{suffix}"


def _reference_option_label(reference: dict) -> str:
    reference_type = reference.get("reference_type_label") or "참고 자료"
    title = reference.get("title") or "제목 없음"
    publisher = reference.get("publisher") or "출처 미입력"
    published_at = reference.get("published_at") or "게시일 미입력"
    return f"[{reference_type}] {title} · {publisher} · {published_at}"


def render_topic_reference_manager(con, topic_id: str) -> None:
    references = list_topic_references(con, topic_id)
    st.subheader("직접 등록한 사실 참고 자료")
    st.caption(
        "YouTube는 글감과 관심도 신호로 사용하고, 공식 기관·기업 발표·공공데이터·뉴스 등은 본문 사실 근거로 따로 등록합니다."
    )

    with st.expander("새 참고 자료 등록", expanded=not references):
        with st.form(f"reference_add_form_{topic_id}", clear_on_submit=True):
            reference_type = st.selectbox(
                "자료 유형",
                REFERENCE_TYPE_OPTIONS,
                format_func=REFERENCE_TYPE_LABELS.get,
            )
            title = st.text_input("자료 제목 *")
            publisher = st.text_input("기관·출처명", placeholder="예: 고용노동부, 한국전력공사")
            url = st.text_input("자료 URL *", placeholder="https://...")
            published_at = st.text_input("게시일·기준일", placeholder="예: 2026-07-14 또는 2026년 7월")
            memo = st.text_area(
                "활용 메모",
                placeholder="본문에서 확인할 내용이나 자료의 적용 범위를 기록하세요.",
                height=90,
            )
            submitted = st.form_submit_button("참고 자료 저장", type="primary")
            if submitted:
                try:
                    _, created = add_topic_reference(
                        con,
                        topic_id=topic_id,
                        reference_type=reference_type,
                        title=title,
                        publisher=publisher,
                        url=url,
                        published_at=published_at,
                        memo=memo,
                    )
                    st.success("새 참고 자료를 저장했습니다." if created else "동일 URL의 참고 자료를 갱신했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    if not references:
        st.info("등록된 사실 참고 자료가 없습니다. 자료팩은 만들 수 있지만 구체적인 사실은 작성 후 별도로 확인해야 합니다.")
        return

    for reference in references:
        label = _reference_option_label(reference)
        with st.expander(label):
            url = str(reference.get("url") or "").strip()
            if url:
                st.link_button("원본 자료 열기", url)
            with st.form(f"reference_edit_form_{reference['reference_id']}"):
                current_type = str(reference.get("reference_type") or "user_reference")
                if current_type not in REFERENCE_TYPE_OPTIONS:
                    current_type = "user_reference"
                reference_type = st.selectbox(
                    "자료 유형",
                    REFERENCE_TYPE_OPTIONS,
                    index=REFERENCE_TYPE_OPTIONS.index(current_type),
                    format_func=REFERENCE_TYPE_LABELS.get,
                )
                title = st.text_input("자료 제목", value=str(reference.get("title") or ""))
                publisher = st.text_input("기관·출처명", value=str(reference.get("publisher") or ""))
                reference_url = st.text_input("자료 URL", value=url)
                published_at = st.text_input(
                    "게시일·기준일",
                    value=str(reference.get("published_at") or ""),
                )
                memo = st.text_area(
                    "활용 메모",
                    value=str(reference.get("memo") or ""),
                    height=90,
                )
                save_col, archive_col = st.columns(2)
                save_button = save_col.form_submit_button("수정 저장", type="primary")
                archive_button = archive_col.form_submit_button("보관 처리")
                if save_button:
                    try:
                        update_topic_reference(
                            con,
                            reference_id=reference["reference_id"],
                            reference_type=reference_type,
                            title=title,
                            publisher=publisher,
                            url=reference_url,
                            published_at=published_at,
                            memo=memo,
                        )
                        st.success("참고 자료를 수정했습니다.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                if archive_button:
                    try:
                        archive_topic_reference(con, reference["reference_id"])
                        st.success("참고 자료를 보관 처리했습니다.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


def render_source_groups(sources: list[dict]) -> None:
    if not sources:
        st.info("이 주제에 연결된 트렌드 신호가 없습니다.")
        return

    group_order = [
        "emerging_topic",
        "recent_video",
        "content_idea",
        "google_trend",
        "wikipedia_pageview",
        "naver_news",
        "naver_blog",
        "daum_web",
        "daum_cafe",
        "other",
    ]
    grouped: dict[str, list[dict]] = {}
    for source in sources:
        grouped.setdefault(str(source.get("signal_type") or "other"), []).append(source)

    for signal_type in group_order:
        items = grouped.get(signal_type, [])
        if not items:
            continue
        label = items[0].get("signal_type_label") or "기타 신호"
        with st.expander(f"{label} · {len(items)}개", expanded=signal_type == "emerging_topic"):
            for index, source in enumerate(items):
                title = source.get("item_title") or source.get("raw_title") or "제목 없음"
                publisher = source.get("source_name") or source.get("source_type") or "출처"
                observed = source.get("observed_at") or "미상"
                metrics = (
                    f"신호값 {_metric_text(source.get('signal_value'))} · "
                    f"조회수 {_metric_text(source.get('view_count'))} · "
                    f"증가량 {_metric_text(source.get('view_delta'))} · "
                    f"시간당 {_metric_text(source.get('views_per_hour'))} · "
                    f"주제점수 {_metric_text(source.get('topic_score'))}"
                )
                left, right = st.columns([8, 1])
                left.markdown(f"**{title}**")
                left.caption(f"{publisher} · 관찰 {observed} · {metrics}")
                url = str(source.get("source_url") or "").strip()
                if url:
                    right.link_button("열기", url)
                if index < len(items) - 1:
                    st.divider()


NAVIGATION_ITEMS = [
    "오늘의 트렌드",
    "주제·트렌드",
    "AI 요청서",
    "AI 결과 가져오기",
    "글 편집",
    "발행 보조",
    "설정",
]

CONTENT_WORKFLOW_PAGES = [
    "AI 요청서",
    "AI 결과 가져오기",
    "글 편집",
    "발행 보조",
]


def navigate_to_page(page: str, **state_updates) -> None:
    prepare_workflow_navigation_state(st.session_state, page, state_updates)
    if page == "설정":
        st.session_state["settings_section"] = "기본 설정"
    st.rerun()


def render_content_workflow_progress(current_page: str) -> None:
    labels = [
        ("AI 요청서", "1. 요청서"),
        ("AI 결과 가져오기", "2. 결과 검사"),
        ("글 편집", "3. 편집·검증"),
        ("발행 보조", "4. 발행"),
    ]
    columns = st.columns(len(labels))
    for column, (page, label) in zip(columns, labels):
        clicked = column.button(
            label,
            key=f"workflow_{current_page}_{page}",
            type="primary" if page == current_page else "secondary",
            width="stretch",
        )
        if clicked and page != current_page:
            navigate_to_page(page)
    st.caption("글감 선택 → 웹 검색 요청서 또는 Gemini 초안 생성 → 결과 확인 → 편집·발행 순서로 진행합니다.")


def render_page_topbar(page: str) -> None:
    title = page_header_title(page)
    st.markdown(
        f'<div class="app-page-topbar" role="banner" aria-label="현재 메뉴"><span>{title}</span></div>',
        unsafe_allow_html=True,
    )


def apply_global_styles() -> None:
    st.markdown(
        """
        <style>
        header,
        [data-testid="stHeader"] {
            height: 3.18rem !important;
            background: transparent !important;
            z-index: 1000 !important;
            pointer-events: none !important;
        }
        [data-testid="stToolbar"] {
            position: fixed !important;
            top: 0.18rem !important;
            right: 1.0rem !important;
            height: 2.55rem !important;
            z-index: 1005 !important;
            display: flex !important;
            align-items: center !important;
            gap: 0.35rem !important;
            margin: 0 !important;
            padding: 0 !important;
            visibility: visible !important;
            opacity: 1 !important;
            pointer-events: auto !important;
        }
        [data-testid="stToolbar"] button {
            min-height: 2.55rem !important;
            height: 2.55rem !important;
            margin: 0 !important;
            padding: 0.32rem 0.45rem !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            pointer-events: auto !important;
        }
        [data-testid="stToolbar"] button p,
        [data-testid="stToolbar"] button span,
        [data-testid="stToolbar"] button svg {
            font-size: 0.88rem !important;
            line-height: 1 !important;
            margin: 0 !important;
            vertical-align: middle !important;
        }
        [data-testid="stMainMenu"],
        [data-testid="stMenu"],
        div[data-baseweb="popover"],
        div[data-baseweb="menu"] {
            z-index: 20000 !important;
            pointer-events: auto !important;
        }
        section[data-testid="stSidebar"],
        [data-testid="collapsedControl"] {
            display: none !important;
        }
        .block-container {
            width: 100%;
            max-width: none;
            padding: 1rem 1rem 2rem 1rem;
        }
        .block-container h1,
        .block-container h2,
        .block-container h3,
        .block-container h4,
        .block-container h5,
        .block-container h6,
        .block-container [data-testid="stVerticalBlockBorderWrapper"] {
            scroll-margin-top: 4.5rem;
        }
        .st-key-app_top_navigation {
            position: sticky;
            top: 0;
            z-index: 999;
            width: 100%;
            box-sizing: border-box;
            margin: 0 0 0.8rem 0;
            padding: 0.32rem 0.45rem;
            border: 1px solid rgba(128, 128, 128, 0.24);
            border-radius: 0.72rem;
            background: var(--background-color);
            backdrop-filter: blur(12px);
            box-shadow: 0 0.28rem 0.85rem rgba(0, 0, 0, 0.14);
        }
        .st-key-app_top_navigation [data-testid="stHorizontalBlock"] {
            gap: 0.14rem;
            align-items: center;
            min-width: 0;
        }
        .st-key-app_top_navigation [data-testid="stColumn"] {
            min-width: 0 !important;
        }
        .st-key-app_top_navigation [data-testid="stColumn"]:first-child [data-testid="stMarkdownContainer"] {
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 2.55rem;
            height: 2.55rem;
            margin: 0;
            padding: 0;
        }
        .top-nav-brand {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            min-height: 2.55rem;
            height: 2.55rem;
            margin: 0;
            padding: 0 0.14rem;
            color: var(--text-color);
            font-size: 1.08rem;
            font-weight: 850;
            letter-spacing: -0.045em;
            line-height: 1;
            text-align: center;
            white-space: nowrap;
        }
        .st-key-app_top_navigation .stButton > button,
        .st-key-app_top_navigation [data-testid="stPopover"] > button {
            min-height: 2.55rem;
            padding: 0.32rem 0.24rem;
            border-radius: 0.58rem;
            font-weight: 700;
            white-space: nowrap;
        }
        .st-key-app_top_navigation .stButton > button p,
        .st-key-app_top_navigation [data-testid="stPopover"] > button p {
            font-size: 0.88rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .st-key-app_top_navigation .stButton > button[kind="primary"] {
            border-color: var(--primary-color);
            box-shadow: inset 0 -3px 0 var(--primary-color);
        }
        .app-page-topbar {
            display: flex;
            align-items: center;
            min-height: 2.35rem;
            margin: 0 0 0.45rem 0;
            padding: 0 0.18rem 0.48rem 0.18rem;
            border-bottom: 1px solid rgba(128, 128, 128, 0.18);
        }
        .app-page-topbar span {
            color: var(--text-color);
            font-size: 1.08rem;
            font-weight: 760;
            letter-spacing: -0.035em;
        }
        .top-usage-panel {
            min-width: 26rem;
            color: var(--text-color);
        }
        .top-usage-title {
            margin-bottom: 0.58rem;
            font-size: 0.88rem;
            font-weight: 780;
        }
        .top-usage-row {
            margin-bottom: 0.62rem;
            padding-bottom: 0.52rem;
            border-bottom: 1px solid rgba(128, 128, 128, 0.18);
            font-size: 0.77rem;
            line-height: 1.4;
        }
        .top-usage-row:last-of-type {
            border-bottom: 0;
        }
        .top-usage-name {
            display: block;
            color: var(--text-color);
            font-weight: 720;
        }
        .top-usage-value {
            display: block;
            margin-top: 0.16rem;
            color: var(--text-color);
            opacity: 0.68;
        }
        .top-usage-value-line {
            display: block;
            white-space: nowrap;
        }
        .sidebar-quota-track {
            width: 100%;
            height: 0.32rem;
            margin: 0.14rem 0 0.28rem 0;
            overflow: hidden;
            border-radius: 999px;
            background: rgba(128, 128, 128, 0.2);
            box-shadow: inset 0 0 0 1px rgba(128, 128, 128, 0.08);
        }
        .sidebar-quota-fill {
            height: 100%;
            min-width: 0;
            border-radius: inherit;
            background: var(--primary-color);
        }
        .sidebar-quota-fill.sidebar-quota-mid { background: #f0b44d; }
        .sidebar-quota-fill.sidebar-quota-high { background: #f06a6a; }
        .sidebar-status {
            float: right;
            padding: 0.02rem 0.34rem;
            border: 1px solid rgba(128, 128, 128, 0.38);
            border-radius: 999px;
            font-size: 0.64rem;
            font-weight: 680;
        }
        .sidebar-status-ready { color: #58cf88; }
        .sidebar-status-warn { color: #e8ad45; }
        .top-usage-note {
            margin-top: 0.15rem;
            color: var(--text-color);
            font-size: 0.68rem;
            line-height: 1.35;
            opacity: 0.58;
        }
        .st-key-trend_action_source_row {
            margin: 0.2rem 0 0.48rem 0;
        }
        .st-key-trend_action_source_row [data-testid="stHorizontalBlock"],
        .st-key-trend_primary_metrics [data-testid="stHorizontalBlock"],
        .st-key-trend_diagnostic_metrics [data-testid="stHorizontalBlock"] {
            gap: 0.42rem;
        }
        .st-key-trend_action_source_row [data-testid="stHorizontalBlock"] {
            gap: 0.42rem !important;
            align-items: stretch !important;
        }
        .st-key-trend_action_source_row [data-testid="stColumn"] {
            display: flex !important;
            flex-direction: column !important;
            align-items: stretch !important;
        }
        .st-key-trend_action_source_row [data-testid="stColumn"] > div,
        .st-key-trend_action_source_row [data-testid="stElementWrapper"],
        .st-key-trend_action_source_row [data-testid="stMarkdownContainer"] {
            display: flex !important;
            flex-direction: column !important;
            flex: 1 1 auto !important;
            height: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        .st-key-trend_action_source_row .stButton {
            display: flex !important;
            flex-direction: column !important;
            height: 100% !important;
            width: 100% !important;
            margin: 0 !important;
        }
        .st-key-trend_action_source_row .stButton > button {
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            min-height: 4.15rem !important;
            height: 4.15rem !important;
            width: 100% !important;
            margin: 0 !important;
            padding: 0.42rem 0.62rem !important;
            border-radius: 0.55rem !important;
            box-sizing: border-box !important;
            white-space: normal !important;
            line-height: 1.18 !important;
            text-align: center !important;
        }
        .st-key-trend_action_source_row .stButton > button p {
            margin: 0 !important;
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            line-height: 1.18 !important;
            text-align: center !important;
            white-space: normal !important;
        }
        .trend-source-status-card {
            display: flex !important;
            flex-direction: column !important;
            justify-content: center !important;
            align-items: center !important;
            min-height: 4.15rem !important;
            height: 4.15rem !important;
            width: 100% !important;
            margin: 0 !important;
            padding: 0.42rem 0.62rem !important;
            border: 1px solid rgba(128, 128, 128, 0.34) !important;
            border-radius: 0.55rem !important;
            box-sizing: border-box !important;
            color: var(--text-color) !important;
            text-align: center !important;
        }
        .trend-source-status-label {
            margin: 0 0 0.18rem 0;
            font-size: 0.65rem;
            font-weight: 700;
            line-height: 1.08;
            white-space: nowrap;
            text-align: center;
        }
        .trend-source-status-value {
            margin: 0;
            font-size: 0.95rem;
            font-weight: 740;
            line-height: 1.1;
            white-space: nowrap;
            text-align: center;
        }
        .st-key-trend_action_source_row [data-testid="stMetric"],
        .st-key-trend_primary_metrics [data-testid="stMetric"],
        .st-key-trend_diagnostic_metrics [data-testid="stMetric"] {
            min-height: 3.95rem;
            padding: 0.38rem 0.5rem !important;
            border-radius: 0.55rem;
        }
        .st-key-trend_action_source_row [data-testid="stMetric"] {
            height: 4.15rem;
            min-height: 4.15rem;
            box-sizing: border-box;
        }
        .trend-intro-copy {
            display: flex;
            flex-direction: column;
            gap: 0.08rem;
            margin: -0.05rem 0 0.48rem 0;
            color: var(--text-color);
            font-size: 0.72rem;
            line-height: 1.35;
            opacity: 0.64;
        }
        .trend-intro-copy p {
            margin: 0;
        }
        .st-key-trend_action_source_row [data-testid="stMetricLabel"] p,
        .st-key-trend_primary_metrics [data-testid="stMetricLabel"] p,
        .st-key-trend_diagnostic_metrics [data-testid="stMetricLabel"] p {
            margin: 0 !important;
            font-size: 0.7rem !important;
            line-height: 1.15 !important;
        }
        .st-key-trend_action_source_row [data-testid="stMetricLabel"] p {
            font-size: 0.65rem !important;
            font-weight: 700 !important;
            white-space: nowrap;
        }
        .st-key-trend_action_source_row [data-testid="stMetricValue"],
        .st-key-trend_action_source_row [data-testid="stMetricValue"] > div {
            font-size: 0.98rem !important;
            font-weight: 720 !important;
            line-height: 1.1 !important;
            white-space: nowrap;
        }
        .st-key-trend_primary_metrics {
            margin: 0.15rem 0 0.42rem 0;
        }
        .st-key-trend_primary_metrics [data-testid="stMetricValue"],
        .st-key-trend_primary_metrics [data-testid="stMetricValue"] > div {
            font-size: 1.28rem !important;
            line-height: 1.08 !important;
            white-space: nowrap;
        }
        .st-key-trend_diagnostic_metrics {
            margin: 0.08rem 0 0.28rem 0;
        }
        .st-key-trend_diagnostic_metrics [data-testid="stMetric"] {
            min-height: 3.8rem;
        }
        .st-key-trend_diagnostic_metrics [data-testid="stMetricValue"],
        .st-key-trend_diagnostic_metrics [data-testid="stMetricValue"] > div {
            font-size: 1.15rem !important;
            line-height: 1.08 !important;
            white-space: nowrap;
        }
        .st-key-trend_selected_detail,
        .st-key-trend_selected_detail [data-testid="stVerticalBlock"],
        .st-key-trend_selected_detail [data-testid="stHorizontalBlock"],
        .st-key-trend_selected_detail [data-testid="stColumn"],
        .st-key-trend_selected_detail [data-testid="stColumn"] > div,
        .st-key-trend_primary_metrics,
        .st-key-trend_primary_metrics [data-testid="stHorizontalBlock"],
        .st-key-trend_primary_metrics [data-testid="stColumn"],
        .st-key-trend_diagnostic_metrics,
        .st-key-trend_diagnostic_metrics [data-testid="stHorizontalBlock"],
        .st-key-trend_diagnostic_metrics [data-testid="stColumn"],
        .explainable-metric-card {
            overflow: visible !important;
        }
        .explainable-metric-card {
            position: relative;
            min-height: 4.1rem;
            height: 100%;
            padding: 0.42rem 0.52rem;
            border: 1px solid rgba(128, 128, 128, 0.34);
            border-radius: 0.55rem;
            box-sizing: border-box;
            color: var(--text-color);
        }
        .explainable-metric-label {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.35rem;
            margin-bottom: 0.22rem;
            font-size: 0.7rem;
            font-weight: 680;
            line-height: 1.12;
        }
        .explainable-metric-value {
            font-size: 1.25rem;
            font-weight: 760;
            line-height: 1.08;
            white-space: nowrap;
        }
        .explainable-metric-delta {
            margin-top: 0.18rem;
            color: var(--primary-color);
            font-size: 0.64rem;
            line-height: 1.15;
        }
        .explainable-metric-help {
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 auto;
            width: 1.1rem;
            height: 1.1rem;
            border: 1px solid rgba(128, 128, 128, 0.48);
            border-radius: 999px;
            color: var(--text-color);
            font-size: 0.7rem;
            font-weight: 780;
            line-height: 1;
            cursor: help;
        }
        .explainable-metric-help::after {
            content: attr(data-tooltip);
            position: absolute;
            z-index: 999999 !important;
            top: calc(100% + 0.5rem);
            width: max-content;
            min-width: 360px;
            max-width: 480px;
            padding: 0.9rem 1.1rem;
            border: 1px solid #4a5568;
            border-radius: 0.65rem;
            background-color: #1a202c !important;
            box-shadow: 0 0.8rem 2rem rgba(0, 0, 0, 0.65), 0 0 0 1px rgba(255, 255, 255, 0.08);
            color: #f7fafc !important;
            font-size: 0.95rem !important;
            font-weight: 450 !important;
            line-height: 1.55 !important;
            text-align: left;
            white-space: pre-line !important;
            word-break: keep-all !important;
            overflow-wrap: break-word !important;
            opacity: 0;
            visibility: hidden;
            transform: translateY(-0.25rem);
            transition: opacity 0.12s ease, transform 0.12s ease, visibility 0.12s ease;
            pointer-events: none;
        }
        .explainable-metric-help.tooltip-align-left::after {
            left: -0.4rem;
            right: auto;
        }
        .explainable-metric-help.tooltip-align-right::after {
            right: -0.4rem;
            left: auto;
        }
        .explainable-metric-help:hover::after,
        .explainable-metric-help:focus::after,
        .explainable-metric-help:focus-within::after {
            opacity: 1 !important;
            visibility: visible !important;
            transform: translateY(0) !important;
        }
        .st-key-trend_selected_detail h3 {
            margin-top: 0.1rem;
            margin-bottom: 0.35rem;
            font-size: 1.16rem;
            line-height: 1.3;
        }
        .st-key-trend_selected_detail h4 {
            margin-top: 0.62rem;
            margin-bottom: 0.3rem;
            font-size: 1rem;
        }
        /* Candidate Table 10-Column Unified CSS Grid Layout */
        .st-key-trend_candidate_list [data-testid="stVerticalBlock"] {
            overflow-x: auto !important;
        }
        .st-key-trend_candidate_table_header {
            position: sticky;
            top: 0;
            z-index: 10;
            margin-bottom: 0;
            background: var(--background-color);
            border-bottom: 2px solid rgba(128, 128, 128, 0.35);
        }
        .st-key-trend_candidate_table_header [data-testid="stHorizontalBlock"],
        [class*="st-key-trend_candidate_row_"] [data-testid="stHorizontalBlock"] {
            display: grid !important;
            grid-template-columns: 38px 44px 48px minmax(180px, 1fr) 46px 44px 44px 50px 70px 52px !important;
            gap: 0 !important;
            align-items: stretch !important;
            width: 100% !important;
            min-width: 636px !important;
        }
        .st-key-trend_candidate_table_header [data-testid="stColumn"],
        [class*="st-key-trend_candidate_row_"] [data-testid="stColumn"] {
            display: flex !important;
            align-items: stretch !important;
            width: 100% !important;
            min-width: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        .st-key-trend_candidate_table_header [data-testid="stColumn"] > div,
        .st-key-trend_candidate_table_header [data-testid="stElementWrapper"],
        .st-key-trend_candidate_table_header [data-testid="stMarkdownContainer"],
        [class*="st-key-trend_candidate_row_"] [data-testid="stColumn"] > div,
        [class*="st-key-trend_candidate_row_"] [data-testid="stElementWrapper"],
        [class*="st-key-trend_candidate_row_"] [data-testid="stMarkdownContainer"] {
            display: flex !important;
            flex-direction: column !important;
            width: 100% !important;
            height: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        .candidate-tbl-hdr {
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 2.3rem;
            height: 100%;
            width: 100%;
            padding: 0.25rem 0.2rem;
            border-right: 1px solid rgba(128, 128, 128, 0.22);
            box-sizing: border-box;
            color: var(--text-color);
            font-size: 0.70rem;
            font-weight: 750;
            opacity: 0.85;
            white-space: normal;
            line-height: 1.15;
            text-align: center;
            word-break: keep-all;
            overflow-wrap: break-word;
        }
        .candidate-tbl-hdr:last-child {
            border-right: none;
        }
        [class*="st-key-trend_candidate_row_"] {
            margin-bottom: 0;
            border-bottom: 1px solid rgba(128, 128, 128, 0.15);
            transition: background-color 0.12s ease;
        }
        [class*="st-key-trend_candidate_row_"][class*="_selected"] {
            background-color: rgba(99, 102, 241, 0.15) !important;
            border-left: 3px solid var(--primary-color) !important;
        }
        .candidate-tbl-cell {
            display: flex;
            align-items: center;
            min-height: 2.6rem;
            height: 100%;
            width: 100%;
            padding: 0.3rem 0.35rem;
            border-right: 1px solid rgba(128, 128, 128, 0.18);
            box-sizing: border-box;
            color: var(--text-color);
            font-size: 0.75rem;
            font-weight: 550;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .candidate-tbl-cell:last-child {
            border-right: none;
        }
        .cell-center {
            justify-content: center;
            text-align: center;
        }
        .cell-right {
            justify-content: flex-end;
            text-align: right;
        }
        .cell-left {
            justify-content: flex-start;
            text-align: left;
        }
        .rank-val {
            font-weight: 700;
            font-size: 0.78rem;
            opacity: 0.85;
        }
        .score-val {
            font-weight: 700;
            color: var(--primary-color);
        }
        .total-val {
            font-weight: 700;
        }
        .src-val {
            font-variant-numeric: tabular-nums;
        }
        .src-val.zero {
            opacity: 0.38;
            font-weight: 400;
        }
        .status-tag {
            font-size: 0.68rem;
            font-weight: 700;
            padding: 0.1rem 0.2rem;
            border-radius: 0.3rem;
        }
        [class*="st-key-trend_candidate_row_"] .stButton {
            width: 100%;
            height: 100%;
            margin: 0;
        }
        [class*="st-key-trend_candidate_row_"] .stButton > button {
            min-height: 2.6rem;
            height: 100%;
            width: 100%;
            justify-content: flex-start;
            padding: 0.3rem 0.45rem;
            border-radius: 0;
            border: none;
            border-right: 1px solid rgba(128, 128, 128, 0.18);
            background: transparent;
            box-sizing: border-box;
            text-align: left;
        }
        [class*="st-key-trend_candidate_row_"] .stButton > button p {
            width: 100%;
            font-size: 0.78rem;
            font-weight: 600;
            line-height: 1.25;
            text-align: left;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        [class*="st-key-trend_candidate_row_"][class*="_selected"] .stButton > button {
            background: transparent !important;
            color: var(--primary-color) !important;
        }
        [class*="st-key-trend_candidate_row_"][class*="_selected"] .stButton > button p {
            font-weight: 750 !important;
        }
        @media (max-width: 1440px) {
            .block-container { padding-left: 0.75rem; padding-right: 0.75rem; }
            .top-nav-brand { font-size: 0.98rem; }
            .st-key-app_top_navigation .stButton > button p,
            .st-key-app_top_navigation [data-testid="stPopover"] > button p {
                font-size: 0.85rem;
            }
            [class*="st-key-trend_candidate_row_"] .stButton > button p { font-size: 0.74rem; }
            .candidate-source-counts { font-size: 0.64rem; }
            .st-key-trend_primary_metrics [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap;
            }
            .st-key-trend_primary_metrics [data-testid="stColumn"] {
                flex: 1 1 calc(33.333% - 0.45rem) !important;
                width: auto !important;
                min-width: 112px !important;
            }
        }
        @media (max-width: 1280px) {
            .st-key-trend_action_source_row [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap;
            }
            .st-key-trend_action_source_row [data-testid="stColumn"] {
                flex: 1 1 calc(33.333% - 0.6rem) !important;
                width: auto !important;
                min-width: 180px !important;
            }
            .st-key-trend_action_source_row [data-testid="stColumn"]:nth-child(-n+3) {
                flex-basis: calc(33.333% - 0.45rem) !important;
                min-width: 220px !important;
            }
        }
        @media (max-width: 980px) {
            .st-key-app_top_navigation {
                top: 0.45rem !important;
                right: 0.55rem;
                overflow-x: auto;
            }
            .st-key-app_top_navigation [data-testid="stHorizontalBlock"] {
                min-width: 64rem;
            }
            .top-nav-brand {
                font-size: 0.94rem;
                white-space: nowrap;
            }
            .top-usage-panel { min-width: 20rem; }
        }
        .st-key-settings_section_navigation {
            position: sticky;
            top: 3.25rem;
            z-index: 985;
            margin: 0.1rem 0 0.9rem 0;
            padding: 0.42rem;
            border: 1px solid rgba(128, 128, 128, 0.28);
            border-radius: 0.72rem;
            background: var(--background-color);
            backdrop-filter: blur(12px);
            box-shadow: 0 0.25rem 0.75rem rgba(0, 0, 0, 0.12);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _quota_percent(used: int, limit: int) -> float:
    if limit <= 0:
        return 0.0
    return max(0.0, min(100.0, (float(used) / float(limit)) * 100.0))


def _quota_bar_html(used: int, limit: int, label: str) -> str:
    percent = _quota_percent(used, limit)
    visible_percent = 0.0 if used <= 0 else max(0.6, percent)
    level_class = ""
    if percent >= 85.0:
        level_class = " sidebar-quota-high"
    elif percent >= 60.0:
        level_class = " sidebar-quota-mid"
    return (
        f'<div class="sidebar-quota-track" title="{label} {percent:.2f}% 사용">'
        f'<div class="sidebar-quota-fill{level_class}" style="width:{visible_percent:.3f}%"></div>'
        '</div>'
    )


def render_top_usage_panel() -> None:
    try:
        with db_connection() as con:
            naver_client_id, naver_client_secret = get_naver_api_credentials()
            kakao_key = get_kakao_rest_api_key()
            google_enabled = _setting_enabled(
                get_setting(con, "google_trends_enabled", "true")
            )
            wikipedia_enabled = _setting_enabled(
                get_setting(con, "wikipedia_pageviews_enabled", "true")
            )
            naver_daily_limit = int(
                get_setting(con, "naver_search_daily_safety_limit", "25000") or 25000
            )
            naver_monthly_limit = int(
                get_setting(con, "naver_search_monthly_safety_limit", "775000") or 775000
            )
            naver_usage = get_naver_search_usage(
                con,
                daily_limit=naver_daily_limit,
                monthly_limit=naver_monthly_limit,
            )
            kakao_usage = get_kakao_daum_usage(
                con,
                daily_limit=int(get_setting(con, "kakao_daum_daily_safety_limit", "50000") or 50000),
                monthly_limit=int(get_setting(con, "kakao_daum_monthly_safety_limit", "3000000") or 3000000),
            )
            google_usage = get_local_api_usage(
                con,
                provider=GOOGLE_TRENDS_PROVIDER,
                api_name=GOOGLE_TRENDS_API,
            )
            wikipedia_usage = get_local_api_usage(
                con,
                provider=WIKIMEDIA_PROVIDER,
                api_name=WIKIMEDIA_API,
            )
            youtube_path = str(get_setting(con, "youtube_parquet_path") or "").strip()
            youtube_file_exists = bool(youtube_path and Path(youtube_path).is_file())
            youtube_item_count = int(
                con.execute(
                    "SELECT COUNT(*) FROM source_items WHERE source_type = 'youtube'"
                ).fetchone()[0]
                or 0
            )
            youtube_last_import = get_last_successful_import(con, "youtube_parquet")
            latest_failures = {
                str(row[0])
                for row in con.execute(
                    """
                    SELECT source_type
                    FROM (
                        SELECT source_type, status,
                               ROW_NUMBER() OVER (
                                   PARTITION BY source_type
                                   ORDER BY started_at DESC
                               ) AS row_num
                        FROM sync_runs
                    ) latest
                    WHERE row_num = 1 AND status = 'failed'
                    """
                ).fetchall()
            }
    except Exception as exc:
        st.caption(f"수집·사용량 상태를 불러오지 못했습니다: {exc}")
        return

    def status_html(label: str, *, warning: bool = False) -> str:
        css = "warn" if warning else "ready"
        return f'<span class="sidebar-status sidebar-status-{css}">{label}</span>'

    naver_status = (
        status_html("최근 실패", warning=True)
        if "naver_search" in latest_failures
        else status_html("준비")
        if naver_client_id and naver_client_secret
        else status_html("키 필요", warning=True)
    )
    daum_status = (
        status_html("최근 실패", warning=True)
        if "daum_search" in latest_failures
        else status_html("준비")
        if kakao_key
        else status_html("키 필요", warning=True)
    )
    google_status = (
        status_html("최근 실패", warning=True)
        if "google_trends_rss" in latest_failures
        else status_html("준비")
        if google_enabled
        else status_html("사용 안 함", warning=True)
    )
    wikipedia_status = (
        status_html("최근 실패", warning=True)
        if "wikimedia_pageviews" in latest_failures
        else status_html("준비")
        if wikipedia_enabled
        else status_html("사용 안 함", warning=True)
    )
    youtube_status = (
        status_html("최근 실패", warning=True)
        if "youtube_parquet" in latest_failures
        else status_html("준비")
        if youtube_file_exists
        else status_html("파일 없음", warning=True)
    )

    naver_daily_bar = _quota_bar_html(
        naver_usage.daily_used,
        naver_usage.daily_limit,
        "NAVER 오늘",
    )
    naver_monthly_bar = _quota_bar_html(
        naver_usage.monthly_used,
        naver_usage.monthly_limit,
        "NAVER 이번 달",
    )
    daum_daily_bar = _quota_bar_html(
        kakao_usage.daily_used,
        kakao_usage.daily_limit,
        "Daum 오늘",
    )
    daum_monthly_bar = _quota_bar_html(
        kakao_usage.monthly_used,
        kakao_usage.monthly_limit,
        "Daum 이번 달",
    )
    youtube_import_text = str(youtube_last_import or "기록 없음")

    st.markdown(
        f"""
        <div class="top-usage-panel">
            <div class="top-usage-title">로컬 수집·호출량</div>
            <div class="top-usage-row">
                <span class="top-usage-name">NAVER 검색 {naver_status}</span>
                <span class="top-usage-value">
                    <span class="top-usage-value-line">오늘 {naver_usage.daily_used:,}/{naver_usage.daily_limit:,}</span>
                    {naver_daily_bar}
                    <span class="top-usage-value-line">월 {naver_usage.monthly_used:,}/{naver_usage.monthly_limit:,}</span>
                    {naver_monthly_bar}
                </span>
            </div>
            <div class="top-usage-row">
                <span class="top-usage-name">Daum 검색 {daum_status}</span>
                <span class="top-usage-value">
                    <span class="top-usage-value-line">오늘 {kakao_usage.daily_used:,}/{kakao_usage.daily_limit:,}</span>
                    {daum_daily_bar}
                    <span class="top-usage-value-line">월 {kakao_usage.monthly_used:,}/{kakao_usage.monthly_limit:,}</span>
                    {daum_monthly_bar}
                </span>
            </div>
            <div class="top-usage-row">
                <span class="top-usage-name">Google 검색 트렌드 {google_status}</span>
                <span class="top-usage-value">
                    <span class="top-usage-value-line">오늘 {google_usage.daily_used:,}회 · 월 {google_usage.monthly_used:,}회</span>
                </span>
            </div>
            <div class="top-usage-row">
                <span class="top-usage-name">위키백과 조회수 {wikipedia_status}</span>
                <span class="top-usage-value">
                    <span class="top-usage-value-line">오늘 {wikipedia_usage.daily_used:,}회 · 월 {wikipedia_usage.monthly_used:,}회</span>
                </span>
            </div>
            <div class="top-usage-row">
                <span class="top-usage-name">YouTube 신호 가져오기 {youtube_status}</span>
                <span class="top-usage-value">
                    <span class="top-usage-value-line">저장 신호 {youtube_item_count:,}건</span>
                    <span class="top-usage-value-line">최근 가져오기 {youtube_import_text}</span>
                </span>
            </div>
            <div class="top-usage-note">Google Trends는 Google 검색 관심도이며 YouTube와 별개입니다. YouTube는 이 앱에서 API를 호출하지 않고 교환 Parquet을 읽습니다.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_top_navigation() -> str:
    active_action = st.session_state.get(TREND_REFRESH_ACTION_KEY)
    navigation_locked = trend_dashboard_navigation_locked(active_action)

    current_page = str(st.session_state.get("page") or NAVIGATION_ITEMS[0])
    if current_page not in NAVIGATION_ITEMS:
        current_page = NAVIGATION_ITEMS[0]
        prepare_workflow_navigation_state(st.session_state, current_page)

    if navigation_locked and current_page != NAVIGATION_ITEMS[0]:
        current_page = NAVIGATION_ITEMS[0]
        prepare_workflow_navigation_state(st.session_state, current_page)

    labels = {
        "오늘의 트렌드": "오늘의 트렌드",
        "주제·트렌드": "주제·트렌드",
        "AI 요청서": "AI 요청서",
        "AI 결과 가져오기": "AI 결과",
        "글 편집": "글 편집",
        "발행 보조": "발행 보조",
        "설정": "설정",
    }

    with st.container(key="app_top_navigation"):
        menu_columns = st.columns(
            [1.05] + [0.68] * len(NAVIGATION_ITEMS) + [0.78, 0.95],
            gap="small",
            vertical_alignment="center",
        )
        menu_columns[0].markdown(
            '<div class="top-nav-brand">콘텐츠 트렌드 트래커</div>',
            unsafe_allow_html=True,
        )

        for menu_col, item in zip(menu_columns[1:-2], NAVIGATION_ITEMS):
            clicked = menu_col.button(
                labels[item],
                key=f"nav_{item}",
                type="primary" if item == current_page else "secondary",
                width="stretch",
                disabled=navigation_locked,
            )
            if clicked and not navigation_locked and item != current_page:
                navigate_to_page(item)

        with menu_columns[-2].popover(
            "수집·사용량",
            use_container_width=True,
        ):
            render_top_usage_panel()

        if navigation_locked:
            action_label = trend_dashboard_action_label(active_action) or "트렌드 작업"
            st.info(
                f"{action_label} 작업 중입니다. 중복 실행 방지를 위해 완료될 때까지 메뉴 이동을 잠시 막습니다.",
                icon=":material/sync:",
            )

    return current_page



def render_trend_refresh_feedback(flash: object) -> None:
    progress_state = st.session_state.get(TREND_REFRESH_PROGRESS_KEY)
    if isinstance(progress_state, dict):
        progress_value = int(progress_state.get("value") or 0)
        progress_message = str(progress_state.get("message") or "수집 중")
        st.progress(progress_value, text=f"진행률 {progress_value}% · {progress_message}")
        return

    if not isinstance(flash, dict):
        return

    summary_text = str(flash.get("summary") or "최근 실행 결과")
    with st.expander(summary_text, expanded=False):
        for detail in flash.get("source_details") or []:
            label = str(detail.get("label") or "출처")
            status = str(detail.get("status") or "성공")
            elapsed = _format_elapsed_seconds(detail.get("elapsed_seconds"))
            if status == "변경 없음":
                st.markdown(f"**⏭️ {label}** · 교환 파일 변경 없음 · 전체 {elapsed}")
            elif status in {"성공", "부분 성공"}:
                skipped = int(detail.get("items_skipped", 0) or 0)
                skipped_text = f" · 형식 제외 {skipped:,}개" if skipped else ""
                request_count = int(detail.get("request_count", 0) or 0)
                planned_request_count = int(detail.get("planned_request_count", 0) or 0)
                successful_requests = int(detail.get("successful_requests", 0) or 0)
                failed_requests = int(detail.get("failed_requests", 0) or 0)
                skipped_requests = int(detail.get("skipped_requests", 0) or 0)
                retry_count = int(detail.get("retry_count", 0) or 0)
                network_seconds = float(detail.get("network_seconds", 0.0) or 0.0)
                database_seconds = float(detail.get("database_seconds", 0.0) or 0.0)
                performance_text = ""
                if request_count or planned_request_count:
                    performance_text = (
                        f" · 실제 요청 {request_count:,}회/계획 {planned_request_count:,}회"
                        f" · 성공 {successful_requests:,} · 실패 {failed_requests:,}"
                        f" · 생략 {skipped_requests:,} · 재시도 {retry_count:,}"
                        f" · API {_format_elapsed_seconds(network_seconds)}"
                        f" · 저장 {_format_elapsed_seconds(database_seconds)}"
                    )
                icon = "✅" if status == "성공" else "⚠️"
                st.markdown(
                    f"**{icon} {label}** · {int(detail.get('items_read', 0) or 0):,}개 · "
                    f"신규 {int(detail.get('items_added', 0) or 0):,}개 · "
                    f"갱신 {int(detail.get('items_updated', 0) or 0):,}개"
                    f"{skipped_text} · 전체 {elapsed}{performance_text}"
                )
                if status == "부분 성공" and detail.get("error"):
                    st.caption(str(detail.get("error")))
            else:
                st.markdown(
                    f"**⚠️ {label}** · 수집 실패 · {elapsed} · "
                    f"{str(detail.get('error') or '오류 내용 없음')}"
                )

        maintenance_detail = flash.get("maintenance_detail") or {}
        if maintenance_detail:
            st.markdown(
                f"**🧹 자동 데이터 정리** · 원본 {int(maintenance_detail.get('source_items_deleted', 0) or 0):,}개 · "
                f"실행 기록 {int(maintenance_detail.get('sync_runs_deleted', 0) or 0):,}개 · "
                f"전체 이력 {int(maintenance_detail.get('collection_runs_deleted', 0) or 0):,}개 · "
                f"호출 기록 {int(maintenance_detail.get('api_usage_rows_deleted', 0) or 0):,}개 삭제"
            )

        ranking_detail = flash.get("ranking_detail") or {}
        if ranking_detail:
            reused_text = " · 기존 결과 재사용" if ranking_detail.get("reused") else ""
            st.markdown(
                f"**📊 통합 순위 계산** · 신호 {int(ranking_detail.get('items', 0) or 0):,}개 → "
                f"주제 {int(ranking_detail.get('clusters', 0) or 0):,}개 · "
                f"{_format_elapsed_seconds(ranking_detail.get('elapsed_seconds'))}{reused_text}"
            )

        topic_angle_detail = flash.get("topic_angle_detail") or {}
        if topic_angle_detail:
            model_name = str(topic_angle_detail.get("model_name") or "").strip()
            model_text = f" · 모델 {model_name}" if model_name else ""
            st.markdown(
                f"**✨ Gemini 글감 분석**{model_text} · 요청 {int(topic_angle_detail.get('requested_clusters', 0) or 0):,}개 · "
                f"저장 글감 {int(topic_angle_detail.get('generated_clusters', 0) or 0):,}개 · "
                f"방향 {int(topic_angle_detail.get('generated_angles', 0) or 0):,}개 · "
                f"요청 묶음 {int(topic_angle_detail.get('completed_batches', 0) or 0):,}/"
                f"{int(topic_angle_detail.get('requested_batches', 0) or 0):,}개 완료 · "
                f"API 시도 {int(topic_angle_detail.get('attempts', 0) or 0):,}회 · "
                f"글감 기회 기준 {float(topic_angle_detail.get('min_opportunity_score', 0) or 0):g}점 이상 · "
                f"{_format_elapsed_seconds(topic_angle_detail.get('duration_seconds'))}"
            )

        for warning in flash.get("warnings") or []:
            st.warning(str(warning))


def _render_trend_inventory_diagnostics(con, *, lookback_hours: int) -> None:
    summary = get_trend_inventory_summary(con, lookback_hours=lookback_hours)
    with st.expander("수집 데이터가 적어 보일 때 확인", expanded=False):
        st.info(
            f"글감 목록은 누적 전체가 아니라 최근 {summary['lookback_hours']}시간 원본만 사용해 "
            "매번 다시 계산합니다. 같은 URL·외부 ID가 반복 수집되면 새 행을 늘리지 않고 기존 행의 "
            "최근 포착 시각과 포착 횟수를 갱신합니다."
        )
        cols = st.columns(5)
        cols[0].metric("저장 원본 전체", f"{summary['stored_items']:,}개")
        cols[1].metric(
            f"최근 {summary['lookback_hours']}시간 원본",
            f"{summary['window_items']:,}개",
        )
        cols[2].metric("최근 24시간 신규", f"{summary['new_items_24h']:,}개")
        cols[3].metric("최근 24시간 갱신", f"{summary['touched_items_24h']:,}개")
        cols[4].metric("현재 통합 주제", f"{summary['cluster_count']:,}개")
        st.caption(
            f"현재 통합 주제 구성: 추천 {summary['recommended_count']:,}개 · "
            f"검토 {summary['review_count']:,}개 · 보류 {summary['hold_count']:,}개. "
            "신규보다 갱신이 많아도 예약 수집이 멈춘 것은 아닐 수 있습니다."
        )
        frame = pd.DataFrame(
            [
                {
                    "출처": item["label"],
                    "저장 전체": item["stored_items"],
                    f"최근 {summary['lookback_hours']}시간": item["window_items"],
                    "최근 제목": item["window_unique_titles"],
                    "24시간 신규": item["new_items_24h"],
                    "24시간 갱신": item["touched_items_24h"],
                    "최근 포착": str(item["last_imported_at"] or "-"),
                }
                for item in summary["sources"]
            ]
        )
        st.dataframe(frame, hide_index=True, width="stretch")
        inactive = [
            item["label"]
            for item in summary["sources"]
            if item["touched_items_24h"] == 0
        ]
        if inactive:
            st.warning(
                "최근 24시간 갱신이 없는 출처: " + ", ".join(inactive)
                + ". 수집·사용량의 최근 실행 상세에서 해당 출처 오류를 확인하세요."
            )


def render_trend_dashboard() -> None:
    flash = st.session_state.get("trend_refresh_flash")
    pending_action = str(
        st.session_state.get(TREND_REFRESH_ACTION_KEY) or ""
    ).strip()
    pending_model = normalize_model_id(
        st.session_state.get(TREND_REFRESH_MODEL_KEY)
    )
    if pending_action in {"refresh", "rebuild", "angles"}:
        with db_connection() as con:
            runtime = _trend_dashboard_runtime_settings(con)
            if not pending_model:
                pending_model = get_selected_gemini_model(
                    con,
                    MODEL_PURPOSE_AUTO,
                )
            background_job = (
                create_clustering_job(con, launcher="dashboard")
                if pending_action == "rebuild"
                else None
            )
        if pending_action == "rebuild":
            warnings: list[str] = []
            if background_job and background_job.get("created"):
                try:
                    pid = launch_clustering_job(
                        str(background_job["job_id"]),
                        db_path=DEFAULT_DB_PATH,
                        lookback_hours=int(runtime["lookback_hours"]),
                    )
                    summary = (
                        f"2단계 군집 작업을 백그라운드에서 시작했습니다 · PID {pid} · "
                        f"요청당 {int(background_job.get('batch_size', 0)):,}개 · "
                        f"최대 {int(background_job.get('max_batches', 0)):,}회"
                    )
                except Exception as exc:
                    summary = "2단계 군집 백그라운드 작업을 시작하지 못했습니다."
                    warnings.append(str(exc))
                    with db_connection() as con:
                        con.execute(
                            """
                            UPDATE trend_clustering_jobs
                            SET status = 'failed', error_message = ?,
                                heartbeat_at = ?, finished_at = ?
                            WHERE job_id = ?
                            """,
                            [str(exc), datetime.now(), datetime.now(), str(background_job["job_id"])],
                        )
            else:
                summary = str(
                    (background_job or {}).get("message")
                    or "이미 2단계 군집 작업이 실행 중입니다."
                )
            st.session_state.pop(TREND_REFRESH_ACTION_KEY, None)
            st.session_state.pop(TREND_REFRESH_MODEL_KEY, None)
            st.session_state["trend_refresh_flash"] = {
                "summary": summary,
                "source_details": [],
                "maintenance_detail": None,
                "ranking_detail": None,
                "topic_angle_detail": None,
                "warnings": warnings,
            }
            st.rerun()

        run_trend_dashboard_action(
            action=pending_action,
            auto_analysis_model=pending_model,
            parquet_path=str(runtime["parquet_path"]),
            client_id=str(runtime["client_id"]),
            client_secret=str(runtime["client_secret"]),
            kakao_rest_api_key=str(runtime["kakao_rest_api_key"]),
            google_enabled=bool(runtime["google_enabled"]),
            wikipedia_enabled=bool(runtime["wikipedia_enabled"]),
            seeds=list(runtime["seeds"]),
            google_limit=int(runtime["google_limit"]),
            wikipedia_limit=int(runtime["wikipedia_limit"]),
            results_per_query=int(runtime["results_per_query"]),
            portal_query_limit=int(runtime["portal_query_limit"]),
            portal_pages_per_query=int(runtime["portal_pages_per_query"]),
            naver_max_workers=int(runtime["naver_max_workers"]),
            daum_max_workers=int(runtime["daum_max_workers"]),
            lookback_hours=int(runtime["lookback_hours"]),
            naver_daily_limit=int(runtime["naver_daily_limit"]),
            naver_monthly_limit=int(runtime["naver_monthly_limit"]),
            kakao_daily_limit=int(runtime["kakao_daily_limit"]),
            kakao_monthly_limit=int(runtime["kakao_monthly_limit"]),
        )
        return

    with db_connection() as con:
        runtime = _trend_dashboard_runtime_settings(con)
        parquet_path = str(runtime["parquet_path"])
        client_id = str(runtime["client_id"])
        client_secret = str(runtime["client_secret"])
        kakao_rest_api_key = str(runtime["kakao_rest_api_key"])
        google_enabled = bool(runtime["google_enabled"])
        wikipedia_enabled = bool(runtime["wikipedia_enabled"])
        seeds = list(runtime["seeds"])
        google_limit = int(runtime["google_limit"])
        wikipedia_limit = int(runtime["wikipedia_limit"])
        results_per_query = int(runtime["results_per_query"])
        portal_query_limit = int(runtime["portal_query_limit"])
        portal_pages_per_query = int(runtime["portal_pages_per_query"])
        naver_max_workers = int(runtime["naver_max_workers"])
        daum_max_workers = int(runtime["daum_max_workers"])
        lookback_hours = int(runtime["lookback_hours"])
        naver_daily_limit = int(runtime["naver_daily_limit"])
        naver_monthly_limit = int(runtime["naver_monthly_limit"])
        kakao_daily_limit = int(runtime["kakao_daily_limit"])
        kakao_monthly_limit = int(runtime["kakao_monthly_limit"])
        naver_usage = get_naver_search_usage(
            con,
            daily_limit=naver_daily_limit,
            monthly_limit=naver_monthly_limit,
        )
        kakao_usage = get_kakao_daum_usage(
            con,
            daily_limit=kakao_daily_limit,
            monthly_limit=kakao_monthly_limit,
        )
        active_clustering_job = get_active_clustering_job(con)
        latest_clustering_attempt = get_latest_clustering_attempt(con)
        clustering_job = get_representative_clustering_job(con)

        estimated_portal_calls = portal_query_limit * 2 * portal_pages_per_query
        st.markdown(
            f"""
            <div class="trend-intro-copy">
                <p>YouTube·NAVER·Daum·Google Trends·위키백과의 최근 관심 신호를 함께 분석해 오늘 쓸 만한 글감을 점수순으로 보여줍니다. 최신 자료 수집과 순위 계산이 끝나면 새 고득점 글감을 아래에서 선택한 Gemini 모델로 한 번 더 분석합니다.</p>
                <p>분석 범위 최근 {lookback_hours}시간 · 포털 탐색어 최대 {portal_query_limit}개 · 탐색어당 {portal_pages_per_query}페이지 · 출처별 실행 예상 최대 {estimated_portal_calls:,}회 · NAVER 오늘 {naver_usage.daily_used:,}회 · Daum 오늘 {kakao_usage.daily_used:,}회</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_trend_inventory_diagnostics(con, lookback_hours=lookback_hours)
        selected_auto_model = _render_auto_analysis_model_selector(con)
        st.caption(
            "API 아이콘이 있는 버튼은 외부 API를 호출할 수 있습니다. "
            "저장 자료 정리·주제 방향 자동 생성은 새 분석 대상이 있을 때만 Gemini API를 사용합니다."
        )
        refresh_lock_status = inspect_trend_refresh_lock(PROJECT_ROOT)
        clustering_lock_status = inspect_trend_clustering_lock(
            data_directory=Path(DEFAULT_DB_PATH).resolve().parent,
        )
        action_guard = build_trend_dashboard_action_guard(
            refresh_status=refresh_lock_status,
            clustering_status=clustering_lock_status,
            active_clustering_job=active_clustering_job,
        )
        for notice in action_guard.notices():
            st.info(notice)

        with st.container(key="trend_action_source_row"):
            action_source_cols = st.columns(
                [1.433, 1.433, 1.433, 1.0, 1.0, 1.0, 1.12, 1.12],
                gap="small",
            )
            if action_source_cols[0].button(
                "최신 데이터 수집·분석",
                type="primary",
                width="stretch",
                icon=API_BUTTON_ICON,
                disabled=bool(pending_action) or action_guard.is_disabled("refresh"),
                help=action_guard.reason_for("refresh") or None,
            ):
                queue_trend_dashboard_action(
                    "refresh",
                    model_name=selected_auto_model,
                )

            if action_source_cols[1].button(
                "저장 자료 정리·순위 다시 계산",
                width="stretch",
                icon=API_BUTTON_ICON,
                disabled=bool(pending_action) or action_guard.is_disabled("rebuild"),
                help=action_guard.reason_for("rebuild") or None,
            ):
                queue_trend_dashboard_action(
                    "rebuild",
                    model_name=selected_auto_model,
                )

            if action_source_cols[2].button(
                "주제 방향 자동 생성",
                width="stretch",
                icon=API_BUTTON_ICON,
                disabled=bool(pending_action) or action_guard.is_disabled("angles"),
                help=action_guard.reason_for("angles") or None,
            ):
                queue_trend_dashboard_action(
                    "angles",
                    model_name=selected_auto_model,
                )

            _render_source_status_card(
                action_source_cols[3],
                "YouTube",
                "준비됨" if Path(parquet_path).is_file() else "파일 없음",
            )
            _render_source_status_card(
                action_source_cols[4],
                "NAVER",
                "API HUB" if client_id and client_secret else "키 필요",
            )
            _render_source_status_card(
                action_source_cols[5],
                "Daum",
                "REST API" if kakao_rest_api_key else "키 필요",
            )
            _render_source_status_card(
                action_source_cols[6],
                "Google Trends",
                "공식 RSS" if google_enabled else "사용 안 함",
            )
            _render_source_status_card(
                action_source_cols[7],
                "위키백과",
                "공개 Pageviews" if wikipedia_enabled else "사용 안 함",
            )

        render_trend_refresh_feedback(flash)

        recent_attempt_notice = build_recent_clustering_attempt_notice(
            clustering_job,
            latest_clustering_attempt,
        )
        if recent_attempt_notice:
            st.info(recent_attempt_notice)

        if clustering_job is not None:
            status_labels = {
                "queued": "대기",
                "running": "실행 중",
                "success": "완료",
                "partial": "시험 범위 완료",
                "failed": "실패",
                "skipped_overlap": "중복 실행 생략",
                "stale": "상태 확인 필요",
            }
            display_status = str(
                clustering_job.get("display_status")
                or clustering_job.get("status")
                or ""
            )
            metric_values = build_clustering_metric_values(clustering_job)
            st.markdown("#### 최근 2단계 군집 작업")
            job_cols = st.columns(6)
            job_cols[0].metric(
                "상태",
                status_labels.get(display_status, display_status or "기록 없음"),
                border=True,
            )
            job_cols[1].metric(
                "완료 배치",
                metric_values["snapshot"],
                border=True,
            )
            job_cols[2].metric(
                "처리 1차 군집",
                metric_values["processed_units"],
                border=True,
            )
            job_cols[3].metric(
                "처리 원문",
                metric_values["processed_source_items"],
                border=True,
            )
            job_cols[4].metric(
                "남은 미처리",
                metric_values["remaining_items"],
                border=True,
            )
            job_cols[5].metric(
                "총 토큰",
                metric_values["total_tokens"],
                border=True,
            )
            processed_units = int(clustering_job.get("processed_units") or 0)
            total_tokens = int(clustering_job.get("total_tokens") or 0)
            estimated_tokens_per_1000 = (
                round(total_tokens * 1000 / processed_units)
                if processed_units > 0
                else 0
            )
            if str(clustering_job.get("status") or "") == "skipped_overlap":
                st.caption("실행 전 중복 차단 · Gemini 호출 및 DB 반영 없음")
            else:
                st.caption(
                    f"기존 2차 군집 연결 {int(clustering_job.get('existing_links') or 0):,}개 · "
                    f"새 군집 {int(clustering_job.get('new_clusters') or 0):,}개 · "
                    f"불확실 {int(clustering_job.get('uncertain_units') or 0):,}개 · "
                    f"충돌 차단 {int(clustering_job.get('conflict_units') or 0):,}개 · "
                    f"검토 전환 {int(clustering_job.get('needs_review_items') or 0):,}개 · "
                    f"1,000개당 예상 토큰 {estimated_tokens_per_1000:,}"
                )
            render_clustering_job_error(st, clustering_job)
            if st.button(
                "군집 작업 상태 새로고침",
                key="refresh_clustering_job_status",
                width="stretch",
            ):
                st.rerun()
            batches = list(clustering_job.get("batches") or ())
            if batches:
                with st.expander("배치별 토큰·품질 로그", expanded=False):
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "배치": int(row.get("batch_number") or 0),
                                    "상태": str(row.get("status") or ""),
                                    "검색 미처리": int(row.get("scanned_pending_items") or 0),
                                    "전체 1차 군집": int(row.get("all_first_stage_units") or 0),
                                    "요청 1차 군집": int(row.get("first_stage_units") or 0),
                                    "요청 원문": int(row.get("source_items") or 0),
                                    "URL 중복 절감": int(row.get("url_merged_items") or 0),
                                    "URL 충돌 분리": int(row.get("url_conflict_splits") or 0),
                                    "동일 제목 병합": int(row.get("title_merged_groups") or 0),
                                    "다음 배치 대기": int(row.get("deferred_units") or 0),
                                    "기존 후보": int(row.get("existing_candidate_refs") or 0),
                                    "처리": int(row.get("processed_units") or 0),
                                    "기존 연결": int(row.get("existing_links") or 0),
                                    "새 군집": int(row.get("new_clusters") or 0),
                                    "불확실": int(row.get("uncertain_units") or 0),
                                    "충돌": int(row.get("conflict_units") or 0),
                                    "입력 토큰": int(row.get("input_tokens") or 0),
                                    "출력 토큰": int(row.get("output_tokens") or 0),
                                    "사고 토큰": int(row.get("thought_tokens") or 0),
                                    "총 토큰": int(row.get("total_tokens") or 0),
                                    "시간(ms)": int(row.get("duration_ms") or 0),
                                    "오류": str(row.get("error_message") or ""),
                                }
                                for row in batches
                            ]
                        ),
                        hide_index=True,
                        width="stretch",
                    )

        if not (client_id and client_secret):
            st.info(
                "NAVER 키가 없어도 다른 출처 데이터로 분석합니다. "
                "뉴스·블로그 근거까지 수집하려면 .env에 NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 입력하세요."
            )

        if not kakao_rest_api_key:
            st.info(
                "Daum 웹문서·카페 검색을 사용하려면 .env에 KAKAO_REST_API_KEY를 입력하세요. "
                "키가 없어도 나머지 출처 수집은 계속됩니다."
            )

        ranking_refresh_status = get_trend_ranking_refresh_status(
            con,
            lookback_hours=lookback_hours,
            source_limits=_analysis_source_limits(con),
        )
        if (
            ranking_refresh_status.get("needs_rebuild")
            and ranking_refresh_status.get("has_rankings")
        ):
            st.info(
                "저장 데이터 또는 순위 규칙 변경이 감지되었습니다. "
                "현재는 마지막 계산 결과를 표시하며, 다음 최신 데이터 수집·분석 때 자동 반영됩니다. "
                "바로 반영하려면 ‘저장 자료 정리·순위 다시 계산’을 누르세요."
            )

        st.subheader("글감 후보")
        st.caption(
            "전체 관련 원문을 함께 확인해 구체 제품·서비스·인물·사건이 반복되는 후보만 추천·검토합니다. "
            "기본 정렬은 실제 글로 발전시키기 좋은 `글감 기회` 순이며, 급상승·자료 완성도·최근 확인 순으로 바꿀 수 있습니다. "
            "날짜·요일·일반 안내만 남거나 대상 근거가 부족한 후보는 삭제하지 않고 `보류`로 숨깁니다."
        )
        filter_col1, filter_col2, filter_col3 = st.columns([1.15, 0.9, 1.1])
        candidate_scope = filter_col1.segmented_control(
            "후보 표시",
            ["추천·검토만", "추천만", "전체"],
            default="추천·검토만",
            key="trend_candidate_scope",
        )
        minimum_score = filter_col2.slider(
            "최소 트렌드 점수",
            min_value=0,
            max_value=100,
            value=30,
            step=5,
            key="trend_minimum_score",
        )
        sort_label = filter_col3.selectbox(
            "정렬",
            ["글감 추천순", "급상승순", "자료 완성도순", "최근 확인순"],
            index=0,
            key="trend_candidate_sort",
        )
        sort_by = {
            "글감 추천순": "opportunity",
            "급상승순": "trend",
            "자료 완성도순": "quality",
            "최근 확인순": "recent",
        }[sort_label]

        recommendation_statuses: list[str] | None = None
        if candidate_scope == "추천·검토만":
            recommendation_statuses = ["recommended", "review"]
        elif candidate_scope == "추천만":
            recommendation_statuses = ["recommended"]

        rankings = list_ranked_trends(
            con,
            limit=100,
            minimum_score=minimum_score,
            recommendation_statuses=recommendation_statuses,
            sort_by=sort_by,
        )
        if rankings.empty:
            st.warning("현재 조건에 맞는 후보가 없습니다. 후보 표시를 ‘전체’로 바꾸거나 최소 점수를 낮춰보세요.")
            return

        matched_count = int(rankings["matched_count"].iloc[0] or 0)
        loaded_count = len(rankings)
        ranking_cluster_ids = [str(value) for value in rankings["cluster_id"].tolist()]
        feedback_map = list_trend_feedback_map(con, ranking_cluster_ids)
        feedback_summary = get_trend_feedback_summary(con)
        review_col1, review_col2 = st.columns([1.2, 2.2], vertical_alignment="center")
        hide_rejected_feedback = review_col1.checkbox(
            "쓸모없음·잘못 묶임 숨기기",
            value=True,
            key="hide_rejected_trend_feedback",
        )
        review_col2.caption(
            "내 평가: "
            f"좋은 글감 {feedback_summary['good']} · "
            f"애매 {feedback_summary['ambiguous']} · "
            f"쓸모없음 {feedback_summary['useless']} · "
            f"잘못 묶임 {feedback_summary['false_merge']}"
        )

        filtered_rankings = rankings.copy()

        if hide_rejected_feedback and not filtered_rankings.empty:
            filtered_rankings = filtered_rankings[
                ~filtered_rankings["cluster_id"].astype(str).map(
                    lambda cluster_id: str(
                        (feedback_map.get(cluster_id) or {}).get("feedback_type") or ""
                    ) in REJECTED_FEEDBACK_TYPES
                )
            ]

        if filtered_rankings.empty:
            st.warning("현재 조건에 맞는 후보가 없습니다. 후보 표시를 ‘전체’로 바꾸거나 최소 점수를 낮춰보세요.")
            return

        candidate_rankings = filtered_rankings.reset_index(drop=True)
        hidden_feedback_count = loaded_count - len(candidate_rankings)
        cluster_ids = [str(value) for value in candidate_rankings["cluster_id"].tolist()]
        if st.session_state.get("selected_trend_cluster_id") not in cluster_ids:
            st.session_state["selected_trend_cluster_id"] = cluster_ids[0]

        selected_cluster_id = str(st.session_state["selected_trend_cluster_id"])
        list_col, detail_col = st.columns([1.55, 1.75], gap="medium")

        with list_col:
            st.subheader("글감 목록")
            count_text = (
                f"현재 조건 일치 {matched_count:,}개 중 현재 정렬 상위 {loaded_count:,}개를 불러왔습니다."
                if matched_count > loaded_count
                else f"현재 조건 일치 {matched_count:,}개를 불러왔습니다."
            )
            hidden_text = (
                f" 내 평가로 {hidden_feedback_count:,}개를 숨겨 목록에는 "
                f"{len(candidate_rankings):,}개가 표시됩니다."
                if hidden_feedback_count
                else ""
            )
            st.caption(
                f"{count_text}{hidden_text} "
                "왼쪽 후보를 누르면 오른쪽 상세 내용이 바뀝니다."
            )
            with st.container(key="trend_candidate_list", height=620, border=True, gap=None):
                with st.container(key="trend_candidate_table_header"):
                    hcols = st.columns(10, gap=None, vertical_alignment="center")
                    hcols[0].markdown('<div class="candidate-tbl-hdr cell-center">순위</div>', unsafe_allow_html=True)
                    hcols[1].markdown('<div class="candidate-tbl-hdr cell-center">판정</div>', unsafe_allow_html=True)
                    hcols[2].markdown('<div class="candidate-tbl-hdr cell-right">트렌드</div>', unsafe_allow_html=True)
                    hcols[3].markdown('<div class="candidate-tbl-hdr cell-center">제목</div>', unsafe_allow_html=True)
                    hcols[4].markdown('<div class="candidate-tbl-hdr cell-right">기회</div>', unsafe_allow_html=True)
                    hcols[5].markdown('<div class="candidate-tbl-hdr cell-right">NAVER</div>', unsafe_allow_html=True)
                    hcols[6].markdown('<div class="candidate-tbl-hdr cell-right">Daum</div>', unsafe_allow_html=True)
                    hcols[7].markdown('<div class="candidate-tbl-hdr cell-right">YouTube</div>', unsafe_allow_html=True)
                    hcols[8].markdown('<div class="candidate-tbl-hdr cell-right">Google Trends</div>', unsafe_allow_html=True)
                    hcols[9].markdown('<div class="candidate-tbl-hdr cell-right">위키백과</div>', unsafe_allow_html=True)
                for index, row in candidate_rankings.iterrows():
                    cluster_id = str(row["cluster_id"])
                    is_selected = cluster_id == selected_cluster_id
                    feedback_type = str(
                        (feedback_map.get(cluster_id) or {}).get("feedback_type") or ""
                    )
                    feedback_badge = {
                        "good": "✓ ",
                        "ambiguous": "? ",
                        "useless": "× ",
                        "false_merge": "⚠ ",
                    }.get(feedback_type, "")

                    row_key = f"trend_candidate_row_{index}{'_selected' if is_selected else ''}"
                    with st.container(key=row_key):
                        rcols = st.columns(10, gap=None, vertical_alignment="center")
                        rcols[0].markdown(
                            f'<div class="candidate-tbl-cell cell-center rank-val">{index + 1}</div>',
                            unsafe_allow_html=True,
                        )
                        status_str = str(row["판정"])
                        rcols[1].markdown(
                            f'<div class="candidate-tbl-cell cell-center status-tag status-{status_str}">{status_str}</div>',
                            unsafe_allow_html=True,
                        )
                        score_val = float(row["트렌드점수"])
                        rcols[2].markdown(
                            f'<div class="candidate-tbl-cell cell-right score-val">{score_val:.1f}</div>',
                            unsafe_allow_html=True,
                        )
                        title_label = f"{feedback_badge}{row['주제']}"
                        if rcols[3].button(
                            title_label,
                            key=f"trend_candidate_{cluster_id}",
                            type="primary" if is_selected else "secondary",
                            use_container_width=True,
                        ):
                            if not is_selected:
                                st.session_state["selected_trend_cluster_id"] = cluster_id
                                st.rerun()

                        opportunity_val = float(row["글감기회"])
                        rcols[4].markdown(
                            f'<div class="candidate-tbl-cell cell-right total-val">{opportunity_val:.1f}</div>',
                            unsafe_allow_html=True,
                        )
                        naver_cnt = int(row.get("naver_count", 0) or 0)
                        rcols[5].markdown(
                            f'<div class="candidate-tbl-cell cell-right src-val{" zero" if not naver_cnt else ""}">{naver_cnt}</div>',
                            unsafe_allow_html=True,
                        )
                        daum_cnt = int(row.get("daum_count", 0) or 0)
                        rcols[6].markdown(
                            f'<div class="candidate-tbl-cell cell-right src-val{" zero" if not daum_cnt else ""}">{daum_cnt}</div>',
                            unsafe_allow_html=True,
                        )
                        yt_cnt = int(row.get("youtube_count", 0) or 0)
                        rcols[7].markdown(
                            f'<div class="candidate-tbl-cell cell-right src-val{" zero" if not yt_cnt else ""}">{yt_cnt}</div>',
                            unsafe_allow_html=True,
                        )
                        gt_cnt = int(row.get("google_count", 0) or 0)
                        rcols[8].markdown(
                            f'<div class="candidate-tbl-cell cell-right src-val{" zero" if not gt_cnt else ""}">{gt_cnt}</div>',
                            unsafe_allow_html=True,
                        )
                        wiki_cnt = int(row.get("wikipedia_count", 0) or 0)
                        rcols[9].markdown(
                            f'<div class="candidate-tbl-cell cell-right src-val{" zero" if not wiki_cnt else ""}">{wiki_cnt}</div>',
                            unsafe_allow_html=True,
                        )

        cluster = get_trend_cluster(con, selected_cluster_id)
        items = get_trend_cluster_items(con, selected_cluster_id)
        if cluster is None:
            return

        status_label = {
            "recommended": "추천",
            "review": "검토",
            "hold": "보류",
        }.get(str(cluster.get("recommendation_status") or "review"), "검토")
        selected_rank = cluster_ids.index(selected_cluster_id) + 1
        stored_ai_angles = list_cluster_ai_angles(con, selected_cluster_id)
        stored_ai_profile = get_cluster_ai_profile(con, selected_cluster_id)

        with detail_col:
            st.subheader("선택한 글감")
            with st.container(border=True, key="trend_selected_detail"):
                display_title = (
                    str(stored_ai_profile.get("display_title") or "").strip()
                    if stored_ai_profile
                    else ""
                ) or str(cluster["canonical_title"])
                st.markdown(f"### {selected_rank}위 · {status_label} · {display_title}")
                if stored_ai_profile:
                    if display_title != str(cluster["canonical_title"]):
                        st.caption(f"원본 군집 제목: {cluster['canonical_title']}")
                    st.markdown("#### Gemini 핵심 요약")
                    st.write(str(stored_ai_profile.get("summary") or ""))
                    content_plan = stored_ai_profile.get("content_plan") or {}
                    if content_plan:
                        with st.expander(
                            "Gemini 추천 작성 설정",
                            expanded=False,
                            icon=":material/edit_note:",
                        ):
                            plan_cols = st.columns(4)
                            plan_cols[0].caption("독자 대상")
                            plan_cols[0].write(str(content_plan.get("audience") or "-"))
                            plan_cols[1].caption("글 목적")
                            plan_cols[1].write(str(content_plan.get("purpose") or "-"))
                            plan_cols[2].caption("카테고리")
                            plan_cols[2].write(str(content_plan.get("category") or "-"))
                            plan_cols[3].caption("권장 분량")
                            plan_cols[3].write(
                                f"{_metric_text(content_plan.get('target_length') or 2500)}자"
                            )
                            outline_items = [
                                str(item)
                                for item in content_plan.get("outline") or []
                                if str(item).strip()
                            ]
                            if outline_items:
                                st.caption("권장 본문 구성")
                                for item in outline_items:
                                    st.markdown(f"- {item}")
                            timeliness = (
                                content_plan.get("timeliness")
                                if isinstance(content_plan.get("timeliness"), dict)
                                else {}
                            )
                            if timeliness:
                                timeliness_labels = {
                                    "breaking": "즉시성 높음",
                                    "short_lived": "단기 글감",
                                    "ongoing": "지속 관찰",
                                    "evergreen": "상시 활용",
                                }
                                freshness_hours = int(
                                    timeliness.get("freshness_window_hours") or 0
                                )
                                freshness_text = (
                                    f"{freshness_hours // 24}일"
                                    if freshness_hours >= 24
                                    and freshness_hours % 24 == 0
                                    else f"{freshness_hours}시간"
                                )
                                timing_cols = st.columns(4)
                                timing_cols[0].caption("게시 시급성")
                                timing_cols[0].write(
                                    timeliness_labels.get(
                                        str(timeliness.get("type") or ""),
                                        str(timeliness.get("type") or "-"),
                                    )
                                )
                                timing_cols[1].caption("우선순위")
                                timing_cols[1].write(
                                    f"{int(timeliness.get('publish_priority') or 0)}/5"
                                )
                                timing_cols[2].caption("권장 유효기간")
                                timing_cols[2].write(freshness_text)
                                timing_cols[3].caption("작성 전 재확인")
                                timing_cols[3].write(
                                    "필요"
                                    if timeliness.get("recheck_before_writing")
                                    else "선택"
                                )
                                if str(timeliness.get("reason") or "").strip():
                                    st.caption(
                                        "시급성 판단: "
                                        + str(timeliness.get("reason") or "").strip()
                                    )

                            primary_direction_reason = str(
                                content_plan.get("primary_direction_reason") or ""
                            ).strip()
                            if primary_direction_reason:
                                st.caption("1순위 방향 추천 이유")
                                st.write(primary_direction_reason)

                            evidence_plan = (
                                content_plan.get("evidence_plan")
                                if isinstance(content_plan.get("evidence_plan"), dict)
                                else {}
                            )
                            if evidence_plan:
                                evidence_cols = st.columns(3)
                                evidence_sections = [
                                    (
                                        "필요한 공식 근거",
                                        evidence_plan.get("required_source_types") or [],
                                    ),
                                    (
                                        "현재 부족한 근거",
                                        evidence_plan.get("evidence_gaps") or [],
                                    ),
                                    (
                                        "공식 자료 검색어",
                                        evidence_plan.get("official_search_queries") or [],
                                    ),
                                ]
                                for evidence_col, (label, values) in zip(
                                    evidence_cols, evidence_sections
                                ):
                                    evidence_col.caption(label)
                                    clean_values = [
                                        str(item).strip()
                                        for item in values
                                        if str(item).strip()
                                    ]
                                    if clean_values:
                                        for item in clean_values:
                                            evidence_col.markdown(f"- {item}")
                                    else:
                                        evidence_col.write("-")

                            plan_rule_cols = st.columns(2)
                            title_rule_items = [
                                str(item)
                                for item in content_plan.get("title_rules") or []
                                if str(item).strip()
                            ]
                            forbidden_items = [
                                str(item)
                                for item in content_plan.get("forbidden_expressions") or []
                                if str(item).strip()
                            ]
                            if title_rule_items:
                                plan_rule_cols[0].caption("주제별 제목 원칙")
                                for item in title_rule_items:
                                    plan_rule_cols[0].markdown(f"- {item}")
                            if forbidden_items:
                                plan_rule_cols[1].caption("주제별 금지 표현")
                                for item in forbidden_items:
                                    plan_rule_cols[1].markdown(f"- {item}")
                            st.caption(
                                "이 설정은 글감을 AI 요청서로 넘길 때 자동 채워지며, "
                                "요청서를 저장한 뒤에는 글감별 사용자 설정으로 보존됩니다."
                            )

                diagnostics = build_cluster_diagnostics(cluster, items)
                score_reasons = [
                    str(reason) for reason in cluster.get("score_reasons") or [] if str(reason)
                ]
                quality_reasons = [
                    str(reason) for reason in cluster.get("quality_reasons") or [] if str(reason)
                ]
                score_reason_text = " · ".join(score_reasons) or "저장된 점수 세부 근거가 없습니다."
                quality_reason_text = " · ".join(quality_reasons) or "제목과 출처 구성을 기준으로 계산했습니다."
                rediscovery_score = float(cluster.get("rediscovery_score") or 0)
                quality_score = float(cluster.get("quality_score") or 0)
                trend_score = float(cluster.get("trend_score") or 0)
                opportunity_score = float(cluster.get("opportunity_score") or 0)
                fact_risk_score = float(cluster.get("fact_risk_score") or 0)
                unique_evidence_count = int(diagnostics.get("unique_evidence_count") or 0)
                duplicate_count = int(diagnostics.get("duplicate_count") or 0)
                publisher_count = int(diagnostics.get("publisher_count") or 0)
                source_type_count = int(diagnostics.get("source_type_count") or 0)

                status_help = (
                    "추천·검토·보류 중 현재 단계입니다. "
                    f"현재 {status_label}: 트렌드 {trend_score:.1f}, 글감 기회 {opportunity_score:.1f}, "
                    f"자료 완성도 {quality_score:.1f}, 독립 근거 {unique_evidence_count}건, "
                    f"독립 발행처 {publisher_count}곳을 함께 판단했습니다. "
                    f"품질 판단 근거: {quality_reason_text}"
                )
                trend_help = (
                    "최근성, 독립 출처 교차 확인, 출처별 근거량, 뉴스·커뮤니티 반응, "
                    "YouTube 확산, Google·위키 관심도, 반복 포착을 더하고 중복과 품질을 보정한 0~100점입니다. "
                    f"현재 계산 근거: {score_reason_text}"
                )
                opportunity_help = (
                    "이 주제로 실제 글을 만들기 좋은 정도입니다. 최근성·교차 확인·정보성·참고 자료 깊이를 높게 보고, "
                    "사실 위험과 중복을 낮게 봅니다. "
                    f"현재 독립 근거 {unique_evidence_count}건, 발행처 {publisher_count}곳, "
                    f"사실 위험 {fact_risk_score:.1f}, 중복 추정 {duplicate_count}건이 반영됐습니다."
                )
                quality_help = (
                    "아직 작성되지 않은 글의 품질이 아니라, 제목의 구체성·출처 다양성·중복 제외 근거량을 계산한 자료 준비 점수입니다. "
                    "검색·위키 또는 YouTube 단독 신호인지도 함께 반영한 0~100점입니다. "
                    f"현재 판단 근거: {quality_reason_text}"
                )
                risk_reason_text = next(
                    (
                        reason
                        for reason in score_reasons
                        if reason.startswith("사실 확인 위험")
                    ),
                    "저장된 세부 위험 근거가 없습니다.",
                )
                risk_help = (
                    "민감 분야 표현뿐 아니라 순위·일정·가격·날씨처럼 시점에 따라 바뀌는 내용, "
                    "날짜·금액·비율 같은 수치 주장과 독립 사실 근거 부족을 함께 보는 0~30점입니다. "
                    f"현재 계산 근거: {risk_reason_text}"
                )
                item_help = (
                    "이 글감 군집에 연결된 원본 신호의 총개수입니다. "
                    f"현재 총 {int(cluster['item_count'])}개 중 중복 제외 근거 {unique_evidence_count}건, "
                    f"중복 추정 {duplicate_count}건, 출처 종류 {source_type_count}종입니다."
                )

                with st.container(key="trend_primary_metrics"):
                    metric_cols = st.columns(6, gap="small")
                    _render_explainable_metric(
                        metric_cols[0], label="판정", value=status_label, help_text=status_help, align="left"
                    )
                    _render_explainable_metric(
                        metric_cols[1],
                        label="트렌드",
                        value=f"{trend_score:.1f}",
                        help_text=trend_help,
                        delta=(
                            f"반복 포착 +{rediscovery_score:.1f}"
                            if rediscovery_score > 0
                            else None
                        ),
                        align="left",
                    )
                    _render_explainable_metric(
                        metric_cols[2],
                        label="글감 기회",
                        value=f"{opportunity_score:.1f}",
                        help_text=opportunity_help,
                        align="left",
                    )
                    _render_explainable_metric(
                        metric_cols[3],
                        label="자료 완성도",
                        value=f"{quality_score:.1f}",
                        help_text=quality_help,
                        align="right",
                    )
                    _render_explainable_metric(
                        metric_cols[4],
                        label="사실 위험",
                        value=f"{fact_risk_score:.1f}",
                        help_text=risk_help,
                        align="right",
                    )
                    _render_explainable_metric(
                        metric_cols[5],
                        label="관련 항목",
                        value=f"{int(cluster['item_count'])}개",
                        help_text=item_help,
                        align="right",
                    )

                st.markdown("#### 글감 근거 진단")
                with st.container(key="trend_diagnostic_metrics"):
                    diagnostic_cols = st.columns(4, gap="small")
                    _render_explainable_metric(
                        diagnostic_cols[0],
                        label="중복 제외 근거",
                        value=f"{unique_evidence_count}건",
                        help_text=(
                            "정규화한 URL과 거의 같은 제목을 하나의 근거로 묶고 남은 실제 근거 수입니다. "
                            f"총 {int(cluster['item_count'])}개 항목에서 중복 추정 {duplicate_count}건을 제외했습니다."
                        ),
                        align="left",
                    )
                    _render_explainable_metric(
                        diagnostic_cols[1],
                        label="중복 추정",
                        value=f"{duplicate_count}건",
                        help_text=(
                            "같은 URL, 복제 수준으로 비슷한 제목 또는 같은 내용을 반복 수집한 것으로 추정한 항목 수입니다. "
                            "이 값은 트렌드 점수에서 중복 감점으로 반영됩니다."
                        ),
                        align="left",
                    )
                    _render_explainable_metric(
                        diagnostic_cols[2],
                        label="독립 발행처",
                        value=f"{publisher_count}곳",
                        help_text=(
                            "도메인과 출처명을 정규화해 서로 다른 발행 주체로 계산한 수입니다. "
                            f"현재 중복 제외 근거 {unique_evidence_count}건이 {publisher_count}곳에서 확인됐습니다."
                        ),
                        align="right",
                    )
                    _render_explainable_metric(
                        diagnostic_cols[3],
                        label="출처 종류",
                        value=f"{source_type_count}종",
                        help_text=(
                            "NAVER 뉴스·블로그, Daum 웹·카페, YouTube, Google Trends, 위키백과처럼 "
                            f"서로 다른 신호 유형의 수입니다. 현재 {source_type_count}종이 연결돼 있습니다."
                        ),
                        align="right",
                    )
                st.caption(str(diagnostics["binding_reason"]))
                repeated_terms = [
                    str(term) for term in diagnostics.get("repeated_terms") or [] if str(term)
                ]
                if repeated_terms:
                    st.caption("반복 확인된 대상: " + " · ".join(repeated_terms))
                oldest_at = diagnostics.get("oldest_at")
                latest_at = diagnostics.get("latest_at")
                if oldest_at or latest_at:
                    st.caption(
                        "근거 시간 범위: "
                        f"{oldest_at or '-'} ~ {latest_at or '-'}"
                    )
                diagnostic_warnings = [
                    str(warning) for warning in diagnostics.get("warnings") or [] if str(warning)
                ]
                if diagnostic_warnings:
                    st.warning(" · ".join(diagnostic_warnings), icon=":material/search_check:")

                st.markdown("#### 주제 방향")
                stored_angle_count = len(stored_ai_angles)
                stored_content_plan = (stored_ai_profile or {}).get("content_plan") or {}
                ai_analysis_complete = bool(
                    stored_ai_profile
                    and stored_content_plan
                    and stored_angle_count == 3
                )
                if stored_angle_count == 3:
                    if ai_analysis_complete:
                        st.success(
                            "Gemini 글감 분석 · 제목·요약·작성 설정·확인 항목·방향 3개 저장됨",
                            icon=":material/check_circle:",
                        )
                    else:
                        st.warning(
                            "Gemini 자동 방향 3/3 · 제목·요약·작성 설정은 다시 생성 필요",
                            icon=":material/pending:",
                        )
                    st.caption(
                        "Gemini가 공개 원문 신호를 검토해 서로 다른 작성 방향 3개를 저장했습니다. "
                        "표시 제목과 요약은 원본 군집 제목을 덮어쓰지 않고 별도로 보관합니다."
                    )
                    ai_option_ids = [str(item["angle_id"]) for item in stored_ai_angles]
                    ai_by_id = {
                        str(item["angle_id"]): item for item in stored_ai_angles
                    }

                    def _format_ai_angle(angle_id: str) -> str:
                        item = ai_by_id[angle_id]
                        score = item.get("direction_score")
                        score_text = f" · {float(score):.0f}점" if score is not None else ""
                        return (
                            f"{int(item.get('angle_order') or 0)}순위{score_text} · "
                            f"{item['angle_label']} · {item['angle_text']}"
                        )

                    selected_ai_angle_id = st.radio(
                        "이 주제를 어떤 방향으로 쓸까요?",
                        ai_option_ids,
                        format_func=_format_ai_angle,
                        key=f"selected_ai_angle_{selected_cluster_id}",
                    )
                    selected_ai_angle = ai_by_id[selected_ai_angle_id]
                    st.caption(f"방향 설명: {selected_ai_angle['rationale']}")
                    if str(selected_ai_angle.get("search_intent") or "").strip():
                        st.caption("검색 의도: " + str(selected_ai_angle["search_intent"]))
                    if str(selected_ai_angle.get("reader_question") or "").strip():
                        st.caption("독자 질문: " + str(selected_ai_angle["reader_question"]))
                    demand_evidence = [
                        str(item).strip()
                        for item in selected_ai_angle.get("demand_evidence") or []
                        if str(item).strip()
                    ]
                    if demand_evidence:
                        st.caption("수요 근거: " + " · ".join(demand_evidence))
                    score_reasons = [
                        str(item).strip()
                        for item in selected_ai_angle.get("score_reasons") or []
                        if str(item).strip()
                    ]
                    if selected_ai_angle.get("direction_score") is not None:
                        with st.expander("방향 점수 근거", expanded=False):
                            st.write(
                                f"총점 {float(selected_ai_angle['direction_score']):.0f}/100 · "
                                "검색 의도·수요 신호·근거 가용성·차별성·시의성의 합계"
                            )
                            for reason in score_reasons:
                                st.markdown(f"- {reason}")
                    search_queries = [
                        str(item)
                        for item in selected_ai_angle.get("search_queries") or []
                        if str(item).strip()
                    ]
                    if search_queries:
                        st.caption("나중에 AI가 검색할 핵심어: " + " · ".join(search_queries))
                    selected_angle_value = format_direction_for_request(selected_ai_angle)
                    verification_points = [
                        str(item).strip()
                        for item in (
                            stored_ai_profile.get("verification_points")
                            if stored_ai_profile
                            else []
                        )
                        if str(item).strip()
                    ]
                    if verification_points:
                        st.markdown("#### 발행 전 확인할 사실")
                        st.caption(
                            "아래 내용은 사실로 확정한 값이 아니라, 최신 공식 자료에서 확인해야 할 항목입니다."
                        )
                        for point in verification_points:
                            st.markdown(f"- {point}")
                    request_button_label = "선택한 방향으로 검색·사실 확인 요청서 만들기"
                else:
                    gemini_config = get_gemini_config()
                    min_ai_score = float(gemini_config.topic_angle_min_opportunity_score)
                    if opportunity_score < min_ai_score:
                        st.info(
                            f"Gemini 자동 분석 제외 · 글감 기회 {opportunity_score:.1f} · "
                            f"자동 분석 기준 {min_ai_score:g}점 미만",
                            icon=":material/filter_alt:",
                        )
                        st.caption(
                            "현재 글감 기회 점수에서는 API 사용량을 아끼기 위해 자동 분석 대상에서 제외됩니다. "
                            "자료가 추가되어 글감 기회 점수가 기준 이상이 되면 다음 실행에서 자동 대상에 포함됩니다. "
                            "아래 입력란은 사용자가 직접 적는 수동 방향입니다."
                        )
                    else:
                        status_text = (
                            "Gemini 자동 방향 · 미생성 0/3"
                            if stored_angle_count == 0
                            else f"Gemini 자동 방향 · 불완전 {stored_angle_count}/3"
                        )
                        st.warning(status_text, icon=":material/pending:")
                        st.caption(
                            "자동 생성된 분석 정보가 없습니다. ‘저장 자료 정리·순위 다시 계산’ 또는 "
                            "‘주제 방향 자동 생성’을 실행하면 글감들을 설정된 개수로 나눠 Gemini로 보완합니다. "
                            "아래 값은 저장된 자동 방향이 아니라 사용자가 직접 입력하는 수동 방향입니다."
                        )
                    selected_angle_value = st.text_area(
                        "수동 주제 방향 · 선택 사항",
                        value="",
                        placeholder="비워 두면 특정 방향을 강제하지 않고 AI가 검색 후 적절한 방향을 정합니다.",
                        key=f"manual_angle_{selected_cluster_id}",
                        height=90,
                    ).strip()
                    request_button_label = (
                        "수동 방향으로 검색·사실 확인 요청서 만들기"
                        if selected_angle_value
                        else "방향 없이 검색·사실 확인 요청서 만들기"
                    )

                if st.button(
                    request_button_label,
                    type="primary",
                    width="stretch",
                    key=f"make_ai_request_{selected_cluster_id}",
                ):
                    try:
                        topic_id = promote_trend_cluster(con, selected_cluster_id)
                        navigate_to_page(
                            "AI 요청서",
                            prefill_topic_id=topic_id,
                            prefill_angle=selected_angle_value,
                        )
                    except ValueError as exc:
                        st.error(str(exc))

                st.space("small")
                reason_col, quality_col = st.columns(2, gap="large")
                with reason_col:
                    st.markdown("#### 선정 근거")
                    reasons = cluster.get("score_reasons", [])
                    if reasons:
                        for reason in reasons:
                            st.markdown(f"- {reason}")
                    else:
                        st.caption("표시할 점수 근거가 없습니다.")
                with quality_col:
                    st.markdown("#### 자료 완성도 판단")
                    quality_reasons = cluster.get("quality_reasons", [])
                    if quality_reasons:
                        for reason in quality_reasons:
                            st.markdown(f"- {reason}")
                    else:
                        st.caption("표시할 품질 판단 근거가 없습니다.")

                st.markdown("#### 내 글감 평가")
                st.caption(
                    "평가는 현재 순위를 자동으로 바꾸지 않고 기록만 남깁니다. "
                    "쓸모없음·잘못 묶임 평가는 기본 목록에서 숨길 수 있습니다."
                )
                current_feedback = get_trend_feedback(con, selected_cluster_id)
                feedback_label_to_type = {
                    label: feedback_type for feedback_type, label in FEEDBACK_LABELS.items()
                }
                feedback_options = ["평가 없음", *FEEDBACK_LABELS.values()]
                current_feedback_label = FEEDBACK_LABELS.get(
                    str((current_feedback or {}).get("feedback_type") or ""),
                    "평가 없음",
                )
                with st.form(
                    f"trend_feedback_form_{selected_cluster_id}",
                    clear_on_submit=False,
                    border=True,
                ):
                    selected_feedback_label = st.radio(
                        "이 후보를 어떻게 평가하나요?",
                        feedback_options,
                        index=feedback_options.index(current_feedback_label),
                        horizontal=True,
                    )
                    feedback_note = st.text_input(
                        "판단 메모",
                        value=str((current_feedback or {}).get("note") or ""),
                        placeholder="예: 제목은 괜찮지만 서로 다른 사건이 섞여 있음",
                    )
                    feedback_submitted = st.form_submit_button(
                        "평가 저장",
                        type="primary",
                        width="stretch",
                    )
                if feedback_submitted:
                    if selected_feedback_label == "평가 없음":
                        clear_trend_feedback(con, selected_cluster_id)
                    else:
                        save_trend_feedback(
                            con,
                            cluster_id=selected_cluster_id,
                            canonical_title=str(cluster["canonical_title"]),
                            feedback_type=feedback_label_to_type[selected_feedback_label],
                            note=feedback_note,
                            diagnostics=diagnostics,
                        )
                    st.toast("글감 평가를 저장했습니다.")
                    st.rerun()

                source_labels = [SOURCE_LABELS.get(item, item) for item in cluster.get("source_types", [])]
                st.caption("포함 출처: " + (", ".join(source_labels) or "없음"))
                if status_label == "보류":
                    st.warning("단일 신호·자극적 제목 등으로 자동 보류된 후보입니다. 원문을 확인한 뒤 사용하세요.")
                if cluster["fact_risk_score"] > 0:
                    st.warning("민감 분야·시점 의존·수치 주장 등 발행 전 사실 확인이 필요한 요소가 포함될 수 있습니다.")

                st.space("small")
                st.markdown("#### 관련 원문과 관심 신호")
                if not items:
                    st.caption("연결된 원문이 없습니다.")

                def render_trend_evidence(item: dict) -> None:
                    metadata = item.get("metadata") or {}
                    title = str(metadata.get("item_title") or item.get("raw_title") or "제목 없음")
                    source_type = str(item.get("source_type") or "")
                    label = SOURCE_LABELS.get(source_type, source_type or "출처")
                    details: list[str] = []
                    if source_type == "google_trends" and metadata.get("approx_traffic"):
                        details.append(f"검색량 {metadata['approx_traffic']}")
                    elif source_type == "wikipedia_pageviews":
                        if metadata.get("rank"):
                            details.append(f"조회 순위 {int(metadata['rank'])}위")
                        if metadata.get("views") is not None:
                            details.append(f"조회수 {int(metadata['views']):,}회")
                    observation_count = int(item.get("observation_count") or 1)
                    if observation_count > 1:
                        details.append(f"수집 포착 {observation_count}회")
                        previous_imported_at = item.get("previous_imported_at")
                        last_imported_at = item.get("last_imported_at")
                        if previous_imported_at and last_imported_at:
                            gap_hours = max(
                                0.0,
                                (last_imported_at - previous_imported_at).total_seconds() / 3600,
                            )
                            details.append(f"최근 재포착 간격 {gap_hours:.1f}시간")
                    with st.container(border=True):
                        left, right = st.columns([7.5, 1.5], vertical_alignment="center")
                        left.markdown(f"**[{label}] {title}**")
                        detail_suffix = " · " + " · ".join(details) if details else ""
                        left.caption(
                            f"{item.get('source_name') or '-'} · 확인 "
                            f"{item.get('published_at') or item.get('observed_at') or '-'}{detail_suffix}"
                        )
                        if item.get("source_url"):
                            link_label = {
                                "youtube": "영상 보기",
                                "naver_news": "뉴스 보기",
                                "naver_blog": "블로그 보기",
                                "daum_web": "웹문서 보기",
                                "daum_cafe": "카페 글 보기",
                                "google_trends": "트렌드 보기",
                                "wikipedia_pageviews": "문서 보기",
                            }.get(source_type, "원문 보기")
                            right.link_button(
                                link_label,
                                str(item["source_url"]),
                                width="stretch",
                            )

                for item in items[:8]:
                    render_trend_evidence(item)
                if len(items) > 8:
                    with st.expander(
                        f"추가 원문 {min(len(items), 30) - 8}개 보기",
                        icon=":material/article:",
                    ):
                        for item in items[8:30]:
                            render_trend_evidence(item)
                if len(items) > 30:
                    st.caption(f"관련 항목 {len(items)}개 중 최근 30개를 표시했습니다.")

def render_topics() -> None:
    pending_import = st.session_state.pop("youtube_import_pending", None)
    if pending_import:
        import_type = str(pending_import.get("type") or "")
        import_path = str(pending_import.get("path") or "")
        import_limit = int(pending_import.get("limit") or 100)
        try:
            adapter = (
                YouTubeParquetAdapter(import_path)
                if import_type == "youtube_parquet"
                else YouTubeDuckDBAdapter(import_path)
            )
            signals = adapter.load_signals(limit=import_limit)
            with db_connection() as write_con:
                result = import_preloaded_source_signals(
                    write_con,
                    list(signals),
                    sync_source_type=import_type,
                    create_topics=True,
                )
            if result["items_read"] == 0:
                message = (
                    "읽을 수 있는 새 YouTube 신호가 없습니다."
                    if import_type == "youtube_parquet"
                    else "가져올 YouTube 신호가 없습니다."
                )
                st.session_state["youtube_import_flash"] = ("info", message)
            else:
                prefix = (
                    "가져오기 완료"
                    if import_type == "youtube_parquet"
                    else "수동 가져오기 완료"
                )
                st.session_state["youtube_import_flash"] = (
                    "success",
                    f"{prefix}: 읽기 {result['items_read']}개 · "
                    f"신규 {result['items_added']}개 · 갱신 {result['items_updated']}개",
                )
        except YouTubeParquetError as exc:
            st.session_state["youtube_import_flash"] = ("error", str(exc))
        except Exception as exc:
            st.session_state["youtube_import_flash"] = (
                "error",
                f"YouTube 신호 가져오기에 실패했습니다: {exc}",
            )
        st.rerun()

    with db_connection() as con:
        topics = list_topics(con)
        total = len(topics)
        interested = int(topics["is_interested"].fillna(False).sum()) if not topics.empty else 0
        ready = int(topics["status"].isin(["ai_ready", "draft_complete", "editing", "publish_ready"]).sum()) if not topics.empty else 0
        published = int((topics["status"] == "published").sum()) if not topics.empty else 0
        cols = st.columns(4)
        cols[0].metric("전체 주제", total)
        cols[1].metric("관심 주제", interested)
        cols[2].metric("제작 진행", ready)
        cols[3].metric("발행 완료", published)

        tab_manual, tab_youtube, tab_manage = st.tabs(["주제 직접 등록", "YouTube 신호 가져오기", "주제 목록·관리"])

        with tab_manual:
            st.write("YouTube에 없는 주제도 메인 프로그램에서 직접 등록할 수 있습니다.")
            with st.form("manual_topic_form", clear_on_submit=True):
                title = st.text_input("주제명 *")
                summary = st.text_area("주제 설명", height=90)
                category = st.text_input("카테고리")
                memo = st.text_area("메모", height=80)
                priority_label = st.selectbox("우선순위", ["낮음", "보통", "높음"], index=1)
                submitted = st.form_submit_button("관심 주제로 저장", type="primary")
                if submitted:
                    priority = {"낮음": 1, "보통": 2, "높음": 3}[priority_label]
                    try:
                        _, created = add_manual_topic(
                            con,
                            title=title,
                            summary=summary,
                            category=category,
                            memo=memo,
                            priority=priority,
                        )
                        st.success("새 주제를 저장했습니다." if created else "기존 주제를 관심 주제로 갱신했습니다.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

        with tab_youtube:
            youtube_import_flash = st.session_state.pop("youtube_import_flash", None)
            if youtube_import_flash:
                level, message = youtube_import_flash
                getattr(st, str(level), st.info)(str(message))
            parquet_path = get_setting(con, "youtube_parquet_path")
            st.caption("기본 신호 출처 · Parquet 교환 파일 (권장)")
            st.code(parquet_path, language=None)
            st.info("YouTube 수집이 끝난 뒤 생성된 교환 파일만 읽습니다.")
            limit = st.slider("가져올 최대 신호 수", 10, 300, 100, 10)
            col1, col2 = st.columns(2)
            if col1.button("교환 파일 상태 확인"):
                try:
                    info = YouTubeParquetAdapter(parquet_path).inspect()
                    st.success(
                        f"읽기 가능: 스키마 {info['schema_version']} · 신호 {info['row_count']}개"
                    )
                except YouTubeParquetError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"YouTube 신호 가져오기에 실패했습니다: {exc}")
            if col2.button("YouTube 신호 가져오기", type="primary"):
                st.session_state["youtube_import_pending"] = {
                    "type": "youtube_parquet",
                    "path": parquet_path,
                    "limit": limit,
                }
                st.rerun()


            with st.expander("수동 호환 옵션 · YouTube DuckDB 직접 읽기"):
                youtube_path = get_setting(con, "youtube_db_path")
                st.code(youtube_path, language=None)
                st.warning("교환 파일을 사용할 수 없을 때만 읽기 전용 fallback으로 사용하세요.")
                fallback_col1, fallback_col2 = st.columns(2)
                if fallback_col1.button("DuckDB 연결 확인"):
                    try:
                        info = YouTubeDuckDBAdapter(youtube_path).inspect()
                        st.success(f"읽기 전용 연결 성공: 테이블 {info['table_count']}개")
                    except Exception as exc:
                        st.error(str(exc))
                if fallback_col2.button("DuckDB에서 수동 가져오기"):
                    st.session_state["youtube_import_pending"] = {
                        "type": "youtube_duckdb",
                        "path": youtube_path,
                        "limit": limit,
                    }
                    st.rerun()


        with tab_manage:
            if topics.empty:
                st.info("아직 저장된 주제가 없습니다.")
                return
            display = topics.copy()
            display["상태"] = display["status"].map(TOPIC_STATUS_LABELS).fillna(display["status"])
            display["관심"] = display["is_interested"].map({True: "예", False: "아니오"})
            display["우선순위"] = display["priority"].map(PRIORITY_LABELS)
            st.dataframe(
                display[["주제", "카테고리", "상태", "우선순위", "관심", "신호수", "출처", "최고신호", "최근확인"]],
                width="stretch",
                hide_index=True,
            )
            options = topic_options(con)
            selected_id = st.selectbox("수정할 주제", options=list(options), format_func=options.get)
            topic = get_topic(con, selected_id)
            if topic:
                with st.form("topic_edit_form"):
                    title = st.text_input("주제명", value=topic["title"])
                    summary = st.text_area("설명", value=topic.get("summary") or "", height=90)
                    category = st.text_input("카테고리", value=topic.get("category") or "")
                    status_index = TOPIC_STATUS_OPTIONS.index(topic["status"]) if topic["status"] in TOPIC_STATUS_OPTIONS else 0
                    status = st.selectbox(
                        "제작 상태",
                        TOPIC_STATUS_OPTIONS,
                        index=status_index,
                        format_func=TOPIC_STATUS_LABELS.get,
                    )
                    priority = st.select_slider("우선순위", options=[1, 2, 3], value=int(topic["priority"]), format_func=PRIORITY_LABELS.get)
                    interested = st.checkbox("관심 주제로 관리", value=bool(topic["is_interested"]))
                    memo = st.text_area("메모", value=topic.get("memo") or "", height=80)
                    saved = st.form_submit_button("변경 저장", type="primary")
                    if saved:
                        update_topic(
                            con,
                            selected_id,
                            title=title,
                            summary=summary,
                            category=category,
                            status=status,
                            priority=priority,
                            is_interested=interested,
                            memo=memo,
                        )
                        st.success("주제 정보를 저장했습니다.")
                        st.rerun()
                sources = get_topic_sources(con, selected_id)
                st.subheader("연결된 트렌드 신호")
                render_source_groups(sources)
                render_topic_reference_manager(con, selected_id)
                with st.expander("주제 보관 처리"):
                    st.warning("보관 처리하면 일반 목록에서 숨겨집니다. 실제 데이터는 즉시 삭제하지 않습니다.")
                    if st.button("이 주제 보관", type="secondary"):
                        archive_topic(con, selected_id)
                        st.success("보관 처리했습니다.")
                        st.rerun()


def render_content_pack() -> None:
    render_content_workflow_progress("AI 요청서")
    st.write(
        "글감과 방향을 정하면 ChatGPT·Gemini가 웹 검색과 사실 확인까지 수행하도록 요청서를 만듭니다. "
        "공식 URL을 사용자가 직접 준비할 필요는 없습니다."
    )
    with db_connection() as con:
        recent_packs = list_content_packs(con)
        options = topic_options(con, interested_only=True)
        start_modes = ["새 글감 바로 입력"]
        if options:
            start_modes.append("저장된 주제 사용")
        prefill_topic_id = str(st.session_state.get("prefill_topic_id") or "")
        default_mode_index = (
            start_modes.index("저장된 주제 사용")
            if prefill_topic_id in options and "저장된 주제 사용" in start_modes
            else 0
        )
        start_mode = st.radio(
            "시작 방법",
            start_modes,
            index=default_mode_index,
            horizontal=True,
            help="오늘의 트렌드에서 선택하거나 주제 관리 화면을 거치지 않고 새 글감을 바로 시작할 수 있습니다.",
        )

        selected_id = None
        topic = None
        sources: list[dict] = []
        selected_source_ids: list[str] = []
        selected_reference_ids: list[str] = []
        factual_references: list[dict] = []
        selected_factual_references: list[dict] = []
        quick_title = ""
        quick_summary = ""
        quick_memo = ""

        if start_mode == "새 글감 바로 입력":
            st.info(
                "글감과 작성 방향만 입력하면 됩니다. 최신 정보와 공식 자료 조사는 AI 요청서에 자동으로 포함됩니다."
            )
            quick_title = st.text_input(
                "글감 또는 주제 *",
                placeholder="예: 정속형 에어컨 전기요금 줄이는 방법",
            )
            quick_summary = st.text_area(
                "글에서 다루고 싶은 내용",
                placeholder="핵심 질문, 독자가 궁금해할 내용, 포함할 범위를 자유롭게 적으세요.",
                height=100,
            )
            quick_memo = st.text_area(
                "개인 메모",
                placeholder="나중에 확인할 내용이나 작성 아이디어가 있으면 적으세요.",
                height=80,
            )
        else:
            option_ids = list(options)
            selected_index = option_ids.index(prefill_topic_id) if prefill_topic_id in option_ids else 0
            selected_id = st.selectbox(
                "저장된 주제",
                options=option_ids,
                index=selected_index,
                format_func=options.get,
            )
            topic = get_topic(con, selected_id)
            sources = get_topic_sources(con, selected_id)
            st.caption(f"연결된 트렌드 신호: {len(sources)}개")
            render_source_groups(sources)

            source_map = {
                str(source["source_item_id"]): source
                for source in sources
                if source.get("source_item_id")
            }
            source_ids = list(source_map)
            selected_source_ids = st.multiselect(
                "AI 자료팩에 포함할 트렌드 근거",
                options=source_ids,
                default=source_ids,
                format_func=lambda source_id: _source_option_label(source_map[source_id]),
                key=f"selected_evidence_{selected_id}",
                help="글감 판단에 도움이 되는 신호만 선택하세요. YouTube 영상은 사실 근거가 아니라 관심도 신호로 전달됩니다.",
            )
            selected_sources = [
                source_map[source_id]
                for source_id in selected_source_ids
                if source_id in source_map
            ]

            factual_references = list_topic_references(con, selected_id)
            reference_map = {
                str(reference["reference_id"]): reference
                for reference in factual_references
                if reference.get("reference_id")
            }
            reference_ids = list(reference_map)
            selected_reference_ids = st.multiselect(
                "추가로 포함할 참고 자료 (선택)",
                options=reference_ids,
                default=reference_ids,
                format_func=lambda reference_id: _reference_option_label(reference_map[reference_id]),
                key=f"selected_factual_references_{selected_id}",
                help="이미 보유한 참고 자료가 있을 때만 선택하세요. 없어도 AI가 웹 검색하도록 요청서가 생성됩니다.",
            )
            selected_factual_references = [
                reference_map[reference_id]
                for reference_id in selected_reference_ids
                if reference_id in reference_map
            ]
            if factual_references:
                st.caption(
                    f"등록된 사실 참고 자료 {len(factual_references)}개 중 {len(selected_reference_ids)}개를 선택했습니다."
                )
            else:
                st.info(
                    "추가 참고 자료가 없어도 됩니다. AI가 웹 검색으로 최신 공식 자료를 확인하도록 요청서에 자동 지시합니다."
                )

            st.markdown("**선택한 트렌드 근거 자동 요약**")
            for summary_line in build_trend_evidence_summary(selected_sources):
                st.markdown(f"- {summary_line}")
            if sources and not selected_source_ids:
                st.warning(
                    "현재 선택한 외부 근거가 없습니다. 자료팩은 만들 수 있지만 별도 조사가 더 필요합니다."
                )

        readiness_topic = topic or {
            "title": quick_title,
            "summary": quick_summary,
            "memo": quick_memo,
        }
        readiness = assess_content_pack_readiness(
            readiness_topic,
            selected_factual_references,
        )
        if readiness["is_freshness_sensitive"]:
            matched_text = ", ".join(readiness["matched_terms"])
            st.info(
                "현재값 확인이 필요한 주제입니다"
                + (f" ({matched_text})" if matched_text else "")
                + ". 생성되는 요청서와 Gemini API 호출에 웹 검색·공식 출처·기준일 확인 규칙을 자동으로 포함합니다."
            )

        default_audience = get_setting(con, "default_audience")
        default_purpose = get_setting(con, "default_purpose")
        if selected_id:
            content_defaults = get_topic_content_defaults(
                con,
                topic_id=selected_id,
                default_audience=default_audience,
                default_purpose=default_purpose,
            )
        else:
            content_defaults = {
                "source": "global",
                "audience": default_audience,
                "purpose": default_purpose,
                "angle": (
                    "독자가 궁금해하는 핵심을 먼저 설명하고, "
                    "확인할 사실과 실용 정보를 구분해 정리"
                ),
                "category": "",
                "target_length": 2500,
                "title_rules": list(DEFAULT_TITLE_RULES),
                "outline": list(DEFAULT_OUTLINE),
                "forbidden_expressions": list(DEFAULT_FORBIDDEN),
                "fact_check_items": list(DEFAULT_FACT_CHECK_ITEMS),
            }

        prefill_angle = st.session_state.get("prefill_angle")
        if prefill_angle is not None:
            content_defaults["angle"] = str(prefill_angle)
        if content_defaults.get("source") == "ai":
            st.info(
                "초기 Gemini 글감 분석에서 추천한 독자·목적·카테고리·분량·구성을 자동으로 채웠습니다. "
                "필요한 부분을 고친 뒤 요청서를 만들면 이 주제의 사용자 설정으로 저장됩니다."
            )
        elif content_defaults.get("source") == "saved":
            st.caption("이 주제에 마지막으로 저장한 AI 요청서 설정을 불러왔습니다.")

        form_scope = f"{selected_id or 'quick'}_{content_defaults.get('source') or 'global'}"
        prefill_angle_text = str(prefill_angle or "").strip()
        angle_scope = (
            hashlib.sha1(prefill_angle_text.encode("utf-8")).hexdigest()[:10]
            if prefill_angle_text
            else "default"
        )
        angle_key = f"content_pack_angle_{form_scope}_{angle_scope}"
        with st.form(f"content_pack_form_{form_scope}"):
            left, right = st.columns(2)
            audience = left.text_input(
                "독자 대상",
                value=str(content_defaults.get("audience") or ""),
                key=f"content_pack_audience_{form_scope}",
            )
            purpose = right.text_input(
                "글 목적",
                value=str(content_defaults.get("purpose") or ""),
                key=f"content_pack_purpose_{form_scope}",
            )
            angle = st.text_input(
                "글의 관점",
                value=str(content_defaults.get("angle") or ""),
                placeholder="비워 두면 AI가 웹 검색 후 글의 방향을 정합니다.",
                key=angle_key,
            )
            category = st.text_input(
                "카테고리",
                value=str(content_defaults.get("category") or ""),
                key=f"content_pack_category_{form_scope}",
            )
            target_length = st.number_input(
                "목표 본문 분량",
                min_value=500,
                max_value=10000,
                value=int(content_defaults.get("target_length") or 2500),
                step=100,
                key=f"content_pack_target_length_{form_scope}",
            )
            col1, col2 = st.columns(2)
            title_rules = col1.text_area(
                "제목 규칙 · 한 줄에 하나",
                value="\n".join(content_defaults.get("title_rules") or DEFAULT_TITLE_RULES),
                height=150,
                key=f"content_pack_title_rules_{form_scope}",
            )
            outline = col2.text_area(
                "본문 구성 · 한 줄에 하나",
                value="\n".join(content_defaults.get("outline") or DEFAULT_OUTLINE),
                height=150,
                key=f"content_pack_outline_{form_scope}",
            )
            forbidden = col1.text_area(
                "금지 표현 · 한 줄에 하나",
                value="\n".join(
                    content_defaults.get("forbidden_expressions") or DEFAULT_FORBIDDEN
                ),
                height=130,
                key=f"content_pack_forbidden_{form_scope}",
            )
            fact_checks = col2.text_area(
                "반드시 확인할 사실 · 한 줄에 하나",
                value="\n".join(content_defaults.get("fact_check_items") or []),
                height=130,
                key=f"content_pack_fact_checks_{form_scope}",
            )
            submitted = st.form_submit_button(
                "검색·사실 확인용 AI 요청서 만들기", type="primary"
            )
            if submitted:
                try:
                    if start_mode == "새 글감 바로 입력":
                        pack = save_quick_content_pack(
                            con,
                            topic_title=quick_title,
                            topic_summary=quick_summary,
                            topic_category=category,
                            topic_memo=quick_memo,
                            audience=audience,
                            purpose=purpose,
                            angle=angle,
                            category=category,
                            target_length=int(target_length),
                            title_rules=title_rules,
                            outline=outline,
                            forbidden_expressions=forbidden,
                            fact_check_items=fact_checks,
                        )
                        created_text = (
                            "새 작업 주제를 자동 저장하고 "
                            if pack["topic_created"]
                            else "같은 이름의 기존 작업 주제를 재사용해 "
                        )
                        st.success(
                            f"{created_text}웹 검색·사실 확인용 요청서를 만들었습니다. "
                            "아래 요청서를 복사해 ChatGPT 또는 사용자가 선택한 AI에서 초안을 만들 수 있습니다."
                        )
                    else:
                        pack = save_content_pack(
                            con,
                            topic_id=selected_id,
                            audience=audience,
                            purpose=purpose,
                            angle=angle,
                            category=category,
                            target_length=int(target_length),
                            title_rules=title_rules,
                            outline=outline,
                            forbidden_expressions=forbidden,
                            fact_check_items=fact_checks,
                            selected_source_item_ids=selected_source_ids,
                            selected_reference_ids=selected_reference_ids,
                        )
                        st.success(
                            f"웹 검색·사실 확인용 요청서를 저장했습니다. "
                            f"트렌드 신호 {pack['trend_reference_count']}개 · "
                            f"추가 참고 자료 {pack['factual_reference_count']}개가 포함됐습니다."
                        )
                        st.session_state.pop("prefill_topic_id", None)
                        st.session_state.pop("prefill_angle", None)
                    st.session_state["last_pack"] = pack
                    st.session_state["prefill_content_pack_id"] = pack["content_pack_id"]
                except ValueError as exc:
                    st.error(str(exc))

        if recent_packs:
            with st.expander("최근에 만든 자료팩 다시 열기"):
                recent_pack_map = {
                    item["content_pack_id"]: (
                        f"{item['topic_title']} · 자료팩 v{item['version']} · {item['created_at']}"
                    )
                    for item in recent_packs
                }
                recent_ids = list(recent_pack_map)
                remembered_pack_id = str(
                    st.session_state.get("prefill_content_pack_id") or ""
                )
                recent_index = (
                    recent_ids.index(remembered_pack_id)
                    if remembered_pack_id in recent_ids
                    else 0
                )
                selected_recent_pack_id = st.selectbox(
                    "다시 열 자료팩",
                    recent_ids,
                    index=recent_index,
                    format_func=recent_pack_map.get,
                )
                if st.button("선택한 자료팩 열기", width="stretch"):
                    reopened_pack = get_content_pack(con, selected_recent_pack_id)
                    if reopened_pack is None:
                        st.error("선택한 자료팩을 찾을 수 없습니다.")
                    else:
                        st.session_state["last_pack"] = reopened_pack
                        st.session_state["prefill_content_pack_id"] = selected_recent_pack_id
                        st.rerun()

        pack = st.session_state.get("last_pack")
        if pack:
            tab1, tab2 = st.tabs(
                ["검색·사실 확인 요청서", "자료팩 상세"]
            )
            with tab1:
                st.text_area(
                    "ChatGPT 또는 Gemini에 그대로 붙여넣기",
                    value=pack["prompt_text"],
                    height=520,
                )
                action_cols = st.columns([1, 1])
                with action_cols[0]:
                    render_chatgpt_request_button(
                        pack["prompt_text"],
                        key=f"chatgpt_request_{pack['content_pack_id']}",
                    )
                if action_cols[1].button(
                    "ChatGPT 결과 붙여넣기로 이동",
                    type="primary",
                    width="stretch",
                ):
                    navigate_to_page(
                        "AI 결과 가져오기",
                        prefill_content_pack_id=pack["content_pack_id"],
                    )
                st.caption(
                    "ChatGPT에서 요청하기는 API를 호출하지 않고 요청서를 복사해 새 탭만 엽니다. "
                    "입력·전송과 답변 JSON 복사는 사용자가 직접 진행하세요."
                )
            with tab2:
                st.markdown(pack["pack_markdown"])


def render_ai_import() -> None:
    render_content_workflow_progress("AI 결과 가져오기")
    with db_connection() as con:
        packs = list_content_packs(con)
        if not packs:
            st.info("먼저 AI 자료팩을 생성하세요.")
            return
        pack_map = {
            item["content_pack_id"]: f"{item['topic_title']} · 자료팩 v{item['version']} · {item['created_at']}"
            for item in packs
        }
        pack_ids = list(pack_map)
        prefill_pack_id = str(
            st.session_state.get("prefill_content_pack_id") or ""
        )
        selected_pack_index = (
            pack_ids.index(prefill_pack_id) if prefill_pack_id in pack_ids else 0
        )
        selected_pack_id = st.selectbox(
            "사용한 자료팩",
            pack_ids,
            index=selected_pack_index,
            format_func=pack_map.get,
        )
        provider = st.selectbox(
            "결과를 생성한 AI",
            ["ChatGPT", "Gemini", "기타"],
            key="ai_import_provider",
            help=(
                "붙여넣은 결과의 생성 출처를 기록하는 항목입니다. "
                "이 선택으로 API 호출 모델이 바뀌지는 않습니다."
            ),
        )
        raw = st.text_area(
            "AI가 출력한 JSON 전체를 붙여넣으세요",
            height=430,
            key=f"ai_import_raw_{selected_pack_id}",
        )
        current_fingerprint = build_ai_result_validation_fingerprint(
            content_pack_id=selected_pack_id,
            ai_provider=provider,
            raw_response=raw,
        )
        if st.button("형식·출처 검사", type="primary"):
            pack = get_content_pack(con, selected_pack_id)
            result = parse_ai_result(raw)
            references = []
            if pack is not None:
                try:
                    parsed_references = json.loads(pack.get("references_json") or "[]")
                    if isinstance(parsed_references, list):
                        references = parsed_references
                except json.JSONDecodeError:
                    result.errors.append("선택한 자료팩의 출처 목록을 읽을 수 없습니다.")
            result = validate_ai_result_against_references(result, references)
            st.session_state["parse_result"] = result
            st.session_state["parse_raw"] = raw
            st.session_state["parse_pack_id"] = selected_pack_id
            st.session_state["parse_provider"] = provider
            st.session_state["parse_fingerprint"] = current_fingerprint
            st.session_state.pop("last_saved_fingerprint", None)

        result = st.session_state.get("parse_result")
        if result:
            checked_pack_id = st.session_state.get("parse_pack_id")
            checked_fingerprint = st.session_state.get("parse_fingerprint")
            validation_is_current = (
                checked_pack_id == selected_pack_id
                and checked_fingerprint == current_fingerprint
            )
            if checked_pack_id != selected_pack_id:
                st.info("현재 선택한 자료팩이 검사 당시 자료팩과 다릅니다. 다시 검사하세요.")
            elif not validation_is_current:
                st.warning(
                    "검사 후 AI 서비스나 붙여넣은 JSON이 변경됐습니다. "
                    "현재 내용으로 다시 검사해야 저장할 수 있습니다."
                )
            if result.errors:
                st.error("저장할 수 없는 오류\n" + "\n".join(f"- {item}" for item in result.errors))
            else:
                st.success("필수 형식과 자료팩 출처 검사를 통과했습니다.")
            if result.warnings:
                st.warning("발행 전 확인할 경고\n" + "\n".join(f"- {item}" for item in result.warnings))
            if result.data:
                st.json(result.data)

            saved_fingerprint = st.session_state.get("last_saved_fingerprint")
            saved_draft_id = str(st.session_state.get("last_saved_draft_id") or "")
            already_saved = (
                validation_is_current
                and saved_fingerprint == current_fingerprint
                and bool(saved_draft_id)
            )
            can_save = result.is_valid and validation_is_current
            if already_saved:
                st.success("현재 검사 결과는 이미 새 초안으로 저장했습니다.")
            elif can_save and st.button("검사 결과를 새 초안으로 저장", type="primary"):
                try:
                    _, draft_id = save_generation_and_draft(
                        con,
                        content_pack_id=st.session_state["parse_pack_id"],
                        ai_provider=st.session_state["parse_provider"],
                        raw_response=st.session_state["parse_raw"],
                        result=result,
                    )
                    st.session_state["last_saved_draft_id"] = draft_id
                    st.session_state["prefill_draft_id"] = draft_id
                    st.session_state["last_saved_fingerprint"] = current_fingerprint
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

            if already_saved:
                if st.button("저장한 초안 편집으로 이동", type="primary", width="stretch"):
                    navigate_to_page(
                        "글 편집",
                        prefill_draft_id=saved_draft_id,
                    )


def render_editor() -> None:
    render_content_workflow_progress("글 편집")
    with db_connection() as con:
        drafts = list_drafts(con)
        if not drafts:
            st.info("AI 결과 가져오기에서 정상 결과를 초안으로 저장하세요.")
            return
        draft_map = {item["draft_id"]: f"{item['title']} · v{item['current_revision']}" for item in drafts}
        draft_ids = list(draft_map)
        prefill_draft_id = str(st.session_state.get("prefill_draft_id") or "")
        draft_index = draft_ids.index(prefill_draft_id) if prefill_draft_id in draft_ids else 0
        draft_id = st.selectbox(
            "편집할 초안",
            draft_ids,
            index=draft_index,
            format_func=draft_map.get,
        )
        draft = get_draft(con, draft_id)
        if draft is None:
            return
        left, right = st.columns([1.05, 0.95])
        with left:
            with st.form("draft_edit_form"):
                title = st.text_input("제목", value=draft["title"])
                summary = st.text_area("요약", value=draft.get("summary") or "", height=90)
                category = st.text_input("카테고리", value=draft.get("category") or "")
                tags_text = st.text_input("태그 · 쉼표로 구분", value=", ".join(draft.get("tags", [])))
                body = st.text_area("Markdown 본문", value=draft["body_markdown"], height=620)
                create_revision = st.checkbox("새 수정 버전으로 저장", value=True)
                change_note = st.text_input("수정 메모", value="사용자 편집")
                save = st.form_submit_button("글 저장", type="primary")
                if save:
                    tags = [tag.strip().lstrip("#") for tag in tags_text.split(",") if tag.strip()]
                    try:
                        revision = update_draft(
                            con,
                            draft_id=draft_id,
                            title=title,
                            summary=summary,
                            category=category,
                            tags=tags,
                            body_markdown=body,
                            create_revision=create_revision,
                            change_note=change_note,
                        )
                        st.success(f"저장했습니다. 현재 버전: {revision}")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
        with right:
            preview_tab, html_tab, checks_tab = st.tabs(["Markdown 미리보기", "HTML 미리보기", "사실 확인"])
            with preview_tab:
                st.markdown(draft["body_markdown"])
            with html_tab:
                components.html(draft.get("body_html") or "", height=700, scrolling=True)
            with checks_tab:
                checks = get_fact_checks(con, draft_id)
                summary = get_fact_check_summary(con, draft_id)
                metric_cols = st.columns(4)
                metric_cols[0].metric("전체", summary["total"])
                metric_cols[1].metric("미확인", summary["needs_verification"])
                metric_cols[2].metric("수정 필요", summary["needs_revision"])
                metric_cols[3].metric("확인 완료", summary["verified"])
                if checks:
                    for check in checks:
                        status_label = FACT_CHECK_STATUS_LABELS.get(
                            str(check.get("check_status") or ""),
                            str(check.get("check_status") or "미확인"),
                        )
                        claim = str(check.get("claim_text") or "확인할 주장")
                        with st.expander(f"[{status_label}] {claim}", expanded=status_label != "확인 완료"):
                            reason = str(check.get("reason") or "").strip()
                            if reason:
                                st.caption(f"AI가 남긴 확인 이유: {reason}")
                            source_ids = check.get("source_ids") or []
                            if source_ids:
                                st.caption("자료팩 출처 ID: " + ", ".join(source_ids))
                            with st.form(f"fact_check_form_{check['fact_check_id']}"):
                                current_status = str(check.get("check_status") or "needs_verification")
                                if current_status not in FACT_CHECK_STATUS_OPTIONS:
                                    current_status = "needs_verification"
                                status = st.selectbox(
                                    "확인 상태",
                                    FACT_CHECK_STATUS_OPTIONS,
                                    index=FACT_CHECK_STATUS_OPTIONS.index(current_status),
                                    format_func=lambda value: FACT_CHECK_STATUS_LABELS[value],
                                )
                                evidence = st.text_area(
                                    "확인 메모",
                                    value=str(check.get("evidence") or ""),
                                    placeholder="확인한 내용, 수정해야 할 부분, 판단 근거를 기록하세요.",
                                    height=100,
                                )
                                source_url = st.text_input(
                                    "확인 근거 URL",
                                    value=str(check.get("source_url") or ""),
                                    placeholder="https://...",
                                )
                                submitted = st.form_submit_button("사실 확인 저장")
                                if submitted:
                                    try:
                                        update_fact_check(
                                            con,
                                            fact_check_id=check["fact_check_id"],
                                            check_status=status,
                                            evidence=evidence,
                                            source_url=source_url,
                                        )
                                        st.success("사실 확인 상태를 저장했습니다.")
                                        st.rerun()
                                    except ValueError as exc:
                                        st.error(str(exc))
                else:
                    st.info(
                        "AI가 등록한 사실 확인 항목이 없습니다. 숫자·가격·정책·법률 내용은 발행 전에 직접 확인하세요."
                    )
        st.subheader("복사")
        copy_cols = st.columns(4)
        with copy_cols[0]:
            render_copy_button("제목 복사", draft["title"], key="copy_title")
        with copy_cols[1]:
            render_copy_button("본문 복사", draft["body_markdown"], key="copy_body")
        with copy_cols[2]:
            render_copy_button("태그 복사", " ".join(f"#{tag}" for tag in draft.get("tags", [])), key="copy_tags")
        with copy_cols[3]:
            render_copy_button("전체 글 복사", build_full_copy_text(draft), key="copy_full")

        final_fact_summary = get_fact_check_summary(con, draft_id)
        if final_fact_summary["unresolved"] > 0:
            st.warning(
                f"사실 확인이 끝나지 않은 항목이 {final_fact_summary['unresolved']}개 남아 있습니다. "
                "발행 보조로 이동할 수는 있지만 최종 게시 전 직접 확인하세요."
            )
        elif final_fact_summary["total"] > 0:
            st.success("등록된 사실 확인 항목을 모두 확인했습니다.")
        if st.button("발행 보조로 이동", type="primary", width="stretch"):
            navigate_to_page("발행 보조", prefill_draft_id=draft_id)


def render_publish() -> None:
    render_content_workflow_progress("발행 보조")
    st.warning("로그인·쿠키·비밀번호를 저장하거나 게시 버튼을 자동으로 누르지 않습니다. 사용자가 최종 검토 후 직접 발행합니다.")
    with db_connection() as con:
        drafts = list_drafts(con)
        if not drafts:
            st.info("발행할 초안이 없습니다.")
            return
        draft_map = {item["draft_id"]: item["title"] for item in drafts}
        draft_ids = list(draft_map)
        prefill_draft_id = str(st.session_state.get("prefill_draft_id") or "")
        draft_index = draft_ids.index(prefill_draft_id) if prefill_draft_id in draft_ids else 0
        draft_id = st.selectbox(
            "발행할 글",
            draft_ids,
            index=draft_index,
            format_func=draft_map.get,
        )
        draft = get_draft(con, draft_id)
        if draft is None:
            st.error("초안을 찾을 수 없습니다.")
            return

        fact_summary = get_fact_check_summary(con, draft_id)
        if fact_summary["total"] == 0:
            st.info("등록된 사실 확인 항목이 없습니다. 숫자·가격·정책·법률 내용을 직접 확인한 뒤 발행하세요.")
        elif fact_summary["unresolved"] > 0:
            st.warning(
                f"사실 확인이 끝나지 않은 항목이 {fact_summary['unresolved']}개 남아 있습니다. "
                f"미확인 {fact_summary['needs_verification']}개 · 수정 필요 {fact_summary['needs_revision']}개"
            )
        else:
            st.success(f"등록된 사실 확인 항목 {fact_summary['verified']}개를 모두 확인했습니다.")

        profile_sync = synchronize_curated_blog_profiles(con)
        profiles = list(profile_sync.profiles)
        if not profiles:
            st.info("등록된 블로그 프로필이 없습니다. 설정에서 블로그 프로필을 먼저 추가하세요.")
            if st.button("블로그 프로필 설정으로 이동", type="primary"):
                navigate_to_page("설정")
            return

        selected_profile_id = render_publish_channel_assignment(
            con,
            draft=draft,
            profiles=profiles,
        )
        if not selected_profile_id:
            return
        profile = next(
            item
            for item in profiles
            if str(item["blog_profile_id"]) == selected_profile_id
        )
        render_publish_preparation(
            con,
            draft=draft,
            profile=profile,
        )

        st.subheader("발행 완료 기록")
        with st.form("publish_record_form"):
            published_url = st.text_input("발행된 글 URL · 아직 없으면 비워도 됨")
            memo = st.text_area("발행 메모", height=80)
            acknowledge_unverified = False
            if fact_summary["unresolved"] > 0:
                acknowledge_unverified = st.checkbox(
                    "미확인 또는 수정 필요 항목이 남아 있음을 확인했고, 직접 검토 후 발행했습니다."
                )
            submitted = st.form_submit_button("발행 완료로 저장")
            if submitted:
                try:
                    mark_published(
                        con,
                        draft_id=draft_id,
                        platform=platform,
                        write_url=write_url,
                        published_url=published_url,
                        memo=memo,
                        allow_unverified=acknowledge_unverified,
                        blog_profile_id=selected_profile_id,
                    )
                    st.success("발행 완료 상태를 저장했습니다.")
                except ValueError as exc:
                    st.error(str(exc))

def _render_actual_quota_usage(
    con,
    *,
    interval_minutes: int,
    naver_daily_limit: int,
    kakao_daily_limit: int,
) -> None:
    assessment = analyze_actual_quota_usage(
        con,
        interval_minutes=interval_minutes,
        naver_daily_limit=naver_daily_limit,
        kakao_daily_limit=kakao_daily_limit,
    )
    portals = {portal.source_name: portal for portal in assessment.portals}

    st.markdown("#### 실제 실행 이력 기준")
    actual_cols = st.columns(4)
    actual_cols[0].metric(
        "분석 표본",
        assessment.sample_label,
        help=(
            "최근 7일의 전체 성공·부분 성공 수집 중 NAVER와 Daum에서 실제 요청이 발생한 실행 수입니다. "
            "표본이 적으면 예상치의 신뢰도도 낮아질 수 있습니다."
        ),
        border=True,
    )
    for column, source_name in zip(actual_cols[1:3], ("naver", "daum")):
        portal = portals[source_name]
        value = (
            f"약 {portal.estimated_calls_per_day:,}회/일"
            if portal.sample_count
            else "기록 없음"
        )
        delta = (
            f"한도 {portal.estimated_usage_percent:.1f}%"
            if portal.sample_count
            else None
        )
        column.metric(
            f"{QUOTA_PORTAL_LABELS[source_name]} 예상",
            value,
            delta,
            delta_color="off",
            help=(
                "최근 7일 실행당 평균 실제 요청 수에 현재 자동 수집 설정의 하루 실행 횟수를 곱한 예상값입니다. "
                "request_count에는 재시도 호출이 이미 포함됩니다."
            ),
            border=True,
        )
    actual_cols[3].metric(
        "운영 판단",
        assessment.status_label,
        help=(
            "최근 실제 실행량과 현재 자동 수집 간격을 함께 비교한 운영 상태입니다. "
            "안전·주의·위험 판단의 자세한 이유는 바로 아래 안내 문구에서 확인합니다."
        ),
        border=True,
    )

    detail_parts = []
    for source_name in ("naver", "daum"):
        portal = portals[source_name]
        if not portal.sample_count:
            detail_parts.append(f"{QUOTA_PORTAL_LABELS[source_name]} 기록 없음")
            continue
        detail_parts.append(
            f"{QUOTA_PORTAL_LABELS[source_name]} 최근 24시간 {portal.calls_24h:,}회 · "
            f"실행당 평균 {portal.average_calls_per_run:.1f}회 · 최대 {portal.max_calls_per_run:,}회 · "
            f"재시도율 {portal.retry_rate_percent:.1f}%"
        )
    st.caption(" | ".join(detail_parts))
    st.caption(
        "예상 하루 호출량은 자동 수집 주기에 따른 값이며, 화면에서 추가로 실행한 수동 수집은 "
        "최근 24시간 실제 호출 수에 포함됩니다. 같은 API 키를 다른 프로그램에서 사용한 양은 포함되지 않습니다."
    )

    if assessment.status_level == "danger":
        st.error(assessment.message)
    elif assessment.status_level == "warning":
        st.warning(assessment.message)
    elif assessment.status_level == "safe":
        st.success(assessment.message)
    else:
        st.info(assessment.message)


def _render_gemini_model_settings(con) -> None:
    st.markdown("#### 모델 선택")
    settings_flash = st.session_state.pop("gemini_model_settings_flash", None)
    if settings_flash:
        st.success(str(settings_flash))
    base_config = get_gemini_config()
    models = get_available_gemini_models(con, base_config=base_config)
    cached_models = load_gemini_model_catalog(con)
    model_map = {model.model_id: model for model in models}
    model_ids = list(model_map)
    current_auto = get_selected_gemini_model(
        con,
        MODEL_PURPOSE_AUTO,
        base_config=base_config,
    )
    current_manual = get_selected_gemini_model(
        con,
        MODEL_PURPOSE_DATA_REVIEW,
        base_config=base_config,
    )
    for selected in (current_auto, current_manual):
        if selected not in model_ids:
            model_ids.insert(0, selected)

    status_cols = st.columns(3)
    status_cols[0].metric(
        "Gemini API 키",
        "설정됨" if base_config.api_key else "키 필요",
        help="API 키는 .env의 GEMINI_API_KEY에서만 읽고 DuckDB에는 저장하지 않습니다.",
        border=True,
    )
    status_cols[1].metric(
        "저장 모델 목록",
        f"{len(cached_models):,}개",
        help=(
            "마지막 정상 API 조회에서 DuckDB에 저장한 모델 수입니다. "
            "목록 조회에 실패해도 기존 캐시는 유지됩니다."
        ),
        border=True,
    )
    refreshed_at = get_setting(con, MODEL_CATALOG_REFRESHED_AT_SETTING, "")
    status_cols[2].metric(
        "마지막 목록 조회",
        refreshed_at or "조회 기록 없음",
        help="Gemini 공식 모델 목록 API를 마지막으로 정상 조회해 저장한 시각입니다.",
        border=True,
    )

    refresh_cols = st.columns([1, 3])
    if refresh_cols[0].button(
        "모델 목록 새로고침",
        type="secondary",
        width="stretch",
        disabled=not bool(base_config.api_key),
        icon=API_BUTTON_ICON,
    ):
        st.session_state["gemini_model_catalog_refresh_requested"] = True
        st.rerun()
    refresh_cols[1].caption(
        "공식 models.list 결과에서 generateContent를 지원하는 Gemini 텍스트 모델만 저장합니다. "
        "API 키 변경 후에는 목록을 다시 조회하세요."
    )

    with st.form("gemini_model_settings_form"):
        auto_model = st.selectbox(
            "자동·예약 글감 분석 모델",
            model_ids,
            index=model_ids.index(current_auto),
            format_func=lambda model_id: model_display_label(model_map[model_id])
            if model_id in model_map
            else model_id,
            help=(
                "최신 데이터 수집·분석, 주제 방향 자동 생성, run_trend_refresh.bat와 "
                "Windows 예약 수집이 공통으로 사용합니다."
            ),
        )
        manual_model = st.selectbox(
            "Gemini 기본 군집화 모델",
            model_ids,
            index=model_ids.index(current_manual),
            format_func=lambda model_id: model_display_label(model_map[model_id])
            if model_id in model_map
            else model_id,
            help=(
                "공개 제목 후보를 같은 사건별로 직접 묶고 분리하며 대표 제목을 생성합니다. "
                "초안 작성에는 사용하지 않습니다."
            ),
        )
        st.markdown("##### AI 기본 군집화")
        ai_clustering_enabled = st.checkbox(
            "Gemini를 기본 주제 군집 엔진으로 사용",
            value=_setting_enabled(
                get_setting(con, AI_CLUSTERING_ENABLED_SETTING, "true")
            ),
            help=(
                "최근 미처리 원문을 URL·안전한 동일 제목으로 1차 정리한 뒤 Flash-Lite가 2차 분류합니다. "
                "API 키가 없거나 기능을 끈 경우에는 미처리 상태를 보존하고 임의 의미 군집을 만들지 않습니다."
            ),
        )
        clustering_max_items = st.number_input(
            "2차 군집 후보를 찾을 최근 미처리 원문",
            min_value=200,
            max_value=10000,
            value=max(200, min(10000, int(get_setting(
                con,
                AI_CLUSTERING_MAX_ITEMS_SETTING,
                str(DEFAULT_AI_CLUSTERING_MAX_ITEMS),
            ) or DEFAULT_AI_CLUSTERING_MAX_ITEMS))),
            step=200,
            help=(
                "기본값은 최근 4,000개입니다. 같은 URL과 안전한 동일 제목을 1차로 묶은 뒤 "
                "최신 1차 군집부터 2차 군집 배치에 넣습니다."
            ),
        )
        clustering_batch_size = st.number_input(
            "Gemini 요청 1회당 1차 군집",
            min_value=20,
            max_value=200,
            value=max(20, min(200, int(get_setting(
                con,
                AI_CLUSTERING_BATCH_SIZE_SETTING,
                str(DEFAULT_AI_CLUSTERING_BATCH_SIZE),
            ) or DEFAULT_AI_CLUSTERING_BATCH_SIZE))),
            step=20,
            help="이번 시험 기본값은 200개입니다.",
        )
        clustering_max_batches = st.number_input(
            "백그라운드 작업 1회당 최대 Gemini 요청",
            min_value=1,
            max_value=20,
            value=max(1, min(20, int(get_setting(
                con,
                AI_CLUSTERING_MAX_BATCHES_SETTING,
                str(DEFAULT_AI_CLUSTERING_MAX_BATCHES),
            ) or DEFAULT_AI_CLUSTERING_MAX_BATCHES))),
            step=1,
            help=(
                "기본값은 시험용 5회이며, 최대 1,000개의 1차 군집을 처리합니다. "
                "토큰 로그를 확인한 뒤 최대 20회로 확대할 수 있습니다."
            ),
        )
        if st.form_submit_button("Gemini 모델 설정 저장", type="primary"):
            set_selected_gemini_model(con, MODEL_PURPOSE_AUTO, auto_model)
            set_selected_gemini_model(con, MODEL_PURPOSE_DATA_REVIEW, manual_model)
            set_setting(
                con,
                AI_CLUSTERING_ENABLED_SETTING,
                "true" if ai_clustering_enabled else "false",
            )
            set_setting(con, AI_CLUSTERING_MAX_ITEMS_SETTING, str(int(clustering_max_items)))
            set_setting(con, AI_CLUSTERING_BATCH_SIZE_SETTING, str(int(clustering_batch_size)))
            set_setting(con, AI_CLUSTERING_MAX_BATCHES_SETTING, str(int(clustering_max_batches)))
            st.session_state["gemini_model_settings_flash"] = (
                "Gemini 자동 분석 모델과 기본 군집화 모델을 저장했습니다."
            )
            st.rerun()

    st.caption(
        "같은 정규 URL을 먼저 묶고, 안전한 완전 동일 제목을 두 번째로 묶어 1차 군집을 만듭니다. "
        "Flash-Lite는 1차 군집 최대 200개를 기존 2차 군집 후보와 비교해 연결하거나 새 군집으로 만듭니다. "
        "기존 2차 군집끼리는 자동 병합하지 않으며, 불확실 결과는 최대 3회 재시도 후 검토 대상으로 남깁니다. "
        "수동 실행은 별도 프로세스에서 최대 5배치를 처리하고 Gemini 대기 중 DuckDB 연결을 유지하지 않습니다."
    )

    auto_config = build_gemini_config_for_purpose(
        con,
        MODEL_PURPOSE_AUTO,
        base_config=base_config,
    )
    request_capacity = (
        int(auto_config.topic_angle_batch_limit)
        * int(auto_config.topic_angle_max_parallel_requests)
    )
    st.caption(
        f"현재 자동 분석: 요청당 최대 {int(auto_config.topic_angle_batch_limit):,}개 · "
        f"동시 요청 최대 {int(auto_config.topic_angle_max_parallel_requests):,}개 · "
        f"버튼 1회 최대 {request_capacity:,}개. 기본 구성은 요청당 15개·동시 요청 1개입니다."
    )

    rows = []
    for model in models:
        rate_limit = model_rate_limit_reference(model.model_id)
        roles = []
        if model.model_id == current_auto:
            roles.append("자동·예약")
        if model.model_id == current_manual:
            roles.append("기본 군집화")
        rows.append(
            {
                "모델": model.display_name,
                "모델 ID": model.model_id,
                "용도": ", ".join(roles) or "-",
                "입력 토큰 한도": model.input_token_limit,
                "출력 토큰 한도": model.output_token_limit,
                "참고 RPM": rate_limit["rpm"] if rate_limit else None,
                "참고 TPM": rate_limit["tpm"] if rate_limit else None,
                "참고 RPD": rate_limit["rpd"] if rate_limit else None,
                "상태": {
                    "stable": "일반",
                    "latest_alias": "자동 별칭",
                    "preview": "미리보기",
                    "experimental": "실험",
                }.get(model.lifecycle, model.lifecycle),
            }
        )
    with st.expander("선택 가능한 Gemini 모델과 참고 한도", expanded=False):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "RPM·TPM·RPD는 2026-07-27에 확인한 무료 티어 참고값이 있는 모델만 표시합니다. "
            "실제 적용 한도는 Google AI Studio의 현재 프로젝트 화면이 우선합니다."
        )



def _format_scheduler_timestamp(value) -> str:
    if value is None:
        return "기록 없음"
    formatter = getattr(value, "strftime", None)
    if callable(formatter):
        return formatter("%Y-%m-%d %H:%M:%S")
    return str(value)


def _render_latest_background_refresh_status(con, *, interval_minutes: int) -> None:
    st.markdown("#### 최근 예약 수집 실제 결과")
    st.caption(
        "Windows 작업 스케줄러의 준비·실행 가능 표시는 작업 등록 상태입니다. "
        "아래 내용이 이 프로그램 DB에 실제로 기록된 최근 예약 수집 결과입니다."
    )
    snapshot = get_latest_background_refresh_snapshot(
        con,
        expected_interval_minutes=interval_minutes,
    )
    if not snapshot.available:
        st.warning(
            "예약 수집 실행 기록이 없습니다. 작업이 등록돼 있다면 다음 실행 뒤 수집 이력에서 다시 확인하세요."
        )
        return

    status_labels = {
        "running": "실행 중",
        "failure": "실패",
        "partial_success": "부분 성공",
        "skipped_overlap": "중복 실행 생략",
        "stale": "예정 주기보다 오래됨",
        "no_change": "정상 실행·변경 없음",
        "success": "정상 반영",
    }
    metric_cols = st.columns(5)
    metric_cols[0].metric(
        "실제 실행 상태",
        status_labels.get(snapshot.diagnostic_status, snapshot.diagnostic_status or "확인 없음"),
        border=True,
    )
    metric_cols[1].metric(
        "최근 시작",
        _format_scheduler_timestamp(snapshot.started_at),
        border=True,
    )
    metric_cols[2].metric(
        "신규 저장",
        f"{snapshot.newly_saved_count:,}개",
        border=True,
    )
    metric_cols[3].metric(
        "기존 갱신",
        f"{snapshot.updated_count:,}개",
        border=True,
    )
    metric_cols[4].metric(
        "외부 요청",
        f"{snapshot.request_count:,}회",
        help=("수집 출처 행에 기록된 요청 수 합계입니다. Gemini 요청 여부·횟수는 "
              "아래 출처별 상세의 Gemini 주제 방향 행에서 따로 확인하세요."),
        border=True,
    )

    message = snapshot.summary or "최근 예약 수집 결과 요약이 없습니다."
    if snapshot.diagnostic_status == "failure":
        st.error(message)
    elif snapshot.diagnostic_status in {"partial_success", "skipped_overlap", "stale"}:
        st.warning(message)
    elif snapshot.diagnostic_status == "no_change":
        st.info(
            message
            + " · 성공으로 끝나도 외부 결과가 이전과 같으면 신규·갱신 건수와 Gemini 호출이 늘지 않을 수 있습니다."
        )
    elif snapshot.diagnostic_status == "running":
        st.info(message)
    else:
        st.success(message)
    if snapshot.error_message:
        st.caption(f"오류·주의: {snapshot.error_message}")

    source_labels = {
        "youtube": "YouTube",
        "google_trends": "Google Trends",
        "wikipedia": "위키백과",
        "naver": "NAVER 뉴스·블로그",
        "daum": "Daum 웹문서·카페",
        "ranking": "통합 군집·순위",
        "topic_angles": "Gemini 주제 방향",
    }
    source_status_labels = {
        "success": "성공",
        "partial_success": "부분 성공",
        "failure": "실패",
        "skipped": "생략·변경 없음",
    }
    rows = []
    for item in snapshot.sources:
        rows.append(
            {
                "출처·단계": source_labels.get(
                    str(item.get("source_name") or ""),
                    str(item.get("source_name") or ""),
                ),
                "상태": source_status_labels.get(
                    str(item.get("status") or ""),
                    str(item.get("status") or ""),
                ),
                "요청": int(item.get("request_count") or 0),
                "재시도": int(item.get("retry_count") or 0),
                "신규": int(item.get("newly_saved_count") or 0),
                "갱신": int(item.get("updated_count") or 0),
                "제외·미처리": int(item.get("skipped_count") or 0),
                "오류": str(item.get("error_message") or ""),
            }
        )
    if rows:
        with st.expander("최근 예약 실행 출처별 상세", expanded=False):
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if snapshot.topic_angle_status == "skipped":
        st.caption(
            "Gemini 주제 방향 단계가 생략·변경 없음이면 새로 분석할 글감이 없거나 API 키가 없는 경우입니다. "
            "이때 예약 수집은 정상이어도 Gemini API 사용 로그의 최근 시각은 바뀌지 않습니다."
        )


def _render_refresh_scheduler_settings(con) -> None:
    st.markdown("#### 예약 실행 설정")
    scheduler_flash = st.session_state.pop("scheduler_flash", None)
    if scheduler_flash:
        st.success(str(scheduler_flash))

    status = get_refresh_scheduler_status(PROJECT_ROOT)
    saved_interval = int(get_setting(con, "trend_refresh_interval_minutes", "180") or 180)
    current_interval = status.interval_minutes or saved_interval
    current_interval = max(
        MIN_SCHEDULE_INTERVAL_MINUTES,
        min(MAX_SCHEDULE_INTERVAL_MINUTES, int(current_interval)),
    )

    portal_query_limit = int(get_setting(con, "trend_portal_query_limit", "50") or 50)
    portal_pages_per_query = int(
        get_setting(con, "trend_portal_pages_per_query", "2") or 2
    )
    naver_daily_limit = int(
        get_setting(con, "naver_search_daily_safety_limit", "25000") or 25000
    )
    kakao_daily_limit = int(
        get_setting(con, "kakao_daum_daily_safety_limit", "50000") or 50000
    )
    recommendation = calculate_quota_interval_recommendation(
        portal_query_limit=portal_query_limit,
        portal_pages_per_query=portal_pages_per_query,
        naver_daily_limit=naver_daily_limit,
        kakao_daily_limit=kakao_daily_limit,
    )

    status_cols = st.columns(4)
    status_cols[0].metric(
        "작업 스케줄러",
        "등록됨" if status.registered else "미등록",
        help="Windows 작업 스케줄러에 콘텐츠 트렌드 자동 수집 작업이 등록되어 있는지 표시합니다.",
        border=True,
    )
    status_cols[1].metric(
        "현재 등록 주기",
        f"{status.interval_minutes}분"
        if status.registered and status.interval_minutes
        else "-",
        help="현재 Windows 작업 스케줄러에 실제 등록된 자동 수집 간격입니다.",
        border=True,
    )
    status_cols[2].metric(
        "다음 실행",
        status.next_run or "확인 없음",
        help="Windows 작업 스케줄러가 알려주는 다음 예정 실행 시각입니다.",
        border=True,
    )
    status_cols[3].metric(
        "작업 상태",
        status.state or ("사용 가능" if status.supported else "Windows 전용"),
        help="작업 스케줄러가 준비·실행 중·사용 안 함 등 어떤 상태인지 표시합니다.",
        border=True,
    )

    if status.registered and status.action_matches_project is False:
        st.warning(
            "등록된 작업이 현재 프로젝트의 run_trend_refresh.bat를 가리키지 않습니다. "
            "아래 등록·변경 버튼을 눌러 현재 경로로 갱신하세요."
        )
    if not status.supported:
        st.info(status.message)

    _render_latest_background_refresh_status(
        con,
        interval_minutes=current_interval,
    )

    interval_minutes = st.number_input(
        "자동 수집 간격(분)",
        min_value=MIN_SCHEDULE_INTERVAL_MINUTES,
        max_value=MAX_SCHEDULE_INTERVAL_MINUTES,
        value=current_interval,
        step=5,
        help="180으로 설정하면 3시간마다 실행됩니다. 등록·변경을 누르면 같은 작업 이름으로 기존 스케줄을 덮어씁니다.",
        key="trend_refresh_scheduler_interval",
    )
    interval_minutes = int(interval_minutes)
    runs_per_day = recommendation.runs_per_day(interval_minutes)
    planned_calls_per_day = recommendation.planned_calls_per_day(interval_minutes)
    retry_worst_calls_per_day = recommendation.retry_worst_calls_per_day(interval_minutes)

    st.markdown("#### 설정 최대치 기준")
    quota_cols = st.columns(4)
    quota_cols[0].metric(
        "수집 1회 포털 호출",
        f"각 {recommendation.planned_calls_per_run:,}회",
        help="NAVER 뉴스·블로그와 Daum 웹문서·카페에 한 번의 수집에서 계획상 보낼 최대 호출 수입니다.",
        border=True,
    )
    quota_cols[1].metric(
        "하루 예상 실행",
        f"약 {runs_per_day}회",
        help="현재 입력한 자동 수집 간격을 24시간에 적용했을 때의 예상 실행 횟수입니다.",
        border=True,
    )
    quota_cols[2].metric(
        "정상 호출 기준 최소",
        f"{recommendation.normal_min_interval_minutes}분 이상",
        help="재시도가 없다고 가정했을 때 설정된 일일 안전 한도 안에 머무르기 위한 최소 수집 간격입니다.",
        border=True,
    )
    quota_cols[3].metric(
        "최대 재시도 포함 권장",
        f"{recommendation.retry_safe_min_interval_minutes}분 이상",
        help="모든 포털 요청에 최대 재시도가 발생하는 보수적인 상황까지 고려한 권장 최소 수집 간격입니다.",
        border=True,
    )
    st.caption(
        f"현재 {interval_minutes}분 설정은 정상 요청 기준으로 하루 포털별 최대 "
        f"{planned_calls_per_day:,}회, 모든 요청이 최대 2회씩 재시도되는 극단적인 경우 "
        f"{retry_worst_calls_per_day:,}회입니다. 실제 탐색어가 적으면 호출량도 줄어듭니다."
    )

    if interval_minutes < recommendation.normal_min_interval_minutes:
        st.error(
            "현재 저장된 포털 탐색 범위와 일일 한도 기준으로 너무 짧습니다. "
            f"최소 {recommendation.normal_min_interval_minutes}분 이상으로 설정하세요."
        )
    elif interval_minutes < recommendation.retry_safe_min_interval_minutes:
        st.warning(
            f"정상 수집 기준으로는 일일 한도 안이지만, 재시도가 계속 발생하는 상황까지 여유 있게 보려면 "
            f"{recommendation.retry_safe_min_interval_minutes}분 이상을 권장합니다. "
            "프로그램의 기존 쿼터 보호 로직은 설정 한도 직전에 추가 포털 호출을 중단하므로 초과 과금 대신 "
            "당일 후반 일부 수집이 생략될 수 있습니다."
        )
    else:
        st.success("현재 선택한 간격은 최대 재시도 상황까지 감안한 일일 권장 범위입니다.")

    _render_actual_quota_usage(
        con,
        interval_minutes=interval_minutes,
        naver_daily_limit=naver_daily_limit,
        kakao_daily_limit=kakao_daily_limit,
    )

    st.caption(
        "같은 이름의 Windows 작업을 강제 갱신하므로 180분으로 변경하면 기존 작업이 3시간 작업으로 교체됩니다. "
        "PC가 켜져 있고 현재 Windows 사용자가 로그인된 동안 실행되며, 첫 실행은 등록한 간격 뒤에 시작됩니다."
    )
    st.caption(
        "수동 수집·저장 데이터 순위 재계산·예약 수집이 겹치면 먼저 시작한 작업만 실행합니다. "
        "두 번째 작업은 외부 API를 호출하지 않고 정상적으로 생략됩니다."
    )
    auto_config = build_gemini_config_for_purpose(con, MODEL_PURPOSE_AUTO)
    st.caption(
        f"예약 수집 뒤 Gemini 자동 분석은 {auto_config.model} 모델로 후보 최대 "
        f"{BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST:,}개를 요청 한 번에 처리합니다. "
        "모델은 위 Gemini 모델 설정과 오늘의 트렌드 화면에서 변경할 수 있습니다."
    )
    button_cols = st.columns(2)
    register_disabled = (
        not status.supported
        or interval_minutes < recommendation.normal_min_interval_minutes
    )
    if button_cols[0].button(
        "스케줄 등록·변경",
        type="primary",
        width="stretch",
        disabled=register_disabled,
    ):
        result = register_or_update_refresh_scheduler(
            PROJECT_ROOT,
            interval_minutes=interval_minutes,
        )
        if result.success:
            set_setting(con, "trend_refresh_interval_minutes", str(interval_minutes))
            st.session_state["scheduler_flash"] = result.message
            st.rerun()
        else:
            st.error(result.message)
    if button_cols[1].button(
        "자동 수집 스케줄 삭제",
        type="secondary",
        width="stretch",
        disabled=not status.supported or not status.registered,
    ):
        result = delete_refresh_scheduler()
        if result.success:
            st.session_state["scheduler_flash"] = result.message
            st.rerun()
        else:
            st.error(result.message)




def _render_blog_profile_settings(con) -> None:
    sync_result = synchronize_curated_blog_profiles(con)
    render_curated_blog_profile_settings(
        con,
        sync_result=sync_result,
    )
    st.divider()
    render_blog_channel_strategy_settings(con)


SETTINGS_SECTION_OPTIONS = (
    "기본 설정",
    "AI·품질",
    "발행 채널",
    "자동화·이력",
    "데이터·연동",
)

SETTINGS_SECTION_DESCRIPTIONS = {
    "기본 설정": "교환 파일 위치와 콘텐츠 작성 기본값을 먼저 확인하고 수집 범위·공개 데이터·안전 한도를 관리합니다.",
    "AI·품질": "Gemini 모델을 선택하고 글감 분석 품질과 운영 표본을 진단합니다.",
    "발행 채널": "Blogger·네이버·티스토리 등 발행 프로필과 추천 규칙을 관리합니다.",
    "자동화·이력": "Windows 예약 수집과 과거 수집 실행 결과를 확인합니다.",
    "데이터·연동": "로컬 데이터 보관 상태, API 연결과 YouTube 교환 파일을 확인합니다.",
}


def _render_settings_navigation_styles() -> None:
    """Apply wide, centered navigation only while the settings page is rendered."""
    st.markdown(
        """
        <style>
        .st-key-settings_section_navigation [data-testid="stSegmentedControl"] {
            width: 100%;
            overflow-x: auto;
            scrollbar-width: thin;
        }
        .st-key-settings_section_navigation [data-baseweb="button-group"] {
            display: flex;
            width: max-content;
            min-width: min(100%, 66rem);
            gap: 0.55rem;
            margin: 0 auto;
        }
        .st-key-settings_section_navigation button {
            flex: 0 0 12.5rem;
            min-width: 12.5rem;
            min-height: 2.7rem;
            padding-left: 1.25rem !important;
            padding-right: 1.25rem !important;
            border-radius: 0.62rem !important;
            font-weight: 720;
            white-space: nowrap;
        }
        .st-key-settings_section_navigation button p {
            font-size: 0.9rem;
            white-space: nowrap;
        }
        .block-container {
            padding-bottom: 5.5rem !important;
        }
        [data-testid="stTabs"] {
            overflow: visible !important;
        }
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            display: flex;
            width: 100%;
            min-width: 0;
            max-width: 100%;
            overflow-x: auto;
            overflow-y: hidden;
            scrollbar-width: thin;
            justify-content: center;
            gap: 0.55rem;
        }
        [data-testid="stTabs"] [role="tabpanel"] {
            overflow: visible !important;
            padding-bottom: 1rem;
        }
        [data-testid="stTabs"] button[role="tab"] {
            flex: 0 0 12.5rem;
            min-width: 12.5rem;
            padding-left: 1.35rem;
            padding-right: 1.35rem;
            justify-content: center;
            white-space: nowrap;
        }
        [data-testid="stTabs"] button[role="tab"] p {
            white-space: nowrap;
        }
        @media (max-width: 980px) {
            .st-key-settings_section_navigation {
                overflow-x: auto;
            }
            .st-key-settings_section_navigation [data-baseweb="button-group"] {
                min-width: 66rem;
            }
            [data-testid="stTabs"] [data-baseweb="tab-list"] {
                justify-content: flex-start;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_settings() -> None:
    _render_settings_navigation_styles()
    st.caption(
        "설정 메뉴를 열면 기본 설정부터 표시합니다. "
        "상단 메뉴에서 필요한 영역만 선택해 조회하고 변경하세요."
    )
    if st.session_state.get("settings_section") not in SETTINGS_SECTION_OPTIONS:
        st.session_state["settings_section"] = SETTINGS_SECTION_OPTIONS[0]
    with st.container(key="settings_section_navigation"):
        settings_section = st.segmented_control(
            "설정 영역",
            SETTINGS_SECTION_OPTIONS,
            default=SETTINGS_SECTION_OPTIONS[0],
            key="settings_section",
            label_visibility="collapsed",
        )
    if settings_section not in SETTINGS_SECTION_OPTIONS:
        settings_section = SETTINGS_SECTION_OPTIONS[0]

    st.markdown(f"### {settings_section}")
    st.caption(SETTINGS_SECTION_DESCRIPTIONS[settings_section])

    refreshed_models = None
    refresh_error = ""
    if (
        settings_section == "AI·품질"
        and st.session_state.pop("gemini_model_catalog_refresh_requested", False)
    ):
        try:
            base_config = get_gemini_config()
            refreshed_models = fetch_gemini_model_catalog(
                base_config.api_key,
                timeout_seconds=min(60, max(10, base_config.timeout_seconds)),
            )
        except GeminiModelCatalogError as exc:
            refresh_error = str(exc)

    with db_connection() as con:
        if settings_section == "기본 설정":
            with st.form("settings_form"):
                collection_tabs = st.tabs(
                    ["기본 정보", "탐색 범위", "공개 데이터", "보관·한도"]
                )

                with collection_tabs[0]:
                    st.caption("교환 파일 위치와 콘텐츠 작성 기본값을 관리합니다.")
                    parquet_path = st.text_input(
                        "YouTube Parquet 교환 파일 경로",
                        value=get_setting(con, "youtube_parquet_path"),
                    )
                    youtube_path = st.text_input("기존 YouTube DuckDB 경로", value=get_setting(con, "youtube_db_path"))
                    audience = st.text_input("기본 독자 대상", value=get_setting(con, "default_audience"))
                    purpose = st.text_input("기본 글 목적", value=get_setting(con, "default_purpose"))

                with collection_tabs[1]:
                    st.caption("자동 탐색 범위와 포털 요청량을 조정합니다.")
                    seed_queries = st.text_area(
                        "기본 탐색어 · 한 줄에 하나",
                        value=get_setting(con, "trend_seed_queries"),
                        height=170,
                        help="YouTube·Google Trends에서 발견한 주제는 자동 추가되며, 여기는 포털 검색으로 보완할 기본 분야입니다.",
                    )
                    lookback_hours = st.number_input(
                        "순위 분석 범위(시간)",
                        min_value=12,
                        max_value=168,
                        value=int(get_setting(con, "trend_lookback_hours", "72") or 72),
                        step=12,
                    )
                    portal_col1, portal_col2, portal_col3 = st.columns(3)
                    with portal_col1:
                        portal_query_limit = st.number_input(
                            "포털 탐색어 최대 개수",
                            min_value=10,
                            max_value=100,
                            value=int(get_setting(con, "trend_portal_query_limit", "50") or 50),
                            step=10,
                            help="Google Trends·YouTube·기본 탐색어를 합쳐 NAVER와 Daum에 조회할 최대 주제 수입니다.",
                        )
                    with portal_col2:
                        portal_pages_per_query = st.number_input(
                            "탐색어당 조회 페이지",
                            min_value=1,
                            max_value=5,
                            value=int(get_setting(con, "trend_portal_pages_per_query", "2") or 2),
                            step=1,
                            help="뉴스·블로그·웹문서·카페에서 각 탐색어를 몇 페이지까지 확인할지 정합니다.",
                        )
                    with portal_col3:
                        results_per_query = st.number_input(
                            "페이지당 검색 결과 수",
                            min_value=3,
                            max_value=50,
                            value=int(get_setting(con, "trend_results_per_query", "10") or 10),
                            step=1,
                        )
                    estimated_calls = int(portal_query_limit) * 2 * int(portal_pages_per_query)
                    st.caption(
                        f"현재 설정은 수집 1회당 NAVER 최대 {estimated_calls:,}회, "
                        f"Daum 최대 {estimated_calls:,}회를 호출합니다. 실제 탐색어가 적으면 더 적게 사용합니다."
                    )
                    worker_col1, worker_col2 = st.columns(2)
                    with worker_col1:
                        naver_max_workers = st.number_input(
                            "NAVER 동시 요청 수",
                            min_value=1,
                            max_value=10,
                            value=int(get_setting(con, "naver_search_workers", "6") or 6),
                            step=1,
                            help="공식 최대 50 RPS보다 충분히 낮게 유지합니다. 기본값 6을 권장합니다.",
                        )
                    with worker_col2:
                        daum_max_workers = st.number_input(
                            "Daum 동시 요청 수",
                            min_value=1,
                            max_value=6,
                            value=int(get_setting(con, "daum_search_workers", "4") or 4),
                            step=1,
                            help="Daum의 초당 제한에 여유를 두기 위해 기본값 4를 권장합니다.",
                        )

                with collection_tabs[2]:
                    st.caption("키 없이 사용하는 공개 데이터 출처를 관리합니다.")
                    public_col1, public_col2 = st.columns(2)
                    with public_col1:
                        google_trends_enabled = st.checkbox(
                            "Google Trends 한국 RSS 사용",
                            value=_setting_enabled(get_setting(con, "google_trends_enabled", "true")),
                            help="별도 키 없이 Google Trends의 공식 RSS 인기 검색어를 수집합니다.",
                        )
                        google_trends_limit = st.number_input(
                            "Google Trends 최대 수집 개수",
                            min_value=10,
                            max_value=100,
                            value=int(get_setting(con, "google_trends_limit", "50") or 50),
                            step=10,
                        )
                    with public_col2:
                        wikipedia_pageviews_enabled = st.checkbox(
                            "한국어 위키백과 조회수 사용",
                            value=_setting_enabled(
                                get_setting(con, "wikipedia_pageviews_enabled", "true")
                            ),
                            help="별도 키 없이 Wikimedia Analytics API의 일간 인기 문서를 수집합니다.",
                        )
                        wikipedia_pageviews_limit = st.number_input(
                            "위키백과 최대 수집 개수",
                            min_value=10,
                            max_value=200,
                            value=int(get_setting(con, "wikipedia_pageviews_limit", "50") or 50),
                            step=10,
                        )

                with collection_tabs[3]:
                    st.caption("보관 기간, 분석량과 API 안전 한도를 관리합니다.")
                    cleanup_enabled = st.checkbox(
                        "하루 한 번 오래된 데이터 자동 정리",
                        value=_setting_enabled(get_setting(con, "data_cleanup_enabled", "true")),
                        help="글감으로 선택한 주제에 연결되지 않은 오래된 원본만 정리합니다.",
                    )
                    retention_col1, retention_col2, retention_col3 = st.columns(3)
                    with retention_col1:
                        source_retention_days = st.number_input(
                            "미사용 원본 보관 일수",
                            min_value=7,
                            max_value=365,
                            value=int(get_setting(con, "source_retention_days", "30") or 30),
                            step=1,
                        )
                    with retention_col2:
                        sync_run_retention_days = st.number_input(
                            "수집 실행 기록 보관 일수",
                            min_value=30,
                            max_value=730,
                            value=int(get_setting(con, "sync_run_retention_days", "90") or 90),
                            step=10,
                        )
                    with retention_col3:
                        api_usage_retention_months = st.number_input(
                            "호출량 기록 보관 개월",
                            min_value=3,
                            max_value=36,
                            value=int(get_setting(con, "api_usage_retention_months", "13") or 13),
                            step=1,
                        )
                    st.caption(
                        "주제·초안과 연결된 원본은 보관 기간이 지나도 삭제하지 않습니다. "
                        "자동 정리는 수집 또는 순위 재계산을 실행할 때 하루 한 번만 확인합니다."
                    )
                    st.markdown("##### 순위 계산 시 출처별 최대 분석량")
                    analysis_col1, analysis_col2, analysis_col3, analysis_col4, analysis_col5 = st.columns(5)
                    with analysis_col1:
                        analysis_youtube_limit = st.number_input(
                            "YouTube",
                            min_value=100,
                            max_value=20000,
                            value=int(get_setting(con, "trend_analysis_youtube_limit", "2000") or 2000),
                            step=100,
                        )
                    with analysis_col2:
                        analysis_naver_limit = st.number_input(
                            "NAVER",
                            min_value=500,
                            max_value=20000,
                            value=int(get_setting(con, "trend_analysis_naver_limit", "4000") or 4000),
                            step=500,
                        )
                    with analysis_col3:
                        analysis_daum_limit = st.number_input(
                            "Daum",
                            min_value=500,
                            max_value=20000,
                            value=int(get_setting(con, "trend_analysis_daum_limit", "4000") or 4000),
                            step=500,
                        )
                    with analysis_col4:
                        analysis_google_limit = st.number_input(
                            "Google Trends",
                            min_value=50,
                            max_value=5000,
                            value=int(get_setting(con, "trend_analysis_google_limit", "500") or 500),
                            step=50,
                        )
                    with analysis_col5:
                        analysis_wikipedia_limit = st.number_input(
                            "위키백과",
                            min_value=50,
                            max_value=5000,
                            value=int(get_setting(con, "trend_analysis_wikipedia_limit", "500") or 500),
                            step=50,
                        )
                    st.caption(
                        "최근 분석 범위 안에서도 한 출처가 문서량만으로 다른 출처를 밀어내지 않도록 출처별 상한을 적용합니다."
                    )

                    st.markdown("#### 네이버 검색 API 무료 최대 한도")
                    naver_daily_limit = st.number_input(
                        "프로그램 일일 한도",
                        min_value=1,
                        max_value=NAVER_SEARCH_OFFICIAL_DAILY_LIMIT,
                        value=int(get_setting(con, "naver_search_daily_safety_limit", "25000") or 25000),
                        step=1000,
                        help="현재 NAVER 검색 API의 일일 최대 제공 한도는 25,000회입니다.",
                    )
                    naver_monthly_limit = st.number_input(
                        "프로그램 월간 한도",
                        min_value=1,
                        max_value=NAVER_SEARCH_OFFICIAL_MONTHLY_LIMIT,
                        value=int(get_setting(con, "naver_search_monthly_safety_limit", "775000") or 775000),
                        step=5000,
                        help="현재 NAVER 검색 API의 월간 최대 무료 제공 한도는 775,000회입니다.",
                    )
                    st.markdown("#### 카카오 Daum 검색 API 무료 최대 한도")
                    kakao_daily_limit = st.number_input(
                        "Daum 프로그램 일일 한도",
                        min_value=1,
                        max_value=KAKAO_DAUM_OFFICIAL_DAILY_LIMIT,
                        value=int(get_setting(con, "kakao_daum_daily_safety_limit", "50000") or 50000),
                        step=1000,
                        help="현재 Daum 검색 API의 일일 무료 제공 한도는 앱당 50,000회입니다.",
                    )
                    kakao_monthly_limit = st.number_input(
                        "카카오 앱 전체 프로그램 월간 한도",
                        min_value=1,
                        max_value=KAKAO_ALL_API_OFFICIAL_MONTHLY_LIMIT,
                        value=int(get_setting(con, "kakao_daum_monthly_safety_limit", "3000000") or 3000000),
                        step=10000,
                        help="카카오디벨로퍼스 앱 전체 API의 월간 무료 제공량은 3,000,000건입니다.",
                    )

                submitted = st.form_submit_button("설정 저장", type="primary", width="stretch")
                if submitted:
                    set_setting(con, "youtube_parquet_path", parquet_path.strip())
                    set_setting(con, "youtube_db_path", youtube_path.strip())
                    set_setting(con, "default_audience", audience.strip())
                    set_setting(con, "default_purpose", purpose.strip())
                    set_setting(con, "trend_seed_queries", seed_queries.strip())
                    set_setting(con, "trend_lookback_hours", str(int(lookback_hours)))
                    set_setting(con, "trend_results_per_query", str(int(results_per_query)))
                    set_setting(con, "trend_portal_query_limit", str(int(portal_query_limit)))
                    set_setting(
                        con,
                        "trend_portal_pages_per_query",
                        str(int(portal_pages_per_query)),
                    )
                    set_setting(con, "naver_search_workers", str(int(naver_max_workers)))
                    set_setting(con, "daum_search_workers", str(int(daum_max_workers)))
                    set_setting(con, "google_trends_enabled", "true" if google_trends_enabled else "false")
                    set_setting(con, "google_trends_limit", str(int(google_trends_limit)))
                    set_setting(
                        con,
                        "wikipedia_pageviews_enabled",
                        "true" if wikipedia_pageviews_enabled else "false",
                    )
                    set_setting(con, "wikipedia_pageviews_limit", str(int(wikipedia_pageviews_limit)))
                    set_setting(con, "naver_search_daily_safety_limit", str(int(naver_daily_limit)))
                    set_setting(con, "naver_search_monthly_safety_limit", str(int(naver_monthly_limit)))
                    set_setting(con, "kakao_daum_daily_safety_limit", str(int(kakao_daily_limit)))
                    set_setting(con, "kakao_daum_monthly_safety_limit", str(int(kakao_monthly_limit)))
                    set_setting(con, "data_cleanup_enabled", "true" if cleanup_enabled else "false")
                    set_setting(con, "source_retention_days", str(int(source_retention_days)))
                    set_setting(con, "sync_run_retention_days", str(int(sync_run_retention_days)))
                    set_setting(con, "api_usage_retention_months", str(int(api_usage_retention_months)))
                    set_setting(con, "trend_analysis_youtube_limit", str(int(analysis_youtube_limit)))
                    set_setting(con, "trend_analysis_naver_limit", str(int(analysis_naver_limit)))
                    set_setting(con, "trend_analysis_daum_limit", str(int(analysis_daum_limit)))
                    set_setting(con, "trend_analysis_google_limit", str(int(analysis_google_limit)))
                    set_setting(con, "trend_analysis_wikipedia_limit", str(int(analysis_wikipedia_limit)))
                    st.success("설정을 저장했습니다.")
        elif settings_section == "AI·품질":
            if refreshed_models is not None:
                save_gemini_model_catalog(con, refreshed_models)
                st.success(
                    f"Gemini 모델 목록 {len(refreshed_models):,}개를 새로 조회해 저장했습니다."
                )
            elif refresh_error:
                st.error(refresh_error)
            ai_tabs = st.tabs(["모델 설정", "품질·운영 진단"])
            with ai_tabs[0]:
                _render_gemini_model_settings(con)
            with ai_tabs[1]:
                render_quality_diagnostic_panels(con, st_module=st)
                st.divider()
                topic_angle_config = get_gemini_config()
                render_topic_angle_quality_diagnostic_panel(
                    con,
                    app_id=topic_angle_config.app_id,
                    items_per_request=topic_angle_config.topic_angle_batch_limit,
                    thinking_level=topic_angle_config.topic_angle_thinking_level,
                    timeout_seconds=topic_angle_config.topic_angle_timeout_seconds,
                    min_opportunity_score=topic_angle_config.topic_angle_min_opportunity_score,
                )
        elif settings_section == "발행 채널":
            _render_blog_profile_settings(con)
        elif settings_section == "자동화·이력":
            automation_tabs = st.tabs(["예약 실행", "수집 이력"])
            with automation_tabs[0]:
                _render_refresh_scheduler_settings(con)
            with automation_tabs[1]:
                render_collection_history(con)
        else:
            data_tabs = st.tabs(["데이터 보관", "백업·복구", "API 상태", "YouTube 연동"])
            with data_tabs[0]:
                cleanup_flash = st.session_state.pop("settings_cleanup_flash", None)
                if cleanup_flash:
                    st.success(str(cleanup_flash))

                st.subheader("데이터 보관 상태")
                retention_days = int(get_setting(con, "source_retention_days", "30") or 30)
                database_stats = get_database_stats(
                    con,
                    db_path=DEFAULT_DB_PATH,
                    retention_days=retention_days,
                    lookback_hours=int(get_setting(con, "trend_lookback_hours", "72") or 72),
                )
                stats_cols = st.columns(5)
                stats_cols[0].metric(
                    "DB 파일 크기",
                    _format_file_size(database_stats.database_size_bytes),
                    help="현재 로컬 DuckDB 파일이 디스크에서 차지하는 크기입니다.",
                    border=True,
                )
                stats_cols[1].metric(
                    "원본 자료",
                    f"{database_stats.source_items_total:,}개",
                    help="현재 source_items 테이블에 보관 중인 전체 원본 수집 자료 수입니다.",
                    border=True,
                )
                stats_cols[2].metric(
                    "최근 분석 범위",
                    f"{database_stats.source_items_recent:,}개",
                    help="설정한 최근 분석 시간 범위 안에 들어오는 원본 자료 수입니다.",
                    border=True,
                )
                stats_cols[3].metric(
                    "주제 연결 자료",
                    f"{database_stats.source_items_linked:,}개",
                    help="사용자가 저장한 주제와 연결되어 정리 대상에서 보호되는 원본 자료 수입니다.",
                    border=True,
                )
                stats_cols[4].metric(
                    f"{retention_days}일 초과 미사용",
                    f"{database_stats.source_items_old_unlinked:,}개",
                    help=(
                        f"마지막 확인 후 {retention_days}일이 지났고 사용자 주제에도 연결되지 않아 "
                        "다음 정리 때 삭제 후보가 되는 원본 자료 수입니다."
                    ),
                    border=True,
                )
                st.caption(
                    f"마지막 정리: {database_stats.last_cleanup_at} · 수집 실행 기록 총 {database_stats.sync_runs_total:,}개"
                )
                if st.button("지금 오래된 자료 정리", type="secondary"):
                    cleanup_result = cleanup_old_data(
                        con,
                        source_retention_days=retention_days,
                        sync_run_retention_days=int(get_setting(con, "sync_run_retention_days", "90") or 90),
                        api_usage_retention_months=int(get_setting(con, "api_usage_retention_months", "13") or 13),
                        checkpoint=True,
                    )
                    st.session_state["settings_cleanup_flash"] = (
                        "정리 완료: 원본 "
                        f"{cleanup_result.source_items_deleted:,}개, 군집 연결 {cleanup_result.cluster_links_deleted:,}개, "
                        f"출처 실행 {cleanup_result.sync_runs_deleted:,}개, 전체 실행 이력 "
                        f"{cleanup_result.collection_runs_deleted:,}개, 호출 기록 {cleanup_result.api_usage_rows_deleted:,}개 삭제"
                    )
                    st.rerun()
            with data_tabs[1]:
                render_database_backup_panel(st_module=st)
            with data_tabs[2]:
                st.subheader("네이버 검색 API 상태")
                naver_client_id, naver_client_secret = get_naver_api_credentials()
                api_cols = st.columns(3)
                api_cols[0].metric(
                    "Client ID",
                    "설정됨" if naver_client_id else "없음",
                    help="프로젝트 루트 .env에서 NAVER_CLIENT_ID를 읽을 수 있는지 표시합니다. 실제 값은 화면과 DB에 표시하지 않습니다.",
                    border=True,
                )
                api_cols[1].metric(
                    "Client Secret",
                    "설정됨" if naver_client_secret else "없음",
                    help="프로젝트 루트 .env에서 NAVER_CLIENT_SECRET을 읽을 수 있는지 표시합니다. 실제 값은 화면과 DB에 표시하지 않습니다.",
                    border=True,
                )
                api_cols[2].metric(
                    "연결 방식",
                    "NAVER API HUB",
                    help="현재 프로그램이 NAVER 뉴스·블로그 검색에 사용하는 공식 API 연결 경로입니다.",
                    border=True,
                )
                usage = get_naver_search_usage(
                    con,
                    daily_limit=int(get_setting(con, "naver_search_daily_safety_limit", "25000") or 25000),
                    monthly_limit=int(get_setting(con, "naver_search_monthly_safety_limit", "775000") or 775000),
                )
                usage_cols = st.columns(4)
                usage_cols[0].metric(
                    f"오늘 로컬 호출 · 한도 {usage.daily_limit:,}회",
                    f"{usage.daily_used:,}회",
                    help="오늘 이 프로그램의 로컬 DB에 기록된 NAVER 검색 API 호출 수입니다. 같은 키를 다른 프로그램에서 사용한 양은 포함되지 않습니다.",
                    border=True,
                )
                usage_cols[1].metric(
                    f"이번 달 로컬 호출 · 한도 {usage.monthly_limit:,}회",
                    f"{usage.monthly_used:,}회",
                    help="이번 달 이 프로그램의 로컬 DB에 기록된 NAVER 검색 API 호출 수입니다. 콘솔 실제 사용량과 다를 수 있습니다.",
                    border=True,
                )
                usage_cols[2].metric(
                    "검색 API 무료 최대",
                    "일 25,000 · 월 775,000",
                    help="프로그램이 참고하는 NAVER 검색 API의 공식 무료 제공 최대 호출량입니다.",
                    border=True,
                )
                usage_cols[3].metric(
                    "공식 요청 속도",
                    f"최대 {NAVER_SEARCH_OFFICIAL_RPS_LIMIT} RPS",
                    help="NAVER 검색 API가 허용하는 초당 최대 요청 수입니다. 프로그램은 이보다 보수적으로 호출합니다.",
                    border=True,
                )
                st.caption(
                    "NAVER 검색 API는 API Key 기준으로 일 최대 25,000회, 월 최대 775,000회까지 제공됩니다. "
                    "현재 프로그램 기본값도 이 무료 최대치와 동일하며, 한도에 도달하면 추가 호출을 중단합니다."
                )
                st.info(
                    "현재 프로그램은 뉴스·블로그 검색 API만 사용합니다. 검색어 트렌드·쇼핑 인사이트는 "
                    "현재 월 최대 50,000회까지 0원이지만 아직 프로그램에서 호출하지 않습니다. "
                    "향후 유료 정책이 실제 적용되면 공지를 확인한 뒤 별도 제한을 추가합니다."
                )
                st.warning(
                    "로컬 호출 수는 이 프로그램 DB에서 실행한 요청만 셉니다. 같은 API 키를 다른 프로그램에서도 사용하거나 "
                    "DB를 초기화했다면 NAVER Cloud 콘솔의 실제 사용량을 최종 기준으로 확인하세요."
                )
                st.caption("NAVER API HUB 키는 .env에서만 읽고 DuckDB에는 저장하지 않습니다.")

                st.subheader("카카오 Daum 검색 API 상태")
                kakao_key = get_kakao_rest_api_key()
                kakao_usage = get_kakao_daum_usage(
                    con,
                    daily_limit=int(get_setting(con, "kakao_daum_daily_safety_limit", "50000") or 50000),
                    monthly_limit=int(get_setting(con, "kakao_daum_monthly_safety_limit", "3000000") or 3000000),
                )
                kakao_cols = st.columns(4)
                kakao_cols[0].metric(
                    "REST API 키",
                    "설정됨" if kakao_key else "없음",
                    help="프로젝트 루트 .env에서 KAKAO_REST_API_KEY를 읽을 수 있는지 표시합니다. 실제 값은 화면과 DB에 표시하지 않습니다.",
                    border=True,
                )
                kakao_cols[1].metric(
                    f"오늘 로컬 호출 · 한도 {kakao_usage.daily_limit:,}회",
                    f"{kakao_usage.daily_used:,}회",
                    help="오늘 이 프로그램의 로컬 DB에 기록된 Daum 검색 API 호출 수입니다. 같은 카카오 앱의 다른 프로그램 호출은 포함되지 않습니다.",
                    border=True,
                )
                kakao_cols[2].metric(
                    f"이번 달 로컬 호출 · 한도 {kakao_usage.monthly_limit:,}회",
                    f"{kakao_usage.monthly_used:,}회",
                    help="이번 달 이 프로그램에서 기록한 카카오 앱 API 호출 수입니다. 카카오 콘솔의 앱 전체 사용량이 최종 기준입니다.",
                    border=True,
                )
                kakao_cols[3].metric(
                    "현재 수집",
                    "웹문서 · 카페",
                    help="현재 프로그램이 Daum 검색 API에서 실제로 수집하는 문서 종류입니다.",
                    border=True,
                )
                st.caption(
                    "Daum 검색은 현재 앱당 일 50,000회, 카카오 앱 전체 API는 월 3,000,000건의 무료 쿼터가 제공됩니다. "
                    "같은 카카오 앱을 다른 프로그램에서도 쓰면 월간 쿼터가 합산되므로 콘솔 사용량이 최종 기준입니다."
                )
                st.caption("카카오 REST API 키도 .env에서만 읽고 DuckDB에는 저장하지 않습니다.")

                st.subheader("무료 공개 데이터 사용량")
                google_usage = get_local_api_usage(
                    con,
                    provider=GOOGLE_TRENDS_PROVIDER,
                    api_name=GOOGLE_TRENDS_API,
                )
                wikipedia_usage = get_local_api_usage(
                    con,
                    provider=WIKIMEDIA_PROVIDER,
                    api_name=WIKIMEDIA_API,
                )
                public_usage_cols = st.columns(4)
                public_usage_cols[0].metric(
                    "Google Trends 오늘",
                    f"{google_usage.daily_used:,}회",
                    help="오늘 Google Trends 공식 RSS에 실제로 보낸 HTTP 요청을 로컬 DB에서 합산한 값입니다.",
                    border=True,
                )
                public_usage_cols[1].metric(
                    "Google Trends 이번 달",
                    f"{google_usage.monthly_used:,}회",
                    help="이번 달 Google Trends 공식 RSS에 실제로 보낸 HTTP 요청의 로컬 누적값입니다.",
                    border=True,
                )
                public_usage_cols[2].metric(
                    "위키백과 오늘",
                    f"{wikipedia_usage.daily_used:,}회",
                    help="오늘 Wikimedia Analytics API에 실제로 보낸 HTTP 요청을 로컬 DB에서 합산한 값입니다.",
                    border=True,
                )
                public_usage_cols[3].metric(
                    "위키백과 이번 달",
                    f"{wikipedia_usage.monthly_used:,}회",
                    help="이번 달 Wikimedia Analytics API에 실제로 보낸 HTTP 요청의 로컬 누적값입니다.",
                    border=True,
                )
                st.caption(
                    "두 출처는 별도 API 키나 유료 요금제를 사용하지 않습니다. 표시 횟수는 이 프로그램이 실제로 보낸 HTTP 요청을 로컬 DB에 기록한 값입니다."
                )
            with data_tabs[3]:
                st.subheader("YouTube Parquet 교환 파일 상태")
                configured_path = get_setting(con, "youtube_parquet_path")
                st.code(configured_path, language=None)
                status_columns = st.columns(5)
                file_exists = Path(configured_path).is_file()
                status_columns[0].metric(
                    "파일 존재",
                    "예" if file_exists else "아니오",
                    help="설정된 YouTube Parquet 교환 파일 경로에 실제 파일이 존재하는지 확인합니다.",
                    border=True,
                )
                try:
                    info = YouTubeParquetAdapter(configured_path).inspect()
                    status_columns[1].metric(
                        "스키마 버전",
                        info["schema_version"],
                        help="youtube-trend-tracker가 내보낸 교환 파일의 데이터 스키마 버전입니다.",
                        border=True,
                    )
                    status_columns[2].metric(
                        "읽기 가능 행",
                        info["row_count"],
                        help="현재 교환 파일에서 콘텐츠 트렌드 신호로 읽을 수 있는 전체 행 수입니다.",
                        border=True,
                    )
                    status_columns[3].metric(
                        "내보낸 시각",
                        str(info["exported_at"] or "기록 없음"),
                        help="youtube-trend-tracker가 이 교환 파일을 마지막으로 생성한 시각입니다.",
                        border=True,
                    )
                except YouTubeParquetError as exc:
                    status_columns[1].metric(
                        "스키마 버전",
                        "확인 불가",
                        help="파일이 없거나 호환되지 않아 스키마 버전을 읽지 못했습니다.",
                        border=True,
                    )
                    status_columns[2].metric(
                        "읽기 가능 행",
                        "확인 불가",
                        help="파일이 없거나 호환되지 않아 읽을 수 있는 행 수를 확인하지 못했습니다.",
                        border=True,
                    )
                    status_columns[3].metric(
                        "내보낸 시각",
                        "확인 불가",
                        help="파일이 없거나 호환되지 않아 내보낸 시각을 확인하지 못했습니다.",
                        border=True,
                    )
                    if file_exists:
                        st.error(str(exc))
                    else:
                        st.info(str(exc))
                last_import = get_last_successful_import(con, "youtube_parquet")
                status_columns[4].metric(
                    "최근 가져오기",
                    str(last_import or "기록 없음"),
                    help="이 프로그램이 YouTube Parquet 교환 파일을 마지막으로 성공적으로 가져온 시각입니다.",
                    border=True,
                )
                st.caption(f"메인 DB: {DEFAULT_DB_PATH}")
                st.info("API 키, 블로그 아이디, 비밀번호, Chrome 쿠키는 이 프로그램 DB에 저장하지 않습니다.")


apply_global_styles()
page = render_top_navigation()
render_page_topbar(page)
st.caption(f"현재 버전: {format_app_version_label(APP_VERSION)}")

if page == "오늘의 트렌드":
    render_trend_dashboard()
elif page == "AI 요청서":
    render_content_pack()
elif page == "주제·트렌드":
    render_topics()
elif page == "AI 결과 가져오기":
    render_ai_import()
elif page == "글 편집":
    render_editor()
elif page == "발행 보조":
    render_publish()
else:
    render_settings()
